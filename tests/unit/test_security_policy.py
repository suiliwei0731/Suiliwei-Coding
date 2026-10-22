"""P4-2 单元测试：工具权限层（路径白名单、命令拦截/白名单、超时与输出上限、网络）。

覆盖：越权路径拒绝并审计、禁止路径硬拦截、目录穿越、危险命令、白名单模式
（enforce/audit/disabled）、受控命令执行（退出码/截断/超时/工作区校验）、
网络允许列表与模型 API 用途放行。
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.audit.logger import AuditLogger
from core.orchestrator.store import StateStore
from core.security.policy import SecurityDenied, SecurityPolicy


@pytest.fixture
def audit(tmp_path: Path) -> Iterator[AuditLogger]:
    store = StateStore(tmp_path / "state.db")
    logger = AuditLogger(store, log_dir=tmp_path / "logs")
    yield logger
    store.close()


def _policy(audit: AuditLogger | None = None, **overrides: object) -> SecurityPolicy:
    base: dict[str, object] = {
        "forbidden_paths": ("C:/Windows", "~/.ssh", "~/"),
        "whitelist_only": True,
        "command_whitelist_mode": "enforce",
        "command_timeout_seconds": 5,
        "max_output_bytes": 1024,
        "blocked_patterns": (
            re.compile(r"git\s+push\s+.*--force", re.IGNORECASE),
            re.compile(r"rm\s+-rf\s+/", re.IGNORECASE),
        ),
        "allowed_executables": ("python", "git", "python3"),
        "network_allowlist": (),
        "allow_model_api": True,
        "audit": audit,
    }
    base.update(overrides)
    return SecurityPolicy(**base)  # type: ignore[arg-type]


# ---------- 路径 ----------


def test_path_inside_workspace_allowed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = _policy()
    assert (
        policy.check_path(workspace / "src" / "main.py", workspace=workspace)
        == (workspace / "src" / "main.py").resolve()
    )


def test_path_outside_workspace_denied_and_audited(tmp_path: Path, audit: AuditLogger) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = _policy(audit)
    with pytest.raises(SecurityDenied, match="不在授权工作区") as exc_info:
        policy.check_path(tmp_path / "outside.txt", workspace=workspace)
    assert exc_info.value.kind == "path"
    events = audit.list_events()
    assert events[-1].action == "security.denied"
    assert events[-1].detail["kind"] == "path"


def test_traversal_out_of_workspace_denied(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = _policy()
    with pytest.raises(SecurityDenied, match="不在授权工作区"):
        policy.check_path(workspace / ".." / "secret.txt", workspace=workspace)


def test_forbidden_system_path_denied_even_with_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = _policy()
    with pytest.raises(SecurityDenied, match="禁止路径"):
        policy.check_path("C:/Windows/System32/drivers/etc/hosts", workspace=workspace)


def test_forbidden_ssh_path_denied(tmp_path: Path) -> None:
    policy = _policy()
    with pytest.raises(SecurityDenied, match="禁止路径"):
        policy.check_path(Path.home() / ".ssh" / "id_rsa", workspace=tmp_path)


def test_home_root_not_implicitly_authorized(tmp_path: Path, audit: AuditLogger) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = _policy(audit)
    with pytest.raises(SecurityDenied, match="不在授权工作区"):
        policy.check_path(Path.home(), workspace=workspace)


def test_no_roots_denies_all_paths(tmp_path: Path) -> None:
    policy = _policy()
    with pytest.raises(SecurityDenied, match="未授权任何工作区"):
        policy.check_path(tmp_path / "x.txt")


# ---------- 命令 ----------


def test_allowed_command_returns_argv(tmp_path: Path) -> None:
    policy = _policy()
    assert policy.check_command("python -m pytest") == ["python", "-m", "pytest"]
    assert policy.check_command(["git", "status"]) == ["git", "status"]


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main --force",
        "rm -rf /",
        "RM -RF /tmp/x",
    ],
)
def test_dangerous_commands_blocked(command: str, audit: AuditLogger) -> None:
    policy = _policy(audit)
    with pytest.raises(SecurityDenied, match="高危命令"):
        policy.check_command(command)
    assert audit.list_events()[-1].action == "security.denied"


def test_unlisted_command_denied_in_enforce_mode(tmp_path: Path, audit: AuditLogger) -> None:
    policy = _policy(audit)
    with pytest.raises(SecurityDenied, match="白名单"):
        policy.check_command("curl https://example.com")
    assert audit.list_events()[-1].detail["kind"] == "command"


def test_audit_mode_allows_unlisted_but_records(audit: AuditLogger) -> None:
    policy = _policy(audit, command_whitelist_mode="audit")
    assert policy.check_command("curl https://example.com") == ["curl", "https://example.com"]
    assert audit.list_events()[-1].action == "security.command_unlisted"


def test_disabled_mode_allows_unlisted_but_still_blocks_dangerous() -> None:
    policy = _policy(command_whitelist_mode="disabled")
    assert policy.check_command("curl https://example.com")[0] == "curl"
    with pytest.raises(SecurityDenied, match="高危命令"):
        policy.check_command("rm -rf /")


def test_config_loading_includes_whitelist_and_limits() -> None:
    policy = SecurityPolicy.from_config()
    assert policy.check_command("python -m pytest") == ["python", "-m", "pytest"]
    with pytest.raises(SecurityDenied, match="高危命令"):
        policy.check_command("git push origin main --force")
    with pytest.raises(SecurityDenied, match="白名单"):
        policy.check_command("curl https://example.com")


# ---------- 受控命令执行 ----------


def test_run_command_success(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = _policy()
    outcome = policy.run_command([sys.executable, "-c", "print('ok')"], workspace=workspace)
    assert outcome.exit_code == 0
    assert outcome.stdout.strip() == "ok"
    assert outcome.timed_out is False


def test_run_command_nonzero_exit_reported(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outcome = _policy().run_command(
        [sys.executable, "-c", "import sys; sys.exit(3)"], workspace=workspace
    )
    assert outcome.exit_code == 3
    assert outcome.timed_out is False


def test_run_command_output_truncated(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = _policy(max_output_bytes=20)
    outcome = policy.run_command([sys.executable, "-c", "print('x' * 100)"], workspace=workspace)
    assert outcome.stdout_truncated is True
    assert "输出已截断" in outcome.stdout
    assert len(outcome.stdout.encode("utf-8")) < 100


def test_run_command_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outcome = _policy().run_command(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        workspace=workspace,
        timeout_seconds=0.3,
    )
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    assert "超时" in outcome.stderr


def test_run_command_cwd_outside_workspace_denied(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(SecurityDenied):
        _policy().run_command(
            [sys.executable, "-c", "print(1)"],
            workspace=workspace,
            cwd=tmp_path / "other",
        )


# ---------- 网络 ----------


def test_network_allowlist_matches_subdomain() -> None:
    policy = _policy(network_allowlist=("example.com",))
    assert policy.check_network("api.example.com") == "api.example.com"


def test_empty_allowlist_denies_general_egress(audit: AuditLogger) -> None:
    policy = _policy(audit)
    with pytest.raises(SecurityDenied, match="egress_allowlist"):
        policy.check_network("example.com")
    assert audit.list_events()[-1].detail["kind"] == "network"


def test_model_api_allowed_by_purpose() -> None:
    policy = _policy()
    assert policy.check_network("api.openai.com", purpose="model_api") == "api.openai.com"


def test_model_api_denied_when_disabled() -> None:
    policy = _policy(allow_model_api=False)
    with pytest.raises(SecurityDenied):
        policy.check_network("api.openai.com", purpose="model_api")
