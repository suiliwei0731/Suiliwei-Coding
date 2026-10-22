# Phase 6（部分）证据报告：知识检索 → Prompt → 模型接线

> 范围说明：本轮只完成"角色写代码的依据从哪来"这条接线，即
> **本地知识检索 → 结构化 prompt → 模型调用**；实施计划中的 P6-1（知识治理）、
> P6-2（公共组件库）、P6-3~P6-5（多语言适配器）、P6-6（混合技术栈）尚未开始。
> 日期：2026-10-22；分支：main。

## 1. 完成内容

| 交付 | 说明 |
|---|---|
| `core/prompt/builder.py` | `PromptBuilder` / `RoleBrief` / `PromptBundle`：按任务检索知识条目，渲染 system + user 两段 prompt；引用携带 `id/来源/分层/可信等级/版本/得分`；默认排除 DEPRECATED；超预算截断并标记 `truncated` |
| `core/orchestrator/pipeline.py` | SPEC/CODE 两阶段由"直接返回硬编码脚本"改为"检索知识 → 组装 prompt → 调用模型"；新增 `model_mode`（injected/real/simulated）、`model_unverified_reason`、`model_calls`、`citations` 字段进入 `PipelineResult` 与 `summary()` |
| 模型解析 | 显式注入优先；否则读 `config/models.yaml`；`enabled=false` 且未设 `AI_DEV_PLATFORM_MODEL_ENABLED=1` 时回退模拟适配器并标注 UNVERIFIED；启用但缺 `BASE_URL/API_KEY/MODEL` 环境变量时同样明确回退，不伪造 |
| 测试 | `tests/unit/test_prompt_builder.py`（12 项）、`tests/unit/test_pipeline_model_wiring.py`（6 项） |

## 2. 执行过的命令与结果（仓库根，全部 0）

- `python -m pytest` → **480 passed**
- `python -m ruff check .` → All checks passed
- `python -m ruff format --check .` → 121 files already formatted
- `python -m mypy` → no issues found in 63 source files
- `python scripts/validate/check_baseline.py` → 基线校验 10/10 通过

## 3. 测试证据

- **知识确实进入 prompt**：`test_prompt_injected_into_model_request` —— 用 `CaptureAdapter`
  捕获 `CompletionRequest`，断言 prompt 同时包含 `## 知识摘录`、`## 项目上下文`、
  知识条目标题与来源；DEPRECATED 条目（"已弃用"）不出现在任何一次请求中。
- **引用可回溯**：`test_citations_carry_source_and_trust` —— 每条引用都带
  `knowledge_id/path/trust/version`，ID 连续为 `K1..Kn`。
- **预算截断显式**：`test_budget_truncation_marks_flag` —— 上下文预算不足时
  `truncated=True` 且正文带 `…（片段截断）`，不静默丢弃来源头。
- **失败不伪造**：`test_model_failure_marks_unverified_not_success` —— 模型抛错时
  SPEC/CODE 两阶段 `status=UNVERIFIED` 并记录原因；五链验证仍真实执行。
- **未配置模型明确回退**：`test_fallback_to_simulated_when_profile_disabled`、
  `test_env_enable_without_credentials_falls_back_honestly` —— 两种缺失路径都得到
  `model_mode=simulated` + 非空 `model_unverified_reason`，`summary().model_unverified=True`。
- **不越权写源码**：`test_code_stage_records_no_source_writes` —— CODE 阶段 `source_writes=0`，
  如实记录本轮不产生源码改动。

## 4. 用户使用方式（真实模型）

设置以下 4 个环境变量即可（密钥不入库、不入日志）：

```powershell
$env:AI_DEV_PLATFORM_MODEL_ENABLED   = "1"
$env:AI_DEV_PLATFORM_MODEL_BASE_URL  = "https://api.deepseek.com/v1"   # 任意 OpenAI 兼容端点
$env:AI_DEV_PLATFORM_MODEL_API_KEY   = "<你的密钥>"
$env:AI_DEV_PLATFORM_MODEL_NAME      = "deepseek-chat"
```

也可改 `config/models.yaml` 中 `cloud-openai-compatible.enabled: true` 达到同样效果。

## 5. 已知限制（如实披露）

1. **真实模型调用 UNVERIFIED**：本轮无云端凭据，未对任何厂商端点发起真实调用；
   接线正确性由捕获适配器与错误路径测试证明。配置真实 Key 后需补一次线上冒烟并留证。
2. **CODE 阶段仍不写源码**：只产出实现计划（`source_writes=0`）。真实代码写入需要
   GitWorkspace 隔离 + allowed_paths 校验 + diff 复核，属于后续任务，不在本轮范围。
3. **知识库规模小**：`knowledge/registry.yaml` 仅 4 条真实登记，检索为关键词打分，
   无向量检索/重排；条目扩充后才能体现实质收益。
4. **prompt 预算固定**：默认 6000 字符、最多 8 条，未按模型上下文窗口自适应。
5. **多语言未接入**：TypeScript/Java/C# 适配器与混合技术栈仍为 P6-3~P6-6 待办。

## 6. 后续计划

- Phase 6 剩余：P6-1 知识治理（入库审查/可信等级流转/弃用标记）、P6-2 公共组件库、
  P6-3~P6-5 多语言适配器、P6-6 混合技术栈；
- 之后：CODE 阶段真实写入源码（工作树隔离 + 越界拦截 + 回归验证），
  以及桌面壳层（Electron/Tauri）与用户配置界面。
