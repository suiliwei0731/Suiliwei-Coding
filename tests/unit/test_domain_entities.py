"""P1-1 单元测试：领域实体序列化往返与默认值。

依据规范 §14.3：实体必须含稳定 ID、创建/更新时间、状态、版本和审计字段。
"""

from __future__ import annotations

from core.domain import (
    Artifact,
    ArtifactKind,
    Defect,
    Project,
    ProjectStatus,
    Review,
    ReviewFinding,
    ReviewVerdict,
    Severity,
    Task,
    TaskStatus,
    TestReport,
    TestStatus,
    new_id,
    utc_now,
)


def test_new_id_format_and_unique() -> None:
    a, b = new_id("TASK"), new_id("TASK")
    assert a.startswith("TASK-") and len(a) == len("TASK-") + 12
    assert a != b


def test_utc_now_iso8601_z_suffix() -> None:
    ts = utc_now()
    assert ts.endswith("Z") and "T" in ts


def test_task_defaults_and_roundtrip() -> None:
    task = Task(id=new_id("TASK"), project_id="PRJ-1", title="实现登录 API", owner_role="backend")
    data = task.to_dict()
    assert data["task_id"] == task.id
    assert data["status"] == "DRAFT"  # 枚举序列化为字符串值
    assert data["version"] == 1 and data["schema_version"] == 1
    assert data["created_at"] and data["updated_at"]
    assert data["dependencies"] == [] and data["acceptance_criteria"] == []

    restored = Task.from_dict(data)
    assert restored == task


def test_task_from_dict_minimal_payload() -> None:
    minimal = {"task_id": "TASK-abc123", "title": "t", "owner_role": "backend"}
    task = Task.from_dict(minimal)
    assert task.status == TaskStatus.DRAFT
    assert task.project_id == ""


def test_review_with_findings_roundtrip() -> None:
    review = Review(
        id="REV-abc123",
        task_id="TASK-abc123",
        reviewer_role="reviewer",
        verdict=ReviewVerdict.CHANGES_REQUIRED,
        findings=[
            ReviewFinding(
                severity=Severity.BLOCKER,
                file_path="src/auth.py",
                line=42,
                impact="登录可被绕过",
                reproduction="传空 token",
                suggestion="校验 token 非空",
                blocking=True,
            )
        ],
    )
    restored = Review.from_dict(review.to_dict())
    assert restored == review
    assert restored.findings[0].severity == Severity.BLOCKER
    assert restored.findings[0].line == 42


def test_test_report_roundtrip_and_unverified() -> None:
    report = TestReport(
        id="TRUN-abc123",
        task_id="TASK-abc123",
        tool="pytest",
        command="pytest tests/unit",
        status=TestStatus.PASSED,
        exit_code=0,
        total=5,
        passed=5,
    )
    restored = TestReport.from_dict(report.to_dict())
    assert restored == report

    unverified = TestReport(
        id="TRUN-def456",
        task_id="TASK-abc123",
        tool="mypy",
        command="",
        status=TestStatus.UNVERIFIED,
        unverified_reason="工具未安装，可复现命令：pip install mypy",
    )
    assert unverified.to_dict()["status"] == "UNVERIFIED"
    assert unverified.exit_code is None


def test_defect_and_artifact_roundtrip() -> None:
    defect = Defect(
        id="DEF-abc123",
        task_id="TASK-abc123",
        severity=Severity.CRITICAL,
        title="空指针",
    )
    assert Defect.from_dict(defect.to_dict()) == defect

    artifact = Artifact(
        id="ART-abc123",
        project_id="PRJ-1",
        kind=ArtifactKind.TEST_REPORT,
        path="PRJ-1/TASK-abc123/report.html",
        sha256="a" * 64,
        size_bytes=1024,
    )
    restored = Artifact.from_dict(artifact.to_dict())
    assert restored == artifact
    assert restored.task_id is None


def test_project_roundtrip() -> None:
    project = Project(id="PRJ-abc123", name="示例项目", status=ProjectStatus.ACTIVE)
    restored = Project.from_dict(project.to_dict())
    assert restored == project
    assert restored.status == ProjectStatus.ACTIVE
