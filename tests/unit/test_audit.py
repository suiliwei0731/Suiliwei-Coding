"""P1-5 单元测试：秘密脱敏过滤器与审计日志（规范 §17/§18）。

红线：注入含密钥样例，落库与 JSONL 导出均不得出现明文。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.audit import REDACTED, AuditLogger, redact_text, redact_value
from core.domain import AuditEvent, new_id
from core.orchestrator import StateStore

SECRET_SAMPLES = {
    "openai": "sk-abc123def456ghi789",
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "github": "ghp_abcdefghijklmnopqrstuvwxyz1234",
    "password": "password=Sup3rS3cret!",
    "api_key": "api_key: my-secret-key-12345",
    "bearer": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9",
    "private_key": (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7\n-----END RSA PRIVATE KEY-----"
    ),
}


def test_each_secret_pattern_redacted() -> None:
    for name, secret in SECRET_SAMPLES.items():
        result = redact_text(f"前缀 {secret} 后缀")
        assert secret not in result, f"{name} 未脱敏"
        assert REDACTED in result, f"{name} 未替换"


def test_normal_text_untouched() -> None:
    text = "任务 TASK-1 状态 READY，命令 pytest 退出码 0"
    assert redact_text(text) == text


def test_redact_value_nested() -> None:
    payload = {
        "cmd": ["curl", "-H", "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9"],
        "env": {"API_KEY": "sk-abc123def456"},
        "count": 3,
    }
    safe = redact_value(payload)
    assert "eyJhbGciOiJIUzI1NiJ9" not in json.dumps(safe, ensure_ascii=False)
    assert "sk-abc123def456" not in json.dumps(safe, ensure_ascii=False)
    assert safe["count"] == 3


def test_audit_logger_redacts_store_and_jsonl(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        logger = AuditLogger(store, tmp_path / "logs")
        event = logger.log(
            "tool.called",
            "controller",
            project_id="PRJ-1",
            task_id="TASK-1",
            detail={
                "command": "deploy --token ghp_abcdefghijklmnopqrstuvwxyz1234",
                "note": "password=Sup3rS3cret!",
                "exit_code": 1,
            },
        )
        assert event.detail["exit_code"] == 1  # 非字符串字段不受影响

        stored = store.load("audit_event", event.id)
        jsonl = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8")
        for sink in (json.dumps(stored, ensure_ascii=False), jsonl):
            assert "ghp_abcdefghijklmnopqrstuvwxyz1234" not in sink
            assert "Sup3rS3cret!" not in sink
            assert REDACTED in sink
        assert json.loads(jsonl.strip())["event_id"] == event.id


def test_audit_logger_list_events(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        logger = AuditLogger(store)  # 无 JSONL 导出
        logger.log("task.status_changed", "controller", detail={"to": "READY"})
        logger.record(AuditEvent(id=new_id("EVT"), action="approval.decided", actor="user"))
        events = logger.list_events()
        assert [e.action for e in events] == ["task.status_changed", "approval.decided"]
