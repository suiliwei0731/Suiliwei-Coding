"""P1-4 单元测试：SQLite 仓储、检查点与恢复逻辑（ADR-001）。

崩溃恢复的杀进程场景见 tests/integration/test_crash_recovery.py。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.domain import Task, TaskStatus
from core.orchestrator import (
    CheckpointManager,
    StateStore,
    recover_in_progress_tasks,
)


def make_task(task_id: str, status: TaskStatus = TaskStatus.READY) -> Task:
    return Task(
        id=task_id,
        project_id="PRJ-test",
        title=f"任务 {task_id}",
        owner_role="backend",
        status=status,
    )


# ---------- StateStore ----------


def test_save_load_roundtrip(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state" / "platform.db") as store:
        store.save("task", make_task("TASK-1"))
        loaded = Task.from_dict(store.load("task", "TASK-1") or {})
    assert loaded.id == "TASK-1" and loaded.status is TaskStatus.READY


def test_upsert_updates_status(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        task = make_task("TASK-1")
        store.save("task", task)
        task.status = TaskStatus.IN_PROGRESS
        task.version += 1
        store.save("task", task)
        rows = store.load_all("task")
    assert len(rows) == 1 and rows[0]["status"] == "IN_PROGRESS" and rows[0]["version"] == 2


def test_load_all_filter_by_status(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        store.save("task", make_task("TASK-1", TaskStatus.READY))
        store.save("task", make_task("TASK-2", TaskStatus.PASSED))
        assert len(store.load_all("task")) == 2
        passed = store.load_all("task", status="PASSED")
        assert [r["task_id"] for r in passed] == ["TASK-2"]
        assert store.load("task", "TASK-X") is None


def test_wal_mode_and_schema_migration(tmp_path: Path) -> None:
    db = tmp_path / "platform.db"
    with StateStore(db) as store:
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]  # noqa: SLF001
        assert str(mode).lower() == "wal"
        row = store._conn.execute(  # noqa: SLF001
            "SELECT MAX(version) AS v FROM schema_migrations"
        ).fetchone()
        assert row["v"] == 1
    # 重复打开不重复迁移
    with StateStore(db) as store:
        row = store._conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()  # noqa: SLF001
        assert row["c"] == 1


# ---------- CheckpointManager ----------


def test_checkpoint_roundtrip_and_missing(tmp_path: Path) -> None:
    manager = CheckpointManager(tmp_path / "checkpoints")
    assert manager.load("scheduler") is None
    manager.save("scheduler", {"in_progress": ["TASK-1"], "seq": 7})
    assert manager.load("scheduler") == {"in_progress": ["TASK-1"], "seq": 7}
    assert not (tmp_path / "checkpoints" / "scheduler.tmp").exists()  # 原子替换无残留


def test_checkpoint_invalid_name(tmp_path: Path) -> None:
    manager = CheckpointManager(tmp_path / "ck")
    with pytest.raises(ValueError, match="非法检查点名"):
        manager.save("a/b", {})


# ---------- 恢复逻辑（进程内） ----------


def test_recover_reverts_only_in_progress(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        store.save("task", make_task("TASK-ready", TaskStatus.READY))
        store.save("task", make_task("TASK-running", TaskStatus.IN_PROGRESS))
        store.save("task", make_task("TASK-done", TaskStatus.PASSED))

        recovered = recover_in_progress_tasks(store)

        assert [t.id for t in recovered] == ["TASK-running"]
        statuses = {r["task_id"]: r["status"] for r in store.load_all("task")}
        assert statuses == {
            "TASK-ready": "READY",
            "TASK-running": "READY",
            "TASK-done": "PASSED",  # 终态不重做
        }


def test_recover_empty_store(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        assert recover_in_progress_tasks(store) == []
