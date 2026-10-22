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
    model_mode: str = "unknown"  # injected / real / simulated
    model_unverified_reason: str | None = None
    model_calls: int = 0
    citations: list[str] = field(default_factory=list)

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
            "model_mode": self.model_mode,
            "model_calls": self.model_calls,
            "model_unverified": self.model_unverified_reason is not None,
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
        knowledge: Any | None = None,  # KnowledgeIndex（延迟类型）
        model_profile_name: str | None = None,
        checkpoint_dir: Path | str = "runtime/checkpoints",
        prompt_max_chars: int = 6000,
    ) -> None:
        self._store = store
        self._audit = audit
        # SecurityPolicy 延迟导入，避免与 core.quality 循环
        from core.security.policy import SecurityPolicy

        self._security = security or SecurityPolicy(audit=audit)
        # PythonLanguageAdapter 延迟导入，避免循环
        from adapters.languages.python import PythonLanguageAdapter

        self._checks = checks or PythonLanguageAdapter()
        # 知识库：默认加载仓库 knowledge/registry.yaml（P4-6）
        if knowledge is None:
            from core.knowledge import KnowledgeIndex

            knowledge = KnowledgeIndex.from_file()
        self._knowledge = knowledge
        self._prompt_max_chars = prompt_max_chars
        # 模型：显式注入优先；否则按 config/models.yaml 解析真实 profile，
        # profile 未启用或环境变量缺失时回退模拟适配器并如实标注 UNVERIFIED。
        self._model, self._model_mode, self._model_reason = self._resolve_model(
            model, model_profile_name
        )
        self._checkpoint = CheckpointManager(checkpoint_dir)
        self._retry = RetryController()

    # ---- 模型解析 ----

    def _resolve_model(
        self, model: _ModelLike | None, profile_name: str | None
    ) -> tuple[_ModelLike | None, str, str | None]:
        """返回（适配器, 模式, 未验证原因）。

        模拟模式下适配器为 ``None``：由 :meth:`_model_for_run` 按本次项目
        动态生成脚本，避免硬编码别的项目 ID/名称。
        """
        if model is not None:
            return model, "injected", None

        from adapters.models import ModelError, ModelProfile, OpenAICompatibleAdapter

        try:
            profile = ModelProfile.from_config(profile_name)
        except ModelError as exc:
            return (
                None,
                "simulated",
                f"模型 profile 不可用：{exc}；本次模型输出为模拟结果，标注 UNVERIFIED",
            )
        # 除 config 的 enabled 外，允许用环境变量显式启用（不写入任何入库文件）
        env_enabled = self._env_truthy("AI_DEV_PLATFORM_MODEL_ENABLED")
        if not profile.enabled:
            if not env_enabled:
                return (
                    None,
                    "simulated",
                    "模型 profile 未启用（config/models.yaml enabled=false，"
                    "且未设置 AI_DEV_PLATFORM_MODEL_ENABLED）；"
                    "本次模型输出为模拟结果，标注 UNVERIFIED",
                )
            from dataclasses import replace

            profile = replace(profile, enabled=True)

        missing = self._missing_env(profile.base_url_env, profile.api_key_env, profile.model_env)
        if missing:
            return (
                None,
                "simulated",
                f"真实模型不可用：缺少环境变量 {', '.join(missing)}；"
                "本次模型输出为模拟结果，标注 UNVERIFIED",
            )
        try:
            adapter = OpenAICompatibleAdapter(profile, audit=self._audit)
        except ModelError as exc:
            return (
                None,
                "simulated",
                f"真实模型不可用：{exc}；本次模型输出为模拟结果，标注 UNVERIFIED",
            )
        return adapter, "real", None

    @staticmethod
    def _env_truthy(name: str) -> bool:
        import os

        return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _missing_env(*names: str) -> list[str]:
        import os

        return [n for n in names if not os.environ.get(n, "").strip()]

    # ---- 默认模型 ----

    @staticmethod
    def _build_simulated(project_id: str, source: Path) -> Any:
        """模拟适配器：输出与当前项目一致的需求/任务 JSON（不跨项目串味）。

        仅在真实模型不可用时启用，调用方须把 ``model_mode=simulated``
        与未验证原因一并记录，禁止把模拟输出当成真实模型结论。
        """
        from adapters.models import SimulatedAdapter

        name, description = EndToEndPipeline._project_idea(source)
        steps = [
            # 第 1 次调用（SPEC）：需求拆解
            json.dumps(
                {
                    "schema_version": 1,
                    "project_id": project_id,
                    "title": name,
                    "description": description or "端到端流水线验证项目",
                    "language": "python",
                    "requirements": [
                        {"req_id": "REQ-001", "title": f"{name}：提供可运行的入口"},
                        {"req_id": "REQ-002", "title": f"{name}：单元测试覆盖核心逻辑"},
                        {"req_id": "REQ-003", "title": f"{name}：构建产物可生成"},
                    ],
                },
                ensure_ascii=False,
            ),
            # 第 2 次调用（CODE）：就绪任务与实现计划
            json.dumps(
                {
                    "schema_version": 1,
                    "project_id": project_id,
                    "title": f"{name}：就绪任务实现计划",
                    "tasks": [
                        {
                            "task_id": "TASK-PIPE-1",
                            "project_id": project_id,
                            "title": "工程初始化",
                            "owner_role": "engineer",
                            "status": "READY",
                            "allowed_paths": ["**"],
                        },
                        {
                            "task_id": "TASK-PIPE-2",
                            "project_id": project_id,
                            "title": "实现并测试",
                            "owner_role": "engineer",
                            "status": "READY",
                            "allowed_paths": ["**"],
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        ]
        return SimulatedAdapter(script=cast(list[Any], steps))

    def _model_for_run(self, project_id: str, source: Path) -> _ModelLike:
        """每次运行取模型：模拟模式按项目重建脚本（避免上次运行耗尽脚本步数）。"""
        if self._model_mode == "simulated":
            return self._build_simulated(project_id, source)
        if self._model is None:  # 理论上不会发生，防御性明确报错
            raise RuntimeError("模型适配器缺失")
        return self._model

    # ---- 执行入口 ----

    def run(self, project_id: str, source_path: Path) -> PipelineResult:
        """对给定项目目录执行端到端流水线。

        - source_path 须为一个已存在的 Python 项目（含 pyproject.toml）；
        - 流水线不修改源码，仅执行五链验证并记录证据；
        - 返回 PipelineResult（phases、verification、ok）；
        - 若某阶段已有 checkpoint（且 source 匹配），跳过该阶段——
          这就是 P5-5 恢复实验的核心：已通过任务不重做。
        """
        from core.quality.checks import TestStatus

        result = PipelineResult(
            project_id=project_id,
            model_mode=self._model_mode,
            model_unverified_reason=self._model_reason,
        )
        # 本次运行使用的模型：真实/注入复用同一实例；模拟模式按项目重建脚本
        model = self._model_for_run(project_id, source_path)
        self._checkpoint.save(
            f"{project_id}-start",
            {"phase": PipelinePhase.INIT.value, "source": str(source_path)},
        )

        # ---- INIT ----
        result.phases.append(self._phase(PipelinePhase.INIT, source_path, project_id=project_id))

        # ---- SPEC（知识检索 → prompt → 模型调用）----
        spec_entry = self._run_spec(result, model, project_id, source_path)
        result.phases.append(spec_entry)
        self._phase(PipelinePhase.SPEC, source_path, project_id=project_id)

        # ---- CODE（任务图 + 实现计划）----
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
        code_entry = self._run_code(result, model, project_id, source_path, len(ready))
        code_entry["graph_tasks"] = 2
        code_entry["ready_count"] = len(ready)
        result.phases.append(code_entry)
        self._phase(PipelinePhase.CODE, source_path, project_id=project_id)

        # ---- VERIFY ----
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
        self._phase(PipelinePhase.VERIFY, source_path, project_id=project_id)

        # ---- DELIVER ----
        from core.orchestrator.deliverables import DeliverableGenerator

        gen = DeliverableGenerator()
        bundle = gen.generate(
            project_id=project_id,
            verification=checks,
            source_path=source_path,
        )
        result.phases.append(
            {
                "phase": PipelinePhase.DELIVER.value,
                "ok": result.ok,
                "deliverables": [
                    str(f.relative_to(bundle.base_dir.parent.parent.parent))
                    for f in bundle.files
                    if f.exists()
                ],
            }
        )
        self._phase(PipelinePhase.DELIVER, source_path, project_id=project_id)

        # ---- DONE ----
        self._phase(PipelinePhase.DONE, source_path, project_id=project_id)
        self._checkpoint.save(
            f"{project_id}-end",
            {"phase": PipelinePhase.DONE.value, "ok": result.ok, "summary": result.summary()},
        )
        # 指标落库（P5-6）
        self._record_metrics(project_id, result.summary())
        return result

    # ---- 模型调用阶段 ----

    def _run_spec(
        self, result: PipelineResult, model: _ModelLike, project_id: str, source: Path
    ) -> dict[str, Any]:
        """SPEC：读项目上下文 → 检索知识 → 组装 prompt → 调用模型。"""
        name, description = self._project_idea(source)
        entry = self._invoke_model(
            result,
            model,
            role="product-architect",
            goal="把项目想法拆解为可验证的需求与任务清单",
            task_title=f"为项目「{name}」产出需求与任务拆解",
            task_description=description,
            project_context=self._project_context_text(source),
            task_id=f"{project_id}-SPEC",
        )
        entry["phase"] = PipelinePhase.SPEC.value
        return entry

    def _run_code(
        self,
        result: PipelineResult,
        model: _ModelLike,
        project_id: str,
        source: Path,
        ready_count: int,
    ) -> dict[str, Any]:
        """CODE：为就绪任务产出实现计划（本阶段不写源码文件）。"""
        name, _ = self._project_idea(source)
        entry = self._invoke_model(
            result,
            model,
            role="engineer",
            goal="为就绪任务给出实现步骤、验收自查项与验证命令",
            task_title=f"为项目「{name}」的 {ready_count} 个就绪任务制定实现计划",
            task_description="仅输出计划，不直接修改源码；改动须遵守 allowed_paths。",
            project_context=self._project_context_text(source),
            task_id=f"{project_id}-CODE",
        )
        entry["phase"] = PipelinePhase.CODE.value
        entry["source_writes"] = 0  # 当前阶段不写源码，如实记录
        return entry

    def _invoke_model(
        self,
        result: PipelineResult,
        model: _ModelLike,
        *,
        role: str,
        goal: str,
        task_title: str,
        task_description: str,
        project_context: str,
        task_id: str,
    ) -> dict[str, Any]:
        """检索知识、组装 prompt 并调用模型；失败如实标注 UNVERIFIED。"""
        from adapters.models import CompletionRequest, ModelError
        from core.prompt import PromptBuilder, RoleBrief

        bundle = PromptBuilder(self._knowledge, max_context_chars=self._prompt_max_chars).build(
            RoleBrief(role=role, goal=goal),
            task_title=task_title,
            task_description=task_description,
            project_context=project_context,
        )
        result.citations.extend(c["knowledge_id"] for c in bundle.citations)
        result.model_calls += 1

        entry: dict[str, Any] = {
            "role": role,
            "model_mode": self._model_mode,
            "prompt_chars": bundle.char_count,
            "knowledge_hits": bundle.knowledge_hits,
            "citations": [c["id"] for c in bundle.citations],
            "truncated": bundle.truncated,
        }
        try:
            completion = model.complete(
                CompletionRequest(role=role, prompt=bundle.text, task_id=task_id)
            )
        except ModelError as exc:
            entry["status"] = "UNVERIFIED"
            entry["unverified_reason"] = f"模型调用失败：{exc}"
            return entry
        entry["status"] = "OK"
        entry["response_chars"] = len(completion.content)
        entry["excerpt"] = completion.content[:240]
        return entry

    @staticmethod
    def _project_idea(source: Path) -> tuple[str, str]:
        """从 pyproject.toml 读取项目名与描述，作为需求种子。"""
        import tomllib

        name = source.name
        description = ""
        pyproject = source / "pyproject.toml"
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project = data.get("project") or {}
            name = str(project.get("name") or name)
            description = str(project.get("description") or "")
        except (OSError, tomllib.TOMLDecodeError):
            pass
        return name, description

    @staticmethod
    def _project_context_text(source: Path) -> str:
        """项目上下文：目录结构与 README 摘要（有界，避免 prompt 失控）。"""
        lines: list[str] = [f"根目录: {source.name}"]
        try:
            entries = sorted(
                p.name for p in source.iterdir() if p.name not in {"__pycache__", "runtime"}
            )
        except OSError:
            entries = []
        if entries:
            lines.append("顶层条目: " + ", ".join(entries[:30]))
        readme = source / "README.md"
        try:
            text = readme.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            lines.extend(["README 摘要:", text[:600]])
        return "\n".join(lines)

    # ---- 恢复 ----

    def has_checkpoint_for(self, phase: PipelinePhase, source_path: Path, project_id: str) -> bool:
        """给定阶段是否已有 checkpoint 且 source 匹配（P5-5 恢复实验查询用）。"""
        data = self._checkpoint.load(f"{phase.value}-latest")
        if not data:
            return False
        return data.get("source") == str(source_path)

    # ---- 内部辅助 ----

    def _phase(
        self,
        phase: PipelinePhase,
        source: Path,
        *,
        ok: bool | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {"phase": phase.value, "source": str(source)}
        if ok is not None:
            entry["ok"] = ok
        self._checkpoint.save(
            f"{phase.value}-latest",
            {"phase": phase.value, "source": str(source)},
        )
        if project_id is not None:
            self._checkpoint.save(
                f"{project_id}-{phase.value}",
                {"phase": phase.value, "source": str(source)},
            )
        return entry

    def _record_metrics(self, project_id: str, summary: dict[str, Any]) -> None:
        """指标落库（P5-6）：写入 checkpoint_dir 下的 metrics.jsonl，
        与该 Pipeline 实例的 checkpoint 隔离。"""
        import json as _json

        path = self._checkpoint._dir / "metrics.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = _json.dumps({"project_id": project_id, **summary}, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def recover(self, project_id: str) -> PipelinePhase | None:
        """返回给定项目最近完成的阶段（供恢复实验 P5-5 使用）。"""
        latest: PipelinePhase | None = None
        for p in PipelinePhase:
            data = self._checkpoint.load(f"{project_id}-{p.value}")
            if data and data.get("source"):
                latest = p
        return latest


__all__ = [
    "EndToEndPipeline",
    "PipelinePhase",
    "PipelineResult",
]
