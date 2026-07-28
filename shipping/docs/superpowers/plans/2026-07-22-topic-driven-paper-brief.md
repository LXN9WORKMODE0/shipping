# 主题驱动论文材料简报 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一条默认的轻量论文分析路径，先按综述主题选择少量 Card，再对入选内容抽取可追溯证据并生成可直接审核的中文论文材料简报。

**Architecture:** 新命令 `llm-topic-brief` 与现有重审计型 `llm-analysis` 隔离。每篇论文最多经历“主题定界、入选 Card 证据抽取、论文简报”三个阶段；未入选 Card 的清单被存档，但其解析缺陷不阻断运行。Evidence 继续使用现有逐字引文、数值和表格合同，论文简报不再运行三道全量语义门禁和人工裁决链。

**Tech Stack:** Python 3.11、jsonschema、现有 OpenAI-compatible Provider、DeepSeek-V4 tokenizer、unittest。

**执行状态（2026-07-22）：已完成。** Task 1-5 已实现并通过回归；Task 6 已完成真实 API 试运行和停止判断。最终规则下5个验收代际全部成功，另保留3 Card早期校准和全部失败代际。最终审计见 `docs/topic-driven-paper-brief-pilot-20260722.md`。原计划中的检查框保留为实施前任务清单，实际完成状态以本段和审计报告为准。

---

## 1. 设计边界

本计划只解决“单篇论文如何围绕综述主题形成可用材料简报”。以下内容明确不在本阶段实现：

- 不修复 MinerU 已损坏的表格或 OCR。
- 不证明所有 Card 都被语义理解。
- 不为未入选 Card 生成 Evidence。
- 不执行 Evidence claim 双轮复核、字段职责复核、陈述支持复核和人工裁决。
- 不直接生成跨论文综述；跨论文综合等论文简报样本验收后单独设计。
- 不改写或删除现有 `llm-analysis`，它保留为需要高强度审计时的可选入口。

默认深度和规模上限写入显式配置，不由 Prompt 隐式决定：

```json
{
  "schema_version": "llm.topic_review_config.v1",
  "max_selected_materials": {
    "core": 6,
    "supporting": 4,
    "peripheral": 2,
    "exclude": 0
  },
  "max_evidence_units_per_material": 1,
  "max_brief_points": {
    "core": 6,
    "supporting": 4,
    "peripheral": 2
  },
  "max_review_uses": 4,
  "max_cautions": 3,
  "scope_max_output_tokens": 8192,
  "brief_max_output_tokens": 8192
}
```

论文相关性定义：

- `core`：直接回答综述主题的核心问题，可作为主要论据。
- `supporting`：提供背景、方法、局部数据或比较材料。
- `peripheral`：只有边缘关联，最多保留一至两个可用点。
- `exclude`：对当前主题没有可用贡献，不再调用 Evidence 和简报模型。

## 2. 文件结构

- Create: `shipping/src/shipping_pipeline/topic_review_contracts.py`
  - 配置、主题定界 Schema、论文简报 Schema 和确定性校验。
- Create: `shipping/src/shipping_pipeline/topic_review.py`
  - 不可变运行目录、三阶段 API 调度、Token 检查和失败状态。
- Create: `shipping/src/shipping_pipeline/topic_review_report.py`
  - 把内部 JSON 和 Evidence ID 转为直接展示原文引文的中文 Markdown。
- Create: `shipping/config/topic-review-default.json`
  - 上述规模和输出预算。
- Create: `shipping/tests/test_topic_review_contracts.py`
  - 动态 Schema、深度上限、ID 归属和数值支持反例。
- Create: `shipping/tests/test_topic_review.py`
  - exclude、正常完成、入选证据失败和未入选坏表不阻断等流程测试。
- Modify: `shipping/main.py`
  - 增加 `llm-topic-brief` 命令。
- Modify: `shipping/README.md`
  - 把轻量入口写成默认路径，把旧入口标为深度审计路径。
- Modify: `shipping/docs/progress-20260709.md`
  - 记录真实样本结果、停止条件和验收结论。

## 3. 数据流与状态

```text
同篇论文全部 Card
  -> topic_scope：只选与主题最有关的 Card
      -> exclude：直接生成排除报告并结束
      -> core/supporting/peripheral
          -> selected_evidence：每张入选 Card 单独读取其 quote candidates
          -> topic_brief：只读取已验证 Evidence
          -> paper_brief.json + paper_brief.md
```

运行目录固定为：

```text
workspace/_topic_reviews/runs/<run_id>/
  manifest.json
  input/paper.json
  input/materials.jsonl
  scope/request.json
  scope/raw_response.json
  scope/parsed_response.json
  scope/validated_scope.json
  evidence/request.json
  evidence/raw_response.json
  evidence/parsed_response.json
  evidence/evidence_units.jsonl
  output/paper_brief.json
  review/paper_brief.md
  audit/omitted_materials.jsonl
  audit/failures.jsonl
```

`manifest.status` 只允许：

- `completed`：入选证据和简报全部通过。
- `excluded`：主题定界为 exclude，未继续调用。
- `failed`：API、JSON、Token、入选 Evidence 或简报合同失败。

未入选 Card 的解析标记只进入 `audit/omitted_materials.jsonl`，不进入失败账本。入选 Card 各自执行一次 Evidence 请求，避免跨 Card 引文错配；任一入选 Card 的 Evidence 合同失败则整篇运行失败。

## 4. 实施任务

### Task 1: 主题定界与简报合同

**Files:**
- Create: `shipping/src/shipping_pipeline/topic_review_contracts.py`
- Create: `shipping/config/topic-review-default.json`
- Create: `shipping/tests/test_topic_review_contracts.py`

- [ ] **Step 1: 写配置和主题定界反例测试**

测试必须覆盖：未知 material ID、重复选择、超过对应深度上限、exclude 仍选择 Card、非 exclude 没有选择 Card。

```python
def test_scope_rejects_selection_beyond_depth_limit(self) -> None:
    config = TopicReviewConfig.default()
    payload = {
        "schema_version": "llm.topic_scope.v1",
        "paper_relevance": "peripheral",
        "relevance_reason": "只有边缘背景作用。",
        "topic_summary": "论文提供一般航运背景。",
        "selected_materials": [
            {
                "material_id": f"m{i}",
                "priority": "secondary",
                "intended_use": "background",
                "selection_reason": "提供背景。",
            }
            for i in range(3)
        ],
    }
    with self.assertRaisesRegex(TopicReviewContractError, "scope.selection_limit_exceeded"):
        validate_topic_scope(payload, {"m0", "m1", "m2"}, config)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_review_contracts -v
```

Expected: FAIL，原因是 `topic_review_contracts` 尚不存在。

- [ ] **Step 3: 实现配置和合同**

核心常量、数据类和函数签名固定为：

```text
TOPIC_SCOPE_SCHEMA_VERSION = "llm.topic_scope.v1"
TOPIC_BRIEF_SCHEMA_VERSION = "llm.topic_brief.v1"
PAPER_RELEVANCE_LEVELS = ("core", "supporting", "peripheral", "exclude")

TopicReviewConfig(
    schema_version: str,
    max_selected_materials: dict[str, int],
    max_evidence_units_per_material: int,
    max_brief_points: dict[str, int],
    max_review_uses: int,
    max_cautions: int,
    scope_max_output_tokens: int,
    brief_max_output_tokens: int,
)

load_topic_review_config(path: Path) -> TopicReviewConfig
build_topic_scope_schema(material_ids: list[str]) -> dict[str, Any]
validate_topic_scope(payload, material_ids, config) -> dict[str, Any]
build_topic_brief_schema(evidence_unit_ids, relevance, config) -> dict[str, Any]
validate_topic_brief(payload, evidence_units, relevance, config) -> dict[str, Any]
```

简报 JSON 固定为：

```json
{
  "schema_version": "llm.topic_brief.v1",
  "topic_contribution": {
    "statement": "论文对当前综述主题最直接的贡献。",
    "evidence_unit_ids": ["evidence_0123456789abcdef01234567"]
  },
  "key_points": [
    {
      "point_type": "finding",
      "statement": "可进入综述的事实或论点。",
      "evidence_unit_ids": ["evidence_0123456789abcdef01234567"]
    }
  ],
  "review_uses": [
    {
      "use_type": "support",
      "statement": "建议在综述中怎样使用。",
      "evidence_unit_ids": ["evidence_0123456789abcdef01234567"]
    }
  ],
  "cautions": [
    {
      "statement": "引用时需要保留的限制。",
      "evidence_unit_ids": ["evidence_0123456789abcdef01234567"]
    }
  ]
}
```

`point_type` 只允许 `background/method/finding/argument/limitation`；`use_type` 只允许 `background/support/comparison/method/counterpoint`。所有 Evidence ID 必须存在。每条 statement 中的数值必须出现在所引 Evidence 的 claim 或逐字引文中。

- [ ] **Step 4: 运行合同测试**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_review_contracts -v
```

Expected: PASS。

### Task 2: 论文级主题定界

**Files:**
- Create: `shipping/src/shipping_pipeline/topic_review.py`
- Create: `shipping/tests/test_topic_review.py`

- [ ] **Step 1: 写 scope 流程测试**

测试必须证明：全部 Card 进入 scope Prompt；模型只返回入选 ID；代码把其余 ID 明确写成 omitted；exclude 后没有 Evidence 和 brief API 调用。

```python
def test_excluded_paper_stops_after_scope_and_records_all_omitted_cards(self) -> None:
    client = FakeClient([
        scope_response(relevance="exclude", selected_material_ids=[]),
    ])
    result = TopicReviewRunner(self.workspace).run(
        paper_id="paper-a",
        topic="三峡船舶积压疏导",
        run_id="scope-exclude",
        client=client,
        profile=self.profile,
        token_counter=self.counter,
        config=self.config,
    )
    self.assertEqual("excluded", result["status"])
    self.assertEqual(1, len(client.calls))
    self.assertEqual(["m1", "m2", "m3"], read_omitted_ids(result["run_dir"]))
```

- [ ] **Step 2: 实现 scope Prompt 和单次请求**

Prompt 必须说明这不是证据抽取任务，模型只选择最值得进一步分析的 Card，不得为覆盖完整而多选。输入使用现有 `project_cards()` 的全部字段，不附带 quote candidates。

核心入口固定为：

```text
TopicReviewRunner(workspace: Path)

TopicReviewRunner.run(
    paper_id: str,
    topic: str,
    run_id: str,
    client: Any,
    profile: ModelProfile,
    token_counter: Any,
    config: TopicReviewConfig,
) -> dict[str, Any]
```

scope 请求必须在调用前检查：

```python
prompt_tokens + config.scope_max_output_tokens + profile.safety_margin_tokens \
    <= profile.context_window_tokens
```

实际 `prompt_tokens` 必须等于服务端 usage；`finish_reason != stop`、reasoning 非空、非法 JSON 或 Schema 错误均写入失败账本并停止，不自动重试。

- [ ] **Step 3: 运行 scope 测试**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_review.TopicScopeFlowTests -v
```

Expected: PASS。

### Task 3: 只对入选 Card 抽取 Evidence

**Files:**
- Modify: `shipping/src/shipping_pipeline/topic_review.py`
- Modify: `shipping/tests/test_topic_review.py`

- [ ] **Step 1: 写选择边界测试**

测试必须证明：未入选坏表不会进入 Evidence Prompt，也不会阻断；入选坏表合同失败会阻断；每张入选 Card 单独请求且只允许1条 Evidence；未入选 Card 不要求 material assessment。

```python
def test_unselected_damaged_table_does_not_block_selected_prose(self) -> None:
    client = FakeClient([
        scope_response(relevance="supporting", selected_material_ids=["prose-1"]),
        evidence_response(material_id="prose-1", claim="船闸通过能力受到约束。"),
        brief_response(evidence_unit_ids=[stable_evidence_id("prose-1")]),
    ])
    result = run_topic_review(client, materials=[valid_prose(), damaged_table()])
    self.assertEqual("completed", result["status"])
    self.assertEqual(["damaged-table"], read_omitted_ids(result["run_dir"]))
```

- [ ] **Step 2: 构造入选 Evidence Schema 和 Prompt**

复用 `build_evidence_batch_schema()`，然后将每个 material result 收紧为：

```python
result_schema["properties"]["disposition"] = {"const": "evidence"}
result_schema["properties"]["reason_code"] = {"const": "direct_evidence"}
result_schema["properties"]["evidence_units"]["minItems"] = 1
result_schema["properties"]["evidence_units"]["maxItems"] = config.max_evidence_units_per_material
```

每次 Evidence Prompt 只接收 scope 已选中的一张 Card，要求提取1条最能服务当前主题的原子证据，不追求覆盖 Card 全部事实。验证继续调用 `validate_evidence_batch()` 和 `assign_evidence_unit_ids()`，因此现有逐字引用、数值、限定词和表格合同保持有效。

- [ ] **Step 3: 明确失败行为**

新轻量路径不执行一次性 Evidence 修正。首次入选证据失败即写入 `audit/failures.jsonl`，报告直接展示 Card 标题、原文和错误；未入选 Card 永远不会进入该失败路径。

- [ ] **Step 4: 运行 Evidence 流程测试**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_review.TopicEvidenceFlowTests -v
```

Expected: PASS。

### Task 4: 生成可人工阅读的论文简报

**Files:**
- Create: `shipping/src/shipping_pipeline/topic_review_report.py`
- Modify: `shipping/src/shipping_pipeline/topic_review.py`
- Modify: `shipping/tests/test_topic_review.py`

- [ ] **Step 1: 写简报与报告测试**

测试必须证明：Brief 只能引用现有 Evidence；数字不能越过证据；Markdown 不要求人工追查 Evidence ID；每个观点下直接显示逐字引文、Card 标题和 Markdown 行号；报告显示入选数和未入选数。

```python
def test_human_report_embeds_quotes_instead_of_only_evidence_ids(self) -> None:
    markdown = render_topic_review_report(scope, brief, evidence_units, materials)
    self.assertIn("## 可用于综述的观点", markdown)
    self.assertIn("船闸通过能力受到约束", markdown)
    self.assertIn("原文：", markdown)
    self.assertIn("L120-L124", markdown)
    self.assertNotIn("请查询 evidence_", markdown)
```

- [ ] **Step 2: 实现 brief Prompt**

模型只读取 topic、scope 结论和已验证 Evidence，不读取未入选 Card。Prompt 要求按当前相关性深度控制数量，避免复述所有 Evidence；`review_uses` 允许有依据的综述用途判断，但必须引用促成该判断的 Evidence。

- [ ] **Step 3: 实现中文 Markdown 报告**

报告顺序固定为：

1. 主题相关性与一句话贡献。
2. 建议在综述中的使用方式。
3. 可用于综述的观点，每条下方直接展示原文。
4. 方法或限制类注意事项。
5. 入选 Card 列表及选择理由。
6. 未入选 Card 数量和章节分布。
7. 解析警告，仅区分“影响入选证据”和“位于未入选内容”。

Evidence ID 保留在 JSON 中；Markdown 只在末尾“机器追踪信息”中显示，不作为人工审核入口。

- [ ] **Step 4: 运行简报测试**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest tests.test_topic_review.TopicBriefFlowTests -v
```

Expected: PASS。

### Task 5: CLI、文档与完整回归

**Files:**
- Modify: `shipping/main.py`
- Modify: `shipping/README.md`
- Modify: `shipping/docs/progress-20260709.md`
- Modify: `shipping/tests/test_topic_review.py`

- [ ] **Step 1: 增加 CLI 测试**

命令固定为：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-topic-brief --workspace shipping/workspace --paper-id "论文ID" --topic "综述主题" --run-id "运行ID" --provider openai-compatible --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json --review-config shipping/config/topic-review-default.json --timeout 900
```

CLI 必须要求非空 topic、已完成且 generation 当前的论文、唯一 run-id。已存在运行目录直接拒绝，不覆盖。

- [ ] **Step 2: 接入 CLI 并更新 README**

README 将 `llm-topic-brief` 标为默认单篇论文处理入口；`llm-analysis` 改称“深度审计入口”，只在核心论文确实需要完整审计时使用。

- [ ] **Step 3: 执行完整回归和编译检查**

Run:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
..\.venv\Scripts\python.exe -m compileall -q src scripts tests
```

Expected: 所有测试通过，两个命令退出码均为0。

### Task 6: 六篇真实论文试运行与停止条件

**Files:**
- Create: `shipping/docs/topic-driven-paper-brief-pilot-20260722.md`
- Runtime outputs: `shipping/workspace/_topic_reviews/runs/<run_id>/`

- [ ] **Step 1: 先做 dry-run Token 规划**

样本固定覆盖不同长度和预期相关度：

- `三峡航运遇瓶颈`：3 Card，短篇。
- `基于Arena的三峡船舶积压疏导策略效果研究`：16 Card，预计核心。
- `三峡坝区船舶积压对策探讨`：19 Card，预计核心或支持。
- `川江滚装运输发展的SWOT分析及对策`：23 Card，预计支持。
- `考虑翻坝和天气的长江班轮运网鲁棒优化模型`：82 Card，长篇且含复杂表格。
- `长江上游地区产业布局及航运适应性研究`：250 Card，超长且预计只有局部相关。

所有 scope Prompt 必须在100万 Token上下文内；超限直接记录，不按字符数或截断处理。

- [ ] **Step 2: 执行真实 Non-think API 运行**

每篇论文先执行 scope。`exclude` 立即结束；其他论文只继续处理入选 Card。每次请求记录 `finish_reason`、reasoning字符数、计划/实际输入 Token和输出 Token。

- [ ] **Step 3: 按面向综述的指标审计**

试运行验收指标固定为：

- 人工阅读一篇 Markdown 简报不需要打开内部 JSON 或手动查询 Evidence ID。
- core 论文提供3至6个可用观点，supporting 提供1至4个，peripheral 不超过2个。
- 每个事实性观点下直接显示支持它的原文。
- 未入选 Card 中的解析缺陷不导致运行失败。
- 入选证据失败时，报告能直接指出 Card、原文和错误。
- 每篇论文需要人工决定的问题不超过2项；超过则视为流程仍然过重。
- 不以“所有 Card 均生成 Evidence”或“所有表格均可解析”作为指标。

- [ ] **Step 4: 执行停止判断**

六篇中至少5篇满足上述指标，并且82/250 Card论文没有因未入选内容失败时，单篇论文材料简报阶段即停止开发并提交人工验收。只有出现跨多篇论文重复的同类问题，才允许修改代码；单篇 OCR、表格和标题个例只写入审计，不新增规则。

## 5. 自审结论

- 输入：同一论文当前 generation 的全部 Card、非空综述主题、固定模型配置和轻量流程配置。
- 处理：整篇主题定界；只对选中 Card 进行严格 Evidence 抽取；Evidence 生成简报。
- 状态：未入选问题不阻断；入选证据问题阻断；所有 API 和失败记录不可变。
- 输出：机器可读 scope/evidence/brief，加一份无需追查内部 ID 的中文报告。
- 上游影响：不修改 MD-to-Card、MinerU 或材料代际。
- 下游影响：本阶段不发布到综述语料；六篇试运行验收后，跨论文综合只读取已验收的 `paper_brief.json`。
- 停止条件：不再根据单篇边缘个例继续扩展解析或证据合同。
