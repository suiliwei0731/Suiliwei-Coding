"""核心枚举：任务状态、严重度、审查/测试结论等。

依据：规范 §9.1（任务 10 态）、§16.3（5 级严重度量表）、§16.5（结果四态区分：
通过/失败/未验证/不适用，未执行不得报告为通过）。
"""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    """任务状态机 10 态（规范 §9.1）。"""

    DRAFT = "DRAFT"  # 草拟
    READY = "READY"  # 输入/依赖/验收齐备
    IN_PROGRESS = "IN_PROGRESS"  # 执行中
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # 等待审查
    TEST_REQUIRED = "TEST_REQUIRED"  # 等待测试
    PASSED = "PASSED"  # 全部必需门禁通过
    BLOCKED = "BLOCKED"  # 依赖或环境阻塞（旁路）
    FAILED = "FAILED"  # 检查或执行失败（旁路）
    REWORK = "REWORK"  # 返工中（旁路）
    CANCELLED = "CANCELLED"  # 经授权取消（旁路）


class Severity(StrEnum):
    """统一严重度量表（规范 §16.3），顺序即优先级。"""

    BLOCKER = "BLOCKER"  # 阻断交付
    CRITICAL = "CRITICAL"  # 关键功能或安全
    MAJOR = "MAJOR"  # 重要逻辑/兼容/质量
    MINOR = "MINOR"  # 一般改进
    INFO = "INFO"  # 非阻断建议


class ReviewVerdict(StrEnum):
    """审查结论（对应 schemas/review.schema.json）。"""

    PASS = "PASS"  # 无阻断问题
    PASS_WITH_COMMENTS = "PASS_WITH_COMMENTS"  # 通过但有非阻断意见
    CHANGES_REQUIRED = "CHANGES_REQUIRED"  # 存在阻断问题，须返工


class TestStatus(StrEnum):
    """测试/检查结果四态（规范 §16.5）：未执行不得标通过。"""

    __test__ = False  # 非 pytest 测试类，避免收集告警

    PASSED = "PASSED"
    FAILED = "FAILED"
    UNVERIFIED = "UNVERIFIED"  # 工具不可用/环境受限，须说明原因与可复现命令
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DefectStatus(StrEnum):
    """缺陷生命周期。"""

    OPEN = "OPEN"
    IN_FIX = "IN_FIX"
    FIXED = "FIXED"
    VERIFIED = "VERIFIED"  # 回归验证通过
    CLOSED = "CLOSED"


class DefectSource(StrEnum):
    """缺陷来源。"""

    REVIEW = "REVIEW"
    TEST = "TEST"
    MANUAL = "MANUAL"


class ArtifactKind(StrEnum):
    """产物类型（runtime/artifacts 文件证据，DB 存引用与校验信息，ADR-001）。"""

    CODE_DIFF = "CODE_DIFF"
    TEST_REPORT = "TEST_REPORT"
    BUILD = "BUILD"
    LOG = "LOG"
    DOC = "DOC"
    OTHER = "OTHER"


class ApprovalStatus(StrEnum):
    """审批状态（规范 §17：高风险操作需明确授权）。"""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ProjectStatus(StrEnum):
    """项目级状态（由子任务与阶段状态汇总，§9.2：不得由总控单方面宣布完成）。"""

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AgentRunStatus(StrEnum):
    """Agent 运行状态。"""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class EvidenceKind(StrEnum):
    """状态转移证据类型（规范 §2、§7.4）：转移只由控制器依据证据触发。"""

    COMMAND_RESULT = "COMMAND_RESULT"  # 命令、退出码、耗时
    TEST_REPORT = "TEST_REPORT"
    REVIEW_RESULT = "REVIEW_RESULT"
    ARTIFACT = "ARTIFACT"
    APPROVAL = "APPROVAL"
    MANUAL = "MANUAL"  # 人工授权/操作记录
