# Phase 1 证据报告：最小可靠核心

> 依据 docs/03 §6：完成内容、修改文件、执行过的命令、测试证据、失败项、已知限制、下一阶段计划。
> 日期：2026-10-22；分支：main；范围：P1-1 ~ P1-9。

## 1. 完成内容

| 任务 | 交付 | 提交 |
|---|---|---|
| P1-1 领域实体与协议 Schema | `core/domain/`（实体/枚举/SchemaRegistry）、`schemas/` 五份 JSON Schema | 3c47b41 |
| P1-2 任务状态机 | `core/workflow/`（§9.2 转移表 26 条合法边、证据驱动、乐观锁、门禁拒绝） | 4a7f6ff |
| P1-3 任务依赖图 | `core/orchestrator/dag.py`（循环检测含路径、拓扑就绪调度、受影响闭包） | 29e79d0 |
| P1-4 持久化与崩溃恢复 | `store.py`（SQLite WAL + schema_migrations）、`checkpoint.py`（原子写）、`recovery.py` | 1fa4be8 |
| P1-5 审计与脱敏 | `core/audit/`（脱敏过滤器 + SQLite/JSONL 双写，写入前强制脱敏） | 95b9f82 |
| P1-6 模拟模型适配器 | `adapters/models/`（统一接口、脚本化响应、四类失败注入、用量记录） | 9ea20ee |
| P1-7 重试与预算控制 | `core/orchestrator/retry.py`（读 config 预算、根因先行、签名升级、超限即停） | f57156a |
| P1-8 命令行 | `apps/cli`（create-project/status/pause/resume/cancel/retry-task，真实执行） | 0f2471d |
| P1-9 测试接 CI | mypy 覆盖扩至 scripts/core/adapters/apps（ci.yml 读配置不变） | 01e032f |

## 2. 执行过的命令与退出码（仓库根，全部 0）

- `python -m pytest` → **151 passed**（unit 137 + integration 14）
- `python -m ruff check .` → All checks passed
- `python -m ruff format --check .` → 50 files already formatted
- `python -m mypy` → no issues found in 27 source files
- `python scripts/validate/check_baseline.py` → 基线校验 10/10 通过
- `python -m apps.cli --help`（子进程冒烟）→ 退出码 0

## 3. 测试证据（门禁对应）

- **阶段门禁 1（模拟适配器验证工作流状态与失败处理）**：
  `tests/integration/test_workflow_failure_loop.py` —— 模拟适配器持续注入 timeout，
  相同错误签名连续两次即升级；任务经 FAILED→REWORK×3 后第四轮转 BLOCKED，
  retry_count=3、blocked_reason 记录、8 次调用全部留有用量与错误记录。
- **阶段门禁 2（崩溃恢复）**：`tests/integration/test_crash_recovery.py` —— 子进程
  `os._exit(1)` 模拟强杀，父进程恢复：IN_PROGRESS 回退 READY 重跑、PASSED 不动、
  检查点完整可读。
- 红线验证用例：UNVERIFIED 不计入通过、失败证据不得用于成功向转移、
  取消须授权、超限即停不无限循环、秘密样例脱敏（sk-/AKIA/ghp_/私钥块等）。

## 4. 失败项与修复

- P1-2 初版测试中"FAILED 报告 + 失败证据"组合先被证据层拒绝（exit_code≠0），
  修正用例使证据非失败、报告实体为 FAILED 以打入门禁 —— 行为符合设计。
- P1-3 拓扑排序初版误反转后序序列，已通过依赖优先语义修正并加菱形拓扑断言。

## 5. 已知限制（如实披露）

1. 模拟适配器非真实模型调用，不得视为多模型能力证据（§1.9）；真实接入在 Phase 4。
2. 状态机门禁的上下文（依赖通过/审查结论/测试报告）由调用方控制器计算传入，
   自动接线在后续 Phase（P2/P3 编排闭环）。
3. 项目级状态机为简化合法表（CREATE/ACTIVE/PAUSED/CANCELLED），
   阶段级 12 态工作流（§8）随 P2 需求工作流实现。
4. UsageRecord 仅记录 token 估算（字符数代替），cost_usd 置 None 不伪造。
5. 审计 JSONL 暂无轮转/保留期策略（Phase 8 稳定性范畴）。

## 6. 下一阶段计划（Phase 2：需求与规划工作流）

P2-1 需求访谈流程（一次一问/选项化/假设标记 + 需求 7 态机）→ P2-2 PRD/追踪基线 →
P2-3 追踪矩阵 → P2-4 架构工作流 → P2-5 任务拆分与计划批准门禁（未批准不得编码）。
