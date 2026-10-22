"""Phase 1 阶段门禁证据（docs/03 §4）：

不依赖真实模型，通过模拟适配器失败注入验证工作流状态与失败处理：
模型持续超时 → 重试控制器升级 → 任务经 FAILED/REWORK 循环，超限后转 BLOCKED，
全程有失败记录与阻塞报告，不无限循环、不假装完成（§9.3 红线）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters.models import CompletionRequest, ScriptStep, SimulatedAdapter
from core.domain import EvidenceKind, Task, TaskStatus
from core.orchestrator import (
    RetryBudgetExhausted,
    RetryPolicy,
    StateStore,
    execute_with_retry,
)
from core.workflow import (
    Evidence,
    GuardRejectionError,
    TaskStateMachine,
    TransitionContext,
)


def run_round(adapter: SimulatedAdapter, task_id: str, policy: RetryPolicy) -> None:
    execute_with_retry(
        lambda: adapter.complete(
            CompletionRequest(role="backend", prompt="实现任务", task_id=task_id)
        ),
        task_id=task_id,
        policy=policy,
    )


def failed_evidence(summary: str) -> Evidence:
    return Evidence(
        kind=EvidenceKind.COMMAND_RESULT,
        summary=summary,
        command="adapter.complete",
        exit_code=1,
    )


def test_simulated_failures_drive_task_to_blocked(tmp_path: Path) -> None:
    policy = RetryPolicy(max_retries_per_task=3)
    adapter = SimulatedAdapter([ScriptStep(failure="timeout")] * 10)
    machine = TaskStateMachine()
    task = Task(
        id="TASK-G1",
        project_id="PRJ-gate",
        title="门禁验证任务",
        owner_role="backend",
        acceptance_criteria=["通过门禁"],
        status=TaskStatus.IN_PROGRESS,
    )

    with StateStore(tmp_path / "state" / "platform.db") as store:
        store.save("task", task)

        # 三轮：失败 → FAILED → REWORK（retry_count 1/2/3）→ IN_PROGRESS
        for round_no in range(1, 4):
            with pytest.raises(RetryBudgetExhausted) as exc_info:
                run_round(adapter, task.id, policy)
            assert exc_info.value.report["outcome"] == "BLOCKED"  # 失败即如实报告

            machine.transition(task, TaskStatus.FAILED, failed_evidence(f"第 {round_no} 轮超时"))
            machine.transition(
                task,
                TaskStatus.REWORK,
                Evidence(kind=EvidenceKind.MANUAL, summary="根因：模型超时，重试"),
            )
            assert task.retry_count == round_no
            machine.transition(
                task,
                TaskStatus.IN_PROGRESS,
                Evidence(
                    kind=EvidenceKind.COMMAND_RESULT,
                    summary="重新领取执行",
                    command="orchestrator dispatch",
                    exit_code=0,
                ),
            )
            store.save("task", task)

        # 第四轮仍失败：FAILED → REWORK 被拒（超限）→ 转 BLOCKED
        with pytest.raises(RetryBudgetExhausted):
            run_round(adapter, task.id, policy)
        machine.transition(task, TaskStatus.FAILED, failed_evidence("第 4 轮超时"))
        with pytest.raises(GuardRejectionError, match="上限"):
            machine.transition(
                task, TaskStatus.REWORK, Evidence(kind=EvidenceKind.MANUAL, summary="再试")
            )
        machine.transition(
            task,
            TaskStatus.BLOCKED,
            Evidence(kind=EvidenceKind.MANUAL, summary="重试超限，升级人工处理"),
            TransitionContext(reason="重试超限（3/3 次已用尽），转 BLOCKED 并请求处理"),
        )
        store.save("task", task)

        # 终态与证据断言
        assert task.status is TaskStatus.BLOCKED and task.retry_count == 3
        assert task.blocked_reason is not None and "重试超限" in task.blocked_reason
        assert adapter.complete is not None
        assert len(adapter.errors) == 8  # 相同签名两次即升级：每轮 2 次调用 × 4 轮
        assert len(adapter.usage_records) == 8  # 每次调用均记录用量（含失败）

        persisted = store.load("task", task.id)
        assert persisted is not None and persisted["status"] == "BLOCKED"
