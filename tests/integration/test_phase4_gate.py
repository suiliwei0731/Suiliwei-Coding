"""Phase 4 阶段门禁集成测试（甲 Phase 3 验收 + 乙阶段 D）。

负面实验（不伪造通过）：
- 未配置/未启用模型 → 明确报错，且不得出现 SUCCEEDED 运行记录；
- 越权路径与危险命令 → 拒绝并写 security.denied 审计；
- 任务改动越出 allowed_paths → 检测并写 workspace.violation 审计；
- 未审查/未试用/未固定版本的外部技能 → 不允许启用；
- DEPRECATED 知识 → 默认检索不推荐。

正向链路：真实 HTTP（本地服务）模型调用超时后恢复，产物写入任务工作树并提交，
改动范围校验通过；技能启用后来源/版本/审查状态/证据可追踪。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from adapters.models import (
    CompletionRequest,
    ModelError,
    ModelProfile,
    ModelTimeout,
    OpenAICompatibleAdapter,
)
from core.audit.logger import AuditLogger
from core.domain import SchemaRegistry
from core.domain.enums import AgentRunStatus
from core.knowledge import KnowledgeIndex, TrustLevel
from core.orchestrator.store import StateStore
from core.security.policy import SecurityDenied, SecurityPolicy
from core.skills import (
    ExternalSkill,
    ExternalSkillRegistry,
    ReviewStatus,
    SkillPipeline,
    SkillPipelineError,
)
from core.workspace import GitWorkspace, ScopeViolation, git_executable

GIT = git_executable()
pytestmark = pytest.mark.skipif(GIT is None, reason="当前环境无 git，Git 隔离门禁未验证")

VALID_TASK_PAYLOAD = {
    "schema_version": 1,
    "task_id": "TASK-GATE1",
    "project_id": "PRJ-GATE",
    "title": "门禁小任务",
    "owner_role": "backend-developer",
    "status": "IN_PROGRESS",
}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server 约定
        length = int(self.headers.get("content-length", 0))
        self.rfile.read(length)
        delay = self.server.delay_ref[0]  # type: ignore[attr-defined]
        if delay:
            time.sleep(float(delay))
        body = {
            "choices": [{"message": {"content": json.dumps(VALID_TASK_PAYLOAD)}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 20},
        }
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # 客户端已超时断开，服务端静默丢弃迟到的响应
            return

    def log_message(self, format: str, *args: Any) -> None:
        return


class _ModelServer(ThreadingHTTPServer):
    delay_ref: list[float]


@contextmanager
def _server(delay_ref: list[float]) -> Iterator[str]:
    """本地 OpenAI 兼容端点；delay_ref[0] 可运行时修改，用于超时→恢复实验。"""
    server = _ModelServer(("127.0.0.1", 0), _Handler)
    server.delay_ref = delay_ref
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def store_audit(tmp_path: Path) -> Iterator[AuditLogger]:
    store = StateStore(tmp_path / "state.db")
    logger = AuditLogger(store, log_dir=tmp_path / "logs")
    yield logger
    store.close()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "gate-tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "gate@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "gate-tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "gate@example.com")
    repo = tmp_path / "repo"
    repo.mkdir()
    assert GIT is not None
    subprocess.run([GIT, "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("# gate\n", encoding="utf-8")
    subprocess.run([GIT, "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run([GIT, "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


# ---------- 负面实验 ----------


def test_unconfigured_model_never_reports_success(store_audit: AuditLogger) -> None:
    profile = ModelProfile.from_config()  # config/models.yaml 默认 enabled=false
    assert profile.enabled is False
    adapter = OpenAICompatibleAdapter(profile, audit=store_audit, env={})
    with pytest.raises(ModelError, match="未启用"):
        adapter.complete(
            CompletionRequest(role="backend", prompt="实现小任务", task_id="TASK-GATE1")
        )
    assert adapter.runs[0].status is AgentRunStatus.FAILED
    assert adapter.usage_records == []
    assert "model.called" not in [e.action for e in store_audit.list_events()]


def test_security_denials_are_audited(tmp_path: Path, store_audit: AuditLogger) -> None:
    policy = SecurityPolicy.from_config(audit=store_audit)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(SecurityDenied, match="不在授权工作区"):
        policy.check_path(tmp_path / "outside.txt", workspace=workspace)
    with pytest.raises(SecurityDenied, match="高危命令"):
        policy.check_command("git push origin main --force")
    with pytest.raises(SecurityDenied, match="白名单"):
        policy.check_command("curl https://example.com")
    events = store_audit.list_events()
    assert [e.action for e in events] == ["security.denied"] * 3
    assert {e.detail["kind"] for e in events} == {"path", "command"}


def test_scope_violation_detected_and_audited(
    repo: Path, tmp_path: Path, store_audit: AuditLogger
) -> None:
    manager = GitWorkspace(repo, worktree_root=tmp_path / "wt", audit=store_audit)
    wt = manager.create_task_worktree("TASK-GATE1")
    (wt.path / "src").mkdir()
    (wt.path / "docs").mkdir()
    (wt.path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (wt.path / "docs" / "notes.md").write_text("越界修改\n", encoding="utf-8")
    wt.commit("实现小任务")
    with pytest.raises(ScopeViolation) as exc_info:
        wt.enforce_allowed_paths(["src"], project_id="PRJ-GATE")
    assert exc_info.value.violations == ("docs/notes.md",)
    event = store_audit.list_events()[-1]
    assert event.action == "workspace.violation"
    assert event.task_id == "TASK-GATE1"


def test_external_skill_cannot_enable_before_review_and_pin(
    tmp_path: Path, store_audit: AuditLogger
) -> None:
    registry = ExternalSkillRegistry(skills={}, path=tmp_path / "skills.yaml")
    pipeline = SkillPipeline(registry, audit=store_audit)
    pipeline.register(
        ExternalSkill(
            id="gate-skill",
            name="门禁候选技能",
            source="https://github.com/example/gate-skill",
            license="MIT",
        )
    )
    with pytest.raises(SkillPipelineError, match="非法流转"):
        pipeline.enable("gate-skill")
    assert registry.get("gate-skill").review_status is ReviewStatus.REGISTERED

    pipeline.mark_reviewed("gate-skill", "docs/development/phase-4-skills-评估报告.md")
    pipeline.sandbox_trial("gate-skill", "runtime/artifacts/gate-skill-trial.json")
    pipeline.pin_version("gate-skill", "0123abc456")
    enabled = pipeline.enable("gate-skill")
    assert enabled.usable is True

    persisted = ExternalSkillRegistry.from_file(tmp_path / "skills.yaml").get("gate-skill")
    assert persisted.version == "0123abc456"
    detail = store_audit.list_events()[-1].detail
    assert detail["source"] == "https://github.com/example/gate-skill"
    assert detail["version"] == "0123abc456"
    assert detail["review_status"] == "ENABLED"
    assert detail["review_evidence"] and detail["trial_evidence"]


def test_deprecated_knowledge_not_recommended(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    entries = [
        {
            "id": "current",
            "title": "当前做法",
            "path": "knowledge/current.md",
            "layer": "PLATFORM",
            "trust": "VERIFIED",
            "version": "2",
            "tags": ["部署规范"],
            "summary": "当前推荐做法",
        },
        {
            "id": "legacy",
            "title": "旧做法",
            "path": "knowledge/legacy.md",
            "layer": "PLATFORM",
            "trust": "DEPRECATED",
            "version": "1",
            "tags": ["部署规范"],
            "summary": "已弃用做法",
        },
    ]
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "entries": entries}, allow_unicode=True),
        encoding="utf-8",
    )
    index = KnowledgeIndex.from_file(path)
    default_ids = [h.entry.id for h in index.search("部署规范")]
    assert default_ids == ["current"]
    assert index.search("部署规范")[0].trust is TrustLevel.VERIFIED
    assert {h.entry.id for h in index.search("部署规范", include_deprecated=True)} == {
        "current",
        "legacy",
    }


# ---------- 正向链路：模型超时→恢复→产物提交→范围校验 ----------


def test_model_timeout_then_recovery_in_task_worktree(
    repo: Path, tmp_path: Path, store_audit: AuditLogger
) -> None:
    manager = GitWorkspace(repo, worktree_root=tmp_path / "wt", audit=store_audit)
    wt = manager.create_task_worktree("TASK-GATE2")
    delay_ref = [0.6]
    with _server(delay_ref) as base_url:
        adapter = OpenAICompatibleAdapter(
            ModelProfile(
                name="gate-profile",
                enabled=True,
                base_url_env="GATE_BASE_URL",
                api_key_env="GATE_API_KEY",
                model_env="GATE_MODEL",
                request_timeout_seconds=0.2,
            ),
            env={"GATE_BASE_URL": base_url, "GATE_API_KEY": "sk-gate", "GATE_MODEL": "gate"},
            registry=SchemaRegistry(),
            audit=store_audit,
        )
        request = CompletionRequest(
            role="backend-developer",
            prompt="完成门禁小任务",
            schema="task",
            task_id="TASK-GATE2",
        )
        with pytest.raises(ModelTimeout):
            adapter.complete(request)
        assert adapter.runs[-1].status is AgentRunStatus.TIMED_OUT

        delay_ref[0] = 0.0
        result = adapter.complete(request)

    assert adapter.runs[-1].status is AgentRunStatus.SUCCEEDED
    assert result.parsed is not None
    assert result.parsed["task_id"] == "TASK-GATE1"
    assert len(adapter.usage_records) == 1  # 超时无响应不记用量；成功调用记录一次

    (wt.path / "src").mkdir()
    (wt.path / "src" / "model_output.json").write_text(result.content, encoding="utf-8")
    commit_hash = wt.commit("保存模型产物", paths=["src/model_output.json"])
    assert len(commit_hash) >= 7
    report = wt.enforce_allowed_paths(["src"], project_id="PRJ-GATE")
    assert report.paths == ("src/model_output.json",)

    actions = [e.action for e in store_audit.list_events()]
    assert "model.failed" in actions and "model.called" in actions
    assert "workspace.violation" not in actions
    assert all("sk-gate" not in json.dumps(e.detail) for e in store_audit.list_events())
