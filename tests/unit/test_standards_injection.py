"""P3-2 单元测试：项目规则注入（规范 §7.1）。

完成标准：不同项目类型加载不同规则集，且注入有审计记录（含规则 id 与版本）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.audit import AuditLogger
from core.domain import Severity
from core.domain.entities import Task, new_id
from core.orchestrator import StateStore
from core.standards import (
    Rule,
    RuleCheck,
    RuleCheckKind,
    RuleSetError,
    RuleWaiverPolicy,
    inject_rules,
    load_ruleset,
    parse_ruleset,
)

BUILTIN_TYPES = ("cli", "service", "desktop", "script")


def _rule(rule_id: str, scope: str = "code", version: int = 1) -> Rule:
    """构造最小规则实体（语义测试用内存规则库）。"""
    return Rule(
        id=rule_id,
        name=f"规则 {rule_id}",
        scope=scope,
        description="要求",
        rationale="原因",
        good_examples=("好例",),
        bad_examples=("坏例",),
        check=RuleCheck(type="auto", method="ruff", kind=RuleCheckKind.STATIC_ANALYSIS),
        severity=Severity.MINOR,
        waiver=RuleWaiverPolicy(allowed=False),
        rule_version=version,
    )


@pytest.mark.parametrize("project_type", BUILTIN_TYPES)
def test_builtin_rulesets_load(project_type: str) -> None:
    ruleset = load_ruleset(project_type)
    assert ruleset.project_type == project_type
    assert ruleset.rules, f"{project_type} 规则集为空"


def test_builtin_rulesets_differ() -> None:
    """完成标准：不同项目类型加载出不同规则集。"""
    selections = {pt: {r.id for r in load_ruleset(pt).rules} for pt in BUILTIN_TYPES}
    assert len({frozenset(ids) for ids in selections.values()}) == len(BUILTIN_TYPES)


def test_builtin_rulesets_content() -> None:
    cli = {r.id for r in load_ruleset("cli").rules}
    service = {r.id for r in load_ruleset("service").rules}
    desktop = {r.id for r in load_ruleset("desktop").rules}
    script = {r.id for r in load_ruleset("script").rules}
    assert "STD-011" not in cli  # CLI 无自有数据库
    assert {"STD-011", "STD-012"} <= service  # 服务端含数据库全部强约束
    assert "STD-011" in desktop and "STD-012" not in desktop  # 桌面：参数化强制，迁移不套用
    assert script == {"STD-002", "STD-003", "STD-004", "STD-005", "STD-009", "STD-010"}
    for selected in (cli, service, desktop, script):
        assert "STD-009" in selected  # 反伪造 BLOCKER 任何项目类型不可免除


def test_select_by_id_and_scope_with_exclude() -> None:
    library = (_rule("STD-001"), _rule("STD-002", scope="docs"), _rule("STD-003", scope="docs"))
    ruleset = parse_ruleset(
        {
            "schema_version": 1,
            "project_type": "cli",
            "select": [{"id": "STD-001"}, {"scope": "docs"}],
            "exclude": [{"id": "STD-003"}],
        },
        rules=library,
    )
    assert [r.id for r in ruleset.rules] == ["STD-001", "STD-002"]


def test_selection_keeps_library_order() -> None:
    library = (_rule("STD-001"), _rule("STD-002"), _rule("STD-003"))
    ruleset = parse_ruleset(
        {
            "schema_version": 1,
            "project_type": "cli",
            "select": [{"id": "STD-003"}, {"id": "STD-001"}],  # 乱序选取
        },
        rules=library,
    )
    assert [r.id for r in ruleset.rules] == ["STD-001", "STD-003"]


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "project_type": "cli", "select": [{"id": "STD-404"}]},
        {"schema_version": 1, "project_type": "cli", "select": [{"scope": "api"}]},  # 空匹配
        {
            "schema_version": 1,
            "project_type": "cli",
            "select": [{"scope": "code"}],
            "exclude": [{"id": "STD-404"}],
        },
        {"schema_version": 2, "project_type": "cli", "select": [{"scope": "code"}]},
        {"schema_version": 1, "project_type": "cli", "select": []},  # 协议校验失败
    ],
)
def test_invalid_rulesets_rejected(payload: dict) -> None:
    with pytest.raises(RuleSetError):
        parse_ruleset(payload)


def test_project_type_mismatch_rejected(tmp_path: Path) -> None:
    (tmp_path / "cli.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "project_type": "service", "select": [{"scope": "code"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuleSetError, match="不一致"):
        load_ruleset("cli", tmp_path)


def test_missing_ruleset_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(RuleSetError, match="不可读"):
        load_ruleset("no-such-type", tmp_path)


def test_inject_rules_writes_audit(tmp_path: Path) -> None:
    """完成标准：注入有记录（action=rules.injected，含规则 id 与 rule_version）。"""
    with StateStore(tmp_path / "platform.db") as store:
        audit = AuditLogger(store)
        task = Task(id=new_id("TASK"), project_id="PRJ-1", title="实现 CLI", owner_role="dev")
        ruleset = load_ruleset("cli")
        injection = inject_rules(task, ruleset, audit)

        assert injection.task_id == task.id
        assert injection.project_type == "cli"
        assert len(injection.rules) == len(ruleset.rules)

        events = audit.list_events()
        assert len(events) == 1
        event = events[0]
        assert event.action == "rules.injected"
        assert event.task_id == task.id and event.project_id == "PRJ-1"
        detail_rules = event.detail["rules"]
        assert detail_rules == [{"id": r.id, "rule_version": r.rule_version} for r in ruleset.rules]
        assert all("rule_version" in item for item in detail_rules)
