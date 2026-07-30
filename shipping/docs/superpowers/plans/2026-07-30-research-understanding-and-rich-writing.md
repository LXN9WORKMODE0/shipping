# Research Understanding与丰富上下文写作 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏现有Card、Evidence、论文池和引用审计链的前提下，新增论文认知层、领域图谱、综述框架和章节级丰富上下文写作，并用同一批14篇论文验证其是否优于Evidence-only写作。

**Architecture:** 新链路与现有 `topic_review -> topic_synthesis` 并行建设，不直接替换稳定模块。单篇阶段读取完整规范化Markdown、全部Card和已有Evidence，形成可追溯的 `Paper Understanding`；跨论文阶段只基于显式选择的Understanding运行建立 `Research Landscape`；写作阶段按章节组装完整知识包，LLM充分阅读后写作，Evidence转为事后事实审计锚点。

**Tech Stack:** Python 3.11、`jsonschema` Draft 2020-12、现有OpenAI-compatible provider、DeepSeek V4 1M tokenizer、JSON/JSONL/Markdown不可变运行目录、pytest、现有FastAPI/React UI（核心链路验证后再接入）。

---

## 1. 当前代码架构分析

### 1.1 PDF、Markdown与Card

| 职责 | 当前文件 | 结论 |
|---|---|---|
| PDF转Markdown | `src/shipping_pipeline/pdf_conversion.py` | 保留，不在本目标中修改 |
| Markdown结构识别 | `markdown_structure.py`、`content_blocks.py` | 保留，Understanding读取其产出的Card和规范化Markdown |
| Card生成与发布 | `card_builder.py`、`pipeline.py` | 保留，继续负责来源定位、代次和解析标记 |
| Card模型投影 | `llm_projection.py` | 复用 `project_cards`，但Understanding还需要完整Markdown |

现有 `create_input_snapshot` 已经完成以下关键工作：

- 校验 `paper_id` 与 `workspace_paper_id`。
- 校验 `run.json`、`materials/current.json` 和Card代次一致。
- 按原文顺序冻结全部Card。
- 计算输入哈希。

新模块应复用这个入口，不能自行猜测“最新Card”。

### 1.2 单篇主题分析

| 职责 | 当前文件 | 结论 |
|---|---|---|
| 论文主题定界、少量Card选择、Evidence与Brief | `topic_review.py` | 保留，作为事实锚点来源 |
| 单篇Schema与语义合同 | `topic_review_contracts.py` | 保留，不扩成Understanding |
| 人工可读报告 | `topic_review_report.py` | 保留 |

`TopicReviewRunner` 的设计目标是“少量最相关材料”，默认核心论文只选择6张Card并最多回查2张。它不能承担论文整体认知层，否则仍会继承当前信息压缩。

### 1.3 跨论文综合

| 职责 | 当前文件 | 结论 |
|---|---|---|
| Evidence主题矩阵与提纲 | `topic_synthesis.py` | 作为方案C对照保留 |
| 跨论文合同 | `topic_synthesis_contracts.py` | 不直接扩充为Landscape |
| 跨论文报告 | `topic_synthesis_report.py` | 保留 |

当前跨论文综合只接收Paper Brief和Evidence投影。它适合证据目录和审计，但不适合作为新认知层的容器。

### 1.4 正文写作

当前正文写作位于 `scripts/generate_review_draft_experiment.py`，不是正式Pipeline模块，也没有接入 `EndToEndPipelineRunner` 或UI Job状态机。

这意味着新写作链可以独立实现，不需要先兼容或迁移旧正文运行。旧脚本保留为A/B/C实验中的方案C。

### 1.5 编排和UI

- `end_to_end.py` 当前阶段为 `cards -> topic_briefs -> topic_synthesis`。
- `shipping_ui/services/job_service.py` 当前支持Card、单篇Brief和跨论文综合Job。
- `project.json` 当前保存 `analysis_run_ids` 和 `synthesis_run_id`，没有Understanding、Landscape或Writing运行。

前两个新阶段先做CLI和不可变运行目录。真实14篇验证通过前，不修改 `end_to_end.py`、UI项目Schema和前端。

## 2. 关键架构判断

### 2.1 Paper Understanding不保存跨论文关系

单篇隔离任务看不到完整论文集合，不能可靠判断“继承、补充或冲突”。因此：

- `Paper Understanding` 只描述论文自身问题、方法、材料、贡献、结果性质、验证水平、限制和可用于综述的角色。
- `relation_to_other_papers` 移至 `Research Landscape.relations`。
- 关系必须引用至少两个不同 `paper_id` 和相应 `contribution_id`。

### 2.2 “研究空白”必须区分语料缺口与领域缺口

14篇定向样本只能产生 `corpus_gaps`。只有集合明确标记为经过系统检索和质量评价的 `systematic_corpus` 时，才允许输出 `field_gap_candidates`，且仍使用候选措辞。

v1集合默认：

```json
{
  "corpus_scope": "targeted_sample"
}
```

因此v1只发布“本次语料未回答的问题”，不发布“领域真正空白”的事实判断。

### 2.3 Understanding不是更长的Evidence

Understanding可以做解释和归纳，但每个方法、贡献、结果和限制都必须绑定已有 `material_id`，并可选绑定 `evidence_unit_id`。完整Markdown用于让模型理解论证过程，Card用于定位，Evidence用于标识已经过严格验证的事实。

### 2.4 新链路先并行，不直接替换

```text
现有方案C：
Card -> Topic Brief/Evidence -> Topic Synthesis -> Evidence-only Draft

新方案B：
Card + Markdown + Evidence
  -> Paper Understanding
  -> Research Landscape
  -> Review Framework
  -> Chapter Knowledge Package
  -> Chapter Writing
  -> Post-writing Evidence Audit
```

方案A为14篇完整Markdown直接写作基线。只有方案B在质量评价中优于方案C且不明显劣于方案A，才进入UI和默认Pipeline。

## 3. 新数据流与插入点

```text
每篇论文当前Card代次
  + normalized/document.md
  + 可选Topic Review run
  + 论文题录
    |
    v
PaperUnderstandingRunner
    |
    +-- output/paper_understanding.json
    +-- review/paper_understanding.md
    +-- audit/source_bindings.jsonl
    |
显式 understanding_collection.json
    |
    v
ResearchLandscapeRunner
    |
    +-- output/research_landscape.json
    +-- review/research_landscape.md
    |
用户综述目标 + Landscape
    |
    v
ReviewFrameworkRunner
    |
    +-- output/review_framework.json
    |
每个章节的相关论文、Understanding、Markdown、Card、Evidence、题录
    |
    v
ChapterKnowledgePackageBuilder
    |
    v
ChapterWritingRunner
    |
    +-- output/review_draft.json
    +-- review/review_draft.md
    |
    v
ReviewClaimAuditRunner
    |
    +-- audit/claim_support.jsonl
    +-- review/claim_audit.md
```

准确插入位置：

1. `Paper Understanding` 位于Card发布之后，与 `TopicReviewRunner` 并列。
2. `Research Landscape` 位于多篇Understanding之后，与现有 `TopicSynthesisRunner` 并列。
3. `Review Framework` 替代新路径中的Evidence提纲，但不删除旧提纲。
4. `Chapter Writing` 位于Framework之后，引用目录装配之前。
5. `Evidence Audit` 位于章节写作和全文编辑之后。

## 4. Paper Understanding v1数据结构

建议采用嵌套多项结构，而不是每篇论文只有一个扁平 `main_contribution`。一篇学位论文可能同时包含多个问题、方法和结果。

```json
{
  "schema_version": "llm.paper_understanding.v1",
  "paper_id": "论文ID",
  "paper_title": "论文题名",
  "topic": "综述主题",
  "paper_relevance": "core",
  "research_questions": [
    {
      "question": "论文试图回答的问题",
      "problem_category": "运行组织优化",
      "material_ids": ["paper:prose:L00010-L00015:01"]
    }
  ],
  "study_context": {
    "study_object": "三峡船闸",
    "data_sources": ["operational_records"],
    "time_scope": "原文给出的时间范围；没有则为空字符串",
    "geographic_scope": "三峡坝区",
    "material_ids": ["paper:prose:L00020-L00025:01"]
  },
  "methods": [
    {
      "method_category": "simulation",
      "method_name": "离散事件仿真",
      "description": "方法在论文中的作用",
      "material_ids": ["paper:prose:L00030-L00040:01"]
    }
  ],
  "contributions": [
    {
      "statement": "论文对当前主题的主要贡献",
      "result_type": "simulation_result",
      "validation_level": "simulation",
      "evidence_strength": "moderate",
      "strength_rationale": "为什么给出该等级",
      "material_ids": ["paper:table:L00100-L00115:01"],
      "evidence_unit_ids": ["evidence_xxx"]
    }
  ],
  "limitations": [
    {
      "statement": "适用边界或限制",
      "basis": "author_stated",
      "material_ids": ["paper:prose:L00200-L00210:01"]
    }
  ],
  "review_roles": [
    {
      "role": "method_comparison",
      "reason": "适合进入综述的用途",
      "contribution_indexes": [1]
    }
  ],
  "unresolved_questions": [
    {
      "question": "本篇论文没有回答但与主题相关的问题",
      "basis": "paper_scope_boundary"
    }
  ],
  "keywords": ["船闸调度", "仿真"]
}
```

程序验证后生成：

- `understanding_id`
- `research_question_id`
- `method_id`
- `contribution_id`
- `limitation_id`

模型不生成任何ID。子对象ID由 `paper_id + 类型 + 规范化内容 + 排序后的material_ids` 计算。

### 4.1 枚举

`paper_relevance`：

```text
core, supporting, peripheral, exclude
```

`data_sources`：

```text
operational_records, field_observation, experiment, simulation_output,
survey, interview, document_analysis, literature_only, author_argument,
mixed, not_reported
```

`method_category`：

```text
descriptive_analysis, statistical_analysis, optimization,
simulation, algorithm_design, system_design, engineering_case,
policy_analysis, conceptual_analysis, mixed
```

`result_type`：

```text
historical_observation, empirical_measurement, experimental_result,
simulation_result, algorithm_benchmark, engineering_implementation,
system_design, recommendation, conceptual_argument
```

`validation_level`：

```text
none, conceptual, simulation, benchmark, field_observation,
engineering_application
```

`evidence_strength`：

```text
strong, moderate, limited, uncertain
```

`basis`：

```text
author_stated, reviewer_inferred
```

### 4.2 来源合同

- `research_questions[*].material_ids` 至少一项。
- `study_context.material_ids` 至少一项。
- `methods[*].material_ids` 至少一项。
- `contributions[*].material_ids` 至少一项。
- `limitations[*].material_ids` 至少一项。
- 所有 `material_id` 必须存在于冻结的当前Card快照。
- `evidence_unit_id` 可以为空；非空时必须存在于显式指定的Topic Review运行。
- `evidence_strength` 不是由论文类型直接推导，必须提供 `strength_rationale`。
- `reviewer_inferred` 限制必须在报告中与作者明确限制分开显示。

## 5. Paper Understanding运行目录

```text
workspace/_paper_understandings/runs/<run_id>/
  input/
    paper.json
    document.md
    materials.jsonl
    projected_cards.jsonl
    evidence_units.jsonl
    topic_review_source.json
    model_profile.json
    understanding_config.json
  understanding/
    request.json
    raw_response.json
    parsed_response.json
    result.json
    validated_understanding.json
  output/
    paper_understanding.json
  audit/
    source_bindings.jsonl
    unreferenced_materials.jsonl
  review/
    paper_understanding.md
  manifest.json
```

输入必须复制到运行目录，不能只记录外部路径。`document.md`、Card、Evidence和来源manifest分别记录SHA-256。

状态：

- `completed`
- `excluded`
- `failed`

输出不存在时，下游不得读取同目录旧文件。

v1使用整篇Markdown和全部Card进行一次调用。调用前执行真实token预算；超过1M上下文配置时以 `understanding.context_budget_exceeded` 失败，不自动截断、缩写或切批。只有真实语料证明需要后，再单独设计大论文分层理解。

## 6. Research Landscape v1数据结构

输入集合：

```json
{
  "schema_version": "llm.research_landscape_collection.v1",
  "topic": "三峡枢纽通航能力提升方法",
  "review_goal": "比较不同提升方法的作用机制、验证水平和适用边界",
  "corpus_scope": "targeted_sample",
  "source_understanding_run_ids": [
    "understanding-paper-001",
    "understanding-paper-002"
  ]
}
```

输出：

```json
{
  "schema_version": "llm.research_landscape.v1",
  "topic": "三峡枢纽通航能力提升方法",
  "central_problem": "该语料共同回答的核心问题",
  "dimensions": [
    {
      "dimension_index": 1,
      "title": "运行组织优化",
      "question": "该维度回答什么问题",
      "paper_ids": ["paper-a", "paper-b"],
      "contribution_ids": ["contribution-a", "contribution-b"]
    }
  ],
  "relations": [
    {
      "relation_type": "complements",
      "from_paper_id": "paper-a",
      "to_paper_id": "paper-b",
      "statement": "两篇论文的互补关系",
      "supporting_contribution_ids": [
        "contribution-a",
        "contribution-b"
      ]
    }
  ],
  "research_evolution": [
    {
      "period": "1997-2015",
      "statement": "本次语料显示的研究关注变化",
      "paper_ids": ["paper-a", "paper-b"]
    }
  ],
  "disagreements": [
    {
      "question": "分歧围绕的问题",
      "positions": [
        {
          "paper_ids": ["paper-a"],
          "statement": "立场A",
          "contribution_ids": ["contribution-a"]
        },
        {
          "paper_ids": ["paper-b"],
          "statement": "立场B",
          "contribution_ids": ["contribution-b"]
        }
      ]
    }
  ],
  "corpus_gaps": [
    {
      "question": "本次语料没有回答的问题",
      "why_it_matters": "对综述目标的影响",
      "related_dimension_indexes": [1]
    }
  ]
}
```

确定性合同：

- 每篇非 `exclude` Understanding必须进入至少一个dimension，或进入显式 `unmapped_papers`。
- 每个relation必须涉及两个不同paper。
- 每个relation至少引用两条来自相应论文的contribution。
- `research_evolution` 只能描述年代顺序和关注变化，不能在没有来源时声称后一论文继承前一论文。
- `corpus_scope=targeted_sample` 时Schema不允许 `field_gaps`。
- 所有跨论文判断在报告中标记为模型综合判断，其下展开Paper Understanding和Card来源。

## 7. Review Framework与章节知识包

### 7.1 Framework

Framework不再锁定段落Evidence ID。它确定章节问题和需要比较的论文：

```json
{
  "schema_version": "llm.review_framework.v1",
  "working_title": "工作标题",
  "central_question": "中心问题",
  "sections": [
    {
      "section_index": 1,
      "title": "运行组织优化",
      "section_type": "body",
      "question": "本节要回答的问题",
      "purpose": "在全文中的作用",
      "dimension_indexes": [1],
      "paper_ids": ["paper-a", "paper-b"],
      "contribution_ids": ["contribution-a", "contribution-b"],
      "required_comparisons": ["比较评价指标和验证水平"],
      "controversy_ids": [],
      "corpus_gap_indexes": []
    }
  ]
}
```

### 7.2 Chapter Knowledge Package

由代码确定性组装，不让LLM自行检索本地目录：

```text
章节任务
+ 相关Paper Understanding
+ 相关论文完整normalized Markdown
+ 相关论文全部Card
+ 已验证Evidence和逐字引文
+ 题录与引用key
+ Landscape中的关系、分歧和语料缺口
```

v1按Framework显式 `paper_ids` 纳入相关论文全文。超过模型预算时明确失败，不静默丢论文或只保留Evidence。

### 7.3 章节输出

```json
{
  "schema_version": "llm.review_chapter.v1",
  "section_index": 1,
  "title": "运行组织优化",
  "paragraphs": [
    {
      "paragraph_index": 1,
      "text": "章节正文，不包含机器ID。",
      "citation_keys": ["ref-paper-a", "ref-paper-b"]
    }
  ]
}
```

正文只使用论文引用key，不显示Evidence ID。Evidence ID、Card和原文位置进入审计报告。

## 8. 对现有模块的影响

| 现有模块 | 第一阶段 | 最终影响 |
|---|---|---|
| `pdf_conversion.py` | 不改 | 保持PDF解析入口 |
| `markdown_structure.py` / Card | 不改 | Card定位用途增强，不再承担完整写作输入 |
| `topic_review.py` | 不改 | 提供可选Evidence来源 |
| `topic_synthesis.py` | 不改 | 保留为方案C和证据目录 |
| `generate_review_draft_experiment.py` | 不改 | 保留为方案C |
| `reference_catalog.py` | 不改 | 在章节合并后复用 |
| `end_to_end.py` | 不改 | A/B/C验证后才增加新阶段 |
| UI Job与前端 | 不改 | 核心阶段验收后再增加入口和状态 |
| 模型profile | 第一阶段不改Schema | 新阶段配置单独保存输出预算 |

新增模块不能把新字段塞进 `TopicReview` 或 `TopicSynthesis` 旧Schema，以免破坏既有不可变运行。

## 9. 分阶段实施计划

## Phase 1：Paper Understanding

### Task 1：冻结Understanding配置与Schema

**Files:**

- Create: `config/research-understanding-default.json`
- Create: `src/shipping_pipeline/research_understanding_contracts.py`
- Create: `tests/test_research_understanding_contracts.py`

- [x] 写失败测试：未知枚举、空research question、contribution无material、未知material/evidence ID。
- [x] 运行测试并确认模块不存在而失败。
- [x] 实现 `ResearchUnderstandingConfig`、动态JSON Schema和 `validate_paper_understanding`。
- [x] 实现程序生成子对象ID，不接受模型ID。
- [x] 运行：

```powershell
..\.venv\Scripts\python.exe -m pytest tests/test_research_understanding_contracts.py -q
```

Expected: 全部契约测试通过。

公开接口：

```python
def build_paper_understanding_schema(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    material_ids: list[str],
    evidence_unit_ids: list[str],
    config: ResearchUnderstandingConfig,
) -> dict[str, Any]: ...


def validate_paper_understanding(
    payload: object,
    *,
    schema: dict[str, Any],
    material_by_id: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]: ...
```

### Task 2：建立完整Markdown、Card和Evidence输入快照

**Files:**

- Create: `src/shipping_pipeline/research_understanding.py`
- Create: `tests/test_research_understanding.py`

- [x] 写测试：Card代次不一致、缺失 `normalized/document.md`、Topic Review论文身份不一致、Evidence引用未知Card。
- [x] 复用 `create_input_snapshot` 读取当前完整Card。
- [x] 从已验证 `workspace_paper_id` 定位 `normalized/document.md`，复制文本和SHA-256。
- [x] Topic Review run为可选显式参数；不提供时Evidence为空，不自动猜最新运行。
- [x] 将全部输入复制到 `_paper_understandings/runs/<run_id>/input/`。
- [x] 运行：

```powershell
..\.venv\Scripts\python.exe -m pytest tests/test_research_understanding.py -q
```

Expected: 快照身份、代次和来源哈希测试通过。

公开快照：

```python
@dataclass(frozen=True)
class PaperUnderstandingSnapshot:
    paper_id: str
    paper_title: str
    topic: str
    workspace_paper_id: str
    generation_id: str
    document_text: str
    document_sha256: str
    materials: tuple[dict[str, Any], ...]
    projected_cards: tuple[dict[str, Any], ...]
    evidence_units: tuple[dict[str, Any], ...]
    source_topic_review_run_id: str | None
    input_sha256: str
```

### Task 3：实现Prompt、Token预算和Runner

**Files:**

- Modify: `src/shipping_pipeline/research_understanding.py`
- Modify: `tests/test_research_understanding.py`

- [x] 写Fake Client测试，断言模型收到完整Markdown、全部Card和全部可用Evidence。
- [x] 写输入超预算测试，断言直接失败且没有正式output。
- [x] 实现 `PAPER_UNDERSTANDING_SYSTEM_PROMPT`，明确单篇隔离、结果类型和来源绑定。
- [x] 复用 `execute_json_stage` 保存request、raw response、usage和finish reason。
- [x] 验证全部material/evidence引用后才发布output和review。
- [x] 运行专项测试。

Runner签名：

```python
class PaperUnderstandingRunner:
    def run(
        self,
        *,
        paper_id: str,
        workspace_paper_id: str,
        topic: str,
        source_topic_review_run_id: str | None = None,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        understanding_config_path: str | Path = DEFAULT_UNDERSTANDING_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]: ...
```

### Task 4：中文报告与CLI

**Files:**

- Create: `src/shipping_pipeline/research_understanding_report.py`
- Modify: `main.py`
- Modify: `tests/test_research_understanding.py`
- Modify: `README.md`

- [x] 报告按“研究问题、研究对象、方法、贡献、验证水平、限制、综述用途”展示。
- [x] 每个对象下直接展开Card标题、原文摘录和Markdown行号，不要求人工查ID。
- [x] 新增 `llm-paper-understanding` 命令。
- [x] 测试成功返回0、失败返回1、不可变run ID拒绝覆盖。

命令：

```powershell
..\.venv\Scripts\python.exe main.py llm-paper-understanding `
  --workspace workspace `
  --paper-id "论文ID" `
  --workspace-paper-id "review-20260728--xxxxxxxxxxxx" `
  --topic "三峡枢纽通航能力提升方法" `
  --source-topic-review-run-id "ui-brief-..." `
  --run-id "understanding-..." `
  --model-profile config/models/deepseek-v4-pro-official.json `
  --understanding-config config/research-understanding-default.json `
  --timeout 900
```

### Task 5：14篇真实论文验收

**Files:**

- Create: `config/pilots/research-understanding-14papers-20260730.json`
- Create: `docs/research-understanding-14papers-validation-20260730.md`

- [x] 先运行1篇短论文、1篇中等论文、1篇长学位论文。
- [x] 检查模型是否正确区分算法benchmark、仿真、历史数据、工程实施和建议。
- [x] 三篇通过后运行剩余11篇。
- [x] 记录失败，不截断输入或缩减字段后重跑。
- [x] 验收条件：
  - [x] 14篇每篇都有显式状态。
  - [x] 所有发布对象的material/evidence引用有效。
  - [x] 不出现跨论文关系。
  - [x] 仿真结果不标记为工程实施。
  - [x] 作者建议不标记为经验事实。
  - [x] 长论文未因Card数量被压缩成少量Evidence复述。

## Phase 2：Research Landscape

### Task 6：Landscape集合、Schema和Runner

**Files:**

- Create: `config/research-landscape-default.json`
- Create: `src/shipping_pipeline/research_landscape_contracts.py`
- Create: `src/shipping_pipeline/research_landscape.py`
- Create: `src/shipping_pipeline/research_landscape_report.py`
- Create: `tests/test_research_landscape_contracts.py`
- Create: `tests/test_research_landscape.py`
- Modify: `main.py`

- [ ] 集合显式列出Understanding run，不猜最新代次。
- [ ] 重验每个Understanding manifest、output和输入哈希。
- [ ] 同一paper只允许一个Understanding运行。
- [ ] 实现dimension、relation、evolution、disagreement和corpus gap合同。
- [ ] targeted sample禁止field gap字段。
- [ ] 所有论文必须进入dimension或显式unmapped。
- [ ] 中文报告直接展开论文题名、贡献、结果类型和验证水平。
- [ ] 用14篇真实Understanding生成Landscape并审查。

运行：

```powershell
..\.venv\Scripts\python.exe -m pytest `
  tests/test_research_landscape_contracts.py `
  tests/test_research_landscape.py -q
```

## Phase 3：Framework、丰富知识包与章节写作

### Task 7：Review Framework

**Files:**

- Create: `src/shipping_pipeline/review_framework_contracts.py`
- Create: `src/shipping_pipeline/review_framework.py`
- Create: `tests/test_review_framework.py`
- Modify: `main.py`

- [ ] 输入Landscape、review goal和题录状态。
- [ ] 章节绑定dimension、paper和contribution，不绑定段落Evidence。
- [ ] 验证所有核心dimension进入正文，未使用论文显式记录。
- [ ] 输出每节问题、比较任务、争议和语料限制。

### Task 8：Chapter Knowledge Package

**Files:**

- Create: `src/shipping_pipeline/chapter_knowledge_package.py`
- Create: `tests/test_chapter_knowledge_package.py`

- [ ] 从Framework显式paper IDs组装相关完整Markdown。
- [ ] 加入Paper Understanding、全部Card、Evidence逐字引文和题录引用key。
- [ ] 计算真实token预算。
- [ ] 超预算直接失败，报告导致超限的论文和输入token，不截断。
- [ ] 快照保存所有输入文件和哈希。

### Task 9：章节写作与全文装配

**Files:**

- Create: `config/review-writing-default.json`
- Create: `src/shipping_pipeline/review_writing_contracts.py`
- Create: `src/shipping_pipeline/review_writing.py`
- Create: `src/shipping_pipeline/review_writing_report.py`
- Create: `tests/test_review_writing.py`
- Modify: `main.py`

- [ ] 每章一次调用，输入仅为该章知识包。
- [ ] 输出自然段和citation keys，不输出Evidence机器ID。
- [ ] 单章失败不发布完整综述，但保留其他章节中间结果。
- [ ] 全部章节完成后确定性装配全文。
- [ ] 资料范围、局部失败和参考文献由代码生成。
- [ ] 最终全文编辑只能在所有章节通过审计后运行，编辑后重新审计。

## Phase 4：写后审计与A/B/C评价

### Task 10：正文Claim审计

**Files:**

- Create: `src/shipping_pipeline/review_claim_audit_contracts.py`
- Create: `src/shipping_pipeline/review_claim_audit.py`
- Create: `src/shipping_pipeline/review_claim_audit_report.py`
- Create: `tests/test_review_claim_audit.py`

- [ ] 将章节拆成可审计事实Claim和导航性综合判断。
- [ ] 对每条事实标记 `supported`、`qualified`、`unsupported`。
- [ ] 单独检查result type和validation level是否被写大。
- [ ] 复用Evidence时展示Evidence；Evidence不足时回到Card和Markdown。
- [ ] 任何核心结论unsupported时整篇状态不得为publishable。

### Task 11：同一14篇的A/B/C实验

**Files:**

- Create: `config/experiments/review-writing-abc-14papers-20260730.json`
- Create: `src/shipping_pipeline/review_evaluation.py`
- Create: `tests/test_review_evaluation.py`
- Create: `docs/review-writing-abc-14papers-20260730.md`

方案：

- A：14篇完整Markdown + 主题 + 题录，一次直接写作。
- B：Understanding + Landscape + Framework + 章节知识包写作。
- C：当前Evidence-only草稿。

统一指标：

```text
论文覆盖率
核心维度覆盖率
跨论文比较段落比例
事实Claim支持率
result type误写数
validation level误写数
引用论文匹配率
语料缺口越界数
人工修改分钟数
人工修改字符比例
API Token与费用
```

自动评价与人工评价分开保存。LLM评分不能代替事实支持率和人工修改时间。

架构决策门槛：

- B的核心事实支持率不低于A。
- B的跨论文比较和结构评分高于C。
- B的人工修改时间低于C至少25%，或达到用户认为可接受的绝对时间。
- B若明显不如A，则项目收缩为论文池、材料索引、引用和写后审计系统，不继续强化自动写作。

## Phase 5：确认新方案有效后接入Pipeline与UI

仅当Phase 4达到门槛后实施：

- Modify: `src/shipping_pipeline/end_to_end.py`
- Modify: `src/shipping_ui/services/job_service.py`
- Modify: `src/shipping_ui/services/run_comparison_service.py`
- Modify: `src/shipping_ui/project_repository.py`
- Modify: `src/shipping_ui/api/jobs.py`
- Modify: `ui/frontend/src/types.ts`
- Modify: `ui/frontend/src/api.ts`
- Modify: `ui/frontend/src/App.tsx`
- Add对应后端、API和前端测试。

项目状态新增：

```text
paper_understanding_run_ids
research_landscape_run_id
review_framework_run_id
review_writing_run_id
review_audit_run_id
```

仍保持论文处理、论文认知、跨论文Landscape和综述写作彼此独立。一篇论文Understanding失败不阻断其他论文进入论文池；综述任务显式选择可用论文。

## 10. 第一阶段不做的工作

- 不修改MD-to-Card规则。
- 不要求逐行重新证明Markdown完整性。
- 不删除Topic Brief和Evidence。
- 不修改旧Topic Synthesis运行。
- 不把Understanding自动回写到现有Paper Brief。
- 不做跨论文关系的单篇推测。
- 不把定向样本缺口称为领域空白。
- 不接UI。
- 不实现自动截断或字符数兜底。
- 不在同一个目标中实现最终全文编辑。

## 11. 自审

### 输入

新单篇阶段显式读取当前Card代次、规范化Markdown和可选Topic Review运行；跨论文阶段显式列出Understanding运行，不依赖时间或目录猜测。

### 处理

Understanding负责论文内部认知，Landscape负责跨论文关系，Framework负责章节问题，Writing负责语言生成，Evidence Audit负责写后核验，职责不混合。

### 状态

每个阶段都有独立不可变run、manifest、失败状态和正式output。失败运行不污染当前可用论文池，也不向下游暴露旧output。

### 输出

机器对象保留ID和来源绑定；中文报告直接展示论文内容、Card摘录和位置，不要求人工按ID跨文件检索。

### 上下游

第一、二阶段与现有链路并行，风险可控。直到A/B/C实验给出质量证据前，不更改默认Pipeline和UI。

### 推荐执行顺序

先完成Phase 1的五个Task并用14篇真实论文验收。不要在同一实现批次提前进入Landscape；只有Paper Understanding确实比现有Brief保留了更多论文问题、方法、结果边界和验证层次，Phase 2才有可靠输入。
