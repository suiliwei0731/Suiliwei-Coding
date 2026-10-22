"""门禁执行与阻断（规范 §7.4、§9.2、§16.3/§16.4；实施计划 P3-4）。

将注入任务的平台规则（core.standards）与检查适配器的统一结果
（core.quality.checks）汇总为门禁结论，并驱动任务状态机落地处置：

- 规则检查类别映射：FORMAT→FORMAT、STATIC_ANALYSIS→LINT、TYPE_CHECK→TYPE_CHECK、
  TEST→TEST；CUSTOM 规则走专用验证器/CI、manual 规则走审查流（§9.2），
  两者均不进本门禁判定，只在结论中显式列出（不得静默丢弃）。
- 单规则结论按其检查类别聚合结果：类别无结果→UNVERIFIED；有 FAILED→FAILED；
  有 UNVERIFIED→UNVERIFIED；全 NOT_APPLICABLE→NOT_APPLICABLE；否则 PASSED。
- 处置（§7.4、§16.3 阻断语义）：
  - 有未豁免 BLOCKER 失败（或未映射到任何规则的 FAILED 结果）→ REWORK
    （重试预算内）/ FAILED（预算耗尽）；
  - 无阻断失败但有 UNVERIFIED → BLOCKED（工具/环境阻塞，须说明原因，§16.5）；
  - 否则 → PASSED。非 BLOCKER 失败不阻断（§7.2.5：BLOCKER 为阻断），其报告
    不作为 PASSED 依据（状态机红线：FAILED 报告不得支持 PASSED），但完整记入
    findings/审计/质量报告（§16.5"带条件通过"）。

红线落实：
- 无任何已执行检查证据时拒绝标 PASSED（无证据不得通过）；
- 处置转移只由本控制器依据检查结果证据触发，失败/未验证绝不改写为通过；
- 每次处置写审计（action=gate.enforced，含结论与各规则判定明细）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.audit.logger import AuditLogger
from core.domain.entities import Task
from core.domain.enums import EvidenceKind, Severity, TaskStatus, TestStatus
from core.quality.checks import CheckKind, CheckResult
from core.standards.rules import Rule, RuleCheckKind
from core.workflow.evidence import Evidence
from core.workflow.machine import TaskStateMachine, TransitionContext

GATE_ACTOR = "质量门禁控制器"

RULE_TO_CHECK_KIND: dict[RuleCheckKind, CheckKind] = {
    RuleCheckKind.FORMAT: CheckKind.FORMAT,
    RuleCheckKind.STATIC_ANALYSIS: CheckKind.LINT,
    RuleCheckKind.TYPE_CHECK: CheckKind.TYPE_CHECK,
    RuleCheckKind.TEST: CheckKind.TEST,
}
"""规则检查类别 → 统一检查类别；CUSTOM 不映射（走专用验证器/CI）。"""


class GateError(RuntimeError):
    """门禁执行前置条件不满足（如无证据却判定通过、任务不在待测试状态）。"""


@dataclass(frozen=True)
class RuleFinding:
    """单条规则的门禁判定。"""

    rule_id: str
    severity: Severity
    status: TestStatus
    check_result: CheckResult | None = None  # 未执行（类别无结果）时为 None
    waived: bool = False
    waiver_id: str | None = None


@dataclass(frozen=True)
class GateOutcome:
    """一次门禁评估结论（纯数据，无副作用）。"""

    task_id: str
    findings: tuple[RuleFinding, ...]
    manual_rule_ids: tuple[str, ...] = ()
    custom_rule_ids: tuple[str, ...] = ()
    unmapped_failures: tuple[CheckResult, ...] = ()  # 未映射到任何规则的 FAILED 结果

    @property
    def failed_findings(self) -> tuple[RuleFinding, ...]:
        """未豁免的失败判定（任何严重度）。"""
        return tuple(f for f in self.findings if f.status is TestStatus.FAILED and not f.waived)

    @property
    def blocking_failures(self) -> tuple[RuleFinding, ...]:
        """阻断失败：BLOCKER 且 FAILED 且未豁免（§16.3、config/quality-gates.yaml）。"""
        return tuple(f for f in self.failed_findings if f.severity is Severity.BLOCKER)

    @property
    def non_blocking_failures(self) -> tuple[RuleFinding, ...]:
        """非阻断失败：记录但不阻止通过（§7.2.5：BLOCKER 为阻断）。"""
        return tuple(f for f in self.failed_findings if f.severity is not Severity.BLOCKER)

    @property
    def unverified_findings(self) -> tuple[RuleFinding, ...]:
        """未验证判定：任何严重度均不得计入通过（红线 2）。"""
        return tuple(f for f in self.findings if f.status is TestStatus.UNVERIFIED)

    @property
    def passed(self) -> bool:
        """门禁通过 = 无阻断失败、无未验证项、无未映射失败。"""
        return (
            not self.blocking_failures
            and not self.unverified_findings
            and not self.unmapped_failures
        )


def evaluate(
    task: Task,
    rules: Sequence[Rule],
    results: Sequence[CheckResult],
) -> GateOutcome:
    """纯函数：按规则聚合检查结果为门禁结论（不驱动状态机、不写审计）。"""
    findings: list[RuleFinding] = []
    manual_ids: list[str] = []
    custom_ids: list[str] = []
    mapped_kinds: set[CheckKind] = set()
    for rule in rules:
        if rule.check.is_manual:
            manual_ids.append(rule.id)
            continue
        kind = rule.check.kind
        if kind is None or kind is RuleCheckKind.CUSTOM:
            custom_ids.append(rule.id)
            continue
        check_kind = RULE_TO_CHECK_KIND[kind]
        mapped_kinds.add(check_kind)
        findings.append(_evaluate_rule(rule, check_kind, results))
    unmapped = tuple(
        r for r in results if r.kind not in mapped_kinds and r.status is TestStatus.FAILED
    )
    return GateOutcome(
        task_id=task.id,
        findings=tuple(findings),
        manual_rule_ids=tuple(manual_ids),
        custom_rule_ids=tuple(custom_ids),
        unmapped_failures=unmapped,
    )


def _evaluate_rule(
    rule: Rule, check_kind: CheckKind, results: Sequence[CheckResult]
) -> RuleFinding:
    """单条已映射 auto 规则按其检查类别聚合：FAILED > UNVERIFIED > N/A > PASSED。"""
    kind_results = [r for r in results if r.kind is check_kind]
    if not kind_results:
        return RuleFinding(rule_id=rule.id, severity=rule.severity, status=TestStatus.UNVERIFIED)
    failed = next((r for r in kind_results if r.status is TestStatus.FAILED), None)
    if failed is not None:
        return RuleFinding(
            rule_id=rule.id, severity=rule.severity, status=TestStatus.FAILED, check_result=failed
        )
    unverified = next((r for r in kind_results if r.status is TestStatus.UNVERIFIED), None)
    if unverified is not None:
        return RuleFinding(
            rule_id=rule.id,
            severity=rule.severity,
            status=TestStatus.UNVERIFIED,
            check_result=unverified,
        )
    if all(r.status is TestStatus.NOT_APPLICABLE for r in kind_results):
        return RuleFinding(
            rule_id=rule.id,
            severity=rule.severity,
            status=TestStatus.NOT_APPLICABLE,
            check_result=kind_results[0],
        )
    passed = next((r for r in kind_results if r.status is TestStatus.PASSED), kind_results[0])
    return RuleFinding(
        rule_id=rule.id, severity=rule.severity, status=TestStatus.PASSED, check_result=passed
    )


class GateExecutor:
    """门禁执行器：评估 → 证据化 → 驱动状态机处置 → 审计（gate.enforced）。"""

    def __init__(self, machine: TaskStateMachine, audit: AuditLogger) -> None:
        self._machine = machine
        self._audit = audit

    def enforce(
        self,
        task: Task,
        rules: Sequence[Rule],
        results: Sequence[CheckResult],
        *,
        max_retries: int = 3,
    ) -> GateOutcome:
        """对 TEST_REQUIRED 任务执行门禁处置；返回门禁结论（任务状态已按结论转移）。"""
        if task.status is not TaskStatus.TEST_REQUIRED:
            raise GateError(f"门禁处置要求任务处于 TEST_REQUIRED，实际 {task.status.value}")
        outcome = evaluate(task, rules, results)
        if outcome.passed:
            decision = self._enforce_passed(task, outcome, results)
        elif outcome.blocking_failures or outcome.unmapped_failures:
            decision = self._enforce_failed(task, outcome, results, max_retries=max_retries)
        else:
            decision = self._enforce_blocked(task, outcome)
        self._audit.log(
            "gate.enforced",
            GATE_ACTOR,
            project_id=task.project_id,
            task_id=task.id,
            detail={
                "decision": decision,
                "findings": [
                    {
                        "rule_id": f.rule_id,
                        "severity": f.severity.value,
                        "status": f.status.value,
                        "waived": f.waived,
                    }
                    for f in outcome.findings
                ],
                "manual_rule_ids": list(outcome.manual_rule_ids),
                "custom_rule_ids": list(outcome.custom_rule_ids),
                "unmapped_failures": [f"{r.tool}: {r.command}" for r in outcome.unmapped_failures],
            },
        )
        return outcome

    def _enforce_passed(
        self, task: Task, outcome: GateOutcome, results: Sequence[CheckResult]
    ) -> str:
        # 非 BLOCKER 失败的报告不作为 PASSED 依据（状态机红线：FAILED 报告不得支持
        # PASSED），但其判定完整保留在 findings 与审计中（§16.5"带条件通过"）。
        excluded = {
            id(f.check_result) for f in outcome.non_blocking_failures if f.check_result is not None
        }
        reports = tuple(r.to_test_report(task.id) for r in results if id(r) not in excluded)
        if not reports:
            raise GateError("无任何已执行检查证据，拒绝标记 PASSED（红线：无证据不得通过）")
        evidence = Evidence(
            kind=EvidenceKind.TEST_REPORT,
            summary=f"质量门禁通过：{len(reports)} 项必需检查全部通过",
            refs=tuple(r.id for r in reports),
            actor=GATE_ACTOR,
        )
        self._machine.transition(
            task, TaskStatus.PASSED, [evidence], TransitionContext(test_reports=reports)
        )
        return TaskStatus.PASSED.value

    def _enforce_failed(
        self,
        task: Task,
        outcome: GateOutcome,
        results: Sequence[CheckResult],
        *,
        max_retries: int,
    ) -> str:
        reports = tuple(r.to_test_report(task.id) for r in results)
        failed_ids = [f.rule_id for f in outcome.blocking_failures]
        failed_ids.extend(f"未映射检查({r.tool})" for r in outcome.unmapped_failures)
        exit_codes = [
            f.check_result.exit_code
            for f in outcome.blocking_failures
            if f.check_result is not None and f.check_result.exit_code is not None
        ]
        exit_codes.extend(r.exit_code for r in outcome.unmapped_failures if r.exit_code is not None)
        evidence = Evidence(
            kind=EvidenceKind.TEST_REPORT,
            summary=f"门禁阻断：{'、'.join(failed_ids)} 检查失败",
            refs=tuple(r.id for r in reports),
            exit_code=exit_codes[0] if exit_codes else 1,
            actor=GATE_ACTOR,
        )
        ctx = TransitionContext(test_reports=reports, max_retries=max_retries)
        if task.retry_count < max_retries:
            self._machine.transition(task, TaskStatus.REWORK, [evidence], ctx)
            return TaskStatus.REWORK.value
        self._machine.transition(task, TaskStatus.FAILED, [evidence], ctx)
        return TaskStatus.FAILED.value

    def _enforce_blocked(self, task: Task, outcome: GateOutcome) -> str:
        items = []
        for finding in outcome.unverified_findings:
            if finding.check_result is not None:
                items.append(f"{finding.rule_id}: {finding.check_result.unverified_reason}")
            else:
                items.append(f"{finding.rule_id}: 未执行对应类别检查（工具不可用或未运行）")
        reason = "门禁项未验证（§16.5：未验证不得计入通过）: " + "；".join(items)
        evidence = Evidence(kind=EvidenceKind.MANUAL, summary=reason, actor=GATE_ACTOR)
        self._machine.transition(
            task, TaskStatus.BLOCKED, [evidence], TransitionContext(reason=reason)
        )
        return TaskStatus.BLOCKED.value
