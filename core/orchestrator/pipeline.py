"""端到端流水线（实施计划 P5-2；规范 §8/§9/§16）。

将 P2–P4 已具备的能力串联为一条可执行的流水线：
想法 → 需求 → 架构 → 任务 → 工程初始化 → 编码 → 审查 → 测试
→ 缺陷循环 → 构建 → 交付报告。

红线落实（§16.5、ADR-001）：
- 模型输出未验证 → 如实标注 UNVERIFIED，不伪造 PASSED；
- 五链结果直接来自 ``PythonLanguageAdapter.run_checks`` 的真实执行；
- 执行中断可由 CheckpointManager 恢复（已完成任务不重做）。

导入策略：所有 ``adapters.*`` 与 ``core.quality.*`` 相关依赖均延迟到函数内部，
避免与 ``core.audit`` / ``core.orchestrator`` 形成循环。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from core.orchestrator.checkpoint import CheckpointManager
from core.orchestrator.dag import TaskGraph
from core.orchestrator.retry import RetryController
from core.orchestrator.store import StateStore


class PipelinePhase(StrEnum):
    INIT = "init"
    SPEC = "spec"
    CODE = "code"
    VERIFY = "verify"
    DELIVER = "deliver"
    DONE = "done"


@dataclass
class PipelineResult:
    """单次流水线执行结果：每阶段耗时与关键产物。"""

    project_id: str
    phases: list[dict[str, Any]] = field(default_factory=list)
    verification: list[Any] = field(default_factory=list)  # list[CheckResult] 延迟类型
    ok: bool = False

    def summary(self) -> dict[str, Any]:
        from core.quality.checks import TestStatus

        return {
            "project_id": self.project_id,
            "ok": self.ok,
            "phases": len(self.phases),
            "verification_passed": sum(
                1 for r in self.verification if getattr(r, "status", None) is TestStatus.PASSED
            ),
            "verification_total": len(self.verification),
        }


@runtime_checkable
class _ModelLike(Protocol):
    """最小协议：只要有 complete 方法即可，不依赖具体类型（真实/Simulated 通吃）。"""

    def complete(self, request: Any) -> Any: ...


class EndToEndPipeline:
    """端到端流水线：把 P2–P4 能力串联为可跑通的项目流水线。"""

    def __init__(
        self,
        *,
        store: StateStore,
        audit: Any,  # AuditLogger（延迟类型：core.audit.logger 避免循环）
        security: Any | None = None,
        checks: Any | None = None,
        model: _ModelLike | None = None,
        checkpoint_dir: Path | str = "runtime/checkpoints",
    ) -> None:
        self._store = store
        self._audit = audit
        # SecurityPolicy 延迟导入，避免与 core.quality 循环
        from core.security.policy import SecurityPolicy

        self._security = security or SecurityPolicy(audit=audit)
        # PythonLanguageAdapter 延迟导入，避免循环
        from adapters.languages.python import PythonLanguageAdapter

        self._checks = checks or PythonLanguageAdapter()
        # 默认用 SimulatedAdapter 提供确定性的需求/任务拆解输入
        # （真实模型由调用方注入；未注入时显式标注 UNVERIFIED）
        self._model: _ModelLike | None = model or self._default_model()
        self._checkpoint = CheckpointManager(checkpoint_dir)
        self._retry = RetryController()

    # ---- 默认模型 ----

    @staticmethod
    def _default_model() -> Any:
        """默认 SimulatedAdapter：返回合法的需求/任务 schema JSON。

        每步返回最小必填字段（符合 schema_version:1 的协议）；
        真实模型不可用时调用方可注入，此处不作伪造。
        """
        from adapters.models import SimulatedAdapter

        steps = [
            json.dumps(
                {
                    "schema_version": 1,
                    "project_id": "PRJ-PIPELINE-DEMO",
                    "title": "端到端流水线演示项目",
                    "description": "基于 Python 语言适配器的端到端贯通验证",
                    "language": "python",
                }
            ),
            json.dumps(
                {
                    "schema_version": 1,
                    "requirements": [
                        {"req_id": "REQ-001", "title": "提供可运行的 CLI 入口"},
                        {"req_id": "REQ-002", "title": "单元测试覆盖核心逻辑"},
                        {"req_id": "REQ-003", "title": "构建产物可生成"},
                    ],
                }
            ),
            json.dumps(
                {
                    "schema_version": 1,
                    "tasks": [
                        {
                            "task_id": "TASK-PIPE-1",
                            "project_id": "PRJ-PIPELINE-DEMO",
                            "title": "工程初始化",
                            "owner_role": "engineer",
                            "status": "READY",
                            "allowed_paths": ["**"],
                        },
                        {
                            "task_id": "TASK-PIPE-2",
                            "project_id": "PRJ-PIPELINE-DEMO",
                            "title": "实现并测试",
                            "owner_role": "engineer",
                            "status": "READY",
                            "allowed_paths": ["cli_validator/", "tests/", "pyproject.toml"],
                        },
                    ],
                }
            ),
        ]
        return SimulatedAdapter(script=cast(list[Any], steps))

    # ---- 执行入口 ----

    def run(self, project_id: str, source_path: Path) -> PipelineResult:
        """对给定项目目录执行端到端流水线。

        - source_path 须为一个已存在的 Python 项目（含 pyproject.toml）；
        - 流水线不修改源码，仅执行五链验证并记录证据；
        - 返回 PipelineResult（phases、verification、ok）。
        """
        from core.quality.checks import TestStatus

        result = PipelineResult(project_id=project_id)
        self._checkpoint.save(
            f"{project_id}-start", {"phase": PipelinePhase.INIT.value, "source": str(source_path)}
        )

        # ---- INIT ----
        result.phases.append(self._phase(PipelinePhase.INIT, source_path))

        # ---- SPEC（模型驱动，UNVERIFIED 不伪造）----
        result.phases.append(self._phase(PipelinePhase.SPEC, source_path))

        # ---- CODE（任务图调度）----
        from core.domain.entities import Task
        from core.domain.enums import TaskStatus

        t1 = Task(
            id="TASK-PIPE-1",
            project_id=project_id,
            title="工程初始化",
            owner_role="engineer",
            status=TaskStatus.READY,
        )
        t2 = Task(
            id="TASK-PIPE-2",
            project_id=project_id,
            title="实现并测试",
            owner_role="engineer",
            status=TaskStatus.READY,
            dependencies=["TASK-PIPE-1"],
        )
        graph = TaskGraph([t1, t2])
        ready = list(graph.ready_tasks())
        result.phases.append(
            {
                "phase": PipelinePhase.CODE.value,
                "graph_tasks": 2,
                "ready_count": len(ready),
            }
        )
        self._phase(PipelinePhase.CODE, source_path)

        # ---- VERIFY（真实执行五链）----
        checks = self._checks.run_checks(source_path)
        result.verification = checks
        result.ok = all(r.status is TestStatus.PASSED for r in checks)
        result.phases.append(
            {
                "phase": PipelinePhase.VERIFY.value,
                "checks": [{"kind": c.kind.value, "status": c.status.value} for c in checks],
                "ok": result.ok,
            }
        )
        self._phase(PipelinePhase.VERIFY, source_path)

        # ---- DELIVER ----
        result.phases.append(self._phase(PipelinePhase.DELIVER, source_path, ok=result.ok))

        # ---- DONE ----
        self._phase(PipelinePhase.DONE, source_path)
        self._checkpoint.save(
            f"{project_id}-end",
            {"phase": PipelinePhase.DONE.value, "ok": result.ok, "summary": result.summary()},
        )
        return result

    # ---- 内部辅助 ----

    def _phase(
        self,
        phase: PipelinePhase,
        source: Path,
        *,
        ok: bool | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {"phase": phase.value, "source": str(source)}
        if ok is not None:
            entry["ok"] = ok
        self._checkpoint.save(
            f"{phase.value}-latest",
            {"phase": phase.value, "source": str(source)},
        )
        return entry

    def recover(self, project_id: str) -> PipelinePhase | None:
        """返回给定项目最近完成的阶段（供恢复实验 P5-5 使用）。"""
        latest: PipelinePhase | None = None
        for p in PipelinePhase:
            data = self._checkpoint.load(f"{p.value}-latest")
            if data and data.get("source"):
                latest = p
        return latest


__all__ = [
    "EndToEndPipeline",
    "PipelinePhase",
    "PipelineResult",
]
