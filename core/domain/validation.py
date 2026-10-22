"""JSON Schema 注册与校验（规范 §14.4 结构化协议）。

schemas/ 目录下每份 ``*.schema.json`` 按文件名登记（task.schema.json → ``task``）。
校验失败抛 :class:`SchemaValidationError`，错误信息含字段路径，便于审计定位。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonValidationError

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
SCHEMA_SUFFIX = ".schema.json"


class SchemaValidationError(ValueError):
    """协议载荷不满足对应 JSON Schema。"""


def _format_error(err: JsonValidationError) -> str:
    path = "/".join(str(p) for p in err.absolute_path) or "(根)"
    return f"{path}: {err.message}"


class SchemaRegistry:
    """加载并校验 schemas/ 下的全部协议 Schema。"""

    def __init__(self, schema_dir: Path | None = None) -> None:
        self._dir = schema_dir or SCHEMA_DIR
        if not self._dir.is_dir():
            raise SchemaValidationError(f"Schema 目录不存在: {self._dir}")
        self._validators: dict[str, Draft202012Validator] = {}
        for path in sorted(self._dir.glob(f"*{SCHEMA_SUFFIX}")):
            name = path.name[: -len(SCHEMA_SUFFIX)]
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)  # Schema 自身非法时尽早暴露
            self._validators[name] = Draft202012Validator(schema)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._validators)

    def validate(self, name: str, instance: dict[str, Any]) -> None:
        """校验载荷；失败抛 SchemaValidationError（含前 5 处错误明细）。"""
        try:
            validator = self._validators[name]
        except KeyError:
            raise SchemaValidationError(f"未登记的 Schema: {name!r}") from None
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
        if errors:
            details = "; ".join(_format_error(e) for e in errors[:5])
            raise SchemaValidationError(f"[{name}] 校验失败（{len(errors)} 处）: {details}")
