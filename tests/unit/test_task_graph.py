"""P1-3 单元测试：任务依赖图（规范 §8 阶段 4、§16.4、§19.1）。

覆盖：依赖解析、循环检测（含自环与多节点环）、拓扑就绪调度、受影响任务标记。
"""

from __future__ import annotations

import pytest

from core.domain import Task, TaskStatus
from core.orchestrator import (
    CycleError,
    DuplicateTaskError,
    TaskGraph,
    UnknownDependencyError,
)


def make_task(
    task_id: str,
    deps: list[str] | None = None,
    status: TaskStatus = TaskStatus.READY,
) -> Task:
    return Task(
        id=task_id,
        project_id="PRJ-test",
        title=f"任务 {task_id}",
        owner_role="backend",
        acceptance_criteria=["验收条件"],
        dependencies=deps or [],
        status=status,
    )


def diamond() -> list[Task]:
    """A → B、A→C、B→D、C→D 的菱形依赖。"""
    return [
        make_task("TASK-A"),
        make_task("TASK-B", ["TASK-A"]),
        make_task("TASK-C", ["TASK-A"]),
        make_task("TASK-D", ["TASK-B", "TASK-C"]),
    ]


# ---------- 建图校验 ----------


def test_empty_graph() -> None:
    graph = TaskGraph([])
    assert graph.task_ids == () and graph.topological_order() == ()
    assert graph.ready_tasks() == ()


def test_duplicate_task_id_rejected() -> None:
    with pytest.raises(DuplicateTaskError, match="TASK-A"):
        TaskGraph([make_task("TASK-A"), make_task("TASK-A")])


def test_unknown_dependency_rejected() -> None:
    with pytest.raises(UnknownDependencyError, match="TASK-X"):
        TaskGraph([make_task("TASK-A", ["TASK-X"])])


def test_cycle_detected_with_path() -> None:
    tasks = [
        make_task("TASK-A", ["TASK-C"]),
        make_task("TASK-B", ["TASK-A"]),
        make_task("TASK-C", ["TASK-B"]),
    ]
    with pytest.raises(CycleError, match=r"TASK-A → TASK-C → TASK-B → TASK-A"):
        TaskGraph(tasks)


def test_self_dependency_is_cycle() -> None:
    with pytest.raises(CycleError, match=r"TASK-A → TASK-A"):
        TaskGraph([make_task("TASK-A", ["TASK-A"])])


def test_query_unknown_task_rejected() -> None:
    graph = TaskGraph(diamond())
    with pytest.raises(UnknownDependencyError):
        graph.dependencies_of("TASK-Z")
    with pytest.raises(UnknownDependencyError):
        graph.affected_by("TASK-Z")


# ---------- 拓扑序与依赖解析 ----------


def test_topological_order_respects_dependencies() -> None:
    order = TaskGraph(diamond()).topological_order()
    assert order.index("TASK-A") < order.index("TASK-B")
    assert order.index("TASK-A") < order.index("TASK-C")
    assert order.index("TASK-B") < order.index("TASK-D")
    assert order.index("TASK-C") < order.index("TASK-D")


def test_dependencies_and_dependents() -> None:
    graph = TaskGraph(diamond())
    assert graph.dependencies_of("TASK-D") == frozenset({"TASK-B", "TASK-C"})
    assert graph.dependents_of("TASK-A") == frozenset({"TASK-B", "TASK-C"})
    assert graph.dependents_of("TASK-D") == frozenset()


# ---------- 就绪调度（依赖全 PASSED 才可就绪） ----------


def test_ready_tasks_requires_ready_status_and_passed_deps() -> None:
    tasks = diamond()
    # A 未通过前，B/C/D 均不就绪
    assert [t.id for t in TaskGraph(tasks).ready_tasks()] == ["TASK-A"]

    tasks[0].status = TaskStatus.PASSED
    assert [t.id for t in TaskGraph(tasks).ready_tasks()] == ["TASK-B", "TASK-C"]

    tasks[1].status = TaskStatus.PASSED  # C 未过，D 仍不就绪
    assert [t.id for t in TaskGraph(tasks).ready_tasks()] == ["TASK-C"]

    tasks[2].status = TaskStatus.PASSED
    assert [t.id for t in TaskGraph(tasks).ready_tasks()] == ["TASK-D"]


def test_ready_tasks_skip_non_ready_status() -> None:
    tasks = [make_task("TASK-A", status=TaskStatus.IN_PROGRESS), make_task("TASK-B", ["TASK-A"])]
    assert TaskGraph(tasks).ready_tasks() == ()


def test_is_unblocked() -> None:
    tasks = diamond()
    graph = TaskGraph(tasks)
    assert graph.is_unblocked("TASK-A") is True
    assert graph.is_unblocked("TASK-D") is False


# ---------- 受影响任务标记（上游变更触发下游重验） ----------


def test_affected_by_transitive_closure() -> None:
    graph = TaskGraph(diamond())
    assert graph.affected_by("TASK-A") == frozenset({"TASK-B", "TASK-C", "TASK-D"})
    assert graph.affected_by("TASK-B") == frozenset({"TASK-D"})
    assert graph.affected_by("TASK-D") == frozenset()
