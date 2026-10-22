"""Phase 2 阶段门禁集成测试（docs/03 §4）。

阶段门禁：从想法到批准的需求、架构和任务计划全程可追踪；
关键需求未确认时工作流阻塞。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.architecture import (
    ADRDocument,
    Alternative,
    ArchitecturePlan,
    Contract,
    RiskItem,
    assert_architecture_ready,
    save_adr,
    save_contract,
)
from core.domain import (
    Approval,
    ApprovalStatus,
    Artifact,
    ArtifactKind,
    FactKind,
    Requirement,
    RequirementKind,
    RequirementStatus,
    TestReport,
    TestStatus,
    new_id,
)
from core.domain.entities import utc_now
from core.planning import (
    PlanNotApprovedError,
    RequirementNotApprovedError,
    TaskSpec,
    assert_plan_approved,
    assert_task_startable,
    build_task_plan,
)
from core.requirements import (
    InterviewSession,
    Option,
    PRDSpec,
    Question,
    RequirementDraft,
    RequirementGuardRejectionError,
    RequirementStateMachine,
    RequirementTransitionContext,
    ScriptedRespondent,
    UserStory,
    build_matrix,
    build_prd,
    run_interview_round,
    save_prd,
    validate_prd_markdown,
)

IDEA = "做一个面向小团队的本地任务管理工具"


def _questions() -> list[Question]:
    return [
        Question(
            id="q1",
            topic="目标用户",
            text="目标用户是谁？",
            options=(
                _opt("small-team", "5-20 人小团队"),
                _opt("enterprise", "大型企业"),
            ),
        ),
        Question(id="q2", topic="核心功能", text="最核心的功能是什么？"),
        Question(
            id="q3",
            topic="部署形态",
            text="部署形态？",
            options=(
                _opt("local", "纯本地运行"),
                _opt("saas", "云端 SaaS"),
            ),
        ),
    ]


def _opt(value: str, label: str) -> Option:
    return Option(value=value, label=label, impact=f"选择{label}将影响后续设计")


def _run_interview() -> RequirementDraft:
    session = InterviewSession(IDEA, _questions())
    respondent = ScriptedRespondent(
        {
            "q1": "small-team",
            "q2": "任务看板与本地数据存储",
            "q3": ("local", FactKind.CONFIRMED_FACT),
        }
    )
    answered = run_interview_round(session, respondent)
    assert answered == 3
    draft = session.draft()
    assert not draft.open_questions
    return draft


def _approved_requirement(req_id: str, title: str) -> Requirement:
    req = Requirement(
        id=req_id,
        project_id="PRJ-gate00001",
        title=title,
        kind=RequirementKind.FUNCTIONAL,
        description=f"{title}（访谈已确认）",
        acceptance_criteria=["按验收条件通过自动化测试"],
    )
    machine = RequirementStateMachine()
    machine.transition(req, RequirementStatus.INTERVIEWING)
    machine.transition(req, RequirementStatus.REVIEW_REQUIRED)
    machine.transition(
        req,
        RequirementStatus.APPROVED,
        RequirementTransitionContext(confirmed_by="user"),
    )
    return req


def test_full_pipeline_idea_to_approved_plan_traceable(tmp_path: Path) -> None:
    # 1) 想法 → 访谈 → 结构化草案（§5.2）
    draft = _run_interview()
    assert any(a.topic == "核心功能" for a in draft.facts)

    # 2) 草案 → 需求 → 状态机推进至 APPROVED（§5.3）
    req1 = _approved_requirement("REQ-000000000001", "任务看板")
    req2 = _approved_requirement("REQ-000000000002", "本地数据存储")
    assert req1.status is RequirementStatus.APPROVED and req1.version > 1

    # 3) PRD 生成 → 模板校验 → 落盘 requirements/（§5.4、§8 阶段 2）
    doc = build_prd(
        PRDSpec(
            project_name="本地任务管理工具",
            project_idea=IDEA,
            user_stories=(
                UserStory(
                    id="US-0001",
                    requirement_id=req1.id,
                    role="团队负责人",
                    goal="在看板上管理任务",
                    benefit="直观跟踪进度",
                    acceptance_criteria=("看板列可自定义",),
                ),
            ),
        ),
        [req1, req2],
        draft,
    )
    validate_prd_markdown(doc.render())
    prd_path = save_prd(doc, tmp_path / "requirements")
    assert prd_path.name == "PRD-v1.md"

    # 4) 架构工作流：契约 + ADR + 方案 + 风险清单，过阶段 3 门禁（§8 阶段 3）
    schemas = tmp_path / "schemas"
    save_contract(
        Contract(
            name="board-api",
            schema={
                "type": "object",
                "required": ["board_id"],
                "properties": {"board_id": {"type": "string"}},
            },
        ),
        schemas,
    )
    decisions = tmp_path / "decisions"
    adr_path = save_adr(
        ADRDocument(
            number=1,
            title="本地存储选型",
            date="2026-10-22",
            background="纯本地运行，零运维",
            decision="SQLite WAL",
            alternatives=(
                Alternative(option="SQLite", pros="零运维", cons="并发有限", verdict="**采用**"),
                Alternative(option="PostgreSQL", pros="功能强", cons="需服务", verdict="拒绝"),
            ),
            conclusion="采用 SQLite，崩溃恢复走检查点",
        ),
        decisions,
    )
    arch = ArchitecturePlan(
        project_name="本地任务管理工具",
        overview="Python + SQLite",
        modules="ui/core/data 三层",
        data_model="task/board 两张表，版本化迁移",
        security_boundary="本地文件最小权限",
        test_strategy="单元+集成",
        deployment="Windows 桌面",
        adr_files=(adr_path.name,),
        api_contracts=("board-api",),
        risks=(
            RiskItem(id="R-01", description="数据损坏", impact="丢任务", mitigation="WAL+备份"),
        ),
    )
    assert_architecture_ready(arch, schema_dir=schemas, decisions_dir=decisions)

    # 5) 任务拆分：仅接受 APPROVED 需求；计划批准前编码任务被门禁拒绝（§8 阶段 4）
    plan = build_task_plan(
        "PLAN-0001",
        "PRJ-gate00001",
        [
            TaskSpec(
                id="TASK-0000000000a1",
                title="实现看板 UI",
                owner_role="前端开发",
                requirement_ids=(req1.id,),
                acceptance_criteria=("看板渲染通过截图验收",),
            ),
            TaskSpec(
                id="TASK-0000000000a2",
                title="实现本地存储层",
                owner_role="后端开发",
                requirement_ids=(req2.id,),
                acceptance_criteria=("CRUD 单测全过",),
            ),
        ],
        [req1, req2],
    )
    with pytest.raises(PlanNotApprovedError):
        assert_task_startable(plan, "TASK-0000000000a1", [])
    approval = Approval(
        id=new_id("APR"),
        project_id="PRJ-gate00001",
        subject=plan.approval_subject,
        requested_by="orchestrator",
        status=ApprovalStatus.APPROVED,
        decided_by="user",
    )
    assert_plan_approved(plan, [approval])
    assert_task_startable(plan, "TASK-0000000000a1", [approval])

    # 6) 追踪矩阵：需求→任务→代码→测试→证据全程可查且无缺口（§5.4）
    matrix = build_matrix(
        requirements=[req1, req2],
        tasks=[plan.graph.get(t) for t in plan.graph.task_ids],
        test_reports=[
            TestReport(
                id=new_id("TR"),
                task_id="TASK-0000000000a1",
                tool="pytest",
                command="python -m pytest",
                status=TestStatus.PASSED,
                started_at=utc_now(),
                finished_at=utc_now(),
            )
        ],
        artifacts=[
            Artifact(
                id=new_id("ART"),
                project_id="PRJ-gate00001",
                kind=ArtifactKind.CODE_DIFF,
                path="diffs/a1.patch",
                sha256="0" * 64,
                size_bytes=128,
                task_id="TASK-0000000000a1",
            ),
            Artifact(
                id=new_id("ART"),
                project_id="PRJ-gate00001",
                kind=ArtifactKind.DOC,
                path="evidence/a1.png",
                sha256="1" * 64,
                size_bytes=64,
                task_id="TASK-0000000000a1",
            ),
        ],
    )
    assert matrix.tasks_for(req1.id) == {"TASK-0000000000a1"}
    gaps = matrix.gaps([req1.id, req2.id])
    assert gaps.no_task == ()
    assert gaps.no_code_change == (req2.id,)  # 存储层尚未提交代码，缺口可检出
    assert not gaps.ok


def test_blocked_when_key_requirement_unconfirmed() -> None:
    """关键需求未确认（open_questions 非空）时工作流阻塞：状态机拒绝批准，
    未批准需求不得拆分任务，任务计划未获用户批准不得启动编码。"""
    req = Requirement(
        id="REQ-000000000009",
        project_id="PRJ-gate00001",
        title="数据同步规则",
        description="多端数据如何同步",
        acceptance_criteria=["冲突可解决"],
        open_questions=["离线编辑冲突策略未定"],
    )
    machine = RequirementStateMachine()
    machine.transition(req, RequirementStatus.INTERVIEWING)
    machine.transition(req, RequirementStatus.REVIEW_REQUIRED)
    with pytest.raises(RequirementGuardRejectionError, match="待确认事项"):
        machine.transition(
            req,
            RequirementStatus.APPROVED,
            RequirementTransitionContext(confirmed_by="user"),
        )

    with pytest.raises(RequirementNotApprovedError, match="未 APPROVED"):
        build_task_plan(
            "PLAN-0002",
            "PRJ-gate00001",
            [
                TaskSpec(
                    id="TASK-0000000000b1",
                    title="实现同步",
                    owner_role="后端开发",
                    requirement_ids=(req.id,),
                    acceptance_criteria=("同步测试通过",),
                )
            ],
            [req],
        )
