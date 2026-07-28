# Phase 3 Task Package 01

本任务包记录 Phase 3 的低置信节点 LLM 修正目标。它保留实现约束、测试要求和验收口径，不再包含多 agent 分工或交接格式。

## 1. Goal

- 只把 LLM 用在规则难以稳定判断的低置信节点上。
- 保留规则结构为主，避免让 LLM 接管整篇文档的结构判定。

## 2. Scope

- 新增低置信节点 refinement 链路。
- 定义 LLM 输入输出契约。
- 合并建议时保证树结构一致性。
- 保存 refinement 审计记录，并在失败时回退到规则结构。

## 3. Non-goals

- 不让 LLM 直接重建整篇论文的层级树。
- 不修改 Phase 4 的摘要产品设计。

## 4. Required Interface Changes

- `LLMClient.refine_low_confidence_nodes(...)`
- `WorkspaceRepository.save_refinement_record(...)`
- `WorkspaceRepository.load_refinement_record(...)`
- `ParseStructureUseCase` 在 parse 中接入 refinement 与 fallback 逻辑

## 5. New Models and Artifacts

- `RefinementSuggestion`
- `RefinementRecord`
- `workspace/<paper_id>/structure/refinement.json`

## 6. Merge Rules

- 只允许修改 `role`、`semantic_level`、`confidence`、`evidence`。
- 禁止修改 `id`、`parent_id`、`ordinal`、`content_ref`、`title_raw`、`title_norm`。
- 不允许建议破坏父子层级关系或导致非法 reparenting。
- merge 完成后必须重算 `is_low_confidence`、`low_confidence_node_ids` 和结构统计字段。

## 7. Pipeline Requirements

- 只有低置信节点进入 refinement。
- refinement 失败时 parse 不能整体失败，必须保留规则结果。
- refinement 失败必须落盘 warning 到 diagnostics 和 `run.json`。
- `run.json` 需要能区分 `phase3_complete` 与 `phase3_fallback`。

## 8. Test Requirements

- LLM 只收到低置信节点。
- 未授权字段修改会被拒绝。
- 未知 `node_id` 会被跳过并记录。
- 非法 `role` / `semantic_level` 变更不会破坏结构树。
- refinement 成功后 `llm_reviewed`、低置信状态与持久化产物一致。
- refinement 失败时 parse 会回退并把 warning 写入 artifact。

## 9. Acceptance Criteria

- LLM 只修低置信节点，不接管整篇结构。
- 所有 refinement 都有持久化记录或 fallback 说明。
- merge 规则足够严格，不会破坏现有树结构。
- parse 在 LLM 不可用时仍然稳定可运行。
