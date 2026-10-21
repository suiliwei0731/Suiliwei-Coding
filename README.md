# AI 自主软件研发平台（ai-dev-platform）

以自然语言想法为入口、以标准化软件工程交付为目标的多智能体研发控制平台：管理软件从需求澄清、架构设计、任务编排、规范化编码、自动化测试、代码审查到集成修复与交付验收的完整生命周期。最终形态为可安装的 Windows 本地桌面应用。

> 当前状态：**Phase 0 — 仓库基线与决策冻结**。本阶段仅建立仓库骨架、规范配置与自检工具，不含业务功能代码。

## 文档

平台规划文档位于仓库上级目录 `../docs/`：

- [02-统一规范-合并版.md](../../docs/02-统一规范-合并版.md) — 唯一执行基准
- [03-实施计划.md](../../docs/03-实施计划.md) — Phase 0–8 路线图与门禁
- [01-冲突与遗漏分析.md](../../docs/01-冲突与遗漏分析.md) — 规范合并的冲突裁决

仓库内决策记录见 [docs/architecture/decisions/](docs/architecture/decisions/)。

## 快速开始

```powershell
# 1. 环境自检（生成 PROJECT_CONTEXT.md）
python scripts/bootstrap/check_env.py

# 2. 基线校验（config 可解析、registry 唯一、严重度量表齐全）
python scripts/validate/check_baseline.py

# 3. 安装开发依赖并运行测试
python -m pip install -e ".[dev]"
python -m pytest
```

## 目录概览

| 目录 | 用途 |
|---|---|
| `apps/` | CLI 驱动（开发期）与 Windows 桌面 UI（产品形态） |
| `core/` | 领域模型、编排、工作流、标准（ESEE）、质量、安全、审计 |
| `adapters/` | 模型/工具/语言/数据库/打包适配器 |
| `agents/` | 按角色组织的 Agent 配置（需求、架构、前后端、测试、审查等） |
| `skills/` | 自有技能（SKILL.md 格式），唯一登记入口 `skills/registry.yaml` |
| `knowledge/` | 标准、模板、组件、模式的受治理知识库 |
| `config/` | 平台策略、模型、门禁、语言适配的集中配置 |
| `runtime/` | 运行态：状态、日志、产物、检查点（不入库） |

完整目录基线见规范 §14.2。Agent 工作规则见 [AGENTS.md](AGENTS.md)。

## 许可证

[MIT](LICENSE)
