"""P1-7 单元测试：重试与预算控制（规范 §9.3）。

红线：超限即停并生成阻塞报告，证明不无限循环、不假装完成。
"""

from __future__ import annotations

import pytest

from adapters.models import MalformedOutput, ModelError, ModelTimeout, NetworkError
from core.orchestrator.retry import (
    RetryBudgetExhausted,
    RetryController,
    RetryPolicy,
    error_signature,
    execute_with_retry,
)

# ---------- 策略配置 ----------


def test_policy_defaults() -> None:
    policy = RetryPolicy()
    assert policy.max_retries_per_task == 3
    assert policy.task_timeout_seconds == 1800


def test_policy_from_repo_config() -> None:
    policy = RetryPolicy.from_config()  # 读仓库 config/models.yaml
    assert policy.max_retries_per_task == 3
    assert policy.task_timeout_seconds == 1800


def test_policy_from_missing_config_uses_defaults(tmp_path) -> None:  # type: ignore[no-untyped-def]
    policy = RetryPolicy.from_config(tmp_path / "不存在.yaml")
    assert policy.max_retries_per_task == 3


# ---------- 失败记录与决策 ----------


def test_record_failure_requires_root_cause() -> None:
    controller = RetryController()
    with pytest.raises(ValueError, match="根因"):
        controller.record_failure(ModelTimeout("t"), "  ")


def test_retry_allowed_for_distinct_failures() -> None:
    controller = RetryController()
    d1 = controller.record_failure(ModelTimeout("超时"), "网络抖动")
    d2 = controller.record_failure(NetworkError("连接重置"), "代理中断")
    assert (d1.action, d2.action) == ("retry", "retry")
    assert controller.attempts == 2


def test_same_signature_twice_escalates() -> None:
    controller = RetryController()
    controller.record_failure(ModelTimeout("超时 30s"), "首次超时")
    decision = controller.record_failure(ModelTimeout("超时 60s"), "再次超时")
    # 数字折叠后签名相同 → 相同错误连续出现 → 升级
    assert decision.action == "escalate" and "相同错误" in decision.reason


def test_non_retryable_error_escalates_immediately() -> None:
    controller = RetryController()
    decision = controller.record_failure(ModelError("协议错误"), "请求构造错误")
    assert decision.action == "escalate"


def test_blocked_after_max_retries_with_report() -> None:
    controller = RetryController(RetryPolicy(max_retries_per_task=3))
    errors = [ModelTimeout("a"), NetworkError("b"), MalformedOutput("c"), ModelTimeout("d")]
    decisions = [controller.record_failure(e, f"根因{i}") for i, e in enumerate(errors)]
    assert [d.action for d in decisions] == ["retry", "retry", "retry", "blocked"]

    report = controller.blocking_report("TASK-1", reason=decisions[-1].reason)
    assert report["outcome"] == "BLOCKED" and report["attempts"] == 4
    assert [f["root_cause"] for f in report["failures"]] == ["根因0", "根因1", "根因2", "根因3"]
    assert "未假装任务完成" in report["note"]


def test_error_signature_folds_digits() -> None:
    assert error_signature(ModelTimeout("超时 30 秒")) == error_signature(
        ModelTimeout("超时 99 秒")
    )


# ---------- 执行器：超限即停，不无限循环 ----------


def test_execute_succeeds_after_retries() -> None:
    calls = iter([ModelTimeout("t1"), NetworkError("n1"), "成功"])

    def fn() -> str:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    assert execute_with_retry(fn, task_id="TASK-1") == "成功"


def test_execute_stops_at_budget_no_infinite_loop() -> None:
    policy = RetryPolicy(max_retries_per_task=3)
    call_count = 0

    def always_timeout() -> None:
        nonlocal call_count
        call_count += 1
        raise ModelTimeout(f"第 {call_count} 次超时")

    # 相同签名第 2 次即升级；用 analyze 给出不同根因不影响签名判定
    with pytest.raises(RetryBudgetExhausted) as exc_info:
        execute_with_retry(always_timeout, task_id="TASK-1", policy=policy)
    assert call_count == 2  # 相同错误连续 → 升级，远小于预算上限
    assert exc_info.value.report["outcome"] == "BLOCKED"

    # 不同错误轮流：走到预算上限（3 次重试 + 首次 = 4 次调用）即停
    call_count = 0
    errors = [ModelTimeout("a"), NetworkError("b"), MalformedOutput("c"), ModelTimeout("d")]

    def rotating_failures() -> None:
        nonlocal call_count
        error = errors[min(call_count, len(errors) - 1)]
        call_count += 1
        raise error

    with pytest.raises(RetryBudgetExhausted):
        execute_with_retry(rotating_failures, task_id="TASK-1", policy=policy)
    assert call_count == 4  # 超限即停，不无限循环


def test_execute_stops_on_time_budget() -> None:
    policy = RetryPolicy(max_retries_per_task=100, task_timeout_seconds=0)
    call_count = 0

    def failing() -> None:
        nonlocal call_count
        call_count += 1
        raise NetworkError(f"故障 {call_count}")

    with pytest.raises(RetryBudgetExhausted) as exc_info:
        execute_with_retry(failing, task_id="TASK-1", policy=policy)
    assert call_count == 1  # 超时预算为 0：首次失败后即停
    assert "时间预算" in exc_info.value.report["reason"]
