"""质量门禁层（规范 §16；实施计划 Phase 3）。

checks：五类检查的统一结果结构与适配器协议（P3-3）；
gate：门禁执行与阻断（P3-4）；waiver：豁免审批（P3-5）；report：质量报告（P3-6）。
"""

from core.quality.checks import (
    DEFAULT_TIMEOUT_SECONDS,
    CheckAdapter,
    CheckKind,
    CheckResult,
)
from core.quality.gate import (
    GATE_ACTOR,
    RULE_TO_CHECK_KIND,
    GateError,
    GateExecutor,
    GateOutcome,
    RuleFinding,
    evaluate,
)
from core.quality.waiver import (
    Waiver,
    WaiverError,
    create_waiver,
    is_waiver_valid,
)

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "GATE_ACTOR",
    "RULE_TO_CHECK_KIND",
    "CheckAdapter",
    "CheckKind",
    "CheckResult",
    "GateError",
    "GateExecutor",
    "GateOutcome",
    "RuleFinding",
    "Waiver",
    "WaiverError",
    "create_waiver",
    "evaluate",
    "is_waiver_valid",
]
