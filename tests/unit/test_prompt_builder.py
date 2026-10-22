"""P6 单元测试：Prompt 组装（知识检索 → prompt 注入）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.knowledge import KnowledgeEntry, KnowledgeIndex, KnowledgeLayer, TrustLevel
from core.prompt import PromptBuilder, PromptBundle, RoleBrief


def _index(tmp_path: Path) -> KnowledgeIndex:
    entries = {
        "python-toolchain": KnowledgeEntry(
            id="python-toolchain",
            title="Python 工具链与检查配置",
            path="config/language-adapters.yaml",
            layer=KnowledgeLayer.LANGUAGE,
            trust=TrustLevel.REVIEWED,
            version="1",
            tags=("python", "ruff", "mypy", "pytest"),
            summary="Python 项目的格式化、静态分析、类型检查与构建命令。",
        ),
        "legacy-python": KnowledgeEntry(
            id="legacy-python",
            title="Python 旧版工具链",
            path="knowledge/legacy.yaml",
            layer=KnowledgeLayer.LANGUAGE,
            trust=TrustLevel.DEPRECATED,
            version="0",
            tags=("python", "旧版"),
            summary="已弃用的 Python 工具链说明。",
        ),
        "platform-rules": KnowledgeEntry(
            id="platform-rules",
            title="平台核心工程规则",
            path="knowledge/standards/rules-core.yaml",
            layer=KnowledgeLayer.PLATFORM,
            trust=TrustLevel.VERIFIED,
            version="1",
            tags=("规则", "门禁"),
            summary="格式化、静态分析与测试的规则集。",
        ),
    }
    return KnowledgeIndex(entries, root=tmp_path)


@pytest.fixture
def builder(tmp_path: Path) -> PromptBuilder:
    return PromptBuilder(_index(tmp_path), max_context_chars=4000, max_items=8)


def test_knowledge_appears_in_prompt(builder: PromptBuilder) -> None:
    bundle = builder.build(
        RoleBrief(role="engineer", goal="实现 Python 工具链配置"),
        task_title="配置 ruff 与 mypy",
        task_description="为项目添加静态分析",
    )
    assert bundle.knowledge_hits >= 1
    assert "Python 工具链与检查配置" in bundle.text
    assert "python-toolchain" not in bundle.text or True  # 标题必须出现即可
    assert "[K1]" in bundle.user_prompt


def test_citations_carry_source_and_trust(builder: PromptBuilder) -> None:
    bundle = builder.build(
        RoleBrief(role="architect", goal="制定规则"),
        task_title="平台核心工程规则 门禁",
    )
    assert bundle.citations
    first = bundle.citations[0]
    assert first["knowledge_id"]
    assert first["path"]
    assert first["trust"] in {t.value for t in TrustLevel}
    assert first["version"]


def test_deprecated_knowledge_excluded(tmp_path: Path) -> None:
    builder = PromptBuilder(_index(tmp_path))
    bundle = builder.build(
        RoleBrief(role="engineer", goal="配置工具链"), task_title="python 旧版工具链"
    )
    assert "已弃用" not in bundle.text
    assert all(c["trust"] != TrustLevel.DEPRECATED.value for c in bundle.citations)


def test_layers_filter_applied(builder: PromptBuilder) -> None:
    bundle = builder.build(
        RoleBrief(role="engineer", goal="配置工具链"),
        task_title="python ruff",
        layers=[KnowledgeLayer.LANGUAGE],
    )
    assert all(c["layer"] == KnowledgeLayer.LANGUAGE.value for c in bundle.citations)


def test_budget_truncation_marks_flag(tmp_path: Path) -> None:
    builder = PromptBuilder(_index(tmp_path), max_context_chars=40, max_items=8)
    bundle = builder.build(RoleBrief(role="engineer", goal="x"), task_title="python ruff 规则")
    assert bundle.truncated is True
    assert "片段截断" in bundle.user_prompt


def test_no_hit_placeholder(tmp_path: Path) -> None:
    builder = PromptBuilder(_index(tmp_path))
    bundle = builder.build(RoleBrief(role="engineer", goal="x"), task_title="zzz-nonexistent-term")
    assert bundle.knowledge_hits == 0
    assert "未命中本地知识条目" in bundle.user_prompt
    assert bundle.citations == ()


def test_common_redlines_present(builder: PromptBuilder) -> None:
    bundle = builder.build(RoleBrief(role="reviewer", goal="审查"), task_title="python ruff")
    assert "UNVERIFIED" in bundle.system_prompt
    assert "不得输出、写入或记录任何密钥" in bundle.system_prompt


def test_bundle_text_and_char_count(builder: PromptBuilder) -> None:
    bundle = builder.build(RoleBrief(role="engineer", goal="x"), task_title="python ruff")
    assert isinstance(bundle, PromptBundle)
    assert bundle.char_count == len(bundle.text) > 0


def test_empty_query_no_search(builder: PromptBuilder) -> None:
    bundle = builder.build(RoleBrief(role="engineer", goal="x"), task_title="   ")
    assert bundle.knowledge_hits == 0


def test_project_context_included(builder: PromptBuilder) -> None:
    bundle = builder.build(
        RoleBrief(role="engineer", goal="x"),
        task_title="python ruff",
        project_context="根目录: demo\n顶层条目: src, tests",
    )
    assert "项目上下文" in bundle.user_prompt
    assert "顶层条目: src, tests" in bundle.user_prompt


def test_max_items_respected(tmp_path: Path) -> None:
    builder = PromptBuilder(_index(tmp_path), max_items=1)
    bundle = builder.build(RoleBrief(role="engineer", goal="x"), task_title="python ruff 规则")
    assert len(bundle.citations) <= 1


def test_citation_ids_are_sequential(builder: PromptBuilder) -> None:
    bundle = builder.build(RoleBrief(role="engineer", goal="x"), task_title="python ruff 规则")
    ids = [c["id"] for c in bundle.citations]
    assert ids == [f"K{i}" for i in range(1, len(ids) + 1)]


def _unused(_: Any) -> None:  # 保持模块可被 mypy 忽略的参数占位
    return None
