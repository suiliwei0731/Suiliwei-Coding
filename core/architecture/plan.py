"""架构方案与阶段 3 门禁（规范 §8 阶段 3：架构与技术方案）。

阶段 3 交付物：架构文档、ADR、契约、风险清单。
通过条件：关键架构决策、技术约束和主要风险有记录；不得由单一编码角色直接跳到实现。

:func:`assert_architecture_ready` 即阶段 3 门禁，逐项检查并一次性报告全部问题：
- 方案必备章节（技术栈与选型/模块与目录/数据模型与迁移/安全边界/测试策略/部署策略）非空；
- 关键决策有 ADR：adr_files 非空且每份文件存在于 decisions/ 目录；
- 契约通过 Schema 校验：api_contracts 每个均已登记于 schemas/（SchemaRegistry）；
- 风险清单非空（主要风险有记录）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.architecture.adr import DECISIONS_DIR
from core.domain.validation import SCHEMA_DIR, SchemaRegistry

# (字段名, 章节标题) —— §8 阶段 3 必备内容
REQUIRED_PLAN_SECTIONS: tuple[tuple[str, str], ...] = (
    ("overview", "技术栈与选型理由"),
    ("modules", "模块与目录"),
    ("data_model", "数据模型与迁移"),
    ("security_boundary", "安全边界与权限"),
    ("test_strategy", "测试策略"),
    ("deployment", "配置/日志/监控/部署策略"),
)


class ArchitectureGateError(ValueError):
    """阶段 3 门禁未通过：problems 列出全部缺口。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("架构方案未通过阶段 3 门禁（§8）: " + "; ".join(problems))


@dataclass(frozen=True)
class RiskItem:
    """风险清单条目：描述、影响、缓解措施均为必填（§8 阶段 3：主要风险有记录）。"""

    id: str
    description: str
    impact: str
    mitigation: str

    def __post_init__(self) -> None:
        for value in (self.id, self.description, self.impact, self.mitigation):
            if not value.strip():
                raise ValueError("风险条目的 id/描述/影响/缓解措施均不能为空")


@dataclass(frozen=True, kw_only=True)
class ArchitecturePlan:
    """架构方案：必备章节 + ADR 引用 + 契约引用 + 风险清单。"""

    project_name: str
    overview: str = ""  # 技术栈与选型理由
    modules: str = ""  # 模块与目录
    data_model: str = ""  # 数据模型与迁移
    security_boundary: str = ""  # 安全边界与权限
    test_strategy: str = ""  # 测试策略
    deployment: str = ""  # 配置/日志/监控/部署策略
    adr_files: tuple[str, ...] = ()  # decisions/ 下的 ADR 文件名
    api_contracts: tuple[str, ...] = ()  # schemas/ 下登记的契约名
    risks: tuple[RiskItem, ...] = ()

    def render(self) -> str:
        """渲染为架构文档 Markdown（§8 阶段 3 交付物之一）。"""
        lines = [f"# 架构方案：{self.project_name}"]
        for field_name, title in REQUIRED_PLAN_SECTIONS:
            body = getattr(self, field_name).strip() or "（待补充）"
            lines += ["", f"## {title}", "", body]
        lines += ["", "## 关键决策（ADR）", ""]
        lines += [f"- {name}" for name in self.adr_files] or ["- （无）"]
        lines += ["", "## 接口与数据契约", ""]
        lines += [f"- schemas/{name}.schema.json" for name in self.api_contracts] or ["- （无）"]
        lines += [
            "",
            "## 风险清单",
            "",
            "| 编号 | 描述 | 影响 | 缓解措施 |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {r.id} | {r.description} | {r.impact} | {r.mitigation} |" for r in self.risks
        ] or ["| — | （无） | — | — |"]
        return "\n".join(lines) + "\n"


def assert_architecture_ready(
    plan: ArchitecturePlan,
    *,
    schema_dir: Path | str | None = None,
    decisions_dir: Path | str | None = None,
) -> None:
    """阶段 3 门禁：任一条件不满足抛 ArchitectureGateError（列出全部缺口）。"""
    problems: list[str] = []
    for field_name, title in REQUIRED_PLAN_SECTIONS:
        if not getattr(plan, field_name).strip():
            problems.append(f"方案缺少章节：{title}")
    if not plan.risks:
        problems.append("风险清单为空：主要风险必须有记录（§8 阶段 3）")
    decisions = Path(decisions_dir) if decisions_dir is not None else DECISIONS_DIR
    if not plan.adr_files:
        problems.append("关键决策缺少 ADR（§8 阶段 3：关键架构决策有记录）")
    else:
        for name in plan.adr_files:
            if not (decisions / name).is_file():
                problems.append(f"ADR 文件不存在: {name}")
    if not plan.api_contracts:
        problems.append("缺少接口/数据契约（§8 阶段 3 交付物）")
    else:
        registry = SchemaRegistry(Path(schema_dir) if schema_dir is not None else SCHEMA_DIR)
        for name in plan.api_contracts:
            if name not in registry.names:
                problems.append(f"契约未登记于 schemas/: {name}")
    if problems:
        raise ArchitectureGateError(problems)


def save_plan(plan: ArchitecturePlan, path: Path | str) -> Path:
    """架构文档落盘（父目录自动创建）。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(plan.render(), encoding="utf-8")
    return out
