"""core.py 单元测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from cli_validator.core import ValidationResult, Violation, main, validate_config


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_valid_config_passes(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "ok.yaml",
        {"project_name": "demo", "owner": "alice", "version": "1.0", "max_workers": 4},
    )
    result = validate_config(cfg)
    assert result.ok is True
    assert result.violations == []


def test_missing_file_is_reported(tmp_path: Path) -> None:
    result = validate_config(tmp_path / "no.yaml")
    assert result.ok is False
    assert any("文件不存在" in v.message for v in result.violations)


def test_missing_required_field(tmp_path: Path) -> None:
    cfg = _write(tmp_path / "bad.yaml", {"project_name": "demo"})
    result = validate_config(cfg)
    paths = {v.path for v in result.violations}
    assert paths == {"owner", "version"}


def test_wrong_types(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "bad.yaml",
        {"project_name": "demo", "owner": 1, "version": "1.0", "debug": "yes"},
    )
    result = validate_config(cfg)
    msgs = {v.path: v.message for v in result.violations}
    assert msgs["owner"].startswith("类型应为 str")
    assert msgs["debug"].startswith("类型应为 bool")


def test_range_violation(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "bad.yaml",
        {
            "project_name": "demo",
            "owner": "alice",
            "version": "1.0",
            "max_workers": 100,
        },
    )
    result = validate_config(cfg)
    assert any(v.path == "max_workers" and "范围" in v.message for v in result.violations)


def test_validation_result_add_method() -> None:
    r = ValidationResult(ok=True)
    r.add("x", "y")
    assert r.ok is False
    assert r.violations == [Violation(path="x", message="y")]


def test_main_return_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ok = _write(
        tmp_path / "ok.yaml",
        {"project_name": "d", "owner": "o", "version": "1"},
    )
    assert main([str(ok)]) == 0
    assert capsys.readouterr().out.strip() == "OK"

    bad = _write(tmp_path / "bad.yaml", {"project_name": "d"})
    assert main([str(bad)]) == 1
    assert main([]) == 2
