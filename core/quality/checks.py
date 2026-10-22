"""检查适配器框架（规范 §7.4、§16.5；实施计划 P3-3）。

五类检查（:class:`CheckKind`）统一产出 :class:`CheckResult`：命令 / 工具版本 /
退出码 / 摘要 / 产物路径 / 测试计数，结论为 §16.5 四态（PASSED / FAILED /
UNVERIFIED / NOT_APPLICABLE）。

红线落实：
- 工具不可用必须 UNVERIFIED，并给出原因与可复现命令——未验证不得计入通过（红线 2）；
- 适配器只报告执行事实（退出码、输出、计数），无权把失败改写为成功；
- 结果可经 :meth:`CheckResult.to_test_report` 转为 TestReport 实体，直接进入
  门禁判定与任务状态机（§9.2：状态转移只由证据驱动）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from core.domain.entities import TestReport, new_id
from core.domain.enums import TestStatus

DEFAULT_TIMEOUT_SECONDS = 300  # 对齐 config/policies.yaml commands.timeout_seconds
SUMMARY_MAX_CHARS = 4000  # 摘要上限（config/policies.yaml commands.max_output_bytes 的摘要面）


class CheckKind(StrEnum):
    """五类自动检查（§7.2.4/§16.4；与规则 check.kind 的映射见 core/quality/gate.py）。"""

    FORMAT = "format"
    LINT = "lint"
    TYPE_CHECK = "type_check"
    TEST = "test"
    BUILD = "build"


@dataclass(frozen=True)
class CheckResult:
    """一次检查执行的统一结果（§7.4 证据字段 + §16.5 四态）。"""

    kind: CheckKind
    tool: str  # 工具名（ruff/mypy/pytest/...）
    status: TestStatus
    command: str = ""  # 实际执行命令（未执行可为空串，但 UNVERIFIED 须提供可复现命令）
    tool_version: str = ""
    exit_code: int | None = None  # 未执行为 None
    summary: str = ""
    artifact_paths: tuple[str, ...] = ()  # 产物位置（§7.4）
    unverified_reason: str = ""  # status=UNVERIFIED 时必填：原因 + 可复现命令
    total: int = 0  # 测试计数（非 TEST 类检查恒为 0）
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    not_run: int = 0
    started_at: str = ""
    finished_at: str = ""

    def __post_init__(self) -> None:
        if self.status is TestStatus.UNVERIFIED and not self.unverified_reason.strip():
            raise ValueError("UNVERIFIED 必须给出原因与可复现命令（§16.5、红线 2）")
        if self.status in (TestStatus.PASSED, TestStatus.FAILED):
            if not self.command.strip():
                raise ValueError("已执行的检查必须记录实际命令（§7.4）")
            if self.exit_code is None:
                raise ValueError("已执行的检查必须记录退出码（§7.4）")

    def to_test_report(self, task_id: str) -> TestReport:
        """转为门禁/状态机使用的测试报告实体（证据字段原样透传）。"""
        return TestReport(
            id=new_id("TRUN"),
            task_id=task_id,
            tool=self.tool,
            command=self.command,
            status=self.status,
            tool_version=self.tool_version,
            exit_code=self.exit_code,
            total=self.total,
            passed=self.passed,
            failed=self.failed,
            skipped=self.skipped,
            not_run=self.not_run,
            artifact_path=self.artifact_paths[0] if self.artifact_paths else "",
            unverified_reason=self.unverified_reason,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


class CheckAdapter(Protocol):
    """检查适配器协议：对给定工作区真实执行一类检查。

    实现约束：subprocess 必须使用 argv 列表且禁 shell=True（§17）；工具不可用或
    环境受限时返回 UNVERIFIED（含原因与可复现命令），不得伪造执行结果。
    """

    def supported_kinds(self) -> frozenset[CheckKind]:
        """该适配器可执行的检查类别。"""
        ...

    def run(
        self,
        kind: CheckKind,
        workdir: Path,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> CheckResult:
        """在 workdir 下执行指定类别的检查并返回统一结果。"""
        ...
