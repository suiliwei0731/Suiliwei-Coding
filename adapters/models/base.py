"""模型适配器接口（ADR-002：云端 OpenAI 兼容优先，本地预留；Phase 1–2 仅模拟实现）。

编排层按错误类型的可重试性决策；结构化输出经 SchemaRegistry 校验，
校验失败按 MalformedOutput 处理，不静默降级（ADR-002）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from core.domain.entities import UsageRecord


class ModelError(Exception):
    """模型调用错误基类。"""

    retryable: bool = False


class ModelTimeout(ModelError):
    """调用超时（§9.3：外部调用须设超时）。"""

    retryable = True


class NetworkError(ModelError):
    """网络错误。"""

    retryable = True


class MalformedOutput(ModelError):
    """输出非合法 JSON 或不满足协议 Schema。"""

    retryable = True


class RateLimited(ModelError):
    """限流。"""

    retryable = True


@dataclass(frozen=True, kw_only=True)
class CompletionRequest:
    """一次补全请求。role 为职责配置，不绑定具体模型（§2）。"""

    role: str
    prompt: str
    schema: str | None = None  # SchemaRegistry 登记的协议名；非空则要求结构化输出
    task_id: str | None = None


@dataclass(frozen=True)
class CompletionResult:
    content: str
    parsed: dict[str, Any] | None  # 结构化输出校验通过后的载荷；非结构化请求为 None
    usage: UsageRecord


class ModelAdapter(Protocol):
    """统一模型接口：补全 + 取消。"""

    def complete(self, request: CompletionRequest) -> CompletionResult: ...

    def cancel(self) -> None: ...
