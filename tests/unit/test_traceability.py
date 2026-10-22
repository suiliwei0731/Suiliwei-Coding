"""追踪矩阵测试（规范 §5.4、§8 阶段 2）。"""

from __future__ import annotations

from typing import Any

import pytest

from core.domain import Artifact, ArtifactKind, Requirement, Task, TestReport, TestStatus, new_id
from core.requirements import (
    GapReport,
    TraceabilityError,
    TraceabilityMatrix,
    TraceRow,
    build_matrix,
)


def _req(req_id: str | None = None) -> Requirement:
    return Requirement(
        id=req_id or new_id("REQ"),
        project_id="PRJ-test000001",
        title="批量导入用户",
    )


def _task(task_id: str, requirement_ids: list[str]) -> Task:
    return Task(
        id=task_id,
        project_id="PRJ-test000001",
        title="实现导入",
        owner_role="backend-dev",
        requirement_ids=requirement_ids,
    )


def _report(report_id: str, task_id: str) -> TestReport:
    return TestReport(
        id=report_id,
        task_id=task_id,
        tool="pytest",
        command="python -m pytest",
        status=TestStatus.PASSED,
    )


def _artifact(art_id: str, task_id: str, kind: ArtifactKind) -> Artifact:
    return Artifact(
        id=art_id,
        project_id="PRJ-test000001",
        kind=kind,
        path=f"artifacts/{art_id}.bin",
        sha256="0" * 64,
        size_bytes=1,
        task_id=task_id,
    )


class TestTraceRow:
    def test_empty_requirement_id_rejected(self) -> None:
        with pytest.raises(TraceabilityError, match="requirement_id"):
            TraceRow(requirement_id="  ")


class TestQuery:
    def test_rows_for_and_reverse_lookup(self) -> None:
        m = TraceabilityMatrix(
            [
                TraceRow("REQ-1", task_id="TASK-1", test_case="pytest:TR-1"),
                TraceRow("REQ-1", task_id="TASK-2"),
                TraceRow("REQ-2", task_id="TASK-2"),
            ]
        )
        assert len(m.rows_for("REQ-1")) == 2
        assert m.tasks_for("REQ-1") == frozenset({"TASK-1", "TASK-2"})
        assert m.requirements_for_task("TASK-2") == frozenset({"REQ-1", "REQ-2"})
        assert m.requirement_ids() == frozenset({"REQ-1", "REQ-2"})

    def test_duplicate_rows_deduped(self) -> None:
        m = TraceabilityMatrix()
        m.add(TraceRow("REQ-1", task_id="TASK-1"))
        m.add(TraceRow("REQ-1", task_id="TASK-1"))
        assert len(m.rows) == 1


class TestGaps:
    def test_full_chain_has_no_gap(self) -> None:
        m = TraceabilityMatrix(
            [
                TraceRow(
                    "REQ-1",
                    task_id="TASK-1",
                    code_change="ART-1",
                    test_case="pytest:TR-1",
                    evidence="ART-2",
                )
            ]
        )
        report = m.gaps()
        assert report.ok

    def test_missing_links_detected(self) -> None:
        m = TraceabilityMatrix(
            [
                TraceRow("REQ-1"),  # 四环节全缺
                TraceRow("REQ-2", task_id="TASK-1"),  # 缺代码/测试/证据
                TraceRow("REQ-3", task_id="TASK-2", test_case="pytest:TR-1"),  # 缺代码/证据
            ]
        )
        report = m.gaps()
        assert not report.ok
        assert report.no_task == ("REQ-1",)
        assert report.no_code_change == ("REQ-1", "REQ-2", "REQ-3")
        assert report.no_test == ("REQ-1", "REQ-2")
        assert report.no_evidence == ("REQ-1", "REQ-2", "REQ-3")

    def test_known_requirement_without_any_row_is_gap(self) -> None:
        m = TraceabilityMatrix([TraceRow("REQ-1", task_id="TASK-1")])
        report = m.gaps(known_requirements=["REQ-1", "REQ-9"])
        assert "REQ-9" in report.no_task
        assert "REQ-9" in report.no_test


class TestCSV:
    def test_round_trip(self, tmp_path: Any) -> None:
        m = TraceabilityMatrix(
            [
                TraceRow("REQ-1", task_id="TASK-1", code_change="ART-1"),
                TraceRow("REQ-2"),
            ]
        )
        path = m.save_csv(tmp_path / "requirements" / "traceability.csv")
        loaded = TraceabilityMatrix.load_csv(path)
        assert loaded.rows == m.rows

    def test_csv_header_enforced(self) -> None:
        with pytest.raises(TraceabilityError, match="表头不符"):
            TraceabilityMatrix.from_csv("a,b,c,d,e\nREQ-1,,,,\n")

    def test_csv_bad_row_rejected(self) -> None:
        with pytest.raises(TraceabilityError, match="列数"):
            TraceabilityMatrix.from_csv(
                "requirement_id,task_id,code_change,test_case,evidence\nREQ-1,TASK-1\n"
            )
        with pytest.raises(TraceabilityError, match="非法"):
            TraceabilityMatrix.from_csv(
                "requirement_id,task_id,code_change,test_case,evidence\n,,,,\n"
            )


class TestBuildMatrix:
    def test_links_derived_from_entities(self) -> None:
        req = _req("REQ-1")
        task = _task("TASK-1", ["REQ-1"])
        report = _report("TR-1", "TASK-1")
        diff = _artifact("ART-1", "TASK-1", ArtifactKind.CODE_DIFF)
        evidence = _artifact("ART-2", "TASK-1", ArtifactKind.TEST_REPORT)
        m = build_matrix([req], [task], [report], [diff, evidence])
        rows = m.rows_for("REQ-1")
        assert rows == (
            TraceRow(
                "REQ-1",
                task_id="TASK-1",
                code_change="ART-1",
                test_case="pytest:TR-1",
                evidence="ART-2",
            ),
        )
        assert m.gaps().ok

    def test_unlinked_requirement_kept_as_empty_row(self) -> None:
        m = build_matrix([_req("REQ-1"), _req("REQ-2")], [_task("TASK-1", ["REQ-1"])])
        assert m.rows_for("REQ-2") == (TraceRow("REQ-2"),)
        report: GapReport = m.gaps(known_requirements=["REQ-1", "REQ-2"])
        assert report.no_task == ("REQ-2",)

    def test_taskless_report_and_artifact_ignored(self) -> None:
        # 报告/产物挂在未关联需求的任务上时，不污染矩阵
        m = build_matrix(
            [_req("REQ-1")],
            [_task("TASK-9", [])],
            [_report("TR-9", "TASK-9")],
            [_artifact("ART-9", "TASK-9", ArtifactKind.LOG)],
        )
        assert m.requirement_ids() == frozenset({"REQ-1"})
