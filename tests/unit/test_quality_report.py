"""P3-6 单元测试：未验证标记与质量报告（规范 §16.5；实施计划 P3-6）。

完成标准：工具缺失的报告显示"未验证"而非"通过"。
- 总体状态三态：阻断失败/未验证 → 未通过；豁免/非阻断失败 → 带条件通过；
  全通过 → 通过；
- UNVERIFIED 行渲染"未验证"+原因+可复现命令，绝不渲染为"通过"；
- manual/custom 规则与未映射失败在报告中显式列出（不得静默丢弃）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.domain import Severity, Task, TaskStatus, TestStatus, new_id
from core.quality import (
    CheckKind,
    CheckResult,
    OverallStatus,
    build_quality_report,
    create_waiver,
    evaluate,
    overall_status,
)
from core.standards import Rule, RuleCheck, RuleCheckKind, RuleWaiverPolicy


def make_task() -> Task:
    task = Task(
        id=new_id("TASK"),
        project_id="PRJ-test",
        title="实现登录 API",
        owner_role="backend",
        acceptance_criteria=["登录成功返回令牌"],
    )
    task.status = TaskStatus.TEST_REQUIRED
    return task


def make_rule(
    rule_id: str = "R-LINT",
    kind: RuleCheckKind | None = RuleCheckKind.STATIC_ANALYSIS,
    severity: Severity = Severity.BLOCKER,
    check_type: str = "auto",
) -> Rule:
    return Rule(
        id=rule_id,
        name=f"规则 {rule_id}",
        scope="code",
        description="要求",
        rationale="原因",
        good_examples=("好例",),
        bad_examples=("坏例",),
        check=RuleCheck(type=check_type, method="工具/审查", kind=kind),
        severity=severity,
        waiver=RuleWaiverPolicy(allowed=False),
    )


def make_result(
    kind: CheckKind = CheckKind.LINT,
    status: TestStatus = TestStatus.PASSED,
    tool: str = "ruff",
) -> CheckResult:
    if status is TestStatus.UNVERIFIED:
        return CheckResult(
            kind=kind,
            tool=tool,
            status=status,
            unverified_reason=f"{tool} 不可用；可复现命令: python -m {tool}",
        )
    return CheckResult(
        kind=kind,
        tool=tool,
        status=status,
        command=f"python -m {tool}",
        exit_code=1 if status is TestStatus.FAILED else 0,
    )


# ---------- 总体状态三态（§16.5） ----------


def test_overall_passed_when_all_passed() -> None:
    outcome = evaluate(make_task(), [make_rule()], [make_result()])
    assert overall_status(outcome) is OverallStatus.PASSED


def test_overall_failed_on_blocking_failure() -> None:
    outcome = evaluate(make_task(), [make_rule()], [make_result(status=TestStatus.FAILED)])
    assert overall_status(outcome) is OverallStatus.FAILED


def test_overall_failed_on_unverified() -> None:
    """未验证不得计入通过：UNVERIFIED → 未通过。"""
    outcome = evaluate(make_task(), [make_rule()], [make_result(status=TestStatus.UNVERIFIED)])
    assert overall_status(outcome) is OverallStatus.FAILED


def test_overall_conditional_on_non_blocking_failure() -> None:
    outcome = evaluate(
        make_task(),
        [make_rule(severity=Severity.MAJOR)],
        [make_result(status=TestStatus.FAILED)],
    )
    assert overall_status(outcome) is OverallStatus.CONDITIONAL


def test_overall_conditional_on_waived_blocker() -> None:
    task = make_task()
    rule = Rule(
        id="R-LINT",
        name="规则 R-LINT",
        scope="code",
        description="要求",
        rationale="原因",
        good_examples=("好例",),
        bad_examples=("坏例",),
        check=RuleCheck(type="auto", method="工具", kind=RuleCheckKind.STATIC_ANALYSIS),
        severity=Severity.BLOCKER,
        waiver=RuleWaiverPolicy(allowed=True, approver_roles=("tech-lead",), max_validity_days=30),
    )
    waiver = create_waiver(
        rule,
        reason="生成代码暂豁免",
        scope=[task.project_id],
        approver_role="tech-lead",
        requested_by="agent-dev",
        approved_by="human-lead",
        expires_at=(datetime.now(UTC) + timedelta(days=7))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    )
    outcome = evaluate(task, [rule], [make_result(status=TestStatus.FAILED)], waivers=[waiver])
    assert overall_status(outcome) is OverallStatus.CONDITIONAL


def test_unverified_dominates_conditional() -> None:
    """同时存在豁免失败与未验证项 → 未通过（未验证优先级高于带条件通过）。"""
    task = make_task()
    rules = [
        make_rule("R-LINT", severity=Severity.MAJOR),
        make_rule("R-TYPE", kind=RuleCheckKind.TYPE_CHECK),
    ]
    results = [
        make_result(CheckKind.LINT, TestStatus.FAILED),
        make_result(CheckKind.TYPE_CHECK, TestStatus.UNVERIFIED, "mypy"),
    ]
    outcome = evaluate(task, rules, results)
    assert overall_status(outcome) is OverallStatus.FAILED


# ---------- Markdown 渲染：工具缺失显示"未验证"而非"通过"（完成标准） ----------


def test_report_marks_missing_tool_as_unverified() -> None:
    task = make_task()
    results = [make_result(status=TestStatus.UNVERIFIED)]
    outcome = evaluate(task, [make_rule()], results)
    report = build_quality_report(task, results, outcome)
    assert report.overall is OverallStatus.FAILED
    md = report.to_markdown()
    assert "**未通过**" in md
    assert "| R-LINT | BLOCKER | 未验证 | ruff |" in md
    assert "ruff 不可用；可复现命令: python -m ruff" in md
    assert "**通过**" not in md and "| R-LINT | BLOCKER | 通过 |" not in md


def test_report_passed_renders_passed() -> None:
    task = make_task()
    results = [make_result(), make_result(CheckKind.TEST, tool="pytest")]
    rules = [make_rule(), make_rule("R-TEST", kind=RuleCheckKind.TEST)]
    outcome = evaluate(task, rules, results)
    report = build_quality_report(task, results, outcome)
    md = report.to_markdown()
    assert report.overall is OverallStatus.PASSED and "**通过**" in md
    assert "| 未验证 |" not in md  # 无任何未验证的判定行/检查行


def test_report_lists_manual_custom_and_unmapped() -> None:
    task = make_task()
    rules = [
        make_rule("R-MAN", kind=None, check_type="manual"),
        make_rule("R-CUS", kind=RuleCheckKind.CUSTOM),
    ]
    results = [make_result(CheckKind.BUILD, TestStatus.FAILED, "build")]
    outcome = evaluate(task, rules, results)
    md = build_quality_report(task, results, outcome).to_markdown()
    assert "人工审查规则" in md and "R-MAN" in md
    assert "自定义验证规则" in md and "R-CUS" in md
    assert "未映射到规则的失败" in md and "build" in md


def test_report_waived_row_shows_waiver_id() -> None:
    task = make_task()
    rule = Rule(
        id="R-LINT",
        name="规则 R-LINT",
        scope="code",
        description="要求",
        rationale="原因",
        good_examples=("好例",),
        bad_examples=("坏例",),
        check=RuleCheck(type="auto", method="工具", kind=RuleCheckKind.STATIC_ANALYSIS),
        severity=Severity.BLOCKER,
        waiver=RuleWaiverPolicy(allowed=True, approver_roles=("tech-lead",), max_validity_days=30),
    )
    waiver = create_waiver(
        rule,
        reason="生成代码暂豁免",
        scope=["*"],
        approver_role="tech-lead",
        requested_by="agent-dev",
        approved_by="human-lead",
        expires_at=(datetime.now(UTC) + timedelta(days=7))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    )
    results = [make_result(status=TestStatus.FAILED)]
    outcome = evaluate(task, [rule], results, waivers=[waiver])
    report = build_quality_report(task, results, outcome)
    md = report.to_markdown()
    assert report.overall is OverallStatus.CONDITIONAL
    assert f"已豁免 {waiver.id}" in md
