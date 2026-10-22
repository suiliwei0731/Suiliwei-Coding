"""模拟模型适配器（Phase 1–2，ADR-002、config/models.yaml simulation 节）。

脚本化响应：按调用序号依次消费脚本步；支持失败注入
（timeout/network_error/malformed_json/rate_limit）。
模拟模式不得声称为真实多模型并行（§1.9）；真实模型须显式配置 profile（Phase 4）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from adapters.models.base import (
    CompletionRequest,
    CompletionResult,
    MalformedOutput,
    ModelError,
    ModelTimeout,
    NetworkError,
    RateLimited,
)
from core.domain.entities import UsageRecord, new_id
from core.domain.validation import SchemaRegistry, SchemaValidationError

FAILURE_TYPES = frozenset({"timeout", "network_error", "malformed_json", "rate_limit"})

_FAILURE_ERRORS: dict[str, type[ModelError]] = {
    "timeout": ModelTimeout,
    "network_error": NetworkError,
    "rate_limit": RateLimited,
}


@dataclass(frozen=True, kw_only=True)
class ScriptStep:
    """脚本步：正常响应内容，或注入一种失败。"""

    content: str = ""
    failure: str | None = None

    def __post_init__(self) -> None:
        if self.failure is not None and self.failure not in FAILURE_TYPES:
            raise ValueError(f"未知失败注入类型: {self.failure!r}（可选 {sorted(FAILURE_TYPES)}）")


class SimulatedAdapter:
    """脚本化模拟适配器：可复现、可注入失败，供工作流错误处理验证。"""

    def __init__(
        self,
        script: list[ScriptStep | dict[str, str] | str],
        *,
        registry: SchemaRegistry | None = None,
        profile: str = "simulated",
    ) -> None:
        self._steps = [self._coerce(s) for s in script]
        self._registry = registry
        self._profile = profile
        self._cursor = 0
        self._cancelled = False
        self.usage_records: list[UsageRecord] = []
        self.errors: list[ModelError] = []  # 发生的错误（含注入）供审计记录

    def complete(self, request: CompletionRequest) -> CompletionResult:
        if self._cancelled:
            raise ModelError("适配器已取消")
        if self._cursor >= len(self._steps):
            raise ModelError("脚本耗尽：调用次数超过脚本步数")
        step = self._steps[self._cursor]
        self._cursor += 1

        usage = UsageRecord(
            id=new_id("USAGE"),
            model_profile=self._profile,
            prompt_tokens=len(request.prompt),  # 模拟计量：字符数代替 token
            completion_tokens=len(step.content),
            cost_usd=None,  # 模拟模式成本不可得，不伪造（红线 1）
            task_id=request.task_id,
        )
        self.usage_records.append(usage)

        if step.failure is not None:
            err_cls = _FAILURE_ERRORS.get(step.failure, MalformedOutput)
            error = err_cls(f"注入失败: {step.failure}")
            self.errors.append(error)
            raise error

        parsed: dict[str, object] | None = None
        if request.schema is not None:
            parsed = self._parse_structured(request.schema, step.content)
        return CompletionResult(content=step.content, parsed=parsed, usage=usage)

    def cancel(self) -> None:
        self._cancelled = True

    def _parse_structured(self, schema: str, content: str) -> dict[str, object]:
        """结构化输出：JSON 解析 + SchemaRegistry 校验，失败抛 MalformedOutput。"""
        if self._registry is None:
            raise ModelError("请求结构化输出但未提供 SchemaRegistry")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            error = MalformedOutput(f"输出非合法 JSON: {exc.msg}")
            self.errors.append(error)
            raise error from exc
        if not isinstance(data, dict):
            error = MalformedOutput("结构化输出须为 JSON 对象")
            self.errors.append(error)
            raise error
        try:
            self._registry.validate(schema, data)
        except SchemaValidationError as exc:
            error = MalformedOutput(f"输出不满足协议 {schema!r}: {exc}")
            self.errors.append(error)
            raise error from exc
        return data

    @staticmethod
    def _coerce(step: ScriptStep | dict[str, str] | str) -> ScriptStep:
        if isinstance(step, ScriptStep):
            return step
        if isinstance(step, str):
            return ScriptStep(content=step)
        return ScriptStep(**step)  # type: ignore[arg-type]
