"""P3-5 单元测试：豁免审批与审计（规范 §7.2.6、§16.3、§17；实施计划 P3-5）。

完成标准：无豁免记录的 BLOCKER 不可跳过。
- create_waiver：理由/范围为空、规则不允许豁免、审批角色未授权、自审自批、
  有效期超上限 → 全部拒绝（一次性报告全部问题）；
- is_waiver_valid：未过期 + 范围覆盖（任务 ID / 项目 ID / "*"）才有效；
- 门禁：BLOCKER 失败 + 有效豁免 → 放行（PASSED）且写审计 waiver.applied；
  豁免过期/无豁免 → 仍阻断（REWORK）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.audit import AuditLogger
from core.domain import Severity, Task, TaskStatus, TestStatus, new_id
from core.orchestrator import StateStore
from core.quality import (
    CheckKind,
    CheckResult,
    GateExecutor,
    Waiver,
    WaiverError,
    create_waiver,
    evaluate,
    is_waiver_valid,
)
from core.standards import Rule, RuleCheck, RuleCheckKind, RuleWaiverPolicy
from core.workflow import TaskStateMachine

S = TaskStatus


# ---------- 构造辅助 ----------


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
    waiver: RuleWaiverPolicy | None = None,
) -> Rule:
    return Rule(
        id=rule_id,
        name=f"规则 {rule_id}",
        scope="code",
        description="要求",
        rationale="原因",
        good_examples=("好例",),
        bad_examples=("坏例",),
        check=RuleCheck(type="auto", method="工具", kind=kind),
        severity=severity,
        waiver=waiver if waiver is not None else RuleWaiverPolicy(allowed=False),
    )


def make_waivable_rule(rule_id: str = "R-LINT", max_validity_days: int = 30) -> Rule:
    return make_rule(
        rule_id,
        waiver=RuleWaiverPolicy(
            allowed=True, approver_roles=("tech-lead",), max_validity_days=max_validity_days
        ),
    )


def make_result(
    kind: CheckKind = CheckKind.LINT,
    status: TestStatus = TestStatus.PASSED,
    tool: str = "ruff",
) -> CheckResult:
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


def future_expiry(days: int = 7) -> str:
    return iso_z(datetime.now(UTC) + timedelta(days=days))


# ---------- create_waiver：创建即校验（§17） ----------


def test_create_waiver_ok() -> None:
    rule = make_waivable_rule()
    waiver = create_waiver(
        rule,
        reason="第三方生成代码，暂不改写",
        scope=["PRJ-test"],
        approver_role="tech-lead",
        requested_by="agent-dev",
        approved_by="human-lead",
        expires_at=future_expiry(),
    )
    assert waiver.id.startswith("WVR-") and waiver.rule_id == "R-LINT"
    assert waiver.scope == ("PRJ-test",)


def test_create_waiver_rejects_empty_reason() -> None:
    with pytest.raises(WaiverError, match="理由不能为空"):
        create_waiver(
            make_waivable_rule(),
            reason="   ",
            scope=["*"],
            approver_role="tech-lead",
            requested_by="agent-dev",
            approved_by="human-lead",
            expires_at=future_expiry(),
        )


def test_create_waiver_rejects_empty_scope() -> None:
    with pytest.raises(WaiverError, match="范围不能为空"):
        create_waiver(
            make_waivable_rule(),
            reason="理由",
            scope=[],
            approver_role="tech-lead",
            requested_by="agent-dev",
            approved_by="human-lead",
            expires_at=future_expiry(),
        )


def test_create_waiver_rejects_rule_without_waiver_allowed() -> None:
    with pytest.raises(WaiverError, match="不允许豁免"):
        create_waiver(
            make_rule(),  # waiver.allowed=False
            reason="理由",
            scope=["*"],
            approver_role="tech-lead",
            requested_by="agent-dev",
            approved_by="human-lead",
            expires_at=future_expiry(),
        )


def test_create_waiver_rejects_unauthorized_approver_role() -> None:
    with pytest.raises(WaiverError, match="不在规则 R-LINT 授权的审批角色"):
        create_waiver(
            make_waivable_rule(),
            reason="理由",
            scope=["*"],
            approver_role="intern",
            requested_by="agent-dev",
            approved_by="human-lead",
            expires_at=future_expiry(),
        )


def test_create_waiver_rejects_self_approval() -> None:
    """审批人不得与申请人相同（§17 职责分离）。"""
    with pytest.raises(WaiverError, match="不得与申请人相同"):
        create_waiver(
            make_waivable_rule(),
            reason="理由",
            scope=["*"],
            approver_role="tech-lead",
            requested_by="human-lead",
            approved_by="human-lead",
            expires_at=future_expiry(),
        )


def test_create_waiver_rejects_expiry_beyond_max_validity() -> None:
    with pytest.raises(WaiverError, match="上限 30 天"):
        create_waiver(
            make_waivable_rule(max_validity_days=30),
            reason="理由",
            scope=["*"],
            approver_role="tech-lead",
            requested_by="agent-dev",
            approved_by="human-lead",
            expires_at=future_expiry(days=31),
        )


def test_create_waiver_reports_all_problems() -> None:
    with pytest.raises(WaiverError) as exc_info:
        create_waiver(
            make_rule(),
            reason="",
            scope=[],
            approver_role="intern",
            requested_by="x",
            approved_by="x",
            expires_at=future_expiry(days=365),
        )
    assert len(exc_info.value.problems) >= 4


# ---------- is_waiver_valid：未过期 + 范围覆盖 ----------


@pytest.fixture
def waiver() -> Waiver:
    return create_waiver(
        make_waivable_rule(),
        reason="理由",
        scope=["PRJ-test"],
        approver_role="tech-lead",
        requested_by="agent-dev",
        approved_by="human-lead",
        expires_at=future_expiry(),
    )


def test_valid_when_scope_covers_project_id(waiver: Waiver) -> None:
    assert is_waiver_valid(waiver, make_task())


def test_valid_when_scope_covers_task_id() -> None:
    task = make_task()
    waiver = create_waiver(
        make_waivable_rule(),
        reason="理由",
        scope=[task.id],
        approver_role="tech-lead",
        requested_by="agent-dev",
        approved_by="human-lead",
        expires_at=future_expiry(),
    )
    assert is_waiver_valid(waiver, task)


def test_valid_when_scope_is_wildcard(waiver: Waiver) -> None:
    wildcard = Waiver(
        rule_id=waiver.rule_id,
        reason=waiver.reason,
        scope=("*",),
        approver_role=waiver.approver_role,
        requested_by=waiver.requested_by,
        approved_by=waiver.approved_by,
        created_at=waiver.created_at,
        expires_at=waiver.expires_at,
    )
    assert is_waiver_valid(wildcard, make_task())


def test_invalid_when_scope_not_covering(waiver: Waiver) -> None:
    other_project_task = make_task(project_id="PRJ-other")
    assert not is_waiver_valid(waiver, other_project_task)


def test_invalid_when_expired(waiver: Waiver) -> None:
    at = iso_z(datetime.now(UTC) + timedelta(days=8))
    assert not is_waiver_valid(waiver, make_task(), at=at)


# ---------- 门禁集成：有效豁免放行；无豁免/过期仍阻断 ----------


def test_evaluate_waives_blocker_failure_with_valid_waiver(waiver: Waiver) -> None:
    task = make_task()
    outcome = evaluate(
        task, [make_waivable_rule()], [make_result(status=TestStatus.FAILED)], [waiver]
    )
    finding = outcome.findings[0]
    assert finding.waived and finding.waiver_id == waiver.id
    assert not outcome.blocking_failures  # 已豁免不再阻断
    assert outcome.waived_findings == (finding,)


def test_evaluate_expired_waiver_still_blocks(waiver: Waiver) -> None:
    task = make_task()
    expired = Waiver(
        rule_id=waiver.rule_id,
        reason=waiver.reason,
        scope=waiver.scope,
        approver_role=waiver.approver_role,
        requested_by=waiver.requested_by,
        approved_by=waiver.approved_by,
        created_at=waiver.created_at,
        expires_at=iso_z(datetime.now(UTC) - timedelta(days=1)),  # 已过期
    )
    outcome = evaluate(
        task, [make_waivable_rule()], [make_result(status=TestStatus.FAILED)], [expired]
    )
    assert not outcome.findings[0].waived and outcome.blocking_failures


def test_evaluate_blocker_without_waiver_cannot_be_skipped() -> None:
    """完成标准：无豁免记录的 BLOCKER 不可跳过（豁免其他规则不影响本规则）。"""
    task = make_task()
    waiver_for_other_rule = create_waiver(
        make_waivable_rule("R-OTHER"),
        reason="理由",
        scope=["*"],
        approver_role="tech-lead",
        requested_by="agent-dev",
        approved_by="human-lead",
        expires_at=future_expiry(),
    )
    outcome = evaluate(
        task,
        [make_waivable_rule()],
        [make_result(status=TestStatus.FAILED)],
        [waiver_for_other_rule],
    )
    assert not outcome.findings[0].waived and outcome.blocking_failures


def test_enforce_waived_blocker_goes_passed_and_audits(tmp_path: Path) -> None:
    """有效豁免放行 → PASSED；豁免失败的报告不作为 PASSED 依据；写 waiver.applied。"""
    with StateStore(tmp_path / "platform.db") as store:
        executor, audit = make_executor(store)
        task = make_task()
        rule = make_waivable_rule()
        waiver = create_waiver(
            rule,
            reason="生成代码暂豁免",
            scope=[task.project_id],
            approver_role="tech-lead",
            requested_by="agent-dev",
            approved_by="human-lead",
            expires_at=future_expiry(),
        )
        results = [
            make_result(status=TestStatus.FAILED),  # R-LINT 失败但被豁免
            make_result(CheckKind.TEST, tool="pytest"),  # 通过证据
        ]
        rules = [rule, make_rule("R-TEST", kind=RuleCheckKind.TEST)]
        outcome = executor.enforce(task, rules, results, waivers=[waiver])
        assert outcome.passed and task.status is S.PASSED
        applied = [e for e in audit.list_events() if e.action == "waiver.applied"]
        assert len(applied) == 1
        assert applied[0].detail["rule_id"] == "R-LINT"
        assert applied[0].detail["waiver_id"] == waiver.id
        assert applied[0].detail["approved_by"] == "human-lead"
        assert audit.list_events()[-1].detail["decision"] == "PASSED"


def test_enforce_expired_waiver_still_goes_rework(tmp_path: Path, waiver: Waiver) -> None:
    """豁免过期 → BLOCKER 失败仍阻断（无有效豁免记录的 BLOCKER 不可跳过）。"""
    with StateStore(tmp_path / "platform.db") as store:
        executor, audit = make_executor(store)
        task = make_task()
        expired = Waiver(
            rule_id=waiver.rule_id,
            reason=waiver.reason,
            scope=waiver.scope,
            approver_role=waiver.approver_role,
            requested_by=waiver.requested_by,
            approved_by=waiver.approved_by,
            created_at=waiver.created_at,
            expires_at=iso_z(datetime.now(UTC) - timedelta(days=1)),
        )
        outcome = executor.enforce(
            task,
            [make_waivable_rule()],
            [make_result(status=TestStatus.FAILED)],
            waivers=[expired],
        )
        assert not outcome.passed and task.status is S.REWORK
        assert not [e for e in audit.list_events() if e.action == "waiver.applied"]
