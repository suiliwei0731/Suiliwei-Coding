"""配置校验核心：读取 YAML，按 schema 校验，返回结构化结果。"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Violation:
    path: str
    message: str


@dataclass
class ValidationResult:
    ok: bool
    violations: list[Violation] = field(default_factory=list)

    def add(self, path: str, message: str) -> None:
        self.violations.append(Violation(path=path, message=message))
        self.ok = False


_SCHEMA: dict[str, Any] = {
    "required": ["project_name", "owner", "version"],
    "types": {
        "project_name": str,
        "owner": str,
        "version": str,
        "max_workers": int,
        "debug": bool,
    },
    "ranges": {"max_workers": (1, 64)},
}


def validate_config(path: str | Path) -> ValidationResult:
    """读取 YAML 配置文件并按内置 schema 校验。"""
    result = ValidationResult(ok=True)
    p = Path(path)
    if not p.is_file():
        result.add(str(p), "文件不存在")
        return result
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        result.add(str(p), f"YAML 解析失败: {exc}")
        return result
    if not isinstance(data, dict):
        result.add(str(p), "根节点必须为映射")
        return result
    _check_required(data, result)
    _check_types(data, result)
    _check_ranges(data, result)
    return result


def _check_required(data: dict[str, Any], result: ValidationResult) -> None:
    for key in _SCHEMA["required"]:
        if key not in data:
            result.add(key, f"缺少必填字段 {key}")


def _check_types(data: dict[str, Any], result: ValidationResult) -> None:
    for key, expected in _SCHEMA["types"].items():
        if key in data and not isinstance(data[key], expected):
            result.add(key, f"类型应为 {expected.__name__}，实际 {type(data[key]).__name__}")


def _check_ranges(data: dict[str, Any], result: ValidationResult) -> None:
    for key, (lo, hi) in _SCHEMA["ranges"].items():
        if key in data and isinstance(data[key], int):
            if not lo <= data[key] <= hi:
                result.add(key, f"值应在 [{lo}, {hi}] 范围内")


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：校验 YAML，打印结果，返回 0（通过）/ 1（违规）。"""
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("用法: cli-validator <config.yaml>", file=sys.stderr)
        return 2
    result = validate_config(argv[0])
    if result.ok:
        print("OK")
        return 0
    for v in result.violations:
        print(f"FAIL {v.path}: {v.message}", file=sys.stderr)
    return 1
