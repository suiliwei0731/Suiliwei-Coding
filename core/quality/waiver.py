"""豁免审批与有效性判定（规范 §7.2.6、§16.3、§17；实施计划 P3-5）。

BLOCKER 规则失败即阻断（§16.3），唯一合法的放行途径是**有效豁免记录**：

- 创建即校验（:func:`create_waiver`）：理由与适用范围非空；规则须允许豁免；
  审批角色须在规则授权列表内；**审批人不得与申请人相同**（§17 职责分离）；
  有效期不得超过规则 ``max_validity_days`` 上限。
- 有效性（:func:`is_waiver_valid`）：未过期且范围覆盖。范围覆盖定义：
  ``scope`` 含 ``"*"``（全局）、任务 ID 或项目 ID 之一即覆盖该任务。
- 门禁侧（core/quality/gate.py）：BLOCKER 失败 + 有效豁免 → finding 标记
  waived 放行；无豁免记录或豁免过期 → 仍阻断（红线：无豁免记录的 BLOCKER
  不可跳过）。每次豁免放行写审计（action=waiver.applied）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from core.domain.entities import Task, new_id, utc_now
from core.standards.rules import Rule


class WaiverError(ValueError):
    """豁免记录创建/校验失败（§17）；problems 一次性列出全部问题。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("豁免记录未通过校验（§17）: " + "; ".join(problems))


def parse_iso_z(value: str) -> datetime:
    """解析 ISO 8601（Z 后缀）时间戳为 aware datetime。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_iso_z(value: datetime) -> str:
    """格式化为 ISO 8601 UTC 时间戳（毫秒精度，Z 后缀；同 utc_now 约定）。"""
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, kw_only=True)
class Waiver:
    """单条豁免记录（§7.2.6：理由/范围/审批人/有效期）。"""

    rule_id: str
    reason: str  # 豁免理由（为什么可以豁免）
    scope: tuple[str, ...]  # 适用范围："*" / 项目 ID / 任务 ID
    approver_role: str  # 审批角色（须在规则 waiver.approver_roles 内）
    requested_by: str  # 申请人
    approved_by: str  # 审批人（§17：不得与申请人相同）
    created_at: str
    expires_at: str
    id: str = field(default_factory=lambda: new_id("WVR"))


def create_waiver(
    rule: Rule,
    *,
    reason: str,
    scope: Sequence[str],
    approver_role: str,
    requested_by: str,
    approved_by: str,
    expires_at: str,
    created_at: str | None = None,
) -> Waiver:
    """按规则豁免策略创建豁免记录；任一校验失败抛 :class:`WaiverError`（含全部问题）。"""
    created = created_at if created_at is not None else utc_now()
    problems: list[str] = []
    if not reason.strip():
        problems.append("豁免理由不能为空")
    scope_tuple = tuple(s.strip() for s in scope)
    if not scope_tuple or any(not s for s in scope_tuple):
        problems.append('豁免适用范围不能为空（"*" / 项目 ID / 任务 ID）')
    if not rule.waiver.allowed:
        problems.append(f"规则 {rule.id} 不允许豁免（§7.2.6 waiver.allowed=false）")
    if approver_role not in rule.waiver.approver_roles:
        problems.append(
            f"审批角色 {approver_role!r} 不在规则 {rule.id} 授权的审批角色 "
            f"{list(rule.waiver.approver_roles)} 内"
        )
    if approved_by == requested_by:
        problems.append(f"审批人 {approved_by!r} 不得与申请人相同（§17 职责分离）")
    created_dt = parse_iso_z(created)
    expires_dt = parse_iso_z(expires_at)
    if expires_dt <= created_dt:
        problems.append("豁免有效期必须晚于创建时间")
    max_days = rule.waiver.max_validity_days
    if max_days is not None and expires_dt > created_dt + timedelta(days=max_days):
        problems.append(f"豁免有效期超过规则 {rule.id} 允许的上限 {max_days} 天（§7.2.6）")
    if problems:
        raise WaiverError(problems)
    return Waiver(
        rule_id=rule.id,
        reason=reason.strip(),
        scope=scope_tuple,
        approver_role=approver_role,
        requested_by=requested_by,
        approved_by=approved_by,
        created_at=created,
        expires_at=expires_at,
    )


def is_waiver_valid(waiver: Waiver, task: Task, *, at: str | None = None) -> bool:
    """豁免在指定时点是否对任务有效：未过期 + 范围覆盖。

    范围覆盖定义：``scope`` 含 ``"*"``（全局）、``task.id`` 或 ``task.project_id``
    之一即覆盖该任务；其余情况视为不覆盖。``at`` 缺省为当前时间。
    """
    moment = parse_iso_z(at) if at is not None else parse_iso_z(utc_now())
    if moment > parse_iso_z(waiver.expires_at):
        return False
    return bool({"*", task.id, task.project_id} & set(waiver.scope))
