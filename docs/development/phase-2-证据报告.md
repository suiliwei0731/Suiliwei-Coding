# Phase 2 证据报告：需求与规划工作流

> 依据 docs/03 §6：完成内容、修改文件、执行过的命令、测试证据、失败项、已知限制、下一阶段计划。
> 日期：2026-10-22；分支：main；范围：P2-1 ~ P2-5 + 阶段门禁。

## 1. 完成内容

| 任务 | 交付 | 提交 |
|---|---|---|
| P2-1 需求访谈与状态机 | `core/requirements/machine.py`（7 态转移表+门禁+变更留痕）、`interview.py`（一次一问/选项 2-4 带影响/矛盾识别/假设标记/defer 待确认/ScriptedRespondent） | f0e8e59 |
| P2-2 PRD 生成与模板校验 | `core/requirements/prd.py`（§5.4 十三类+版本记录共 16 章节、用户故事关联校验、空章节显式占位不编造、校验不过不落盘） | dc751a2 |
| P2-3 追踪矩阵 | `core/requirements/traceability.py`（需求↔任务↔代码↔测试↔证据，可查询、缺口可检出、CSV 落盘 `requirements/traceability.csv` 格式） | f0ee15d |
| P2-4 架构工作流 | `core/architecture/`（ADR 生成/编号/落盘、契约即 JSON Schema 落盘 schemas/ 前 check_schema、架构方案与阶段 3 门禁、风险清单）；ADR-004 记录目录细化 | 2fc3a54 |
| P2-5 任务拆分与计划门禁 | `core/planning/`（TaskSpec 覆盖 §8 阶段 4 要素、仅接受 APPROVED 需求建 DAG、计划批准门禁 decided_by≠requested_by） | 5424f60 |
| 阶段门禁集成测试 | `tests/integration/test_phase2_gate.py` | f6cada2 |

## 2. 执行过的命令与退出码（仓库根，全部 0）

- `python -m pytest` → **237 passed**
- `python -m ruff check .` → All checks passed
- `python -m ruff format --check .` → 70 files already formatted
- `python -m mypy` → no issues found in 38 source files
- `python scripts/validate/check_baseline.py` → 基线校验 10/10 通过

## 3. 测试证据（阶段门禁对应）

docs/03 §4 阶段门禁：从想法到批准的需求、架构和任务计划全程可追踪；关键需求未确认时工作流阻塞。

- **全程可追踪**：`test_full_pipeline_idea_to_approved_plan_traceable` ——
  想法 → ScriptedRespondent 三轮访谈产出结构化草案（无待确认项）→
  两条需求经 DRAFT→INTERVIEWING→REVIEW_REQUIRED→APPROVED（用户确认）→
  PRD 过模板校验并落盘 → 契约落盘 + ADR 落盘 + 架构方案过阶段 3 门禁 →
  任务计划从 APPROVED 需求建 DAG → 用户批准后任务可启动 →
  追踪矩阵可查出需求↔任务链路，且准确检出"存储层无代码变更"缺口（gaps 非空，如实报告）。
- **关键需求未确认时阻塞**：`test_blocked_when_key_requirement_unconfirmed` ——
  需求携带 open_questions 时状态机拒绝进入 APPROVED（RequirementGuardRejectionError）；
  未 APPROVED 需求拆分任务被拒（RequirementNotApprovedError）；
  计划无批准记录时编码任务启动被拒（PlanNotApprovedError）。
- 红线验证用例：待确认事项不得当作事实（假设/OPEN 分离）、计划批准决定人
  不得与发起方相同（"AI 自己同意"不构成授权，§17）、ADR 同编号拒覆盖、
  契约落盘拒覆盖、PRD 空章节写显式占位而非编造内容。

## 4. 失败项与修复

- ADR-004 首次生成时标题含 `/`，被文件名清洗剔除后文件名不佳；删除后换简洁标题
  重新生成（同一编号未浪费，因清洗发生在落盘前命名阶段，模块行为符合预期）。

## 5. 已知限制（如实披露）

1. 访谈引擎为纯逻辑，Phase 2 以 ScriptedRespondent 模拟用户驱动；真实用户交互
   在 Phase 7 桌面 UI 对接。
2. PRD/追踪矩阵落盘到调用方指定目录；与用户项目工作区（runtime/）的自动接线
   随编排闭环在后续 Phase 完成。
3. 计划批准记录当前由调用方构造传入；审批持久化与过期（expires_at）联动
   随 Phase 3+ 编排器落地。
4. 契约演进策略为"拒覆盖"，版本化契约命名约定（如 xxx-v2）暂未规范化。

## 6. 下一阶段计划（Phase 3：工程标准执行引擎）

按 docs/03 §4：工程标准配置化落地、契约与测试先行执行、骨架验证等，
详见实施计划 P3-x 任务表。
