"""P4-1 单元测试：OpenAI 兼容真实模型适配器（本地 HTTP 服务，不访问外网）。

覆盖：未启用/未配置明确报错、成功调用与用量、结构化校验、错误分类
（401/429/5xx/超时/连接失败/非 JSON）、流式增量、流式取消、审计不含密钥。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from adapters.models import (
    CompletionRequest,
    MalformedOutput,
    ModelCancelled,
    ModelError,
    ModelProfile,
    ModelTimeout,
    NetworkError,
    OpenAICompatibleAdapter,
    RateLimited,
)
from core.audit.logger import AuditLogger
from core.domain.validation import SchemaRegistry
from core.orchestrator.store import StateStore


class _Handler(BaseHTTPRequestHandler):
    """按 server.config 返回预设响应；支持延时与 SSE 分块。"""

    def do_POST(self) -> None:  # noqa: N802 - http.server 约定
        length = int(self.headers.get("content-length", 0))
        self.rfile.read(length)
        config: dict[str, Any] = self.server.config  # type: ignore[attr-defined]
        if config.get("delay"):
            time.sleep(float(config["delay"]))
        if config.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in config.get("chunks", []):
                self.wfile.write(str(chunk).encode("utf-8"))
                self.wfile.flush()
                time.sleep(float(config.get("chunk_delay", 0)))
            return
        body = config.get("body", {})
        raw = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        self.send_response(int(config.get("status", 200)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw.encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 静默测试日志
        return


@contextmanager
def _server(config: dict[str, Any]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.config = config  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()


def _profile(**overrides: Any) -> ModelProfile:
    base: dict[str, Any] = {
        "name": "test-profile",
        "enabled": True,
        "base_url_env": "TEST_BASE_URL",
        "api_key_env": "TEST_API_KEY",
        "model_env": "TEST_MODEL",
        "request_timeout_seconds": 2.0,
    }
    base.update(overrides)
    return ModelProfile(**base)


def _adapter(
    base_url: str,
    *,
    timeout: float = 2.0,
    registry: SchemaRegistry | None = None,
    audit: AuditLogger | None = None,
    env: dict[str, str] | None = None,
) -> OpenAICompatibleAdapter:
    resolved_env = (
        env
        if env is not None
        else {
            "TEST_BASE_URL": base_url,
            "TEST_API_KEY": "sk-test-secret-value",
            "TEST_MODEL": "test-model",
        }
    )
    return OpenAICompatibleAdapter(
        _profile(request_timeout_seconds=timeout),
        registry=registry,
        audit=audit,
        env=resolved_env,
    )


def _req(schema: str | None = None) -> CompletionRequest:
    return CompletionRequest(role="backend", prompt="你好", schema=schema, task_id="TASK-1")


def _audit(tmp_path: Path) -> tuple[AuditLogger, StateStore]:
    store = StateStore(tmp_path / "state.db")
    return AuditLogger(store, log_dir=tmp_path / "logs"), store


# ---------- 未启用/未配置：明确报错，不伪造 ----------


def test_disabled_profile_rejected_without_network() -> None:
    adapter = OpenAICompatibleAdapter(_profile(enabled=False), env={})
    with pytest.raises(ModelError, match="未启用"):
        adapter.complete(_req())
    assert adapter.runs[0].status.value == "FAILED"
    assert adapter.usage_records == []


def test_missing_env_rejected_explicitly() -> None:
    adapter = _adapter("http://127.0.0.1:9/v1", env={})
    with pytest.raises(ModelError, match="未配置环境变量"):
        adapter.complete(_req())
    assert adapter.runs[0].status.value == "FAILED"


def test_default_config_profile_is_disabled() -> None:
    profile = ModelProfile.from_config()
    assert profile.name == "cloud-openai-compatible"
    assert profile.enabled is False


# ---------- 成功调用与用量 ----------


def test_success_records_content_usage_and_audit(tmp_path: Path) -> None:
    audit, store = _audit(tmp_path)
    body = {
        "choices": [{"message": {"content": "实现完成"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 4},
    }
    with _server({"body": body}) as url:
        adapter = _adapter(url, audit=audit)
        result = adapter.complete(_req())

    assert result.content == "实现完成"
    assert result.parsed is None
    assert result.usage.prompt_tokens == 7
    assert result.usage.completion_tokens == 4
    assert result.usage.cost_usd is None  # 不可得，不伪造
    assert adapter.runs[0].status.value == "SUCCEEDED"
    assert adapter.runs[0].id == result.usage.run_id
    events = audit.list_events()
    store.close()
    assert [e.action for e in events] == ["model.called"]
    assert events[0].detail["run_status"] == "SUCCEEDED"


def test_structured_output_validated() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "task_id": "TASK-1",
                            "project_id": "PRJ-1",
                            "title": "实现登录",
                            "owner_role": "backend",
                            "status": "READY",
                            "schema_version": 1,
                        }
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8},
    }
    with _server({"body": payload}) as url:
        adapter = _adapter(url, registry=SchemaRegistry())
        result = adapter.complete(_req(schema="task"))
    assert result.parsed is not None
    assert result.parsed["task_id"] == "TASK-1"


def test_structured_output_schema_violation() -> None:
    body = {"choices": [{"message": {"content": '{"task_id": 1}'}}]}
    with _server({"body": body}) as url:
        adapter = _adapter(url, registry=SchemaRegistry())
        with pytest.raises(MalformedOutput, match="不满足协议"):
            adapter.complete(_req(schema="task"))
        assert adapter.runs[0].status.value == "FAILED"


def test_non_json_response_is_malformed() -> None:
    with _server({"body": "not-json"}) as url:
        with pytest.raises(MalformedOutput, match="非合法 JSON"):
            _adapter(url).complete(_req())


# ---------- 错误分类 ----------


@pytest.mark.parametrize(
    ("status", "error_type", "retryable"),
    [
        (429, RateLimited, True),
        (500, NetworkError, True),
        (503, NetworkError, True),
        (401, ModelError, False),
        (403, ModelError, False),
        (400, ModelError, False),
    ],
)
def test_http_errors_classified(status: int, error_type: type[ModelError], retryable: bool) -> None:
    with _server({"status": status, "body": {"error": "x"}}) as url:
        adapter = _adapter(url)
        with pytest.raises(error_type) as exc_info:
            adapter.complete(_req())
    assert exc_info.value.retryable is retryable
    assert adapter.runs[0].status.value == "FAILED"


def test_timeout_classified() -> None:
    body = {"choices": [{"message": {"content": "too late"}}]}
    with _server({"body": body, "delay": 0.6}) as url:
        adapter = _adapter(url, timeout=0.2)
        with pytest.raises(ModelTimeout):
            adapter.complete(_req())
    assert adapter.runs[0].status.value == "TIMED_OUT"


def test_connection_failure_is_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise urllib.error.URLError(ConnectionRefusedError(10061, "连接被拒绝"))

    monkeypatch.setattr("urllib.request.urlopen", _refuse)
    adapter = _adapter("http://127.0.0.1:9/v1")
    with pytest.raises(NetworkError):
        adapter.complete(_req())
    assert adapter.runs[0].status.value == "FAILED"


def test_api_key_not_leaked_in_error_or_audit(tmp_path: Path) -> None:
    audit, store = _audit(tmp_path)
    with _server({"status": 401, "body": {"error": "unauthorized"}}) as url:
        adapter = _adapter(url, audit=audit)
        with pytest.raises(ModelError) as exc_info:
            adapter.complete(_req())
    events = audit.list_events()
    store.close()
    assert "sk-test-secret-value" not in str(exc_info.value)
    assert all("sk-test-secret-value" not in json.dumps(e.detail) for e in events)
    assert [e.action for e in events] == ["model.failed"]


# ---------- 流式 ----------


def test_stream_yields_chunks_and_usage() -> None:
    chunks = [
        'data: {"choices":[{"delta":{"content":"你"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"好"}}]}\n\n',
        'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n',
        "data: [DONE]\n\n",
    ]
    with _server({"stream": True, "chunks": chunks}) as url:
        adapter = _adapter(url)
        received = list(adapter.stream(_req()))
    assert received == ["你", "好"]
    assert adapter.runs[0].status.value == "SUCCEEDED"
    assert adapter.usage_records[0].prompt_tokens == 3
    assert adapter.usage_records[0].completion_tokens == 2
    assert adapter.usage_records[0].cost_usd is None


def test_stream_cancel_stops_at_chunk_boundary() -> None:
    chunks = [
        'data: {"choices":[{"delta":{"content":"一"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"二"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"三"}}]}\n\n',
        "data: [DONE]\n\n",
    ]
    with _server({"stream": True, "chunks": chunks, "chunk_delay": 0.15}) as url:
        adapter = _adapter(url)
        iterator = adapter.stream(_req())
        assert next(iterator) == "一"
        adapter.cancel()
        with pytest.raises(ModelCancelled):
            next(iterator)
    assert adapter.runs[0].status.value == "CANCELLED"


def test_stream_malformed_chunk() -> None:
    chunks = ["data: not-json\n\n"]
    with _server({"stream": True, "chunks": chunks}) as url:
        adapter = _adapter(url)
        with pytest.raises(MalformedOutput):
            list(adapter.stream(_req()))
    assert adapter.runs[0].status.value == "FAILED"
