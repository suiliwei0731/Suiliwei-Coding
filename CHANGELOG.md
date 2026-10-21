# 变更日志

本项目的所有重要变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [0.1.0] - 2026-10-21

### 新增（Phase 0：仓库基线与决策冻结）

- 按规范 §14.2 建立完整目录骨架（apps/core/adapters/agents/skills/knowledge/config/runtime/tests 等）。
- 根文件：AGENTS.md（仓库级 Agent 规则）、CLAUDE.md（薄指针）、CONTRIBUTING、LICENSE（MIT）、.gitignore、.editorconfig、.env.example。
- pyproject.toml：Python >= 3.12，dev 依赖 ruff/mypy/pytest/pyyaml。
- ADR-000（架构总览）、ADR-001（存储选型）、ADR-002（模型接入）、ADR-003（基线决策 D1–D10）。
- config/ 五份配置初版：platform / models / policies / quality-gates / language-adapters。
- skills/registry.yaml：11 个自有技能清单（planned/draft）＋ requirements-interview 技能示范（draft）。
- knowledge/standards/rules-core.yaml：首批 12 条通用工程规则（含 ID/严重度/检查方式）。
- scripts/bootstrap/check_env.py：环境自检并生成 PROJECT_CONTEXT.md。
- scripts/validate/check_baseline.py：基线校验（配置可解析、注册表唯一、严重度量表齐全）。
- tests/unit/test_baseline.py：基线单元测试。
- CI：.github/workflows/ci.yml（windows-latest，lint/type/test/基线校验）。

### 已知限制

- Git/Node/Docker 在当前开发机未安装（见 PROJECT_CONTEXT.md 环境报告）；涉及它们的验证如实标注"未验证"。
