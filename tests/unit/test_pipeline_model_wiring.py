"""P6 单元测试：流水线模型接线（知识检索 → prompt → 模型调用）。"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from adapters.languages.python.adapter import _TEMPLATES_ROOT
from adapters.models import (
    CompletionRequest,
    CompletionResult,
    ModelError,
)
from core.audit.logger import AuditLogger
from core.domain.entities import UsageRecord, new_id
from core.knowledge import KnowledgeEntry, KnowledgeIndex, KnowledgeLayer, TrustLevel
from core.orchestrator.pipeline import EndToEndPipeline
from core.orchestrator.store import StateStore


class CaptureAdapter:
    """捕获请求的假适配器：按调用返回固定文本。"""

    def __init__(self, response: str = "计划：先补测试，再改实现。") -> None:
        self.requests: list[CompletionRequest] = []
        self._response = response

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)
        return CompletionResult(
            content=self._response,
            parsed=None,
            usage=UsageRecord(
                id=new_id("USAGE"),
                model_profile="capture",
                prompt_tokens=len(request.prompt),
                completion_tokens=len(self._response),
                cost_usd=None,
                task_id=request.task_id,
            ),
        )

    def cancel(self) -> None:
        return


class FailingAdapter:
    """模拟模型调用失败，用于验证不伪造成功。"""

    def __init__(self, message: str = "上游 500") -> None:
        self._message = message
        self.calls = 0

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.calls += 1
        raise ModelError(self._message)

    def cancel(self) -> None:
        return


@pytest.fixture
def store_audit(tmp_path: Path) -> Iterator[tuple[StateStore, AuditLogger]]:
    store = StateStore(tmp_path / "state.db")
    audit = AuditLogger(store, log_dir=tmp_path / "logs")
    yield store, audit
    store.close()


@pytest.fixture
def cli_copy(tmp_path: Path) -> Path:
    src = _TEMPLATES_ROOT / "cli"
    dst = tmp_path / "cli-proj"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "runtime"))
    # 用一个能命中知识条目的描述
    pyproject = dst / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(
        text.replace(
            'description = "CLI 模板项目：配置文件校验器"',
            'description = "CLI 模板项目：python ruff mypy pytest 工具链校验器"',
        ),
        encoding="utf-8",
    )
    return dst


@pytest.fixture
def knowledge(tmp_path: Path) -> KnowledgeIndex:
    return KnowledgeIndex(
        {
            "python-toolchain": KnowledgeEntry(
                id="python-toolchain",
                title="Python 工具链与检查配置",
                path="config/language-adapters.yaml",
                layer=KnowledgeLayer.LANGUAGE,
                trust=TrustLevel.VERIFIED,
                version="1",
                tags=("python", "ruff", "mypy", "pytest", "工具链"),
                summary="Python 格式化、静态分析、类型检查与构建命令。",
            ),
            "legacy": KnowledgeEntry(
                id="legacy",
                title="Python 旧版工具链",
                path="knowledge/legacy.yaml",
                layer=KnowledgeLayer.LANGUAGE,
                trust=TrustLevel.DEPRECATED,
                version="0",
                tags=("python", "旧版"),
                summary="已弃用。",
            ),
        },
        root=tmp_path,
    )


def _pipeline(store_audit, knowledge, tmp_path: Path, **kwargs: Any) -> EndToEndPipeline:
    store, audit = store_audit
    return EndToEndPipeline(
        store=store,
        audit=audit,
        knowledge=knowledge,
        checkpoint_dir=tmp_path / "ckpts",
        **kwargs,
    )


def test_prompt_injected_into_model_request(
    store_audit, knowledge: KnowledgeIndex, cli_copy: Path, tmp_path: Path
) -> None:
    """知识检索结果必须真实出现在发给模型的 prompt 中。"""
    adapter = CaptureAdapter()
    pipeline = _pipeline(store_audit, knowledge, tmp_path, model=adapter)
    pipeline.run("PRJ-PROMPT", cli_copy)

    assert len(adapter.requests) == 2  # SPEC + CODE
    for request in adapter.requests:
        assert "## 知识摘录" in request.prompt
        assert "## 项目上下文" in request.prompt
        assert "cli-proj" in request.prompt
    # 至少一次命中该知识条目（标题/来源可回溯）
    assert any("Python 工具链与检查配置" in r.prompt for r in adapter.requests)
    assert all("已弃用" not in r.prompt for r in adapter.requests)


def test_pipeline_records_citations_and_mode(
    store_audit, knowledge: KnowledgeIndex, cli_copy: Path, tmp_path: Path
) -> None:
    adapter = CaptureAdapter()
    pipeline = _pipeline(store_audit, knowledge, tmp_path, model=adapter)
    result = pipeline.run("PRJ-CITE", cli_copy)

    assert result.model_mode == "injected"
    assert result.model_unverified_reason is None
    assert result.model_calls == 2
    assert "python-toolchain" in result.citations
    spec = next(p for p in result.phases if p.get("phase") == "spec")
    assert spec["status"] == "OK"
    assert spec["knowledge_hits"] >= 1
    assert spec["citations"]


def test_model_failure_marks_unverified_not_success(
    store_audit, knowledge: KnowledgeIndex, cli_copy: Path, tmp_path: Path
) -> None:
    adapter = FailingAdapter("上游 500")
    pipeline = _pipeline(store_audit, knowledge, tmp_path, model=adapter)
    result = pipeline.run("PRJ-FAIL", cli_copy)

    spec = next(p for p in result.phases if p.get("phase") == "spec")
    code = next(p for p in result.phases if p.get("phase") == "code")
    assert spec["status"] == "UNVERIFIED"
    assert "上游 500" in spec["unverified_reason"]
    assert code["status"] == "UNVERIFIED"
    assert adapter.calls == 2
    # 五链验证仍然真实执行，不因模型失败而伪造
    assert result.verification
    assert result.summary()["model_calls"] == 2


def test_fallback_to_simulated_when_profile_disabled(
    store_audit, knowledge: KnowledgeIndex, cli_copy: Path, tmp_path: Path
) -> None:
    pipeline = _pipeline(store_audit, knowledge, tmp_path)
    result = pipeline.run("PRJ-SIM", cli_copy)

    assert result.model_mode == "simulated"
    assert result.model_unverified_reason is not None
    assert "enabled=false" in result.model_unverified_reason
    assert result.summary()["model_unverified"] is True
    assert result.ok is True  # 验证链真实通过


def test_env_enable_without_credentials_falls_back_honestly(
    store_audit,
    knowledge: KnowledgeIndex,
    cli_copy: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """仅设置启用开关但缺 base_url/key/model 时，必须明确回退并标注。"""
    monkeypatch.setenv("AI_DEV_PLATFORM_MODEL_ENABLED", "1")
    for name in (
        "AI_DEV_PLATFORM_MODEL_BASE_URL",
        "AI_DEV_PLATFORM_MODEL_API_KEY",
        "AI_DEV_PLATFORM_MODEL_NAME",
    ):
        monkeypatch.delenv(name, raising=False)

    pipeline = _pipeline(store_audit, knowledge, tmp_path)
    result = pipeline.run("PRJ-ENV", cli_copy)

    assert result.model_mode == "simulated"
    assert "真实模型不可用" in (result.model_unverified_reason or "")
    assert result.summary()["model_unverified"] is True


def test_simulated_output_is_project_aware(
    store_audit, knowledge: KnowledgeIndex, cli_copy: Path, tmp_path: Path
) -> None:
    """模拟 fallback 必须按当前项目生成，不得出现其他项目的硬编码 ID/名称。"""
    pipeline = _pipeline(store_audit, knowledge, tmp_path)
    result = pipeline.run("PRJ-SCOPE", cli_copy)

    assert result.model_mode == "simulated"
    spec = next(p for p in result.phases if p.get("phase") == "spec")
    code = next(p for p in result.phases if p.get("phase") == "code")
    assert spec["status"] == "OK"
    assert code["status"] == "OK"
    # 项目名取自 pyproject.toml（cli-validator），且不含历史硬编码项目
    for entry in (spec, code):
        assert "cli-validator" in entry["excerpt"]
        assert "PRJ-PIPELINE-DEMO" not in entry["excerpt"]
    assert "PRJ-SCOPE" in code["excerpt"]


def test_code_stage_records_no_source_writes(
    store_audit, knowledge: KnowledgeIndex, cli_copy: Path, tmp_path: Path
) -> None:
    adapter = CaptureAdapter()
    pipeline = _pipeline(store_audit, knowledge, tmp_path, model=adapter)
    result = pipeline.run("PRJ-NOWRITE", cli_copy)

    code = next(p for p in result.phases if p.get("phase") == "code")
    assert code["source_writes"] == 0
    assert code["graph_tasks"] == 2
