"""P1-4 集成测试：崩溃恢复（ADR-001、§4 原则 11）。

子进程写入状态后 ``os._exit(1)`` 模拟强杀（不经任何清理/关闭），
父进程重新打开同一数据库执行恢复，断言状态无损且语义正确。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core.domain import TaskStatus
from core.orchestrator import CheckpointManager, StateStore, recover_in_progress_tasks

REPO_ROOT = Path(__file__).resolve().parents[2]

# 子进程脚本：建库 → 写三个任务（READY/IN_PROGRESS/PASSED）→ 写检查点 → 强杀
_CHILD_SCRIPT = (
    "import os, sys;"
    "from pathlib import Path;"
    "from core.domain import Task, TaskStatus;"
    "from core.orchestrator import CheckpointManager, StateStore;"
    "store = StateStore(Path(sys.argv[1]));"
    "[store.save('task', Task(id=t, project_id='PRJ-crash', title=t,"
    " owner_role='backend', status=s))"
    " for t, s in [('TASK-ready', TaskStatus.READY),"
    " ('TASK-running', TaskStatus.IN_PROGRESS), ('TASK-done', TaskStatus.PASSED)]];"
    "CheckpointManager(Path(sys.argv[2])).save('scheduler',"
    " {'in_progress': ['TASK-running']});"
    "store.close();"
    "os._exit(1)"
)


def test_crash_recovery_after_hard_kill(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "platform.db"
    ck_dir = tmp_path / "checkpoints"

    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, str(db_path), str(ck_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0, "子进程应被模拟强杀（非零退出）"

    # 父进程重新打开：WAL 保证已提交数据仍在
    with StateStore(db_path) as store:
        recovered = recover_in_progress_tasks(store)
        assert [t.id for t in recovered] == ["TASK-running"]

        statuses = {r["task_id"]: r["status"] for r in store.load_all("task")}
        assert statuses["TASK-running"] == TaskStatus.READY.value  # 中断任务回退重跑
        assert statuses["TASK-done"] == TaskStatus.PASSED.value  # 已通过不重做
        assert statuses["TASK-ready"] == TaskStatus.READY.value

    # 检查点完整可读（原子写入）
    assert CheckpointManager(ck_dir).load("scheduler") == {"in_progress": ["TASK-running"]}
