"""需求追踪矩阵（规范 §5.4、§8 阶段 2）：需求 ↔ 任务 ↔ 代码变更 ↔ 测试 ↔ 证据。

- 每行表示一条可追踪链：``requirement_id`` 必填，其余字段空串表示该环节缺失；
- :func:`build_matrix` 从既有实体（Task.requirement_ids、TestReport、Artifact）
  自动推导链接，也可手工 :meth:`TraceabilityMatrix.add`；
- :meth:`TraceabilityMatrix.gaps` 检出缺口：无任务/无代码变更/无测试/无证据的需求
  （P2-3 完成标准：缺口可检出）；
- CSV 落盘格式 ``requirements/traceability.csv``（§5.4），行按字典序排列保证确定性。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from core.domain.entities import Artifact, Requirement, Task, TestReport
from core.domain.enums import ArtifactKind

CSV_HEADER: tuple[str, ...] = ("requirement_id", "task_id", "code_change", "test_case", "evidence")


class TraceabilityError(ValueError):
    """追踪矩阵数据非法（ID 为空、CSV 表头不符等）。"""


@dataclass(frozen=True)
class TraceRow:
    """一条追踪链；task_id/code_change/test_case/evidence 为空串表示环节缺失。"""

    requirement_id: str
    task_id: str = ""
    code_change: str = ""
    test_case: str = ""
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.requirement_id.strip():
            raise TraceabilityError("追踪链 requirement_id 不能为空（§5.4：每项需求唯一 ID）")


@dataclass(frozen=True)
class GapReport:
    """追踪缺口报告：任一环节缺失的需求 ID 列表（升序去重）。"""

    no_task: tuple[str, ...]  # 无任何任务
    no_code_change: tuple[str, ...]  # 无代码变更记录
    no_test: tuple[str, ...]  # 无测试用例
    no_evidence: tuple[str, ...]  # 无验收证据

    @property
    def ok(self) -> bool:
        """True 表示所有受追踪需求四环节齐全。"""
        return not (self.no_task or self.no_code_change or self.no_test or self.no_evidence)


def _row_key(row: TraceRow) -> tuple[str, str, str, str, str]:
    return (row.requirement_id, row.task_id, row.code_change, row.test_case, row.evidence)


class TraceabilityMatrix:
    """追踪矩阵：行集合 + 查询 + 缺口检出 + CSV 序列化。"""

    def __init__(self, rows: Iterable[TraceRow] = ()) -> None:
        self._rows: set[TraceRow] = set(rows)

    def add(self, row: TraceRow) -> None:
        """加入一条追踪链（完全相同的行自动去重）。"""
        self._rows.add(row)

    @property
    def rows(self) -> tuple[TraceRow, ...]:
        """全部行，按字典序排列（确定性输出）。"""
        return tuple(sorted(self._rows, key=_row_key))

    def requirement_ids(self) -> frozenset[str]:
        return frozenset(r.requirement_id for r in self._rows)

    def rows_for(self, requirement_id: str) -> tuple[TraceRow, ...]:
        """某需求的全部追踪链（§5.4：按需求查全链）。"""
        return tuple(r for r in self.rows if r.requirement_id == requirement_id)

    def tasks_for(self, requirement_id: str) -> frozenset[str]:
        """某需求关联的任务 ID（不含空串）。"""
        return frozenset(r.task_id for r in self._rows if r.requirement_id == requirement_id) - {""}

    def requirements_for_task(self, task_id: str) -> frozenset[str]:
        """某任务关联的需求 ID。"""
        return frozenset(r.requirement_id for r in self._rows if r.task_id == task_id)

    def gaps(self, known_requirements: Iterable[str] = ()) -> GapReport:
        """检出缺口：对受追踪需求逐一检查四环节是否齐全。

        :param known_requirements: 必须被追踪的需求 ID（如全部 APPROVED 需求）；
            与矩阵中出现的需求取并集。完全无行的需求四个环节均记缺口。
        """
        subjects = sorted(set(known_requirements) | self.requirement_ids())
        no_task: list[str] = []
        no_code: list[str] = []
        no_test: list[str] = []
        no_evidence: list[str] = []
        for rid in subjects:
            rows = [r for r in self._rows if r.requirement_id == rid]
            if not any(r.task_id for r in rows):
                no_task.append(rid)
            if not any(r.code_change for r in rows):
                no_code.append(rid)
            if not any(r.test_case for r in rows):
                no_test.append(rid)
            if not any(r.evidence for r in rows):
                no_evidence.append(rid)
        return GapReport(
            no_task=tuple(no_task),
            no_code_change=tuple(no_code),
            no_test=tuple(no_test),
            no_evidence=tuple(no_evidence),
        )

    def to_csv(self) -> str:
        """序列化为 CSV 文本（含表头，行排序确定）。"""
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(CSV_HEADER)
        for r in self.rows:
            writer.writerow([r.requirement_id, r.task_id, r.code_change, r.test_case, r.evidence])
        return buf.getvalue()

    @classmethod
    def from_csv(cls, text: str) -> TraceabilityMatrix:
        """从 CSV 文本加载；表头不符或数据非法抛 TraceabilityError。"""
        reader = csv.reader(io.StringIO(text))
        try:
            header = next(reader)
        except StopIteration:
            raise TraceabilityError("追踪矩阵 CSV 为空") from None
        if tuple(header) != CSV_HEADER:
            raise TraceabilityError(
                f"追踪矩阵 CSV 表头不符: 期望 {CSV_HEADER}，实际 {tuple(header)}"
            )
        matrix = cls()
        for i, raw in enumerate(reader, start=2):
            if not raw:
                continue
            if len(raw) != len(CSV_HEADER):
                raise TraceabilityError(f"第 {i} 行列数 {len(raw)} 与表头不符")
            try:
                matrix.add(
                    TraceRow(
                        requirement_id=raw[0],
                        task_id=raw[1],
                        code_change=raw[2],
                        test_case=raw[3],
                        evidence=raw[4],
                    )
                )
            except TraceabilityError as e:
                raise TraceabilityError(f"第 {i} 行非法: {e}") from None
        return matrix

    def save_csv(self, path: Path | str) -> Path:
        """落盘 CSV（父目录自动创建）。"""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.to_csv(), encoding="utf-8")
        return out

    @classmethod
    def load_csv(cls, path: Path | str) -> TraceabilityMatrix:
        """从 CSV 文件加载。"""
        return cls.from_csv(Path(path).read_text(encoding="utf-8"))


def build_matrix(
    requirements: Iterable[Requirement] = (),
    tasks: Iterable[Task] = (),
    test_reports: Iterable[TestReport] = (),
    artifacts: Iterable[Artifact] = (),
) -> TraceabilityMatrix:
    """从既有实体自动推导追踪矩阵。

    - 需求 ↔ 任务：Task.requirement_ids；
    - 任务 ↔ 代码变更：Artifact(kind=CODE_DIFF, task_id=任务)；
    - 任务 ↔ 测试用例：TestReport(task_id=任务)，记为 ``工具:报告ID``；
    - 任务 ↔ 验收证据：其余 Artifact(task_id=任务) 的产物路径；
    - 未关联任何任务的需求保留一行空链，便于缺口检出。
    """
    reports_by_task: dict[str, list[str]] = {}
    for rep in test_reports:
        reports_by_task.setdefault(rep.task_id, []).append(f"{rep.tool}:{rep.id}")
    code_by_task: dict[str, list[str]] = {}
    evidence_by_task: dict[str, list[str]] = {}
    for art in artifacts:
        if art.task_id is None:
            continue
        if art.kind is ArtifactKind.CODE_DIFF:
            code_by_task.setdefault(art.task_id, []).append(art.id)
        else:
            evidence_by_task.setdefault(art.task_id, []).append(art.id)

    matrix = TraceabilityMatrix()
    linked: set[str] = set()
    for task in tasks:
        for rid in task.requirement_ids:
            linked.add(rid)
            codes = code_by_task.get(task.id) or [""]
            tests = reports_by_task.get(task.id) or [""]
            evidences = evidence_by_task.get(task.id) or [""]
            for code in codes:
                for test in tests:
                    for evidence in evidences:
                        matrix.add(
                            TraceRow(
                                requirement_id=rid,
                                task_id=task.id,
                                code_change=code,
                                test_case=test,
                                evidence=evidence,
                            )
                        )
    for req in requirements:
        if req.id not in linked:
            matrix.add(TraceRow(requirement_id=req.id))
    return matrix
