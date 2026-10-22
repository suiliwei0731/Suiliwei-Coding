"""任务拆分与计划门禁（规范 §8 阶段 4、§17）。

- :func:`build_task_plan` 从 APPROVED 需求生成任务计划（DAG）：
  每任务唯一 ID、目标与需求 ID、前置依赖、允许修改范围、输入、预期产物、
  契约、测试要求、验收条件、风险等级、所需权限（§8 阶段 4）；
  引用的需求必须存在且已 APPROVED——需求基线未立不得进入实施规划（§5.3/§8 阶段 2）；
- :func:`assert_plan_approved` 计划批准门禁：编码任务启动前，编排器必须校验
  存在状态为 APPROVED 且由用户（非发起方）决定的批准记录——"AI 自己同意"
  不构成授权（§17）；未批准抛 :class:`PlanNotApprovedError`；
- :func:`assert_task_startable` 即"编码任务启动"门禁，供编排器调用。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from core.domain.entities import Approval, Requirement, Task
from core.domain.enums import ApprovalStatus, RequirementStatus, TaskStatus
from core.orchestrator.dag import TaskGraph

RISK_LEVELS: tuple[str, ...] = ("低", "中", "高")


class PlanningError(ValueError):
    """任务拆分/计划门禁错误基类。"""


class RequirementNotApprovedError(PlanningError):
    """任务引用的需求不存在或未进入 APPROVED（§8 阶段 4：需求基线后才拆分任务）。"""


class PlanNotApprovedError(PlanningError):
    """计划未获用户批准，编码任务不得启动（§8 阶段 4、§17）。"""


@dataclass(frozen=True, kw_only=True)
class TaskSpec:
    """任务规格（§8 阶段 4 拆分粒度）：目标、需求 ID、依赖、验收条件等必填。"""

    id: str
    title: str  # 目标
    owner_role: str
    requirement_ids: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()  # 允许修改范围
    inputs: tuple[str, ...] = ()  # 输入与适用规则
    outputs: tuple[str, ...] = ()  # 预期产物
    contracts: tuple[str, ...] = ()  # 相关契约（schemas/ 登记名）
    verification_commands: tuple[str, ...] = ()  # 测试要求
    risk_level: str = "低"  # 风险等级
    permissions: tuple[str, ...] = ()  # 所需权限

    def __post_init__(self) -> None:
        for value in (self.id, self.title, self.owner_role):
            if not value.strip():
                raise PlanningError(
                    "任务 id/title/owner_role 均不能为空（§8 阶段 4：唯一 ID 与目标）"
                )
        if not self.requirement_ids:
            raise PlanningError(f"任务 {self.id} 缺少需求 ID（§8 阶段 4：目标与需求 ID）")
        if not self.acceptance_criteria:
            raise PlanningError(f"任务 {self.id} 缺少验收条件（§8 阶段 4 通过条件）")
        if self.risk_level not in RISK_LEVELS:
            raise PlanningError(f"任务 {self.id} 风险等级非法: {self.risk_level!r}")


@dataclass(frozen=True)
class TaskPlan:
    """任务计划：计划 ID + 任务 DAG；供批准与调度引用。"""

    plan_id: str
    project_id: str
    graph: TaskGraph
    specs: tuple[TaskSpec, ...] = field(compare=False)

    @property
    def approval_subject(self) -> str:
        """批准记录 subject 约定：``plan:{plan_id}``。"""
        return f"plan:{self.plan_id}"

    def render(self) -> str:
        """渲染为 Markdown（提交用户批准的计划视图，按拓扑序排列）。"""
        lines = [
            f"# 任务计划：{self.plan_id}",
            "",
            "| 任务 ID | 目标 | 负责角色 | 需求 ID | 依赖 | 验收条件 | 风险 |",
            "|---|---|---|---|---|---|---|",
        ]
        for tid in self.graph.topological_order():
            spec = next(s for s in self.specs if s.id == tid)
            deps = "、".join(spec.dependencies) or "—"
            reqs = "、".join(spec.requirement_ids)
            lines.append(
                f"| {tid} | {spec.title} | {spec.owner_role} | {reqs} "
                f"| {deps} | {len(spec.acceptance_criteria)} 项 | {spec.risk_level} |"
            )
        return "\n".join(lines) + "\n"


def build_task_plan(
    plan_id: str,
    project_id: str,
    specs: Iterable[TaskSpec],
    requirements: Iterable[Requirement],
) -> TaskPlan:
    """由任务规格与需求实体生成任务计划（DAG）。

    - 每个规格引用的需求必须存在且状态为 APPROVED，否则抛
      :class:`RequirementNotApprovedError`（§8 阶段 4：需求基线后才拆分）；
    - 任务 ID 重复、依赖未知、循环依赖由 TaskGraph 建图校验抛出（§8 阶段 4）。
    """
    if not plan_id.strip():
        raise PlanningError("计划 ID 不能为空")
    spec_list = list(specs)
    if not spec_list:
        raise PlanningError("任务规格不能为空：计划至少包含一个任务")
    req_map = {r.id: r for r in requirements}
    problems: list[str] = []
    for spec in spec_list:
        for rid in spec.requirement_ids:
            req = req_map.get(rid)
            if req is None:
                problems.append(f"任务 {spec.id} 引用的需求不存在: {rid}")
            elif req.status is not RequirementStatus.APPROVED:
                problems.append(
                    f"任务 {spec.id} 引用的需求 {rid} 未 APPROVED（当前 {req.status.value}）"
                )
    if problems:
        raise RequirementNotApprovedError("; ".join(problems))
    tasks = [
        Task(
            id=spec.id,
            project_id=project_id,
            title=spec.title,
            owner_role=spec.owner_role,
            requirement_ids=list(spec.requirement_ids),
            inputs=list(spec.inputs),
            dependencies=list(spec.dependencies),
            allowed_paths=list(spec.allowed_paths),
            acceptance_criteria=list(spec.acceptance_criteria),
            verification_commands=list(spec.verification_commands),
            status=TaskStatus.DRAFT,
        )
        for spec in spec_list
    ]
    return TaskPlan(
        plan_id=plan_id, project_id=project_id, graph=TaskGraph(tasks), specs=tuple(spec_list)
    )


def _approved_by_user(plan: TaskPlan, approvals: Iterable[Approval]) -> Approval | None:
    for approval in approvals:
        if (
            approval.subject == plan.approval_subject
            and approval.status is ApprovalStatus.APPROVED
            and approval.decided_by
            and approval.decided_by != approval.requested_by
        ):
            return approval
    return None


def assert_plan_approved(plan: TaskPlan, approvals: Iterable[Approval]) -> None:
    """计划批准门禁：无有效用户批准记录抛 PlanNotApprovedError。

    有效批准：subject 匹配 ``plan:{plan_id}``、状态 APPROVED、决定人非空且
    不同于发起方（§17：高风险操作明确授权，"AI 自己同意"不构成授权）。
    """
    approval_list = list(approvals)
    if _approved_by_user(plan, approval_list) is not None:
        return
    related = [a for a in approval_list if a.subject == plan.approval_subject]
    if not related:
        raise PlanNotApprovedError(f"计划 {plan.plan_id} 无批准记录，编码任务不得启动（§17）")
    states = "、".join(f"{a.status.value}(decided_by={a.decided_by or '—'})" for a in related)
    raise PlanNotApprovedError(
        f"计划 {plan.plan_id} 未获用户批准（现有记录: {states}），编码任务不得启动（§17）"
    )


def assert_task_startable(plan: TaskPlan, task_id: str, approvals: Iterable[Approval]) -> None:
    """编码任务启动门禁：任务须在计划内且计划已获用户批准。

    编排器在启动任何编码任务前调用本函数；未批准一律拒绝（P2-5 完成标准）。
    """
    plan.graph.get(task_id)  # 任务不存在时抛 UnknownDependencyError
    assert_plan_approved(plan, approvals)
