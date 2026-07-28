# 单篇论文 Token 感知 LLM 分析实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Card→LLM v2 改造成严格单论文隔离、使用 DeepSeek-V4-Pro 官方 Tokenizer、默认关闭思考、按章节处理超预算论文并对综合证据做完整覆盖审计的 v3 流程。

**Architecture:** 每次运行仍只接受一篇论文的当前 Card generation。系统先生成只含模型必需字段的 Card 投影，再使用固定版本的 DeepSeek-V4-Pro 官方编码器计算真实 Token；整篇论文满足输入和输出预算时只调用一次证据抽取，超预算时才读取该论文现有 `structure.json` 并按可验证章节拆分。所有证据抽取完成后，综合阶段按预算选择直接论文综合或“章节综合→论文综合”，失败只记录、不修补、不自动降级。

**Tech Stack:** Python 3.13 独立环境 `.venv`、标准库、`jsonschema==4.26.0`、`tokenizers==0.23.1`、DeepSeek-V4-Pro 官方 tokenizer/encoding（固定 Hugging Face revision `0e1a0e5e52aea73055f50fef6f2423db370265b6`）、SiliconFlow OpenAI-compatible API。

---

## 已确认约束

1. 一篇论文等于一个隔离运行；不允许跨论文拼 Prompt。
2. DeepSeek-V4-Pro 支持 1,000,000 Token 上下文和 Non-think、Think High、Think Max 三种模式。
3. OpenAI 格式关闭思考的正式参数为：

   ```json
   {"thinking": {"type": "disabled"}}
   ```

4. 官方模型最大输出为 384,000 Token；本项目不使用该上限，初始阶段使用更小的任务级输出预算。
5. 当前 SiliconFlow 路由是否原样透传 `thinking.type=disabled`，必须通过一次最小探针确认；不尝试 `enable_thinking` 等替代参数。
6. Tokenizer 或模型配置缺失时拒绝规划；不使用字符数、字节数或经验比例替代 Token 计数。
7. 不修改 MD→Card 的制卡规则。章节拆分只读取已经存在的 `structure/structure.json` 和 Card 来源行号。
8. 章节结构不能形成无重叠、全覆盖分区时，超预算论文直接规划失败，不退回按 Card 顺序贪心切分。
9. 证据抽取采用 Non-think；论文综合也先采用 Non-think。是否为论文综合启用 Think High，留到 v3 质量对照完成后另立目标，不在本计划引入双路径。

## 预算定义

新增固定模型配置 `siliconflow-deepseek-v4-pro`，初始值如下：

```json
{
  "schema_version": "llm.model_profile.v1",
  "profile_id": "siliconflow-deepseek-v4-pro",
  "provider": "openai-compatible",
  "request_model": "deepseek-ai/DeepSeek-V4-Pro",
  "tokenizer_repo": "deepseek-ai/DeepSeek-V4-Pro",
  "tokenizer_revision": "0e1a0e5e52aea73055f50fef6f2423db370265b6",
  "context_window_tokens": 1000000,
  "model_max_output_tokens": 384000,
  "safety_margin_tokens": 10000,
  "evidence_output_base_tokens": 2048,
  "evidence_output_tokens_per_card": 1024,
  "evidence_max_output_tokens": 65536,
  "section_summary_max_output_tokens": 32768,
  "paper_synthesis_max_output_tokens": 32768,
  "thinking": {"type": "disabled"}
}
```

证据抽取批次的计划输出量为：

```python
planned_output_tokens = 2048 + 1024 * len(projected_cards)
```

只有同时满足以下条件才允许形成批次：

```python
planned_output_tokens <= profile.evidence_max_output_tokens
input_tokens + planned_output_tokens + profile.safety_margin_tokens <= profile.context_window_tokens
```

`planned_output_tokens` 同时作为 API `max_tokens`。这个规则保证十几至二十几张 Card 的论文可以整篇单批；82张和250张 Card 的论文会因为输出预算进入章节规划。若实际模型仍因输出过长返回 `finish_reason != "stop"`，该批次失败并暴露，不能自动缩批重试。

## 文件职责

**新增文件**

- `shipping/config/models/siliconflow-deepseek-v4-pro.json`：唯一的生产模型预算与 tokenizer 配置。
- `shipping/src/shipping_pipeline/llm_model_profile.py`：读取和严格校验模型配置。
- `shipping/src/shipping_pipeline/llm_tokenizer.py`：加载固定 tokenizer/encoder、编码消息、计算 Token。
- `shipping/src/shipping_pipeline/llm_projection.py`：将完整 Card 投影为模型输入 Card。
- `shipping/src/shipping_pipeline/llm_sections.py`：从现有结构产物生成可验证章节分区。
- `shipping/src/shipping_pipeline/llm_planner.py`：完成整篇优先、章节拆分和综合层级规划。
- `shipping/scripts/setup_llm_tokenizer.py`：一次性下载固定 revision 的 tokenizer 和官方 encoding 文件。
- `shipping/scripts/validate_llm_plan_corpus.py`：对全部真实 Card 做只读规划验收。
- `shipping/tests/test_llm_model_profile.py`
- `shipping/tests/test_llm_tokenizer.py`
- `shipping/tests/test_llm_projection.py`
- `shipping/tests/test_llm_sections.py`
- `shipping/tests/test_llm_planner.py`

**修改文件**

- `shipping/src/shipping_pipeline/llm_contracts.py`：升级证据、章节摘要和论文综合 Schema，并生成稳定证据身份。
- `shipping/src/shipping_pipeline/llm_provider.py`：加入 Non-think、`max_tokens`、`finish_reason` 和推理输出检查。
- `shipping/src/shipping_pipeline/llm_analysis.py`：使用新规划器执行证据批次和分层综合。
- `shipping/src/shipping_pipeline/llm_review.py`：展示 Token、章节和未采用证据，按稳定身份迁移审核决定。
- `shipping/main.py`：删除字符预算参数，新增模型配置和供应商探针命令。
- `shipping/tests/test_llm_contracts.py`
- `shipping/tests/test_llm_analysis.py`
- `shipping/tests/test_llm_review.py`
- `shipping/requirements.txt`
- `shipping/.env.example`
- `shipping/README.md`
- `shipping/docs/progress-20260709.md`
- `.gitignore`

---

### Task 1: 固定模型配置和官方 Tokenizer 资产

**Files:**
- Create: `shipping/config/models/siliconflow-deepseek-v4-pro.json`
- Create: `shipping/src/shipping_pipeline/llm_model_profile.py`
- Create: `shipping/src/shipping_pipeline/llm_tokenizer.py`
- Create: `shipping/scripts/setup_llm_tokenizer.py`
- Create: `shipping/tests/test_llm_model_profile.py`
- Create: `shipping/tests/test_llm_tokenizer.py`
- Modify: `shipping/requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: 先写模型配置失败测试**

覆盖以下行为：未知字段、空模型名、非正整数预算、任务输出上限超过供应商上限、`safety_margin_tokens >= context_window_tokens`、tokenizer revision 不是40位十六进制时全部拒绝。

```python
def test_model_profile_rejects_invalid_budget(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        payload = valid_profile_payload()
        payload["evidence_max_output_tokens"] = 400001
        path = Path(directory) / "profile.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ModelProfileError, "model_max_output_tokens"):
            load_model_profile(path)
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest shipping.tests.test_llm_model_profile -v
```

Expected: `ImportError` 或缺少 `load_model_profile`。

- [ ] **Step 3: 实现严格模型配置对象**

核心接口固定为：

```python
@dataclass(frozen=True)
class ModelProfile:
    schema_version: str
    profile_id: str
    provider: str
    request_model: str
    tokenizer_repo: str
    tokenizer_revision: str
    context_window_tokens: int
    model_max_output_tokens: int
    safety_margin_tokens: int
    evidence_output_base_tokens: int
    evidence_output_tokens_per_card: int
    evidence_max_output_tokens: int
    section_summary_max_output_tokens: int
    paper_synthesis_max_output_tokens: int
    thinking: dict[str, str]


def load_model_profile(path: Path) -> ModelProfile:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = {field.name for field in dataclasses.fields(ModelProfile)}
    if set(payload) != expected:
        raise ModelProfileError("模型配置字段必须与 llm.model_profile.v1 完全一致。")
    profile = ModelProfile(**payload)
    integer_fields = (
        "context_window_tokens",
        "model_max_output_tokens",
        "safety_margin_tokens",
        "evidence_output_base_tokens",
        "evidence_output_tokens_per_card",
        "evidence_max_output_tokens",
        "section_summary_max_output_tokens",
        "paper_synthesis_max_output_tokens",
    )
    if any(getattr(profile, name) < 1 for name in integer_fields):
        raise ModelProfileError("所有 Token 预算必须是正整数。")
    if profile.evidence_max_output_tokens > profile.model_max_output_tokens:
        raise ModelProfileError("evidence_max_output_tokens 超过 model_max_output_tokens。")
    if profile.section_summary_max_output_tokens > profile.model_max_output_tokens:
        raise ModelProfileError("section_summary_max_output_tokens 超过 model_max_output_tokens。")
    if profile.paper_synthesis_max_output_tokens > profile.model_max_output_tokens:
        raise ModelProfileError("paper_synthesis_max_output_tokens 超过 model_max_output_tokens。")
    if profile.safety_margin_tokens >= profile.context_window_tokens:
        raise ModelProfileError("safety_margin_tokens 必须小于 context_window_tokens。")
    if not re.fullmatch(r"[0-9a-f]{40}", profile.tokenizer_revision):
        raise ModelProfileError("tokenizer_revision 必须是40位小写十六进制 revision。")
    if profile.thinking != {"type": "disabled"}:
        raise ModelProfileError("当前 profile 只允许 Non-think。")
    return profile
```

配置文件必须使用本计划“预算定义”中的完整 JSON；禁止从环境变量覆盖预算值，避免同一 profile 在不同运行中含义漂移。

- [ ] **Step 4: 加入 tokenizer 依赖和本地缓存目录**

在 `shipping/requirements.txt` 增加：

```text
tokenizers==0.23.1
```

在根 `.gitignore` 增加：

```text
shipping/.model_cache/
```

- [ ] **Step 5: 实现一次性 tokenizer 安装脚本**

脚本只下载固定 revision 的以下文件：

```text
tokenizer.json
tokenizer_config.json
encoding/encoding_dsv4.py
encoding/README.md
encoding/tests/test_input_1.json
encoding/tests/test_input_2.json
encoding/tests/test_input_3.json
encoding/tests/test_input_4.json
encoding/tests/test_output_1.txt
encoding/tests/test_output_2.txt
encoding/tests/test_output_3.txt
encoding/tests/test_output_4.txt
LICENSE
```

目标目录固定为：

```text
shipping/.model_cache/deepseek-v4-pro/0e1a0e5e52aea73055f50fef6f2423db370265b6/
```

脚本通过 `https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/resolve/<revision>/<path>` 下载；下载完成后生成 `manifest.json`，记录每个文件的 SHA-256。任何文件下载失败都删除该次临时目录并返回非零，不保留半安装状态。

- [ ] **Step 6: 实现 TokenCounter**

```python
class TokenizerUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenCount:
    prompt_tokens: int
    encoded_prompt_sha256: str


class DeepSeekV4TokenCounter:
    def __init__(self, profile: ModelProfile, cache_root: Path) -> None:
        self.profile = profile
        self.asset_dir = cache_root / "deepseek-v4-pro" / profile.tokenizer_revision
        manifest_path = self.asset_dir / "manifest.json"
        if not manifest_path.is_file():
            raise TokenizerUnavailableError("缺少 tokenizer manifest。")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for relative_path, expected_sha256 in manifest["files"].items():
            asset_path = self.asset_dir / relative_path
            if not asset_path.is_file() or sha256_file(asset_path) != expected_sha256:
                raise TokenizerUnavailableError(f"tokenizer 资产缺失或哈希不符：{relative_path}")
        self._tokenizer = Tokenizer.from_file(str(self.asset_dir / "tokenizer.json"))
        self._encoder = load_pinned_encoder(self.asset_dir / "encoding" / "encoding_dsv4.py")

    def count_messages(self, messages: list[dict[str, str]]) -> TokenCount:
        prompt = self._encoder.encode_messages(messages, thinking_mode="chat")
        token_ids = self._tokenizer.encode(prompt, add_special_tokens=False).ids
        return TokenCount(
            prompt_tokens=len(token_ids),
            encoded_prompt_sha256=sha256_text(prompt),
        )

    def count_text(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pinned_encoder(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("shipping_deepseek_v4_encoding", path)
    if spec is None or spec.loader is None:
        raise TokenizerUnavailableError("无法加载固定版本的 DeepSeek-V4 encoding。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

`count_messages` 必须使用固定 revision 中的官方 `encode_messages(messages, thinking_mode="chat")`，不能只把 system/user 字符串分别 tokenize。官方 encoding 将 `chat` 定义为 Non-think。启动时校验 `manifest.json` 内全部 SHA-256；缺文件或哈希变化直接抛出 `TokenizerUnavailableError`。

- [ ] **Step 7: 使用官方 encoding 样例做一致性测试**

将官方4组输入交给 `count_messages` 使用的编码路径，并将生成的完整 Prompt 字符串与官方 `test_output_*.txt` 逐字比较；其中第4组明确使用 `thinking_mode="chat"` 验证 Non-think。随后使用固定 `tokenizer.json` 编码该字符串并断言 Token 数为正。测试还应证明缺缓存、错误 revision 和被修改的 tokenizer 文件都会失败。

- [ ] **Step 8: 安装依赖、下载 tokenizer 并运行测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r shipping\requirements.txt
.\.venv\Scripts\python.exe shipping\scripts\setup_llm_tokenizer.py --profile shipping\config\models\siliconflow-deepseek-v4-pro.json
.\.venv\Scripts\python.exe -m unittest shipping.tests.test_llm_model_profile shipping.tests.test_llm_tokenizer -v
```

Expected: 官方4组编码样例全部一致；缓存清单完整；测试通过。

---

### Task 2: 建立精简 Card 投影和有界证据 Schema

**Files:**
- Create: `shipping/src/shipping_pipeline/llm_projection.py`
- Create: `shipping/tests/test_llm_projection.py`
- Modify: `shipping/src/shipping_pipeline/llm_contracts.py`
- Modify: `shipping/tests/test_llm_contracts.py`

- [ ] **Step 1: 写 Card 投影测试**

```python
def test_project_card_only_exposes_model_fields() -> None:
    projected = project_card(full_material())
    assert set(projected) == {
        "material_id",
        "content_kind",
        "heading_path",
        "title",
        "extract",
        "parse_flags",
    }
    assert projected["parse_flags"] == ["parse.a", "quality.b"]
    assert "source_span" not in projected
    assert "source_fingerprint" not in projected
    assert "summary" not in projected
    assert "lead_excerpt" not in projected
```

还要覆盖：flags 去重且保序、`extract` 空值拒绝、`material_id` 重复拒绝、输入 Card 顺序保持不变。

- [ ] **Step 2: 实现不可扩展的模型输入投影**

```python
MODEL_CARD_FIELDS = (
    "material_id",
    "content_kind",
    "heading_path",
    "title",
    "extract",
    "parse_flags",
)


def project_card(material: dict[str, Any]) -> dict[str, Any]:
    return {
        "material_id": material["material_id"],
        "content_kind": material["content_kind"],
        "heading_path": list(material.get("heading_path", [])),
        "title": material.get("clean_title") or material.get("raw_title") or material["paper_title"],
        "extract": material["extract"],
        "parse_flags": _unique([*material.get("confidence_flags", []), *material.get("quality_flags", [])]),
    }
```

投影结果写入运行目录，完整 Card 仍保存在 `input/materials.jsonl`，后续逐字引文和来源回填只能读取完整 Card。

- [ ] **Step 3: 将证据 Schema 升级到 `llm.evidence_batch.v2`**

每张 Card 仍必须有且只有一个判定，但删除自由文本 `reason`，替换成枚举 `reason_code`：

```python
ASSESSMENT_REASON_CODES = [
    "direct_evidence",
    "context_only",
    "off_topic",
    "parse_damage",
    "empty_or_nontext",
    "duplicate_content",
]
```

字段边界固定为：

```text
claim.maxLength = 320
relevance.maxLength = 240
citations.maxItems = 4
quote.maxLength = 600
caveats.maxItems = 4
caveat.maxLength = 240
evidence_units.maxItems = max(1, 2 * batch_card_count)
```

通过函数生成每个批次的动态 Schema：

```python
def build_evidence_batch_schema(batch_card_count: int) -> dict[str, Any]:
    if batch_card_count < 1:
        raise ValueError("batch_card_count 必须 >= 1。")
    schema = copy.deepcopy(EVIDENCE_BATCH_SCHEMA_TEMPLATE)
    schema["properties"]["material_assessments"]["minItems"] = batch_card_count
    schema["properties"]["material_assessments"]["maxItems"] = batch_card_count
    schema["properties"]["evidence_units"]["maxItems"] = max(1, 2 * batch_card_count)
    return schema
```

- [ ] **Step 4: 加入判定字段交叉校验**

规则如下：

```text
disposition=evidence       -> reason_code=direct_evidence，且至少被一个 evidence unit 引用
disposition=context        -> reason_code=context_only
disposition=not_relevant   -> reason_code=off_topic
disposition=unusable       -> reason_code 属于 parse_damage/empty_or_nontext/duplicate_content
```

继续保留现有逐字 quote 子串验证和批次 Card 全覆盖验证。

- [ ] **Step 5: 运行投影和契约测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest shipping.tests.test_llm_projection shipping.tests.test_llm_contracts -v
```

Expected: 所有测试通过；测试明确证明完整 Card 的路径、generation、fingerprint、summary、lead_excerpt 不进入 Prompt。

---

### Task 3: 接入 Non-think、输出上限和结束原因验证

**Files:**
- Modify: `shipping/src/shipping_pipeline/llm_provider.py`
- Modify: `shipping/tests/test_llm_analysis.py`
- Modify: `shipping/main.py`

- [ ] **Step 1: 写请求和响应失败测试**

覆盖：

```python
request = build_chat_request(
    model="deepseek-ai/DeepSeek-V4-Pro",
    system_prompt="system",
    user_prompt="user",
    max_tokens=8192,
    thinking={"type": "disabled"},
)
assert request["thinking"] == {"type": "disabled"}
assert request["max_tokens"] == 8192
```

响应测试覆盖：`finish_reason=stop` 成功；`length`、`content_filter`、空值、多 choice 全部失败；Non-think 响应出现非空 `reasoning_content` 时失败。

- [ ] **Step 2: 扩展 ProviderResult**

```python
@dataclass(frozen=True)
class ProviderResult:
    raw_body: bytes
    parsed_response: dict[str, Any] | None
    response_id: str | None
    response_model: str | None
    system_fingerprint: str | None
    finish_reason: str | None
    reasoning_content_chars: int
    usage: dict[str, int | None]
```

新增：

```python
def validate_completed_response(result: ProviderResult, *, thinking_disabled: bool) -> None:
    if result.finish_reason != "stop":
        raise ProviderCallError(f"provider.finish_reason.{result.finish_reason or 'missing'}")
    if thinking_disabled and result.reasoning_content_chars:
        raise ProviderCallError("provider.non_think_returned_reasoning")
```

- [ ] **Step 3: 修改请求构造**

`build_chat_request` 必须显式写入 `max_tokens` 和 `thinking`。删除固定 `temperature=0`，避免把与任务无关的采样参数混入供应商能力判断。

- [ ] **Step 4: 新增 `llm-provider-probe` 命令**

命令参数：

```text
--workspace
--model-profile
--api-url
--api-key-env
--timeout
```

探针只发送一个固定 JSON 任务，`max_tokens=65536`、Non-think，要求立即返回 `{"ok": true}`。这既验证 Non-think，也验证生产证据批次使用的最大 `max_tokens` 值能被当前 SiliconFlow 路由接受；实际只按生成 Token 记录 usage。结果写入：

```text
workspace/_llm_analysis/provider_probes/<UTC时间>/
  request.json
  raw_response.json
  result.json
```

`result.json` 必须记录：profile hash、模型、`finish_reason`、reasoning字符数、本地计划 Token、供应商实际 prompt Token、差值和 usage。探针失败返回非零，不尝试其他参数。

- [ ] **Step 5: 运行纯本地测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest shipping.tests.test_llm_analysis -v
```

Expected: fake transport 能证明请求包含 Non-think 和 `max_tokens`，所有非 `stop` 响应被记录为失败。

---

### Task 4: 从现有结构产物生成可验证章节分区

**Files:**
- Create: `shipping/src/shipping_pipeline/llm_sections.py`
- Create: `shipping/tests/test_llm_sections.py`

- [ ] **Step 1: 为两篇大论文写真实结构测试**

测试读取：

```text
shipping/workspace/长江上游地区产业布局及航运适应性研究/structure/structure.json
shipping/workspace/考虑翻坝和天气的长江班轮运网鲁棒优化模型/structure/structure.json
```

断言每张 Card 恰好属于一个章节，章节范围按原文行号递增且互不重叠，章节标题包含明确的“第…章”。

- [ ] **Step 2: 定义章节数据结构**

```python
@dataclass(frozen=True)
class AnalysisSection:
    section_id: str
    title: str
    level: int
    start_line: int
    end_line: int
    parent_section_id: str | None
    material_ids: tuple[str, ...]


@dataclass(frozen=True)
class SectionMap:
    paper_id: str
    generation_id: str
    sections: tuple[AnalysisSection, ...]
    material_to_section: dict[str, str]
    source_sha256: str
```

- [ ] **Step 3: 实现显式章节锚点识别**

只接受以下一级章节形式：

```text
第1章 / 第一章 / 第 1 章
Chapter 1
```

摘要和英文摘要分别形成 `front:abstract`、`front:abstract_en`。参考文献、附录等后置部分形成 `back:<规范标题>`。不得把普通 `1. 重庆`、`(1) 数据来源` 或 `Step 1` 识别为一级章节。

- [ ] **Step 4: 实现二级、三级章节解析**

一级章节内部只接受与当前章号一致的 `ordinal_path`：例如第二章中的 `[2, 3]` 和 `[2, 3, 1]`。标题深度与 ordinal path 冲突、同级编号倒退、范围重叠时抛出：

```text
planning.section_structure_unreliable
```

- [ ] **Step 5: 将 Card 按来源行号映射到最深章节**

使用完整 Card 的 `source_span.start_line/end_line`。Card 横跨两个章节边界、没有来源行号、落在所有章节之外时直接失败。完成后验证：

```python
sorted(section_map.material_to_section) == sorted(all_material_ids)
```

- [ ] **Step 6: 增加结构不足反例**

构造只有平级杂乱标题、没有显式章节锚点的超预算论文，断言失败而不是顺序切批。构造一个无需拆批的小论文，断言规划器不读取它的结构文件。

- [ ] **Step 7: 运行章节测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest shipping.tests.test_llm_sections -v
```

Expected: 两篇真实大论文形成无重叠、全覆盖章节图；所有反例按指定代码失败。

---

### Task 5: 实现整篇优先的 Token 感知规划器

**Files:**
- Create: `shipping/src/shipping_pipeline/llm_planner.py`
- Create: `shipping/tests/test_llm_planner.py`
- Modify: `shipping/src/shipping_pipeline/llm_analysis.py`

- [ ] **Step 1: 写整篇单批测试**

为3张、19张和23张 Card 的论文构造真实投影，断言：

```python
assert plan.scope == "full_paper"
assert plan.strategy == "single_evidence_batch"
assert len(plan.evidence_batches) == 1
assert plan.evidence_batches[0].material_ids == tuple(all_material_ids)
```

- [ ] **Step 2: 写大论文章节拆分测试**

82张和250张 Card 的论文必须进入 `section_evidence_batches`。每张 Card 恰好出现一次；批次不能跨一级章节；每批记录触发原因。

- [ ] **Step 3: 定义规划对象**

```python
@dataclass(frozen=True)
class PlannedRequest:
    request_id: str
    stage: str
    section_ids: tuple[str, ...]
    material_ids: tuple[str, ...]
    input_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int
    split_reason: str
    prompt_sha256: str


@dataclass(frozen=True)
class PaperAnalysisPlan:
    schema_version: str
    paper_id: str
    generation_id: str
    model_profile_id: str
    model_profile_sha256: str
    tokenizer_revision: str
    strategy: str
    evidence_batches: tuple[PlannedRequest, ...]
    synthesis_strategy: str
    input_material_ids: tuple[str, ...]
    plan_sha256: str
```

- [ ] **Step 4: 实现规划顺序**

严格执行：

```text
整篇投影
→ 满足输入、输出和上下文预算：整篇单批
→ 不满足：生成章节图
→ 按一级章节
→ 一级章节仍超预算：按二级章节
→ 二级章节仍超预算：按三级章节
→ 三级章节仍超预算：仅在该三级章节内部按 Card 边界装箱
→ 单 Card 仍超预算：失败
```

最后一级 Card 装箱也必须使用 Token 预算，且不能跨当前最深章节。它不是通用兜底，只是规范中明确允许的最小单位拆分。

- [ ] **Step 5: 生成稳定批次请求 ID**

批次 ID 不使用顺序号，使用：

```python
request_id = "evidence_" + sha256_json({
    "paper_generation": generation_id,
    "stage": "evidence",
    "material_ids": material_ids,
    "prompt_sha256": prompt_sha256,
})[:16]
```

同一输入和同一 Prompt 重跑得到相同 ID；改变拆批结果会得到不同 ID。

- [ ] **Step 6: 写预算边界测试**

覆盖恰好等于上限、超过1 Token、单 Card 超限、输出预算超过上限、上下文总和超过上限、tokenizer 不可用、章节结构不可用。所有失败均带明确代码，不产生部分 plan。

- [ ] **Step 7: 运行规划器测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest shipping.tests.test_llm_planner -v
```

Expected: 小中型论文单批；两篇大论文只在论文内部按章节拆分；无字符预算字段。

---

### Task 6: 建立与批次无关的证据锚点和观点版本

**Files:**
- Modify: `shipping/src/shipping_pipeline/llm_contracts.py`
- Modify: `shipping/tests/test_llm_contracts.py`

- [ ] **Step 1: 写证据身份稳定性测试**

同一组 citations 和 evidence type 在不同 `request_id` 中必须得到同一个 `evidence_anchor_id`；相同锚点但不同 claim 必须得到不同 `evidence_unit_id`。

- [ ] **Step 2: 实现双层身份**

```python
def build_evidence_anchor_id(
    generation_id: str,
    evidence_type: str,
    citations: list[dict[str, str]],
) -> str:
    canonical = {
        "generation_id": generation_id,
        "evidence_type": evidence_type,
        "citations": sorted(
            ({"material_id": row["material_id"], "quote": row["quote"]} for row in citations),
            key=lambda row: (row["material_id"], row["quote"]),
        ),
    }
    return "anchor_" + sha256_json(canonical)[:24]


def build_evidence_unit_id(anchor_id: str, claim: str) -> str:
    normalized_claim = " ".join(claim.split())
    return "evidence_" + sha256_text(anchor_id + "\n" + normalized_claim)[:24]
```

`request_id`、batch顺序和模型响应顺序都不能进入上述哈希。

- [ ] **Step 3: 明确重复和碰撞行为**

同一响应出现重复 `evidence_unit_id` 时契约失败。两个响应产生相同 anchor、不同 claim 时都保留，但在综合提示中标记为同源观点；不能静默合并。

- [ ] **Step 4: 运行身份测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest shipping.tests.test_llm_contracts -v
```

Expected: 调整批次预算不改变相同证据的 anchor；观点文本变化会产生新 unit ID。

---

### Task 7: 用新计划执行证据抽取并核对实际 Token

**Files:**
- Modify: `shipping/src/shipping_pipeline/llm_analysis.py`
- Modify: `shipping/tests/test_llm_analysis.py`

- [ ] **Step 1: 写新运行目录测试**

每次运行必须包含：

```text
input/materials.jsonl
input/projected_cards.jsonl
input/paper.json
input/section_map.json（仅章节策略）
plan/model_profile.json
plan/analysis_plan.json
plan/tokenizer_manifest.json
batches/<request_id>/request.json
batches/<request_id>/raw_response.json
batches/<request_id>/validated_output.json
batches/<request_id>/result.json
coverage.json
manifest.json
```

- [ ] **Step 2: 将 LLMAnalysisRunner 改为先完整规划再执行**

`run()` 在创建任何批次目录前完成：输入 generation 校验、Card 投影、模型配置加载、tokenizer 校验和完整 plan。规划失败只生成 run manifest、failure ledger 和能够重现失败的输入快照，不执行 API。

- [ ] **Step 3: 执行计划中的精确请求**

每批请求使用计划中的 `max_output_tokens`，并包含：

```json
{
  "thinking": {"type": "disabled"},
  "max_tokens": 32768
}
```

示例中的32768由该批计划决定，不写死在 provider。

- [ ] **Step 4: 比较计划 Token 与供应商实际 Token**

批次结果新增：

```json
{
  "planned_input_tokens": 1234,
  "actual_prompt_tokens": 1234,
  "prompt_token_delta": 0,
  "planned_max_output_tokens": 8192,
  "actual_completion_tokens": 2100,
  "finish_reason": "stop",
  "reasoning_content_chars": 0
}
```

实际 prompt Token 与计划值不相等时，该批标记 `provider.prompt_token_mismatch` 并阻止发布。任何 `finish_reason != stop` 或 reasoning 非空直接失败。

- [ ] **Step 5: 保持现有失败隔离和显式重跑规则**

继续执行：一个批次失败不阻止其他已规划证据批次执行；任一证据批次失败都阻止综合；不自动重试。显式重跑只有在父运行的 `plan_sha256`、profile hash、tokenizer revision、generation和Prompt hash全部相同时才能复用成功批次。

- [ ] **Step 6: 运行执行层测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest shipping.tests.test_llm_analysis -v
```

Expected: fake provider 覆盖成功、Token不匹配、reasoning意外出现、length截断、中间批失败、显式重跑和source generation变化。

---

### Task 8: 为综合阶段增加预算和章节层级

**Files:**
- Modify: `shipping/src/shipping_pipeline/llm_contracts.py`
- Modify: `shipping/src/shipping_pipeline/llm_planner.py`
- Modify: `shipping/src/shipping_pipeline/llm_analysis.py`
- Modify: `shipping/tests/test_llm_contracts.py`
- Modify: `shipping/tests/test_llm_planner.py`
- Modify: `shipping/tests/test_llm_analysis.py`

- [ ] **Step 1: 定义章节摘要 Schema**

`llm.section_summary.v1` 输出：

```json
{
  "schema_version": "llm.section_summary.v1",
  "section_id": "section_0004",
  "section_focus": "三峡船闸通过能力与积压成因",
  "claims": [
    {
      "statement": "船闸能力约束会延长船舶等待时间。",
      "evidence_unit_ids": ["evidence_0123456789abcdef01234567"]
    }
  ],
  "evidence_dispositions": [
    {
      "evidence_unit_id": "evidence_0123456789abcdef01234567",
      "disposition": "used|redundant|peripheral|excluded",
      "reason_code": "supports_claim|duplicate_support|background_only|low_confidence|off_topic"
    }
  ]
}
```

每个输入 evidence ID 必须且只能出现一次。`used` 的 evidence ID 必须被至少一个 claim 引用，其他状态不能被引用。

- [ ] **Step 2: 为论文综合 Schema 增加证据处置**

`llm.paper_analysis.v2` 保留研究焦点、研究类型、方法、核心发现、局限、综述用途和未解决问题，同时增加：

```json
"evidence_dispositions": [
  {
    "evidence_unit_id": "evidence_0123456789abcdef01234567",
    "disposition": "used|redundant|peripheral|excluded",
    "reason_code": "supports_claim|duplicate_support|background_only|low_confidence|off_topic"
  }
]
```

论文综合必须覆盖传入综合阶段的每个 evidence ID。

- [ ] **Step 3: 实现直接综合预算**

完整 evidence units 构造的论文综合 Prompt 满足：

```python
input_tokens + profile.paper_synthesis_max_output_tokens + profile.safety_margin_tokens \
    <= profile.context_window_tokens
```

则直接综合，不生成章节摘要。

- [ ] **Step 4: 实现章节综合预算**

直接综合不满足预算时，按 `SectionMap` 聚合 evidence units。先对最深章节生成摘要，再向父章节合并；父章节 Prompt 只包含子章节摘要和仍直接属于父章节的 evidence units。任一单章节摘要请求超预算时规划失败，不跳过该章节。

证据单元引用多个章节时，系统根据其 citations 对应 Card 的章节集合求章节树中的最近公共父节点，并只把该证据分配给该父节点；同一 evidence unit 不得进入两个平行章节摘要。找不到唯一公共父节点时以论文根节点承载，并在章节审计中标记 `section.cross_section_evidence`。

- [ ] **Step 5: 实现论文级传递引用校验**

最终论文综合读取章节摘要，每个最终 statement 仍引用原始 `evidence_unit_id`。系统验证引用 ID 能沿章节摘要追溯到真实证据；章节摘要不得创造新 evidence ID。

- [ ] **Step 6: 生成三类覆盖集合**

```text
all_evidence_unit_ids
used_in_final_analysis_ids
not_used_in_final_analysis_ids
```

并验证：

```python
all_ids == used_ids | not_used_ids
assert not (used_ids & not_used_ids)
```

`unresolved_evidence_unit_ids` 只在处置缺失时出现；非空即运行失败。未被最终采用但已明确处置的证据列入审计，不视为运行失败。

- [ ] **Step 7: 运行综合测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest shipping.tests.test_llm_contracts shipping.tests.test_llm_planner shipping.tests.test_llm_analysis -v
```

Expected: 直接综合和章节综合均通过；漏掉、重复、伪造 evidence ID 均失败。

---

### Task 9: 更新中文人工审核和审核决定迁移

**Files:**
- Modify: `shipping/src/shipping_pipeline/llm_review.py`
- Modify: `shipping/tests/test_llm_review.py`

- [ ] **Step 1: 扩展中文审核报告测试**

报告必须展示：模型profile、tokenizer revision、Non-think状态、批次拆分原因、计划/实际Token、章节路径、证据anchor、最终是否采用和未采用原因。

- [ ] **Step 2: 更新审核 CSV**

固定列：

```text
evidence_unit_id,evidence_anchor_id,决策,修改后观点,备注
```

人工可填写的决策仍为：`待审核/接受/拒绝/修改`。导入时验证 unit ID 和 anchor ID 对应同一个运行产物。

- [ ] **Step 3: 实现决定迁移规则**

```python
def migrate_review_decisions(previous_state: dict[str, Any], current_units: list[dict[str, Any]]) -> dict[str, Any]:
    previous_by_unit = {row["evidence_unit_id"]: row for row in previous_state["decisions"]}
    previous_anchor_ids = {row["evidence_anchor_id"] for row in previous_state["decisions"]}
    migrated = []
    for unit in current_units:
        old = previous_by_unit.get(unit["evidence_unit_id"])
        if old is not None:
            migrated.append({**old, "migration": "exact_unit"})
        elif unit["evidence_anchor_id"] in previous_anchor_ids:
            migrated.append({
                "evidence_unit_id": unit["evidence_unit_id"],
                "evidence_anchor_id": unit["evidence_anchor_id"],
                "决策": "待审核",
                "修改后观点": "",
                "备注": "来源相同但观点变化",
                "migration": "anchor_only",
            })
    return {"schema_version": "llm.review_decisions.v2", "decisions": migrated}
```

规则：

```text
evidence_unit_id 完全相同 -> 继承决定
anchor相同但unit不同     -> 新结果为“待审核”，备注“来源相同但观点变化”
anchor不存在             -> 不迁移
```

禁止仅凭 material_id 继承审核结论。

- [ ] **Step 4: 运行审核测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest shipping.tests.test_llm_review -v
```

Expected: 中文报告可定位全部证据；迁移规则对同unit、同anchor异unit和新anchor分别正确。

---

### Task 10: 切换 CLI、全语料规划验收和真实 API 验证

**Files:**
- Create: `shipping/scripts/validate_llm_plan_corpus.py`
- Modify: `shipping/main.py`
- Modify: `shipping/.env.example`
- Modify: `shipping/README.md`
- Modify: `shipping/docs/progress-20260709.md`

- [ ] **Step 1: 删除字符预算接口**

从 CLI、环境变量、manifest和README删除：

```text
--max-prompt-chars
LLM_ANALYSIS_MAX_PROMPT_CHARS
max_prompt_chars
prompt_chars
```

同时删除 `--model` 和 `LLM_ANALYSIS_MODEL`；请求模型只能来自已校验 profile。保留 `--provider` 时必须与 profile 中的 provider 完全相同，否则在规划前失败。

`llm-analysis` 新增必需参数：

```text
--model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json
```

运行产物使用 `input_tokens`、`max_output_tokens` 和 `context_total_reserved_tokens`。

- [ ] **Step 2: 实现全语料只读规划脚本**

脚本读取当前 corpus 16篇、490张 Card，只生成内存 plan 和一份汇总报告，不调用 API。报告字段至少包括：

```text
paper_id
card_count
strategy
evidence_batch_count
section_count
largest_input_tokens
largest_max_output_tokens
unplanned_material_ids
multiply_planned_material_ids
planning_failure_code
```

- [ ] **Step 3: 运行全语料 dry-run**

Run:

```powershell
.\.venv\Scripts\python.exe shipping\scripts\validate_llm_plan_corpus.py --workspace shipping\workspace --model-profile shipping\config\models\siliconflow-deepseek-v4-pro.json
```

Acceptance:

```text
16篇论文全部单独规划
490张 Card 每张恰好进入一个证据批次
不存在跨论文批次
十几至23张 Card 的论文为整篇单批
82张和250张 Card 的论文进入章节策略
每个请求满足 input + max_output + safety <= 1,000,000
报告中不存在字符预算字段
```

若真实章节结构导致其中任一验收项不成立，保留失败报告并修改规划器规则；不得通过提高预算或跳过 Card 使验收通过。

- [ ] **Step 4: 运行完整本地回归**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s shipping\tests -v
.\.venv\Scripts\python.exe -m compileall -q shipping\src shipping\main.py shipping\scripts
.\.venv\Scripts\python.exe shipping\scripts\validate_md_card_corpus.py --workspace shipping\workspace
.\.venv\Scripts\python.exe -m pip check
```

Expected: 全部测试通过；MD→Card三层覆盖结论不变；依赖完整。

- [ ] **Step 5: 执行一次真实 Non-think 能力探针**

Run:

```powershell
.\.venv\Scripts\python.exe shipping\main.py llm-provider-probe --workspace shipping\workspace --model-profile shipping\config\models\siliconflow-deepseek-v4-pro.json --timeout 300
```

Acceptance:

```text
finish_reason=stop
reasoning_content_chars=0
响应JSON有效
本地Token等于供应商实际prompt_tokens
密钥未进入任何运行文件
```

- [ ] **Step 6: 对3张 Card 小论文做真实完整运行**

Run:

```powershell
.\.venv\Scripts\python.exe shipping\main.py llm-analysis --workspace shipping\workspace --topic "三峡船舶积压与长江航运组织" --run-id llm-v3-nonthink-small --provider openai-compatible --paper-id "三峡航运遇瓶颈" --model-profile shipping\config\models\siliconflow-deepseek-v4-pro.json --timeout 900
```

Acceptance: 单证据批次、完整论文综合、Card覆盖率1.0、`finish_reason=stop`、reasoning为空、无未解决证据ID。

- [ ] **Step 7: 对19张 Card 中型论文做真实单批运行**

Run:

```powershell
.\.venv\Scripts\python.exe shipping\main.py llm-analysis --workspace shipping\workspace --topic "三峡船舶积压与长江航运组织" --run-id llm-v3-nonthink-medium --provider openai-compatible --paper-id "三峡坝区船舶积压对策探讨" --model-profile shipping\config\models\siliconflow-deepseek-v4-pro.json --timeout 1800
```

Acceptance: 19张 Card 一次证据抽取；所有 Card 有判定；综合完成；审计报告列出实际输入、输出Token和最终未采用证据。

- [ ] **Step 8: 只对82张和250张论文做章节 dry-run，不调用 API**

检查章节边界、批次 Token 和综合层级后生成中文计划报告。未完成人工检查前，不对这两篇论文发起真实调用。

- [ ] **Step 9: 更新进度记录**

在 `progress-20260709.md` 记录：代码版本、模型profile hash、tokenizer revision、全语料规划统计、探针结果、两次真实运行的Token/耗时/失败代码，以及未执行的大论文真实调用。README只保留 v3 命令，不继续展示字符预算 v2 命令。

---

## 最终验收标准

1. 调度边界始终是一篇论文，代码和审计中均不存在跨论文批次。
2. 所有 Prompt 使用精简 Card 投影；完整 Card 只在代码侧用于验证和来源回填。
3. 所有规划使用固定 revision 的 DeepSeek-V4-Pro 官方编码器；Tokenizer 不可用时拒绝运行。
4. 所有请求显式使用 `thinking.type=disabled` 和 `max_tokens`。
5. `finish_reason != stop`、Non-think返回reasoning、计划Token小于实际Token均导致当前运行不可发布。
6. 小中型论文整篇单批；大论文只在论文内部按可验证章节拆分。
7. 综合阶段有独立Token预算；超预算时使用章节摘要，不无界吞入全部证据。
8. 每张 Card 有且只有一个判定，每个证据有稳定anchor，每个综合输入证据有且只有一个最终处置。
9. 所有未进入最终论文观点的证据在中文审计中显式列出原因。
10. 不存在字符估算、JSON修补、自动重试、跨论文拼批、结构失败后的顺序兜底或旧结果继续发布。

## 上下游影响

**上游：** 不修改 `material.v2` 和 MD→Card 结果。LLM层新增的 `projected_cards.jsonl`、`section_map.json` 都是派生产物；上游重建 generation 后，旧计划因 generation/hash 不匹配而不可复用。

**下游：** 论文分析 Schema 从 v1 升到 v2；审核文件新增 `evidence_anchor_id` 和证据处置。后续综述生成只能读取已完成、当前generation一致且人工审核状态允许的 v2 论文分析，不能读取旧 v1 结果。

**未在本目标执行：** 多论文横向综合、综述大纲生成、自动写综述、为综合阶段开启 Think High、对82张和250张论文发起真实付费调用。
