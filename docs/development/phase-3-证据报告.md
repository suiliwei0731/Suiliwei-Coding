# Phase 3 证据报告：工程标准执行引擎

> 依据 docs/03 §6：完成内容、修改文件、执行过的命令、测试证据、失败项、已知限制、下一阶段计划。
> 日期：2026-10-22；分支：main；范围：P3-1 ~ P3-6 + 阶段门禁。

## 1. 完成内容

| 任务 | 交付 | 提交 |
|---|---|---|
| P3-1 规则 Schema 与加载器 | `schemas/rule.schema.json`、`knowledge/standards/rules-core.yaml`（12 条核心规则）、`core/standards/rules.py`（§7.2 七要素；加载即校验，失败一次性报告全部问题；manual 规则显式标记不得伪装机器强制） | c046990 |
| P3-2 项目规则注入 | `config/rulesets/*.yaml`、`core/standards/injection.py`（项目规则集合并/覆盖/追加，注入记录留痕可审计） | b8025d2 |
| P3-3 检查适配器框架 + Python 首实现 | `core/quality/checks.py`（五类检查统一结果、§16.5 四态、§7.4 证据字段、UNVERIFIED 必填原因与可复现命令）、`adapters/tools/python_checks.py`（ruff/mypy/pytest/build，argv 列表禁 shell，JUnit XML 计数与产物入证据） | bf2bf63 |
| P3-4 门禁执行与阻断 | `core/quality/gate.py`（规则类别映射与聚合、manual/custom 显式列出、未映射 FAILED 同样阻断；处置：BLOCKER 失败→REWORK/FAILED、UNVERIFIED→BLOCKED、否则→PASSED；无证据拒绝 PASSED；每次处置写审计 gate.enforced） | fc30453 |
| P3-5 豁免审批与审计 | `core/quality/waiver.py`（create_waiver 创建即校验：规则须允许豁免、审批角色授权、§17 审批人≠申请人、有效期上限；is_waiver_valid：未过期+范围覆盖 */项目/任务）；门禁接入 waivers，豁免失败不作为 PASSED 依据，每条放行写 waiver.applied | 51e5301 |
| P3-6 未验证标记与质量报告 | `core/quality/report.py`（总体三态 §16.5：阻断失败/未映射失败/任何 UNVERIFIED→未通过，豁免或非阻断失败→带条件通过，否则→通过；Markdown 渲染 UNVERIFIED 行显示"未验证"+原因+可复现命令） | 386bdcd |
| 阶段门禁集成测试 | `tests/integration/test_phase3_gate.py`（5 个负面实验，真实工具执行） | d26f4e4 |

## 2. 执行过的命令与退出码（仓库根，全部 0）

- `python -m pytest` → **350 passed**
- `python -m ruff check .` → All checks passed
- `python -m ruff format --check .` → 89 files already formatted
- `python -m mypy` → no issues found in 48 source files
- `python scripts/validate/check_baseline.py` → 基线校验 10/10 通过

## 3. 测试证据（阶段门禁对应）

docs/03 §4 乙阶段 C 验收：违规代码/失败测试不得标 PASSED；工具缺失如实标"未验证"；无豁免记录的 BLOCKER 不可跳过。

- **违规代码不得标 PASSED**：`test_ruff_violation_blocks_and_cannot_be_marked_passed` ——
  tmp 目录真实构造 F401 违规项目，PythonCheckAdapter 真实执行 ruff → FAILED（退出码非 0、
  命令/版本证据齐全）→ 门禁处置 REWORK；处置后试图标 PASSED：REWORK→PASSED 无合法边
  （IllegalTransitionError），TEST_REQUIRED+FAILED 报告被守卫拒绝（GuardRejectionError）；
  重试预算耗尽 → FAILED；修复代码后同一适配器重跑转 PASSED，闭环成立。
- **失败测试同理**：`test_failing_pytest_blocks_with_junit_counts` —— 故意失败用例真实执行
  pytest → FAILED，JUnit XML 计数（total=1/failed=1）与产物路径入证据 → REWORK。
- **工具缺失标"未验证"**：`test_missing_tool_marks_unverified_not_passed` —— 模拟工具模块
  缺失 → UNVERIFIED（含可复现命令）→ 任务转 BLOCKED；质量报告总体"未通过"，规则行渲染
  "未验证"+原因+可复现命令，绝不渲染为"通过"。
- **无有效豁免的 BLOCKER 不可跳过**：`test_expired_waiver_still_blocks` —— 过期豁免记录
  不放行（finding.waived=False），仍处置 REWORK，且无 waiver.applied 审计；
  单测另覆盖：无豁免/豁免其他规则/自审自批/角色未授权/有效期超上限全部拒绝。
- **上游 FAILED → 下游不可见**：`test_upstream_failed_hides_downstream` —— 上游 FAILED 时
  ready_tasks 为空；上游 PASSED 后下游就绪（对照成立，§16.4）。

## 4. 失败项与修复

- 集成测试首次构造违规项目时漏建目录（FileNotFoundError），补 `mkdir` 后通过。
- 违规样例误加 `# noqa: F401` 把违规抑制掉导致 ruff 误判 PASSED；移除后如实 FAILED。
- 质量报告测试断言 `"未验证" not in md` 与报告摘要行"未验证 0"字样冲突；改为表格单元格级
  断言（`"| 未验证 |"`），语义更精确。

## 5. 已知限制（如实披露）

1. 豁免记录当前由调用方构造传入，尚未持久化到 StateStore；审批流 UI 与豁免到期自动失效
   扫描随编排闭环在后续 Phase 落地。
2. 质量报告为 Markdown 文本产出，落盘与归档接线（runtime/artifacts）随编排闭环完成。
3. 检查适配器仅 Python 工具链（ruff/mypy/pytest/build）；JS/TS 等语言适配器按 ADR-005
   在后续 Phase 按需扩展。
4. BUILD 类检查在门禁映射中无对应规则类别（CUSTOM 走专用验证器/CI），未映射 FAILED
   结果按保守策略阻断并显式列出。

## 6. 下一阶段计划（Phase 4：模型、工具与 Skills 真实接入）

按 docs/03 §4：真实模型适配器（请求/流式/超时/取消/重试/错误分类/用量记录、结构化
输出校验、不可用明确报错不伪造）等，详见实施计划 P4-x 任务表。
