"""任务状态机（规范 §9.1/§9.2）：合法转移表 + 证据驱动 + 版本乐观锁。

红线落实：
- 状态转移只由控制器依据证据触发（§9.2），AI 自报状态一律拒绝；
- 非法转移抛 :class:`IllegalTransitionError`（终态 PASSED/CANCELLED 无出边）；
- 缺必需证据或证据不合规抛 :class:`~core.workflow.evidence.EvidenceError`；
- 门禁条件（依赖、审查结论、测试结果、重试预算、授权）不满足抛 :class:`GuardRejectionError`；
- 成功转移使 ``Task.version`` +1，调用方可传 ``expected_version`` 做乐观锁校验。

retry_count 语义：进入 REWORK（开始返工）时 +1；``max_retries`` 由控制器按
config/models.yaml ``usage.budget_limits.max_retries_per_task`` 注入。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.domain.entities import Review, Task, TestReport, utc_now
from core.domain.enums import EvidenceKind, ReviewVerdict, TaskStatus, TestStatus
from core.workflow.evidence import Evidence, EvidenceError


class WorkflowError(Exception):
    """工作流错误基类。"""


class IllegalTransitionError(WorkflowError):
    """源状态与目标状态之间不存在合法转移（含终态不可再转移）。"""


class GuardRejectionError(WorkflowError):
    """转移门禁条件不满足（依赖未过、审查阻断、测试未过、未授权等）。"""


class ConcurrencyError(WorkflowError):
    """版本乐观锁冲突：任务已被其他执行者修改。"""


@dataclass(frozen=True, kw_only=True)
class TransitionContext:
    """控制器依据实际领域对象计算的转移上下文（非 AI 自报）。

    - dependencies_passed: 前置依赖任务是否全部 PASSED（READY→IN_PROGRESS 必需）；
    - execution_permission: 执行权限是否可用（§9.2）；
    - reviews / test_reports: 与本次转移相关的审查结论 / 必需测试报告实体；
    - max_retries: 每任务最大重试次数（config/models.yaml usage.budget_limits）；
    - authorized: 高风险操作（取消等）是否已获授权（§9.1 经授权取消、§17）；
    - reason: 阻塞/取消/失败原因（相应转移必填，写入 blocked_reason 与审计）。
    """

    dependencies_passed: bool = False
    execution_permission: bool = True
    reviews: tuple[Review, ...] = ()
    test_reports: tuple[TestReport, ...] = ()
    max_retries: int = 3
    authorized: bool = False
    reason: str = ""


GuardFn = Callable[[Task, TransitionContext], str | None]
"""门禁函数：返回 None 表示通过，否则返回拒绝原因。"""


@dataclass(frozen=True)
class TransitionRule:
    """单条转移规则：必需证据种类（每类至少一条）+ 门禁 + 是否允许失败证据。"""

    required_kinds: frozenset[EvidenceKind]
    guard: GuardFn
    allow_failing: bool = False


def _guard_ok(task: Task, ctx: TransitionContext) -> str | None:
    return None


def _guard_ready(task: Task, ctx: TransitionContext) -> str | None:
    if not task.title.strip() or not task.owner_role.strip():
        return "任务标题或责任角色为空"
    if not task.acceptance_criteria:
        return "验收条件为空，不得进入 READY（§9.2：输入/依赖/验收齐备）"
    return None


def _guard_in_progress(task: Task, ctx: TransitionContext) -> str | None:
    if not ctx.dependencies_passed:
        return "前置依赖未全部通过（§9.2：前置依赖通过才可执行）"
    if not ctx.execution_permission:
        return "执行权限不可用（§9.2）"
    return None


def _latest_review(ctx: TransitionContext) -> Review | None:
    if not ctx.reviews:
        return None
    return max(ctx.reviews, key=lambda r: r.created_at)


def _review_has_blocker(review: Review) -> bool:
    return review.verdict is ReviewVerdict.CHANGES_REQUIRED or any(
        f.blocking for f in review.findings
    )


def _guard_test_required(task: Task, ctx: TransitionContext) -> str | None:
    review = _latest_review(ctx)
    if review is None:
        return "缺少审查结论实体（控制器须传入实际 Review，不接受自报）"
    if _review_has_blocker(review):
        return "审查存在阻断问题，应转 REWORK（§9.2：审查无阻断问题才进测试）"
    return None


def _guard_rework_from_review(task: Task, ctx: TransitionContext) -> str | None:
    review = _latest_review(ctx)
    if review is None:
        return "缺少审查结论实体"
    if not _review_has_blocker(review):
        return "审查无阻断问题，无需返工"
    return None


def _guard_passed(task: Task, ctx: TransitionContext) -> str | None:
    if not ctx.test_reports:
        return "缺少必需测试报告实体"
    for report in ctx.test_reports:
        if report.status is TestStatus.UNVERIFIED:
            return f"报告 {report.id} 为 UNVERIFIED：未验证不得计入通过（红线 2）"
        if report.status is TestStatus.NOT_APPLICABLE:
            continue  # 不适用项不计入通过判定
        if report.status is not TestStatus.PASSED:
            return f"报告 {report.id} 状态 {report.status.value}，未通过"
    return None


def _guard_failed_from_test(task: Task, ctx: TransitionContext) -> str | None:
    if not any(r.status is TestStatus.FAILED for r in ctx.test_reports):
        return "无 FAILED 测试报告，失败转移缺少依据"
    return None


def _guard_rework_from_test(task: Task, ctx: TransitionContext) -> str | None:
    if not any(r.status is TestStatus.FAILED for r in ctx.test_reports):
        return "无 FAILED 测试报告，返工缺少依据"
    if task.retry_count >= ctx.max_retries:
        return f"重试已达上限（{ctx.max_retries}），应转 FAILED/BLOCKED（§9.3）"
    return None


def _guard_rework_from_failed(task: Task, ctx: TransitionContext) -> str | None:
    if task.retry_count >= ctx.max_retries:
        return f"重试已达上限（{ctx.max_retries}），应转 BLOCKED 并请求处理（§9.2/§9.3）"
    return None


def _guard_reason_required(task: Task, ctx: TransitionContext) -> str | None:
    if not ctx.reason.strip():
        return "该转移必须记录原因（§9.2/§9.3：阻塞须说明并请求处理）"
    return None


def _guard_cancelled(task: Task, ctx: TransitionContext) -> str | None:
    if not ctx.authorized:
        return "CANCELLED 须经授权（§9.1：经授权取消）"
    if not ctx.reason.strip():
        return "CANCELLED 须记录原因"
    return None


_E = EvidenceKind
_S = TaskStatus

# 合法转移表（§9.2 主线 + 旁路；PASSED/CANCELLED 为终态无出边）
TRANSITIONS: dict[TaskStatus, dict[TaskStatus, TransitionRule]] = {
    _S.DRAFT: {
        _S.READY: TransitionRule(frozenset({_E.MANUAL}), _guard_ready),
        _S.CANCELLED: TransitionRule(frozenset({_E.APPROVAL}), _guard_cancelled),
    },
    _S.READY: {
        _S.IN_PROGRESS: TransitionRule(frozenset({_E.COMMAND_RESULT}), _guard_in_progress),
        _S.BLOCKED: TransitionRule(frozenset({_E.MANUAL}), _guard_reason_required),
        _S.CANCELLED: TransitionRule(frozenset({_E.APPROVAL}), _guard_cancelled),
    },
    _S.IN_PROGRESS: {
        _S.REVIEW_REQUIRED: TransitionRule(frozenset({_E.ARTIFACT}), _guard_ok),
        _S.FAILED: TransitionRule(frozenset({_E.COMMAND_RESULT}), _guard_ok, allow_failing=True),
        _S.BLOCKED: TransitionRule(
            frozenset({_E.COMMAND_RESULT}), _guard_reason_required, allow_failing=True
        ),
        _S.CANCELLED: TransitionRule(frozenset({_E.APPROVAL}), _guard_cancelled),
    },
    _S.REVIEW_REQUIRED: {
        _S.TEST_REQUIRED: TransitionRule(frozenset({_E.REVIEW_RESULT}), _guard_test_required),
        _S.REWORK: TransitionRule(frozenset({_E.REVIEW_RESULT}), _guard_rework_from_review),
        _S.BLOCKED: TransitionRule(frozenset({_E.MANUAL}), _guard_reason_required),
        _S.CANCELLED: TransitionRule(frozenset({_E.APPROVAL}), _guard_cancelled),
    },
    _S.TEST_REQUIRED: {
        _S.PASSED: TransitionRule(frozenset({_E.TEST_REPORT}), _guard_passed),
        _S.FAILED: TransitionRule(
            frozenset({_E.TEST_REPORT}), _guard_failed_from_test, allow_failing=True
        ),
        _S.REWORK: TransitionRule(
            frozenset({_E.TEST_REPORT}), _guard_rework_from_test, allow_failing=True
        ),
        _S.BLOCKED: TransitionRule(frozenset({_E.MANUAL}), _guard_reason_required),
        _S.CANCELLED: TransitionRule(frozenset({_E.APPROVAL}), _guard_cancelled),
    },
    _S.REWORK: {
        _S.IN_PROGRESS: TransitionRule(frozenset({_E.COMMAND_RESULT}), _guard_ok),
        _S.BLOCKED: TransitionRule(frozenset({_E.MANUAL}), _guard_reason_required),
        _S.CANCELLED: TransitionRule(frozenset({_E.APPROVAL}), _guard_cancelled),
    },
    _S.FAILED: {
        _S.REWORK: TransitionRule(frozenset({_E.MANUAL}), _guard_rework_from_failed),
        _S.BLOCKED: TransitionRule(frozenset({_E.MANUAL}), _guard_reason_required),
        _S.CANCELLED: TransitionRule(frozenset({_E.APPROVAL}), _guard_cancelled),
    },
    _S.BLOCKED: {
        _S.READY: TransitionRule(frozenset({_E.MANUAL}), _guard_reason_required),
        _S.CANCELLED: TransitionRule(frozenset({_E.APPROVAL}), _guard_cancelled),
    },
    _S.PASSED: {},
    _S.CANCELLED: {},
}

TERMINAL_STATES: frozenset[TaskStatus] = frozenset({_S.PASSED, _S.CANCELLED})


@dataclass(frozen=True)
class TransitionResult:
    """一次成功转移的记录（供审计与持久化使用）。"""

    task: Task
    from_status: TaskStatus
    to_status: TaskStatus
    evidence: tuple[Evidence, ...]
    at: str


class TaskStateMachine:
    """任务状态机：对 Task 执行证据驱动的状态转移（§9.2）。"""

    def legal_targets(self, from_status: TaskStatus) -> frozenset[TaskStatus]:
        """源状态的全部合法目标状态。"""
        return frozenset(TRANSITIONS.get(from_status, {}))

    def can_transition(self, from_status: TaskStatus, to_status: TaskStatus) -> bool:
        return to_status in self.legal_targets(from_status)

    def transition(
        self,
        task: Task,
        to_status: TaskStatus,
        evidence: Evidence | list[Evidence] | tuple[Evidence, ...],
        context: TransitionContext | None = None,
        *,
        expected_version: int | None = None,
    ) -> TransitionResult:
        """执行一次状态转移；任何校验失败抛异常且任务保持不变。

        :param task: 目标任务（原地更新 status/version/updated_at 等字段）；
        :param to_status: 目标状态；
        :param evidence: 本次转移依据的证据（至少一条，须覆盖规则要求的种类）；
        :param context: 控制器计算的转移上下文；
        :param expected_version: 乐观锁：非 None 时要求等于 task.version。
        """
        ctx = context or TransitionContext()
        ev_list = (evidence,) if isinstance(evidence, Evidence) else tuple(evidence)

        rule = TRANSITIONS.get(task.status, {}).get(to_status)
        if rule is None:
            raise IllegalTransitionError(
                f"非法转移: {task.status.value} → {to_status.value}（§9.2 转移表不含此边）"
            )
        if expected_version is not None and task.version != expected_version:
            raise ConcurrencyError(
                f"版本冲突: 期望 {expected_version}，实际 {task.version}（任务已被修改）"
            )
        self._check_evidence(rule, ev_list)
        rejection = rule.guard(task, ctx)
        if rejection is not None:
            raise GuardRejectionError(
                f"{task.status.value} → {to_status.value} 被门禁拒绝: {rejection}"
            )

        from_status = task.status
        task.status = to_status
        task.version += 1
        task.updated_at = utc_now()
        if to_status is _S.REWORK:
            task.retry_count += 1
        if to_status is _S.BLOCKED:
            task.blocked_reason = ctx.reason
        elif from_status is _S.BLOCKED:
            task.blocked_reason = None  # 解除阻塞时清空原因
        return TransitionResult(
            task=task,
            from_status=from_status,
            to_status=to_status,
            evidence=ev_list,
            at=task.updated_at,
        )

    @staticmethod
    def _check_evidence(rule: TransitionRule, ev_list: tuple[Evidence, ...]) -> None:
        if not ev_list:
            raise EvidenceError("状态转移必须携带证据（§9.2：不接受 AI 自报状态）")
        for ev in ev_list:
            ev.validate()
            if ev.is_failing and not rule.allow_failing:
                raise EvidenceError(f"失败证据（exit_code={ev.exit_code}）不得用于成功向转移")
        present = {ev.kind for ev in ev_list}
        missing = rule.required_kinds - present
        if missing:
            names = ", ".join(sorted(k.value for k in missing))
            raise EvidenceError(f"缺少必需证据类型: {names}")
