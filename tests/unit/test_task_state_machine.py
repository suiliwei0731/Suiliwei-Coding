"""P1-2 单元测试：任务状态机（规范 §9.1/§9.2、§19.1）。

覆盖：全部合法转移、典型非法转移、证据缺失/不合规、失败证据误用、
门禁条件（依赖/审查阻断/测试未过/UNVERIFIED 红线/重试上限/授权）、版本乐观锁。
"""

from __future__ import annotations

import pytest

from core.domain import (
    EvidenceKind,
    Review,
    ReviewFinding,
    ReviewVerdict,
    Severity,
    Task,
    TaskStatus,
    TestReport,
    TestStatus,
    new_id,
)
from core.workflow import (
    TRANSITIONS,
    ConcurrencyError,
    Evidence,
    EvidenceError,
    GuardRejectionError,
    IllegalTransitionError,
    TaskStateMachine,
    TransitionContext,
)

S = TaskStatus


# ---------- 构造辅助 ----------


def make_task(status: S = S.DRAFT, **overrides: object) -> Task:
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


def ev_manual(summary: str = "人工登记：输入/依赖/验收齐备") -> Evidence:
    return Evidence(kind=EvidenceKind.MANUAL, summary=summary)


def ev_command(exit_code: int = 0, summary: str = "依赖与权限检查通过") -> Evidence:
    return Evidence(
        kind=EvidenceKind.COMMAND_RESULT,
        summary=summary,
        command="orchestrator check-deps",
        exit_code=exit_code,
    )


def ev_artifact() -> Evidence:
    return Evidence(
        kind=EvidenceKind.ARTIFACT,
        summary="实现产物已生成",
        refs=("artifacts/TASK-1/diff.patch",),
    )


def ev_review(review_id: str = "REV-1") -> Evidence:
    return Evidence(kind=EvidenceKind.REVIEW_RESULT, summary="审查结论已记录", refs=(review_id,))


def ev_test(report_id: str = "TR-1", exit_code: int = 0) -> Evidence:
    return Evidence(
        kind=EvidenceKind.TEST_REPORT,
        summary="测试报告已保存",
        refs=(report_id,),
        exit_code=exit_code,
    )


def ev_approval() -> Evidence:
    return Evidence(kind=EvidenceKind.APPROVAL, summary="用户授权", refs=("APR-1",))


def make_review(
    verdict: ReviewVerdict = ReviewVerdict.PASS,
    blocking: bool = False,
    review_id: str = "REV-1",
) -> Review:
    findings = (
        [
            ReviewFinding(
                severity=Severity.BLOCKER,
                file_path="a.py",
                impact="鉴权绕过",
                suggestion="补充校验",
                blocking=True,
            )
        ]
        if blocking
        else []
    )
    return Review(
        id=review_id,
        task_id="TASK-1",
        reviewer_role="reviewer",
        verdict=verdict,
        findings=findings,
    )


def make_report(status: TestStatus = TestStatus.PASSED, report_id: str = "TR-1") -> TestReport:
    return TestReport(
        id=report_id,
        task_id="TASK-1",
        tool="pytest",
        command="pytest tests",
        status=status,
        exit_code=0 if status is TestStatus.PASSED else 1,
    )


@pytest.fixture(name="sm")
def _sm() -> TaskStateMachine:
    return TaskStateMachine()


# ---------- 主线与全部合法转移（§9.2） ----------


def test_happy_path_main_flow(sm: TaskStateMachine) -> None:
    task = make_task()
    r = sm.transition(task, S.READY, ev_manual())
    assert r.from_status is S.DRAFT and task.status is S.READY and task.version == 2

    sm.transition(task, S.IN_PROGRESS, ev_command(), TransitionContext(dependencies_passed=True))
    sm.transition(task, S.REVIEW_REQUIRED, ev_artifact())
    sm.transition(task, S.TEST_REQUIRED, ev_review(), TransitionContext(reviews=(make_review(),)))
    sm.transition(task, S.PASSED, ev_test(), TransitionContext(test_reports=(make_report(),)))
    assert task.status is S.PASSED and task.version == 6


_CANCEL_CTX = TransitionContext(authorized=True, reason="需求取消")
_REASON_CTX = TransitionContext(reason="环境不可用")

# (源, 目标, 证据, 上下文, 任务覆盖) —— 主线 5 条已由上一用例覆盖，此处覆盖旁路 21 条
LEGAL_SIDELINE_CASES = [
    pytest.param(S.DRAFT, S.CANCELLED, (ev_approval(),), _CANCEL_CTX, {}, id="DRAFT->CANCELLED"),
    pytest.param(
        S.READY, S.BLOCKED, (ev_manual("阻塞登记"),), _REASON_CTX, {}, id="READY->BLOCKED"
    ),
    pytest.param(S.READY, S.CANCELLED, (ev_approval(),), _CANCEL_CTX, {}, id="READY->CANCELLED"),
    pytest.param(
        S.IN_PROGRESS,
        S.FAILED,
        (ev_command(exit_code=1, summary="执行失败"),),
        None,
        {},
        id="IN_PROGRESS->FAILED",
    ),
    pytest.param(
        S.IN_PROGRESS,
        S.BLOCKED,
        (ev_command(exit_code=1, summary="权限不足"),),
        _REASON_CTX,
        {},
        id="IN_PROGRESS->BLOCKED",
    ),
    pytest.param(
        S.IN_PROGRESS,
        S.CANCELLED,
        (ev_approval(),),
        _CANCEL_CTX,
        {},
        id="IN_PROGRESS->CANCELLED",
    ),
    pytest.param(
        S.REVIEW_REQUIRED,
        S.REWORK,
        (ev_review(),),
        TransitionContext(
            reviews=(make_review(verdict=ReviewVerdict.CHANGES_REQUIRED, blocking=True),)
        ),
        {},
        id="REVIEW_REQUIRED->REWORK",
    ),
    pytest.param(
        S.REVIEW_REQUIRED,
        S.BLOCKED,
        (ev_manual("关键决策缺失"),),
        _REASON_CTX,
        {},
        id="REVIEW_REQUIRED->BLOCKED",
    ),
    pytest.param(
        S.REVIEW_REQUIRED,
        S.CANCELLED,
        (ev_approval(),),
        _CANCEL_CTX,
        {},
        id="REVIEW_REQUIRED->CANCELLED",
    ),
    pytest.param(
        S.TEST_REQUIRED,
        S.FAILED,
        (ev_test(exit_code=1),),
        TransitionContext(test_reports=(make_report(TestStatus.FAILED),)),
        {},
        id="TEST_REQUIRED->FAILED",
    ),
    pytest.param(
        S.TEST_REQUIRED,
        S.REWORK,
        (ev_test(exit_code=1),),
        TransitionContext(test_reports=(make_report(TestStatus.FAILED),)),
        {},
        id="TEST_REQUIRED->REWORK",
    ),
    pytest.param(
        S.TEST_REQUIRED,
        S.BLOCKED,
        (ev_manual("测试环境不可用"),),
        _REASON_CTX,
        {},
        id="TEST_REQUIRED->BLOCKED",
    ),
    pytest.param(
        S.TEST_REQUIRED,
        S.CANCELLED,
        (ev_approval(),),
        _CANCEL_CTX,
        {},
        id="TEST_REQUIRED->CANCELLED",
    ),
    pytest.param(
        S.REWORK,
        S.IN_PROGRESS,
        (ev_command(summary="重新领取执行"),),
        None,
        {},
        id="REWORK->IN_PROGRESS",
    ),
    pytest.param(
        S.REWORK, S.BLOCKED, (ev_manual("无法修复"),), _REASON_CTX, {}, id="REWORK->BLOCKED"
    ),
    pytest.param(S.REWORK, S.CANCELLED, (ev_approval(),), _CANCEL_CTX, {}, id="REWORK->CANCELLED"),
    pytest.param(
        S.FAILED,
        S.REWORK,
        (ev_manual("根因已定位，决定返工"),),
        None,
        {},
        id="FAILED->REWORK",
    ),
    pytest.param(
        S.FAILED,
        S.BLOCKED,
        (ev_manual("重试超限升级"),),
        TransitionContext(reason="重试超限"),
        {"retry_count": 3},
        id="FAILED->BLOCKED",
    ),
    pytest.param(S.FAILED, S.CANCELLED, (ev_approval(),), _CANCEL_CTX, {}, id="FAILED->CANCELLED"),
    pytest.param(
        S.BLOCKED,
        S.READY,
        (ev_manual("阻塞已解除"),),
        TransitionContext(reason="依赖已安装"),
        {"blocked_reason": "依赖缺失"},
        id="BLOCKED->READY",
    ),
    pytest.param(
        S.BLOCKED, S.CANCELLED, (ev_approval(),), _CANCEL_CTX, {}, id="BLOCKED->CANCELLED"
    ),
]


@pytest.mark.parametrize(
    ("from_status", "to_status", "evidence", "ctx", "overrides"), LEGAL_SIDELINE_CASES
)
def test_all_legal_sidelined_transitions(
    sm: TaskStateMachine,
    from_status: S,
    to_status: S,
    evidence: tuple[Evidence, ...],
    ctx: TransitionContext | None,
    overrides: dict[str, object],
) -> None:
    task = make_task(from_status, **overrides)
    result = sm.transition(task, to_status, evidence, ctx)
    assert task.status is to_status
    assert result.from_status is from_status and result.to_status is to_status


def test_transition_table_covers_all_ten_states() -> None:
    assert set(TRANSITIONS) == set(TaskStatus)
    assert TRANSITIONS[S.PASSED] == {} and TRANSITIONS[S.CANCELLED] == {}


# ---------- 非法转移（§19.1） ----------


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (S.DRAFT, S.IN_PROGRESS),
        (S.DRAFT, S.PASSED),
        (S.DRAFT, S.REVIEW_REQUIRED),
        (S.READY, S.REVIEW_REQUIRED),
        (S.READY, S.PASSED),
        (S.IN_PROGRESS, S.PASSED),  # 跳过审查/测试
        (S.IN_PROGRESS, S.TEST_REQUIRED),
        (S.REVIEW_REQUIRED, S.PASSED),
        (S.REVIEW_REQUIRED, S.IN_PROGRESS),
        (S.TEST_REQUIRED, S.IN_PROGRESS),
        (S.PASSED, S.READY),  # 终态不可再转移
        (S.PASSED, S.REWORK),
        (S.CANCELLED, S.READY),
        (S.BLOCKED, S.IN_PROGRESS),  # 须先回 READY
        (S.FAILED, S.IN_PROGRESS),  # 须经 REWORK
        (S.REWORK, S.PASSED),
    ],
)
def test_illegal_transitions_rejected(sm: TaskStateMachine, from_status: S, to_status: S) -> None:
    task = make_task(from_status)
    all_evidence = [
        ev_manual(),
        ev_command(),
        ev_artifact(),
        ev_review(),
        ev_test(),
        ev_approval(),
    ]
    ctx = TransitionContext(
        dependencies_passed=True,
        authorized=True,
        reason="x",
        reviews=(make_review(),),
        test_reports=(make_report(),),
    )
    with pytest.raises(IllegalTransitionError):
        sm.transition(task, to_status, all_evidence, ctx)
    assert task.status is from_status  # 失败后状态不变


def test_terminal_states_have_no_outgoing(sm: TaskStateMachine) -> None:
    for status in (S.PASSED, S.CANCELLED):
        assert sm.legal_targets(status) == frozenset()


# ---------- 证据规则（§7.4/§9.2：不接受自报状态） ----------


def test_transition_requires_evidence(sm: TaskStateMachine) -> None:
    with pytest.raises(EvidenceError, match="必须携带证据"):
        sm.transition(make_task(), S.READY, [])


def test_command_evidence_requires_command_and_exit_code() -> None:
    with pytest.raises(EvidenceError, match="exit_code"):
        Evidence(kind=EvidenceKind.COMMAND_RESULT, summary="x", command="pytest").validate()
    with pytest.raises(EvidenceError, match="command"):
        Evidence(kind=EvidenceKind.COMMAND_RESULT, summary="x", exit_code=0).validate()


def test_ref_kinds_require_refs_and_summary_required() -> None:
    with pytest.raises(EvidenceError, match="refs"):
        Evidence(kind=EvidenceKind.ARTIFACT, summary="x").validate()
    with pytest.raises(EvidenceError, match="summary"):
        Evidence(kind=EvidenceKind.MANUAL, summary="  ").validate()


def test_missing_required_evidence_kind(sm: TaskStateMachine) -> None:
    task = make_task(S.IN_PROGRESS)
    with pytest.raises(EvidenceError, match="ARTIFACT"):
        sm.transition(task, S.REVIEW_REQUIRED, ev_command())
    assert task.status is S.IN_PROGRESS


def test_failing_evidence_rejected_for_success_transition(sm: TaskStateMachine) -> None:
    task = make_task(S.TEST_REQUIRED)
    ctx = TransitionContext(test_reports=(make_report(),))
    with pytest.raises(EvidenceError, match="失败证据"):
        sm.transition(task, S.PASSED, ev_test(exit_code=1), ctx)


# ---------- 门禁条件 ----------


def test_ready_requires_acceptance_criteria(sm: TaskStateMachine) -> None:
    task = make_task(S.DRAFT, acceptance_criteria=[])
    with pytest.raises(GuardRejectionError, match="验收条件"):
        sm.transition(task, S.READY, ev_manual())


def test_in_progress_requires_dependencies_and_permission(sm: TaskStateMachine) -> None:
    with pytest.raises(GuardRejectionError, match="前置依赖"):
        sm.transition(make_task(S.READY), S.IN_PROGRESS, ev_command())
    with pytest.raises(GuardRejectionError, match="执行权限"):
        sm.transition(
            make_task(S.READY),
            S.IN_PROGRESS,
            ev_command(),
            TransitionContext(dependencies_passed=True, execution_permission=False),
        )


def test_review_blocker_goes_to_rework_not_test(sm: TaskStateMachine) -> None:
    task = make_task(S.REVIEW_REQUIRED)
    blocking = make_review(verdict=ReviewVerdict.CHANGES_REQUIRED, blocking=True)
    with pytest.raises(GuardRejectionError, match="阻断"):
        sm.transition(task, S.TEST_REQUIRED, ev_review(), TransitionContext(reviews=(blocking,)))
    sm.transition(task, S.REWORK, ev_review(), TransitionContext(reviews=(blocking,)))
    assert task.status is S.REWORK and task.retry_count == 1


def test_passed_rejects_unverified_report(sm: TaskStateMachine) -> None:
    """红线 2：UNVERIFIED 不得计入通过。"""
    ctx = TransitionContext(test_reports=(make_report(TestStatus.UNVERIFIED),))
    with pytest.raises(GuardRejectionError, match="UNVERIFIED"):
        sm.transition(make_task(S.TEST_REQUIRED), S.PASSED, ev_test(), ctx)


def test_passed_rejects_failed_report(sm: TaskStateMachine) -> None:
    ctx = TransitionContext(test_reports=(make_report(TestStatus.FAILED),))
    with pytest.raises(GuardRejectionError, match="未通过"):
        sm.transition(make_task(S.TEST_REQUIRED), S.PASSED, ev_test(), ctx)


def test_passed_allows_not_applicable_report(sm: TaskStateMachine) -> None:
    ctx = TransitionContext(
        test_reports=(
            make_report(TestStatus.PASSED),
            make_report(TestStatus.NOT_APPLICABLE, "TR-2"),
        )
    )
    sm.transition(make_task(S.TEST_REQUIRED), S.PASSED, ev_test(), ctx)


def test_failed_transition_requires_failed_report(sm: TaskStateMachine) -> None:
    ctx = TransitionContext(test_reports=(make_report(TestStatus.PASSED),))
    with pytest.raises(GuardRejectionError, match="FAILED"):
        sm.transition(make_task(S.TEST_REQUIRED), S.FAILED, ev_test(exit_code=1), ctx)


def test_rework_rejected_when_retry_limit_reached(sm: TaskStateMachine) -> None:
    task = make_task(S.FAILED, retry_count=3)
    with pytest.raises(GuardRejectionError, match="上限"):
        sm.transition(task, S.REWORK, ev_manual(), TransitionContext(max_retries=3))


def test_blocked_requires_reason(sm: TaskStateMachine) -> None:
    with pytest.raises(GuardRejectionError, match="原因"):
        sm.transition(make_task(S.IN_PROGRESS), S.BLOCKED, ev_command(exit_code=1))


def test_blocked_reason_recorded_and_cleared(sm: TaskStateMachine) -> None:
    task = make_task(S.IN_PROGRESS)
    sm.transition(task, S.BLOCKED, ev_command(exit_code=1), TransitionContext(reason="权限不足"))
    assert task.blocked_reason == "权限不足"
    sm.transition(task, S.READY, ev_manual("阻塞解除"), TransitionContext(reason="已获授权"))
    assert task.blocked_reason is None


def test_cancel_requires_authorization_and_reason(sm: TaskStateMachine) -> None:
    with pytest.raises(GuardRejectionError, match="授权"):
        sm.transition(make_task(S.READY), S.CANCELLED, ev_approval())
    with pytest.raises(GuardRejectionError, match="原因"):
        sm.transition(
            make_task(S.READY), S.CANCELLED, ev_approval(), TransitionContext(authorized=True)
        )
    task = make_task(S.READY)
    sm.transition(task, S.CANCELLED, ev_approval(), _CANCEL_CTX)
    assert task.status is S.CANCELLED


# ---------- 版本乐观锁与转移记录 ----------


def test_optimistic_lock(sm: TaskStateMachine) -> None:
    task = make_task()
    with pytest.raises(ConcurrencyError, match="版本冲突"):
        sm.transition(task, S.READY, ev_manual(), expected_version=99)
    sm.transition(task, S.READY, ev_manual(), expected_version=1)
    assert task.version == 2


def test_transition_result_records_evidence(sm: TaskStateMachine) -> None:
    result = sm.transition(make_task(), S.READY, ev_manual())
    assert result.evidence[0].kind is EvidenceKind.MANUAL
    assert result.at == result.task.updated_at
