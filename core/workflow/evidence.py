"""状态转移证据（规范 §2、§7.4、§9.2）。

状态转移只由控制器依据证据触发，不接受 AI 自报状态。本模块定义证据值对象
及其完整性校验：任何字段缺失或不合规的证据不得用于驱动状态机。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.domain.entities import utc_now
from core.domain.enums import EvidenceKind


class EvidenceError(ValueError):
    """证据缺失或不合规（字段不全、失败证据误用等）。"""


# 必须携带实体/产物引用（refs）的证据类型
REF_REQUIRED_KINDS: frozenset[EvidenceKind] = frozenset(
    {
        EvidenceKind.TEST_REPORT,
        EvidenceKind.REVIEW_RESULT,
        EvidenceKind.ARTIFACT,
        EvidenceKind.APPROVAL,
    }
)


@dataclass(frozen=True, kw_only=True)
class Evidence:
    """一次状态转移所依据的客观记录。

    字段对齐 §7.4 门禁证据要求（命令、工具版本、退出码、摘要、产物位置）：

    - ``kind=COMMAND_RESULT`` 时 ``command`` 与 ``exit_code`` 必填；
    - 引用类证据（测试报告/审查结论/产物/审批）至少一条 ``refs``
      （report_id、review_id、artifact 路径、approval_id 等）；
    - ``exit_code`` 非零即失败证据，仅允许用于失败向转移
      （FAILED/REWORK/BLOCKED 等），用于成功向转移一律拒绝。
    """

    kind: EvidenceKind
    summary: str
    command: str = ""
    exit_code: int | None = None
    refs: tuple[str, ...] = ()
    actor: str = "controller"  # 证据来源（控制器/工具/角色），默认系统控制器
    tool_version: str = ""
    created_at: str = field(default_factory=utc_now)

    @property
    def is_failing(self) -> bool:
        """退出码非零即失败证据。"""
        return self.exit_code is not None and self.exit_code != 0

    def validate(self) -> None:
        """校验证据完整性；不合规抛 :class:`EvidenceError`。"""
        if not self.summary.strip():
            raise EvidenceError("证据缺少 summary（§7.4 要求可复查摘要）")
        if not self.actor.strip():
            raise EvidenceError("证据缺少 actor（§18 审计要求记录角色/来源）")
        if self.kind is EvidenceKind.COMMAND_RESULT:
            if not self.command.strip():
                raise EvidenceError("COMMAND_RESULT 证据缺少 command")
            if self.exit_code is None:
                raise EvidenceError("COMMAND_RESULT 证据缺少 exit_code")
        if self.kind in REF_REQUIRED_KINDS and not self.refs:
            raise EvidenceError(f"{self.kind.value} 证据至少需一条实体/产物引用（refs）")
