"""协议 JSON Schema 的正反例校验（实施计划 P1-1/P2-1 完成标准）。

正例：与实体 to_dict() 对齐的最小合法载荷必须通过。
反例：缺必填、非法枚举、非法 ID 模式、附加字段、以及条件约束
（CHANGES_REQUIRED 必须有 findings；UNVERIFIED 必须有原因）必须被拒绝。
"""

from __future__ import annotations

import pytest

from core.domain import SchemaRegistry, SchemaValidationError


@pytest.fixture(scope="module")
def registry() -> SchemaRegistry:
    return SchemaRegistry()


def test_registry_loads_all_schemas(registry: SchemaRegistry) -> None:
    assert registry.names == (
        "artifact",
        "defect",
        "requirement",
        "review",
        "task",
        "test-report",
    )


VALID_TASK = {
    "schema_version": 1,
    "task_id": "TASK-abc123",
    "project_id": "PRJ-1",
    "title": "实现用户登录 API",
    "owner_role": "backend-developer",
    "requirement_ids": ["REQ-001"],
    "inputs": ["docs/api-contract.yaml"],
    "dependencies": [],
    "allowed_paths": ["src/auth/", "tests/auth/"],
    "acceptance_criteria": ["合法凭证可成功登录"],
    "verification_commands": ["pytest tests/auth"],
    "status": "READY",
}

VALID_REVIEW = {
    "schema_version": 1,
    "review_id": "REV-abc123",
    "task_id": "TASK-abc123",
    "reviewer_role": "code-reviewer",
    "verdict": "CHANGES_REQUIRED",
    "findings": [
        {
            "severity": "BLOCKER",
            "file_path": "src/auth.py",
            "line": 42,
            "impact": "登录可被绕过",
            "suggestion": "校验 token 非空",
            "blocking": True,
        }
    ],
}

VALID_TEST_REPORT = {
    "schema_version": 1,
    "report_id": "TRUN-abc123",
    "task_id": "TASK-abc123",
    "tool": "pytest",
    "tool_version": "8.3.3",
    "command": "pytest tests/auth",
    "exit_code": 0,
    "status": "PASSED",
    "total": 3,
    "passed": 3,
}

VALID_DEFECT = {
    "schema_version": 1,
    "defect_id": "DEF-abc123",
    "task_id": "TASK-abc123",
    "severity": "CRITICAL",
    "title": "空凭证未拦截",
    "status": "OPEN",
    "source": "TEST",
}

VALID_ARTIFACT = {
    "schema_version": 1,
    "artifact_id": "ART-abc123",
    "project_id": "PRJ-1",
    "kind": "TEST_REPORT",
    "path": "PRJ-1/TASK-abc123/report.html",
    "sha256": "a" * 64,
    "size_bytes": 2048,
}


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("task", VALID_TASK),
        ("review", VALID_REVIEW),
        ("test-report", VALID_TEST_REPORT),
        ("defect", VALID_DEFECT),
        ("artifact", VALID_ARTIFACT),
    ],
)
def test_valid_payloads_pass(registry: SchemaRegistry, name: str, payload: dict) -> None:
    registry.validate(name, payload)  # 不抛异常即通过


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("task", {**VALID_TASK, "status": "DONE"}),  # 非法状态枚举
        ("task", {k: v for k, v in VALID_TASK.items() if k != "title"}),  # 缺必填
        ("task", {**VALID_TASK, "task_id": "abc"}),  # ID 模式非法
        ("task", {**VALID_TASK, "extra": 1}),  # 附加字段
        ("review", {**VALID_REVIEW, "findings": []}),  # CHANGES_REQUIRED 必须有 findings
        ("review", {**VALID_REVIEW, "findings": [{}]}),  # finding 缺必填字段
        ("test-report", {**VALID_TEST_REPORT, "status": "UNVERIFIED"}),  # 缺 unverified_reason
        ("test-report", {**VALID_TEST_REPORT, "status": "UNKNOWN"}),  # 非法四态外枚举
        ("defect", {**VALID_DEFECT, "severity": "HIGH"}),  # 严重度必须为 5 级量表
        ("artifact", {**VALID_ARTIFACT, "sha256": "xyz"}),  # 校验信息非法
        ("artifact", {**VALID_ARTIFACT, "size_bytes": -1}),
    ],
)
def test_invalid_payloads_rejected(registry: SchemaRegistry, name: str, payload: dict) -> None:
    with pytest.raises(SchemaValidationError):
        registry.validate(name, payload)


def test_review_pass_without_findings_is_valid(registry: SchemaRegistry) -> None:
    registry.validate("review", {**VALID_REVIEW, "verdict": "PASS", "findings": []})


def test_unverified_with_reason_is_valid(registry: SchemaRegistry) -> None:
    registry.validate(
        "test-report",
        {
            **VALID_TEST_REPORT,
            "status": "UNVERIFIED",
            "exit_code": None,
            "unverified_reason": "CI 镜像缺少工具，可复现命令：mypy core",
        },
    )


def test_unknown_schema_name_rejected(registry: SchemaRegistry) -> None:
    with pytest.raises(SchemaValidationError):
        registry.validate("no-such-schema", {})
