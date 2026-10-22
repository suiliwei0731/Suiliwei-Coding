"""P5-4 单元测试：缺陷注入实验。

闭环：注入缺陷 → Pipeline 检出 FAILED → 修复 → 回归验证通过。
全程不伪造 PASSED——FAILED 绝不变形。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from adapters.languages.python.adapter import _TEMPLATES_ROOT
from core.audit.logger import AuditLogger
from core.orchestrator.fault_injection import (
    FaultInjector,
    FaultType,
    verify_fault_was_caught,
)
from core.orchestrator.pipeline import EndToEndPipeline
from core.orchestrator.store import StateStore
from core.quality.checks import CheckKind


@pytest.fixture
def infected_project(tmp_path: Path) -> Path:
    """复制 CLI 模板到临时目录，供注入使用。"""
    src = _TEMPLATES_ROOT / "cli"
    dst = tmp_path / "infected-cli"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "runtime"))
    return dst


@pytest.fixture
def store_audit(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    audit = AuditLogger(store, log_dir=tmp_path / "logs")
    yield store, audit
    store.close()


def test_inject_unused_import_triggers_lint_failure(infected_project: Path) -> None:
    injector = FaultInjector(infected_project)
    fault = injector.inject_unused_import("cli_validator/core.py")
    assert fault.kind is FaultType.LINT

    injector2 = FaultInjector(infected_project)
    # 注入在 cli_validator/core.py → ruff F401
    injector2.inject_unused_import("cli_validator/core.py")
    from adapters.tools.python_checks import PythonCheckAdapter

    result = PythonCheckAdapter().run(CheckKind.LINT, infected_project)
    assert verify_fault_was_caught([result], CheckKind.LINT) is True


def test_pipeline_detects_injected_lint_failure(
    infected_project: Path, store_audit, tmp_path: Path
) -> None:
    """注入 lint 缺陷 → Pipeline ok=False；FAILED 绝不变形。"""
    store, audit = store_audit
    injector = FaultInjector(infected_project)
    injector.inject_unused_import("cli_validator/core.py")

    pipeline = EndToEndPipeline(store=store, audit=audit, checkpoint_dir=tmp_path / "ckpts")
    result = pipeline.run("PRJ-FAULT", infected_project)
    assert result.ok is False
    assert verify_fault_was_caught(result.verification, CheckKind.LINT)

    # 直接断言：FAILED 检查的 status 就是 FAILED，没有被改写
    lint_result = next(r for r in result.verification if r.kind is CheckKind.LINT)
    assert lint_result.status.value == "FAILED"
    assert "F401" in (lint_result.summary or "") or "unused" in (lint_result.summary or "").lower()


def test_after_fault_removed_pipeline_passes(
    infected_project: Path, store_audit, tmp_path: Path
) -> None:
    """修复缺陷 → Pipeline ok=True；完成闭环。"""
    store, audit = store_audit
    injector = FaultInjector(infected_project)
    injector.inject_unused_import("cli_validator/core.py")

    pipeline = EndToEndPipeline(store=store, audit=audit, checkpoint_dir=tmp_path / "ckpts")
    result = pipeline.run("PRJ-BAD", infected_project)
    assert result.ok is False

    # 恢复：手动移除注入行（tmp_path 天然隔离，直接复制干净模板覆盖）
    src = _TEMPLATES_ROOT / "cli"
    shutil.rmtree(infected_project)
    shutil.copytree(
        src, infected_project, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "runtime")
    )

    result2 = pipeline.run("PRJ-FIXED", infected_project)
    assert result2.ok is True
    # 再次确认 FAILED 检查已经转为 PASSED
    lint_result = next(r for r in result2.verification if r.kind is CheckKind.LINT)
    assert lint_result.status.value == "PASSED"
