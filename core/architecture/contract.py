"""接口/数据契约文件管理（规范 §8 阶段 3/5：接口先行、契约机器可读）。

- 契约即 JSON Schema 文档，落盘 ``schemas/{name}.schema.json``；
- 落盘前先以 ``Draft202012Validator.check_schema`` 校验契约自身合法性，
  落盘后用 :class:`~core.domain.validation.SchemaRegistry` 重载确认可登记
  （P2-4 完成标准：契约通过 Schema 校验）；
- 契约一经落盘不得覆盖（§8 阶段 5：契约必须先达成一致，演进应新增而非篡改）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError as JsonSchemaError

from core.domain.validation import SCHEMA_DIR, SCHEMA_SUFFIX, SchemaRegistry

CONTRACT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class ContractError(ValueError):
    """契约非法（名称不符、自身不是合法 JSON Schema、覆盖既有契约等）。"""


@dataclass(frozen=True)
class Contract:
    """一份契约：名称 + JSON Schema 文档 + 可选说明。"""

    name: str
    schema: dict[str, Any]
    description: str = ""

    def __post_init__(self) -> None:
        if not CONTRACT_NAME_RE.fullmatch(self.name):
            raise ContractError(
                f"契约名非法: {self.name!r}（须匹配 {CONTRACT_NAME_RE.pattern}，"
                "与 task/test-report 等既有命名一致）"
            )
        if not isinstance(self.schema, dict) or not self.schema:
            raise ContractError(f"契约 {self.name} 的 schema 必须是非空字典")
        try:
            Draft202012Validator.check_schema(self.schema)
        except JsonSchemaError as e:
            raise ContractError(f"契约 {self.name} 自身不是合法 JSON Schema: {e.message}") from None


def save_contract(contract: Contract, schema_dir: Path | str | None = None) -> Path:
    """落盘契约并以 SchemaRegistry 重载确认可登记；已存在同名契约时抛 ContractError。"""
    directory = Path(schema_dir) if schema_dir is not None else SCHEMA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / f"{contract.name}{SCHEMA_SUFFIX}"
    if out.exists():
        raise ContractError(f"契约已存在: {out.name}（契约演进应新增版本，不得覆盖既有契约）")
    out.write_text(
        json.dumps(contract.schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    registry = SchemaRegistry(directory)
    if contract.name not in registry.names:
        raise ContractError(f"契约落盘后未被 SchemaRegistry 登记: {contract.name}")
    return out


def validate_payload(
    contract_name: str, payload: dict[str, Any], schema_dir: Path | str | None = None
) -> None:
    """按已登记契约校验载荷；失败抛 SchemaValidationError（含字段路径明细）。"""
    SchemaRegistry(Path(schema_dir) if schema_dir is not None else SCHEMA_DIR).validate(
        contract_name, payload
    )
