"""基线校验脚本（P0-4/P0-5 配套）。

校验项：
  1. config/ 五份 YAML 存在且可解析，schema_version == 1
  2. quality-gates.yaml 严重度量表为规范 §16.3 的 5 级且顺序正确
  3. skills/registry.yaml：条目字段齐全、ID 唯一、状态合法；draft/active 技能文件必须存在
  4. knowledge/standards/rules-core.yaml：规则 ID 唯一、严重度合法、检查方式或人工审查项标记齐全

任一失败退出码为 1。可被 tests/unit/test_baseline.py 以模块方式复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

CONFIG_FILES = [
    "platform.yaml",
    "models.yaml",
    "policies.yaml",
    "quality-gates.yaml",
    "language-adapters.yaml",
]
SEVERITY_SCALE = ["BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO"]
SKILL_STATUSES = {"planned", "draft", "draft-reviewed", "active", "deprecated"}
SKILL_REQUIRED_KEYS = {"id", "name", "path", "status", "description"}


@dataclass
class CheckResult:
    """单条校验结果。"""

    name: str
    ok: bool
    detail: str


def load_yaml(path: Path) -> tuple[Any | None, str]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh), ""
    except (OSError, yaml.YAMLError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def check_config_files(root: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    for name in CONFIG_FILES:
        path = root / "config" / name
        data, err = load_yaml(path)
        if err:
            results.append(CheckResult(f"config/{name} 可解析", False, err))
            continue
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            results.append(CheckResult(f"config/{name} schema_version==1", False, "缺失或值不为 1"))
            continue
        results.append(CheckResult(f"config/{name} 可解析", True, "schema_version=1"))
    return results


def check_severity_scale(root: Path) -> CheckResult:
    data, err = load_yaml(root / "config" / "quality-gates.yaml")
    if err:
        return CheckResult("严重度量表（5 级齐全且顺序正确）", False, err)
    assert data is not None
    scale = data.get("severity_scale")
    ok = scale == SEVERITY_SCALE
    detail = f"severity_scale={scale}" if ok else "应为 BLOCKER>CRITICAL>MAJOR>MINOR>INFO"
    return CheckResult("严重度量表（5 级齐全且顺序正确）", ok, detail)


def check_registry(root: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    data, err = load_yaml(root / "skills" / "registry.yaml")
    if err or not isinstance(data, dict):
        return [CheckResult("skills/registry.yaml 可解析", False, err or "内容不是映射")]
    skills = data.get("skills")
    if not isinstance(skills, list) or not skills:
        return [CheckResult("skills/registry.yaml skills 非空", False, "缺失 skills 列表")]

    results.append(CheckResult("skills/registry.yaml 可解析", True, f"{len(skills)} 条记录"))
    seen: set[str] = set()
    dup: list[str] = []
    for item in skills:
        skill_id = str(item.get("id", ""))
        if skill_id in seen:
            dup.append(skill_id)
        seen.add(skill_id)
        missing = SKILL_REQUIRED_KEYS - set(item)
        if missing:
            results.append(
                CheckResult(
                    f"技能 {skill_id or '(无 ID)'} 字段齐全", False, f"缺 {sorted(missing)}"
                )
            )
            continue
        status = item["status"]
        if status not in SKILL_STATUSES:
            results.append(CheckResult(f"技能 {skill_id} 状态合法", False, f"非法状态 {status!r}"))
            continue
        if status in {"draft", "draft-reviewed", "active"}:
            skill_file = root / item["path"]
            if not skill_file.is_file():
                results.append(
                    CheckResult(f"技能 {skill_id} 文件存在", False, f"{item['path']} 不存在")
                )
    if dup:
        results.append(CheckResult("技能 ID 唯一", False, f"重复: {dup}"))
    else:
        results.append(CheckResult("技能 ID 唯一", True, f"共 {len(seen)} 条"))
    return results


def check_rules(root: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    data, err = load_yaml(root / "knowledge" / "standards" / "rules-core.yaml")
    if err or not isinstance(data, dict):
        return [CheckResult("rules-core.yaml 可解析", False, err or "内容不是映射")]
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        return [CheckResult("rules-core.yaml rules 非空", False, "缺失 rules 列表")]

    results.append(CheckResult("rules-core.yaml 可解析", True, f"{len(rules)} 条规则"))
    seen: set[str] = set()
    dup: list[str] = []
    for rule in rules:
        rule_id = str(rule.get("id", ""))
        if rule_id in seen:
            dup.append(rule_id)
        seen.add(rule_id)
        if not rule.get("name"):
            results.append(CheckResult(f"规则 {rule_id} 有名称", False, "缺失 name"))
        if rule.get("severity") not in SEVERITY_SCALE:
            results.append(
                CheckResult(f"规则 {rule_id} 严重度合法", False, f"{rule.get('severity')!r}")
            )
        check = rule.get("check")
        if not isinstance(check, dict) or check.get("type") not in {"auto", "manual"}:
            results.append(
                CheckResult(f"规则 {rule_id} 检查方式合法", False, "check.type 须为 auto/manual")
            )
        elif not str(check.get("method", "")).strip():
            results.append(CheckResult(f"规则 {rule_id} 检查方式合法", False, "check.method 为空"))
    if dup:
        results.append(CheckResult("规则 ID 唯一", False, f"重复: {dup}"))
    else:
        results.append(CheckResult("规则 ID 唯一", True, f"共 {len(seen)} 条"))
    return results


def run_checks(root: Path = ROOT) -> list[CheckResult]:
    results = check_config_files(root)
    results.append(check_severity_scale(root))
    results.extend(check_registry(root))
    results.extend(check_rules(root))
    return results


def main() -> int:
    results = run_checks()
    failed = 0
    for res in results:
        mark = "PASS" if res.ok else "FAIL"
        print(f"[{mark}] {res.name}: {res.detail}")
        failed += 0 if res.ok else 1
    total = len(results)
    print(f"\n基线校验：{total - failed}/{total} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
