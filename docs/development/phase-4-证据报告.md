# Phase 4 证据报告：模型、工具与 Skills 真实接入

> 依据 docs/03 §4：完成内容、修改文件、执行过的命令、测试证据、失败项、已知限制、下一阶段计划。
> 日期：2026-10-22；分支：main；范围：P4-1 ~ P4-6 + 阶段门禁。

## 1. 完成内容

| 任务 | 交付 | 提交 |
|---|---|---|
| P4-1 真实模型适配器 | `adapters/models/openai_compat.py`（OpenAI 兼容 Chat Completions：请求/SSE 流式/超时/取消/重试/错误分类/用量记录/结构化输出校验；未启用或缺 env 明确抛 ModelError，不伪造成功；审计不含密钥）、`adapters/models/base.py`（新增 ModelCancelled，不可重试） | 9268acb |
| P4-2 工具权限层 | `core/security/policy.py`（工作区路径白名单与目录穿越拦截、命令白名单/危险命令正则、执行超时与输出截断、网络允许列表；每次拒绝写 security.denied）、`config/policies.yaml`（allowed_executables） | e1b1817 |
| P4-3 Git 隔离 | `core/workspace/git.py`（任务级分支与 worktree、小提交、diff/未跟踪文件采集、allowed_paths 前缀校验与越界审计 workspace.violation；基线固定为提交哈希避免 HEAD 漂移）、`.gitignore`（runtime/worktrees/） | 563faa1 |
| P4-4 外部 Skill 注册表与接入流水线 | `core/skills/external.py`（REGISTERED→REVIEWED→SANDBOX_TRIAL→PINNED→ENABLED，REJECTED 终态；许可证未知不可固定版本；登记/审查/试用/固定/启用/拒绝全量审计）、`knowledge/skills-registry/registry.yaml` | 3630ed7 |
| P4-5 候选 Skills 评估报告 | `docs/development/phase-4-skills-评估报告.md`（5 个候选仓库逐一审查来源/许可证/依赖/兼容性/结论） | 7558bf6 |
| P4-6 知识库基础检索 | `core/knowledge/retrieval.py`（PLATFORM/PROJECT/LANGUAGE/DOMAIN 分层过滤、ASCII+中文二元组关键词检索、可信等级随结果传递、DEPRECATED 默认不推荐）、`knowledge/registry.yaml`（4 条真实登记） | d004057 |
| 阶段门禁集成测试 | `tests/integration/test_phase4_gate.py`（5 个负面实验 + 1 条真实 HTTP 超时→恢复正向链路） | 本次提交 |

## 2. 执行过的命令与退出码（仓库根，全部 0）

- `python -m pytest` → **435 passed**
- `python -m ruff check .` → All checks passed
- `python -m ruff format --check .` → 106 files already formatted
- `python -m mypy` → no issues found in 56 source files
- `python scripts/validate/check_baseline.py` → 基线校验 10/10 通过

## 3. 测试证据（阶段门禁对应）

docs/03 §4 乙阶段 D + 甲 Phase 3 验收：真实模型在受控工作区完成小任务、失败可恢复；
技能版本固定；来源/版本/审查状态/验证证据可追踪。

- **未配置模型不得伪造成功**：`test_unconfigured_model_never_reports_success` ——
  `config/models.yaml` 默认 `enabled=false`，调用直接抛 `ModelError（未启用）`；
  运行记录为 FAILED、用量记录为空、审计中不存在 `model.called`。
- **越权访问被拦截并审计**：`test_security_denials_are_audited` —— 工作区外路径、
  `git push --force`、白名单外 `curl` 三类请求全部抛 `SecurityDenied`，产生 3 条
  `security.denied` 审计（kind=path/command），拒绝行为可追踪。
- **Git 越界修改被检测并告警**：`test_scope_violation_detected_and_audited` ——
  在任务工作树中同时改动 `src/` 与 `docs/`，提交后按 `allowed_paths=["src"]` 校验，
  检出 `docs/notes.md` 越界并抛 `ScopeViolation`，审计 `workspace.violation` 带 task_id。
- **外部技能未审查/未固定不得启用**：`test_external_skill_cannot_enable_before_review_and_pin` ——
  REGISTERED 直接启用被拒（非法流转）；完成审查→隔离试用→版本固定后才可 ENABLED；
  持久化后版本 `0123abc456`、来源、审查/试用证据齐全，`skill.enabled` 审计可追踪。
- **DEPRECATED 知识不被默认推荐**：`test_deprecated_knowledge_not_recommended` ——
  同一查询默认仅返回 VERIFIED 条目；显式 `include_deprecated=True` 才返回旧条目，
  可信等级随结果传递。
- **模型超时→恢复→产物提交→范围校验（正向闭环）**：`test_model_timeout_then_recovery_in_task_worktree` ——
  本地真实 HTTP OpenAI 兼容端点，首轮响应延迟 0.6s、客户端超时 0.2s → `ModelTimeout`、
  运行状态 TIMED_OUT；端点恢复即时响应后同一适配器调用成功，结构化输出通过 task schema
  校验，用量记录 1 条；模型产物写入任务工作树并以小提交落库，`allowed_paths=["src"]`
  范围校验通过（无越界）；审计同时含 `model.failed` 与 `model.called`，且全文不含 API Key。

## 4. 失败项与修复

- 门禁正向链路首跑失败：适配器请求了 `schema="task"` 但未注入 `SchemaRegistry`，
  抛 `ModelError（请求结构化输出但未提供 SchemaRegistry）`。补齐依赖后通过；该失败恰好
  验证了"缺少证据/能力时明确报错"的约束。
- 门禁本地服务初版用类级 monkey-patch 动态改写延时，存在线程与恢复遗漏风险；改为
  服务端持有 `delay_ref` 列表、处理器按请求读取，并在客户端超时断开时吞掉
  BrokenPipe/ConnectionAborted，避免测试噪声。
- P4-3 初版 `base_ref="HEAD"` 为符号引用，提交后 diff 为空；改为创建工作树即固定为提交哈希。
- P4-3 `_normalize` 用 `lstrip("./")` 误删 `.github` 前导点；改为循环剥离 `./`。
- P4-1 Windows 连接失败会先触发超时而非立即失败：测试改为注入 `URLError(ConnectionRefusedError)` 确定性验证网络错误分类。

## 5. 已知限制（如实披露）

1. **真实云端模型调用 UNVERIFIED**：当前环境无云端 API Key。P4-1 的协议正确性、超时、
   取消、流式、错误分类与用量记录均由本地 HTTP 服务真实验证（真实 socket 请求/SSE），
   但未对任何商业模型厂商端点发起调用；接入真实 Key 后需补一次线上冒烟并留证。
2. **本轮无外部技能进入 ENABLED**：P4-5 结论为 3 拒绝、2 暂缓（许可待确认/仅限子集试点），
   `knowledge/skills-registry/registry.yaml` 保持空表；流水线能力已由单测与门禁覆盖。
3. VoltAgent 仓库分类目录请求返回 403，评估报告中该部分标注 UNVERIFIED，未推测填充。
4. 工具权限层当前以配置驱动的静态策略执行，未接入进程级沙箱/资源配额；GPU、容器等
   更强隔离随部署形态在后续 Phase 评估。
5. 知识检索为本地关键词打分，无向量检索与重排；登记表为 4 条真实条目，规模扩大后需
   评估索引构建与增量更新。

## 6. 下一阶段计划（Phase 5：单语言端到端闭环）

按 docs/03 §4 与实施计划 P5-x：Python 语言适配器（模板/依赖锁定/ruff/mypy/pytest/build/Docker）、
想法→需求→架构→任务→初始化→编码→审查→测试→缺陷循环→构建→交付报告全链路贯通、
交付物生成器、缺陷注入实验、中断恢复实验与指标基线采集。
