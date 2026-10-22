"""项目规则注入（规范 §7.1；实施计划 P3-2）。

项目类型模板（config/rulesets/<project_type>.yaml）在平台核心规则库
（knowledge/standards/rules-core.yaml）上声明式选取规则：

- 加载即校验：先过 schemas/ruleset.schema.json（SchemaRegistry），再做语义校验
  （文件内外 project_type 一致、select 的 id 存在、scope 至少匹配一条、
  exclude 的 id 存在），问题一次性全部报告并抛 :class:`RuleSetError`；
- :func:`inject_rules` 将规则集绑定到任务并写审计（action=``rules.injected``，
  detail 含规则 id 与 rule_version 列表），保证"哪版规则约束哪个任务"可追溯
  （§7.2 审计字段、§17 审计要求）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from core.domain.entities import utc_now
from core.domain.validation import SCHEMA_DIR, SchemaRegistry, SchemaValidationError
from core.standards.rules import Rule, load_rules

if TYPE_CHECKING:
    from core.audit.logger import AuditLogger
    from core.domain.entities import Task

RULESETS_DIR = Path(__file__).resolve().parents[2] / "config" / "rulesets"
RULESET_SCHEMA_NAME = "ruleset"
FILE_SCHEMA_VERSION = 1
DEFAULT_ACTOR = "工程标准执行引擎"


class RuleSetError(ValueError):
    """规则集文件解析或语义校验失败（problems 列出全部问题）。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("项目规则集未通过校验（§7.1）: " + "; ".join(problems))


@dataclass(frozen=True)
class RuleRef:
    """注入记录中的规则引用：id + 规则版本（审计字段）。"""

    id: str
    rule_version: int

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "rule_version": self.rule_version}


@dataclass(frozen=True)
class RuleSet:
    """一个项目类型的规则集：从核心规则库解析后的确定规则序列（按库内顺序）。"""

    project_type: str
    description: str
    rules: tuple[Rule, ...]

    @property
    def rule_refs(self) -> tuple[RuleRef, ...]:
        """规则 id 与版本引用列表（注入与审计使用）。"""
        return tuple(RuleRef(id=r.id, rule_version=r.rule_version) for r in self.rules)


@dataclass(frozen=True)
class RuleInjection:
    """一次规则注入的记录（inject_rules 返回值；审计事件由 AuditLogger 落库）。"""

    task_id: str
    project_id: str
    project_type: str
    rules: tuple[RuleRef, ...]
    at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "project_type": self.project_type,
            "rules": [r.to_dict() for r in self.rules],
            "at": self.at,
        }


def parse_ruleset(
    data: Any,
    *,
    registry: SchemaRegistry | None = None,
    rules: tuple[Rule, ...] | None = None,
) -> RuleSet:
    """从已解析的规则集载荷构造 RuleSet；任一失败抛 :class:`RuleSetError`（含全部问题）。

    :param rules: 可选规则库（默认加载平台核心规则库）；解析顺序保持库内顺序。
    """
    reg = registry or SchemaRegistry(SCHEMA_DIR)
    library = {r.id: r for r in (rules if rules is not None else load_rules(registry=reg))}
    problems: list[str] = []
    if not isinstance(data, dict):
        raise RuleSetError(["规则集文件内容不是映射"])
    if data.get("schema_version") != FILE_SCHEMA_VERSION:
        problems.append(
            f"schema_version 应为 {FILE_SCHEMA_VERSION}: {data.get('schema_version')!r}"
        )
    try:
        reg.validate(RULESET_SCHEMA_NAME, data)
    except SchemaValidationError as exc:
        problems.append(f"规则集协议校验失败: {exc}")
    if problems:
        raise RuleSetError(problems)

    project_type = str(data["project_type"])
    selected: set[str] = set()
    for entry in data["select"]:
        if "id" in entry:
            rid = str(entry["id"])
            if rid not in library:
                problems.append(f"select 引用了未知规则 ID: {rid}")
                continue
            selected.add(rid)
        else:
            scope = str(entry["scope"])
            matched = [r for r in library.values() if r.scope == scope]
            if not matched:
                problems.append(f"select 的 scope 未匹配任何规则: {scope}")
                continue
            selected.update(r.id for r in matched)
    for entry in data.get("exclude", []):
        rid = str(entry["id"])
        if rid not in library:
            problems.append(f"exclude 引用了未知规则 ID: {rid}")
            continue
        selected.discard(rid)
    if problems:
        raise RuleSetError(problems)
    ordered = tuple(r for r in library.values() if r.id in selected)
    return RuleSet(
        project_type=project_type,
        description=str(data.get("description", "")),
        rules=ordered,
    )


def load_ruleset(
    project_type: str,
    rulesets_dir: Path | str = RULESETS_DIR,
    *,
    registry: SchemaRegistry | None = None,
    rules: tuple[Rule, ...] | None = None,
) -> RuleSet:
    """按项目类型加载规则集模板（config/rulesets/<project_type>.yaml）。

    文件内 project_type 与请求的项目类型不一致时拒绝（防止模板错挂）。
    """
    path = Path(rulesets_dir) / f"{project_type}.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuleSetError([f"规则集文件不可读: {path}（{exc}）"]) from exc
    except yaml.YAMLError as exc:
        raise RuleSetError([f"规则集文件 YAML 解析失败: {path}（{exc}）"]) from exc
    if isinstance(data, dict) and data.get("project_type") not in (None, project_type):
        raise RuleSetError(
            [
                f"规则集文件 project_type 为 {data.get('project_type')!r}，"
                f"与请求的 {project_type!r} 不一致"
            ]
        )
    return parse_ruleset(data, registry=registry, rules=rules)


def inject_rules(
    task: Task,
    ruleset: RuleSet,
    audit: AuditLogger,
    *,
    actor: str = DEFAULT_ACTOR,
) -> RuleInjection:
    """把规则集绑定到任务：写审计（action=rules.injected）并返回注入记录。"""
    injection = RuleInjection(
        task_id=task.id,
        project_id=task.project_id,
        project_type=ruleset.project_type,
        rules=ruleset.rule_refs,
        at=utc_now(),
    )
    audit.log(
        "rules.injected",
        actor,
        project_id=task.project_id,
        task_id=task.id,
        detail={
            "project_type": ruleset.project_type,
            "rules": [r.to_dict() for r in injection.rules],
        },
    )
    return injection
