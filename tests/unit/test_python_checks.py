"""P3-3 单元测试：检查适配器框架与 Python 首实现（规范 §7.4/§16.5）。

适配器对临时工作区真实执行 ruff/mypy/pytest（开发环境已安装）；
工具缺失与超时通过 monkeypatch 模拟——必须 UNVERIFIED 且含原因与可复现命令（红线 2）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from adapters.tools import PythonCheckAdapter
from core.domain.enums import TestStatus
from core.quality import CheckKind, CheckResult

CLEAN_PY = '"""模块。"""\n\n\ndef add(a: int, b: int) -> int:\n    return a + b\n'
LINT_VIOLATION_PY = "import os\n"  # F401：导入未使用


@pytest.fixture()
def adapter(tmp_path: Path) -> PythonCheckAdapter:
    return PythonCheckAdapter(artifacts_dir=tmp_path / "artifacts")


def _write(path: Path, name: str, content: str) -> None:
    (path / name).write_text(content, encoding="utf-8")


def test_supported_kinds_covers_five(adapter: PythonCheckAdapter) -> None:
    """完成标准：五类检查统一结构。"""
    assert adapter.supported_kinds() == frozenset(CheckKind)
    assert {k.value for k in adapter.supported_kinds()} == {
        "format",
        "lint",
        "type_check",
        "test",
        "build",
    }


def test_lint_clean_passes(adapter: PythonCheckAdapter, tmp_path: Path) -> None:
    _write(tmp_path, "ok.py", CLEAN_PY)
    result = adapter.run(CheckKind.LINT, tmp_path)
    assert result.status is TestStatus.PASSED
    assert result.exit_code == 0
    assert result.tool == "ruff" and result.tool_version
    assert "ruff" in result.command and result.started_at and result.finished_at


def test_format_clean_passes(adapter: PythonCheckAdapter, tmp_path: Path) -> None:
    _write(tmp_path, "ok.py", CLEAN_PY)
    result = adapter.run(CheckKind.FORMAT, tmp_path)
    assert result.status is TestStatus.PASSED
    assert result.exit_code == 0


def test_lint_violation_fails(adapter: PythonCheckAdapter, tmp_path: Path) -> None:
    _write(tmp_path, "bad.py", LINT_VIOLATION_PY)
    result = adapter.run(CheckKind.LINT, tmp_path)
    assert result.status is TestStatus.FAILED
    assert result.exit_code == 1
    assert "F401" in result.summary


def test_type_check_passes(adapter: PythonCheckAdapter, tmp_path: Path) -> None:
    _write(tmp_path, "typed.py", CLEAN_PY)
    result = adapter.run(CheckKind.TYPE_CHECK, tmp_path)
    assert result.status is TestStatus.PASSED
    assert result.tool == "mypy" and result.tool_version


def test_pytest_pass_with_counts_and_artifact(adapter: PythonCheckAdapter, tmp_path: Path) -> None:
    _write(tmp_path, "test_ok.py", "def test_ok() -> None:\n    assert 1 + 1 == 2\n")
    result = adapter.run(CheckKind.TEST, tmp_path)
    assert result.status is TestStatus.PASSED
    assert (result.total, result.passed, result.failed) == (1, 1, 0)
    assert len(result.artifact_paths) == 1
    assert Path(result.artifact_paths[0]).exists()  # JUnit XML 产物（§7.4）


def test_pytest_failure_fails(adapter: PythonCheckAdapter, tmp_path: Path) -> None:
    _write(tmp_path, "test_bad.py", "def test_bad() -> None:\n    assert 1 == 2\n")
    result = adapter.run(CheckKind.TEST, tmp_path)
    assert result.status is TestStatus.FAILED
    assert result.failed == 1 and result.exit_code != 0


def test_missing_tool_unverified(
    adapter: PythonCheckAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """红线 2：工具不可用 → UNVERIFIED + 原因 + 可复现命令，不计入通过。"""
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)
    result = adapter.run(CheckKind.LINT, tmp_path)
    assert result.status is TestStatus.UNVERIFIED
    assert result.exit_code is None
    assert "未安装模块 ruff" in result.unverified_reason
    assert "可复现命令" in result.unverified_reason
    assert "ruff" in result.command  # 可复现命令入证据


def test_timeout_unverified(
    adapter: PythonCheckAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["x"], timeout=1)

    monkeypatch.setattr(subprocess, "run", _raise)
    _write(tmp_path, "ok.py", CLEAN_PY)
    result = adapter.run(CheckKind.LINT, tmp_path)
    assert result.status is TestStatus.UNVERIFIED
    assert "超时" in result.unverified_reason and "可复现命令" in result.unverified_reason


def test_checkresult_validation() -> None:
    with pytest.raises(ValueError, match="UNVERIFIED"):
        CheckResult(kind=CheckKind.LINT, tool="ruff", status=TestStatus.UNVERIFIED)
    with pytest.raises(ValueError, match="退出码"):
        CheckResult(kind=CheckKind.LINT, tool="ruff", status=TestStatus.PASSED, command="ruff .")


def test_to_test_report_maps_fields(adapter: PythonCheckAdapter, tmp_path: Path) -> None:
    _write(tmp_path, "test_ok.py", "def test_ok() -> None:\n    assert True\n")
    result = adapter.run(CheckKind.TEST, tmp_path)
    report = result.to_test_report("TASK-1")
    assert report.id.startswith("TRUN-")
    assert report.task_id == "TASK-1"
    assert report.tool == result.tool and report.command == result.command
    assert report.status is result.status and report.exit_code == result.exit_code
    assert (report.total, report.passed) == (result.total, result.passed)
    assert report.artifact_path == result.artifact_paths[0]
