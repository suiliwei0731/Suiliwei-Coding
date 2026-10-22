"""P3-4 单元测试：门禁执行与阻断（规范 §7.4、§9.2、§16.3；实施计划 P3-4）。

完成标准：
- 门禁失败（BLOCKER 规则 FAILED）→ 任务转 REWORK（重试预算内）/ FAILED（耗尽）；
- 全部通过 → PASSED；存在 UNVERIFIED → BLOCKED（未验证不得计入通过）；
- 处置后试图把失败任务标 PASSED 被状态机拒绝（违规代码不得标 PASSED）；
- manual/custom 规则不进门禁判定，但显式列出；
- 未映射到任何规则的 FAILED 结果同样阻断（不得静默忽略失败）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.audit import AuditLogger
from core.domain import EvidenceKind, Severity, Task, TaskStatus, TestStatus, new_id
from core.orchestrator import StateStore
from core.quality import (
    CheckKind,
    CheckResult,
    GateError,
    GateExecutor,
    evaluate,
)
from core.quality.gate import GATE_ACTOR
from core.standards import Rule, RuleCheck, RuleCheckKind, RuleWaiverPolicy
from core.workflow import (
    Evidence,
    GuardRejectionError,
    IllegalTransitionError,
    TaskStateMachine,
    TransitionContext,
)

S = TaskStatus


# ---------- 构造辅助 ----------


def make_task(status: S = S.TEST_REQUIRED, **overrides: object) -> Task:
    task = Task(
        id=new_id("TASK"),
        project_id="PRJ-test",
        title="实现登录 API",
        owner_role="backend",
        acceptance_criteria=["登录成功返回令牌"],
    )
    task.status = status
    for key, value in overrides.items():
        setattr(task, key, value)
    return task


def make_rule(
    rule_id: str = "R-LINT",
    kind: RuleCheckKind | None = RuleCheckKind.STATIC_ANALYSIS,
    severity: Severity = Severity.BLOCKER,
    check_type: str = "auto",
) -> Rule:
    return Rule(
        id=rule_id,
        name=f"规则 {rule_id}",
        scope="code",
        description="要求",
        rationale="原因",
        good_examples=("好例",),
        bad_examples=("坏例",),
        check=RuleCheck(type=check_type, method="工具/审查", kind=kind),
        severity=severity,
        waiver=RuleWaiverPolicy(allowed=False),
    )


def make_result(
    kind: CheckKind = CheckKind.LINT,
    status: TestStatus = TestStatus.PASSED,
    tool: str = "ruff",
) -> CheckResult:
    if status is TestStatus.UNVERIFIED:
        return CheckResult(
            kind=kind,
            tool=tool,
            status=status,
            unverified_reason=f"{tool} 不可用；可复现命令: python -m {tool}",
        )
    return CheckResult(
        kind=kind,
        tool=tool,
        status=status,
        command=f"python -m {tool}",
        exit_code=1 if status is TestStatus.FAILED else 0,
    )


def make_executor(store: StateStore) -> tuple[GateExecutor, AuditLogger]:
    audit = AuditLogger(store)
    return GateExecutor(TaskStateMachine(), audit), audit


# ---------- evaluate 纯函数：单规则按类别聚合 ----------


def test_evaluate_passed_aggregation() -> None:
    task = make_task()
    outcome = evaluate(task, [make_rule()], [make_result()])
    assert outcome.passed
    finding = outcome.findings[0]
    assert finding.rule_id == "R-LINT" and finding.status is TestStatus.PASSED
    assert finding.check_result is not None


def test_evaluate_missing_check_is_unverified() -> None:
    """类别无结果 → UNVERIFIED（未验证），不得计入通过。"""
    outcome = evaluate(make_task(), [make_rule()], [])
    assert not outcome.passed
    finding = outcome.findings[0]
    assert finding.status is TestStatus.UNVERIFIED and finding.check_result is None
    assert outcome.unverified_findings == (finding,)


def test_evaluate_failed_dominates_regardless_of_order() -> None:
    results = [make_result(status=TestStatus.PASSED), make_result(status=TestStatus.FAILED)]
    outcome = evaluate(make_task(), [make_rule()], results)
    assert outcome.findings[0].status is TestStatus.FAILED
    assert not outcome.passed and outcome.blocking_failures == outcome.findings


def test_evaluate_unverified_when_no_failure() -> None:
    results = [make_result(status=TestStatus.UNVERIFIED), make_result(status=TestStatus.PASSED)]
    outcome = evaluate(make_task(), [make_rule()], results)
    assert outcome.findings[0].status is TestStatus.UNVERIFIED
    assert not outcome.passed


def test_evaluate_all_not_applicable() -> None:
    results = [make_result(status=TestStatus.NOT_APPLICABLE)]
    outcome = evaluate(make_task(), [make_rule()], results)
    assert outcome.findings[0].status is TestStatus.NOT_APPLICABLE
    assert outcome.passed  # 不适用项不计入通过判定（§16.5）


def test_evaluate_mixed_passed_and_not_applicable() -> None:
    results = [
        make_result(status=TestStatus.NOT_APPLICABLE),
        make_result(status=TestStatus.PASSED),
    ]
    outcome = evaluate(make_task(), [make_rule()], results)
    assert outcome.findings[0].status is TestStatus.PASSED


def test_evaluate_kind_mapping_isolation() -> None:
    """FORMAT 规则只聚合 FORMAT 结果；LINT 结果不能充当 FORMAT 证据。"""
    rule = make_rule("R-FMT", kind=RuleCheckKind.FORMAT)
    outcome = evaluate(make_task(), [rule], [make_result(CheckKind.LINT)])
    assert outcome.findings[0].status is TestStatus.UNVERIFIED


def test_manual_and_custom_rules_not_gated() -> None:
    """manual/custom 规则不进门禁判定，但显式列出（不得伪装成机器强制）。"""
    manual = make_rule("R-MAN", kind=None, check_type="manual")
    custom = make_rule("R-CUS", kind=RuleCheckKind.CUSTOM)
    outcome = evaluate(make_task(), [manual, custom], [])
    assert outcome.findings == ()
    assert outcome.manual_rule_ids == ("R-MAN",)
    assert outcome.custom_rule_ids == ("R-CUS",)


def test_unmapped_failed_result_blocks_pass() -> None:
    """未映射到任何规则的 FAILED 结果同样阻断：失败不得被静默忽略。"""
    results = [make_result(), make_result(CheckKind.BUILD, TestStatus.FAILED, "build")]
    outcome = evaluate(make_task(), [make_rule()], results)
    assert not outcome.passed
    assert [r.tool for r in outcome.unmapped_failures] == ["build"]


# ---------- GateExecutor.enforce：证据化处置 + 审计 ----------


def test_enforce_all_passed_transitions_to_passed(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        executor, audit = make_executor(store)
        task = make_task()
        rules = [
            make_rule("R-LINT"),
            make_rule("R-TYPE", kind=RuleCheckKind.TYPE_CHECK),
            make_rule("R-TEST", kind=RuleCheckKind.TEST),
        ]
        results = [
            make_result(CheckKind.LINT),
            make_result(CheckKind.TYPE_CHECK, tool="mypy"),
            make_result(CheckKind.TEST, tool="pytest"),
        ]
        outcome = executor.enforce(task, rules, results)
        assert outcome.passed and task.status is S.PASSED
        events = audit.list_events()
        assert events[-1].action == "gate.enforced"
        assert events[-1].actor == GATE_ACTOR and events[-1].task_id == task.id
        assert events[-1].detail["decision"] == "PASSED"
        assert len(events[-1].detail["findings"]) == 3


def test_enforce_empty_evidence_raises(tmp_path: Path) -> None:
    """无证据不得标 PASSED（红线）。"""
    with StateStore(tmp_path / "platform.db") as store:
        executor, _ = make_executor(store)
        task = make_task()
        with pytest.raises(GateError, match="无.*证据|证据"):
            executor.enforce(task, [], [])
        assert task.status is S.TEST_REQUIRED  # 任务状态未被改变


def test_enforce_blocker_failure_goes_rework(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        executor, audit = make_executor(store)
        task = make_task()
        outcome = executor.enforce(task, [make_rule()], [make_result(status=TestStatus.FAILED)])
        assert not outcome.passed and outcome.blocking_failures
        assert task.status is S.REWORK and task.retry_count == 1
        assert audit.list_events()[-1].detail["decision"] == "REWORK"


def test_enforce_blocker_failure_retry_exhausted_goes_failed(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        executor, audit = make_executor(store)
        task = make_task(retry_count=3)
        executor.enforce(task, [make_rule()], [make_result(status=TestStatus.FAILED)])
        assert task.status is S.FAILED
        assert audit.list_events()[-1].detail["decision"] == "FAILED"


def test_enforce_unverified_goes_blocked(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        executor, _ = make_executor(store)
        task = make_task()
        outcome = executor.enforce(task, [make_rule()], [make_result(status=TestStatus.UNVERIFIED)])
        assert not outcome.passed and outcome.unverified_findings
        assert task.status is S.BLOCKED
        assert task.blocked_reason is not None and "R-LINT" in task.blocked_reason
        assert "未验证" in task.blocked_reason


def test_failing_task_cannot_be_marked_passed(tmp_path: Path) -> None:
    """违规代码不得标 PASSED：FAILED 报告被门禁拒绝；处置后非法越级同样被拒。"""
    with StateStore(tmp_path / "platform.db") as store:
        executor, _ = make_executor(store)
        task = make_task()
        failed = make_result(status=TestStatus.FAILED)
        executor.enforce(task, [make_rule()], [failed])
        assert task.status is S.REWORK
        report = failed.to_test_report(task.id)
        ev = Evidence(
            kind=EvidenceKind.TEST_REPORT,
            summary="试图标过",
            refs=(report.id,),
            exit_code=0,
        )
        # 处置后状态为 REWORK：REWORK→PASSED 无合法边
        with pytest.raises(IllegalTransitionError):
            TaskStateMachine().transition(task, S.PASSED, [ev], TransitionContext())
        # 仍在 TEST_REQUIRED 的任务：FAILED 报告被 _guard_passed 拒绝
        task2 = make_task()
        with pytest.raises(GuardRejectionError, match="未通过"):
            TaskStateMachine().transition(
                task2, S.PASSED, [ev], TransitionContext(test_reports=(report,))
            )
        assert task2.status is S.TEST_REQUIRED


def test_non_blocking_failure_does_not_block(tmp_path: Path) -> None:
    """非 BLOCKER 失败不阻断（§7.2.5），但失败判定完整记入结论与审计。"""
    with StateStore(tmp_path / "platform.db") as store:
        executor, audit = make_executor(store)
        task = make_task()
        rules = [
            make_rule("R-LINT", severity=Severity.MAJOR),
            make_rule("R-TEST", kind=RuleCheckKind.TEST),
        ]
        results = [
            make_result(CheckKind.LINT, TestStatus.FAILED),
            make_result(CheckKind.TEST, tool="pytest"),
        ]
        outcome = executor.enforce(task, rules, results)
        assert outcome.passed and task.status is S.PASSED
        assert [f.rule_id for f in outcome.non_blocking_failures] == ["R-LINT"]
        detail = audit.list_events()[-1].detail
        assert detail["decision"] == "PASSED"
        statuses = {f["rule_id"]: f["status"] for f in detail["findings"]}
        assert statuses["R-LINT"] == "FAILED" and statuses["R-TEST"] == "PASSED"


def test_enforce_requires_test_required(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        executor, _ = make_executor(store)
        with pytest.raises(GateError, match="TEST_REQUIRED"):
            executor.enforce(make_task(S.IN_PROGRESS), [make_rule()], [make_result()])
