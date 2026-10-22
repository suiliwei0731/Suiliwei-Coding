"""Python 检查适配器（实施计划 P3-3；ADR-005：adapters/tools 为语言工具执行层）。

- 全部经 ``sys.executable -m <tool>`` 调用，subprocess argv 列表且禁 shell=True（§17）；
- 工具模块缺失 / 执行超时 → UNVERIFIED + 原因与可复现命令（§16.5），不得计入通过；
- pytest 产出 JUnit XML：计数（total/passed/failed/skipped）与产物路径入证据（§7.4）。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from core.domain.entities import utc_now
from core.domain.enums import TestStatus
from core.quality.checks import (
    DEFAULT_TIMEOUT_SECONDS,
    SUMMARY_MAX_CHARS,
    CheckKind,
    CheckResult,
)

# 每类检查对应的工具模块（探测与版本查询均以模块名为准）
_KIND_MODULE: dict[CheckKind, str] = {
    CheckKind.FORMAT: "ruff",
    CheckKind.LINT: "ruff",
    CheckKind.TYPE_CHECK: "mypy",
    CheckKind.TEST: "pytest",
    CheckKind.BUILD: "build",
}


def _argv(kind: CheckKind, junit_path: Path | None) -> list[str]:
    """构造 argv 列表（禁 shell 字符串；§17 命令白名单友好）。"""
    module = _KIND_MODULE[kind]
    argv = [sys.executable, "-m", module]
    if kind is CheckKind.FORMAT:
        return [*argv, "format", "--check", "."]
    if kind is CheckKind.LINT:
        return [*argv, "check", "."]
    if kind is CheckKind.TEST:
        assert junit_path is not None
        return [*argv, "-q", f"--junitxml={junit_path}"]
    if kind is CheckKind.TYPE_CHECK:
        return [*argv, "."]
    return argv  # BUILD: python -m build（对当前目录执行）


def _tool_version(module: str) -> str:
    try:
        return importlib.metadata.version(module)
    except importlib.metadata.PackageNotFoundError:
        return ""


def _summarize(stdout: str, stderr: str) -> str:
    text = stdout.strip() or stderr.strip()
    if len(text) > SUMMARY_MAX_CHARS:
        return "……（截断）" + text[-SUMMARY_MAX_CHARS:]
    return text


def _parse_junit(path: Path) -> dict[str, int]:
    """从 JUnit XML 提取测试计数；解析失败返回空 dict（计数保持 0）。"""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return {}
    total = int(suite.get("tests", 0))
    failed = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    skipped = int(suite.get("skipped", 0))
    return {
        "total": total,
        "failed": failed,
        "skipped": skipped,
        "passed": total - failed - skipped,
    }


class PythonCheckAdapter:
    """Python 工具链适配器：ruff（format/lint）、mypy、pytest、build。"""

    def __init__(self, artifacts_dir: Path | str | None = None) -> None:
        # 默认对齐 config/quality-gates.yaml evidence.storage（runtime/artifacts）
        self._artifacts_dir = Path(artifacts_dir) if artifacts_dir else Path("runtime/artifacts")

    def supported_kinds(self) -> frozenset[CheckKind]:
        return frozenset(CheckKind)

    def run(
        self,
        kind: CheckKind,
        workdir: Path | str,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> CheckResult:
        """真实执行检查；工具缺失/超时返回 UNVERIFIED（含可复现命令）。"""
        workdir = Path(workdir)
        module = _KIND_MODULE[kind]
        junit_path: Path | None = None
        if kind is CheckKind.TEST:
            self._artifacts_dir.mkdir(parents=True, exist_ok=True)
            junit_path = self._artifacts_dir / f"junit-{uuid4().hex[:12]}.xml"
        argv = _argv(kind, junit_path)
        command = shlex.join(argv)

        if importlib.util.find_spec(module) is None:
            return CheckResult(
                kind=kind,
                tool=module,
                status=TestStatus.UNVERIFIED,
                command=command,
                unverified_reason=f"工具不可用：当前环境未安装模块 {module}；可复现命令：{command}",
            )
        version = _tool_version(module)
        started = utc_now()
        try:
            proc = subprocess.run(
                argv,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                kind=kind,
                tool=module,
                status=TestStatus.UNVERIFIED,
                command=command,
                tool_version=version,
                started_at=started,
                finished_at=utc_now(),
                unverified_reason=(
                    f"执行超时（{timeout_seconds}s，config/policies.yaml "
                    f"commands.timeout_seconds），结果未产生；可复现命令：{command}"
                ),
            )
        finished = utc_now()
        junit_produced = junit_path is not None and junit_path.exists()
        counts = _parse_junit(junit_path) if junit_produced and junit_path is not None else {}
        return CheckResult(
            kind=kind,
            tool=module,
            status=TestStatus.PASSED if proc.returncode == 0 else TestStatus.FAILED,
            command=command,
            tool_version=version,
            exit_code=proc.returncode,
            summary=_summarize(proc.stdout, proc.stderr),
            artifact_paths=(str(junit_path),) if junit_produced else (),
            total=counts.get("total", 0),
            passed=counts.get("passed", 0),
            failed=counts.get("failed", 0),
            skipped=counts.get("skipped", 0),
            started_at=started,
            finished_at=finished,
        )
