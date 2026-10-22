"""架构工作流测试（规范 §8 阶段 3）：ADR、契约与阶段 3 门禁。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.architecture import (
    ADRDocument,
    ADRError,
    Alternative,
    ArchitectureGateError,
    ArchitecturePlan,
    Contract,
    ContractError,
    RiskItem,
    assert_architecture_ready,
    next_adr_number,
    save_adr,
    save_contract,
    save_plan,
    validate_payload,
)
from core.domain.validation import SCHEMA_DIR, SchemaRegistry, SchemaValidationError


def _alt(**kwargs: Any) -> Alternative:
    defaults: dict[str, Any] = {
        "option": "A. 方案甲",
        "pros": "简单直接",
        "cons": "扩展性有限",
        "verdict": "**采用**",
    }
    defaults.update(kwargs)
    return Alternative(**defaults)


def _adr(**kwargs: Any) -> ADRDocument:
    defaults: dict[str, Any] = {
        "number": 7,
        "title": "测试决策",
        "background": "需要为某能力选型",
        "decision": "采用方案甲",
        "alternatives": (_alt(), _alt(option="B. 方案乙", verdict="拒绝")),
        "conclusion": "按方案甲实施，后续演进另立 ADR",
        "date": "2026-10-22",
        "related": ("ADR-000（架构总览）",),
    }
    defaults.update(kwargs)
    return ADRDocument(**defaults)


class TestADR:
    def test_render_structure(self) -> None:
        text = _adr().render()
        assert text.startswith("# ADR-007：测试决策\n")
        assert "- 状态：已接受" in text
        assert "- 日期：2026-10-22" in text
        assert "- 关联：ADR-000（架构总览）" in text
        for section in ("## 背景", "## 决策", "## 备选方案对比", "## 结论"):
            assert section in text
        assert "| 方案 | 优点 | 缺点 | 结论 |" in text
        assert "| A. 方案甲 | 简单直接 | 扩展性有限 | **采用** |" in text
        assert "| B. 方案乙 |" in text

    def test_default_status_and_date(self) -> None:
        adr = _adr(date="")
        assert adr.status == "已接受"
        assert "- 日期：2" in adr.render()  # 空日期回退为当天

    def test_invalid_fields_rejected(self) -> None:
        with pytest.raises(ADRError, match="标题"):
            _adr(title=" ")
        with pytest.raises(ADRError, match="状态非法"):
            _adr(status="随便")
        with pytest.raises(ADRError, match="备选方案"):
            _adr(alternatives=())
        with pytest.raises(ADRError, match="背景"):
            _adr(background="")
        with pytest.raises(ADRError, match="编号"):
            _adr(number=-1)

    def test_alternative_fields_required(self) -> None:
        with pytest.raises(ADRError):
            _alt(cons="")

    def test_next_adr_number(self, tmp_path: Path) -> None:
        assert next_adr_number(tmp_path) == 0
        (tmp_path / "ADR-000-架构总览.md").write_text("x", encoding="utf-8")
        (tmp_path / "ADR-003-基线决策记录.md").write_text("x", encoding="utf-8")
        (tmp_path / "ADR-001-存储选型.md").write_text("x", encoding="utf-8")
        (tmp_path / "README.md").write_text("x", encoding="utf-8")
        assert next_adr_number(tmp_path) == 4

    def test_save_adr_writes_file(self, tmp_path: Path) -> None:
        path = save_adr(_adr(number=4, title="Phase 2 目录细化"), tmp_path)
        assert path.name == "ADR-004-Phase 2 目录细化.md"
        assert path.read_text(encoding="utf-8").startswith("# ADR-004：Phase 2 目录细化")

    def test_save_adr_sanitizes_filename(self, tmp_path: Path) -> None:
        path = save_adr(_adr(number=1, title='选型: A/B "对比"'), tmp_path)
        assert path.name == "ADR-001-选型 AB 对比.md"

    def test_save_adr_refuses_overwrite(self, tmp_path: Path) -> None:
        save_adr(_adr(number=2), tmp_path)
        with pytest.raises(ADRError, match="不得覆盖"):
            save_adr(_adr(number=2, title="另一个标题"), tmp_path)


def _contract(**kwargs: Any) -> Contract:
    defaults: dict[str, Any] = {
        "name": "widget-api",
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["widget_id"],
            "properties": {"widget_id": {"type": "string"}},
            "additionalProperties": False,
        },
    }
    defaults.update(kwargs)
    return Contract(**defaults)


class TestContract:
    def test_save_and_validate_payload(self, tmp_path: Path) -> None:
        path = save_contract(_contract(), tmp_path)
        assert path.name == "widget-api.schema.json"
        assert "widget-api" in SchemaRegistry(tmp_path).names
        validate_payload("widget-api", {"widget_id": "W-1"}, tmp_path)  # 不抛异常即通过
        with pytest.raises(SchemaValidationError):
            validate_payload("widget-api", {"widget_id": 42}, tmp_path)
        with pytest.raises(SchemaValidationError):
            validate_payload("widget-api", {}, tmp_path)

    def test_invalid_schema_rejected(self) -> None:
        with pytest.raises(ContractError, match="合法 JSON Schema"):
            _contract(schema={"type": "no-such-type"})
        with pytest.raises(ContractError, match="非空字典"):
            _contract(schema={})

    def test_invalid_name_rejected(self) -> None:
        with pytest.raises(ContractError, match="契约名非法"):
            _contract(name="Bad Name")
        with pytest.raises(ContractError, match="契约名非法"):
            _contract(name="1abc")

    def test_overwrite_refused(self, tmp_path: Path) -> None:
        save_contract(_contract(), tmp_path)
        with pytest.raises(ContractError, match="不得覆盖"):
            save_contract(_contract(), tmp_path)


def _risk(**kwargs: Any) -> RiskItem:
    defaults: dict[str, Any] = {
        "id": "R-01",
        "description": "模型输出不稳定",
        "impact": "任务返工率升高",
        "mitigation": "重试预算 + 证据门禁",
    }
    defaults.update(kwargs)
    return RiskItem(**defaults)


def _plan(**kwargs: Any) -> ArchitecturePlan:
    defaults: dict[str, Any] = {
        "project_name": "演示项目",
        "overview": "Python 3.12 + SQLite，理由：零运维桌面形态",
        "modules": "core/ 与 apps/ 分层",
        "data_model": "实体表 + 版本化迁移",
        "security_boundary": "最小权限，秘密不入库",
        "test_strategy": "单元 + 集成 + 阶段门禁",
        "deployment": "Windows 本地桌面应用",
        "adr_files": ("ADR-009-测试决策.md",),
        "api_contracts": ("task",),
        "risks": (_risk(),),
    }
    defaults.update(kwargs)
    return ArchitecturePlan(**defaults)


@pytest.fixture()
def decisions_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "decisions"
    directory.mkdir()
    (directory / "ADR-009-测试决策.md").write_text("# ADR-009：测试决策\n", encoding="utf-8")
    return directory


class TestArchitectureGate:
    def test_ready_plan_passes(self, decisions_dir: Path) -> None:
        # 契约用仓库真实 schemas/（task 已登记），ADR 用临时目录
        assert_architecture_ready(_plan(), decisions_dir=decisions_dir)

    def test_reports_all_problems(self, decisions_dir: Path, tmp_path: Path) -> None:
        plan = _plan(
            overview="",
            data_model="  ",
            adr_files=("ADR-404-不存在.md",),
            api_contracts=("no-such-contract",),
            risks=(),
        )
        with pytest.raises(ArchitectureGateError) as exc_info:
            assert_architecture_ready(plan, decisions_dir=decisions_dir)
        problems = exc_info.value.problems
        assert any("技术栈与选型理由" in p for p in problems)
        assert any("数据模型与迁移" in p for p in problems)
        assert any("风险清单为空" in p for p in problems)
        assert any("ADR-404" in p for p in problems)
        assert any("no-such-contract" in p for p in problems)

    def test_missing_adr_and_contracts_rejected(self, decisions_dir: Path) -> None:
        plan = _plan(adr_files=(), api_contracts=())
        with pytest.raises(ArchitectureGateError) as exc_info:
            assert_architecture_ready(plan, decisions_dir=decisions_dir)
        assert any("缺少 ADR" in p for p in exc_info.value.problems)
        assert any("缺少接口/数据契约" in p for p in exc_info.value.problems)

    def test_saved_contract_passes_gate(self, decisions_dir: Path, tmp_path: Path) -> None:
        schemas = tmp_path / "schemas"
        save_contract(_contract(), schemas)
        plan = _plan(api_contracts=("widget-api",))
        assert_architecture_ready(plan, schema_dir=schemas, decisions_dir=decisions_dir)

    def test_risk_item_fields_required(self) -> None:
        with pytest.raises(ValueError):
            _risk(mitigation="")


class TestPlanRender:
    def test_render_contains_sections_and_risks(self) -> None:
        text = _plan().render()
        for title in ("技术栈与选型理由", "模块与目录", "数据模型与迁移", "安全边界与权限"):
            assert f"## {title}" in text
        assert "## 关键决策（ADR）" in text and "ADR-009-测试决策.md" in text
        assert "## 接口与数据契约" in text and "schemas/task.schema.json" in text
        assert "| R-01 | 模型输出不稳定 | 任务返工率升高 | 重试预算 + 证据门禁 |" in text

    def test_save_plan(self, tmp_path: Path) -> None:
        out = save_plan(_plan(), tmp_path / "docs" / "architecture" / "方案.md")
        assert out.read_text(encoding="utf-8").startswith("# 架构方案：演示项目")


def test_repo_schemas_dir_registered() -> None:
    # 既有契约在真实 schemas/ 下均可登记（门禁默认路径可用）
    assert set(SchemaRegistry(SCHEMA_DIR).names) >= {"task", "requirement", "test-report"}
