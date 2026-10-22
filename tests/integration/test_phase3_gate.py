"""Phase 3 阶段门禁集成测试（docs/03 §4：工程标准执行引擎验收）。

负面实验（乙阶段 C 验收，全部基于真实工具执行，不伪造证据）：
1. ruff 违规代码 → 真实运行 FAILED → 门禁处置 REWORK；再试图标 PASSED 被拒；
2. 失败 pytest 用例同理（JUnit 计数与产物入证据）；
3. 工具不可用 → UNVERIFIED → BLOCKED，质量报告显示"未验证"+可复现命令而非"通过"；
4. 过期豁免仍阻断（无有效豁免记录的 BLOCKER 不可跳过）；
5. 上游 FAILED 时下游任务不在 ready_tasks（§16.4）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adapters.tools import PythonCheckAdapter, python_checks
from core.audit import AuditLogger
from core.domain import EvidenceKind, Severity, Task, TaskStatus, TestStatus, new_id
from core.orchestrator import StateStore, TaskGraph
from core.quality import (
    CheckKind,
    CheckResult,
    GateExecutor,
    build_quality_report,
    create_waiver,
)
from core.quality.report import OverallStatus
from core.standards import Rule, RuleCheck, RuleCheckKind, RuleWaiverPolicy
from core.workflow import (
    Evidence,
    GuardRejectionError,
    IllegalTransitionError,
    TaskStateMachine,
    TransitionContext,
)

S = TaskStatus


def make_task(status: S = S.TEST_REQUIRED, **overrides: object) -> Task:
    task = Task(
        id=new_id("TASK"),
        project_id="PRJ-gate3",
        title="实现登录 API",
        owner_role="backend",
        acceptance_criteria=["登录成功返回令牌"],
    )
    task.status = status
    for key, value in overrides.items():
        setattr(task, key, value)
    return task


def make_rule(
    rule_id: str = "R-LINT",
    kind: RuleCheckKind = RuleCheckKind.STATIC_ANALYSIS,
    *,
    waivable: bool = False,
) -> Rule:
    return Rule(
        id=rule_id,
        name=f"规则 {rule_id}",
        scope="code",
        description="要求",
        rationale="原因",
        good_examples=("好例",),
        bad_examples=("坏例",),
        check=RuleCheck(type="auto", method="工具", kind=kind),
        severity=Severity.BLOCKER,
        waiver=(
            RuleWaiverPolicy(allowed=True, approver_roles=("tech-lead",), max_validity_days=30)
            if waivable
            else RuleWaiverPolicy(allowed=False)
        ),
    )


def make_executor(store: StateStore) -> tuple[GateExecutor, AuditLogger]:
    audit = AuditLogger(store)
    return GateExecutor(TaskStateMachine(), audit), audit


def make_ruff_violation_project(root: Path) -> Path:
    """构造含 ruff 违规（未使用导入）的最小 Python 项目。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["F"]\n', encoding="utf-8")
    (root / "bad.py").write_text(
        "import os  # 故意未使用，触发 F401\n\n\ndef f() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    return root


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------- 1. ruff 违规：FAILED → REWORK；标 PASSED 被拒 ----------


def test_ruff_violation_blocks_and_cannot_be_marked_passed(tmp_path: Path) -> None:
    workdir = make_ruff_violation_project(tmp_path / "proj")
    adapter = PythonCheckAdapter(artifacts_dir=tmp_path / "artifacts")
    result = adapter.run(CheckKind.LINT, workdir)
    assert result.status is TestStatus.FAILED and result.exit_code != 0
    assert result.command and result.tool_version  # §7.4 证据字段齐全

    with StateStore(tmp_path / "platform.db") as store:
        executor, audit = make_executor(store)
        task = make_task()
        outcome = executor.enforce(task, [make_rule()], [result])
        assert not outcome.passed and task.status is S.REWORK and task.retry_count == 1
        assert audit.list_events()[-1].detail["decision"] == "REWORK"

        # 处置后试图把失败任务标 PASSED：REWORK→PASSED 无合法边
        report = result.to_test_report(task.id)
        evidence = Evidence(
            kind=EvidenceKind.TEST_REPORT,
            summary="试图标过",
            refs=(report.id,),
            exit_code=0,
        )
        with pytest.raises(IllegalTransitionError):
            TaskStateMachine().transition(task, S.PASSED, [evidence], TransitionContext())

        # 仍在 TEST_REQUIRED 的任务：FAILED 报告被守卫拒绝（违规代码不得标 PASSED）
        task2 = make_task()
        with pytest.raises(GuardRejectionError, match="未通过"):
            TaskStateMachine().transition(
                task2, S.PASSED, [evidence], TransitionContext(test_reports=(report,))
            )
        assert task2.status is S.TEST_REQUIRED

        # 重试预算耗尽 → FAILED
        task3 = make_task(retry_count=3)
        executor.enforce(task3, [make_rule()], [result])
        assert task3.status is S.FAILED

    # 修复违规后重跑：同一适配器真实执行转为 PASSED
    (workdir / "bad.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    fixed = adapter.run(CheckKind.LINT, workdir)
    assert fixed.status is TestStatus.PASSED and fixed.exit_code == 0


# ---------- 2. pytest 失败：FAILED + JUnit 计数 → REWORK ----------


def test_failing_pytest_blocks_with_junit_counts(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "test_fail.py").write_text(
        "def test_intentional_failure() -> None:\n    assert False, '故意失败'\n",
        encoding="utf-8",
    )
    adapter = PythonCheckAdapter(artifacts_dir=tmp_path / "artifacts")
    result = adapter.run(CheckKind.TEST, proj)
    assert result.status is TestStatus.FAILED and result.exit_code != 0
    assert result.total == 1 and result.failed == 1  # JUnit XML 计数入证据（§7.4）
    assert result.artifact_paths and Path(result.artifact_paths[0]).exists()

    with StateStore(tmp_path / "platform.db") as store:
        executor, audit = make_executor(store)
        task = make_task()
        executor.enforce(task, [make_rule("R-TEST", RuleCheckKind.TEST)], [result], max_retries=1)
        assert task.status is S.REWORK and task.retry_count == 1
        assert audit.list_events()[-1].detail["decision"] == "REWORK"


# ---------- 3. 工具不可用：UNVERIFIED → BLOCKED；报告显示"未验证" ----------


def test_missing_tool_marks_unverified_not_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = make_ruff_violation_project(tmp_path / "proj")
    monkeypatch.setitem(python_checks._KIND_MODULE, CheckKind.LINT, "definitely_missing_tool_xyz")
    adapter = PythonCheckAdapter(artifacts_dir=tmp_path / "artifacts")
    result = adapter.run(CheckKind.LINT, workdir)
    assert result.status is TestStatus.UNVERIFIED
    assert "可复现命令" in result.unverified_reason

    with StateStore(tmp_path / "platform.db") as store:
        executor, _ = make_executor(store)
        task = make_task()
        outcome = executor.enforce(task, [make_rule()], [result])
        assert not outcome.passed and task.status is S.BLOCKED
        assert "未验证" in (task.blocked_reason or "")

        report = build_quality_report(task, [result], outcome)
        assert report.overall is OverallStatus.FAILED  # 未验证 → 未通过（§16.5）
        md = report.to_markdown()
        assert "| R-LINT | BLOCKER | 未验证 |" in md
        assert "可复现命令" in md and "| R-LINT | BLOCKER | 通过 |" not in md


# ---------- 4. 过期豁免仍阻断 ----------


def test_expired_waiver_still_blocks(tmp_path: Path) -> None:
    workdir = make_ruff_violation_project(tmp_path / "proj")
    adapter = PythonCheckAdapter(artifacts_dir=tmp_path / "artifacts")
    result = adapter.run(CheckKind.LINT, workdir)
    assert result.status is TestStatus.FAILED

    rule = make_rule(waivable=True)
    now = datetime.now(UTC)
    expired = create_waiver(
        rule,
        reason="历史豁免，已过期",
        scope=["*"],
        approver_role="tech-lead",
        requested_by="agent-dev",
        approved_by="human-lead",
        created_at=iso_z(now - timedelta(days=10)),
        expires_at=iso_z(now - timedelta(days=5)),
    )
    with StateStore(tmp_path / "platform.db") as store:
        executor, audit = make_executor(store)
        task = make_task()
        outcome = executor.enforce(task, [rule], [result], waivers=[expired])
        assert not outcome.passed and task.status is S.REWORK
        assert not outcome.findings[0].waived  # 过期豁免不得放行
        assert not [e for e in audit.list_events() if e.action == "waiver.applied"]


# ---------- 5. 上游 FAILED → 下游不可见（§16.4） ----------


def test_upstream_failed_hides_downstream(tmp_path: Path) -> None:
    with StateStore(tmp_path / "platform.db") as store:
        executor, _ = make_executor(store)
        upstream = make_task(retry_count=3)
        failed_result = make_ruff_failure_result()
        executor.enforce(upstream, [make_rule()], [failed_result])
        assert upstream.status is S.FAILED

        downstream = make_task(status=S.READY, dependencies=[upstream.id])
        graph = TaskGraph([upstream, downstream])
        assert graph.ready_tasks() == ()  # 未通过任务对下游不可见

        # 对照：上游 PASSED 后下游就绪
        upstream_ok = make_task()
        passed_result = make_ruff_failure_result(status=TestStatus.PASSED, exit_code=0)
        executor.enforce(upstream_ok, [make_rule()], [passed_result])
        assert upstream_ok.status is S.PASSED
        downstream_ok = make_task(status=S.READY, dependencies=[upstream_ok.id])
        graph_ok = TaskGraph([upstream_ok, downstream_ok])
        assert [t.id for t in graph_ok.ready_tasks()] == [downstream_ok.id]


def make_ruff_failure_result(
    status: TestStatus = TestStatus.FAILED, exit_code: int = 1
) -> CheckResult:
    return CheckResult(
        kind=CheckKind.LINT,
        tool="ruff",
        status=status,
        command="python -m ruff check .",
        exit_code=exit_code,
    )
