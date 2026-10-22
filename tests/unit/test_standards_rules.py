"""规则 Schema 与加载器测试（规范 §7.2；实施计划 P3-1 完成标准）。

完成标准：规则文件解析与校验通过；支持"人工审查项"标记。
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from core.domain import Severity
from core.standards import (
    RULES_CORE_PATH,
    Rule,
    RuleCheckKind,
    RuleError,
    load_rules,
    parse_rules,
)


def _entry(rule_id: str, **kwargs: Any) -> dict[str, Any]:
    """最小合法规则载荷（字段对齐 schemas/rule.schema.json）。"""
    defaults: dict[str, Any] = {
        "id": rule_id,
        "name": "示例规则",
        "scope": "code",
        "description": "要求什么",
        "rationale": "为什么",
        "examples": {"good": ["好例子"], "bad": ["坏例子"]},
        "check": {"type": "auto", "kind": "static_analysis", "method": "ruff"},
        "severity": "MAJOR",
        "waiver": {"allowed": True, "approver_roles": ["架构师"], "max_validity_days": 30},
        "rule_version": 1,
    }
    defaults.update(kwargs)
    return defaults


def _file(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "rules": list(entries)}


class TestCoreRulesetLoads:
    """平台核心规则库（knowledge/standards/rules-core.yaml）必须通过校验。"""

    def test_core_rules_load(self) -> None:
        rules = load_rules()
        assert len(rules) == 12
        assert all(isinstance(r, Rule) for r in rules)

    def test_ids_unique_and_prefixed(self) -> None:
        rules = load_rules()
        ids = [r.id for r in rules]
        assert len(ids) == len(set(ids))
        assert all(r.id.startswith("STD-") for r in rules)

    def test_manual_rules_explicitly_marked(self) -> None:
        """人工/AI 审查项：manual 规则显式标记且不携带检查类别（不得伪装成机器强制）。"""
        rules = load_rules()
        manual = [r for r in rules if r.is_manual_review]
        auto = [r for r in rules if not r.is_manual_review]
        assert manual and auto  # 两类都存在
        assert all(r.check.kind is None for r in manual)
        assert all(isinstance(r.check.kind, RuleCheckKind) for r in auto)

    def test_severity_and_blocking(self) -> None:
        rules = {r.id: r for r in load_rules()}
        assert rules["STD-009"].severity is Severity.BLOCKER
        assert rules["STD-009"].is_blocking
        assert not rules["STD-009"].waiver.allowed  # 红线规则不可豁免
        assert not rules["STD-001"].is_blocking

    def test_audit_fields_present(self) -> None:
        """§7.2.7 审计字段：规则版本、检查方式、豁免策略齐全。"""
        for rule in load_rules():
            assert rule.rule_version >= 1
            assert rule.check.method.strip()
            if rule.waiver.allowed:
                assert rule.waiver.approver_roles
                assert rule.waiver.max_validity_days

    def test_roundtrip_dict_still_valid(self) -> None:
        """Rule.to_dict() 可重新过 Schema 并解析（序列化不失真）。"""
        rule = load_rules()[0]
        parsed = parse_rules(_file(rule.to_dict()))
        assert parsed == (rule,)


class TestParseValidation:
    """加载器语义校验：一次性报告全部问题，不产生半截规则集。"""

    def test_valid_minimal(self) -> None:
        rules = parse_rules(_file(_entry("TST-001")))
        assert rules[0].id == "TST-001"
        assert rules[0].check.kind is RuleCheckKind.STATIC_ANALYSIS

    def test_manual_without_kind_valid(self) -> None:
        entry = _entry("TST-002", check={"type": "manual", "method": "AI/人工审查项"})
        rules = parse_rules(_file(entry))
        assert rules[0].is_manual_review
        assert rules[0].check.kind is None

    def test_auto_without_kind_rejected(self) -> None:
        entry = _entry("TST-003")
        entry["check"] = {"type": "auto", "method": "ruff"}  # 缺 kind
        with pytest.raises(RuleError, match="kind"):
            parse_rules(_file(entry))

    def test_manual_with_kind_rejected(self) -> None:
        """manual 携带 kind = 伪装成机器强制（§7.2 末款），必须拒绝。"""
        entry = _entry("TST-004")
        entry["check"] = {"type": "manual", "method": "审查", "kind": "test"}
        with pytest.raises(RuleError):
            parse_rules(_file(entry))

    def test_duplicate_id_rejected(self) -> None:
        with pytest.raises(RuleError, match="重复"):
            parse_rules(_file(_entry("TST-005"), _entry("TST-005")))

    def test_waiver_allowed_requires_roles_and_validity(self) -> None:
        entry = _entry("TST-006", waiver={"allowed": True})
        with pytest.raises(RuleError):
            parse_rules(_file(entry))

    def test_illegal_severity_rejected(self) -> None:
        with pytest.raises(RuleError):
            parse_rules(_file(_entry("TST-007", severity="HIGH")))

    def test_collects_multiple_problems(self) -> None:
        bad_a = _entry("TST-008", severity="HIGH")
        bad_b = _entry("TST-009")
        bad_b["check"] = {"type": "auto", "method": "ruff"}
        with pytest.raises(RuleError) as exc_info:
            parse_rules(_file(bad_a, bad_b))
        assert len(exc_info.value.problems) >= 2

    def test_non_mapping_file_rejected(self) -> None:
        with pytest.raises(RuleError):
            parse_rules(["not", "a", "mapping"])

    def test_empty_rules_rejected(self) -> None:
        with pytest.raises(RuleError, match="rules"):
            parse_rules({"schema_version": 1, "rules": []})


class TestLoadRulesFile:
    def test_missing_file(self, tmp_path: Any) -> None:
        with pytest.raises(RuleError, match="不可读"):
            load_rules(tmp_path / "no-such.yaml")

    def test_broken_yaml(self, tmp_path: Any) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("rules: [unclosed", encoding="utf-8")
        with pytest.raises(RuleError, match="YAML"):
            load_rules(path)

    def test_load_from_alt_path(self, tmp_path: Any) -> None:
        path = tmp_path / "rules.yaml"
        path.write_text(yaml.safe_dump(_file(_entry("TST-010")), allow_unicode=True), "utf-8")
        rules = load_rules(path)
        assert rules[0].id == "TST-010"

    def test_default_path_is_core_rules(self) -> None:
        assert RULES_CORE_PATH.is_file()
