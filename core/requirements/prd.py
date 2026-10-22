"""PRD 生成与模板校验（规范 §5.4、§8 阶段 2）。

- 必填章节由 :data:`REQUIRED_SECTIONS` 唯一权威定义（§5.4 十三类内容 +
  §8 阶段 2 的版本与变更记录）；
- 内容来源：需求实体（版本/状态/验收条件/假设/待确认/变更记录）、访谈草案
  （假设与待确认事项）、调用方提供的章节正文；未涉及章节写入显式占位说明，
  不编造内容（红线：不伪造）；
- :func:`save_prd` 先渲染、再过 :func:`validate_prd_markdown` 模板校验，
  校验不过不落盘（P2-2 完成标准）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.domain.entities import Requirement, utc_now
from core.domain.enums import RequirementKind
from core.requirements.interview import RequirementDraft

EMPTY_SECTION = "（本轮未涉及，待后续补充）"

# (机器键, 章节标题) —— §5.4 全部内容 + §8 阶段 2 的版本与变更记录
REQUIRED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("goal_background", "项目目标与背景"),
    ("users_scenarios", "目标用户与场景"),
    ("scope_in", "功能范围"),
    ("scope_out", "明确不做的内容"),
    ("user_stories", "用户故事与验收条件"),
    ("business_rules", "业务规则与状态转换"),
    ("interaction", "交互需求"),
    ("data_entities", "数据实体与保留策略"),
    ("permission_audit", "权限模型与审计要求"),
    ("exceptions", "异常与边界"),
    ("non_functional", "非功能要求"),
    ("deployment", "部署环境与运行限制"),
    ("external_systems", "外部系统与依赖"),
    ("acceptance_plan", "验收计划"),
    ("risks", "风险、假设与待确认事项"),
    ("version_history", "版本与变更记录"),
)

SECTION_TITLES: tuple[str, ...] = tuple(title for _, title in REQUIRED_SECTIONS)


class PRDTemplateError(ValueError):
    """PRD 未通过模板校验：缺少必填章节、章节正文为空或章节顺序不符。"""

    def __init__(
        self,
        *,
        missing: tuple[str, ...] = (),
        empty: tuple[str, ...] = (),
        out_of_order: tuple[str, ...] = (),
    ) -> None:
        self.missing = missing
        self.empty = empty
        self.out_of_order = out_of_order
        parts: list[str] = []
        if missing:
            parts.append(f"缺少必填章节: {list(missing)}")
        if empty:
            parts.append(f"章节正文为空: {list(empty)}")
        if out_of_order:
            parts.append(f"章节顺序不符合模板: {list(out_of_order)}")
        super().__init__("PRD 模板校验失败（§5.4）: " + "; ".join(parts))


@dataclass(frozen=True, kw_only=True)
class UserStory:
    """用户故事（§5.4）：必须关联已存在的需求 ID，验收条件非空。"""

    id: str
    requirement_id: str
    role: str  # 作为…
    goal: str  # 我希望…
    benefit: str  # 以便…
    acceptance_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.id, self.requirement_id, self.role, self.goal, self.benefit):
            if not value.strip():
                raise ValueError("用户故事 id/requirement_id/role/goal/benefit 均不能为空")
        if not self.acceptance_criteria:
            raise ValueError(f"用户故事 {self.id} 缺少验收条件（§5.4）")


@dataclass(frozen=True)
class PRDSection:
    """PRD 单个章节：机器键、标题与已渲染正文。"""

    key: str
    title: str
    body: str


@dataclass(frozen=True)
class PRDDocument:
    """一份完整 PRD：§5.4 章节齐全，含版本与变更记录（§8 阶段 2）。"""

    project_name: str
    prd_version: int
    generated_at: str
    sections: tuple[PRDSection, ...]
    project_idea: str = ""

    def render(self) -> str:
        """渲染为 Markdown 文本。"""
        lines = [
            f"# PRD：{self.project_name}",
            "",
            f"- PRD 版本: v{self.prd_version}",
            f"- 生成时间: {self.generated_at}",
        ]
        if self.project_idea:
            lines.append(f"- 项目想法: {self.project_idea}")
        for section in self.sections:
            lines += ["", f"## {section.title}", "", section.body]
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, kw_only=True)
class PRDSpec:
    """PRD 生成输入：章节正文（空串 → 显式占位）与用户故事。

    功能范围/非功能要求/部署环境章节会自动并入对应类别的需求实体列表；
    风险章节会自动并入需求与访谈草案中的假设、待确认事项。
    """

    project_name: str
    project_idea: str = ""
    prd_version: int = 1
    goal_background: str = ""
    users_scenarios: str = ""
    scope_in: str = ""
    scope_out: str = ""
    business_rules: str = ""
    interaction: str = ""
    data_entities: str = ""
    permission_audit: str = ""
    exceptions_boundary: str = ""
    deployment: str = ""
    external_systems: str = ""
    acceptance_plan: str = ""
    risks: str = ""
    user_stories: tuple[UserStory, ...] = ()
    generated_at: str = ""  # 空串 → 构建时取当前 UTC 时间


def _join_blocks(*blocks: str) -> str:
    return "\n".join(b.strip() for b in blocks if b.strip())


def _requirement_lines(reqs: list[Requirement]) -> list[str]:
    return [
        f"- {r.id} [{r.status.value}] {r.title}（验收条件 {len(r.acceptance_criteria)} 项）"
        for r in reqs
    ]


def _render_user_stories(stories: tuple[UserStory, ...], reqs: list[Requirement]) -> str:
    parts: list[str] = []
    for s in stories:
        criteria = "\n".join(f"  - {c}" for c in s.acceptance_criteria)
        parts.append(
            f"### {s.id}（关联需求 {s.requirement_id}）\n"
            f"作为 {s.role}，我希望 {s.goal}，以便 {s.benefit}。\n"
            f"验收条件：\n{criteria}"
        )
    ac_lines = [
        f"- {r.id} {r.title}：" + "；".join(r.acceptance_criteria)
        for r in reqs
        if r.acceptance_criteria
    ]
    if ac_lines:
        parts.append("需求验收条件汇总：\n" + "\n".join(ac_lines))
    return "\n\n".join(parts)


def _render_risks(risks: str, reqs: list[Requirement], draft: RequirementDraft | None) -> str:
    parts: list[str] = []
    if risks.strip():
        parts.append(risks.strip())
    assumptions = [f"- [{r.id}] {a}" for r in reqs for a in r.assumptions]
    if draft is not None:
        assumptions += [f"- [访谈] {a.topic}: {a.value}" for a in draft.assumptions]
    if assumptions:
        parts.append("临时假设（未经确认不得当作事实，§5.2）：\n" + "\n".join(assumptions))
    opens = [f"- [{r.id}] {q}" for r in reqs for q in r.open_questions]
    if draft is not None:
        opens += [f"- [访谈] {o.topic}: {o.reason}" for o in draft.open_questions]
    if opens:
        parts.append("待确认事项（标记待定，不得当作事实，§5.2）：\n" + "\n".join(opens))
    return "\n\n".join(parts)


def _render_version_history(reqs: list[Requirement]) -> str:
    if not reqs:
        return ""
    lines = [
        "| 需求 ID | 标题 | 状态 | 版本 | 确认方 | 变更次数 |",
        "|---|---|---|---|---|---|",
    ]
    for r in reqs:
        lines.append(
            f"| {r.id} | {r.title} | {r.status.value} | v{r.version} "
            f"| {r.confirmed_by or '—'} | {len(r.change_records)} |"
        )
    details: list[str] = []
    for r in reqs:
        for i, c in enumerate(r.change_records, 1):
            affected = "、".join(c.affected_tasks) or "—"
            details.append(
                f"### {r.id} 变更 {i}（{c.at}）\n"
                f"- 原因：{c.reason}\n"
                f"- 影响范围：{c.impact_scope}\n"
                f"- 成本：{c.cost}\n"
                f"- 受影响任务：{affected}\n"
                f"- 回归测试要求：{c.regression_required or '—'}\n"
                f"- 提出方：{c.requested_by or '—'}"
            )
    if details:
        lines += ["", "\n\n".join(details)]
    return "\n".join(lines)


def build_prd(
    spec: PRDSpec,
    requirements: list[Requirement] | tuple[Requirement, ...],
    draft: RequirementDraft | None = None,
) -> PRDDocument:
    """由需求实体（+ 可选访谈草案）生成完整 PRD 文档。

    - 用户故事关联的需求 ID 必须存在，否则抛 ValueError；
    - 需求按类别（功能/非功能/约束）自动并入对应章节；
    - 未涉及章节写入显式占位说明，不编造内容。
    """
    reqs = list(requirements)
    ids = {r.id for r in reqs}
    for story in spec.user_stories:
        if story.requirement_id not in ids:
            raise ValueError(f"用户故事 {story.id} 关联的需求不存在: {story.requirement_id}")

    functional = [r for r in reqs if r.kind is RequirementKind.FUNCTIONAL]
    non_functional = [r for r in reqs if r.kind is RequirementKind.NON_FUNCTIONAL]
    constraints = [r for r in reqs if r.kind is RequirementKind.CONSTRAINT]

    bodies: dict[str, str] = {
        "goal_background": _join_blocks(
            spec.goal_background,
            f"项目想法：{spec.project_idea}" if spec.project_idea.strip() else "",
        ),
        "users_scenarios": spec.users_scenarios,
        "scope_in": _join_blocks(spec.scope_in, *_requirement_lines(functional)),
        "scope_out": spec.scope_out,
        "user_stories": _render_user_stories(spec.user_stories, reqs),
        "business_rules": spec.business_rules,
        "interaction": spec.interaction,
        "data_entities": spec.data_entities,
        "permission_audit": spec.permission_audit,
        "exceptions": spec.exceptions_boundary,
        "non_functional": _join_blocks(*_requirement_lines(non_functional)),
        "deployment": _join_blocks(spec.deployment, *_requirement_lines(constraints)),
        "external_systems": spec.external_systems,
        "acceptance_plan": spec.acceptance_plan,
        "risks": _render_risks(spec.risks, reqs, draft),
        "version_history": _render_version_history(reqs),
    }
    sections = tuple(
        PRDSection(key=key, title=title, body=bodies[key].strip() or EMPTY_SECTION)
        for key, title in REQUIRED_SECTIONS
    )
    return PRDDocument(
        project_name=spec.project_name,
        prd_version=spec.prd_version,
        generated_at=spec.generated_at.strip() or utc_now(),
        sections=sections,
        project_idea=spec.project_idea,
    )


def validate_prd_markdown(text: str) -> None:
    """模板校验：§5.4 必填章节齐全、顺序一致、正文非空；失败抛 PRDTemplateError。"""
    lines = text.splitlines()
    heading_idx: dict[str, int] = {}
    for i, line in enumerate(lines):
        if line.startswith("## "):
            heading_idx.setdefault(line[3:].strip(), i)

    missing = tuple(t for t in SECTION_TITLES if t not in heading_idx)
    out_of_order: tuple[str, ...] = ()
    if not missing:
        positions = [heading_idx[t] for t in SECTION_TITLES]
        if positions != sorted(positions):
            out_of_order = ("章节顺序须与 §5.4 模板一致",)

    empty: list[str] = []
    for title in SECTION_TITLES:
        if title not in heading_idx:
            continue
        start = heading_idx[title] + 1
        end = len(lines)
        for j in range(start, len(lines)):
            if lines[j].startswith(("# ", "## ")):
                end = j
                break
        if not "\n".join(lines[start:end]).strip():
            empty.append(title)

    if missing or empty or out_of_order:
        raise PRDTemplateError(missing=missing, empty=tuple(empty), out_of_order=out_of_order)


def save_prd(doc: PRDDocument, directory: Path | str) -> Path:
    """渲染 → 模板校验 → 落盘 ``<directory>/PRD-v{版本}.md``；校验不过不写文件。"""
    text = doc.render()
    validate_prd_markdown(text)
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"PRD-v{doc.prd_version}.md"
    path.write_text(text, encoding="utf-8")
    return path
