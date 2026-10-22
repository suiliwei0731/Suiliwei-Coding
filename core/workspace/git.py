"""任务级 Git 隔离（实施计划 P4-3；规范 §9、§17）。

- 每个任务在独立分支 ``task/<task_id>`` 的独立工作树中执行，主线不被直接改动；
- 提交必须携带非空信息且确有待提交变更（拒绝空提交），支持按路径小提交；
- ``collect_diff`` 采集相对基线（含已提交、暂存、未暂存与未跟踪文件）的变更；
- ``enforce_allowed_paths`` 对照任务的 ``allowed_paths``：越界改动写审计
  ``workspace.violation`` 并抛 :class:`ScopeViolation`，不得静默放过。

所有 git 调用均为 argv 列表、禁 shell；git 不可用时抛 :class:`GitUnavailable`，
不得假装隔离已生效。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.audit.logger import AuditLogger

WORKSPACE_ACTOR = "Git 隔离层"
DEFAULT_WORKTREE_DIR = "runtime/worktrees"
_WINDOWS_GIT_CANDIDATES = (
    Path("C:/Program Files/Git/cmd/git.exe"),
    Path("C:/Program Files (x86)/Git/cmd/git.exe"),
    Path("C:/Program Files/Git/bin/git.exe"),
)


class GitError(RuntimeError):
    """git 操作失败（非零退出或参数非法）。"""


class GitUnavailable(GitError):
    """当前环境不可用 git（未安装或不可执行）。"""


class ScopeViolation(GitError):
    """任务改动超出 allowed_paths（携带越界文件与完整 diff 报告）。"""

    def __init__(self, violations: tuple[str, ...], report: DiffReport) -> None:
        self.violations = violations
        self.report = report
        super().__init__(
            "改动超出 allowed_paths: " + ", ".join(violations) + "（已写 workspace.violation 审计）"
        )


@dataclass(frozen=True)
class ChangedFile:
    """一处变更：状态（A/M/D/R）+ 路径（重命名取新路径）。"""

    status: str
    path: str
    old_path: str | None = None


@dataclass(frozen=True)
class DiffReport:
    """任务工作树相对基线的变更报告。"""

    task_id: str
    base_ref: str
    files: tuple[ChangedFile, ...]
    insertions: int = 0
    deletions: int = 0
    diff_text: str = ""

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files)


def git_executable() -> str | None:
    """定位 git 可执行文件：环境变量 → PATH → Windows 常见安装路径。"""
    override = os.environ.get("AI_DEV_PLATFORM_GIT_EXE")
    if override and Path(override).is_file():
        return override
    found = shutil.which("git")
    if found:
        return found
    for candidate in _WINDOWS_GIT_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


def _safe_id(task_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id.strip())
    return cleaned or "task"


def _normalize(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/").rstrip("/")


def _is_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    normalized = _normalize(path)
    for entry in allowed_paths:
        allowed = _normalize(entry)
        if not allowed:
            continue
        if normalized == allowed or normalized.startswith(allowed + "/"):
            return True
    return False


class GitWorkspace:
    """仓库级隔离管理器：创建/移除任务工作树。"""

    def __init__(
        self,
        repo_root: Path | str,
        *,
        worktree_root: Path | str | None = None,
        git_exe: str | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self._repo = Path(repo_root).resolve()
        self._worktree_root = (
            Path(worktree_root).resolve()
            if worktree_root is not None
            else self._repo / DEFAULT_WORKTREE_DIR
        )
        self._git = git_exe or git_executable()
        if not self._git:
            raise GitUnavailable("未找到 git 可执行文件：请安装 Git 或设置 AI_DEV_PLATFORM_GIT_EXE")
        self._audit = audit
        if not self._repo.is_dir():
            raise GitError(f"仓库目录不存在: {self._repo}")
        result = self._run(["rev-parse", "--is-inside-work-tree"], cwd=self._repo)
        if result.stdout.strip() != "true":
            raise GitError(f"不是 git 工作树: {self._repo}")

    @property
    def git(self) -> str:
        return str(self._git)

    def create_task_worktree(
        self, task_id: str, *, base_ref: str = "HEAD", branch: str | None = None
    ) -> TaskWorktree:
        """为任务创建独立分支与工作树；已存在则拒绝（不静默复用）。"""
        safe = _safe_id(task_id)
        target_branch = branch or f"task/{safe}"
        path = self._worktree_root / safe
        if path.exists():
            raise GitError(f"任务工作树已存在: {path}（须先移除或改用新任务 ID）")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["worktree", "add", "-b", target_branch, str(path), base_ref], cwd=self._repo)
        # 基线固定为创建时的提交哈希，后续提交不会移动基线（diff 才可复现）
        base_commit = self._run(["rev-parse", base_ref], cwd=path).stdout.strip()
        info = TaskWorktree(
            task_id=task_id,
            branch=target_branch,
            path=path,
            base_ref=base_commit,
            git=self.git,
            audit=self._audit,
        )
        if self._audit is not None:
            self._audit.log(
                "workspace.created",
                WORKSPACE_ACTOR,
                task_id=task_id,
                detail={
                    "branch": target_branch,
                    "path": str(path),
                    "base_ref": base_ref,
                    "base_commit": base_commit,
                },
            )
        return info

    def remove_task_worktree(self, task_id: str, *, delete_branch: bool = True) -> None:
        """移除任务工作树（可选删除任务分支）；不存在时抛 GitError。"""
        safe = _safe_id(task_id)
        path = self._worktree_root / safe
        if not path.exists():
            raise GitError(f"任务工作树不存在: {path}")
        self._run(["worktree", "remove", "--force", str(path)], cwd=self._repo)
        branch = f"task/{safe}"
        if delete_branch:
            self._run(["branch", "-D", branch], cwd=self._repo)
        if self._audit is not None:
            self._audit.log(
                "workspace.removed",
                WORKSPACE_ACTOR,
                task_id=task_id,
                detail={"branch": branch, "path": str(path), "delete_branch": delete_branch},
            )

    def _run(
        self, args: list[str], *, cwd: Path | str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        assert self._git is not None
        try:
            proc = subprocess.run(
                [self._git, *args],
                cwd=Path(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                check=False,
            )
        except FileNotFoundError as exc:  # pragma: no cover - 构造时已探测
            raise GitUnavailable(f"git 不可执行: {self._git}") from exc
        if check and proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} 失败（退出码 {proc.returncode}）: {proc.stderr.strip()}"
            )
        return proc


class TaskWorktree:
    """单个任务工作树：提交、diff 采集与改动范围校验。"""

    def __init__(
        self,
        *,
        task_id: str,
        branch: str,
        path: Path,
        base_ref: str,
        git: str,
        audit: AuditLogger | None = None,
    ) -> None:
        self.task_id = task_id
        self.branch = branch
        self.path = Path(path)
        self.base_ref = base_ref
        self._git = git
        self._audit = audit

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [self._git, *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
        )
        if check and proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} 失败（退出码 {proc.returncode}）: {proc.stderr.strip()}"
            )
        return proc

    def commit(self, message: str, paths: tuple[str, ...] | list[str] = ()) -> str:
        """按路径（缺省全部）提交；拒绝空信息与无变更的空提交。返回提交哈希。"""
        if not message.strip():
            raise GitError("提交信息不得为空（小提交须说明改动意图）")
        if paths:
            self._run(["add", "--", *paths])
        else:
            self._run(["add", "-A"])
        staged = self._run(["diff", "--cached", "--name-only"]).stdout.strip()
        if not staged:
            raise GitError("没有可提交的变更（拒绝空提交）")
        self._run(["commit", "-m", message])
        return self._run(["rev-parse", "HEAD"]).stdout.strip()

    def collect_diff(self, base_ref: str | None = None) -> DiffReport:
        """采集相对基线的全部变更（含未跟踪文件），并汇总增删行数。"""
        base = base_ref or self.base_ref
        files: dict[str, ChangedFile] = {}
        name_status = self._run(["diff", "--name-status", "-M", base]).stdout
        for line in name_status.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0][:1]
            if status in {"R", "C"} and len(parts) >= 3:
                files[parts[2]] = ChangedFile(status=status, path=parts[2], old_path=parts[1])
            else:
                files[parts[1]] = ChangedFile(status=status, path=parts[1])

        untracked = self._run(["ls-files", "--others", "--exclude-standard"]).stdout
        for path in untracked.splitlines():
            if path.strip():
                files[path.strip()] = ChangedFile(status="A", path=path.strip())

        insertions = 0
        deletions = 0
        numstat = self._run(["diff", "--numstat", base]).stdout
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added, removed = parts[0], parts[1]
            if added.isdigit():
                insertions += int(added)
            if removed.isdigit():
                deletions += int(removed)

        diff_text = self._run(["diff", base]).stdout
        return DiffReport(
            task_id=self.task_id,
            base_ref=base,
            files=tuple(files[p] for p in sorted(files)),
            insertions=insertions,
            deletions=deletions,
            diff_text=diff_text,
        )

    def check_allowed_paths(
        self, allowed_paths: tuple[str, ...] | list[str], report: DiffReport | None = None
    ) -> tuple[str, ...]:
        """返回越界文件（纯检查，不写审计）；allowed_paths 为空即不允许任何改动。"""
        diff = report or self.collect_diff()
        allowed = tuple(allowed_paths)
        return tuple(f.path for f in diff.files if not _is_allowed(f.path, allowed))

    def enforce_allowed_paths(
        self,
        allowed_paths: tuple[str, ...] | list[str],
        *,
        project_id: str | None = None,
    ) -> DiffReport:
        """校验改动范围：越界则写 ``workspace.violation`` 审计并抛 ScopeViolation。"""
        report = self.collect_diff()
        violations = self.check_allowed_paths(allowed_paths, report)
        if violations:
            if self._audit is not None:
                self._audit.log(
                    "workspace.violation",
                    WORKSPACE_ACTOR,
                    project_id=project_id,
                    task_id=self.task_id,
                    detail={
                        "branch": self.branch,
                        "allowed_paths": list(allowed_paths),
                        "violations": list(violations),
                        "changed_files": list(report.paths),
                    },
                )
            raise ScopeViolation(violations, report)
        return report
