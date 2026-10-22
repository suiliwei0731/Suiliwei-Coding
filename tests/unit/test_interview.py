"""需求访谈引擎测试（规范 §5.2）。

含 P2-1 完成标准场景：与模拟用户的两轮访谈产出结构化草案。
"""

from __future__ import annotations

import pytest

from core.domain import FactKind
from core.requirements import (
    DraftNotReadyError,
    DuplicateAnswerError,
    InterviewSession,
    InvalidAnswerError,
    Option,
    Question,
    RequirementDraft,
    ScriptedRespondent,
    UnknownQuestionError,
    run_interview_round,
)

_ROUND1 = [
    Question(id="Q1", topic="goal", text="这个项目要解决什么问题？"),
    Question(
        id="Q2",
        topic="users",
        text="主要使用者是谁？",
        options=(
            Option(value="ops", label="运维人员", impact="强调批量操作与审计"),
            Option(value="admin", label="系统管理员", impact="强调权限与配置"),
            Option(value="dev", label="开发者", impact="强调 API 与可集成性"),
        ),
    ),
    Question(id="Q3", topic="scale", text="预计同时使用的用户规模？"),
]


class TestQuestionValidation:
    def test_options_must_be_2_to_4_with_impact(self) -> None:
        with pytest.raises(ValueError, match="2–4 个"):
            Question(
                id="Q",
                topic="t",
                text="?",
                options=(Option(value="a", label="A", impact="i"),),
            )
        with pytest.raises(ValueError, match="影响说明"):
            Question(
                id="Q",
                topic="t",
                text="?",
                options=(
                    Option(value="a", label="A", impact=""),
                    Option(value="b", label="B", impact="i"),
                ),
            )

    def test_answer_must_hit_option(self) -> None:
        session = InterviewSession("想法", list(_ROUND1))
        with pytest.raises(InvalidAnswerError, match="命中选项"):
            session.answer("Q2", "student")


class TestOneQuestionAtATime:
    def test_next_question_returns_single_unanswered(self) -> None:
        session = InterviewSession("想法", list(_ROUND1))
        assert session.next_question().id == "Q1"  # type: ignore[union-attr]
        session.answer("Q1", "批量导入用户")
        assert session.next_question().id == "Q2"  # type: ignore[union-attr]
        session.answer("Q2", "admin")
        session.answer("Q3", "约 200 人")
        assert session.next_question() is None

    def test_duplicate_answer_rejected(self) -> None:
        session = InterviewSession("想法", list(_ROUND1))
        session.answer("Q1", "批量导入用户")
        with pytest.raises(DuplicateAnswerError):
            session.answer("Q1", "换个回答")

    def test_unknown_question_rejected(self) -> None:
        session = InterviewSession("想法", list(_ROUND1))
        with pytest.raises(UnknownQuestionError):
            session.answer("Q99", "x")


class TestAssumptionsAndKinds:
    def test_assumption_cannot_be_confirmed_fact(self) -> None:
        session = InterviewSession("想法", list(_ROUND1))
        with pytest.raises(InvalidAnswerError, match="假设"):
            session.answer("Q1", "v", assumption=True)
        session.answer("Q1", "v", kind=FactKind.INFERENCE, assumption=True)
        draft = _finish(session)
        assert len(draft.assumptions) == 1
        assert len(draft.facts) == 2

    def test_classification_in_draft(self) -> None:
        session = InterviewSession("想法", list(_ROUND1))
        session.answer("Q1", "批量导入用户")
        session.answer("Q2", "admin", kind=FactKind.PREFERENCE)
        session.defer("Q3", "规模待与运营确认")
        draft = session.draft()
        assert len(draft.facts) == 1
        assert len(draft.preferences) == 1
        assert len(draft.open_questions) == 1
        assert draft.open_questions[0].topic == "scale"
        assert "[待确认] scale" in draft.summary()


class TestContradiction:
    def test_conflicting_facts_flagged_and_resolved(self) -> None:
        session = InterviewSession("想法", list(_ROUND1))
        session.answer("Q1", "批量导入用户")
        session.answer("Q2", "admin")
        # 第二轮追问同主题，回答与已确认事实冲突
        session.add_questions([Question(id="Q2b", topic="users", text="确认：使用者是运维？")])
        contradiction = session.answer("Q2b", "ops")
        assert contradiction is not None
        assert contradiction.topic == "users"
        # 矛盾未解决：同主题不再追问，草案拒绝产出
        with pytest.raises(DraftNotReadyError, match="未解决矛盾"):
            session.draft()
        session.resolve_contradiction("users", keep="incoming")
        session.answer("Q3", "约 200 人")
        draft = session.draft()
        # 接受新回答后，旧答案被替换
        users_facts = [a for a in draft.facts if a.topic == "users"]
        assert len(users_facts) == 1
        assert users_facts[0].value == "ops"

    def test_keep_existing_discards_incoming(self) -> None:
        session = InterviewSession("想法", list(_ROUND1))
        session.answer("Q2", "admin")
        session.add_questions([Question(id="Q2b", topic="users", text="再确认一次")])
        session.answer("Q2b", "ops")
        session.resolve_contradiction("users", keep="existing")
        session.answer("Q1", "批量导入用户")
        session.answer("Q3", "约 200 人")
        draft = session.draft()
        users_facts = [a for a in draft.facts if a.topic == "users"]
        assert len(users_facts) == 1
        assert users_facts[0].value == "admin"


class TestDraftReadiness:
    def test_pending_required_questions_block_draft(self) -> None:
        session = InterviewSession("想法", list(_ROUND1))
        session.answer("Q1", "批量导入用户")
        with pytest.raises(DraftNotReadyError, match="必答问题未处理"):
            session.draft()

    def test_open_kind_not_allowed_in_answer(self) -> None:
        session = InterviewSession("想法", list(_ROUND1))
        with pytest.raises(InvalidAnswerError, match="OPEN"):
            session.answer("Q1", "v", kind=FactKind.OPEN)


def _finish(session: InterviewSession) -> RequirementDraft:
    """回答剩余问题并产出草案（测试辅助）。"""
    while (q := session.next_question()) is not None:
        session.answer(q.id, "ops" if q.options else "v")
    return session.draft()


class TestTwoRoundInterviewWithScriptedUser:
    """P2-1 完成标准：与模拟用户的两轮访谈能产出结构化草案。"""

    def test_two_rounds_produce_structured_draft(self) -> None:
        session = InterviewSession("做一个内部设备巡检登记工具", list(_ROUND1))

        # 第一轮：基础问题
        user = ScriptedRespondent(
            {
                "Q1": "巡检记录靠纸质，易丢失难统计",
                "Q2": "ops",
                "Q3": ("约 200 台设备", FactKind.CONFIRMED_FACT),
            }
        )
        answered = run_interview_round(session, user)
        assert answered == 3
        # 一次一问：模拟用户逐题被问，顺序与队列一致
        assert user.asked == ["Q1", "Q2", "Q3"]

        # 第二轮：依据第一轮答案动态追加追问（异常/权限/边界，§5.2）
        session.add_questions(
            [
                Question(
                    id="Q4",
                    topic="offline",
                    text="现场无网络时怎么办？",
                    options=(
                        Option(
                            value="offline-first",
                            label="离线先存、联网后同步",
                            impact="需要本地存储与冲突处理",
                        ),
                        Option(
                            value="online-only", label="仅联网可用", impact="实现简单但现场受限"
                        ),
                    ),
                ),
                Question(id="Q5", topic="permission", text="谁可以修改巡检记录？"),
                Question(id="Q6", topic="retention", text="记录保留多久？", required=False),
            ]
        )
        user2 = ScriptedRespondent(
            {
                "Q4": "offline-first",
                "Q5": ("仅巡检组长", FactKind.CONFIRMED_FACT),
                "Q6": ("默认保留 3 年", FactKind.INFERENCE, True),  # 显式假设
            }
        )
        answered2 = run_interview_round(session, user2)
        assert answered2 == 3

        draft = session.draft()
        assert draft.project_idea == "做一个内部设备巡检登记工具"
        assert len(draft.facts) == 5
        assert len(draft.assumptions) == 1
        assert draft.assumptions[0].topic == "retention"
        assert not draft.open_questions
        summary = draft.summary()
        assert "offline-first" in summary
        assert "已确认事实 5 项" in summary
