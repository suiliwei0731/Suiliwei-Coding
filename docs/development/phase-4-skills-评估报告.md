# Phase 4 候选 Skills 仓库评估报告（P4-5）

- 评估日期：2026-10-22
- 依据：`docs/03-实施计划.md` P4-5；`config/policies.yaml` `skills.external_pipeline`
- 方法：GitHub REST API 读取仓库元数据与目录结构（`api.github.com/repos/...`），
  逐项核对仓库级许可证、技能格式、依赖与平台兼容性。**本轮不安装、不执行任何外部代码。**
- 统一红线：外部技能指令性内容不得高于平台安全策略（`skills.external_policy_precedence:
  platform`）；任何采用候选必须经 P4-4 流水线 `register → review → sandbox_trial →
  pin_version → enable`，未固定版本不得启用。

## 结论总览

| # | 仓库 | 许可证（仓库级） | 技能格式 | 结论 | 关键理由 |
|---|---|---|---|---|---|
| 1 | `anthropics/skills` | 无仓库级 LICENSE（各技能目录内含 `LICENSE.txt`） | `SKILL.md` + `spec/` + `template/` | **暂缓** | 官方参考实现、格式完全兼容，但仓库级许可缺失，须先确认授权并逐技能核对 |
| 2 | `obra/superpowers` | MIT | 每个技能目录 `SKILL.md` | **暂缓（限定子集试点）** | 许可证明确、格式兼容、无 npm 运行依赖；但整仓含 hooks/多平台插件与流程方法论，须隔离试用并裁剪 |
| 3 | `ComposioHQ/awesome-claude-skills` | 无仓库级 LICENSE（单技能目录许可证不一致，如 `artifacts-builder` 为 Apache-2.0） | `SKILL.md` + 脚本 + 连接器 | **拒绝** | 聚合仓、许可碎片化、含第三方应用连接器（外部凭据与数据外联风险），不适合整仓接入 |
| 4 | `wshobson/agents` | MIT | 多 harness 插件（`.claude-plugin/`、agents/commands/skills） | **拒绝** | 插件市场形态超出平台 `SKILL.md` 协议，体量大且指令面复杂，与平台角色/权限模型冲突 |
| 5 | `VoltAgent/awesome-claude-code-subagents` | MIT | Claude Code subagent Markdown + `install-agents.sh` | **拒绝** | 非 Agent Skills 格式，安装脚本直接写入用户目录，违反工作区白名单与最小权限原则 |

**本轮无候选进入 `ENABLED`**：外部技能注册表保持为空（`knowledge/skills-registry/registry.yaml`），
避免未经审查/试用/固定版本的采用。

## 逐项评估

### 1. anthropics/skills

- 现状：GitHub 182,820 stars；最近推送 2026-10-09；主分支 `main`；语言 Python；
  描述 “Public repository for Agent Skills”。
- 结构：根目录含 `skills/`（academy-guide、algorithmic-art、brand-guidelines、canvas-design、
  claude-api、doc-coauthoring、docx、frontend-design、internal-comms、mcp-builder、pdf 等）、
  `spec/`、`template/`、`.claude-plugin/`、`THIRD_PARTY_NOTICES.md`；**无仓库级 `LICENSE` 文件**。
  单个技能示例：`skills/algorithmic-art/` 含 `LICENSE.txt`、`SKILL.md`、`templates/`。
- 许可证：仓库级缺失；GitHub API `license` 为空。逐技能 `LICENSE.txt` 内容未逐份核验（UNVERIFIED）。
- 依赖：未发现仓库级 Python/Node 依赖清单；技能以 Markdown + 模板为主。
- 兼容性：`SKILL.md` 格式与平台技能注册表一致，可映射 `skills/registry.yaml` 的 `path` 约定。
- 结论：**暂缓**。理由：官方性最强、格式兼容，但缺少仓库级授权文件，采用前必须取得明确许可并
  逐技能核对 `LICENSE.txt`；在许可确认前不登记、不试用。

### 2. obra/superpowers

- 现状：GitHub 303,247 stars；最近推送 2026-10-21；主分支 `main`；语言 Shell；
  描述 “An agentic skills framework & software development methodology”。
- 结构：`skills/`（brainstorming、systematic-debugging、test-driven-development、
  using-git-worktrees、requesting-code-review、executing-plans 等 12 个技能）、`hooks/`、
  `index.js`、`package.json`、多平台插件目录（`.claude-plugin/`、`.codex-plugin/` 等）、`tests/`。
- 许可证：MIT（仓库级 `LICENSE`，GitHub API `spdx_id=MIT`）。
- 依赖：`package.json`（v7.0.1）无 `dependencies`/`devDependencies` 字段；运行时入口为
  `.opencode/plugins/superpowers.js` 与 `.pi/extensions/superpowers.ts`（平台不使用）。
- 兼容性：单技能为 `SKILL.md`（如 `skills/brainstorming/SKILL.md`），内容可拆分映射；
  hooks、插件运行时与多 harness 配置**不兼容**平台执行模型，不在接入范围。
- 结论：**暂缓（限定子集试点）**。仅允许在隔离工作树中试用与平台流程一致的技能子集
  （如 `test-driven-development`、`systematic-debugging`），排除 hooks/插件运行时；
  试点通过后再按流水线固定 commit 版本并登记。

### 3. ComposioHQ/awesome-claude-skills

- 现状：GitHub 77,636 stars；最近推送 2026-09-18；主分支 `master`；语言 Python；
  描述 “A curated list of awesome Claude Skills, resources, and tools”。
- 结构：根目录为大量技能目录（artifacts-builder、brand-guidelines、composio-skills、
  connect-apps、langsmith-fetch、webapp-testing 等）与 `connect-apps-plugin/`；
  根目录**无 LICENSE**。`artifacts-builder/` 含 `LICENSE.txt`（Apache License 2.0）、
  `SKILL.md`、`scripts/`。
- 许可证：仓库级缺失；各技能目录许可证不一致（Apache-2.0 仅为个别目录核验结果）。
- 依赖：部分技能含脚本；`composio-skills`/`connect-apps` 涉及第三方应用连接与凭据，
  与 `policies.yaml` 的网络允许列表、secrets 禁止出本地约束冲突。
- 兼容性：格式部分兼容（`SKILL.md`），但许可与安全边界不可统一保证。
- 结论：**拒绝**。不整仓接入；如未来需要个别技能，须独立 fork 后逐技能重新登记、
  审查许可证与数据流。

### 4. wshobson/agents

- 现状：GitHub 41,501 stars；最近推送 2026-10-19；主分支 `main`；语言 Python；
  描述多 harness 的 “agentic plugin marketplace”。
- 结构：`plugins/`（accessibility-compliance、agent-orchestration、backend-development、
  api-testing-observability 等大量插件）、`.claude-plugin/`、`.codex-plugin/`、
  `agents/`、`commands/`、`skills/`、`evals/`、`tools/`、`Makefile`。
- 许可证：MIT（仓库级 `LICENSE`）。
- 依赖：根目录无 `package.json`/`pyproject.toml`；含 `Makefile`、`tools/`、`evals/`，
  插件内部结构超出单一 `SKILL.md` 协议。
- 兼容性：插件同时包含 agents、commands、skills，指令面与平台角色定义、权限层重叠；
  无法保证“外部指令不高于平台策略”。
- 结论：**拒绝**。不导入插件运行时；如需参考其 agent 编排思路，仅作阅读参考，不入注册表。

### 5. VoltAgent/awesome-claude-code-subagents

- 现状：GitHub 25,938 stars；最近推送 2026-10-11；主分支 `main`；语言 Shell；
  描述 “100+ specialized Claude Code subagents”。
- 结构：`categories/`（01-core-development … 10-research-analysis）、`install-agents.sh`、
  `tools/`、`.claude-plugin/`、`CLAUDE.md`；分类目录逐文件清单因 GitHub API 403 未取得
  （UNVERIFIED），但仓库定位为 Claude Code subagent 集合。
- 许可证：MIT（仓库级 `LICENSE`）。
- 依赖：Shell 安装脚本；不提供 `SKILL.md` 技能包。
- 兼容性：subagent 定义格式与平台 `SKILL.md` 不兼容；安装脚本直接写入用户目录，
  违反工作区白名单与最小权限原则。
- 结论：**拒绝**。不执行安装脚本、不导入定义文件。

## 后续动作

1. `anthropics/skills`：向权利方确认仓库级许可；确认前状态保持“暂缓”，不登记。
2. `obra/superpowers`：若启动试点，先建立独立任务工作树，导入限定技能子集，
   按 P4-4 流水线记录 review/trial 证据并固定 commit；未通过则 `REJECTED`。
3. 已拒绝来源：不进入注册表；如需复用内容，须独立 fork 并重新评估许可证与数据流。
4. 任何外部技能启用前必须满足：来源可追踪、许可证明确、版本固定、审查与试用证据齐全
   （P4-4 `ExternalSkill.usable`）。
