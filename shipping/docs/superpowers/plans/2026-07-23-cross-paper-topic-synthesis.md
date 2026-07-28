# 跨论文主题综合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把多篇已经完成的单篇主题简报转换为可追溯的跨论文主题矩阵、综合论证单元和段落级综述提纲，为后续正式写作提供有限、清晰、可核查的材料包。

**Architecture:** 新入口只接受显式列出的不可变 `llm-topic-brief` 运行，不自动猜测同一论文的“最新结果”。代码先重验来源契约并建立 Evidence 目录；LLM 第一次对全部 Evidence 做全局主题编码，第二次用主题矩阵形成跨论文综合单元和提纲；代码最后验证覆盖率、跨论文来源数和引用绑定，并把原始引文与 Markdown 行号回填到中文报告。当前 DeepSeek 模型具有 1M 上下文，因此 v1 不做分批或层级摘要，超出模型 Token 预算时明确失败。

**Tech Stack:** Python 3.11、`jsonschema` Draft 2020-12、现有 OpenAI-compatible provider、DeepSeek V4 tokenizer、`unittest`、JSON/JSONL/Markdown 不可变运行产物。

---

## 1. 阶段边界

本阶段输入不是 Markdown、Card 或原始论文，而是已经完成的单篇主题简报运行。它解决：

1. 多篇论文分别提供了哪些严格 Evidence。
2. 这些 Evidence 可以按哪些综述问题组织。
3. 不同论文之间是支持、互补、限制还是分歧关系。
4. 每个综述段落应完成什么论证动作，并调用哪些 Evidence。
5. 当前语料仍缺少哪些证据，下一轮检索应寻找什么。

本阶段不解决：

- 不重新解析 Markdown，不重新选择 Card。
- 不自动改写单篇 Evidence claim。
- 不生成正式综述正文。
- 不生成作者—年份或数字编号参考文献；当前单篇产物没有可靠的统一文献元数据。
- 不启动跨论文缺口到 Card 的递归回查。
- 不增加人工裁决表。

最终报告中的主题名称、关系判断、综合说明和段落任务属于**导航性跨论文推断**；其下展示的 Evidence claim、逐字引文和原文位置属于**严格来源层**。后续正文生成不得把导航性推断当成未经引用的论文事实。

## 2. 总体流程

```text
显式 collection.json
  -> 重验每个 topic-review run
  -> 冻结 paper brief + Evidence + 来源哈希
  -> 构造全局 Evidence 目录
  -> LLM：主题编码与证据归组
  -> 代码：完整性、唯一归组、跨论文关系校验
  -> LLM：综合单元与段落级提纲
  -> 代码：主题覆盖、Evidence 引用、来源多样性校验
  -> 回填逐字引文与原文行号
  -> topic_synthesis.json + topic_synthesis.md + manifest.json
```

两个 LLM 阶段都一次读取整个当前语料。不得在没有正式 token-aware 分层方案时按字符数切批，也不得把失败阶段换模型、缩 Prompt 或改 Schema 后静默重试。

输出预算按输入规模确定：

```text
theme_map_output =
  min(theme_map_output_base_tokens
      + evidence_count * theme_map_output_tokens_per_evidence,
      theme_map_max_output_tokens)

outline_output =
  min(outline_output_base_tokens
      + theme_count * outline_output_tokens_per_theme,
      outline_max_output_tokens)
```

两个阶段都必须满足 `planned_input + planned_output + safety_margin <= context_window`，并且 `planned_output <= model_max_output_tokens`。条件不满足时直接产生 `input.context_budget_exceeded`，v1 不自动分批。

## 3. 输入契约

集合文件示例：

```json
{
  "schema_version": "llm.topic_synthesis_collection.v1",
  "topic": "三峡船舶积压疏导策略与长江航运组织",
  "source_run_ids": [
    "topic-brief-auto-revisit-short3-v2-20260723",
    "topic-brief-auto-revisit-information15-20260723",
    "topic-brief-auto-revisit-reservoir9-v2-20260723",
    "topic-brief-auto-revisit-long250-v2-20260723"
  ]
}
```

确定性输入规则：

- `source_run_ids` 至少包含两项且不得重复。
- 每项必须位于 `workspace/_topic_reviews/runs/<run_id>/`。
- 同一 `paper_id` 只能出现一次，避免成功代际和失败代际混合。
- 所有来源 `topic` 必须与 collection 完全一致。
- 来源必须使用 `llm.topic_paper_result.v2`、`llm.topic_review_run.v2` 和 `llm.topic_review_config.v2`；旧 v1 运行直接拒绝，需要时先按当前单篇流程重新运行。
- 接受 `completed` 和 `completed_with_revisit_failure`；后者必须在报告中标记为“单篇回查未闭合”。
- 拒绝 `failed`、`excluded`、缺失 manifest、缺失 `paper_brief.json` 或缺失 Evidence 文件。
- 重新调用 `validate_topic_brief`，验证每个主要观点仍逐字等于其 Evidence claim。
- 所有被简报引用的 Evidence ID 必须存在于该运行的 `evidence/evidence_units.jsonl`。
- 全局 Evidence ID 必须唯一。
- 对 manifest、paper brief、Evidence 文件分别计算 SHA-256 并写入聚合输入快照。

单篇模型投影只包含：

```json
{
  "paper_id": "论文ID",
  "paper_title": "论文标题",
  "paper_relevance": "core",
  "source_status": "completed",
  "topic_contribution": "导航性贡献说明",
  "cautions": ["导航性注意事项"],
  "remaining_evidence_gaps": ["单篇回查后仍存在的缺口"],
  "evidence": [
    {
      "evidence_unit_id": "evidence_xxx",
      "claim": "经过单篇合同验证的 claim",
      "evidence_type": "result",
      "confidence": "high",
      "caveats": [],
      "is_key_point": true
    }
  ]
}
```

不向跨论文模型发送逐字 quote、原文路径、request ID、token 使用量、Card 全文或未入选 Card。quote 和原文坐标只由代码在最终报告中回填。

## 4. 第一阶段：主题矩阵

输入为全部单篇投影。模型输出 `llm.topic_evidence_map.v1`：

```json
{
  "schema_version": "llm.topic_evidence_map.v1",
  "themes": [
    {
      "title": "通过能力与需求增长",
      "focus": "该主题回答的综述问题",
      "synthesis_value": "为什么应成为综述中的独立分析维度",
      "relation_type": "complementary",
      "assignments": [
        {
          "evidence_unit_id": "evidence_xxx",
          "role": "main_support"
        }
      ]
    }
  ],
  "unassigned_evidence": [
    {
      "evidence_unit_id": "evidence_yyy",
      "reason_code": "too_narrow",
      "reason": "没有进入主题矩阵的具体原因"
    }
  ],
  "corpus_gaps": [
    {
      "gap_type": "implementation",
      "question": "当前语料仍未回答的问题",
      "why_it_matters": "该问题对综述结构的影响"
    }
  ]
}
```

默认约束：

- 主题数 `1..8`；当全部 Evidence 只回答一个实质问题时，不强迫模型拆出伪主题。
- `relation_type`：`convergent`、`complementary`、`divergent`、`mixed`、`single_source`。
- Evidence 角色：`main_support`、`context`、`qualification`、`contrast`。
- 每个 Evidence 必须且只能进入一个主题，或进入 `unassigned_evidence`。
- `unassigned_evidence` 仅允许 `redundant`、`too_narrow`、`weak_connection`、`quality_concern`。
- `convergent`、`complementary`、`divergent`、`mixed` 必须至少涉及两篇论文。
- 只有确实仅含一篇论文 Evidence 的主题才能标记 `single_source`。
- Evidence 条数不代表研究权重；同一论文的多条 Evidence 仍只算一个独立来源。模型不得因为某篇论文 Card 多、Evidence 多就把它当成多篇研究的一致结论。
- `core/supporting/peripheral` 只描述与当前综述主题的用途，不代表研究质量或结论可信度。
- 模型不得输出新的论文事实或改写 Evidence claim；主题字段只是导航文本。
- 代码在验证后按 `topic + title + focus` 生成稳定 `theme_id`，不让模型发明 ID。
- 代码在验证后按主题与缺口内容生成稳定 `gap_id`；重复缺口直接拒绝。

这里的“唯一归组”只约束第一层主题目录，防止同一 Evidence 在矩阵中被重复计算；第二阶段允许同一 Evidence 服务多个段落，但会在覆盖审计中显示复用次数。

## 5. 第二阶段：综合单元与提纲

输入为已验证主题矩阵、主题内 Evidence claim、论文标题与相关性。模型输出 `llm.topic_review_outline.v1`：

```json
{
  "schema_version": "llm.topic_review_outline.v1",
  "working_title": "综述工作标题",
  "central_question": "整篇综述要回答的中心问题",
  "synthesis_units": [
    {
      "unit_index": 1,
      "unit_type": "consensus",
      "synthesis_statement": "跨论文层面的导航性综合判断",
      "theme_ids": ["theme_xxx"],
      "evidence_unit_ids": ["evidence_a", "evidence_b"],
      "gap_ids": []
    }
  ],
  "sections": [
    {
      "title": "章节标题",
      "purpose": "本节在全文中的任务",
      "theme_ids": ["theme_xxx"],
      "paragraphs": [
        {
          "paragraph_role": "evidence_comparison",
          "synthesis_move": "该段应如何组织和比较证据",
          "synthesis_unit_indexes": [1],
          "evidence_unit_ids": ["evidence_a", "evidence_b"]
        }
      ]
    }
  ],
  "search_directions": [
    {
      "priority": "high",
      "question": "下一轮检索需要回答的问题",
      "reason": "为什么现有语料不足",
      "related_theme_ids": ["theme_xxx"],
      "source_gap_ids": ["gap_xxx"]
    }
  ]
}
```

模型使用从 `1` 开始的 `unit_index`，段落用 `synthesis_unit_indexes` 引用。代码必须验证索引连续、唯一、全部可解析；验证后为综合单元生成 `synthesis_<hash>`，把段落引用转换为稳定 ID，并在发布产物中移除临时索引。

默认约束：

- 综合单元类型：`consensus`、`complement`、`contrast`、`causal_chain`、`single_source_context`、`corpus_gap`。
- 除 `single_source_context` 和 `corpus_gap` 外，每个综合单元必须引用至少两篇论文。
- `consensus` 表示至少两篇论文对同一问题给出方向一致的证据；`complement` 表示证据回答同一问题的不同环节；`contrast` 表示结果、条件或建议存在实质差异。不得把仅仅措辞不同的 Evidence 标成分歧。
- `corpus_gap` 不得伪造 Evidence；其 Evidence 列表可以为空。
- `corpus_gap` 必须引用已识别的 `gap_id`；非 gap 综合单元的 `gap_ids` 必须为空。
- 非 gap 综合单元至少引用一条 Evidence，最多八条。
- `synthesis_statement` 和 `synthesis_move` 禁止阿拉伯数字；具体数值只在下方原样 Evidence claim 中出现。
- 章节数最多八个，每节最多六个段落。
- 每个主题必须进入且只进入一个主章节。
- 每个主题必须先进入至少一个综合单元；每个语料缺口必须至少进入一个检索方向。
- 每个非 gap 段落必须通过 `synthesis_unit_indexes` 引用至少一个综合单元，并且段落 Evidence 必须是这些综合单元 Evidence 的子集。
- `evidence_unit_ids` 只能引用主题矩阵中已归组的 Evidence，不能使用第一阶段明确未采用的 Evidence。
- 代码计算每个段落和章节涉及的论文数，不接受模型重复输出 `paper_ids`。
- 未进入提纲的已归组 Evidence 由代码计算并写入 coverage，不要求模型逐项解释。

## 6. 发布产物与状态

运行目录：

```text
workspace/_topic_syntheses/runs/<run_id>/
  input/
    collection.json
    source_runs.jsonl
    paper_briefs.jsonl
    evidence_catalog.jsonl
    model_projection.json
    model_profile.json
    synthesis_config.json
  theme_map/
    request.json
    raw_response.json
    parsed_response.json
    result.json
    validated_theme_map.json
  outline/
    request.json
    raw_response.json
    parsed_response.json
    result.json
    validated_outline.json
  output/
    topic_synthesis.json
  audit/
    unassigned_evidence.jsonl
    unused_assigned_evidence.jsonl
    source_statuses.jsonl
    coverage.json
  review/
    topic_synthesis.md
  manifest.json
```

状态只有：

- `completed`：两个 LLM 阶段和全部确定性校验通过，正式 output 与 review 已发布。
- `failed`：任一输入、调用或合同失败。保留已生成的请求与中间产物，但不发布 `output/topic_synthesis.json`。

没有 `completed_with_*` 状态。跨论文阶段如果只完成主题矩阵而提纲失败，主题矩阵仍是审计中间件，不是可供下游使用的正式结果。

Manifest 必须记录：

- collection SHA-256。
- 每个来源运行的 ID、状态、paper ID、generation ID 和三类输入文件哈希。
- 论文数、Evidence 总数、已归组数、未归组数、进入提纲数、未进入提纲数。
- 主题数、综合单元数、章节数、段落数。
- 两次请求的模型、prompt hash、计划/实际 token、`finish_reason`、reasoning 字符数。
- 全部失败代码。

## 7. 人类可读报告

`review/topic_synthesis.md` 的阅读顺序：

1. **语料范围**：列出论文标题、单篇相关性、来源运行状态和 Evidence 数。
2. **主题矩阵**：每个主题显示关系类型、涉及论文和 Evidence claim。
3. **跨论文综合**：先显示“模型综合判断”，随后直接显示每条 Evidence 的论文标题、claim、逐字引文、Card 标题和 Markdown 行号。
4. **综述提纲**：按章节和段落展示论证任务，并把该段需要的 Evidence 直接铺在下面。
5. **证据缺口与检索方向**：明确标注为语料层判断，不当作论文结论。
6. **覆盖审计**：显示总 Evidence、未归组 Evidence、已归组但未进入提纲 Evidence、单来源主题和部分状态论文。

主报告不要求读者根据 Evidence ID 去其他文件查内容。Evidence ID 作为机器锚点显示在来源行末，不作为主要阅读标签。

## 8. 文件职责

- Create: `src/shipping_pipeline/topic_synthesis_contracts.py`
  - 配置、collection、主题矩阵、综合提纲 Schema 和确定性验证。
- Create: `src/shipping_pipeline/topic_synthesis.py`
  - 来源快照、模型投影、两阶段执行、原子发布、manifest。
- Create: `src/shipping_pipeline/topic_synthesis_report.py`
  - 回填 Evidence claim、quote 和原文坐标，渲染中文报告。
- Create: `src/shipping_pipeline/llm_json_stage.py`
  - 从 `topic_review.py` 提取通用 JSON 调用归档与 token 检查。
- Create: `config/topic-synthesis-default.json`
  - 主题数、章节数、段落数、Evidence 引用数和输出 token 上限。
- Create: `tests/test_topic_synthesis_contracts.py`
  - 动态 Schema、覆盖、跨论文来源数、未知 ID、重复 ID。
- Create: `tests/test_topic_synthesis.py`
  - 来源重验、两阶段调用、失败隔离、报告、CLI。
- Modify: `src/shipping_pipeline/topic_review.py`
  - 改为使用 `llm_json_stage.execute_json_stage`，行为与产物保持不变。
- Modify: `main.py`
  - 新增 `llm-topic-synthesis`。
- Modify: `README.md`
  - 增加 collection 示例、命令、状态和报告说明。
- Modify: `docs/progress-20260709.md`
  - 记录设计转入跨论文阶段及真实验证结论。

## 9. 实施任务

### Task 1: 冻结配置与 collection 契约

**Files:**
- Create: `config/topic-synthesis-default.json`
- Create: `src/shipping_pipeline/topic_synthesis_contracts.py`
- Create: `tests/test_topic_synthesis_contracts.py`

- [ ] **Step 1: 写配置和 collection 失败测试**

```python
def test_collection_requires_two_unique_source_runs_and_exact_topic():
    with self.assertRaises(TopicSynthesisContractError):
        validate_collection({
            "schema_version": "llm.topic_synthesis_collection.v1",
            "topic": "主题",
            "source_run_ids": ["run-a", "run-a"],
        })
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_synthesis_contracts -v
```

Expected: `ModuleNotFoundError` 或缺少 `validate_collection`。

- [ ] **Step 3: 实现配置数据类和 collection 验证**

```python
@dataclass(frozen=True)
class TopicSynthesisConfig:
    max_themes: int
    max_synthesis_units: int
    max_sections: int
    max_paragraphs_per_section: int
    max_evidence_per_synthesis_unit: int
    max_search_directions: int
    theme_map_output_base_tokens: int
    theme_map_output_tokens_per_evidence: int
    theme_map_max_output_tokens: int
    outline_output_base_tokens: int
    outline_output_tokens_per_theme: int
    outline_max_output_tokens: int


def validate_collection(payload: object) -> dict[str, Any]:
    validated = _validate_schema(
        payload,
        build_collection_schema(),
        "schema.topic_synthesis_collection_invalid",
    )
    run_ids = list(validated["source_run_ids"])
    if len(run_ids) != len(set(run_ids)):
        raise TopicSynthesisContractError(
            "collection.duplicate_source_run",
            "source_run_ids 不得重复。",
            path="$.source_run_ids",
        )
    return copy.deepcopy(validated)
```

默认配置：

```json
{
  "schema_version": "llm.topic_synthesis_config.v1",
  "max_themes": 8,
  "max_synthesis_units": 16,
  "max_sections": 8,
  "max_paragraphs_per_section": 6,
  "max_evidence_per_synthesis_unit": 8,
  "max_search_directions": 8,
  "theme_map_output_base_tokens": 4096,
  "theme_map_output_tokens_per_evidence": 192,
  "theme_map_max_output_tokens": 131072,
  "outline_output_base_tokens": 8192,
  "outline_output_tokens_per_theme": 2048,
  "outline_max_output_tokens": 65536
}
```

- [ ] **Step 4: 运行契约测试**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_synthesis_contracts -v
```

Expected: collection 与配置测试通过。

### Task 2: 重验来源运行并建立不可变快照

**Files:**
- Create: `src/shipping_pipeline/topic_synthesis.py`
- Modify: `src/shipping_pipeline/topic_synthesis_contracts.py`
- Modify: `tests/test_topic_synthesis.py`

- [ ] **Step 1: 写重复论文、主题不一致、来源失败和 Evidence 缺失测试**

```python
def test_snapshot_rejects_two_runs_for_same_paper(self):
    collection = self.write_collection(["run-a", "run-a-new-generation"])
    with self.assertRaisesRegex(AnalysisInputError, "同一论文"):
        create_topic_synthesis_snapshot(self.workspace, collection)
```

- [ ] **Step 2: 实现来源加载**

```python
@dataclass(frozen=True)
class TopicSynthesisSource:
    run_id: str
    status: str
    paper_id: str
    paper_title: str
    topic: str
    generation_id: str
    manifest_sha256: str
    paper_brief_sha256: str
    evidence_sha256: str
    paper_result: dict[str, Any]
    evidence_units: tuple[dict[str, Any], ...]
```

`create_topic_synthesis_snapshot` 必须重验单篇 brief，并生成全局 `evidence_by_id`。只有 `completed` 和 `completed_with_revisit_failure` 可以进入。

剩余缺口按确定规则归一化：存在 `revisit` 时读取 `revisit.remaining_evidence_gaps`；没有回查结果时读取 `brief.evidence_gaps`。不得把回查前已经关闭的缺口重新送入跨论文阶段。

- [ ] **Step 3: 写入完整输入快照**

精确写出 `collection.json`、`source_runs.jsonl`、`paper_briefs.jsonl`、`evidence_catalog.jsonl` 和 `model_projection.json`。不得只保存外部路径。

- [ ] **Step 4: 运行来源快照测试**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_synthesis.TopicSynthesisSnapshotTests -v
```

Expected: 成功来源、部分来源、重复论文和缺失 Evidence 路径全部按契约处理。

### Task 3: 提取通用 JSON 调用器

**Files:**
- Create: `src/shipping_pipeline/llm_json_stage.py`
- Modify: `src/shipping_pipeline/topic_review.py`
- Modify: `tests/test_topic_review.py`

- [ ] **Step 1: 把 `_execute_json_stage` 移入独立模块**

公开签名：

`execute_json_stage(*, run_dir: Path, stage: str, task_name: str, directory_name: str | None, system_prompt: str, user_prompt: str, max_output_tokens: int, context: dict[str, Any], client: Any, profile: ModelProfile, token_counter: Any) -> tuple[dict[str, Any], dict[str, Any]]`

`task_name` 用于 provider 调用标签；request ID、token 窗口检查、raw response、`finish_reason`、reasoning 和 token 一致性检查保持现状。

- [ ] **Step 2: `TopicReviewRunner` 改用公开函数**

不改变任何目录名、request ID 算法或 manifest 字段。

- [ ] **Step 3: 运行单篇专项回归**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_review_contracts tests.test_topic_review -v
```

Expected: 现有23项测试全部通过。

### Task 4: 实现主题矩阵动态 Schema 与第一阶段

**Files:**
- Modify: `src/shipping_pipeline/topic_synthesis_contracts.py`
- Modify: `src/shipping_pipeline/topic_synthesis.py`
- Modify: `tests/test_topic_synthesis_contracts.py`
- Modify: `tests/test_topic_synthesis.py`

- [ ] **Step 1: 写 Evidence 覆盖失败测试**

覆盖缺失、重复归组、同时归组和未归组、未知 Evidence、错误 `single_source`、多论文关系实际只有一篇来源。

- [ ] **Step 2: 实现动态 Schema**

公开函数为：

- `build_topic_evidence_map_schema(evidence_unit_ids: list[str], config: TopicSynthesisConfig) -> dict[str, Any]`
- `validate_topic_evidence_map(payload: object, evidence_to_paper: dict[str, str], config: TopicSynthesisConfig) -> dict[str, Any]`

Schema 把每个 assignment 和 unassigned ID 绑定到输入 Evidence enum；确定性验证取两个集合的并集和交集，要求并集恰好等于全部输入 ID、交集为空，并按 `evidence_to_paper` 验证关系类型的独立论文数量。

验证后调用：

```python
theme_id = "theme_" + hashlib.sha256(
    f"{topic}\n{title}\n{focus}".encode("utf-8")
).hexdigest()[:20]
```

- [ ] **Step 3: 实现主题矩阵 Prompt**

系统 Prompt 明确：

- 只能分组输入 Evidence，不能新造事实。
- 每条 Evidence 只能有一个主归属。
- 主题标题和综合价值是导航判断。
- 没有写作用途的 Evidence 必须进入显式未归组集合。

- [ ] **Step 4: 接入 `execute_json_stage`**

输出保存在 `theme_map/`，通过验证后写 `validated_theme_map.json`。

- [ ] **Step 5: 运行主题矩阵测试**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_synthesis_contracts tests.test_topic_synthesis -v
```

Expected: 第一阶段成功与失败样例均通过。

### Task 5: 实现综合单元和段落级提纲

**Files:**
- Modify: `src/shipping_pipeline/topic_synthesis_contracts.py`
- Modify: `src/shipping_pipeline/topic_synthesis.py`
- Modify: `tests/test_topic_synthesis_contracts.py`
- Modify: `tests/test_topic_synthesis.py`

- [ ] **Step 1: 写跨论文来源数和引用边界测试**

```python
def test_consensus_requires_two_distinct_papers():
    with self.assertRaisesRegex(TopicSynthesisContractError, "至少两篇"):
        validate_topic_review_outline(
            payload,
            theme_map=theme_map,
            evidence_to_paper={"e1": "paper-a", "e2": "paper-a"},
            config=config,
        )
```

- [ ] **Step 2: 实现 outline 动态 Schema**

所有 theme ID、Evidence ID 和临时综合单元索引均使用动态 enum。代码验证每个主题恰好进入一个章节，并验证段落 Evidence 是其综合单元 Evidence 的子集。

- [ ] **Step 3: 实现第二阶段 Prompt 和稳定综合单元 ID**

```python
synthesis_id = "synthesis_" + hashlib.sha256(
    _compact_json({
        "unit_type": unit["unit_type"],
        "theme_ids": sorted(unit["theme_ids"]),
        "evidence_unit_ids": sorted(unit["evidence_unit_ids"]),
        "statement": " ".join(unit["synthesis_statement"].split()),
    }).encode("utf-8")
).hexdigest()[:20]
```

- [ ] **Step 4: 实现确定性 coverage**

```python
coverage = {
    "source_evidence_count": len(all_evidence_ids),
    "theme_assigned_count": len(assigned_ids),
    "theme_unassigned_count": len(unassigned_ids),
    "outline_used_count": len(outline_used_ids),
    "assigned_but_unused_count": len(assigned_ids - outline_used_ids),
}
```

- [ ] **Step 5: 运行两阶段 runner 测试**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_synthesis_contracts tests.test_topic_synthesis -v
```

Expected: 两次调用、完整覆盖、未知引用、来源数不足、outline 失败不发布均通过。

### Task 6: 生成不依赖 ID 查找的中文报告

**Files:**
- Create: `src/shipping_pipeline/topic_synthesis_report.py`
- Modify: `src/shipping_pipeline/topic_synthesis.py`
- Modify: `tests/test_topic_synthesis.py`

- [ ] **Step 1: 写报告内容测试**

断言报告同一位置包含综合说明、论文标题、Evidence claim、逐字 quote、Card 标题和 Markdown 行号，而不是只显示 Evidence ID。

- [ ] **Step 2: 实现 Evidence 回填**

```python
def render_evidence_source(
    evidence: dict[str, Any],
    *,
    paper_title: str,
) -> list[str]:
    citation = evidence["citations"][0]
    source = citation["source_ref"]
    return [
        f"- 来源论文：{paper_title}",
        f"- 证据观点：{evidence['claim']}",
        f"- 原文：> {citation['quote']}",
        f"- 位置：{source['path']} L{source['start_line']}-L{source['end_line']}",
    ]
```

多 citation Evidence 必须逐项展示，不能只取第一条；上例只是单 citation 的最小测试形态。

- [ ] **Step 3: 渲染语料、主题、综合、提纲、缺口和覆盖六部分**

导航判断使用引用块提示“模型综合判断”；严格来源使用固定的“证据观点/原文/位置”标签。

- [ ] **Step 4: 运行报告测试**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_synthesis.TopicSynthesisReportTests -v
```

Expected: 人工阅读不需要打开 JSON 或按 Evidence ID 查找。

### Task 7: CLI、manifest 和失败隔离

**Files:**
- Modify: `main.py`
- Modify: `src/shipping_pipeline/topic_synthesis.py`
- Modify: `tests/test_topic_synthesis.py`
- Modify: `README.md`

- [ ] **Step 1: 新增命令**

```powershell
..\.venv\Scripts\python.exe main.py llm-topic-synthesis `
  --workspace workspace `
  --collection config/my-topic-collection.json `
  --run-id topic-synthesis-20260723 `
  --provider openai-compatible `
  --model-profile config/models/siliconflow-deepseek-v4-pro.json `
  --synthesis-config config/topic-synthesis-default.json `
  --timeout 900
```

- [ ] **Step 2: 实现原子发布**

Runner 只有在 outline 验证完成后写：

```text
output/topic_synthesis.json
review/topic_synthesis.md
```

任何失败都写 `manifest.json`，状态为 `failed`，CLI 返回1。

- [ ] **Step 3: 测试不可变 run ID、失败 manifest 和 CLI 返回码**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_synthesis -v
```

Expected: 成功返回0；输入、主题矩阵或提纲失败返回1且无正式 output。

### Task 8: 真实语料试运行与收束

**Files:**
- Create: `config/pilots/topic-synthesis-shipping-20260723.json`
- Create: `docs/topic-synthesis-pilot-20260723.md`
- Modify: `docs/progress-20260709.md`

- [ ] **Step 1: 用四至六篇不同角色论文建立显式 collection**

至少包含：

- 直接讨论积压疏导策略的核心论文。
- 讨论设施或通过能力的支持论文。
- 提供管理或信息视角的边缘论文。
- 已完成自动回查的大论文。

直接策略论文若只有旧 v1 或失败运行，先用当前 `llm-topic-brief` 生成新的 v2 不可变运行。只有状态为 `completed` 或 `completed_with_revisit_failure` 的新运行才写入 collection，不读取旧输出作为兼容来源。

- [ ] **Step 2: 执行真实 API**

Run:

```powershell
..\.venv\Scripts\python.exe main.py llm-topic-synthesis `
  --workspace workspace `
  --collection config/pilots/topic-synthesis-shipping-20260723.json `
  --run-id topic-synthesis-pilot-20260723 `
  --provider openai-compatible `
  --model-profile config/models/siliconflow-deepseek-v4-pro.json `
  --synthesis-config config/topic-synthesis-default.json `
  --timeout 900
```

Expected: 两个请求均 `finish_reason=stop`、reasoning字符数为0、计划/实际输入 Token一致。

- [ ] **Step 3: 按停止条件审视**

必须同时满足：

- 每条源 Evidence 都进入主题或显式未归组。
- 每个跨论文综合单元可以在报告内直接看到至少两篇论文的原文证据。
- 主题矩阵没有被单篇250 Card论文垄断。
- 提纲段落不是论文逐篇罗列，而是按问题、机制、策略或分歧组织。
- 检索方向来自当前语料缺口，不伪装为论文结论。
- 阅读报告不需要人工按 Evidence ID 查找其他文件。

- [ ] **Step 4: 运行完整回归和编译检查**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
..\.venv\Scripts\python.exe -m compileall -q src scripts tests main.py
```

Expected: 全部测试通过，编译检查无输出。

## 10. 自审结论

### 输入

输入来源显式、不可变、同主题、每篇唯一；不依赖目录时间或“最新运行”猜测。部分回查失败的单篇结果可以使用，但状态被带入报告。

### 处理

只进行全局主题编码和提纲组织，不重做单篇解析或 Evidence 抽取。两次调用都受 tokenizer、模型上下文和输出预算约束；超限直接失败。

### 状态

只有 `completed` 和 `failed`。中间成功不作为正式下游产物，避免下游误读半成品。

### 输出

机器产物保留稳定 ID 和完整覆盖账本；人类报告直接展开 claim、quote 和位置，不要求人工跨文件查 ID。

### 上下游

上游 `llm-topic-brief` 不改业务行为。下游正式综述写作只读取 `output/topic_synthesis.json`，但在生成正式引文前仍需要一份论文书目信息注册表。

### 当前推荐

先实现并用四至六篇真实论文验证“主题矩阵 + 综合单元 + 段落级提纲”。不在同一目标内加入最终正文生成、自动参考文献或跨论文缺口递归回查；这些要由本阶段真实产物证明必要后再进入下一个目标。
