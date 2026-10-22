"""OpenAI 兼容 Chat Completions 适配器（实施计划 P4-1；ADR-002、config/models.yaml）。

红线落实：
- profile 未启用（``enabled: false``）或环境变量缺失 → 立即 :class:`ModelError`，
  绝不返回伪造的成功结果；
- 密钥只经环境变量读取，不写入异常信息、审计或日志（policies.yaml secrets 节）；
- 错误按类型分类：401/403 鉴权失败不可重试；429 限流；5xx/连接失败网络错误；
  请求超时超时错误；响应非 JSON/结构不符格式错误（均可由编排层按 retryable 决策）；
- 结构化输出经 SchemaRegistry 校验，失败抛 MalformedOutput，不静默降级；
- 用量如实记录：上游未返回 usage 时为 0，成本不可得为 None，不伪造。

覆盖 ``complete``（一次性补全）与 ``stream``（SSE 流式），两者均支持取消；
每次调用生成 :class:`AgentRun` 记录（SUCCEEDED/FAILED/TIMED_OUT/CANCELLED），
并在注入 AuditLogger 时写 ``model.called`` / ``model.failed`` 审计。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from adapters.models.base import (
    CompletionRequest,
    CompletionResult,
    MalformedOutput,
    ModelCancelled,
    ModelError,
    ModelTimeout,
    NetworkError,
    RateLimited,
)
from core.audit.logger import AuditLogger
from core.domain.entities import AgentRun, UsageRecord, new_id, utc_now
from core.domain.enums import AgentRunStatus
from core.domain.validation import SchemaRegistry, SchemaValidationError

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
DEFAULT_PROFILE = "cloud-openai-compatible"
CHAT_SUFFIX = "/chat/completions"


@dataclass(frozen=True, kw_only=True)
class ModelProfile:
    """模型接入 profile（config/models.yaml ``profiles`` 的一节）。"""

    name: str
    provider: str = "openai-compatible"
    enabled: bool = False
    base_url_env: str = "AI_DEV_PLATFORM_MODEL_BASE_URL"
    api_key_env: str = "AI_DEV_PLATFORM_MODEL_API_KEY"
    model_env: str = "AI_DEV_PLATFORM_MODEL_NAME"
    request_timeout_seconds: float = 60.0
    max_retries: int = 3
    enforce_json_schema: bool = True

    @classmethod
    def from_config(cls, name: str | None = None, path: Path | str | None = None) -> ModelProfile:
        """从 config/models.yaml 读取指定 profile；缺失时抛 ModelError。"""
        config_path = Path(path) if path else DEFAULT_CONFIG
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise ModelError(f"模型配置不可读: {config_path}（{exc}）") from exc
        except yaml.YAMLError as exc:
            raise ModelError(f"模型配置 YAML 解析失败: {config_path}（{exc}）") from exc
        profile_name = name or str(data.get("default_profile") or DEFAULT_PROFILE)
        profiles = data.get("profiles") or {}
        entry = profiles.get(profile_name)
        if not isinstance(entry, dict):
            raise ModelError(f"模型配置缺少 profile: {profile_name!r}")
        structured = entry.get("structured_output") or {}
        return cls(
            name=profile_name,
            provider=str(entry.get("provider", "openai-compatible")),
            enabled=bool(entry.get("enabled", False)),
            base_url_env=str(entry.get("base_url_env", "AI_DEV_PLATFORM_MODEL_BASE_URL")),
            api_key_env=str(entry.get("api_key_env", "AI_DEV_PLATFORM_MODEL_API_KEY")),
            model_env=str(entry.get("model_env", "AI_DEV_PLATFORM_MODEL_NAME")),
            request_timeout_seconds=float(entry.get("request_timeout_seconds", 60)),
            max_retries=int(entry.get("max_retries", 3)),
            enforce_json_schema=bool(structured.get("enforce_json_schema", True)),
        )


@dataclass(frozen=True)
class _Endpoint:
    """解析后的连接参数（密钥仅在此内存对象中短暂存在，不落日志）。"""

    base_url: str
    api_key: str
    model: str


class OpenAICompatibleAdapter:
    """OpenAI 兼容模型适配器：真实 HTTP 调用 + 错误分类 + 用量/运行记录。"""

    def __init__(
        self,
        profile: ModelProfile,
        *,
        registry: SchemaRegistry | None = None,
        audit: AuditLogger | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._profile = profile
        self._registry = registry
        self._audit = audit
        self._env: Mapping[str, str] = env if env is not None else os.environ
        self._cancelled = threading.Event()
        self.usage_records: list[UsageRecord] = []
        self.runs: list[AgentRun] = []

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # ---------- 取消 ----------

    def cancel(self) -> None:
        """置位取消标记；进行中的流式读取在下一块边界停止并抛 ModelCancelled。"""
        self._cancelled.set()

    def reset_cancel(self) -> None:
        """清除取消标记（供新一轮执行复用适配器实例）。"""
        self._cancelled.clear()

    # ---------- 补全 ----------

    def complete(self, request: CompletionRequest) -> CompletionResult:
        run = self._begin_run(request)
        started = time.monotonic()
        try:
            endpoint = self._resolve_endpoint()
            self._raise_if_cancelled()
            payload = self._payload(request, endpoint.model, stream=False)
            data = self._post_json(endpoint, payload)
            content = self._extract_content(data)
            usage = self._record_usage(data.get("usage"), request, run)
            parsed = self._parse_structured(request, content)
            self._finish_run(run, AgentRunStatus.SUCCEEDED)
            self._audit_call("model.called", request, run, started, usage)
            return CompletionResult(content=content, parsed=parsed, usage=usage)
        except ModelError as exc:
            self._fail_run(run, request, exc, started)
            raise

    def stream(self, request: CompletionRequest) -> Iterator[str]:
        """流式补全：逐块产出文本增量；结构化请求在流结束后统一校验。

        取消或错误时停止迭代并抛对应 :class:`ModelError` 子类。
        """
        run = self._begin_run(request)
        started = time.monotonic()
        chunks: list[str] = []
        usage_payload: dict[str, Any] | None = None
        try:
            endpoint = self._resolve_endpoint()
            self._raise_if_cancelled()
            payload = self._payload(request, endpoint.model, stream=True)
            request_obj = urllib.request.Request(
                endpoint.base_url + CHAT_SUFFIX,
                data=json.dumps(payload).encode("utf-8"),
                headers=self._headers(endpoint.api_key),
                method="POST",
            )
            with self._open(request_obj) as response:
                for raw in response:
                    self._raise_if_cancelled()
                    text, usage_chunk = self._consume_sse_line(raw)
                    if usage_chunk is not None:
                        usage_payload = usage_chunk
                    if text:
                        chunks.append(text)
                        yield text
            self._raise_if_cancelled()
            content = "".join(chunks)
            usage = self._record_usage(usage_payload, request, run)
            self._parse_structured(request, content)
            self._finish_run(run, AgentRunStatus.SUCCEEDED)
            self._audit_call("model.called", request, run, started, usage)
        except ModelError as exc:
            self._fail_run(run, request, exc, started)
            raise

    # ---------- 配置解析 ----------

    def _resolve_endpoint(self) -> _Endpoint:
        profile = self._profile
        if not profile.enabled:
            raise ModelError(
                f"模型 profile {profile.name!r} 未启用（config/models.yaml enabled=false）；"
                "禁止真实调用，也不伪造成功"
            )
        base_url = str(self._env.get(profile.base_url_env) or "").strip()
        api_key = str(self._env.get(profile.api_key_env) or "").strip()
        model = str(self._env.get(profile.model_env) or "").strip()
        missing = [
            env_name
            for env_name, value in (
                (profile.base_url_env, base_url),
                (profile.api_key_env, api_key),
                (profile.model_env, model),
            )
            if not value
        ]
        if missing:
            raise ModelError(
                f"模型 profile {profile.name!r} 未配置环境变量: {', '.join(missing)}；"
                "未配置时明确报错，不伪造结果"
            )
        if not base_url.startswith(("http://", "https://")):
            raise ModelError(f"模型 base_url 非法（须 http/https）: {profile.base_url_env}")
        return _Endpoint(base_url=base_url.rstrip("/"), api_key=api_key, model=model)

    def _payload(self, request: CompletionRequest, model: str, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if request.schema is not None and self._profile.enforce_json_schema:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    # ---------- HTTP ----------

    def _open(self, request_obj: urllib.request.Request) -> Any:
        """发起请求并返回响应对象；按错误类型分类，密钥不出现在消息中。"""
        try:
            return urllib.request.urlopen(
                request_obj, timeout=self._profile.request_timeout_seconds
            )
        except urllib.error.HTTPError as exc:
            raise self._classify_http_error(exc.code) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise ModelTimeout(
                    f"模型请求超时（{self._profile.request_timeout_seconds}s）"
                ) from exc
            raise NetworkError(f"模型请求网络错误: {reason}") from exc
        except TimeoutError as exc:
            raise ModelTimeout(f"模型请求超时（{self._profile.request_timeout_seconds}s）") from exc
        except OSError as exc:
            raise NetworkError(f"模型请求网络错误: {exc}") from exc

    def _post_json(self, endpoint: _Endpoint, payload: dict[str, Any]) -> dict[str, Any]:
        request_obj = urllib.request.Request(
            endpoint.base_url + CHAT_SUFFIX,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(endpoint.api_key),
            method="POST",
        )
        with self._open(request_obj) as response:
            body = response.read()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise MalformedOutput(f"模型响应非合法 JSON: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise MalformedOutput("模型响应须为 JSON 对象")
        return data

    def _classify_http_error(self, code: int) -> ModelError:
        if code in (401, 403):
            return ModelError(
                f"模型鉴权失败（HTTP {code}）：请检查环境变量 {self._profile.api_key_env}"
            )
        if code == 429:
            return RateLimited(f"模型限流（HTTP 429，profile {self._profile.name}）")
        if code in (408, 504):
            return ModelTimeout(f"模型请求超时（HTTP {code}）")
        if code >= 500:
            return NetworkError(f"模型服务端错误（HTTP {code}）")
        return ModelError(f"模型请求失败（HTTP {code}）")

    # ---------- 响应解析 ----------

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise MalformedOutput("模型响应缺少 choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise MalformedOutput("模型响应 choices 条目格式非法")
        message = first.get("message")
        if not isinstance(message, dict):
            raise MalformedOutput("模型响应缺少 message")
        content = message.get("content")
        if content is None:
            return ""
        if not isinstance(content, str):
            raise MalformedOutput("模型响应 message.content 须为字符串")
        return content

    def _consume_sse_line(self, raw: bytes | str) -> tuple[str, dict[str, Any] | None]:
        """解析一行 SSE：返回 (文本增量, usage 或 None)。注释/空行忽略。"""
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            return "", None
        data_str = line[len("data:") :].strip()
        if data_str == "[DONE]":
            return "", None
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError as exc:
            raise MalformedOutput(f"流式响应块非合法 JSON: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise MalformedOutput("流式响应块须为 JSON 对象")
        error = event.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise NetworkError(f"模型流式响应错误: {message}")
        usage = event.get("usage")
        usage_dict = usage if isinstance(usage, dict) else None
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            return "", usage_dict
        first = choices[0]
        delta = first.get("delta") if isinstance(first, dict) else None
        content = delta.get("content") if isinstance(delta, dict) else None
        return (content if isinstance(content, str) else ""), usage_dict

    # ---------- 结构化输出 ----------

    def _parse_structured(self, request: CompletionRequest, content: str) -> dict[str, Any] | None:
        if request.schema is None:
            return None
        if self._registry is None:
            raise ModelError("请求结构化输出但未提供 SchemaRegistry")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise MalformedOutput(f"输出非合法 JSON: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise MalformedOutput("结构化输出须为 JSON 对象")
        try:
            self._registry.validate(request.schema, data)
        except SchemaValidationError as exc:
            raise MalformedOutput(f"输出不满足协议 {request.schema!r}: {exc}") from exc
        return data

    # ---------- 运行/用量/审计 ----------

    def _begin_run(self, request: CompletionRequest) -> AgentRun:
        run = AgentRun(
            id=new_id("RUN"),
            task_id=request.task_id or "",
            role=request.role,
            model_profile=self._profile.name,
        )
        self.runs.append(run)
        return run

    @staticmethod
    def _finish_run(run: AgentRun, status: AgentRunStatus) -> None:
        run.status = status
        run.finished_at = utc_now()
        run.updated_at = run.finished_at

    def _fail_run(
        self,
        run: AgentRun,
        request: CompletionRequest,
        exc: ModelError,
        started: float,
    ) -> None:
        status = (
            AgentRunStatus.TIMED_OUT
            if isinstance(exc, ModelTimeout)
            else AgentRunStatus.CANCELLED
            if isinstance(exc, ModelCancelled)
            else AgentRunStatus.FAILED
        )
        run.status = status
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = utc_now()
        run.updated_at = run.finished_at
        self._audit_log(
            "model.failed",
            request,
            run,
            started,
            extra={
                "error_type": type(exc).__name__,
                "retryable": exc.retryable,
                "error": str(exc),
            },
        )

    def _record_usage(
        self,
        payload: Any,
        request: CompletionRequest,
        run: AgentRun,
    ) -> UsageRecord:
        usage = payload if isinstance(payload, dict) else {}
        record = UsageRecord(
            id=new_id("USAGE"),
            model_profile=self._profile.name,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cost_usd=None,  # 上游未提供成本，不伪造（红线）
            task_id=request.task_id,
            run_id=run.id,
        )
        self.usage_records.append(record)
        return record

    def _audit_call(
        self,
        action: str,
        request: CompletionRequest,
        run: AgentRun,
        started: float,
        usage: UsageRecord,
    ) -> None:
        self._audit_log(
            action,
            request,
            run,
            started,
            extra={
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cost_usd": usage.cost_usd,
            },
        )

    def _audit_log(
        self,
        action: str,
        request: CompletionRequest,
        run: AgentRun,
        started: float,
        *,
        extra: dict[str, Any],
    ) -> None:
        if self._audit is None:
            return
        detail: dict[str, Any] = {
            "model_profile": self._profile.name,
            "role": request.role,
            "run_id": run.id,
            "run_status": run.status.value,
            "duration_ms": int((time.monotonic() - started) * 1000),
            **extra,
        }
        self._audit.log(action, "模型适配器", task_id=request.task_id, detail=detail)

    def _raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise ModelCancelled("模型调用已取消")
