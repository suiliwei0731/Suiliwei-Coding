# ADR-005：Phase 3 ESEE 落位与规则 Schema

- 状态：已接受
- 日期：2026-10-22
- 关联：ADR-003、ADR-004、规范 §7

## 背景

Phase 3 实现工程标准执行引擎（规范 §7）。Phase 0 基线已预留空目录 core/standards、core/quality、adapters/tools；规则初版在 knowledge/standards/rules-core.yaml（仅 ID/名称/范围/严重度/检查方式），§7.2 要求的正反例、豁免策略、规则版本等完整结构待 P3-1 定稿。

## 决策

1. ESEE 规则层落位 core/standards（规则实体/加载器/规则集注入），门禁执行层落位 core/quality（检查框架/门禁/豁免/质量报告），Python 检查适配器落位 adapters/tools；2. 规则协议登记为 schemas/rule.schema.json（§7.2 七要素），rules-core.yaml 升级为完整结构并保持基线自检第 4 项兼容（check.type auto/manual、method 非空）；3. 项目类型规则集配置放 config/rulesets/（配置集中原则）。

## 备选方案对比

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| A. 落位基线预留目录 core/standards + core/quality + adapters/tools | 与 ADR-003/§14.2 基线结构一致，无目录 churn；职责边界清晰 | 规则与门禁分两个包，跨包引用需理清 | 采用 |
| B. 新建独立 core/esee 包 | 单一包名直达 ESEE 概念 | 违背基线预留结构；§14.2 模块边界需整体重议 | 拒绝 |
| C. 规则全部塞进 config/ YAML，不建实体层 | 不新增代码 | §7.2 要求规则可执行化管理，无实体层则注入/门禁/豁免无处挂载 | 拒绝 |

## 结论

按决策落位；规则 Schema 变更即契约变更，后续修订走 schemas/rule.schema.json 版本演进。
