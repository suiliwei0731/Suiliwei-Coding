"""重试与预算控制（规范 §9.3 循环控制与防失控）。

规则（§9.3 与 config/models.yaml usage.budget_limits）：
- 每任务最大重试次数 max_retries_per_task（默认 3）、时间预算 task_timeout_seconds（默认 1800）；
- 失败后先记根因再决策，不无条件重复同一操作；
- 相同错误签名连续出现 → 触发升级（escalate）；不可重试错误立即升级；
- 超限/超时 → 转 BLOCKED 并生成阻塞报告，如实说明，不得假装任务完成。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from core.domain.entities import utc_now

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


@dataclass(frozen=True)
class RetryPolicy:
    """重试/时间预算（对应 config/models.yaml usage.budget_limits）。"""

    max_retries_per_task: int = 3
    task_timeout_seconds: int = 1800

    @classmethod
    def from_config(cls, path: Path | str | None = None) -> RetryPolicy:
        """从 config/models.yaml 读取预算；缺文件或缺键时用默认值。"""
        config_path = Path(path) if path else DEFAULT_CONFIG
        if not config_path.is_file():
            return cls()
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        limits = (data.get("usage") or {}).get("budget_limits") or {}
        return cls(
            max_retries_per_task=int(limits.get("max_retries_per_task", 3)),
            task_timeout_seconds=int(limits.get("task_timeout_seconds", 1800)),
        )


@dataclass(frozen=True)
class FailureRecord:
    """一次失败记录：第几次、错误签名、根因、时间（§9.3 记录尝试次数与失败原因）。"""

    attempt: int
    signature: str
    root_cause: str
    at: str


@dataclass(frozen=True)
class RetryDecision:
    """重试决策：retry=允许重试；escalate/blocked=停止并转 BLOCKED。"""

    action: str  # "retry" / "escalate" / "blocked"
    reason: str
    attempt: int


class RetryBudgetExhausted(Exception):
    """重试或时间预算耗尽/触发升级；report 为阻塞报告（§9.3 达到停止条件须清晰报告）。"""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__(report["reason"])
        self.report = report


def error_signature(error: Exception) -> str:
    """错误签名：类型名 + 归一化消息（数字折叠为 N），用于相同错误连续检测。"""
    normalized = re.sub(r"\d+", "N", str(error))
    return f"{type(error).__name__}:{normalized[:80]}"


class RetryController:
    """单任务的重试预算与升级控制。"""

    def __init__(self, policy: RetryPolicy | None = None) -> None:
        self._policy = policy or RetryPolicy()
        self._failures: list[FailureRecord] = []

    @property
    def policy(self) -> RetryPolicy:
        return self._policy

    @property
    def failures(self) -> tuple[FailureRecord, ...]:
        return tuple(self._failures)

    @property
    def attempts(self) -> int:
        return len(self._failures)

    def record_failure(self, error: Exception, root_cause: str) -> RetryDecision:
        """记录失败并给出决策。root_cause 必填：先根因分析再重试（§9.3）。"""
        if not root_cause.strip():
            raise ValueError("必须先记录根因再决策重试（§9.3）")
        self._failures.append(
            FailureRecord(
                attempt=len(self._failures) + 1,
                signature=error_signature(error),
                root_cause=root_cause,
                at=utc_now(),
            )
        )
        n = len(self._failures)
        if n > self._policy.max_retries_per_task:
            return RetryDecision(
                "blocked",
                f"重试超限（{n - 1}/{self._policy.max_retries_per_task} 次已用尽），转 BLOCKED",
                n,
            )
        if not getattr(error, "retryable", True):
            return RetryDecision("escalate", f"不可重试错误 {type(error).__name__}，立即升级", n)
        if n >= 2 and self._failures[-1].signature == self._failures[-2].signature:
            return RetryDecision(
                "escalate", f"相同错误连续出现（{self._failures[-1].signature}），触发升级", n
            )
        return RetryDecision("retry", "允许重试", n)

    def blocking_report(self, task_id: str, *, reason: str) -> dict[str, Any]:
        """阻塞报告：如实记录尝试与根因，不假装任务完成（§9.3）。"""
        return {
            "task_id": task_id,
            "outcome": "BLOCKED",
            "reason": reason,
            "attempts": self.attempts,
            "failures": [asdict(f) for f in self._failures],
            "policy": asdict(self._policy),
            "generated_at": utc_now(),
            "note": "达到停止条件：如实报告，未假装任务完成（§9.3）",
        }


def execute_with_retry[F](
    fn: Callable[[], F],
    *,
    task_id: str,
    policy: RetryPolicy | None = None,
    analyze: Callable[[Exception], str] | None = None,
) -> F:
    """在重试/时间预算内执行 fn；耗尽或升级抛 RetryBudgetExhausted（附阻塞报告）。

    每次失败先经 analyze 提取根因（缺省为异常消息），再按决策重试或停止；
    时间预算在每次重试前检查，超时即停止。保证不无限循环。
    """
    controller = RetryController(policy)
    started = time.monotonic()
    while True:
        try:
            return fn()
        except Exception as exc:
            root_cause = analyze(exc) if analyze else str(exc)
            decision = controller.record_failure(exc, root_cause)
            if decision.action != "retry":
                raise RetryBudgetExhausted(
                    controller.blocking_report(task_id, reason=decision.reason)
                ) from exc
            if time.monotonic() - started >= controller.policy.task_timeout_seconds:
                raise RetryBudgetExhausted(
                    controller.blocking_report(task_id, reason="任务时间预算耗尽（超时）")
                ) from exc
