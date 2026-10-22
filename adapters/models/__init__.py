"""模型适配器（ADR-002）：统一接口 + 模拟实现 + OpenAI 兼容真实实现（P4-1）。"""

from adapters.models.base import (
    CompletionRequest,
    CompletionResult,
    MalformedOutput,
    ModelAdapter,
    ModelCancelled,
    ModelError,
    ModelTimeout,
    NetworkError,
    RateLimited,
)
from adapters.models.openai_compat import ModelProfile, OpenAICompatibleAdapter
from adapters.models.simulated import FAILURE_TYPES, ScriptStep, SimulatedAdapter

__all__ = [
    "FAILURE_TYPES",
    "CompletionRequest",
    "CompletionResult",
    "MalformedOutput",
    "ModelAdapter",
    "ModelCancelled",
    "ModelError",
    "ModelProfile",
    "ModelTimeout",
    "NetworkError",
    "OpenAICompatibleAdapter",
    "RateLimited",
    "ScriptStep",
    "SimulatedAdapter",
]
