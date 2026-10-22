"""P1-6 单元测试：模拟模型适配器（ADR-002、config/models.yaml simulation 节）。

覆盖：脚本化响应顺序消费、结构化输出 SchemaRegistry 校验（正反例）、
四类失败注入、用量记录、取消与脚本耗尽。
"""

from __future__ import annotations

import pytest

from adapters.models import (
    CompletionRequest,
    MalformedOutput,
    ModelError,
    ModelTimeout,
    NetworkError,
    RateLimited,
    ScriptStep,
    SimulatedAdapter,
)
from core.domain import SchemaRegistry


def req(prompt: str = "你好", schema: str | None = None) -> CompletionRequest:
    return CompletionRequest(role="backend", prompt=prompt, schema=schema, task_id="TASK-1")


# ---------- 脚本化响应与用量 ----------


def test_scripted_responses_in_order_and_usage_recorded() -> None:
    adapter = SimulatedAdapter(["第一次响应", ScriptStep(content="第二次响应")])
    r1 = adapter.complete(req())
    r2 = adapter.complete(req())
    assert (r1.content, r2.content) == ("第一次响应", "第二次响应")
    assert r1.parsed is None

    assert len(adapter.usage_records) == 2
    usage = adapter.usage_records[0]
    assert usage.task_id == "TASK-1" and usage.model_profile == "simulated"
    assert usage.prompt_tokens == len("你好")
    assert usage.cost_usd is None  # 模拟模式成本不可得，不伪造（红线 1）


def test_script_exhausted_raises() -> None:
    adapter = SimulatedAdapter(["仅一次"])
    adapter.complete(req())
    with pytest.raises(ModelError, match="脚本耗尽"):
        adapter.complete(req())


def test_cancel_blocks_further_calls() -> None:
    adapter = SimulatedAdapter(["x"])
    adapter.cancel()
    with pytest.raises(ModelError, match="已取消"):
        adapter.complete(req())


# ---------- 结构化输出（SchemaRegistry 校验） ----------


def test_structured_output_validated_by_registry() -> None:
    payload = (
        '{"task_id": "TASK-1", "project_id": "PRJ-1", "title": "实现登录",'
        ' "owner_role": "backend", "status": "READY", "schema_version": 1}'
    )
    adapter = SimulatedAdapter([payload], registry=SchemaRegistry())
    result = adapter.complete(req(schema="task"))
    assert result.parsed is not None
    assert result.parsed["task_id"] == "TASK-1"


def test_structured_output_schema_violation() -> None:
    adapter = SimulatedAdapter(['{"task_id": 1}'], registry=SchemaRegistry())
    with pytest.raises(MalformedOutput, match="不满足协议"):
        adapter.complete(req(schema="task"))
    assert len(adapter.errors) == 1


def test_structured_output_invalid_json() -> None:
    adapter = SimulatedAdapter(["{不是 JSON"], registry=SchemaRegistry())
    with pytest.raises(MalformedOutput, match="非合法 JSON"):
        adapter.complete(req(schema="task"))


def test_structured_output_requires_registry() -> None:
    adapter = SimulatedAdapter(["{}"])  # 未提供 registry
    with pytest.raises(ModelError, match="SchemaRegistry"):
        adapter.complete(req(schema="task"))


# ---------- 失败注入（按调用序号） ----------


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        ("timeout", ModelTimeout),
        ("network_error", NetworkError),
        ("rate_limit", RateLimited),
        ("malformed_json", MalformedOutput),
    ],
)
def test_failure_injection_by_call_order(failure: str, error_type: type[ModelError]) -> None:
    adapter = SimulatedAdapter([ScriptStep(failure=failure), "恢复正常"])
    with pytest.raises(error_type) as exc_info:
        adapter.complete(req())
    assert exc_info.value.retryable is True
    assert adapter.errors[0] is exc_info.value  # 错误被记录，供审计
    assert adapter.complete(req()).content == "恢复正常"  # 脚本继续消费


def test_unknown_failure_type_rejected() -> None:
    with pytest.raises(ValueError, match="未知失败注入类型"):
        ScriptStep(failure="explode")


def test_dict_and_str_script_coercion() -> None:
    adapter = SimulatedAdapter([{"content": "a"}, "b"])
    assert adapter.complete(req()).content == "a"
    assert adapter.complete(req()).content == "b"
