"""工具权限层（实施计划 P4-2；规范 §17；config/policies.yaml）。

职责：
- 文件路径：只允许授权工作区（``workspace.allowed_roots`` + 调用方传入的
  受控工作区）；``forbidden_paths`` 为先硬禁止，工作区授权不得放宽；
- 命令：高危特征正则拦截 + 可执行文件白名单（``whitelist_mode``
  enforce/audit/disabled）；subprocess 一律 argv 列表且禁 shell；
- 命令执行：超时与输出上限（``commands.timeout_seconds`` /
  ``max_output_bytes``），超限输出显式截断并标记；
- 网络：目标主机须在 ``network.egress_allowlist``，空表即全部按审批拒绝；
  模型 API 在 ``allow_model_api`` 允许时按功能必需放行。

每次拦截写审计 ``security.denied``（含类别、对象、原因），密钥/凭据由
AuditLogger 统一脱敏。未授权即拒绝，不存在静默放行路径。
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.audit.logger import AuditLogger

DEFAULT_POLICIES = Path(__file__).resolve().parents[2] / "config" / "policies.yaml"
SECURITY_ACTOR = "安全策略层"
TRUNCATION_MARKER = "……（输出已截断）\n"


class SecurityDenied(PermissionError):
    """越权操作被策略层拒绝（携带类别与原因，供审计与上层处置）。"""

    def __init__(self, kind: str, subject: str, reason: str) -> None:
        self.kind = kind
        self.subject = subject
        self.reason = reason
        super().__init__(f"[{kind}] 拒绝访问 {subject!r}: {reason}")


@dataclass(frozen=True)
class CommandOutcome:
    """一次受控命令执行结果（输出已按上限截断并显式标记）。"""

    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    duration_ms: int = 0


def _is_within(path: Path, root: Path) -> bool:
    """path 是否等于 root 或位于其下（已 resolve，符号链接逃逸不可用）。"""
    return path == root or root in path.parents


class SecurityPolicy:
    """权限策略：路径、命令、网络与受控命令执行。"""

    def __init__(
        self,
        *,
        allowed_roots: Sequence[Path | str] = (),
        forbidden_paths: Sequence[Path | str] = (),
        whitelist_only: bool = True,
        allow_outside_workspace: bool = False,
        command_whitelist_mode: str = "enforce",
        command_timeout_seconds: int = 300,
        max_output_bytes: int = 1048576,
        blocked_patterns: Sequence[re.Pattern[str]] = (),
        allowed_executables: Sequence[str] = (),
        network_allowlist: Sequence[str] = (),
        allow_model_api: bool = True,
        audit: AuditLogger | None = None,
    ) -> None:
        if command_whitelist_mode not in {"enforce", "audit", "disabled"}:
            raise ValueError(f"未知命令白名单模式: {command_whitelist_mode!r}")
        self._home = Path.home().resolve()
        self._allowed_roots = tuple(self.resolve_path(p) for p in allowed_roots)
        self._forbidden = tuple(self.resolve_path(p) for p in forbidden_paths)
        self._whitelist_only = whitelist_only
        self._allow_outside_workspace = allow_outside_workspace
        self._command_mode = command_whitelist_mode
        self._timeout_seconds = command_timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._blocked = tuple(blocked_patterns)
        self._allowed_executables = {e.lower() for e in allowed_executables}
        self._network_allowlist = tuple(h.lower() for h in network_allowlist)
        self._allow_model_api = allow_model_api
        self._audit = audit

    @classmethod
    def from_config(
        cls, path: Path | str | None = None, *, audit: AuditLogger | None = None
    ) -> SecurityPolicy:
        """加载 config/policies.yaml；缺文件/解析失败抛 OSError/ValueError（不静默降级）。"""
        config_path = Path(path) if path else DEFAULT_POLICIES
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        workspace = data.get("workspace") or {}
        commands = data.get("commands") or {}
        network = data.get("network") or {}
        return cls(
            allowed_roots=tuple(workspace.get("allowed_roots") or ()),
            forbidden_paths=tuple(workspace.get("forbidden_paths") or ()),
            whitelist_only=bool(workspace.get("whitelist_only", True)),
            allow_outside_workspace=bool(workspace.get("allow_outside_workspace", False)),
            command_whitelist_mode=str(commands.get("whitelist_mode", "enforce")),
            command_timeout_seconds=int(commands.get("timeout_seconds", 300)),
            max_output_bytes=int(commands.get("max_output_bytes", 1048576)),
            blocked_patterns=tuple(
                re.compile(str(p), re.IGNORECASE) for p in commands.get("blocked_patterns") or ()
            ),
            allowed_executables=tuple(str(x) for x in commands.get("allowed_executables") or ()),
            network_allowlist=tuple(str(h) for h in network.get("egress_allowlist") or ()),
            allow_model_api=bool(network.get("allow_model_api", True)),
            audit=audit,
        )

    # ---------- 路径 ----------

    @staticmethod
    def resolve_path(path: Path | str) -> Path:
        """展开 ~ 并解析为绝对路径（不存在也解析，用于越权判定）。"""
        return Path(path).expanduser().resolve()

    def check_path(
        self,
        path: Path | str,
        *,
        workspace: Path | str | None = None,
        write: bool = False,
    ) -> Path:
        """校验路径可访问性；越权抛 :class:`SecurityDenied` 并写审计。"""
        resolved = self.resolve_path(path)
        for forbidden in self._forbidden:
            # "~/" 条目表示用户主目录本身不隐含授权（子路径仍须工作区授权），
            # 其余禁止路径为先硬禁止，工作区授权不得放宽。
            if forbidden == self._home:
                continue
            if _is_within(resolved, forbidden):
                self._deny(
                    "path",
                    str(resolved),
                    f"命中禁止路径 {forbidden}（config/policies.yaml forbidden_paths）",
                )
        if self._whitelist_only and not self._allow_outside_workspace:
            roots = list(self._allowed_roots)
            if workspace is not None:
                roots.append(self.resolve_path(workspace))
            if not roots:
                self._deny(
                    "path",
                    str(resolved),
                    "未授权任何工作区（workspace.allowed_roots 为空且未传入 workspace）",
                )
            if not any(_is_within(resolved, root) for root in roots):
                self._deny("path", str(resolved), "路径不在授权工作区内")
        return resolved

    # ---------- 命令 ----------

    def check_command(self, command: str | Sequence[str]) -> list[str]:
        """校验命令：危险特征 → 拒绝；白名单模式决定未登记可执行文件处置。"""
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        if not argv:
            self._deny("command", "", "空命令")
        text = command if isinstance(command, str) else shlex.join(argv)
        for pattern in self._blocked:
            if pattern.search(text):
                self._deny("command", text, f"命中高危命令特征 /{pattern.pattern}/")
        executable = Path(argv[0]).name.lower()
        keys = {executable}
        if "." in executable:
            keys.add(executable.rsplit(".", 1)[0])
        listed = bool(keys & self._allowed_executables)
        if self._command_mode == "enforce" and not listed:
            self._deny(
                "command",
                text,
                "可执行文件不在白名单（commands.allowed_executables，whitelist_mode=enforce）",
            )
        if self._command_mode == "audit" and not listed:
            self._audit_warning("security.command_unlisted", text)
        return argv

    def run_command(
        self,
        command: str | Sequence[str],
        *,
        workspace: Path | str,
        cwd: Path | str | None = None,
        timeout_seconds: int | None = None,
    ) -> CommandOutcome:
        """在受控工作区内执行命令：argv 列表、禁 shell、超时、输出上限。"""
        argv = self.check_command(command)
        workdir = self.check_path(cwd if cwd is not None else workspace, workspace=workspace)
        timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=workdir,
                capture_output=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout, stdout_cut = self._truncate(exc.stdout)
            stderr, stderr_cut = self._truncate(exc.stderr)
            return CommandOutcome(
                argv=tuple(argv),
                exit_code=None,
                stdout=stdout,
                stderr=(stderr + f"\n命令执行超时（{timeout}s）").strip(),
                stdout_truncated=stdout_cut,
                stderr_truncated=stderr_cut,
                timed_out=True,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        stdout, stdout_cut = self._truncate(proc.stdout)
        stderr, stderr_cut = self._truncate(proc.stderr)
        return CommandOutcome(
            argv=tuple(argv),
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_cut,
            stderr_truncated=stderr_cut,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _truncate(self, data: bytes | None) -> tuple[str, bool]:
        raw = data or b""
        if len(raw) <= self._max_output_bytes:
            return raw.decode("utf-8", errors="replace"), False
        tail = raw[-self._max_output_bytes :]
        return TRUNCATION_MARKER + tail.decode("utf-8", errors="replace"), True

    # ---------- 网络 ----------

    def check_network(self, host: str, *, purpose: str = "general") -> str:
        """校验外联目标：允许列表匹配（含子域）或模型 API 允许用途。"""
        normalized = host.strip().lower()
        if not normalized:
            self._deny("network", host, "空主机名")
        if purpose == "model_api" and self._allow_model_api:
            return normalized
        if any(
            normalized == allowed or normalized.endswith("." + allowed)
            for allowed in self._network_allowlist
        ):
            return normalized
        self._deny(
            "network",
            normalized,
            "外联目标不在 egress_allowlist；空表时全部外联须逐项审批",
        )
        raise AssertionError("unreachable")  # pragma: no cover - _deny 必抛

    # ---------- 审计 ----------

    def _deny(self, kind: str, subject: str, reason: str) -> None:
        if self._audit is not None:
            self._audit.log(
                "security.denied",
                SECURITY_ACTOR,
                detail={"kind": kind, "subject": subject, "reason": reason},
            )
        raise SecurityDenied(kind, subject, reason)

    def _audit_warning(self, action: str, subject: str) -> None:
        if self._audit is not None:
            self._audit.log(action, SECURITY_ACTOR, detail={"subject": subject})
