"""质量报告（规范 §16.5；实施计划 P3-6）。

将门禁结论（core/quality/gate.py :class:`GateOutcome`）渲染为单任务质量报告：

- 总体状态三态（§16.5）：通过 / 带条件通过 / 未通过。
  - 有未豁免 BLOCKER 失败、未映射 FAILED 结果或任何 UNVERIFIED → 未通过
    （红线 2：未验证不得计入通过）；
  - 有已豁免失败或非阻断失败 → 带条件通过；
  - 否则 → 通过。
- 报告逐规则列出判定；UNVERIFIED 行显示"未验证"并附原因与可复现命令——
  工具缺失的检查在报告中绝不显示为"通过"（完成标准）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from core.domain.entities import Task, utc_now
from core.domain.enums import TestStatus
from core.quality.checks import CheckResult
from core.quality.gate import GateOutcome, RuleFinding

STATUS_LABELS: dict[TestStatus, str] = {
    TestStatus.PASSED: "通过",
    TestStatus.FAILED: "失败",
    TestStatus.UNVERIFIED: "未验证",
    TestStatus.NOT_APPLICABLE: "不适用",
}
"""检查四态（§16.5）的报告显示文案。"""


class OverallStatus(StrEnum):
    """质量报告总体状态（§16.5 三态）。"""

    PASSED = "通过"
    CONDITIONAL = "带条件通过"
    FAILED = "未通过"


def overall_status(outcome: GateOutcome) -> OverallStatus:
    """由门禁结论汇总总体状态：阻断失败/未验证 → 未通过；豁免/非阻断失败 → 带条件通过。"""
    if outcome.blocking_failures or outcome.unmapped_failures or outcome.unverified_findings:
        return OverallStatus.FAILED
    if outcome.waived_findings or outcome.non_blocking_failures:
        return OverallStatus.CONDITIONAL
    return OverallStatus.PASSED


def _cell(text: str) -> str:
    """Markdown 表格单元格转义。"""
    return text.replace("|", "\\|").replace("\n", " ").strip() or "—"


def _finding_row(finding: RuleFinding) -> str:
    status = STATUS_LABELS[finding.status]
    if finding.waived:
        status = f"失败（已豁免 {finding.waiver_id}）"
    tool = "—"
    detail = "未执行对应类别检查（工具不可用或未运行）"
    if finding.check_result is not None:
        result = finding.check_result
        tool = result.tool or "—"
        if finding.status is TestStatus.UNVERIFIED:
            detail = result.unverified_reason  # 含原因与可复现命令（CheckResult 契约）
        elif finding.status is TestStatus.FAILED:
            detail = result.summary or f"退出码 {result.exit_code}"
        else:
            detail = result.command
    return (
        f"| {_cell(finding.rule_id)} | {finding.severity.value} | {_cell(status)} "
        f"| {_cell(tool)} | {_cell(detail)} |"
    )


@dataclass(frozen=True)
class QualityReport:
    """单任务质量报告（§16.5）：总体状态 + 逐规则判定 + 已执行检查清单。"""

    task_id: str
    overall: OverallStatus
    findings: tuple[RuleFinding, ...]
    checks: tuple[CheckResult, ...]
    generated_at: str
    manual_rule_ids: tuple[str, ...] = ()
    custom_rule_ids: tuple[str, ...] = ()
    unmapped_failures: tuple[CheckResult, ...] = ()

    def to_markdown(self) -> str:
        """渲染为 Markdown；UNVERIFIED 行显示"未验证"+原因+可复现命令。"""
        failed = sum(1 for f in self.findings if f.status is TestStatus.FAILED)
        unverified = sum(1 for f in self.findings if f.status is TestStatus.UNVERIFIED)
        waived = sum(1 for f in self.findings if f.waived)
        lines = [
            f"# 质量报告：{self.task_id}",
            "",
            f"- 总体状态：**{self.overall.value}**",
            f"- 生成时间：{self.generated_at}",
            f"- 规则判定 {len(self.findings)} 条：失败 {failed} / 未验证 {unverified} / "
            f"已豁免 {waived}",
            "",
            "## 规则判定",
            "",
            "| 规则 | 严重度 | 状态 | 工具 | 详情 |",
            "| --- | --- | --- | --- | --- |",
            *(_finding_row(f) for f in self.findings),
        ]
        if self.manual_rule_ids:
            lines += [
                "",
                "## 人工审查规则（不进门禁判定，§9.2）",
                "",
                *(f"- {rule_id}" for rule_id in self.manual_rule_ids),
            ]
        if self.custom_rule_ids:
            lines += [
                "",
                "## 自定义验证规则（走专用验证器/CI）",
                "",
                *(f"- {rule_id}" for rule_id in self.custom_rule_ids),
            ]
        if self.unmapped_failures:
            lines += [
                "",
                "## 未映射到规则的失败（同样阻断，不得静默忽略）",
                "",
                *(f"- {r.tool}: {r.command}" for r in self.unmapped_failures),
            ]
        if self.checks:
            lines += [
                "",
                "## 已执行检查",
                "",
                "| 类别 | 工具 | 状态 | 退出码 | 命令 |",
                "| --- | --- | --- | --- | --- |",
                *(
                    f"| {r.kind.value} | {_cell(r.tool)} | {STATUS_LABELS[r.status]} | "
                    f"{r.exit_code if r.exit_code is not None else '—'} | {_cell(r.command)} |"
                    for r in self.checks
                ),
            ]
        return "\n".join(lines) + "\n"


def build_quality_report(
    task: Task, results: Sequence[CheckResult], outcome: GateOutcome
) -> QualityReport:
    """汇总门禁结论与检查结果为质量报告（纯函数，不写审计、不驱动状态机）。"""
    return QualityReport(
        task_id=task.id,
        overall=overall_status(outcome),
        findings=outcome.findings,
        checks=tuple(results),
        generated_at=utc_now(),
        manual_rule_ids=outcome.manual_rule_ids,
        custom_rule_ids=outcome.custom_rule_ids,
        unmapped_failures=outcome.unmapped_failures,
    )
