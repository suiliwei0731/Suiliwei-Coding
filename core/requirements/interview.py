"""需求访谈引擎（规范 §5.2）：一次一问、选项化、矛盾识别、假设标记。

规则落实：
- 一次一问：:meth:`InterviewSession.next_question` 每次只返回一个未处理问题，
   interviewer 不得一次抛出整批问题；
- 不重复询问：同一问题只允许回答一次，重复回答抛 :class:`DuplicateAnswerError`；
- 选项化：选项非空时须为 2–4 个且每项带影响说明（§5.2），回答值必须命中选项；
- 矛盾识别：同一主题出现与已确认事实冲突的新事实回答时记录
  :class:`Contradiction`，未解决前 :meth:`InterviewSession.draft` 拒绝产出；
- 假设标记：``assumption=True`` 的回答不得标为已确认事实（假设≠事实，§5.2
  「未经确认的重大业务规则不得静默当成事实」），产出时进入假设清单；
- 动态调整：支持 :meth:`InterviewSession.add_questions` 依据已得答案追加追问
  （第二轮访谈），已答问题不受影响；
- 待确认：:meth:`InterviewSession.defer` 将问题显式标为待确认（OPEN），
  产出草案时列入 ``open_questions``，不得静默当作事实。

访谈引擎为纯逻辑（无 I/O）；真实用户交互在 Phase 7 桌面 UI 对接，
Phase 2 以 :class:`ScriptedRespondent`（模拟用户）驱动验证。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from core.domain.enums import FactKind


class InterviewError(Exception):
    """访谈流程错误基类。"""


class DuplicateAnswerError(InterviewError):
    """同一问题被重复回答（§5.2：不重复询问）。"""


class UnknownQuestionError(InterviewError):
    """回答/操作了不存在的问题。"""


class InvalidAnswerError(InterviewError):
    """回答不合法：未命中选项、假设与事实标记冲突等。"""


class DraftNotReadyError(InterviewError):
    """草案未可产出：存在未处理问题或未解决矛盾。"""


@dataclass(frozen=True)
class Option:
    """访谈选项（§5.2：2–4 个易懂选项并说明影响）。"""

    value: str  # 机器可读值（回答时引用）
    label: str  # 面向用户的易懂表述
    impact: str  # 选择影响说明


@dataclass(frozen=True)
class Question:
    """访谈问题：同 topic 的问题用于矛盾识别；required=False 可跳过。"""

    id: str
    topic: str  # 主题（矛盾识别按主题归组）
    text: str
    options: tuple[Option, ...] = ()
    required: bool = True

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.topic.strip() or not self.text.strip():
            raise ValueError("问题 id/topic/text 均不能为空")
        if self.options:
            if not 2 <= len(self.options) <= 4:
                raise ValueError(f"选项须为 2–4 个（§5.2），实际 {len(self.options)} 个")
            for opt in self.options:
                if not opt.impact.strip():
                    raise ValueError(f"选项 {opt.value!r} 缺少影响说明（§5.2）")


@dataclass(frozen=True)
class Answer:
    """一次回答：问题、值、信息分类与假设标记。"""

    question_id: str
    topic: str
    value: str
    kind: FactKind = FactKind.CONFIRMED_FACT
    assumption: bool = False
    note: str = ""


@dataclass(frozen=True)
class Contradiction:
    """矛盾记录：同主题下与已确认事实冲突的新事实回答。"""

    topic: str
    existing: Answer
    incoming: Answer
    description: str


@dataclass(frozen=True)
class OpenItem:
    """待确认事项（§5.2：标记待定，不得当作事实）。"""

    question_id: str
    topic: str
    reason: str


@dataclass(frozen=True)
class RequirementDraft:
    """结构化需求草案（§5.2：结构化文档而非聊天记录）。"""

    project_idea: str
    facts: tuple[Answer, ...]  # 已确认事实
    preferences: tuple[Answer, ...]  # 用户偏好
    inferences: tuple[Answer, ...]  # 系统推断
    assumptions: tuple[Answer, ...]  # 显式标记的临时假设
    open_questions: tuple[OpenItem, ...]  # 待确认事项

    def summary(self) -> str:
        """简明摘要（§5.2：最终用简明摘要请用户确认）。"""
        lines = [
            f"项目想法: {self.project_idea}",
            f"已确认事实 {len(self.facts)} 项 / 偏好 {len(self.preferences)} 项 / "
            f"推断 {len(self.inferences)} 项 / 临时假设 {len(self.assumptions)} 项 / "
            f"待确认 {len(self.open_questions)} 项",
        ]
        for ans in self.facts:
            lines.append(f"[事实] {ans.topic}: {ans.value}")
        for ans in self.preferences:
            lines.append(f"[偏好] {ans.topic}: {ans.value}")
        for ans in self.assumptions:
            lines.append(f"[假设] {ans.topic}: {ans.value}（待用户确认）")
        for item in self.open_questions:
            lines.append(f"[待确认] {item.topic}: {item.reason}")
        return "\n".join(lines)


class InterviewSession:
    """一次需求访谈会话：维护问题队列、答案、矛盾与待确认事项。"""

    def __init__(self, project_idea: str, questions: list[Question] | None = None) -> None:
        if not project_idea.strip():
            raise ValueError("项目想法不能为空")
        self._idea = project_idea
        self._questions: list[Question] = list(questions or [])
        self._answers: dict[str, Answer] = {}  # question_id → Answer
        self._open: dict[str, OpenItem] = {}  # question_id → OpenItem
        self._contradictions: dict[str, Contradiction] = {}  # topic → 未解决矛盾
        self._superseded: dict[str, str] = {}  # 被取代 question_id → 生效 question_id

    @property
    def project_idea(self) -> str:
        return self._idea

    @property
    def answers(self) -> tuple[Answer, ...]:
        return tuple(self._answers.values())

    @property
    def open_items(self) -> tuple[OpenItem, ...]:
        return tuple(self._open.values())

    @property
    def unresolved_contradictions(self) -> tuple[Contradiction, ...]:
        return tuple(self._contradictions.values())

    def add_questions(self, questions: list[Question]) -> None:
        """依据已有答案动态追加追问（§5.2：根据答案动态调整）。"""
        known = {q.id for q in self._questions}
        for q in questions:
            if q.id in known:
                raise ValueError(f"问题 ID 重复: {q.id}")
            known.add(q.id)
            self._questions.append(q)

    def get_question(self, question_id: str) -> Question:
        for q in self._questions:
            if q.id == question_id:
                return q
        raise UnknownQuestionError(f"问题不存在: {question_id}")

    def next_question(self) -> Question | None:
        """当前最重要的一个未处理问题（一次一问，§5.2）。

        已回答或已标待确认的问题不再出现；返回 None 表示问题队列已处理完。
        """
        for q in self._questions:
            if q.id in self._answers or q.id in self._open or q.id in self._superseded:
                continue
            if q.topic in self._contradictions:
                continue  # 矛盾未解决前不再追问同主题问题
            return q
        return None

    def answer(
        self,
        question_id: str,
        value: str,
        *,
        kind: FactKind = FactKind.CONFIRMED_FACT,
        assumption: bool = False,
        note: str = "",
    ) -> Contradiction | None:
        """记录一次回答；检测到矛盾时返回 Contradiction（回答暂缓接受）。

        - 选项化问题必须回答选项值之一；
        - assumption=True 时 kind 不得为 CONFIRMED_FACT（假设不是已确认事实）；
        - 同主题已有已确认事实且新回答亦为事实但取值不同 → 矛盾，须
          :meth:`resolve_contradiction` 后新回答才可能生效。
        """
        question = self.get_question(question_id)
        if question_id in self._answers:
            raise DuplicateAnswerError(f"问题 {question_id} 已回答，不得重复询问（§5.2）")
        if question_id in self._open:
            raise InvalidAnswerError(f"问题 {question_id} 已标待确认，请先解决再回答")
        if kind is FactKind.OPEN:
            raise InvalidAnswerError("待确认请使用 defer()，answer() 不接受 OPEN 分类")
        if assumption and kind is FactKind.CONFIRMED_FACT:
            raise InvalidAnswerError("临时假设不得标为已确认事实（§5.2）")
        if not value.strip():
            raise InvalidAnswerError("回答不能为空")
        if question.options:
            allowed = {opt.value for opt in question.options}
            if value not in allowed:
                raise InvalidAnswerError(f"回答须命中选项 {sorted(allowed)}，实际 {value!r}")

        incoming = Answer(
            question_id=question_id,
            topic=question.topic,
            value=value,
            kind=kind,
            assumption=assumption,
            note=note,
        )

        # 矛盾识别：同主题已确认事实与新事实回答取值冲突
        if kind is FactKind.CONFIRMED_FACT:
            for existing in self._answers.values():
                if (
                    existing.topic == question.topic
                    and existing.kind is FactKind.CONFIRMED_FACT
                    and existing.value != incoming.value
                ):
                    contradiction = Contradiction(
                        topic=question.topic,
                        existing=existing,
                        incoming=incoming,
                        description=(
                            f"主题 {question.topic!r} 的回答冲突: "
                            f"既有 {existing.value!r}（问题 {existing.question_id}），"
                            f"新答 {incoming.value!r}"
                        ),
                    )
                    self._contradictions[question.topic] = contradiction
                    return contradiction

        self._answers[question_id] = incoming
        return None

    def resolve_contradiction(self, topic: str, keep: Literal["existing", "incoming"]) -> None:
        """解决矛盾：保留既有回答或接受新回答。

        被取代一方的问题记为"已处理"（superseded），不再追问也不阻塞草案。
        """
        contradiction = self._contradictions.get(topic)
        if contradiction is None:
            raise InterviewError(f"主题 {topic!r} 不存在未解决矛盾")
        if keep == "incoming":
            old_qid = contradiction.existing.question_id
            new_qid = contradiction.incoming.question_id
            del self._answers[old_qid]
            self._answers[new_qid] = contradiction.incoming
            self._superseded[old_qid] = new_qid
        else:
            self._superseded[contradiction.incoming.question_id] = (
                contradiction.existing.question_id
            )
        del self._contradictions[topic]

    def defer(self, question_id: str, reason: str) -> OpenItem:
        """将问题显式标为待确认（OPEN）：标记待定，不得当作事实（§5.2）。"""
        question = self.get_question(question_id)
        if question_id in self._answers:
            raise DuplicateAnswerError(f"问题 {question_id} 已回答")
        if not reason.strip():
            raise InvalidAnswerError("待确认必须说明原因")
        item = OpenItem(question_id=question_id, topic=question.topic, reason=reason)
        self._open[question_id] = item
        return item

    def draft(self) -> RequirementDraft:
        """产出结构化需求草案（§5.2：结构化文档而非聊天记录）。

        所有必答问题须已回答或显式标为待确认；存在未解决矛盾时拒绝产出。
        """
        if self._contradictions:
            topics = sorted(self._contradictions)
            raise DraftNotReadyError(f"存在未解决矛盾（{len(self._contradictions)} 项）: {topics}")
        pending = [
            q.id
            for q in self._questions
            if q.required
            and q.id not in self._answers
            and q.id not in self._open
            and q.id not in self._superseded
        ]
        if pending:
            raise DraftNotReadyError(f"必答问题未处理: {pending}")

        facts, preferences, inferences, assumptions = [], [], [], []
        for ans in self._answers.values():
            if ans.assumption:
                assumptions.append(ans)
            elif ans.kind is FactKind.CONFIRMED_FACT:
                facts.append(ans)
            elif ans.kind is FactKind.PREFERENCE:
                preferences.append(ans)
            else:
                inferences.append(ans)
        return RequirementDraft(
            project_idea=self._idea,
            facts=tuple(facts),
            preferences=tuple(preferences),
            inferences=tuple(inferences),
            assumptions=tuple(assumptions),
            open_questions=tuple(self._open.values()),
        )


RespondFn = Callable[[Question], str]
"""受访者回答函数：接收问题，返回回答值。"""


class ScriptedRespondent:
    """模拟用户（测试/演示用）：按预置脚本逐题回答，可指定分类与假设标记。

    responses 的键为问题 ID，值为回答值或 ``(回答值, FactKind, assumption)`` 三元组。
    """

    def __init__(
        self,
        responses: dict[str, str | tuple[str, FactKind] | tuple[str, FactKind, bool]],
    ) -> None:
        self._responses = dict(responses)
        self.asked: list[str] = []  # 实际被问顺序（供断言"一次一问"）

    def answer(self, question: Question) -> Answer:
        self.asked.append(question.id)
        raw = self._responses[question.id]
        if isinstance(raw, str):
            value, kind, assumption = raw, FactKind.CONFIRMED_FACT, False
        elif len(raw) == 2:
            value, kind = raw
            assumption = False
        else:
            value, kind, assumption = raw
        return Answer(
            question_id=question.id,
            topic=question.topic,
            value=value,
            kind=kind,
            assumption=assumption,
        )


def run_interview_round(session: InterviewSession, respondent: ScriptedRespondent) -> int:
    """驱动一轮访谈：逐题询问直至队列耗尽或遇待解决矛盾，返回实际回答数。"""
    count = 0
    while (question := session.next_question()) is not None:
        ans = respondent.answer(question)
        contradiction = session.answer(
            question.id, ans.value, kind=ans.kind, assumption=ans.assumption, note=ans.note
        )
        if contradiction is not None:
            break  # 矛盾待解决，暂停本轮（§5.2：识别矛盾后主动确认）
        count += 1
    return count
