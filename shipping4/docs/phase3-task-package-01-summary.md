# Phase 3 Task Package 01 — 实现总结

## 1. 概述

本任务包为 Phase 3 的第一轮，实现了 LLM 修正链路与现有结构层的对接。核心约束：

- **只修正低置信度节点**，不重新判定整篇文章的完整层级
- **只允许修改受控字段**（role、semantic_level、confidence、evidence）
- **全链路可审计、可回放、可降级**

---

## 2. 新增文件

| 文件 | 作用 |
|------|------|
| `src/parsing/refinement.py` | LLM 修正引擎：校验、合并、拒绝规则 |
| `tests/test_refinement.py` | 19 个测试用例，覆盖所有合并规则和集成路径 |

---

## 3. 修改文件

### 3.1 `src/core/models.py`

新增两个数据类：

**`RefinementSuggestion`** — 单条 LLM 修正建议
```
node_id: str
role: str | None
semantic_level: int | None
confidence: float | None
evidence: list[str] | None
reason: str
forbidden_fields: set[str]   # LLM 尝试修改的禁止字段
```

- `from_dict()` 会自动检测 LLM 尝试修改的禁止字段并记录到 `forbidden_fields`
- `applied_fields()` 只返回允许修改的字段
- `has_forbidden_attempt()` 检测是否尝试修改禁止字段

**`RefinementRecord`** — 整次 LLM 修正的审计记录
```
paper_id: str
generated_at: str
input_node_ids: list[str]      # 输入的低置信度节点
applied_node_ids: list[str]    # 被接受的节点
skipped_node_ids: list[str]    # 被跳过的节点
suggestions: list[RefinementSuggestion]
skipped_details: dict[str, str] # node_id -> 拒绝原因
```

落盘为 `workspace/<paper_id>/structure/refinement.json`。

### 3.2 `src/clients/llm.py`

**`refine_low_confidence_nodes()`** 接口：

**输入**：`paper_id`、`detected_schema`、`language`、低置信度节点列表（只含必要字段：id、title_raw、title_norm、role、semantic_level、confidence、evidence、parent_id、ordinal）

**输出**：JSON 数组，每项包含 `node_id`、`reason`，以及可选的 `role`、`semantic_level`、`confidence`、`evidence`

**LLM 禁止修改的字段**：`id`、`parent_id`、`ordinal`、`content_ref`、`title_raw`、`title_norm`

**降级行为**：若 LLM 不可用，返回空列表并记录 warning，不抛异常。

### 3.3 `src/workspace/repository.py`

新增两个方法（与现有 `save_diagnostics` / `load_diagnostics` 风格一致）：

```python
save_refinement_record(paper_id, record) -> Path
load_refinement_record(paper_id) -> RefinementRecord | None
```

落盘路径：`workspace/<paper_id>/structure/refinement.json`

### 3.4 `src/parsing/refinement.py`

**`RefinementEngine.refine()`** — 核心修正引擎

合并规则（按优先级）：

1. **未知 node_id** → 忽略并记录
2. **禁止字段变更** → 忽略并记录
3. **语义层级不一致**（子节点级别 ≤ 父节点级别）→ 忽略并记录
4. **通过全部检查** → 应用变更，标记 `llm_reviewed=true`，向 evidence 追加 `llm_applied`

### 3.5 `src/pipeline/parse.py`

**`ParseStructureUseCase`** 新增构造函数参数：

```python
llm_client: LLMClient | None = None
refinement_enabled: bool = True
```

**`execute()` 流程**（refinement 开启时）：

```
1. 按现有规则生成 structure.json
2. 若有低置信度节点 且 llm_client 可用 → 调用 RefinementEngine
3. 保存 refinement.json（无论是否实际修正了节点）
4. 保存更新后的 structure.json
5. 重新生成 diagnostics.json 和 outline.json
6. parse_stage = "phase3_complete"（LLM 实际运行过）
```

**降级**：LLM 调用失败不影响 parse 全流程，保留规则结果并写 warning。

---

## 4. LLM Contract 设计摘要

### 4.1 输入格式（用户提示词片段）

```
Paper ID: {paper_id}
Detected schema: {detected_schema}
Language: {language}

Low-confidence nodes:
[node_id, title_raw, title_norm, role, semantic_level, confidence, evidence, parent_id, ordinal]
```

### 4.2 输出格式（期望的 JSON 结构）

```json
[
  {
    "node_id": "node_001",
    "reason": "标题样式表明这是一个章节而非小节",
    "role": "body",
    "semantic_level": 2,
    "confidence": 0.85,
    "evidence": ["heading_pattern_match"]
  }
]
```

### 4.3 校验规则

| 规则 | 处理方式 |
|------|---------|
| 非 JSON 响应 | 忽略，记录 warning |
| 缺少 node_id | 忽略，记录 skipped_details |
| 尝试修改禁止字段 | 忽略，记录 skipped_details |
| 语义层级会破坏树结构 | 忽略，记录 skipped_details |
| node_id 不在低置信度列表中 | 忽略，记录 skipped_details |

---

## 5. Merge / Skip 规则摘要

| 场景 | 结果 | 记录 |
|------|------|------|
| LLM 返回空列表 | 正常返回 | skipped_details 为空 |
| 未知 node_id | 跳过该条 | skipped_details[node_id] = "unknown node_id" |
| 尝试修改禁止字段 | 跳过该条 | skipped_details[node_id] = "attempted forbidden fields: {fields}" |
| 语义层级不一致 | 跳过该条 | skipped_details[node_id] = "would break parent-child semantic level ordering" |
| 所有检查通过 | 应用变更 | applied_node_ids 包含该 node_id |

---

## 6. 测试覆盖

### 6.1 新增测试（`tests/test_refinement.py`）

- `TestRefinementModels`：4 个测试 — 模型序列化/反序列化、applied_fields、forbidden_fields 检测
- `TestFlattenNodes`：1 个测试 — 树扁平化辅助函数
- `TestParseSuggestions`：2 个测试 — 有效/无效建议解析
- `TestMergeSuggestions`：4 个测试 — 接受、拒绝（未知ID、禁止字段、层级不一致）
- `TestRefinementEngine`：4 个测试 — 跳过无低置信度节点、禁用时跳过、仅传低置信度节点、空响应不崩溃
- `TestParseStructureUseCaseRefinement`：4 个测试 — refinement.json 落盘、禁用时不调用 LLM、parse_stage 正确、原有 stub 流程兼容

### 6.2 测试结果

```
48 passed in 0.92s
```

---

## 7. 验收标准达成情况

| 验收标准 | 状态 |
|---------|------|
| LLM 不接管整篇结构判定，只处理低置信度节点 | ✅ — `input_node_ids` 仅含低置信度节点 |
| 所有 LLM 修正都有落盘记录，可追踪 | ✅ — `refinement.json` 记录 applied/skipped 及原因 |
| 合并规则足够严格，不会破坏现有树结构一致性 | ✅ — 语义层级校验阻止父子关系破坏 |
| parse 在 LLM 不可用时仍可退回到纯规则结果 | ✅ — try/except 包裹 LLM 调用，失败时继续 |

---

## 8. 仍未解决的边界问题

1. **连续 refinement** — 当前实现不支持对同一结构多次调用 refinement（如需迭代修正）；如需支持，需在 `RefinementRecord` 中增加 `iteration` 字段
2. **parent_id 变更** — 当前 merge 规则未处理 LLM 建议修改 `parent_id` 的情况（实际上 LLM 也不会收到这个字段，但结构层面若需要支持子节点迁移到另一个父节点，需要额外处理）
3. **prompt 优化** — 当前 prompt 为初始版本，未经过调优；任务包说明中明确"不追求提示词最优"
4. **并发 refinement** — 不支持同一 paper_id 并行多次 refinement，需要外部锁机制

---

## 9. Handoff  checklist

- [x] 修改文件列表完整
- [x] LLM contract 设计明确
- [x] merge/skip 规则完整
- [x] 新增测试及结果（48 passed）
- [x] 原有 stub 流程验证通过
- [x] 验收标准全部达成
