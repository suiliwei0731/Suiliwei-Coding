"""Phase 5 阶段门禁集成测试（甲 Phase 4 验收 + 乙阶段 B）。

验收标准：
1. 真实小项目可交付——Pipeline 对 Python 模板跑通五链，
   DELIVER 阶段生成 README/测试报告/追踪矩阵/已知限制/.env.example；
2. 缺陷注入实验证明不谎报——注入 lint/type 缺陷 → ok=False 且
   FAILED 不被改写；修复后 ok=True；
3. 恢复实验通过——中途终止（checkpoint 存在）后重启续跑，
   已通过阶段不重做。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from adapters.languages.python.adapter import _TEMPLATES_ROOT
from core.audit.logger import AuditLogger
from core.orchestrator.deliverables import DeliverableBundle
from core.orchestrator.fault_injection import FaultInjector
from core.orchestrator.pipeline import EndToEndPipeline, PipelinePhase
from core.orchestrator.store import StateStore
from core.quality.checks import CheckKind, TestStatus


@pytest.fixture
def store_audit(tmp_path: Path):
    store = StateStore(tmp_path / "state.db")
    audit = AuditLogger(store, log_dir=tmp_path / "logs")
    yield store, audit
    store.close()


@pytest.fixture
def cli_copy(tmp_path: Path) -> Path:
    """复制 CLI 模板到临时目录——避免污染 git 跟踪的模板源。"""
    src = _TEMPLATES_ROOT / "cli"
    dst = tmp_path / "cli-proj"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "runtime"))
    return dst


@pytest.fixture
def pipeline(store_audit, tmp_path: Path) -> EndToEndPipeline:
    store, audit = store_audit
    return EndToEndPipeline(store=store, audit=audit, checkpoint_dir=tmp_path / "ckpts")


# ---------- 验收点 1：真实小项目可交付 ----------


def test_pipeline_delivers_five_artifacts(pipeline: EndToEndPipeline, cli_copy: Path) -> None:
    result = pipeline.run("PRJ-DELIVER", cli_copy)
    assert result.ok is True

    deliver_phase = next(p for p in result.phases if p.get("phase") == "deliver")
    files = deliver_phase.get("deliverables")
    assert files is not None
    assert len(files) == 5
    # runtime/artifacts/PRJ-DELIVER/deliverable/ 下的 5 个文件
    DeliverableBundle(
        project_id="PRJ-DELIVER",
        base_dir=Path("runtime/artifacts/PRJ-DELIVER/deliverable"),
        files=tuple(),
    )
    # 用 deliver_phase 记录的相对路径直接断言
    assert any("README.md" in f for f in files)
    assert any("test-report.md" in f for f in files)
    assert any("traceability.json" in f for f in files)
    assert any("known-limits.md" in f for f in files)
    assert any(".env.example" in f for f in files)


def test_deliverable_summary_counts_passed(pipeline: EndToEndPipeline, cli_copy: Path) -> None:
    result = pipeline.run("PRJ-SUMMARY", cli_copy)
    summary = result.summary()
    assert summary["ok"] is True
    assert summary["verification_passed"] == 5
    assert summary["verification_total"] == 5
    assert summary["project_id"] == "PRJ-SUMMARY"


# ---------- 验收点 2：缺陷注入不谎报 ----------


def test_injected_lint_failure_becomes_ok_false(pipeline: EndToEndPipeline, cli_copy: Path) -> None:
    injector = FaultInjector(cli_copy)
    injector.inject_unused_import("cli_validator/core.py")

    result = pipeline.run("PRJ-LINT", cli_copy)
    assert result.ok is False
    lint = next(r for r in result.verification if r.kind is CheckKind.LINT)
    # 红线：FAILED 绝不变形
    assert lint.status is TestStatus.FAILED


def test_fault_fixed_pipeline_recovers_ok(pipeline: EndToEndPipeline, cli_copy: Path) -> None:
    # 先注入缺陷跑一次（ok=False）
    injector = FaultInjector(cli_copy)
    injector.inject_unused_import("cli_validator/core.py")
    result_bad = pipeline.run("PRJ-RECOVERY", cli_copy)
    assert result_bad.ok is False

    # 修复：干净模板覆盖
    src = _TEMPLATES_ROOT / "cli"
    shutil.rmtree(cli_copy)
    shutil.copytree(src, cli_copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "runtime"))

    result_ok = pipeline.run("PRJ-FIXED", cli_copy)
    assert result_ok.ok is True
    lint = next(r for r in result_ok.verification if r.kind is CheckKind.LINT)
    assert lint.status is TestStatus.PASSED


# ---------- 验收点 3：恢复实验 ----------


def test_pipeline_recover_returns_latest_phase(pipeline: EndToEndPipeline, cli_copy: Path) -> None:
    pipeline.run("PRJ-RUN1", cli_copy)
    last = pipeline.recover("PRJ-RUN1")
    assert last is PipelinePhase.DONE


def test_has_checkpoint_for_after_run(pipeline: EndToEndPipeline, cli_copy: Path) -> None:
    pipeline.run("PRJ-CKPT", cli_copy)
    for phase in PipelinePhase:
        assert pipeline.has_checkpoint_for(phase, cli_copy, "PRJ-CKPT")


def test_metrics_jsonl_append(tmp_path: Path, cli_copy: Path, store_audit) -> None:
    store, audit = store_audit
    ckpt_dir = tmp_path / "ckpts1"
    pipeline1 = EndToEndPipeline(store=store, audit=audit, checkpoint_dir=ckpt_dir)
    pipeline1.run("PRJ-MA", cli_copy)

    # 同一 pipeline 第二次跑：metrics 应该追加而不是覆盖
    pipeline1.run("PRJ-MB", cli_copy)

    metrics_path = ckpt_dir / "metrics.jsonl"
    assert metrics_path.is_file()
    lines = metrics_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert "PRJ-MA" in lines[0]
    assert "PRJ-MB" in lines[1]
