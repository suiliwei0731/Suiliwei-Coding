"""工作流核心：任务状态机（规范 §9）与状态转移证据（§7.4）。"""

from core.workflow.evidence import Evidence, EvidenceError
from core.workflow.machine import (
    TERMINAL_STATES,
    TRANSITIONS,
    ConcurrencyError,
    GuardRejectionError,
    IllegalTransitionError,
    TaskStateMachine,
    TransitionContext,
    TransitionResult,
    WorkflowError,
)

__all__ = [
    "TERMINAL_STATES",
    "TRANSITIONS",
    "ConcurrencyError",
    "Evidence",
    "EvidenceError",
    "GuardRejectionError",
    "IllegalTransitionError",
    "TaskStateMachine",
    "TransitionContext",
    "TransitionResult",
    "WorkflowError",
]
