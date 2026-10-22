# ADR-004：Phase 2 目录细化

- 状态：已接受
- 日期：2026-10-22
- 关联：ADR-000（架构总览）、规范 §14.2（目录结构）

## 背景

Phase 2 引入需求与规划工作流（访谈、需求状态机、PRD、追踪矩阵）与架构工作流（ADR、契约、方案门禁）。Phase 0 基线仅有 core/domain、core/workflow、core/orchestrator、core/audit、adapters、apps，新增能力需要明确落位，且属 §14.2 目录结构的细化，须以 ADR 留痕。

## 决策

1. 新增 core/requirements 包：machine（需求 7 态状态机）、interview（访谈引擎）、prd（PRD 生成与模板校验）、traceability（追踪矩阵）。
2. 新增 core/architecture 包：adr（ADR 生成与编号）、contract（契约落盘与校验）、plan（架构方案与阶段 3 门禁）。
3. 产物落盘位置：ADR 至 docs/architecture/decisions/；契约至 schemas/；PRD 至用户项目工作区 requirements/。

## 备选方案对比

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| A. 新建 core/requirements 与 core/architecture 两个包 | 边界清晰，与 §14.2 模块边界一致；可独立测试与演进 | 包数量增加 | **采用** |
| B. 并入 core/domain | 不新增包 | domain 仅放实体与协议校验，混入工作流逻辑会模糊边界 | 拒绝 |
| C. 放入 apps/ | 靠近入口 | 工作流属核心能力，须被 CLI 与未来桌面壳复用，不属于应用层 | 拒绝 |

## 结论

Phase 2 产物按此落位；后续 Phase 新增包继续以 ADR 记录，保持 §14.2 目录结构可追溯。
