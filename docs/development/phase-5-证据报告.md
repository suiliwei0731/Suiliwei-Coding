# Phase 5 证据报告：单语言端到端闭环（Python）

> 依据 docs/03 §4：完成内容、修改文件、执行过的命令、测试证据、失败项、已知限制、下一阶段计划。
> 日期：2026-10-22；分支：main；范围：P5-1 ~ P5-6 + 阶段门禁。

## 1. 完成内容

| 任务 | 交付 | 提交 |
|---|---|---|
| P5-1 Python 语言适配器 | `templates/python/cli/`（cli-validator：PyYAML 依赖、模板业务逻辑、五链真实通过）、`templates/python/service/`（status-service：仅标准库、HTTP 状态查询、五链真实通过）、`adapters/languages/python/adapter.py`（PythonLanguageAdapter：模板识别/元数据/五链执行）、`config/language-adapters.yaml` python.status → implemented | 89b22bb |
| P5-2 端到端流水线贯通 | `core/orchestrator/pipeline.py`（EndToEndPipeline：INIT→SPEC→CODE→VERIFY→DELIVER→DONE 六阶段，串联 StateStore/TaskGraph/CheckpointManager/PythonLanguageAdapter/SecurityPolicy/SimulatedAdapter） | 00dfbb4 |
| P5-3 交付物生成器 | `core/orchestrator/deliverables.py`（DeliverableGenerator：README/测试报告/追踪矩阵/已知限制/.env.example 五项交付），集成到 DELIVER 阶段 | d098e25 |
| P5-4 缺陷注入实验 | `core/orchestrator/fault_injection.py`（FaultInjector：就地注入未使用 import → ruff F401；verify_fault_was_caught 红线断言：FAILED 不变形） | a7a35b9 |
| P5-5 恢复实验 | Pipeline.checkpoint 双写（`{phase}-latest` + `{project_id}-{phase}`）、has_checkpoint_for(project, phase, source)、recover(project_id) 精确匹配项目级 checkpoint | be4b881 |
| P5-6 指标基线采集 | DONE 阶段把 summary 追加到 checkpoint_dir/metrics.jsonl，含 project_id/ok/phases/verification_passed/total | be4b881 |
| 阶段门禁集成测试 | `tests/integration/test_phase5_gate.py`（7 项：交付物五文件、交付摘要、lint 缺陷检出、缺陷修复恢复、recover 返回 DONE、has_checkpoint_for 全覆盖、metrics.jsonl 追加） | 本次提交 |

## 2. 执行过的命令与退出码（仓库根，全部 0）

- `python -m pytest` → **453 passed**
- `python -m ruff check .` → All checks passed
- `python -m ruff format --check .` → 116 files already formatted
- `python -m mypy` → no issues found in 61 source files
- `python scripts/validate/check_baseline.py` → 基线校验 10/10 通过
- `python -m build`（对 cli/service 模板各一次）→ 成功生成 .tar.gz + .whl

## 3. 测试证据（阶段门禁对应）

docs/03 §4 甲 Phase 4 验收 + 乙阶段 B 验收：真实小项目可交付；缺陷注入实验证明不谎报；恢复实验通过。

- **真实小项目可交付**：`test_pipeline_delivers_five_artifacts` — Pipeline.run("PRJ-DELIVER", cli 模板) 跑完五链 ok=True，DELIVER 阶段生成 README.md / test-report.md / traceability.json / known-limits.md / .env.example 共五项交付产物；`test_deliverable_summary_counts_passed` 断言 summary 中 verification_passed=5/total=5。
- **缺陷注入证明不谎报**：`test_injected_lint_failure_becomes_ok_false` — FaultInjector 注入 `import os` 未使用 → ruff F401 → Pipeline ok=False，lint.status 为 FAILED（绝不被改写为 PASSED）；`test_fault_fixed_pipeline_recovers_ok` — 覆盖干净模板重跑 → ok=True，lint.status 转 PASSED，闭环成立。
- **恢复实验**：`test_pipeline_recover_returns_latest_phase` — run 后 recover(project_id) 返回 DONE；`test_has_checkpoint_for_after_run` — 六个阶段每个都有项目级 checkpoint 且 source 匹配。
- **指标落库**：`test_metrics_jsonl_append` — 同一 Pipeline 实例两次 run 分别追加两行到 checkpoint_dir/metrics.jsonl，路径隔离无跨测试残留。

## 4. 失败项与修复

- **循环导入**：pipeline.py 顶层导入 `adapters.languages.python` → `python_checks` → `core.quality/gate` → AuditLogger → StateStore → orchestrator/__init__ → pipeline.py 环形闭合。修复：所有 adapters.* / core.quality.* 相关导入延迟到函数内部；同时从 orchestrator/__init__.py 移除 Pipeline 导出，让调用方直接 `from core.orchestrator.pipeline import ...`。
- **TaskGraph 接口**：Pipeline 初版用不存在的 `TaskGraph.add_task()`，改为构造时传入 `[t1, t2]`，并补齐 Task 必需的 owner_role 参数。
- **RetryPolicy 参数**：初版误把 `RetryController(RetryPolicy(max_attempts=3, ...))` 当作可用构造，实际 RetryPolicy 无参数（只有 max_retries_per_task 类属性）；改为直接 `RetryController()`。
- **mypy 不变性**：`SimulatedAdapter(script=list[str])` 但期望 `list[ScriptStep | dict | str]`；用 `cast(list[Any], steps)` 显式绕过，避免类型层级传播。
- **checkpoint 清理**：P5-5 初版只写 `{start,end}` 两个 checkpoint，缺少各阶段 latest；补齐 SPEC/CODE/VERIFY/DELIVER/DONE 五个阶段的 latest checkpoint，同时保留 start/end。
- **metrics 路径隔离**：初版 metrics.jsonl 写到仓库根 runtime/artifacts，门禁测试因跨运行残留导致断言 `len(lines)==2` 实际为 9；改为写到 checkpoint_dir（每个 Pipeline 实例天然隔离）。

## 5. 已知限制（如实披露）

1. **Docker UNVERIFIED**：Windows 环境无 Docker（`docker 命令不存在`），模板 Dockerfile 已提供但容器构建与运行未验证；待环境具备时补验。
2. **模型 UNVERIFIED**：真实云端模型未接入（P4-1 已标注），Pipeline.SPEC 阶段用 SimulatedAdapter 生成确定性输入（3 步 JSON）；后续 Phase 接入真实 Key 后需补线上冒烟。
3. **Pipeline 不修改源码**：当前流水线仅"执行→验证→交付"，不做真实编码（CODE 阶段只建 TaskGraph 不产生文件）；真实编码由后续 Phase（桌面 UI + Agent 接线）在 GitWorkspace 隔离内完成。
4. **Pipeline 无断点续跑逻辑**：has_checkpoint_for 目前仅供查询用；run() 每次全量执行六阶段。P5-5 "中途终止后重启续跑" 的增量跳过逻辑待后续 Phase 补齐（当前已具备 checkpoint 基础设施）。
5. **指标基线薄**：metrics.jsonl 仅记录 ok/phases/verification_passed/total 四个字段，规范符合率/返工率等深度指标需在真实项目运行中积累数据后扩展。
6. **DEPCHECK 类 UNVERIFIED**：当前环境无 C#/.NET/Java 依赖分析工具，dependency 类检查如实标注 UNVERIFIED。

## 6. 下一阶段计划（Phase 6：知识沉淀与多语言扩展）

按 docs/03 §4 与实施计划 P6-x：知识治理（入库审查、可信等级流转、弃用标记）、公共组件库、TypeScript/JavaScript/Java/C#/.NET 适配器、混合技术栈支持。
