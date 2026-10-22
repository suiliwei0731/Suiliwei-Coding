"""架构决策记录（ADR）生成与编号（规范 §8 阶段 3、§14.2）。

- ADR 落盘 ``docs/architecture/decisions/ADR-NNN-标题.md``，编号自动接续目录中
  现有最大编号（ADR-000 起）；
- 文档结构固定：标题行 + 状态/日期/关联 + 背景 / 决策 / 备选方案对比（表格）/ 结论；
- 备选方案必须给出优点、缺点与结论——重大分歧向用户解释选项与代价（§8 阶段 3）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DECISIONS_DIR = Path(__file__).resolve().parents[2] / "docs" / "architecture" / "decisions"
ADR_FILE_RE = re.compile(r"^ADR-(\d{3,})-.+\.md$")
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')

ADR_STATUSES: tuple[str, ...] = ("提议", "已接受", "已废弃", "被取代")


class ADRError(ValueError):
    """ADR 数据非法（必填字段为空、状态非法、编号冲突等）。"""


@dataclass(frozen=True)
class Alternative:
    """备选方案对比项（§8 阶段 3：向用户解释选项与代价）。"""

    option: str  # 方案
    pros: str  # 优点
    cons: str  # 缺点
    verdict: str  # 结论

    def __post_init__(self) -> None:
        for value in (self.option, self.pros, self.cons, self.verdict):
            if not value.strip():
                raise ADRError("备选方案的 方案/优点/缺点/结论 均不能为空")


@dataclass(frozen=True, kw_only=True)
class ADRDocument:
    """一份 ADR：编号、标题、状态、日期、关联、背景、决策、备选方案对比、结论。"""

    number: int
    title: str
    background: str
    decision: str
    alternatives: tuple[Alternative, ...]
    conclusion: str
    status: str = "已接受"
    date: str = ""  # 空串 → 渲染时取当天日期（YYYY-MM-DD）
    related: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.number < 0:
            raise ADRError(f"ADR 编号不得为负: {self.number}")
        if not self.title.strip():
            raise ADRError("ADR 标题不能为空")
        if self.status not in ADR_STATUSES:
            raise ADRError(f"ADR 状态非法: {self.status!r}（合法值 {ADR_STATUSES}）")
        for label, value in (
            ("背景", self.background),
            ("决策", self.decision),
            ("结论", self.conclusion),
        ):
            if not value.strip():
                raise ADRError(f"ADR {label}不能为空")
        if not self.alternatives:
            raise ADRError("ADR 必须包含备选方案对比（§8 阶段 3：解释选项与代价）")

    @property
    def filename(self) -> str:
        """落盘文件名：``ADR-NNN-标题.md``（剔除 Windows 文件名非法字符）。"""
        slug = INVALID_FILENAME_CHARS.sub("", self.title).strip()
        return f"ADR-{self.number:03d}-{slug}.md"

    def render(self) -> str:
        """渲染为 Markdown 文本（结构与既有 ADR-000~003 一致）。"""
        date = self.date.strip() or datetime.now(UTC).date().isoformat()
        related = "、".join(self.related) if self.related else "—"
        lines = [
            f"# ADR-{self.number:03d}：{self.title}",
            "",
            f"- 状态：{self.status}",
            f"- 日期：{date}",
            f"- 关联：{related}",
            "",
            "## 背景",
            "",
            self.background.strip(),
            "",
            "## 决策",
            "",
            self.decision.strip(),
            "",
            "## 备选方案对比",
            "",
            "| 方案 | 优点 | 缺点 | 结论 |",
            "|---|---|---|---|",
        ]
        for alt in self.alternatives:
            lines.append(f"| {alt.option} | {alt.pros} | {alt.cons} | {alt.verdict} |")
        lines += ["", "## 结论", "", self.conclusion.strip()]
        return "\n".join(lines) + "\n"


def next_adr_number(decisions_dir: Path | str = DECISIONS_DIR) -> int:
    """目录中现有最大 ADR 编号 + 1；无 ADR 时返回 0（ADR-000 为架构总览）。"""
    directory = Path(decisions_dir)
    highest = -1
    if directory.is_dir():
        for path in directory.iterdir():
            match = ADR_FILE_RE.match(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def save_adr(adr: ADRDocument, decisions_dir: Path | str = DECISIONS_DIR) -> Path:
    """落盘 ADR；同编号已存在时抛 ADRError（决策演进应新增 ADR 而非覆盖历史）。"""
    directory = Path(decisions_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.iterdir():
        match = ADR_FILE_RE.match(path.name)
        if match and int(match.group(1)) == adr.number:
            raise ADRError(f"ADR-{adr.number:03d} 已存在: {path.name}（不得覆盖既有决策记录）")
    out = directory / adr.filename
    out.write_text(adr.render(), encoding="utf-8")
    return out
