"""Prompt 组装：把本地知识检索结果注入角色请求（Phase 6 接线）。

职责边界：
- 只负责「检索 → 结构化上下文 → prompt 文本」，不发起模型调用，也不依赖 adapters.*，
  以免与 ``core.audit`` / ``adapters.models`` 形成循环导入；
- 知识默认排除 DEPRECATED（P4-6 语义），引用必须携带来源、版本、可信等级，
  使模型输出可回溯到知识条目；
- 超出字符预算时按得分顺序截断，并显式标记 ``truncated``，不静默丢弃。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from core.knowledge import KnowledgeHit, KnowledgeIndex, KnowledgeLayer, TrustLevel

DEFAULT_MAX_CONTEXT_CHARS = 6000
DEFAULT_MAX_ITEMS = 8
SNIPPET_MARK = "…（片段截断）"


@dataclass(frozen=True, kw_only=True)
class RoleBrief:
    """角色职责说明（规范 §2：角色不绑定模型）。"""

    role: str
    goal: str
    constraints: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class PromptBundle:
    """一次角色请求的完整 prompt 与引用清单。"""

    role: str
    system_prompt: str
    user_prompt: str
    citations: tuple[dict[str, Any], ...] = ()
    truncated: bool = False
    knowledge_hits: int = 0

    @property
    def text(self) -> str:
        """合并后的完整文本（便于测试与审计摘要）。"""
        return f"{self.system_prompt}\n\n{self.user_prompt}"

    @property
    def char_count(self) -> int:
        return len(self.text)


_COMMON_CONSTRAINTS = (
    "不得伪造验证结果：未实际执行的检查一律标注 UNVERIFIED，不得写成通过。",
    "不得输出、写入或记录任何密钥、令牌与凭据。",
    "只依据提供的任务上下文与知识摘录作答；知识不足时明确说明缺口，不得编造来源。",
)


class PromptBuilder:
    """从本地知识库检索并组装角色 prompt。"""

    def __init__(
        self,
        knowledge: KnowledgeIndex,
        *,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        max_items: int = DEFAULT_MAX_ITEMS,
    ) -> None:
        self._knowledge = knowledge
        self._max_context_chars = max(max_context_chars, 0)
        self._max_items = max(max_items, 0)

    def build(
        self,
        brief: RoleBrief,
        *,
        task_title: str,
        task_description: str = "",
        project_context: str = "",
        layers: Sequence[KnowledgeLayer] | None = None,
        trust_levels: Sequence[TrustLevel] | None = None,
    ) -> PromptBundle:
        """检索知识并组装 system/user 两段 prompt。"""
        query = " ".join(part for part in (task_title, task_description) if part.strip())
        hits = (
            self._knowledge.search(
                query,
                layers=layers,
                trust_levels=trust_levels,
                include_deprecated=False,
                limit=self._max_items,
            )
            if query.strip()
            else ()
        )

        system_prompt = self._render_system(brief)
        knowledge_block, citations, truncated = self._render_knowledge(hits)
        user_prompt = self._render_user(
            task_title=task_title,
            task_description=task_description,
            project_context=project_context,
            knowledge_block=knowledge_block,
            hit_count=len(hits),
        )
        return PromptBundle(
            role=brief.role,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            citations=citations,
            truncated=truncated,
            knowledge_hits=len(hits),
        )

    # ---- 渲染 ----

    @staticmethod
    def _render_system(brief: RoleBrief) -> str:
        lines = [f"你是本研发平台中的「{brief.role}」角色。", f"目标：{brief.goal}", "", "约束："]
        lines.extend(f"- {c}" for c in (*brief.constraints, *_COMMON_CONSTRAINTS))
        lines.extend(
            [
                "",
                "输出要求：",
                "- 结论先行，给出可执行的步骤或任务清单；",
                "- 引用知识时标注编号（如 [K1]），便于回溯来源。",
            ]
        )
        return "\n".join(lines)

    def _render_knowledge(
        self, hits: Sequence[KnowledgeHit]
    ) -> tuple[str, tuple[dict[str, Any], ...], bool]:
        """按预算拼接知识块；超出预算时截断片段并标记。"""
        if not hits:
            return "（本次检索未命中本地知识条目）", (), False

        blocks: list[str] = []
        citations: list[dict[str, Any]] = []
        used = 0
        truncated = False
        for index, hit in enumerate(hits, start=1):
            entry = hit.entry
            header = (
                f"[K{index}] {entry.title} | 层: {entry.layer.value} | "
                f"可信: {entry.trust.value} | 版本: {entry.version} | 来源: {entry.path}"
            )
            body = f"片段: {hit.snippet}".rstrip()
            block = f"{header}\n{body}"
            remaining = self._max_context_chars - used
            if remaining <= 0:
                truncated = True
                break
            if len(block) > remaining:
                # 只截断片段正文，保留可回溯的来源头；空间不足时也显式留下截断标记
                keep = max(remaining - len(header) - len(SNIPPET_MARK) - 1, 0)
                body = f"{hit.snippet[:keep]}{SNIPPET_MARK}" if keep else SNIPPET_MARK
                block = f"{header}\n{body}"
                truncated = True
            blocks.append(block)
            used += len(block)
            citations.append(
                {
                    "id": f"K{index}",
                    "knowledge_id": entry.id,
                    "title": entry.title,
                    "layer": entry.layer.value,
                    "trust": entry.trust.value,
                    "version": entry.version,
                    "path": entry.path,
                    "score": hit.score,
                }
            )
            if truncated:
                break

        return "\n\n".join(blocks), tuple(citations), truncated

    @staticmethod
    def _render_user(
        *,
        task_title: str,
        task_description: str,
        project_context: str,
        knowledge_block: str,
        hit_count: int,
    ) -> str:
        sections = ["## 任务", f"{task_title}"]
        if task_description.strip():
            sections.append(task_description.strip())
        if project_context.strip():
            sections.extend(["", "## 项目上下文", project_context.strip()])
        note = f"（命中 {hit_count} 条，已排除 DEPRECATED）" if hit_count else ""
        sections.extend(["", f"## 知识摘录{note}", knowledge_block])
        return "\n".join(sections)


__all__ = ["PromptBundle", "PromptBuilder", "RoleBrief"]
