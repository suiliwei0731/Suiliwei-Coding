"""任务拆分与计划门禁测试（规范 §8 阶段 4、§17；P2-5 完成标准）。"""

from __future__ import annotations

from typing import Any

import pytest

from core.domain import (
    Approval,
    ApprovalStatus,
    Requirement,
    RequirementStatus,
    new_id,
)
from core.orchestrator.dag import CycleError, DuplicateTaskError, UnknownDependencyError
from core.planning import (
    PlanningError,
    PlanNotApprovedError,
    RequirementNotApprovedError,
    TaskSpec,
    assert_plan_approved,
    assert_task_startable,
    build_task_plan,
)


def _req(req_id: str, **kwargs: Any) -> Requirement:
    defaults: dict[str, Any] = {
        "id": req_id,
        "project_id": "PRJ-test000001",
        "title": f"需求 {req_id}",
        "status": RequirementStatus.APPROVED,
        "acceptance_criteria": ["验收条件"],
        "confirmed_by": "user",
    }
    defaults.update(kwargs)
    return Requirement(**defaults)


def _spec(spec_id: str, req_id: str, **kwargs: Any) -> TaskSpec:
    defaults: dict[str, Any] = {
        "id": spec_id,
        "title": f"实现 {req_id}",
        "owner_role": "后端开发",
        "requirement_ids": (req_id,),
        "acceptance_criteria": ("按验收条件通过",),
    }
    defaults.update(kwargs)
    return TaskSpec(**defaults)


def _approval(plan_subject: str, **kwargs: Any) -> Approval:
    defaults: dict[str, Any] = {
        "id": new_id("APR"),
        "project_id": "PRJ-test000001",
        "subject": plan_subject,
        "requested_by": "orchestrator",
    }
    defaults.update(kwargs)
    return Approval(**defaults)


def _plan(**kwargs: Any) -> Any:
    reqs = [_req("REQ-000000000001"), _req("REQ-000000000002")]
    specs = [
        _spec("TASK-0000000000a1", "REQ-000000000001"),
        _spec(
            "TASK-0000000000a2",
            "REQ-000000000002",
            dependencies=("TASK-0000000000a1",),
            risk_level="高",
        ),
    ]
    defaults: dict[str, Any] = {
        "plan_id": "PLAN-0001",
        "project_id": "PRJ-test000001",
        "specs": specs,
        "requirements": reqs,
    }
    defaults.update(kwargs)
    return build_task_plan(**defaults)


class TestTaskSpec:
    def test_required_fields(self) -> None:
        with pytest.raises(PlanningError, match="需求 ID"):
            _spec("TASK-x00000000001", "REQ-000000000001", requirement_ids=())
        with pytest.raises(PlanningError, match="验收条件"):
            _spec("TASK-000000000001", "REQ-000000000001", acceptance_criteria=())
        with pytest.raises(PlanningError, match="风险等级非法"):
            _spec("TASK-000000000001", "REQ-000000000001", risk_level="巨大")
        with pytest.raises(PlanningError, match="不能为空"):
            _spec(" ", "REQ-000000000001")


class TestBuildTaskPlan:
    def test_builds_dag_from_approved_requirements(self) -> None:
        plan = _plan()
        assert plan.graph.topological_order() == ("TASK-0000000000a1", "TASK-0000000000a2")
        task = plan.graph.get("TASK-0000000000a2")
        assert task.requirement_ids == ["REQ-000000000002"]
        assert task.dependencies == ["TASK-0000000000a1"]
        assert task.acceptance_criteria == ["按验收条件通过"]

    def test_requirement_must_be_approved(self) -> None:
        reqs = [_req("REQ-000000000001", status=RequirementStatus.REVIEW_REQUIRED)]
        specs = [_spec("TASK-0000000000a1", "REQ-000000000001")]
        with pytest.raises(RequirementNotApprovedError, match="未 APPROVED"):
            build_task_plan("PLAN-0001", "PRJ-test000001", specs, reqs)

    def test_requirement_must_exist(self) -> None:
        specs = [_spec("TASK-0000000000a1", "REQ-999999999999")]
        with pytest.raises(RequirementNotApprovedError, match="不存在"):
            build_task_plan("PLAN-0001", "PRJ-test000001", specs, [])

    def test_empty_specs_rejected(self) -> None:
        with pytest.raises(PlanningError, match="至少包含一个任务"):
            build_task_plan("PLAN-0001", "PRJ-test000001", [], [])

    def test_duplicate_and_cycle_rejected(self) -> None:
        req = _req("REQ-000000000001")
        dup = [_spec("TASK-0000000000a1", req.id), _spec("TASK-0000000000a1", req.id)]
        with pytest.raises(DuplicateTaskError):
            build_task_plan("PLAN-0001", "PRJ-test000001", dup, [req])
        cyc = [
            _spec("TASK-0000000000a1", req.id, dependencies=("TASK-0000000000a2",)),
            _spec("TASK-0000000000a2", req.id, dependencies=("TASK-0000000000a1",)),
        ]
        with pytest.raises(CycleError, match="依赖循环"):
            build_task_plan("PLAN-0001", "PRJ-test000001", cyc, [req])
        unknown = [_spec("TASK-0000000000a1", req.id, dependencies=("TASK-999999999999",))]
        with pytest.raises(UnknownDependencyError):
            build_task_plan("PLAN-0001", "PRJ-test000001", unknown, [req])

    def test_render_lists_tasks_topologically(self) -> None:
        text = _plan().render()
        assert text.startswith("# 任务计划：PLAN-0001")
        first = text.index("TASK-0000000000a1")
        second = text.index("TASK-0000000000a2")
        assert first < second
        assert "| 高 |" in text and "REQ-000000000002" in text


class TestPlanApprovalGate:
    def test_unapproved_plan_rejected(self) -> None:
        plan = _plan()
        with pytest.raises(PlanNotApprovedError, match="无批准记录"):
            assert_plan_approved(plan, [])

    def test_pending_or_rejected_not_accepted(self) -> None:
        plan = _plan()
        records = [
            _approval(plan.approval_subject, status=ApprovalStatus.PENDING),
            _approval(
                plan.approval_subject,
                status=ApprovalStatus.REJECTED,
                decided_by="user",
                reason="风险过高",
            ),
        ]
        with pytest.raises(PlanNotApprovedError, match="未获用户批准"):
            assert_plan_approved(plan, records)

    def test_self_approval_not_accepted(self) -> None:
        """§17：AI 自己同意不构成授权——decided_by 与 requested_by 相同视为无效。"""
        plan = _plan()
        records = [
            _approval(
                plan.approval_subject,
                status=ApprovalStatus.APPROVED,
                requested_by="orchestrator",
                decided_by="orchestrator",
            )
        ]
        with pytest.raises(PlanNotApprovedError):
            assert_plan_approved(plan, records)

    def test_user_approval_accepted(self) -> None:
        plan = _plan()
        records = [
            _approval(
                plan.approval_subject,
                status=ApprovalStatus.APPROVED,
                decided_by="user",
            )
        ]
        assert_plan_approved(plan, records)  # 不抛异常即通过

    def test_other_plan_approval_not_accepted(self) -> None:
        plan = _plan()
        records = [_approval("plan:PLAN-9999", status=ApprovalStatus.APPROVED, decided_by="user")]
        with pytest.raises(PlanNotApprovedError):
            assert_plan_approved(plan, records)


class TestTaskStartGate:
    def test_coding_task_blocked_without_approval(self) -> None:
        """P2-5 完成标准：未获批准的计划无法启动编码任务（被编排器拒绝）。"""
        plan = _plan()
        with pytest.raises(PlanNotApprovedError):
            assert_task_startable(plan, "TASK-0000000000a1", [])

    def test_startable_after_user_approval(self) -> None:
        plan = _plan()
        records = [
            _approval(plan.approval_subject, status=ApprovalStatus.APPROVED, decided_by="user")
        ]
        assert_task_startable(plan, "TASK-0000000000a1", records)  # 不抛异常即通过

    def test_unknown_task_rejected(self) -> None:
        plan = _plan()
        records = [
            _approval(plan.approval_subject, status=ApprovalStatus.APPROVED, decided_by="user")
        ]
        with pytest.raises(UnknownDependencyError):
            assert_task_startable(plan, "TASK-999999999999", records)
