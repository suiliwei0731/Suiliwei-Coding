"""崩溃恢复（ADR-001、§4 原则 11：一切流程可暂停、恢复、审计）。

恢复语义：进程重启后，IN_PROGRESS 任务一律回退 READY 重跑
（执行中断，产物不可信，不得假装完成）；PASSED/CANCELLED 终态与其余状态不动。
被恢复的任务列表返回给调用方，由审计模块（P1-5）记录状态变化。
"""

from __future__ import annotations

from core.domain.entities import Task, utc_now
from core.domain.enums import TaskStatus
from core.orchestrator.store import StateStore

TASK_KIND = "task"  # entities 表中 Task 实体的 kind 标识


def recover_in_progress_tasks(store: StateStore) -> list[Task]:
    """将全部 IN_PROGRESS 任务回退为 READY，返回被恢复的任务列表。"""
    recovered: list[Task] = []
    for payload in store.load_all(TASK_KIND, status=TaskStatus.IN_PROGRESS.value):
        task = Task.from_dict(payload)
        task.status = TaskStatus.READY
        task.updated_at = utc_now()
        store.save(TASK_KIND, task)
        recovered.append(task)
    return recovered
