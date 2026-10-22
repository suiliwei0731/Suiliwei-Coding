"""知识库基础检索（实施计划 P4-6；规范 §10、§18）。

- 分层过滤：PLATFORM → PROJECT → LANGUAGE → DOMAIN，检索可限定层；
- 关键词检索：ASCII 词 + 中文二元组，按标签/标题/摘要/ID 加权打分；
- 可信等级传递：结果携带来源（path）、版本与 trust；排序同时考虑得分与可信度；
- DEPRECATED 默认不返回（不被默认推荐），仅在显式 ``include_deprecated`` 或
  显式 trust 过滤时可见；未验证（UNVERIFIED）知识如实标注，不冒充已核验。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

DEFAULT_KNOWLEDGE_REGISTRY = Path(__file__).resolve().parents[2] / "knowledge" / "registry.yaml"
FILE_SCHEMA_VERSION = 1
SNIPPET_MAX_CHARS = 160

_ASCII_TERM = re.compile(r"[a-z0-9_]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


class KnowledgeLayer(StrEnum):
    """知识分层（由通用到具体）。"""

    PLATFORM = "PLATFORM"
    PROJECT = "PROJECT"
    LANGUAGE = "LANGUAGE"
    DOMAIN = "DOMAIN"


class TrustLevel(StrEnum):
    """可信等级（顺序即可信度）。"""

    VERIFIED = "VERIFIED"
    REVIEWED = "REVIEWED"
    UNVERIFIED = "UNVERIFIED"
    DEPRECATED = "DEPRECATED"


_TRUST_RANK: dict[TrustLevel, int] = {
    TrustLevel.VERIFIED: 4,
    TrustLevel.REVIEWED: 3,
    TrustLevel.UNVERIFIED: 2,
    TrustLevel.DEPRECATED: 1,
}


class KnowledgeRegistryError(ValueError):
    """知识登记表解析或校验失败（一次性列出全部问题）。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("知识登记表未通过校验: " + "; ".join(problems))


@dataclass(frozen=True)
class KnowledgeEntry:
    """一条知识登记（来源、分层、可信等级、版本）。"""

    id: str
    title: str
    path: str
    layer: KnowledgeLayer
    trust: TrustLevel
    version: str
    tags: tuple[str, ...] = ()
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "path": self.path,
            "layer": self.layer.value,
            "trust": self.trust.value,
            "version": self.version,
            "tags": list(self.tags),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeEntry:
        try:
            layer = KnowledgeLayer(str(data.get("layer", "")))
        except ValueError as exc:
            raise ValueError(f"非法知识分层: {data.get('layer')!r}") from exc
        try:
            trust = TrustLevel(str(data.get("trust", "")))
        except ValueError as exc:
            raise ValueError(f"非法可信等级: {data.get('trust')!r}") from exc
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            path=str(data.get("path", "")),
            layer=layer,
            trust=trust,
            version=str(data.get("version", "")),
            tags=tuple(str(t) for t in data.get("tags", ())),
            summary=str(data.get("summary", "")),
        )

    def validate(self) -> list[str]:
        problems: list[str] = []
        for key, value in (
            ("id", self.id),
            ("title", self.title),
            ("path", self.path),
            ("version", self.version),
        ):
            if not str(value).strip():
                problems.append(f"知识 {self.id or '(无 ID)'} 缺少 {key}")
        return problems


@dataclass(frozen=True)
class KnowledgeHit:
    """一条检索结果：知识条目 + 得分 + 命中词 + 摘要片段（含来源/版本/可信等级）。"""

    entry: KnowledgeEntry
    score: int
    matched_terms: tuple[str, ...]
    snippet: str = ""

    @property
    def trust(self) -> TrustLevel:
        return self.entry.trust


def _terms(query: str) -> tuple[str, ...]:
    """查询分词：ASCII 词 + 中文二元组（单字中文保留单字）。"""
    lowered = query.lower()
    terms: set[str] = set(_ASCII_TERM.findall(lowered))
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            terms.add(run)
        else:
            terms.update(run[i : i + 2] for i in range(len(run) - 1))
    return tuple(sorted(terms))


def _score(entry: KnowledgeEntry, terms: tuple[str, ...]) -> tuple[int, tuple[str, ...]]:
    """加权打分：标签 3 / 标题 2 / 摘要 1 / ID 1，同一词多次出现累计。"""
    fields = (
        (" ".join(entry.tags).lower(), 3),
        (entry.title.lower(), 2),
        (entry.summary.lower(), 1),
        (entry.id.lower(), 1),
    )
    score = 0
    matched: list[str] = []
    for term in terms:
        hit = False
        for text, weight in fields:
            count = text.count(term)
            if count:
                score += weight * count
                hit = True
        if hit:
            matched.append(term)
    return score, tuple(matched)


class KnowledgeIndex:
    """知识索引：加载登记表，提供分层/可信过滤与关键词检索。"""

    def __init__(
        self,
        entries: dict[str, KnowledgeEntry] | None = None,
        *,
        root: Path | None = None,
        path: Path | None = None,
    ) -> None:
        self._entries = entries or {}
        self._root = root or Path(__file__).resolve().parents[2]
        self._path = path

    @classmethod
    def from_file(cls, path: Path | str = DEFAULT_KNOWLEDGE_REGISTRY) -> KnowledgeIndex:
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise KnowledgeRegistryError([f"知识登记表不可读: {path}（{exc}）"]) from exc
        except yaml.YAMLError as exc:
            raise KnowledgeRegistryError([f"知识登记表 YAML 解析失败: {path}（{exc}）"]) from exc
        problems: list[str] = []
        if not isinstance(data, dict):
            raise KnowledgeRegistryError(["知识登记表内容不是映射"])
        if data.get("schema_version") != FILE_SCHEMA_VERSION:
            problems.append(
                f"schema_version 应为 {FILE_SCHEMA_VERSION}: {data.get('schema_version')!r}"
            )
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            problems.append("登记表缺少 entries 列表")
            raw_entries = []
        entries: dict[str, KnowledgeEntry] = {}
        for raw in raw_entries:
            if not isinstance(raw, dict):
                problems.append(f"知识条目不是映射: {raw!r}")
                continue
            try:
                entry = KnowledgeEntry.from_dict(raw)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            problems.extend(entry.validate())
            if entry.id in entries:
                problems.append(f"知识 ID 重复: {entry.id}")
                continue
            entries[entry.id] = entry
        if problems:
            raise KnowledgeRegistryError(problems)
        return cls(entries, root=path.parent.parent, path=path)

    def list(self) -> tuple[KnowledgeEntry, ...]:
        return tuple(self._entries[k] for k in sorted(self._entries))

    def search(
        self,
        query: str,
        *,
        layers: Sequence[KnowledgeLayer] | None = None,
        trust_levels: Sequence[TrustLevel] | None = None,
        include_deprecated: bool = False,
        limit: int = 10,
    ) -> tuple[KnowledgeHit, ...]:
        """检索知识：默认排除 DEPRECATED，返回带可信等级与来源的结果。"""
        terms = _terms(query)
        if not terms:
            return ()
        layer_filter = set(layers) if layers is not None else None
        trust_filter = set(trust_levels) if trust_levels is not None else None
        allow_deprecated = include_deprecated or (
            trust_filter is not None and TrustLevel.DEPRECATED in trust_filter
        )
        hits: list[KnowledgeHit] = []
        for entry in self.list():
            if layer_filter is not None and entry.layer not in layer_filter:
                continue
            if entry.trust is TrustLevel.DEPRECATED and not allow_deprecated:
                continue
            if trust_filter is not None and entry.trust not in trust_filter:
                continue
            score, matched = _score(entry, terms)
            if score <= 0:
                continue
            hits.append(
                KnowledgeHit(
                    entry=entry,
                    score=score,
                    matched_terms=matched,
                    snippet=self._snippet(entry, matched),
                )
            )
        hits.sort(key=lambda h: (-h.score, -_TRUST_RANK[h.entry.trust], h.entry.id))
        return tuple(hits[: max(limit, 0)])

    def _snippet(self, entry: KnowledgeEntry, terms: tuple[str, ...]) -> str:
        """优先返回来源文件中首个命中行；文件不可读时退回摘要。"""
        summary = entry.summary.strip()
        source = self._root / entry.path
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            return summary[:SNIPPET_MAX_CHARS]
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or not any(t in stripped.lower() for t in terms):
                continue
            return stripped[:SNIPPET_MAX_CHARS]
        return summary[:SNIPPET_MAX_CHARS]
