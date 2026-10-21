# 贡献指南

> 本仓库当前处于 Phase 0（仓库基线），由 AI Agent 按实施计划推进；人工贡献前请先阅读 [AGENTS.md](AGENTS.md)。

## 开发环境

- Windows 优先；Python >= 3.12；Git（未安装时相关步骤如实标注"未验证"）。
- 安装依赖：`python -m pip install -e ".[dev]"`

## 必须遵守

1. **红线不可违反**（见 AGENTS.md 第 3 节）：不伪造通过；未验证标"未验证"；状态转移只由控制器依据证据触发。
2. **阶段纪律**：只做当前 Phase 计划内任务；Phase 0 禁止业务功能代码。
3. **禁止空壳代码**：不为凑目录结构生成无用途的占位模块。
4. **决策变更走 ADR**：修改 `docs/architecture/decisions/` 并说明理由，不口头变更。

## 代码风格

- 提交前运行：`ruff check . && ruff format --check . && mypy scripts && pytest`
- 注释、文档、commit message 使用中文。
- Commit 格式：`type(scope): 主题`，如 `feat(core): 任务状态机`、`docs(adr): 记录存储选型`。
- 不跳过 hooks、不放宽断言换取绿灯、不删除测试。

## 证据要求

每个阶段的产出须附证据：执行命令、退出码、测试结果；无法执行的验证标注"未验证"及原因与可复现命令。
