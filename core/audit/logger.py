"""审计日志（规范 §17/§18）。

AuditEvent 双写：SQLite（kind=audit_event，可查询）+ runtime/logs/audit.jsonl
（机器可读导出）。写入前对全字段强制脱敏，任何路径都不得绕过（§17）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.audit.redact import redact_value
from core.domain.entities import AuditEvent, new_id
from core.orchestrator.store import StateStore

AUDIT_KIND = "audit_event"


class AuditLogger:
    """审计记录器：脱敏 → 落库 → JSONL 导出。"""

    def __init__(self, store: StateStore, log_dir: Path | str | None = None) -> None:
        self._store = store
        self._log_path: Path | None = None
        if log_dir is not None:
            log_dir = Path(log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = log_dir / "audit.jsonl"

    def record(self, event: AuditEvent) -> AuditEvent:
        """记录一条审计事件；返回脱敏后的事件（调用方不得再使用原对象）。"""
        safe = AuditEvent.from_dict(redact_value(event.to_dict()))
        self._store.save(AUDIT_KIND, safe)
        if self._log_path is not None:
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(safe.to_dict(), ensure_ascii=False) + "\n")
        return safe

    def log(
        self,
        action: str,
        actor: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """便捷入口：构造并记录审计事件。"""
        return self.record(
            AuditEvent(
                id=new_id("EVT"),
                action=action,
                actor=actor,
                project_id=project_id,
                task_id=task_id,
                detail=detail or {},
            )
        )

    def list_events(self) -> list[AuditEvent]:
        """按写入顺序读取全部审计事件。"""
        return [AuditEvent.from_dict(row) for row in self._store.load_all(AUDIT_KIND)]
