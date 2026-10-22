"""需求状态机测试（规范 §5.3）。"""

from __future__ import annotations

from typing import Any

import pytest

from core.domain import (
    Requirement,
    RequirementChange,
    RequirementStatus,
    new_id,
)
from core.requirements import (
    IllegalRequirementTransitionError,
    RequirementConcurrencyError,
    RequirementGuardRejectionError,
    RequirementStateMachine,
    RequirementTransitionContext,
)

_S = RequirementStatus


def _req(**kwargs: Any) -> Requirement:
    defaults: dict[str, Any] = {
        "id": new_id("REQ"),
        "project_id": "PRJ-test000001",
        "title": "批量导入用户",
        "description": "支持通过 CSV 批量导入用户账号",
        "acceptance_criteria": ["合法 CSV 全部导入成功", "非法行报告行号与原因"],
    }
    defaults.update(kwargs)
    return Requirement(**defaults)


def _change(**kwargs: Any) -> RequirementChange:
    defaults: dict[str, Any] = {
        "reason": "客户要求增加去重规则",
        "impact_scope": "导入流程与去重模块",
        "cost": "约 2 个任务工作量",
        "affected_tasks": ["TASK-aaa"],
        "regression_required": "导入回归测试全集",
    }
    defaults.update(kwargs)
    return RequirementChange(**defaults)


class TestMainFlow:
    def test_full_main_flow(self) -> None:
        sm = RequirementStateMachine()
        req = _req()
        sm.transition(req, _S.INTERVIEWING)
        sm.transition(req, _S.REVIEW_REQUIRED)
        result = sm.transition(req, _S.APPROVED, RequirementTransitionContext(confirmed_by="user"))
        assert req.status is _S.APPROVED
        assert req.confirmed_by == "user"
        assert result.from_status is _S.REVIEW_REQUIRED
        sm.transition(req, _S.IN_IMPLEMENTATION)
        sm.transition(req, _S.VERIFIED)
        sm.transition(req, _S.DELIVERED)
        assert req.status is _S.DELIVERED
        assert req.version == 7

    def test_legal_targets(self) -> None:
        sm = RequirementStateMachine()
        assert sm.legal_targets(_S.APPROVED) == frozenset(
            {_S.IN_IMPLEMENTATION, _S.REVIEW_REQUIRED}
        )
        assert sm.legal_targets(_S.DELIVERED) == frozenset()
        assert sm.can_transition(_S.DRAFT, _S.INTERVIEWING)
        assert not sm.can_transition(_S.DRAFT, _S.APPROVED)

    def test_terminal_state_has_no_outgoing(self) -> None:
        sm = RequirementStateMachine()
        req = _req(status=_S.DELIVERED)
        with pytest.raises(IllegalRequirementTransitionError):
            sm.transition(req, _S.REVIEW_REQUIRED, RequirementTransitionContext(change=_change()))

    def test_illegal_jump_rejected(self) -> None:
        sm = RequirementStateMachine()
        req = _req()
        with pytest.raises(IllegalRequirementTransitionError):
            sm.transition(req, _S.APPROVED, RequirementTransitionContext(confirmed_by="user"))


class TestGuards:
    def test_approve_requires_user_confirmation(self) -> None:
        """未经用户确认不得进入 APPROVED（§5.2 红线）。"""
        sm = RequirementStateMachine()
        req = _req(status=_S.REVIEW_REQUIRED)
        with pytest.raises(RequirementGuardRejectionError, match="用户显式确认"):
            sm.transition(req, _S.APPROVED)

    def test_approve_blocked_by_open_questions(self) -> None:
        """存在待确认事项不得批准（§5.2：未确认项不得当作事实）。"""
        sm = RequirementStateMachine()
        req = _req(status=_S.REVIEW_REQUIRED, open_questions=["并发上限未定"])
        with pytest.raises(RequirementGuardRejectionError, match="待确认事项"):
            sm.transition(req, _S.APPROVED, RequirementTransitionContext(confirmed_by="user"))

    def test_submit_review_requires_description_and_criteria(self) -> None:
        sm = RequirementStateMachine()
        req = _req(status=_S.INTERVIEWING, description="")
        with pytest.raises(RequirementGuardRejectionError, match="描述为空"):
            sm.transition(req, _S.REVIEW_REQUIRED)
        req2 = _req(status=_S.INTERVIEWING, acceptance_criteria=[])
        with pytest.raises(RequirementGuardRejectionError, match="验收条件为空"):
            sm.transition(req2, _S.REVIEW_REQUIRED)

    def test_change_back_requires_full_change_record(self) -> None:
        """需求变更回到评审必须附完整变更记录（§5.3）。"""
        sm = RequirementStateMachine()
        req = _req(status=_S.APPROVED, confirmed_by="user")
        with pytest.raises(RequirementGuardRejectionError, match="变更记录"):
            sm.transition(req, _S.REVIEW_REQUIRED)
        with pytest.raises(RequirementGuardRejectionError, match="不完整"):
            sm.transition(
                req,
                _S.REVIEW_REQUIRED,
                RequirementTransitionContext(change=_change(cost="")),
            )

    def test_change_back_appends_record(self) -> None:
        sm = RequirementStateMachine()
        req = _req(status=_S.IN_IMPLEMENTATION, confirmed_by="user")
        sm.transition(req, _S.REVIEW_REQUIRED, RequirementTransitionContext(change=_change()))
        assert req.status is _S.REVIEW_REQUIRED
        assert len(req.change_records) == 1
        assert req.change_records[0].reason == "客户要求增加去重规则"

    def test_back_to_interviewing_requires_reason(self) -> None:
        sm = RequirementStateMachine()
        req = _req(status=_S.REVIEW_REQUIRED)
        with pytest.raises(RequirementGuardRejectionError, match="原因"):
            sm.transition(req, _S.INTERVIEWING)
        sm.transition(req, _S.INTERVIEWING, RequirementTransitionContext(reason="范围需澄清"))
        assert req.status is _S.INTERVIEWING

    def test_expected_version_conflict(self) -> None:
        sm = RequirementStateMachine()
        req = _req()
        with pytest.raises(RequirementConcurrencyError):
            sm.transition(req, _S.INTERVIEWING, expected_version=99)


class TestSerialization:
    def test_requirement_roundtrip_and_schema(self) -> None:
        from core.domain import SchemaRegistry

        req = _req(change_records=[_change()])
        data = req.to_dict()
        SchemaRegistry().validate("requirement", data)
        restored = Requirement.from_dict(data)
        assert restored.to_dict() == data
