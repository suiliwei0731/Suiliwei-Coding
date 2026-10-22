"""秘密脱敏过滤器（规范 §17：日志/审计不得含未脱敏秘密）。

所有写入审计与日志的内容必须先经本模块处理；宁可误伤（过宽匹配）
也不得漏脱敏。命中片段统一替换为 ``***REDACTED***``。
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

# 顺序即优先级：私钥块整体替换优先于行内模式
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        REDACTED,
    ),
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), REDACTED),  # OpenAI 兼容密钥
    (re.compile(r"AKIA[0-9A-Z]{16}"), REDACTED),  # AWS Access Key ID
    (re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), REDACTED),  # GitHub token
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
            r"\b\s*[:=]\s*[^\s,;]+"
        ),
        rf"\1={REDACTED}",
    ),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), f"Bearer {REDACTED}"),
]


def redact_text(text: str) -> str:
    """对文本执行全部秘密模式替换。"""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any) -> Any:
    """递归脱敏：dict/list/tuple/str 逐层处理，其余类型原样返回。"""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value
