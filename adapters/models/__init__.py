"""模型适配器（ADR-002）：统一接口 + Phase 1–2 模拟实现。"""

from adapters.models.base import (
    CompletionRequest,
    CompletionResult,
    MalformedOutput,
    ModelAdapter,
    ModelError,
    ModelTimeout,
    NetworkError,
    RateLimited,
)
from adapters.models.simulated import FAILURE_TYPES, ScriptStep, SimulatedAdapter

__all__ = [
    "FAILURE_TYPES",
    "CompletionRequest",
    "CompletionResult",
    "MalformedOutput",
    "ModelAdapter",
    "ModelError",
    "ModelTimeout",
    "NetworkError",
    "RateLimited",
    "ScriptStep",
    "SimulatedAdapter",
]
