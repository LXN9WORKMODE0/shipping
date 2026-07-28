# Phase 2 Task Package 01

本任务包记录 Phase 2 的结构产物强化目标。它保留实现边界、测试要求和验收口径，不再包含多 agent 分工或交接格式。

## 1. Goal

- 把 `structure.json` 升级为可调试、可解释、可复用的核心语义层。
- 让后续流程只消费结构层，不再重复猜测 parent、ordinal 或节点类型。

## 2. Scope

- 扩展 `SectionNode` 和 `DocumentStructure` 字段。
- 生成 `diagnostics.json` 和 `outline.json`。
- 在 `parse` 阶段统一落盘结构诊断产物。

## 3. Non-goals

- 不引入 LLM 结构修正。
- 不改 Phase 4 的摘要策略。

## 4. Required Model Changes

- `SectionNode` 增加 `parent_id`、`node_type`、`ordinal`、`is_low_confidence`、`evidence`、`llm_reviewed`。
- `DocumentStructure` 增加 `diagnostics_version` 和 `low_confidence_node_ids`。
- 新增 `LowConfidenceNode` 与 `DiagnosticsReport`。
- 新增 `OutlineNode` 作为轻量结构视图。

## 5. New Artifacts

- `workspace/<paper_id>/structure/structure.json`
- `workspace/<paper_id>/structure/diagnostics.json`
- `workspace/<paper_id>/structure/outline.json`

## 6. Implementation Requirements

- `WorkspaceRepository` 需要支持 diagnostics 与 outline 的读写。
- `DocumentStructureBuilder` 需要补齐 parent、ordinal、node type 和低置信状态。
- `ParseStructureUseCase` 需要把结构诊断产物与 `run.json` 一起落盘。

## 7. Test Requirements

- 结构模型 round-trip 不丢字段。
- `parse` 后 `diagnostics.json` 与 `outline.json` 存在且内容稳定。
- `low_confidence_node_ids` 与 diagnostics 中的低置信节点保持一致。
- 旧的 `convert -> parse -> summarize` stub 集成流程继续通过。

## 8. Acceptance Criteria

- `structure.json` 能单独承担后续结构消费。
- `diagnostics.json` 能解释低置信节点来源。
- `outline.json` 是 `structure.json` 的派生产物，而不是另一套独立结构。
- 关键结构字段必须持久化，不允许只存在于内存中。
