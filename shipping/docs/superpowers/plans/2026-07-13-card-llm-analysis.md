# Card-to-LLM 可审计论文分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 把当前单卡 LLM 冒烟接口升级为以单篇论文为事务边界、严格校验、失败可追踪、结果可人工审核的论文分析环节。

**Architecture:** 输入是一篇论文当前发布代际的全部 `material.v2` 卡片。runner 先冻结输入快照，再按确定性字符预算在论文内分批抽取材料判定和证据单元；所有批次完整后，第二次基于已验证证据生成论文级分析。每次请求和响应都不可变存档，任何失败进入账本且不触发自动修复或自动重试，只有完整运行才能生成论文级候选结果。

**Tech Stack:** Python 3 标准库、`jsonschema` Draft 2020-12、现有 OpenAI-compatible HTTP 接口、JSON/JSONL/Markdown/CSV 文件。

---

## 1. 原始需求与范围

### 1.1 要解决的问题

MD-to-Card 已经能够提供完整、可追踪的输入，但当前 `llm.analysis.v1` 还存在四个结构性问题：

1. 一张材料卡被强制转换成一张分析卡，即使该卡只有背景、目录式内容或 OCR 噪声。
2. 单张卡独立分析缺少论文上下文，直接把整篇所有卡放进一次请求又无法覆盖 250 卡、约 10.5 万字符的最大论文。
3. 模型输出只做非空检查，非法类型、非法枚举、未知 ID 和重复 ID 可以进入结果。
4. 失败会中断整篇，而且缺少请求、响应、失败位置和 token 用量记录。

### 1.2 本目标包含

- 单篇论文输入快照和来源代际绑定。
- 论文内确定性分批。
- 严格的证据批次 schema 和论文分析 schema。
- 逐批执行、逐批原始响应存档和失败账本。
- 材料覆盖证明、引文逐字验证和跨层引用验证。
- 论文级分析生成。
- 中文人工审核报告和审核决策 CSV。
- 显式选择失败批次重新运行的入口；不自动重试。

### 1.3 本目标不包含

- 不修改 MinerU、Markdown 解析、Card 构建和 material corpus 发布规则。
- 不生成跨论文综述正文。
- 不做向量检索、自动选论文或自动引用格式化。
- 不迁移或兼容读取历史 `llm.analysis.v1` 输出；历史目录只作为旧实验记录保留。
- 不根据模型错误自动补字段、截断输出、提取 Markdown 代码块或切换模型。

## 2. 链路设计

```text
单篇论文当前 material.v2 代际
  -> 输入与代际校验
  -> 不可变输入快照
  -> 确定性批次计划
  -> 批次证据抽取
  -> JSON Schema + 引用完整性校验
  -> 材料覆盖账本
  -> 论文级证据综合
  -> 证据引用校验
  -> 中文人工审核包
```

### 2.1 输入

运行必须显式提供：

- `paper_id`
- `topic`
- `provider`
- `model` 或 env 中的模型名
- `run_id`

runner 从 `_corpus/materials.jsonl` 选取该论文所有当前卡，并同时读取 `_corpus/papers.jsonl`。输入前置条件：

- 论文状态是 `completed`。
- 所有卡的 `paper_id` 相同。
- 所有卡的 `generation_id` 相同，并等于论文当前 generation。
- `material_id` 全部非空且唯一。
- `source_fingerprint` 全部存在。

`--limit` 只保留为 API 冒烟参数。使用它的运行必须标记为 `scope=partial_smoke`，不得生成可发布的完整论文分析。

### 2.2 批次规划

论文是运行和发布边界，请求批次是 API 调用边界。批次按 Card 的稳定顺序构造，不跨论文：

- 先按 `order`、`source_span.start_line`、`material_id` 排序。
- 逐张加入当前批次。
- 以序列化后的实际 prompt 字符数计算预算，而不是估算 Card 数量。
- 超过 `max_prompt_chars` 前关闭当前批次并开始下一批。
- 单张 Card 自身超过预算时，前置校验失败并记录 `input.single_material_over_budget`，不截断 Card。

首版默认 `max_prompt_chars=20000`，同时提供 CLI 参数并写入 manifest。这个值是当前未经真实模型上下文窗口验证的工程假设；实施后必须用实际 API 记录验证，而不能写成模型上限事实。

每个批次都接收论文标题、综述主题、由代码生成的去重标题路径纲要和本批原文。纲要与 schema 都计入实际 prompt 字符预算；如果固定 prompt 本身已经超过预算，前置校验直接失败。批次之间不共享隐藏对话状态。

### 2.3 第一阶段：证据抽取

模型不再被要求“一卡生成一卡”。每张输入 Card 必须在 `material_assessments` 中出现一次：

- `evidence`：包含可用于综述的明确证据。
- `context`：只有背景或衔接信息。
- `not_relevant`：与当前综述主题无直接关系。
- `unusable`：OCR、表格残缺或语义破坏导致不能可靠使用。

一张 Card 可以产生零个、一个或多个 `evidence_units`。模型输出的每个证据单元包含：

- `claim`：对该证据的保守概括。
- `evidence_type`：`background/method/data/result/mechanism/argument/limitation/recommendation/definition`。
- `citations`：至少一个 `{material_id, quote}`。
- `relevance`：与综述主题的关系。
- `confidence`：`high/medium/low`。
- `caveats`：字符串数组，可以为空。

`quote` 必须是对应 Card `extract` 的逐字连续子串。无法逐字引用时，该证据单元校验失败，不允许用“意思相近”代替。

模型不生成任何系统 ID、论文 ID 或原文位置。批次输出通过 schema 和 quote 校验后，runner 按批次 ID 与数组顺序生成 `evidence_unit_id`，并从 Card 中补入来源位置。这样模型不能伪造或重复系统身份字段。

### 2.4 第二阶段：论文级分析

只有全部批次成功，并且每张输入 Card 恰好有一个材料判定后才执行。模型只接收：

- 论文标题与综述主题。
- 已验证证据单元。
- Card 质量标记的确定性汇总。
- 材料判定数量汇总。

它不再接收原始 Markdown，也不能新增证据。输出 `paper_analysis.v1`：

- `research_focus`
- `study_type`：`quantitative/qualitative/simulation/modeling/policy_analysis/review/conceptual/mixed/unclear`
- `methods`
- `core_findings[]`
- `limitations[]`
- `review_uses[]`
- `unresolved_questions[]`

`core_findings`、`limitations` 和 `review_uses` 中每一项都必须引用一个或多个 `evidence_unit_id`。不存在的 ID 直接使论文级分析失败。

如果所有 Card 都已成功判定，但证据单元数量为 0，这是一个有效的“没有可用证据”结果，不是 API 故障。runner 不调用论文综合模型，写出确定性的 `paper_disposition=no_usable_evidence`，运行状态仍可为 `completed`，但该结果不能作为综述证据发布。

### 2.5 状态变化

运行状态只有以下几种：

- `running`：输入快照已建立，批次尚未全部完成。
- `partial`：至少一个批次成功、至少一个批次失败，保留诊断产物，不生成论文级分析。
- `failed`：输入校验失败、全部批次失败或论文级分析失败。
- `completed`：全部材料已判定、证据校验完成、论文级分析校验完成。

人工审核状态独立于运行状态：

- `pending`
- `accepted`
- `rejected`
- `needs_revision`

`completed` 只表示机器契约完整，不表示内容已经被人接受。

### 2.6 不做的兜底

- 不为缺失字段提供默认值。
- 不把字符串自动转换为列表。
- 不接受 schema 外字段。
- 不从 Markdown 代码块中猜 JSON。
- 不在 JSON 解析失败后调用模型“修复 JSON”。
- 不自动重试超时、HTTP 错误或 schema 错误。
- 不用低质量批次的部分结果生成论文级分析。

## 3. 输出目录和审计产物

新版本写入独立目录，不覆盖历史 v1 结果：

```text
workspace/_llm_analysis/runs/<run_id>/
  manifest.json
  input/
    paper.json
    materials.jsonl
    batches.json
  schemas/
    evidence_batch.schema.json
    paper_analysis.schema.json
  prompts/
    evidence_system.md
    synthesis_system.md
  batches/
    batch_0001/
      request.json
      raw_response.json
      parsed_response.json
      validated_output.json
      result.json
  output/
    material_assessments.jsonl
    evidence_units.jsonl
    paper_analysis.json
  audit/
    failures.jsonl
    coverage.json
  review/
    review.md
    decisions.csv
```

`request.json` 不包含 API key。`raw_response.json` 保存服务返回的完整 JSON；若响应不是 JSON，另存 `raw_response.txt`。每个 `result.json` 至少记录：

- `batch_id`
- `status`
- `material_ids`
- `provider`
- 请求模型名和响应模型名
- `response_id`
- `system_fingerprint`
- `prompt_sha256`
- `input_sha256`
- `started_at` / `finished_at`
- `usage.prompt_tokens` / `usage.completion_tokens` / `usage.total_tokens`
- 失败时的 `error_code`、`error_message` 和 schema 错误路径

没有返回的 usage 字段写 `null`，不能估算后伪装成 API 实际值。

## 4. 完整性不变量

完成状态必须同时满足：

1. `input material_id` 集合等于 `material_assessments.material_id` 集合。
2. 每个输入 `material_id` 只被判定一次。
3. 每个 evidence citation 的 `material_id` 属于对应批次。
4. 每个 evidence quote 是对应 Card `extract` 的逐字子串。
5. 所有 `evidence_unit_id` 全局唯一。
6. 每个论文级结论引用的 evidence ID 存在。
7. 输入 snapshot 的 generation 在运行结束时仍是该论文当前 generation。
8. `partial_smoke` 运行永远不能满足完整论文发布条件。
9. `paper_disposition=no_usable_evidence` 的运行不包含模型生成的论文结论。

`coverage.json` 必须显式给出：

- 输入 Card 数量。
- 已判定 Card 数量。
- 未判定和重复判定的 material IDs。
- 证据单元数量。
- 非法 citation 和非法 quote 列表。
- 论文级未解析 evidence IDs。

## 5. 文件职责

- Create: `shipping/src/shipping_pipeline/llm_contracts.py`
  - 保存两个 Draft 2020-12 JSON Schema、枚举、系统 ID 生成和所有本地完整性校验。
- Create: `shipping/src/shipping_pipeline/llm_provider.py`
  - 负责 OpenAI-compatible 请求、原始响应 envelope 和 usage/model 元数据提取。
- Modify: `shipping/src/shipping_pipeline/llm_analysis.py`
  - 只负责输入快照、批次计划、运行状态、产物写入和两阶段编排。
- Create: `shipping/src/shipping_pipeline/llm_review.py`
  - 生成中文 Markdown 审核报告、决策 CSV 并校验导入结果。
- Modify: `shipping/main.py`
  - 增加批次预算、失败批次显式重跑和审核导入参数。
- Create: `shipping/tests/test_llm_contracts.py`
  - 覆盖 schema、枚举、未知字段、ID、quote 和引用完整性。
- Create: `shipping/tests/test_llm_analysis.py`
  - 覆盖批次规划、部分失败、原始响应、代际变化、论文综合和原子状态。
- Create: `shipping/tests/test_llm_review.py`
  - 覆盖中文审核报告和决策 CSV。
- Create: `shipping/requirements.txt`
  - 固定 `jsonschema>=4.23,<5`；当前环境尚未安装该依赖，实施时需要安装后验证。
- Modify: `shipping/README.md`
  - 更新 v2 运行、输出和故障检查说明。
- Modify: `shipping/docs/progress-20260709.md`
  - 实施完成后追加真实 API 验证结果，不覆盖本次设计记录。

## 6. 实施任务

### Task 1: 建立正式 schema 和确定性校验

**Files:**
- Create: `shipping/src/shipping_pipeline/llm_contracts.py`
- Create: `shipping/tests/test_llm_contracts.py`
- Create: `shipping/requirements.txt`

- [x] **Step 1: 写失败测试，证明非法类型、非法枚举、未知字段和空字符串被拒绝**

测试分别构造 `confidence="certain"`、`caveats="无"`、额外字段和空 `claim`，断言返回包含 JSON 路径的 `ContractViolation`。

- [x] **Step 2: 运行契约测试并确认因模块不存在而失败**

Run: `python -m unittest shipping.tests.test_llm_contracts -v`

Expected: `ModuleNotFoundError: shipping_pipeline.llm_contracts`

- [x] **Step 3: 添加 `jsonschema` 依赖并实现 Draft 2020-12 schema**

两个 schema 均设置 `additionalProperties: false`，所有对象明确 `required`，所有文本字段设置 `minLength: 1`，枚举使用第 2 节定义的闭集。

- [x] **Step 4: 实现跨字段完整性校验**

实现以下显式函数：

```python
def validate_evidence_batch(payload: object, materials: list[dict[str, Any]]) -> dict[str, Any]: ...
def validate_paper_analysis(payload: object, evidence_ids: set[str]) -> dict[str, Any]: ...
def verify_material_coverage(material_ids: list[str], assessments: list[dict[str, Any]]) -> dict[str, list[str]]: ...
def verify_exact_quotes(evidence_units: list[dict[str, Any]], materials_by_id: dict[str, dict[str, Any]]) -> list[dict[str, str]]: ...
def assign_evidence_unit_ids(batch_id: str, evidence_units: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
```

- [x] **Step 5: 运行契约测试**

Run: `python -m unittest shipping.tests.test_llm_contracts -v`

Expected: PASS。

### Task 2: 冻结输入快照并生成确定性批次

**Files:**
- Modify: `shipping/src/shipping_pipeline/llm_analysis.py`
- Create: `shipping/tests/test_llm_analysis.py`

- [x] **Step 1: 写输入代际和批次边界失败测试**

覆盖混合论文、混合 generation、重复 material ID、单卡超过预算，以及相同输入重复规划得到相同 batch ID 和 Card 顺序。

- [x] **Step 2: 实现 `AnalysisInputSnapshot` 和 `plan_batches`**

快照保存 source generation、Card ID、有序输入 SHA-256 和 topic SHA-256。批次 ID 使用 `batch_0001` 顺序编号，批次清单写明实际 prompt 字符数。

- [x] **Step 3: 验证 16 篇真实语料的批次计划**

运行 dry-run，不调用 API。报告每篇批次数、最大批次字符数和超预算 Card；预期 490 张 Card 中的每一张都恰好进入一个已规划批次。

### Task 3: 拆出 provider 并保存完整调用记录

**Files:**
- Create: `shipping/src/shipping_pipeline/llm_provider.py`
- Modify: `shipping/src/shipping_pipeline/llm_analysis.py`
- Modify: `shipping/tests/test_llm_analysis.py`

- [x] **Step 1: 写响应 envelope 测试**

使用 fake transport 分别返回成功响应、无 choices、HTTP 异常、非 JSON 正文和带 usage 的响应。断言原始响应先保存，再进入解析和校验。

- [x] **Step 2: 实现 `ProviderResult`**

```python
@dataclass(frozen=True)
class ProviderResult:
    raw_body: bytes
    parsed_response: dict[str, Any] | None
    response_id: str | None
    response_model: str | None
    system_fingerprint: str | None
    usage: dict[str, int | None]
```

- [x] **Step 3: 确保 API key 不写入任何产物**

测试扫描整个 run 目录，断言 secret 字符串不存在。

### Task 4: 实现逐批执行和失败账本

**Files:**
- Modify: `shipping/src/shipping_pipeline/llm_analysis.py`
- Modify: `shipping/tests/test_llm_analysis.py`

- [x] **Step 1: 写中间批次失败测试**

构造三个批次，让第二批返回非法枚举。断言第一、三批仍完成，第二批写入失败账本，run 状态为 `partial`，且不存在 `output/paper_analysis.json`。

- [x] **Step 2: 实现单批状态机**

每个批次按 `request_saved -> response_saved -> parsed -> validated` 顺序记录；任何阶段失败后只终止该批次，不删除已有文件。

- [x] **Step 3: 实现整篇 coverage 审计**

在所有批次结束后独立重算 Card 判定覆盖、quote 和 ID 唯一性，不信任单批返回的自报数量。

- [x] **Step 4: 实现显式失败批次重跑**

CLI 必须提供原 run ID 和明确 batch ID。新 run 记录 `parent_run_id`；不自动发现后直接重试，不覆盖父运行文件。只有 source generation、输入快照 SHA-256、schema SHA-256、prompt SHA-256、provider 和 model 全部与父运行一致时，子运行才允许显式复用父运行中已验证的成功批次。`manifest.json` 必须分别列出 `reused_batch_ids` 和 `executed_batch_ids`；任一条件变化都要求重新运行整篇，不能混合两套分析条件。

### Task 5: 实现论文级综合

**Files:**
- Modify: `shipping/src/shipping_pipeline/llm_analysis.py`
- Modify: `shipping/src/shipping_pipeline/llm_contracts.py`
- Modify: `shipping/tests/test_llm_analysis.py`

- [x] **Step 1: 写论文综合前置条件测试**

断言存在失败批次、漏判 Card、重复判定或非法 quote 时均不调用 synthesis provider。

另写零证据测试：所有 Card 均被判定为 `context/not_relevant/unusable` 时，不调用 synthesis provider，输出 `paper_disposition=no_usable_evidence`，且不存在模型生成的 findings。

- [x] **Step 2: 构造只包含已验证证据的 synthesis prompt**

prompt 不包含原始整篇 Markdown，不允许新增 citation，只允许引用给定 evidence IDs。

- [x] **Step 3: 校验论文级引用**

不存在的 evidence ID、空 findings、非法 study type 或 schema 外字段均使 run 失败，并保留 synthesis 原始响应。

- [x] **Step 4: 完成后重查输入代际**

若运行期间 source generation 已变化，记录 `input.source_generation_changed`，run 变为 `failed`，不发布论文级结果。

### Task 6: 生成人工审核包和决策文件

**Files:**
- Create: `shipping/src/shipping_pipeline/llm_review.py`
- Create: `shipping/tests/test_llm_review.py`

- [x] **Step 1: 写中文审核报告测试**

审核报告按“论文总览 -> 方法 -> 核心发现 -> 限制 -> 逐条证据”组织。每条证据显示观点、逐字引文、Card 标题、原 Markdown 位置、Card 质量标记和 LLM 置信度。

- [x] **Step 2: 生成 `decisions.csv`**

CSV 每个 evidence unit 一行，列为 `evidence_unit_id, 决策, 修改后观点, 备注`；初始决策为 `pending`。

- [x] **Step 3: 校验审核决策导入**

只接受 `accept/reject/revise/pending`，拒绝未知 evidence ID、重复行和 `revise` 但修改后观点为空。

- [x] **Step 4: 保持运行状态与审核状态分离**

导入决策只写新的 review 状态文件，不修改不可变的模型输出和原始响应。

### Task 7: CLI、文档与全链验证

**Files:**
- Modify: `shipping/main.py`
- Modify: `shipping/README.md`
- Modify: `shipping/docs/progress-20260709.md`
- Modify: `shipping/tests/test_pipeline.py`

- [x] **Step 1: 更新 CLI**

保留 `llm-analysis` 名称，新增 `--max-prompt-chars`、`--retry-from`、`--retry-batch-id` 和 `--dry-run`。真实完整运行仍必须显式 `--paper-id`。

- [x] **Step 2: 运行全部自动测试和编译检查**

Run: `python -m unittest discover -s shipping/tests -v`

Run: `python -m compileall -q shipping/src shipping/main.py shipping/scripts`

Expected: 全部通过，命令退出码为 0。

- [x] **Step 3: 对 16 篇当前论文执行 dry-run**

不调用 API，只验证输入快照、批次覆盖和预算。任何论文存在未分批 Card 时停止真实验证。

- [x] **Step 4: 运行 mock 的完整单篇两阶段分析**

选择中等规模论文，确认所有 Card 被判定、证据 quote 可验证、论文结论引用存在、审核包可读。

- [x] **Step 5: 运行真实 API 小规模验证**

先用 `partial_smoke` 验证一批，再选一篇小论文执行完整运行。记录真实模型名、响应 usage、耗时、失败代码和人工抽查结论。不得把一次 API 成功表述为全语料质量已经验证。

## 7. 验收标准

本目标只有同时满足以下条件才算完成：

- 自动测试覆盖所有不变量和失败路径。
- 16 篇当前可发布论文的 dry-run 输入覆盖均为 100%。
- mock 完整运行能形成可读的论文级审核包。
- 至少一篇真实论文完成全部批次和论文级综合。
- 任意批次失败时仍能看到成功批次和原始失败响应，但没有论文级结果。
- manifest 能回答“用了哪一代 Card、什么 prompt、什么模型、哪些批次、多少 token、哪里失败”。
- 人工审核可以从结论回到 evidence unit，再回到 material ID 和原 Markdown 位置。

## 8. 已知假设与未验证前提

- 当前 OpenAI-compatible endpoint 是否支持原生 `response_format=json_schema` 尚未验证。首版以本地 Draft 2020-12 校验为最终契约，不把服务端 schema 支持作为完成前提。
- 默认 20000 prompt 字符预算已通过 16 篇 dry-run 和 DeepSeek 真实 API 验证；结果显示它可用但对 1M 模型偏碎，默认策略仍需比较实验。
- 仓库根目录已经建立 `.venv`，并安装固定版本 `jsonschema==4.26.0`。
- Card 中的逐字引文只能证明模型引用了现有 Card，不能修复 MinerU 已经丢失或识别错误的原文。

## 9. 自检结果

- 输入、处理、状态、输出及上下游边界均已定义。
- 设计没有要求修改 MD-to-Card，也没有直接接入综述生成。
- 部分运行不会发布为完整论文分析。
- 所有模型生成内容都必须经过结构和引用校验。
- 未确认的模型能力和字符预算已经明确标为待验证假设。
