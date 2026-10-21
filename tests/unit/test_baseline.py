"""Phase 0 基线单元测试：配置可解析、注册表完整、严重度量表齐全、规则唯一且合法。

对应实施计划 P0-4/P0-5；复用 scripts/validate/check_baseline.py 的校验逻辑，
保证「测试即校验」而非另写一套口径。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "validate"))

import check_baseline  # noqa: E402

EXPECTED_SEVERITY_SCALE = ["BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO"]
EXPECTED_SKILL_COUNT = 11  # Phase 0 交付的 11 个自有技能清单


def _load(relpath: str) -> dict:
    data = yaml.safe_load((ROOT / relpath).read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{relpath} 应为映射"
    return data


def test_config_files_parse_and_versioned() -> None:
    for name in check_baseline.CONFIG_FILES:
        data = _load(f"config/{name}")
        assert data.get("schema_version") == 1, f"config/{name} schema_version 应为 1"


def test_severity_scale_complete_and_ordered() -> None:
    gates = _load("config/quality-gates.yaml")
    assert gates["severity_scale"] == EXPECTED_SEVERITY_SCALE
    assert "BLOCKER" in gates["blocking_severities"]


def test_registry_unique_and_complete() -> None:
    registry = _load("skills/registry.yaml")
    skills = registry["skills"]
    assert len(skills) == EXPECTED_SKILL_COUNT
    ids = [s["id"] for s in skills]
    assert len(ids) == len(set(ids)), "技能 ID 必须唯一"
    for skill in skills:
        assert set(check_baseline.SKILL_REQUIRED_KEYS) <= set(skill)
        assert skill["status"] in check_baseline.SKILL_STATUSES
        if skill["status"] in {"draft", "draft-reviewed", "active"}:
            assert (ROOT / skill["path"]).is_file(), f"{skill['path']} 应存在"


def test_rules_unique_and_valid() -> None:
    rules_doc = _load("knowledge/standards/rules-core.yaml")
    rules = rules_doc["rules"]
    ids = [r["id"] for r in rules]
    assert len(ids) == len(set(ids)), "规则 ID 必须唯一"
    for rule in rules:
        assert rule["severity"] in EXPECTED_SEVERITY_SCALE
        assert rule["check"]["type"] in {"auto", "manual"}
        assert str(rule["check"]["method"]).strip(), "检查方式不能为空"


def test_check_baseline_module_all_pass() -> None:
    results = check_baseline.run_checks(ROOT)
    failed = [r for r in results if not r.ok]
    assert not failed, "基线校验存在失败项: " + "; ".join(f"{r.name}: {r.detail}" for r in failed)
