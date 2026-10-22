"""需求状态机（规范 §5.3）：7 态合法转移表 + 门禁 + 变更留痕。

红线落实：
- 非法转移抛 :class:`IllegalRequirementTransitionError`（DELIVERED 为终态无出边）；
- 进入 APPROVED 必须由用户确认（``confirmed_by`` 必填），且不得携带未确认的
  待确认事项（``open_questions`` 非空即拒绝，§5.2：未确认项不得当作事实）；
- 从 APPROVED/IN_IMPLEMENTATION/VERIFIED 回到 REVIEW_REQUIRED 属需求变更，
  必须附完整 :class:`~core.domain.entities.RequirementChange`（原因、影响范围、
  成本、受影响任务、回归测试要求，§5.3），变更记录写入实体留痕；
- 成功转移使 ``Requirement.version`` +1，支持 ``expected_version`` 乐观锁。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.domain.entities import Requirement, RequirementChange, utc_now
from core.domain.enums import RequirementStatus


class RequirementWorkflowError(Exception):
    """需求工作流错误基类。"""


class IllegalRequirementTransitionError(RequirementWorkflowError):
    """源状态与目标状态之间不存在合法转移（含终态不可再转移）。"""


class RequirementGuardRejectionError(RequirementWorkflowError):
    """需求转移门禁条件不满足（缺少用户确认、存在待确认事项、变更记录不全等）。"""


class RequirementConcurrencyError(RequirementWorkflowError):
    """版本乐观锁冲突：需求已被其他执行者修改。"""


@dataclass(frozen=True, kw_only=True)
class RequirementTransitionContext:
    """控制器依据实际情况计算的转移上下文（不接受 AI 自报）。

    - confirmed_by: 用户确认标识（→APPROVED 必填，§5.2 最终摘要确认）；
    - change: 需求变更记录（APPROVED/IN_IMPLEMENTATION/VERIFIED → REVIEW_REQUIRED
      必填，§5.3）；
    - reason: 退回访谈等辅助说明（写入审计）。
    """

    confirmed_by: str = ""
    change: RequirementChange | None = None
    reason: str = ""


ReqGuardFn = Callable[[Requirement, RequirementTransitionContext], str | None]
"""需求门禁函数：返回 None 表示通过，否则返回拒绝原因。"""


@dataclass(frozen=True)
class RequirementTransitionRule:
    """单条需求转移规则：门禁 + 是否属需求变更（变更须留痕）。"""

    guard: ReqGuardFn
    is_change: bool = False


def _guard_ok(req: Requirement, ctx: RequirementTransitionContext) -> str | None:
    return None


def _guard_start_interview(req: Requirement, ctx: RequirementTransitionContext) -> str | None:
    if not req.title.strip():
        return "需求标题为空，不得开始访谈"
    return None


def _guard_submit_review(req: Requirement, ctx: RequirementTransitionContext) -> str | None:
    if not req.description.strip():
        return "需求描述为空，草案不齐备不得提交评审（§5.2：结构化文档而非聊天记录）"
    if not req.acceptance_criteria:
        return "验收条件为空，不得提交评审（§5.4）"
    return None


def _guard_approve(req: Requirement, ctx: RequirementTransitionContext) -> str | None:
    if not ctx.confirmed_by.strip():
        return "APPROVED 必须由用户显式确认（§5.2：最终用简明摘要请用户确认）"
    if req.open_questions:
        return (
            f"存在 {len(req.open_questions)} 项待确认事项，不得批准"
            "（§5.2：未经确认的重大业务规则不得静默当成事实）"
        )
    return None


def _guard_change_back(req: Requirement, ctx: RequirementTransitionContext) -> str | None:
    change = ctx.change
    if change is None:
        return "需求变更回到评审必须附变更记录（§5.3：原因/影响范围/成本/受影响任务/回归测试要求）"
    if not change.reason.strip() or not change.impact_scope.strip() or not change.cost.strip():
        return "变更记录不完整：原因、影响范围、成本均为必填（§5.3）"
    return None


def _guard_reason_required(req: Requirement, ctx: RequirementTransitionContext) -> str | None:
    if not ctx.reason.strip():
        return "该转移必须记录原因"
    return None


_S = RequirementStatus

TransitionMap = dict[RequirementStatus, dict[RequirementStatus, RequirementTransitionRule]]

# 合法转移表（§5.3 主线 + 变更回到评审；DELIVERED 为终态无出边）
REQUIREMENT_TRANSITIONS: TransitionMap = {
    _S.DRAFT: {
        _S.INTERVIEWING: RequirementTransitionRule(_guard_start_interview),
    },
    _S.INTERVIEWING: {
        _S.REVIEW_REQUIRED: RequirementTransitionRule(_guard_submit_review),
    },
    _S.REVIEW_REQUIRED: {
        _S.APPROVED: RequirementTransitionRule(_guard_approve),
        _S.INTERVIEWING: RequirementTransitionRule(_guard_reason_required),
    },
    _S.APPROVED: {
        _S.IN_IMPLEMENTATION: RequirementTransitionRule(_guard_ok),
        _S.REVIEW_REQUIRED: RequirementTransitionRule(_guard_change_back, is_change=True),
    },
    _S.IN_IMPLEMENTATION: {
        _S.VERIFIED: RequirementTransitionRule(_guard_ok),
        _S.REVIEW_REQUIRED: RequirementTransitionRule(_guard_change_back, is_change=True),
    },
    _S.VERIFIED: {
        _S.DELIVERED: RequirementTransitionRule(_guard_ok),
        _S.REVIEW_REQUIRED: RequirementTransitionRule(_guard_change_back, is_change=True),
    },
    _S.DELIVERED: {},
}

REQUIREMENT_TERMINAL_STATES: frozenset[RequirementStatus] = frozenset({_S.DELIVERED})


@dataclass(frozen=True)
class RequirementTransitionResult:
    """一次成功需求转移的记录（供审计与持久化使用）。"""

    requirement: Requirement
    from_status: RequirementStatus
    to_status: RequirementStatus
    at: str


class RequirementStateMachine:
    """需求状态机：对 Requirement 执行门禁驱动的状态转移（§5.3）。"""

    def legal_targets(self, from_status: RequirementStatus) -> frozenset[RequirementStatus]:
        """源状态的全部合法目标状态。"""
        return frozenset(REQUIREMENT_TRANSITIONS.get(from_status, {}))

    def can_transition(self, from_status: RequirementStatus, to_status: RequirementStatus) -> bool:
        return to_status in self.legal_targets(from_status)

    def transition(
        self,
        requirement: Requirement,
        to_status: RequirementStatus,
        context: RequirementTransitionContext | None = None,
        *,
        expected_version: int | None = None,
    ) -> RequirementTransitionResult:
        """执行一次需求转移；任何校验失败抛异常且需求保持不变。

        :param requirement: 目标需求（原地更新 status/version/updated_at 等字段）；
        :param to_status: 目标状态；
        :param context: 转移上下文（确认方/变更记录/原因）；
        :param expected_version: 乐观锁版本号，不匹配抛 RequirementConcurrencyError。
        """
        ctx = context or RequirementTransitionContext()
        from_status = requirement.status

        if expected_version is not None and expected_version != requirement.version:
            raise RequirementConcurrencyError(
                f"版本冲突: 期望 {expected_version}，实际 {requirement.version}"
            )

        rule = REQUIREMENT_TRANSITIONS.get(from_status, {}).get(to_status)
        if rule is None:
            raise IllegalRequirementTransitionError(
                f"非法需求转移: {from_status.value} → {to_status.value}"
            )

        rejection = rule.guard(requirement, ctx)
        if rejection is not None:
            raise RequirementGuardRejectionError(
                f"需求转移被拒绝（{from_status.value} → {to_status.value}）: {rejection}"
            )

        requirement.status = to_status
        requirement.version += 1
        requirement.updated_at = utc_now()
        if rule.is_change and ctx.change is not None:
            requirement.change_records.append(ctx.change)
        if to_status is _S.APPROVED:
            requirement.confirmed_by = ctx.confirmed_by

        return RequirementTransitionResult(
            requirement=requirement,
            from_status=from_status,
            to_status=to_status,
            at=requirement.updated_at,
        )
