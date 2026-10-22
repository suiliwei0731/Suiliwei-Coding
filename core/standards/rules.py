"""工程规则实体与加载器（规范 §7.2、§16.3；实施计划 P3-1）。

规则文件（knowledge/standards/*.yaml）按 §7.2 七要素管理：
唯一标识与适用范围 / 规则说明（要求什么+为什么）/ 正例与反例 /
自动检查方法 / 严重程度（§16.3 五级）/ 豁免策略 / 审计字段（规则版本）。

- 加载即校验：先过 schemas/rule.schema.json（SchemaRegistry），再做语义校验
  （重复 ID、manual 规则不得携带检查类别、auto 规则必须声明类别）；
- 无法自动检查的规则 ``check.type == "manual"``（人工/AI 审查项，显式标记，
  不得伪装成机器强制，§7.2 末款）；
- 校验失败一次性报告全部问题并抛 :class:`RuleError`，不产生半截规则集。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from core.domain.enums import Severity
from core.domain.validation import SCHEMA_DIR, SchemaRegistry, SchemaValidationError

RULES_CORE_PATH = (
    Path(__file__).resolve().parents[2] / "knowledge" / "standards" / "rules-core.yaml"
)
RULE_SCHEMA_NAME = "rule"
FILE_SCHEMA_VERSION = 1


class RuleError(ValueError):
    """规则文件解析或语义校验失败（problems 列出全部问题）。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("规则文件未通过校验（§7.2）: " + "; ".join(problems))


class RuleCheckKind(StrEnum):
    """自动检查类别（§7.2.4：格式化、静态分析、类型检查、测试或自定义验证器）。"""

    FORMAT = "format"
    STATIC_ANALYSIS = "static_analysis"
    TYPE_CHECK = "type_check"
    TEST = "test"
    CUSTOM = "custom"


@dataclass(frozen=True)
class RuleCheck:
    """规则的检查方式：auto=机器可执行（必须声明 kind）；manual=人工/AI 审查项。"""

    type: str  # "auto" | "manual"（协议枚举，loader 已校验取值）
    method: str  # 检查方法说明（工具/规则族/审查要点）
    kind: RuleCheckKind | None = None

    @property
    def is_manual(self) -> bool:
        """人工/AI 审查项（§7.2：不得伪装成机器强制）。"""
        return self.type == "manual"


@dataclass(frozen=True)
class RuleWaiverPolicy:
    """豁免策略（§7.2.6 的策略面）：是否可豁免、授权审批角色、有效期上限。

    单条豁免记录（理由/范围/审批人/实际有效期）由 core/quality/waiver.py 管理。
    """

    allowed: bool
    approver_roles: tuple[str, ...] = ()
    max_validity_days: int | None = None


@dataclass(frozen=True)
class Rule:
    """一条可执行化管理的工程规则（§7.2 七要素）。"""

    id: str
    name: str
    scope: str  # 适用范围
    description: str  # 要求什么
    rationale: str  # 为什么
    good_examples: tuple[str, ...]
    bad_examples: tuple[str, ...]
    check: RuleCheck
    severity: Severity
    waiver: RuleWaiverPolicy
    rule_version: int = 1  # 审计字段：规则版本

    @property
    def is_manual_review(self) -> bool:
        """人工/AI 审查项标记（P3-1 完成标准）。"""
        return self.check.is_manual

    @property
    def is_blocking(self) -> bool:
        """BLOCKER 级规则失败即阻断（§16.3、config/quality-gates.yaml blocking_severities）。"""
        return self.severity is Severity.BLOCKER

    def to_dict(self) -> dict[str, Any]:
        """序列化为与 schemas/rule.schema.json 对齐的载荷（文件级版本不在条目中重复）。"""
        return {
            "id": self.id,
            "name": self.name,
            "scope": self.scope,
            "description": self.description,
            "rationale": self.rationale,
            "examples": {"good": list(self.good_examples), "bad": list(self.bad_examples)},
            "check": {
                "type": self.check.type,
                "method": self.check.method,
                **({"kind": self.check.kind.value} if self.check.kind is not None else {}),
            },
            "severity": self.severity.value,
            "waiver": {
                "allowed": self.waiver.allowed,
                **(
                    {
                        "approver_roles": list(self.waiver.approver_roles),
                        "max_validity_days": self.waiver.max_validity_days,
                    }
                    if self.waiver.allowed
                    else {}
                ),
            },
            "rule_version": self.rule_version,
        }


def _parse_rule(entry: Any, registry: SchemaRegistry, problems: list[str]) -> Rule | None:
    """单条规则：Schema 校验 + 语义校验；问题记入 problems，返回 None 表示该条失败。"""
    if not isinstance(entry, dict):
        problems.append(f"规则条目不是映射: {entry!r}")
        return None
    label = str(entry.get("id") or "(无 ID)")
    try:
        registry.validate(RULE_SCHEMA_NAME, entry)
    except SchemaValidationError as exc:
        problems.append(f"规则 {label} 协议校验失败: {exc}")
        return None
    check = entry["check"]
    kind = RuleCheckKind(check["kind"]) if check["type"] == "auto" else None
    waiver = entry["waiver"]
    return Rule(
        id=str(entry["id"]),
        name=str(entry["name"]),
        scope=str(entry["scope"]),
        description=str(entry["description"]),
        rationale=str(entry["rationale"]),
        good_examples=tuple(str(x) for x in entry["examples"]["good"]),
        bad_examples=tuple(str(x) for x in entry["examples"]["bad"]),
        check=RuleCheck(type=str(check["type"]), method=str(check["method"]), kind=kind),
        severity=Severity(entry["severity"]),
        waiver=RuleWaiverPolicy(
            allowed=bool(waiver["allowed"]),
            approver_roles=tuple(str(r) for r in waiver.get("approver_roles", ())),
            max_validity_days=(
                int(waiver["max_validity_days"]) if waiver.get("max_validity_days") else None
            ),
        ),
        rule_version=int(entry["rule_version"]),
    )


def parse_rules(data: Any, registry: SchemaRegistry | None = None) -> tuple[Rule, ...]:
    """从已解析的规则文件载荷构造规则集；任一失败抛 :class:`RuleError`（含全部问题）。"""
    reg = registry or SchemaRegistry(SCHEMA_DIR)
    problems: list[str] = []
    if not isinstance(data, dict):
        raise RuleError(["规则文件内容不是映射"])
    if data.get("schema_version") != FILE_SCHEMA_VERSION:
        problems.append(
            f"schema_version 应为 {FILE_SCHEMA_VERSION}: {data.get('schema_version')!r}"
        )
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        problems.append("规则文件缺少非空 rules 列表")
    rules: list[Rule] = []
    seen: set[str] = set()
    for entry in raw_rules if isinstance(raw_rules, list) else []:
        rule = _parse_rule(entry, reg, problems)
        if rule is None:
            continue
        if rule.id in seen:
            problems.append(f"规则 ID 重复: {rule.id}")
            continue
        seen.add(rule.id)
        rules.append(rule)
    if problems:
        raise RuleError(problems)
    return tuple(rules)


def load_rules(
    path: Path | str = RULES_CORE_PATH, registry: SchemaRegistry | None = None
) -> tuple[Rule, ...]:
    """加载规则文件（默认平台核心规则库）；解析/校验失败抛 :class:`RuleError`。"""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuleError([f"规则文件不可读: {path}（{exc}）"]) from exc
    except yaml.YAMLError as exc:
        raise RuleError([f"规则文件 YAML 解析失败: {path}（{exc}）"]) from exc
    return parse_rules(data, registry)
