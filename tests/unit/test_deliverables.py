"""P5-3 单元测试：交付物生成器。"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters.tools.python_checks import PythonCheckAdapter
from core.orchestrator.deliverables import DeliverableBundle, DeliverableGenerator


@pytest.fixture
def tmp_artifacts(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture
def ok_results() -> list[object]:
    from core.quality.checks import CheckKind

    cli = Path(__file__).resolve().parents[2] / "templates" / "python" / "cli"
    adapter = PythonCheckAdapter()
    return [
        adapter.run(kind, cli)
        for kind in (
            CheckKind.FORMAT,
            CheckKind.LINT,
            CheckKind.TYPE_CHECK,
            CheckKind.TEST,
            CheckKind.BUILD,
        )
    ]


def test_deliverable_generator_produces_five_files(
    tmp_artifacts: Path, ok_results: list[object]
) -> None:
    gen = DeliverableGenerator(output_root=tmp_artifacts)
    bundle = gen.generate(
        project_id="PRJ-OK",
        verification=ok_results,
        source_path=Path("templates/python/cli"),
    )
    assert isinstance(bundle, DeliverableBundle)
    assert bundle.project_id == "PRJ-OK"
    assert len(bundle.files) == 5
    assert bundle.all_exist is True
    names = {p.name for p in bundle.files}
    assert names == {
        "README.md",
        "test-report.md",
        "traceability.json",
        "known-limits.md",
        ".env.example",
    }


def test_readme_contains_validation_summary(tmp_artifacts: Path, ok_results: list[object]) -> None:
    gen = DeliverableGenerator(output_root=tmp_artifacts)
    bundle = gen.generate(project_id="PRJ-RM", verification=ok_results, source_path=Path("src"))
    readme = next(p for p in bundle.files if p.name == "README.md")
    text = readme.read_text(encoding="utf-8")
    assert "PRJ-RM" in text
    assert "5/5" in text  # 五链全过


def test_traceability_is_valid_json(tmp_artifacts: Path, ok_results: list[object]) -> None:
    import json

    gen = DeliverableGenerator(output_root=tmp_artifacts)
    bundle = gen.generate(project_id="PRJ-MX", verification=ok_results, source_path=Path("src"))
    mx = next(p for p in bundle.files if p.name == "traceability.json")
    data = json.loads(mx.read_text(encoding="utf-8"))
    assert data["project_id"] == "PRJ-MX"
    assert len(data["matrix_rows"]) == 5


def test_known_lists_mentions_unverified(tmp_artifacts: Path) -> None:
    """注入一条 UNVERIFIED 检查，known-limits 必须列明原因。"""
    from core.quality.checks import CheckKind, CheckResult, TestStatus

    result = CheckResult(
        kind=CheckKind.BUILD,
        tool="build",
        status=TestStatus.UNVERIFIED,
        command="python -m build",
        unverified_reason="环境未安装 build 模块",
    )
    gen = DeliverableGenerator(output_root=tmp_artifacts)
    bundle = gen.generate(project_id="PRJ-UV", verification=[result], source_path=Path("src"))
    limits = next(p for p in bundle.files if p.name == "known-limits.md")
    text = limits.read_text(encoding="utf-8")
    assert "未验证" in text


def test_env_example_has_model_vars(tmp_artifacts: Path) -> None:
    gen = DeliverableGenerator(output_root=tmp_artifacts)
    bundle = gen.generate(project_id="PRJ-ENV", verification=[], source_path=Path("src"))
    env = next(p for p in bundle.files if p.name == ".env.example")
    text = env.read_text(encoding="utf-8")
    assert "MODEL_BASE_URL" in text
    assert "MODEL_API_KEY" in text
    assert "不入库" in text
