"""P4-3 单元测试：任务级 Git 隔离（分支/工作树、提交、diff、allowed_paths 越界）。

git 不可用时跳过并如实标注（不伪造通过）。测试全部在临时仓库内执行。
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.audit.logger import AuditLogger
from core.orchestrator.store import StateStore
from core.workspace import GitError, GitUnavailable, GitWorkspace, ScopeViolation, git_executable

GIT = git_executable()
pytestmark = pytest.mark.skipif(GIT is None, reason="当前环境无 git，Git 隔离未验证")


def _git(*args: str, cwd: Path | None = None) -> str:
    assert GIT is not None
    proc = subprocess.run(
        [GIT, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "tester@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "tester@example.com")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "main", str(repo))
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    return repo


@pytest.fixture
def audit(tmp_path: Path) -> Iterator[AuditLogger]:
    store = StateStore(tmp_path / "state.db")
    logger = AuditLogger(store, log_dir=tmp_path / "logs")
    yield logger
    store.close()


@pytest.fixture
def workspace(repo: Path, tmp_path: Path, audit: AuditLogger) -> Iterator[GitWorkspace]:
    manager = GitWorkspace(repo, worktree_root=tmp_path / "wt", audit=audit)
    yield manager


def test_create_worktree_on_isolated_branch(repo: Path, workspace: GitWorkspace) -> None:
    wt = workspace.create_task_worktree("TASK-1")
    assert wt.path.is_dir()
    assert (wt.path / "README.md").is_file()
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt.path).strip() == "task/TASK-1"
    # 任务分支不影响主线
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo).strip() == "main"
    workspace.remove_task_worktree("TASK-1")
    assert not wt.path.exists()


def test_commit_and_diff_detects_out_of_scope(workspace: GitWorkspace, audit: AuditLogger) -> None:
    wt = workspace.create_task_worktree("TASK-2")
    (wt.path / "src").mkdir()
    (wt.path / "docs").mkdir()
    (wt.path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (wt.path / "docs" / "notes.md").write_text("notes\n", encoding="utf-8")

    commit_hash = wt.commit("实现功能并补充说明")
    assert len(commit_hash) >= 7

    report = wt.collect_diff()
    assert report.paths == ("docs/notes.md", "src/app.py")
    assert report.insertions == 2

    with pytest.raises(ScopeViolation) as exc_info:
        wt.enforce_allowed_paths(["src"], project_id="PRJ-1")
    assert exc_info.value.violations == ("docs/notes.md",)
    event = audit.list_events()[-1]
    assert event.action == "workspace.violation"
    assert event.task_id == "TASK-2"
    assert event.detail["violations"] == ["docs/notes.md"]


def test_enforce_allowed_paths_passes_for_in_scope(workspace: GitWorkspace) -> None:
    wt = workspace.create_task_worktree("TASK-3")
    (wt.path / "src").mkdir()
    (wt.path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    wt.commit("新增模块", paths=["src/app.py"])
    report = wt.enforce_allowed_paths(["src/"])
    assert report.paths == ("src/app.py",)
    assert report.insertions == 1


def test_untracked_file_is_detected(workspace: GitWorkspace) -> None:
    wt = workspace.create_task_worktree("TASK-4")
    (wt.path / "outside.py").write_text("escape = True\n", encoding="utf-8")
    with pytest.raises(ScopeViolation):
        wt.enforce_allowed_paths(["src"])


def test_empty_allowed_paths_rejects_any_change(workspace: GitWorkspace) -> None:
    wt = workspace.create_task_worktree("TASK-5")
    (wt.path / "anything.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(ScopeViolation):
        wt.enforce_allowed_paths([])


def test_empty_commit_rejected(workspace: GitWorkspace) -> None:
    wt = workspace.create_task_worktree("TASK-6")
    with pytest.raises(GitError, match="没有可提交"):
        wt.commit("空提交")
    (wt.path / "a.txt").write_text("a\n", encoding="utf-8")
    with pytest.raises(GitError, match="提交信息不得为空"):
        wt.commit("  ")


def test_duplicate_worktree_rejected(workspace: GitWorkspace) -> None:
    workspace.create_task_worktree("TASK-7")
    with pytest.raises(GitError, match="已存在"):
        workspace.create_task_worktree("TASK-7")


def test_missing_git_reports_unavailable(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(GitUnavailable):
        GitWorkspace(repo, worktree_root=tmp_path / "wt", git_exe="no-such-git-binary")


def test_remove_missing_worktree_rejected(workspace: GitWorkspace) -> None:
    with pytest.raises(GitError, match="不存在"):
        workspace.remove_task_worktree("TASK-404")
