"""SQLite 状态仓储（ADR-001）。

- WAL 模式：读写并发、崩溃后已提交数据不丢；
- 实体统一存 ``entities`` 表：``(kind, id)`` 主键，``payload`` 为完整 JSON，
  ``status``/``updated_at`` 冗余为索引列（便于按状态筛选调度）；
- ``schema_migrations`` 表版本化，迁移按版本号递增执行，禁止改写已应用版本。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from core.domain.entities import Entity, utc_now

SCHEMA_VERSION = 1

# 迁移脚本按版本号递增登记；已应用的版本不得修改（§7.3 数据库标准：版本化迁移）
_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE entities (
        kind TEXT NOT NULL,
        id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (kind, id)
    );
    CREATE INDEX idx_entities_status ON entities (kind, status);
    """,
}


class StateStore:
    """平台状态存储：实体 JSON 入库，提交即持久（崩溃恢复依赖 WAL）。"""

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    @property
    def path(self) -> Path:
        return self._path

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
        ).fetchone()
        current = int(row["v"])
        for version in sorted(_MIGRATIONS):
            if version > current:
                self._conn.executescript(_MIGRATIONS[version])
                self._conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, utc_now()),
                )
        self._conn.commit()

    def save(self, kind: str, entity: Entity) -> None:
        """按 (kind, id) upsert 实体；payload 为 to_dict() 完整 JSON。"""
        payload = entity.to_dict()
        self._conn.execute(
            "INSERT INTO entities (kind, id, status, payload, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (kind, id) DO UPDATE SET"
            " status=excluded.status, payload=excluded.payload,"
            " updated_at=excluded.updated_at",
            (
                kind,
                entity.id,
                str(payload.get("status", "")),
                json.dumps(payload, ensure_ascii=False),
                entity.updated_at,
            ),
        )
        self._conn.commit()

    def load(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT payload FROM entities WHERE kind=? AND id=?", (kind, entity_id)
        ).fetchone()
        return json.loads(str(row["payload"])) if row else None

    def load_all(self, kind: str, *, status: str | None = None) -> list[dict[str, Any]]:
        if status is None:
            rows = self._conn.execute(
                "SELECT payload FROM entities WHERE kind=? ORDER BY updated_at", (kind,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT payload FROM entities WHERE kind=? AND status=? ORDER BY updated_at",
                (kind, status),
            ).fetchall()
        return [json.loads(str(r["payload"])) for r in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
