"""任务依赖图（规范 §8 阶段 4、§9.2、§16.4）。

职责：
- 建图时校验：任务 ID 唯一、依赖引用必须存在、不得有循环依赖
  （§8 阶段 4：依赖构成 DAG，发现循环先解决，报错含循环路径）；
- 拓扑就绪调度：仅当任务状态为 READY 且全部前置依赖 PASSED 时才可领取执行
  （§16.4：未通过任务对依赖它的后续任务不可见）；
- 受影响分析：上游任务变更时给出需重验的全部下游任务
  （§8 阶段 7：上游契约变更时标记受影响任务并触发重验）。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from core.domain.entities import Task
from core.domain.enums import TaskStatus


class DependencyError(ValueError):
    """依赖图结构错误基类。"""


class DuplicateTaskError(DependencyError):
    """任务 ID 重复。"""


class UnknownDependencyError(DependencyError):
    """依赖引用了不存在的任务 ID。"""


class CycleError(DependencyError):
    """依赖图存在循环依赖。"""


class TaskGraph:
    """任务 DAG：依赖解析、循环检测、就绪调度与受影响分析。

    图在建构时冻结结构（任务对象的 status 变化不影响结构，只影响调度结果）。
    """

    def __init__(self, tasks: Iterable[Task]) -> None:
        self._tasks: dict[str, Task] = {}
        for task in tasks:
            if task.id in self._tasks:
                raise DuplicateTaskError(f"任务 ID 重复: {task.id}")
            self._tasks[task.id] = task

        self._deps: dict[str, frozenset[str]] = {}
        self._dependents: dict[str, set[str]] = defaultdict(set)
        for task_id, task in self._tasks.items():
            deps = frozenset(task.dependencies)
            unknown = deps - self._tasks.keys()
            if unknown:
                raise UnknownDependencyError(
                    f"任务 {task_id} 依赖不存在的任务: {', '.join(sorted(unknown))}"
                )
            self._deps[task_id] = deps
            for dep in deps:
                self._dependents[dep].add(task_id)

        self._topo = self._topological_order()  # 建图时即检测循环

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(self._tasks)

    def get(self, task_id: str) -> Task:
        self._require_known(task_id)
        return self._tasks[task_id]

    def dependencies_of(self, task_id: str) -> frozenset[str]:
        """直接前置依赖集合。"""
        self._require_known(task_id)
        return self._deps[task_id]

    def dependents_of(self, task_id: str) -> frozenset[str]:
        """直接下游（依赖该任务的任务）。"""
        self._require_known(task_id)
        return frozenset(self._dependents.get(task_id, ()))

    def affected_by(self, task_id: str) -> frozenset[str]:
        """上游变更时需重验的全部下游任务（传递闭包，不含自身）。"""
        self._require_known(task_id)
        affected: set[str] = set()
        stack = list(self._dependents.get(task_id, ()))
        while stack:
            current = stack.pop()
            if current in affected:
                continue
            affected.add(current)
            stack.extend(self._dependents.get(current, ()))
        return frozenset(affected)

    def topological_order(self) -> tuple[str, ...]:
        """拓扑序（依赖先于下游；同层按任务 ID 字典序，保证确定性）。"""
        return self._topo

    def is_unblocked(self, task_id: str) -> bool:
        """全部前置依赖均为 PASSED。"""
        self._require_known(task_id)
        return all(self._tasks[d].status is TaskStatus.PASSED for d in self._deps[task_id])

    def ready_tasks(self) -> tuple[Task, ...]:
        """可领取执行的任务：状态 READY 且依赖全部 PASSED（按拓扑序返回）。"""
        return tuple(
            self._tasks[tid]
            for tid in self._topo
            if self._tasks[tid].status is TaskStatus.READY and self.is_unblocked(tid)
        )

    def _require_known(self, task_id: str) -> None:
        if task_id not in self._tasks:
            raise UnknownDependencyError(f"任务不存在: {task_id}")

    def _topological_order(self) -> tuple[str, ...]:
        """迭代 DFS 拓扑排序；发现循环抛 CycleError（含循环路径）。"""
        _WHITE, _GRAY, _BLACK = 0, 1, 2
        color = dict.fromkeys(self._tasks, _WHITE)
        order: list[str] = []
        for root in self._tasks:
            if color[root] != _WHITE:
                continue
            # 栈与路径同步维护：stack 保存 (节点, 未访问依赖列表)，path 保存当前灰色链
            stack: list[tuple[str, list[str]]] = [(root, sorted(self._deps[root]))]
            path: list[str] = [root]
            color[root] = _GRAY
            while stack:
                node, children = stack[-1]
                descended = False
                while children:
                    child = children.pop()
                    if color[child] == _GRAY:
                        cycle = path[path.index(child) :] + [child]
                        raise CycleError("依赖循环: " + " → ".join(cycle))
                    if color[child] == _WHITE:
                        color[child] = _GRAY
                        stack.append((child, sorted(self._deps[child])))
                        path.append(child)
                        descended = True
                        break
                if not descended:
                    stack.pop()
                    path.pop()
                    color[node] = _BLACK
                    order.append(node)
        # 后序完成序列即依赖优先（DFS 沿"依赖"方向下潜，先完成者必为被依赖方）
        return tuple(order)
