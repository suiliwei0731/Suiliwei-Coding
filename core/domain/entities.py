"""核心领域实体（规范 §14.3 的 Phase 1 子集）。

约定：
- 全部实体含稳定 ID、创建/更新时间、版本号、协议版本（§14.3/§14.4）。
- to_dict() 输出与 schemas/ 下 JSON Schema 对齐的载荷（枚举序列化为字符串值）；
  from_dict() 执行反向转换。序列化键名以各实体的 ``id_field`` 为准（如 Task → task_id）。
- 仅实现 Phase 1 实际使用的实体；Requirement/UserStory 等随 Phase 2 补充，不建空壳。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar
from uuid import uuid4

from core.domain.enums import (
    AgentRunStatus,
    ApprovalStatus,
    ArtifactKind,
    DefectSource,
    DefectStatus,
    ProjectStatus,
    ReviewVerdict,
    Severity,
    TaskStatus,
    TestStatus,
)


def utc_now() -> str:
    """ISO 8601 UTC 时间戳（毫秒精度，Z 后缀）。"""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    """生成稳定唯一 ID，形如 TASK-1a2b3c4d5e6f。"""
    return f"{prefix}-{uuid4().hex[:12]}"


def _to_json_value(value: Any) -> Any:
    """递归转换：枚举取其值，字典/列表逐层处理。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _to_json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_value(v) for v in value]
    return value


@dataclass(kw_only=True)
class Entity:
    """实体基类：稳定 ID、协议版本、数据版本、时间戳。"""

    id_field: ClassVar[str] = "id"

    id: str
    schema_version: int = 1
    version: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in asdict(self).items():
            out[self.id_field if key == "id" else key] = _to_json_value(value)
        return out


@dataclass(kw_only=True)
class Project(Entity):
    id_field: ClassVar[str] = "project_id"

    name: str
    status: ProjectStatus = ProjectStatus.CREATED
    workspace_path: str = ""  # 用户项目受控工作区（runtime/ 下）
    current_phase: str = "P1"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        return cls(
            id=str(data["project_id"]),
            name=str(data["name"]),
            status=ProjectStatus(data.get("status", "CREATED")),
            workspace_path=str(data.get("workspace_path", "")),
            current_phase=str(data.get("current_phase", "P1")),
            schema_version=int(data.get("schema_version", 1)),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(kw_only=True)
class Task(Entity):
    """任务（协议字段见规范 §14.4 与 schemas/task.schema.json）。"""

    id_field: ClassVar[str] = "task_id"

    project_id: str
    title: str
    owner_role: str
    requirement_ids: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)  # 前置任务 task_id 列表
    allowed_paths: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.DRAFT
    retry_count: int = 0
    blocked_reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=str(data["task_id"]),
            project_id=str(data.get("project_id", "")),
            title=str(data["title"]),
            owner_role=str(data["owner_role"]),
            requirement_ids=[str(x) for x in data.get("requirement_ids", [])],
            inputs=[str(x) for x in data.get("inputs", [])],
            dependencies=[str(x) for x in data.get("dependencies", [])],
            allowed_paths=[str(x) for x in data.get("allowed_paths", [])],
            acceptance_criteria=[str(x) for x in data.get("acceptance_criteria", [])],
            verification_commands=[str(x) for x in data.get("verification_commands", [])],
            status=TaskStatus(data.get("status", "DRAFT")),
            retry_count=int(data.get("retry_count", 0)),
            blocked_reason=data.get("blocked_reason"),
            schema_version=int(data.get("schema_version", 1)),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(kw_only=True)
class ReviewFinding:
    """单条审查意见（规范 §16.3：文件/行号、影响、复现条件、建议、是否阻断）。"""

    severity: Severity
    file_path: str
    impact: str
    suggestion: str
    blocking: bool
    line: int | None = None
    reproduction: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _to_json_value(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewFinding:
        return cls(
            severity=Severity(data["severity"]),
            file_path=str(data["file_path"]),
            impact=str(data["impact"]),
            suggestion=str(data["suggestion"]),
            blocking=bool(data["blocking"]),
            line=int(data["line"]) if data.get("line") is not None else None,
            reproduction=str(data.get("reproduction", "")),
        )


@dataclass(kw_only=True)
class Review(Entity):
    id_field: ClassVar[str] = "review_id"

    task_id: str
    reviewer_role: str
    verdict: ReviewVerdict
    findings: list[ReviewFinding] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Review:
        return cls(
            id=str(data["review_id"]),
            task_id=str(data["task_id"]),
            reviewer_role=str(data["reviewer_role"]),
            verdict=ReviewVerdict(data["verdict"]),
            findings=[ReviewFinding.from_dict(f) for f in data.get("findings", [])],
            schema_version=int(data.get("schema_version", 1)),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(kw_only=True)
class TestReport(Entity):
    """机器可读的测试/检查报告（§7.4 证据字段 + §16.5 四态）。"""

    id_field: ClassVar[str] = "report_id"
    __test__: ClassVar[bool] = False  # 非 pytest 测试类，避免收集告警

    task_id: str
    tool: str  # 工具名（pytest/ruff/mypy/...）
    command: str  # 实际执行命令（未执行时为空串）
    status: TestStatus
    tool_version: str = ""
    exit_code: int | None = None  # 未执行为 None
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    not_run: int = 0
    artifact_path: str = ""  # 报告产物相对 runtime/artifacts 的路径
    unverified_reason: str = ""  # status=UNVERIFIED 时必填（红线 2）
    started_at: str = ""
    finished_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestReport:
        return cls(
            id=str(data["report_id"]),
            task_id=str(data["task_id"]),
            tool=str(data["tool"]),
            command=str(data.get("command", "")),
            status=TestStatus(data["status"]),
            tool_version=str(data.get("tool_version", "")),
            exit_code=int(data["exit_code"]) if data.get("exit_code") is not None else None,
            total=int(data.get("total", 0)),
            passed=int(data.get("passed", 0)),
            failed=int(data.get("failed", 0)),
            skipped=int(data.get("skipped", 0)),
            not_run=int(data.get("not_run", 0)),
            artifact_path=str(data.get("artifact_path", "")),
            unverified_reason=str(data.get("unverified_reason", "")),
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
            schema_version=int(data.get("schema_version", 1)),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(kw_only=True)
class Defect(Entity):
    id_field: ClassVar[str] = "defect_id"

    task_id: str
    severity: Severity
    title: str
    status: DefectStatus = DefectStatus.OPEN
    source: DefectSource = DefectSource.MANUAL
    description: str = ""
    root_cause: str = ""
    evidence: list[str] = field(default_factory=list)  # 证据产物 ID/路径引用

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Defect:
        return cls(
            id=str(data["defect_id"]),
            task_id=str(data["task_id"]),
            severity=Severity(data["severity"]),
            title=str(data["title"]),
            status=DefectStatus(data.get("status", "OPEN")),
            source=DefectSource(data.get("source", "MANUAL")),
            description=str(data.get("description", "")),
            root_cause=str(data.get("root_cause", "")),
            evidence=[str(x) for x in data.get("evidence", [])],
            schema_version=int(data.get("schema_version", 1)),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(kw_only=True)
class Artifact(Entity):
    """文件产物引用（ADR-001：DB 存路径与校验信息，正文在 runtime/artifacts）。"""

    id_field: ClassVar[str] = "artifact_id"

    project_id: str
    kind: ArtifactKind
    path: str  # 相对 runtime/artifacts 的路径
    sha256: str
    size_bytes: int
    task_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Artifact:
        return cls(
            id=str(data["artifact_id"]),
            project_id=str(data["project_id"]),
            kind=ArtifactKind(data["kind"]),
            path=str(data["path"]),
            sha256=str(data["sha256"]),
            size_bytes=int(data["size_bytes"]),
            task_id=str(data["task_id"]) if data.get("task_id") else None,
            schema_version=int(data.get("schema_version", 1)),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(kw_only=True)
class Approval(Entity):
    """审批记录（§17：高风险操作明确授权，"AI 自己同意"不构成授权）。"""

    id_field: ClassVar[str] = "approval_id"

    project_id: str
    subject: str  # 审批对象描述（如 "cancel:TASK-xxx"、"gate:G-DEPS 豁免"）
    requested_by: str  # 发起方（角色/用户）
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    reason: str = ""
    expires_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Approval:
        return cls(
            id=str(data["approval_id"]),
            project_id=str(data["project_id"]),
            subject=str(data["subject"]),
            requested_by=str(data["requested_by"]),
            status=ApprovalStatus(data.get("status", "PENDING")),
            decided_by=str(data["decided_by"]) if data.get("decided_by") else None,
            reason=str(data.get("reason", "")),
            expires_at=str(data["expires_at"]) if data.get("expires_at") else None,
            schema_version=int(data.get("schema_version", 1)),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(kw_only=True)
class AgentRun(Entity):
    """一次角色执行记录（§2）：输入、输出、模型调用与证据。"""

    id_field: ClassVar[str] = "run_id"

    task_id: str
    role: str
    model_profile: str  # config/models.yaml 的 profile 名（Phase 1 为 simulation）
    status: AgentRunStatus = AgentRunStatus.RUNNING
    skill_versions: dict[str, str] = field(default_factory=dict)
    input_refs: list[str] = field(default_factory=list)  # 输入产物引用
    output_refs: list[str] = field(default_factory=list)  # 输出产物引用
    error: str = ""
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentRun:
        return cls(
            id=str(data["run_id"]),
            task_id=str(data["task_id"]),
            role=str(data["role"]),
            model_profile=str(data["model_profile"]),
            status=AgentRunStatus(data.get("status", "RUNNING")),
            skill_versions={str(k): str(v) for k, v in data.get("skill_versions", {}).items()},
            input_refs=[str(x) for x in data.get("input_refs", [])],
            output_refs=[str(x) for x in data.get("output_refs", [])],
            error=str(data.get("error", "")),
            started_at=str(data.get("started_at") or utc_now()),
            finished_at=str(data["finished_at"]) if data.get("finished_at") else None,
            schema_version=int(data.get("schema_version", 1)),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(kw_only=True)
class UsageRecord(Entity):
    """模型用量/成本记录（§18、D10：第一版记录不强制限额）。"""

    id_field: ClassVar[str] = "usage_id"

    model_profile: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None  # 不可得时为 None，不伪造
    task_id: str | None = None
    run_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UsageRecord:
        return cls(
            id=str(data["usage_id"]),
            model_profile=str(data["model_profile"]),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            cost_usd=float(data["cost_usd"]) if data.get("cost_usd") is not None else None,
            task_id=str(data["task_id"]) if data.get("task_id") else None,
            run_id=str(data["run_id"]) if data.get("run_id") else None,
            schema_version=int(data.get("schema_version", 1)),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )


@dataclass(kw_only=True)
class AuditEvent(Entity):
    """审计事件（§18 字段：状态变化、角色/模型/技能版本、输入输出引用、

    工具调用命令/退出码/耗时、重试与失败原因、批准与取消、用量成本）。
    写入前必须经 core/audit 脱敏过滤器处理（§17）。
    """

    id_field: ClassVar[str] = "event_id"

    action: str  # 如 task.status_changed / tool.called / approval.decided
    actor: str  # 触发方（controller / 角色 / 用户）
    project_id: str | None = None
    task_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEvent:
        return cls(
            id=str(data["event_id"]),
            action=str(data["action"]),
            actor=str(data["actor"]),
            project_id=str(data["project_id"]) if data.get("project_id") else None,
            task_id=str(data["task_id"]) if data.get("task_id") else None,
            detail=dict(data.get("detail", {})),
            schema_version=int(data.get("schema_version", 1)),
            version=int(data.get("version", 1)),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
        )
