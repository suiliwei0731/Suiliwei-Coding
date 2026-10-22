"""P4-6 单元测试：知识库分层过滤、关键词检索与可信等级传递。

覆盖：默认登记表加载、来源/版本/可信等级随结果返回、分层与可信过滤、
DEPRECATED 默认排除（显式请求才返回）、加权排序、登记表校验。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from core.knowledge import (
    KnowledgeIndex,
    KnowledgeLayer,
    KnowledgeRegistryError,
    TrustLevel,
)


def _entry(entry_id: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": entry_id,
        "title": "示例知识",
        "path": "knowledge/example.md",
        "layer": "PLATFORM",
        "trust": "VERIFIED",
        "version": "1",
        "tags": ["示例"],
        "summary": "示例摘要",
    }
    base.update(overrides)
    return base


def _index(tmp_path: Path, entries: list[dict[str, Any]]) -> KnowledgeIndex:
    path = tmp_path / "registry.yaml"
    path.write_text(
        yaml.safe_dump(
            {"schema_version": 1, "entries": entries}, allow_unicode=True, sort_keys=False
        ),
        encoding="utf-8",
    )
    return KnowledgeIndex.from_file(path)


def test_default_registry_loads() -> None:
    index = KnowledgeIndex.from_file()
    assert len(index.list()) == 4
    ids = {e.id for e in index.list()}
    assert ids == {
        "platform-rules-core",
        "platform-agents-conventions",
        "project-context",
        "python-toolchain",
    }


def test_search_returns_source_version_trust() -> None:
    index = KnowledgeIndex.from_file()
    hits = index.search("门禁")
    assert hits
    top = hits[0]
    assert top.entry.id == "platform-rules-core"
    assert top.entry.path == "knowledge/standards/rules-core.yaml"
    assert top.entry.version == "1"
    assert top.trust is TrustLevel.VERIFIED
    assert "门禁" in top.matched_terms
    assert top.snippet


def test_search_mixed_chinese_and_ascii() -> None:
    index = KnowledgeIndex.from_file()
    hits = index.search("python 类型检查")
    assert hits
    assert hits[0].entry.id == "python-toolchain"


def test_empty_query_returns_nothing() -> None:
    assert KnowledgeIndex.from_file().search("   ") == ()


def test_unknown_term_returns_nothing() -> None:
    assert KnowledgeIndex.from_file().search("nonexistent-term-xyz") == ()


def test_layer_filter(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _entry("platform-doc", tags=["部署"], layer="PLATFORM"),
            _entry("project-doc", tags=["部署"], layer="PROJECT"),
            _entry("domain-doc", tags=["部署"], layer="DOMAIN"),
        ],
    )
    hits = index.search("部署", layers=[KnowledgeLayer.PROJECT])
    assert [h.entry.id for h in hits] == ["project-doc"]


def test_trust_filter(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _entry("verified-doc", trust="VERIFIED"),
            _entry("unverified-doc", trust="UNVERIFIED"),
        ],
    )
    hits = index.search("示例", trust_levels=[TrustLevel.VERIFIED])
    assert [h.entry.id for h in hits] == ["verified-doc"]


def test_deprecated_excluded_by_default(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _entry("current-doc", trust="VERIFIED"),
            _entry("legacy-doc", trust="DEPRECATED"),
        ],
    )
    default_hits = index.search("示例")
    assert {h.entry.id for h in default_hits} == {"current-doc"}

    included = index.search("示例", include_deprecated=True)
    assert {h.entry.id for h in included} == {"current-doc", "legacy-doc"}

    explicit = index.search("示例", trust_levels=[TrustLevel.DEPRECATED])
    assert [h.entry.id for h in explicit] == ["legacy-doc"]


def test_tag_match_ranks_above_summary_match(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _entry("summary-only", tags=["其他"], title="其他", summary="包含检索词的摘要"),
            _entry("tag-match", tags=["检索词"], title="其他", summary="其他摘要"),
        ],
    )
    hits = index.search("检索词")
    assert [h.entry.id for h in hits] == ["tag-match", "summary-only"]
    assert hits[0].score > hits[1].score


def test_limit(tmp_path: Path) -> None:
    index = _index(tmp_path, [_entry(f"doc-{i}") for i in range(5)])
    assert len(index.search("示例", limit=2)) == 2


def test_verified_ranks_above_unverified_on_tie(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _entry("unverified-doc", trust="UNVERIFIED"),
            _entry("verified-doc", trust="VERIFIED"),
        ],
    )
    hits = index.search("示例")
    assert [h.entry.id for h in hits] == ["verified-doc", "unverified-doc"]


def test_invalid_registry_fields(tmp_path: Path) -> None:
    index_entries = [
        _entry("bad", title=""),
        _entry("bad-layer", layer="SYSTEM"),
    ]
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "entries": index_entries}, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(KnowledgeRegistryError) as exc_info:
        KnowledgeIndex.from_file(path)
    text = str(exc_info.value)
    assert "缺少 title" in text
    assert "非法知识分层" in text


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeRegistryError, match="ID 重复"):
        _index(tmp_path, [_entry("dup"), _entry("dup")])
