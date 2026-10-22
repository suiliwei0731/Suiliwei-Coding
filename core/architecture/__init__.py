"""架构工作流（规范 §8 阶段 3）：ADR、契约与架构方案门禁。"""

from core.architecture.adr import (
    ADR_STATUSES,
    DECISIONS_DIR,
    ADRDocument,
    ADRError,
    Alternative,
    next_adr_number,
    save_adr,
)
from core.architecture.contract import (
    CONTRACT_NAME_RE,
    Contract,
    ContractError,
    save_contract,
    validate_payload,
)
from core.architecture.plan import (
    REQUIRED_PLAN_SECTIONS,
    ArchitectureGateError,
    ArchitecturePlan,
    RiskItem,
    assert_architecture_ready,
    save_plan,
)

__all__ = [
    "ADR_STATUSES",
    "CONTRACT_NAME_RE",
    "DECISIONS_DIR",
    "REQUIRED_PLAN_SECTIONS",
    "ADRDocument",
    "ADRError",
    "Alternative",
    "ArchitectureGateError",
    "ArchitecturePlan",
    "Contract",
    "ContractError",
    "RiskItem",
    "assert_architecture_ready",
    "next_adr_number",
    "save_adr",
    "save_contract",
    "save_plan",
    "validate_payload",
]
