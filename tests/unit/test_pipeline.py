"""P5-2 单元测试：端到端流水线。"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters.languages.python.adapter import _TEMPLATES_ROOT
from core.audit.logger import AuditLogger
from core.orchestrator.pipeline import EndToEndPipeline, PipelinePhase
from core.orchestrator.store import StateStore
from core.quality.checks import TestStatus


@pytest.fixture
def store_audit(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    audit = AuditLogger(store, log_dir=tmp_path / "logs")
    yield store, audit
    store.close()


@pytest.fixture
def cli_source() -> Path:
    return _TEMPLATES_ROOT / "cli"


def test_pipeline_runs_cli_template(store_audit, cli_source: Path, tmp_path: Path) -> None:
    store, audit = store_audit
    pipeline = EndToEndPipeline(store=store, audit=audit, checkpoint_dir=tmp_path / "ckpts")
    result = pipeline.run("PRJ-DEMO", cli_source)

    assert result.project_id == "PRJ-DEMO"
    assert len(result.phases) >= 4
    assert len(result.verification) == 5
    assert result.ok is True
    for r in result.verification:
        assert r.status is TestStatus.PASSED, f"{r.kind} failed: {r.summary}"


def test_pipeline_summary_counts(store_audit, cli_source: Path, tmp_path: Path) -> None:
    store, audit = store_audit
    pipeline = EndToEndPipeline(store=store, audit=audit, checkpoint_dir=tmp_path / "ckpts")
    result = pipeline.run("PRJ-SUM", cli_source)

    summary = result.summary()
    assert summary["project_id"] == "PRJ-SUM"
    assert summary["ok"] is True
    assert summary["verification_passed"] == summary["verification_total"]


def test_pipeline_saves_checkpoints(store_audit, cli_source: Path, tmp_path: Path) -> None:
    store, audit = store_audit
    ckpt = tmp_path / "ckpts"
    pipeline = EndToEndPipeline(store=store, audit=audit, checkpoint_dir=ckpt)
    pipeline.run("PRJ-CK", cli_source)

    files = {p.stem for p in ckpt.glob("*.checkpoint.json")}
    assert any("start" in f for f in files)
    assert any("end" in f for f in files)
    for phase in PipelinePhase:
        assert any(f"{phase.value}-latest" in f for f in files), f"{phase.value}-latest 未找到"


def test_pipeline_recover_returns_last_phase(store_audit, cli_source: Path, tmp_path: Path) -> None:
    store, audit = store_audit
    ckpt = tmp_path / "ckpts"
    pipeline = EndToEndPipeline(store=store, audit=audit, checkpoint_dir=ckpt)
    pipeline.run("PRJ-REC", cli_source)

    last = pipeline.recover("PRJ-REC")
    assert last is PipelinePhase.DONE
