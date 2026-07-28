# MD-to-Card 稳健性重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Markdown 到材料卡的流程重构为可定界、可追溯、无静默丢失、可用真实论文验收的通用解析流程。

**Architecture:** 在现有 `LiteraturePipeline` 与卡片输出之间增加“文档地图”中间层。流程先确定目标论文在 Markdown 中的范围，再识别前置内容、摘要、正文和后置内容，随后构建标题路径与内容块，最后按结构边界打包卡片；无法唯一判断文档范围时直接失败，不默认选择第一段。`material.v2` 的必需字段保持不变，只增加可选的 `content_kind`、`heading_path`、`source_spans` 和 `source_fingerprint`，MinerU 与 LLM API 不在本计划中修改。

**Tech Stack:** Python 标准库、`dataclasses`、`html.parser.HTMLParser`、`unittest`、现有 JSON/JSONL 工作区格式。

---

## 一、范围与硬约束

本计划只修改以下链路：

```text
normalized/document.md
  -> 目标文档定界
  -> 区域与标题结构
  -> 内容块
  -> material.v2 卡片
  -> 结构/材料质量审计
```

不修改：

- MinerU 精确模式 API 调用与 PDF 下载流程。
- LLM provider、单论文调用约束和分析结果 schema。
- 上游论文获取方式。
- 综述生成逻辑。

必须满足：

1. 多篇文章混在一个 Markdown 时，只能在目标范围唯一确定后制卡。
2. 无法唯一确定目标范围时，状态为 `failed/red`，记录问题，不选择第一篇作为兜底。
3. 进入目标论文正文范围的内容不得静默丢失、重叠或截断。
4. 参考文献、致谢、作者简介等后置内容不得进入正文卡。
5. 表格不得替换成 `[table removed]`；解析失败时保留原始表格文本并标记问题。
6. 卡片标题只描述可验证的结构位置，不使用全文关键词伪造语义标题。
7. 所有新增问题说明和审计报告使用中文，问题代码与 JSON 字段保持英文。

## 二、文件分工

### 新建文件

- `shipping/src/shipping_pipeline/markdown_structure.py`
  - 文档分段、目标范围选择、标题候选、标题路径、区域识别和文档地图。
- `shipping/src/shipping_pipeline/content_blocks.py`
  - 将选定区域转换为段落、表格、图片占位、公式等可追溯内容块。
- `shipping/src/shipping_pipeline/card_builder.py`
  - 按标题路径和内容类型打包 `material.v2` 卡片，处理长块拆分和短尾合并。
- `shipping/tests/test_markdown_structure.py`
  - 文档定界、编号语法、普通标题序列和后置区域单元测试。
- `shipping/tests/test_content_blocks.py`
  - HTML 表格、图片、段落和来源范围单元测试。
- `shipping/tests/test_card_builder.py`
  - 卡片边界、标题、完整性和兼容字段单元测试。
- `shipping/scripts/validate_md_card_corpus.py`
  - 对现有真实论文集执行明确的回归断言。

### 修改文件

- `shipping/src/shipping_pipeline/pipeline.py`
  - 保留五阶段编排；删除其中的结构推断、表格删除、关键词标题和字符截断实现，改为调用新模块。
- `shipping/src/shipping_pipeline/material_quality.py`
  - 增加范围、覆盖率、后置内容、表格、短卡和重复文献审计。
- `shipping/src/shipping_pipeline/material_review.py`
  - 展示新增的内容类型与标题路径，不改变人工勾选格式。
- `shipping/tests/test_pipeline.py`
  - 保留端到端和 CLI 测试，更新旧的卡片标题与表格删除断言。
- `shipping/docs/md-card-robustness-20260709.md`
  - 在实施完成后追加本轮重构结果和前后对比。

## 三、核心数据契约

在 `markdown_structure.py` 中定义以下不可变数据结构：

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceSpan:
    path: str
    start_line: int
    end_line: int
    start_char: int | None = None
    end_char: int | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        payload: dict[str, int | str | None] = {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }
        if self.start_char is not None:
            payload["start_char"] = self.start_char
        if self.end_char is not None:
            payload["end_char"] = self.end_char
        return payload


@dataclass(frozen=True)
class DocumentSegment:
    title: str
    start_line: int
    end_line: int
    h1_line: int | None
    match_score: float = 0.0


@dataclass(frozen=True)
class HeadingNode:
    node_id: str
    raw_title: str
    clean_title: str
    depth: int
    source: str
    start_line: int
    end_line: int
    ordinal_path: tuple[int, ...] = ()
    confidence_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Region:
    role: str
    span: SourceSpan
    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentMap:
    paper_id: str
    paper_title: str
    selected_segment: DocumentSegment
    headings: tuple[HeadingNode, ...]
    regions: tuple[Region, ...]
    quality_label: str
    issues: tuple[dict, ...] = field(default_factory=tuple)
```

在 `content_blocks.py` 中定义：

```python
@dataclass(frozen=True)
class ContentBlock:
    block_id: str
    group_id: str
    content_kind: str
    text: str
    source_spans: tuple[SourceSpan, ...]
    heading_path: tuple[str, ...]
    quality_flags: tuple[str, ...] = ()
```

卡片继续输出以下现有必需字段：

```text
schema_version, material_id, material_type, paper_id, paper_title,
source_path, raw_title, clean_title, parent_ref, order, summary,
lead_excerpt, extract, source_span, content_ref,
confidence_flags, quality_flags
```

并增加以下可选字段：

```json
{
  "content_kind": "prose|table|abstract|formula|image",
  "heading_path": ["第二章 方法", "2.1 数据来源"],
  "source_spans": [
    {"path": "normalized/document.md", "start_line": 20, "end_line": 27}
  ],
  "source_block_ids": ["body_0007", "body_0008"],
  "source_fingerprint": "sha256:..."
}
```

`structure/structure.json` 增加但不删除现有字段：

```json
{
  "selected_segment": {
    "title": "三峡航运遇瓶颈",
    "start_line": 25,
    "end_line": 44,
    "match_score": 1.0
  },
  "regions": [
    {"role": "front_matter", "start_line": 26, "end_line": 27, "included_in_materials": false},
    {"role": "body", "start_line": 28, "end_line": 44, "included_in_materials": true}
  ],
  "coverage": {
    "included_block_count": 8,
    "materialized_block_count": 8,
    "unmaterialized_block_ids": [],
    "multiply_materialized_block_ids": [],
    "body_coverage_ratio": 1.0
  }
}
```

前置内容只记录在结构文件和审计报告中，不写入 `_corpus/materials.jsonl`。明确标记的摘要仍生成 `abstract` 卡；无法分类的前置内容记录 `parse.unclassified_front_matter`，避免作者单位等元数据进入现有 LLM 流程。

## 四、质量状态定义

`red` 条件：

- `parse.document_scope_ambiguous`
- `parse.empty_selected_document`
- `coverage.missing_body_content`
- `coverage.unexpected_overlap`
- `content_ref.invalid` 或 `content_ref.out_of_range`

`silver` 条件：

- `parse.document_title_fuzzy_match`
- `parse.inferred_headings`
- `parse.unclassified_front_matter`
- `table.parse_failed`
- `table.row_too_long`
- `media.image_omitted`
- 明确记录的 OCR 污染

`gold` 条件：

- 目标文档范围唯一。
- 正文块覆盖率为 `1.0`。
- 没有未解释重叠。
- 后置内容未进入材料卡。
- 没有结构类 warning。

`gold` 不再由“检测到至少一个章节节点”直接得到。

---

### Task 1: 建立文档范围模型与严格定界

**Files:**
- Create: `shipping/src/shipping_pipeline/markdown_structure.py`
- Test: `shipping/tests/test_markdown_structure.py`

- [x] **Step 1: 写多 H1 精确选段失败测试**

```python
import unittest

from shipping_pipeline.markdown_structure import build_document_map


class DocumentScopeTests(unittest.TestCase):
    def test_selects_h1_segment_matching_paper_id(self) -> None:
        lines = """# 湖南凤凰大桥坍塌的背后

桥梁事故正文。

# 三峡航运遇瓶颈

三峡船闸正文。

# 老城墙倒塌

城墙正文。
""".splitlines()

        result = build_document_map("三峡航运遇瓶颈", lines)

        self.assertEqual(result.paper_title, "三峡航运遇瓶颈")
        self.assertEqual(result.selected_segment.start_line, 5)
        self.assertEqual(result.selected_segment.end_line, 8)
        self.assertNotEqual(result.quality_label, "red")

    def test_rejects_ambiguous_multiple_h1_segments(self) -> None:
        lines = """# 第一篇文章

正文一。

# 第二篇文章

正文二。
""".splitlines()

        result = build_document_map("目标论文", lines)

        self.assertEqual(result.quality_label, "red")
        self.assertIn(
            "parse.document_scope_ambiguous",
            [issue["code"] for issue in result.issues],
        )
```

- [x] **Step 2: 运行测试并确认失败**

Run: `python -m unittest shipping.tests.test_markdown_structure.DocumentScopeTests -v`

Expected: `ImportError` 或 `ModuleNotFoundError`，因为模块尚未实现。

- [x] **Step 3: 实现 H1 分段和唯一范围选择**

实现规则：

```python
def split_h1_segments(lines: list[str]) -> list[DocumentSegment]:
    """H1 行是段起点，下一个 H1 的前一行是段终点。无 H1 时整篇为一个匿名段。"""


def select_document_segment(
    paper_id: str,
    segments: list[DocumentSegment],
) -> tuple[DocumentSegment | None, list[dict]]:
    """选择唯一最佳段；并列或最高分不足时返回 None 和显式问题。"""
```

标题归一化必须：去除空白、常见标点、HTML 标签并转小写，但不得删除中文、字母和数字。评分顺序固定为：

```text
归一化后完全相等 = 1.00
一方完整包含另一方且较短标题不少于 6 个字符 = 0.85
difflib.SequenceMatcher >= 0.72 = 实际相似度
其余 = 0.00
```

只有最高分 `>= 0.72` 且与第二名差值 `>= 0.15` 时才允许选择；否则记录 `parse.document_scope_ambiguous`。单 H1 文档直接选择该段；无 H1 文档选择全文并记录 `parse.no_h1`，不得因此失败。

- [x] **Step 4: 运行范围测试并确认通过**

Run: `python -m unittest shipping.tests.test_markdown_structure.DocumentScopeTests -v`

Expected: 两个测试均为 `ok`。

### Task 2: 重建标题语法与层级，不再依赖章根节点

**Files:**
- Modify: `shipping/src/shipping_pipeline/markdown_structure.py`
- Modify: `shipping/tests/test_markdown_structure.py`

- [x] **Step 1: 写编号变体和年份误判测试**

```python
class HeadingRecognitionTests(unittest.TestCase):
    def test_supports_common_chinese_and_arabic_numbering(self) -> None:
        lines = """# 论文

## 1、运输化与工业化的联系

正文。

## 2. 数据分析

正文。

## 2.1 德国数据

正文。

## 三、结论

正文。
""".splitlines()

        result = build_document_map("论文", lines)
        titles = [heading.raw_title for heading in result.headings]

        self.assertEqual(
            titles,
            ["1、运输化与工业化的联系", "2. 数据分析", "2.1 德国数据", "三、结论"],
        )
        self.assertEqual([heading.depth for heading in result.headings], [1, 1, 2, 1])

    def test_does_not_promote_isolated_year_sentence_to_heading(self) -> None:
        lines = """# 论文

## 第1章 绪论

2000 年，Yu 和 Li 提出了一个模型：

公式正文。

## 第2章 方法

方法正文。
""".splitlines()

        result = build_document_map("论文", lines)

        self.assertNotIn("2000 年，Yu 和 Li 提出了一个模型：", [item.raw_title for item in result.headings])
```

- [x] **Step 2: 运行测试并确认年份测试失败**

Run: `python -m unittest shipping.tests.test_markdown_structure.HeadingRecognitionTests -v`

Expected: 当前普通编号规则会把 `2000 年...` 当作标题，测试失败。

- [x] **Step 3: 实现统一编号解析器**

定义：

```python
@dataclass(frozen=True)
class Numbering:
    depth: int
    ordinal_path: tuple[int, ...]
    label: str


def parse_numbering(title: str) -> Numbering | None:
    """支持第N章、一/二/三、与 1、1.、1．、1.1、1.1.1。"""
```

规则：

- `第N章`、`一、`、`1 `、`1、`、`1.`、`1．` 为深度 1。
- `1.1` 为深度 2，`1.1.1` 为深度 3。
- 根序号必须处于 `0..99`，避免年份成为根序号。
- Markdown 标题全部保留为结构边界；编号只决定层级，不决定标题是否存在。
- 普通文本标题只有在同一目标段内形成至少两个连续根序号，或与 Markdown 标题共同构成连续序列时才升级为标题。
- 普通标题必须长度不超过 60，且前后至少一侧为空行；句末为完整句号、问号、感叹号时不得升级。

- [x] **Step 4: 运行标题测试并确认通过**

Run: `python -m unittest shipping.tests.test_markdown_structure.HeadingRecognitionTests -v`

Expected: 两个测试均为 `ok`。

### Task 3: 明确摘要、正文和后置区域

**Files:**
- Modify: `shipping/src/shipping_pipeline/markdown_structure.py`
- Modify: `shipping/tests/test_markdown_structure.py`

- [x] **Step 1: 写参考文献变体和摘要保留测试**

```python
class RegionTests(unittest.TestCase):
    def test_classifies_abstract_and_stops_before_reference_variants(self) -> None:
        for reference_heading in (
            "## 参考文献：",
            "## 【参考文献】",
            "## 参 考 文 献<sup>．</sup>",
            "## References",
        ):
            with self.subTest(reference_heading=reference_heading):
                lines = f"""# 论文

## 【文章摘要】

这是摘要。

## 1、研究方法

这是正文。

{reference_heading}

[1] 参考文献内容。
""".splitlines()

                result = build_document_map("论文", lines)
                roles = [region.role for region in result.regions]

                self.assertIn("abstract", roles)
                self.assertIn("body", roles)
                self.assertIn("back_matter", roles)
                body_end = max(region.span.end_line for region in result.regions if region.role == "body")
                back_start = min(region.span.start_line for region in result.regions if region.role == "back_matter")
                self.assertLess(body_end, back_start)
```

- [x] **Step 2: 运行区域测试并确认失败**

Run: `python -m unittest shipping.tests.test_markdown_structure.RegionTests -v`

Expected: 带冒号、书名括号、空格或 HTML 的参考文献标题无法全部识别。

- [x] **Step 3: 实现区域标题规范化和区域划分**

实现：

```python
ABSTRACT_TITLES = {"摘要", "文章摘要", "abstract"}
KEYWORD_TITLES = {"关键词", "关键字", "keywords", "keywordsindex"}
BACK_MATTER_TITLES = {
    "参考文献",
    "references",
    "bibliography",
    "致谢",
    "acknowledgements",
    "作者简介",
}


def canonical_heading_title(value: str) -> str:
    """去 HTML、全角/半角括号、空白和尾部标点后转小写。"""
```

区域规则：

- 目标 H1 行本身不进入材料正文。
- 明确摘要标题到下一个同级结构边界之间为 `abstract`。
- 第一个正文标题前无法进一步分类的可见内容为 `front_matter`，保留来源范围但不生成材料卡，并记录 `parse.unclassified_front_matter`。
- `摘要：`、`Abstract:`、`关键词：` 等行内标签也参与摘要和关键词识别，不能仅依赖 Markdown 标题。
- 正文标题到首个后置标题前为 `body`。
- 后置标题开始到目标文档段结束为 `back_matter`，不制卡。
- 无正文标题时，目标 H1 后到后置标题前作为 `body`，记录 `parse.no_structured_body`。

- [x] **Step 4: 运行区域测试并确认通过**

Run: `python -m unittest shipping.tests.test_markdown_structure.RegionTests -v`

Expected: 所有 subtest 为 `ok`。

### Task 4: 将正文转换为不丢证据的内容块

**Files:**
- Create: `shipping/src/shipping_pipeline/content_blocks.py`
- Test: `shipping/tests/test_content_blocks.py`

- [x] **Step 1: 写 HTML 表格和图片暴露测试**

```python
import unittest

from shipping_pipeline.content_blocks import build_content_blocks


class ContentBlockTests(unittest.TestCase):
    def test_preserves_html_table_cells_as_table_block(self) -> None:
        lines = """## 2 结果

<table><tr><th>方案</th><th>等待时间</th></tr><tr><td>A</td><td>12.5</td></tr></table>
""".splitlines()

        blocks = build_content_blocks(lines, start_line=1, end_line=len(lines), heading_path=("2 结果",))

        table = next(item for item in blocks if item.content_kind == "table")
        self.assertIn("方案 | 等待时间", table.text)
        self.assertIn("A | 12.5", table.text)
        self.assertNotIn("[table removed]", table.text)

    def test_marks_image_omission_instead_of_silently_deleting_it(self) -> None:
        lines = """## 2 结果

结果如图所示。

![排队长度变化](images/queue.png)
""".splitlines()

        blocks = build_content_blocks(lines, start_line=1, end_line=len(lines), heading_path=("2 结果",))

        image = next(item for item in blocks if item.content_kind == "image")
        self.assertEqual(image.text, "排队长度变化")
        self.assertIn("media.image_omitted", image.quality_flags)

    def test_splits_long_single_line_without_losing_text(self) -> None:
        body = "长段落内容。" * 500
        lines = ["## 第一章 绪论", "", body]

        blocks = build_content_blocks(lines, start_line=1, end_line=3, heading_path=("第一章 绪论",))
        prose = [item for item in blocks if item.content_kind == "prose"]

        self.assertEqual("".join(item.text for item in prose), body)
        self.assertTrue(all(len(item.text) <= 1400 for item in prose))
        self.assertTrue(all(item.source_spans[0].start_line == 3 for item in prose))
        self.assertTrue(all(item.source_spans[0].start_char is not None for item in prose))
```

- [x] **Step 2: 运行内容块测试并确认失败**

Run: `python -m unittest shipping.tests.test_content_blocks -v`

Expected: `ModuleNotFoundError`。

- [x] **Step 3: 实现段落和表格块解析**

实现要求：

```python
class TableTextParser(HTMLParser):
    """提取 th/td 单元格，保留行边界，不推断表格语义。"""


def build_content_blocks(
    lines: list[str],
    start_line: int,
    end_line: int,
    heading_path: tuple[str, ...],
) -> list[ContentBlock]:
    """按空行、标题、HTML 表格和 Markdown 图片构建有来源范围的块。"""
```

处理规则：

- 连续普通文本组成 `prose` 块。
- `<table>...</table>` 使用 `HTMLParser` 转成 `单元格 | 单元格` 行文本，类型为 `table`；同一表格共享 `group_id`。
- 表格解析异常时保留原始可见文本，标记 `table.parse_failed`，不得替换为空。
- Markdown 图片生成 `image` 块，保留 alt 文本并标记 `media.image_omitted`。
- LaTeX/Markdown 公式原样保留为 `formula` 或合并进相邻 `prose`，不得删除。
- 每个块至少包含一个 `SourceSpan`。
- 普通文本单块超过 1400 字符时，在内容块阶段按句末拆分；仍无法拆分时按字符拆分并记录 `start_char/end_char`。
- 表格按行组成不超过 1400 字符的原子块；不得切断单元格。单行本身超过 1400 字符时保留整行并标记 `table.row_too_long`，由审计暴露，不截断。

- [x] **Step 4: 运行内容块测试并确认通过**

Run: `python -m unittest shipping.tests.test_content_blocks -v`

Expected: 三个测试均为 `ok`。

### Task 5: 按标题路径制卡，消除关键词标题和静默截断

**Files:**
- Create: `shipping/src/shipping_pipeline/card_builder.py`
- Test: `shipping/tests/test_card_builder.py`

- [x] **Step 1: 写边界、短尾和标题测试**

```python
import unittest

from shipping_pipeline.card_builder import build_material_cards
from shipping_pipeline.content_blocks import ContentBlock
from shipping_pipeline.markdown_structure import SourceSpan


def make_blocks(
    heading_path: tuple[str, ...],
    texts: list[str],
    start_line: int = 1,
) -> list[ContentBlock]:
    return [
        ContentBlock(
            block_id=f"block_{index:03d}",
            group_id=f"prose_{index:03d}",
            content_kind="prose",
            text=text,
            source_spans=(
                SourceSpan(
                    path="normalized/document.md",
                    start_line=start_line + index - 1,
                    end_line=start_line + index - 1,
                ),
            ),
            heading_path=heading_path,
        )
        for index, text in enumerate(texts, start=1)
    ]


def make_mixed_blocks() -> list[ContentBlock]:
    return [
        *make_blocks(("第二章 结果",), ["结果说明。"]),
        ContentBlock(
            block_id="table_001_part_001",
            group_id="table_001",
            content_kind="table",
            text="方案 | 等待时间\nA | 12.5",
            source_spans=(SourceSpan("normalized/document.md", 3, 3),),
            heading_path=("第二章 结果",),
        ),
    ]


class CardBuilderTests(unittest.TestCase):
    def test_does_not_cross_heading_path_or_use_keyword_title(self) -> None:
        blocks = make_blocks(
            ("第二章 方法", "2.1 数据"),
            ["结果一词只是正文，不应把标题改成结果分析。" * 20],
        ) + make_blocks(
            ("第三章 结论",),
            ["主要结论。" * 20],
        )

        cards = build_material_cards("paper", "论文", blocks)

        self.assertTrue(cards[0]["clean_title"].startswith("第二章 方法 > 2.1 数据"))
        self.assertNotIn("结果分析", cards[0]["clean_title"])
        self.assertNotEqual(cards[0]["heading_path"], cards[-1]["heading_path"])

    def test_assigns_every_atomic_block_exactly_once(self) -> None:
        blocks = make_blocks(
            ("第一章 绪论",),
            ["甲" * 600, "乙" * 600, "丙" * 600],
            start_line=10,
        )

        cards = build_material_cards("paper", "论文", blocks)

        assigned = [block_id for card in cards for block_id in card["source_block_ids"]]
        self.assertCountEqual(assigned, [item.block_id for item in blocks])
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertTrue(all(len(card["extract"]) <= 1400 for card in cards))

    def test_keeps_table_separate_from_prose(self) -> None:
        blocks = make_mixed_blocks()

        cards = build_material_cards("paper", "论文", blocks)

        self.assertIn("table", {card["content_kind"] for card in cards})
        self.assertTrue(all(card["content_kind"] != "table" or "方案 | 等待时间" in card["extract"] for card in cards))
```

- [x] **Step 2: 运行制卡测试并确认失败**

Run: `python -m unittest shipping.tests.test_card_builder -v`

Expected: `ModuleNotFoundError`。

- [x] **Step 3: 实现结构感知打包器**

公开接口：

```python
MATERIAL_TARGET_CHARS = 1100
MATERIAL_MAX_CHARS = 1400
MATERIAL_MIN_TAIL_CHARS = 200


def build_material_cards(
    paper_id: str,
    paper_title: str,
    blocks: list[ContentBlock],
    confidence_flags: list[str] | None = None,
) -> list[dict]:
    """按 heading_path/content_kind 分组打包，不跨结构边界，不丢字符。"""
```

规则：

- 只有 `heading_path` 和 `content_kind` 相同的相邻块可以合并。
- `table` 和 `image` 永远独立成卡。
- 输入块必须已经不超过 1400 字符；发现超长 prose 块时抛出 `ValueError`，因为这表示内容块阶段违反契约。`table.row_too_long` 是唯一允许的超长输入，并在卡片中完整保留。
- 最后一个 prose 片段不足 200 字符时，仅当与前一片段合并后不超过 1400 才合并；否则保留并标记 `card.short_tail`。
- 不再调用 `_bound_extract` 截断文本。
- `summary/lead_excerpt` 仍取前两句且不超过 280 字符，但 `extract` 必须完整。
- `clean_title` 固定为：

```text
有标题路径：标题1 > 标题2（片段 i/n）
无标题路径：正文片段 001（L10-L18）
表格：标题路径 > 表格（片段 i/n）
摘要：论文标题 > 摘要（片段 i/n）
```

- `material_id` 固定为 `{paper_id}:{content_kind}:L{start:05d}-L{end:05d}:{fragment:02d}`。
- `source_fingerprint` 为规范化 `extract` 的 SHA-256，不用于去重，只用于审计。
- `source_block_ids` 列出组成卡片的原子内容块；每个纳入材料的块必须且只能出现在一张卡中，覆盖率据此计算。

- [x] **Step 4: 运行制卡测试并确认通过**

Run: `python -m unittest shipping.tests.test_card_builder -v`

Expected: 三个测试均为 `ok`。

### Task 6: 接入现有五阶段 pipeline，并重定义结构质量

**Files:**
- Modify: `shipping/src/shipping_pipeline/pipeline.py`
- Modify: `shipping/tests/test_pipeline.py`

- [x] **Step 1: 写端到端失败测试**

在 `test_pipeline.py` 增加：

```python
def test_pipeline_rejects_ambiguous_document_bundle(self) -> None:
    source = self.tmp_path / "bundle.md"
    source.write_text("# 第一篇\n\n正文一。\n\n# 第二篇\n\n正文二。\n", encoding="utf-8")

    result = LiteraturePipeline(self.workspace).run(source, paper_id="目标论文")

    self.assertEqual(result.status, "failed")
    self.assertEqual(result.structure_quality, "red")
    self.assertFalse((self.workspace / "目标论文" / "materials" / "materials.jsonl").exists())


def test_pipeline_preserves_abstract_and_excludes_references(self) -> None:
    source = self.tmp_path / "paper.md"
    source.write_text(
        "# 论文\n\n## 摘要\n\n摘要证据。\n\n## 1、方法\n\n正文证据。\n\n## 参考文献：\n\n禁止进入卡片。\n",
        encoding="utf-8",
    )

    result = LiteraturePipeline(self.workspace).run(source, paper_id="论文")
    materials = read_jsonl(self.workspace / "论文" / "materials" / "materials.jsonl")

    self.assertEqual(result.status, "completed")
    self.assertIn("abstract", {item["content_kind"] for item in materials})
    self.assertNotIn("禁止进入卡片", " ".join(item["extract"] for item in materials))
```

- [x] **Step 2: 运行端到端测试并确认失败**

Run: `python -m unittest shipping.tests.test_pipeline.PipelineTests -v`

Expected: 多 H1 仍被继续制卡，参考文献变体仍进入材料，测试失败。

- [x] **Step 3: 替换 pipeline 内部实现**

`LiteraturePipeline._parse` 改为：

```python
document_map = build_document_map(paper_id, content.splitlines())
write_document_map(document_map, paper_workspace / "structure" / "structure.json")
write_quality(document_map, paper_workspace / "structure" / "quality.json")
```

`LiteraturePipeline._materialize` 改为：

```python
blocks = build_blocks_for_document_map(lines, document_map)
materials = build_material_cards(
    paper_id=paper_id,
    paper_title=document_map.paper_title,
    blocks=blocks,
    confidence_flags=[issue["code"] for issue in document_map.issues],
)
```

删除或停止调用以下旧逻辑：

- `_select_chapter_headings`
- `_node_material_cards`
- `_unstructured_material_cards`
- `_semantic_title`
- `_bound_extract`
- `TABLE_BLOCK_PATTERN` 替换删除

当 `document_map.quality_label == "red"` 时不生成材料文件，与当前解析失败行为一致。

- [x] **Step 4: 运行全部 pipeline 测试**

Run: `python -m unittest shipping.tests.test_pipeline -v`

Expected: 所有测试通过；旧测试中期待 `[table removed]`、`未识别主题片段` 和单纯关键词标题的断言已替换为新契约。

### Task 7: 扩展审计，让结构错误真正出现在报告中

**Files:**
- Modify: `shipping/src/shipping_pipeline/material_quality.py`
- Modify: `shipping/src/shipping_pipeline/material_review.py`
- Modify: `shipping/tests/test_pipeline.py`

- [x] **Step 1: 写覆盖率、表格和重复内容审计测试**

```python
def test_material_audit_reports_structure_and_content_integrity(self) -> None:
    materials = []
    for paper_id, coverage in (("paper-a", 0.8), ("paper-b", 1.0)):
        paper_dir = self.workspace / paper_id
        (paper_dir / "normalized").mkdir(parents=True)
        (paper_dir / "structure").mkdir(parents=True)
        (paper_dir / "normalized" / "document.md").write_text(
            "# 论文\n\n## 结果\n\n<table><tr><td>A</td><td>12.5</td></tr></table>\n",
            encoding="utf-8",
        )
        (paper_dir / "structure" / "structure.json").write_text(
            json.dumps(
                {
                    "paper_id": paper_id,
                    "coverage": {
                        "body_coverage_ratio": coverage,
                        "unmaterialized_block_ids": ["missing"] if coverage < 1.0 else [],
                        "multiply_materialized_block_ids": [],
                    },
                    "regions": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        materials.append(
            {
                "schema_version": "material.v2",
                "material_id": f"{paper_id}:table:L00005-L00005:01",
                "material_type": "evidence_card",
                "paper_id": paper_id,
                "paper_title": "论文",
                "source_path": "normalized/document.md",
                "raw_title": "结果",
                "clean_title": "结果 > 表格",
                "parent_ref": {"node_id": "result", "title": "结果"},
                "order": 1,
                "summary": "方案 A 的等待时间为 12.5。",
                "lead_excerpt": "方案 A 的等待时间为 12.5。",
                "extract": "方案 | 等待时间\nA | 12.5",
                "source_span": {"path": "normalized/document.md", "start_line": 5, "end_line": 5},
                "content_ref": {"path": "normalized/document.md", "start_line": 5, "end_line": 5},
                "confidence_flags": [],
                "quality_flags": [],
                "content_kind": "table",
                "heading_path": ["结果"],
                "source_spans": [{"path": "normalized/document.md", "start_line": 5, "end_line": 5}],
                "source_fingerprint": "sha256:same",
            }
        )

    corpus = self.workspace / "_corpus"
    corpus.mkdir()
    (corpus / "materials.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in materials),
        encoding="utf-8",
    )

    result = MaterialQualityAuditor(self.workspace).run(run_id="integrity")

    self.assertEqual(result["issue_counts"]["coverage.missing_body_content"], 1)
    self.assertEqual(result["issue_counts"]["corpus.duplicate_content"], 2)
    self.assertNotIn("extract.contains_markup", result["issue_counts"])
```

- [x] **Step 2: 运行审计测试并确认失败**

Run: `python -m unittest shipping.tests.test_pipeline.PipelineTests.test_material_audit_reports_structure_and_content_integrity -v`

Expected: 当前审计不读取结构覆盖指标，也不检查指纹重复，测试失败。

- [x] **Step 3: 实现新增审计项目**

材料级检查：

```text
card.short_tail
table.parse_failed
table.row_too_long
media.image_omitted
content_ref.invalid
content_ref.out_of_range
```

论文级检查：

```text
parse.document_scope_ambiguous
parse.document_title_fuzzy_match
parse.inferred_headings
parse.unclassified_front_matter
coverage.missing_body_content
coverage.unexpected_overlap
region.back_matter_in_material
```

语料级检查：

```text
corpus.duplicate_content
corpus.duplicate_source
```

重复内容只在跨论文出现相同 `source_fingerprint` 时报告 `paper_id/material_id/source_fingerprint`，同一论文内部的重复短语不作为语料重复；不自动删除。审计 Markdown 报告新增：

```text
文档范围
正文覆盖率
摘要卡数量
正文卡数量
表格卡数量
后置内容排除范围
推断标题数量
短尾卡数量
重复内容组
```

人工审核文件在每张卡中增加：

```markdown
- 内容类型: `table`
- 标题路径: `第五章 ... > 5.2.4 判断矩阵`
```

- [x] **Step 4: 运行审计和人工审核测试**

Run: `python -m unittest shipping.tests.test_pipeline -v`

Expected: 全部通过，审计结果不再只显示弱标题问题。

### Task 8: 用当前真实论文集建立验收门槛

**Files:**
- Create: `shipping/scripts/validate_md_card_corpus.py`
- Modify: `shipping/docs/md-card-robustness-20260709.md`

- [x] **Step 1: 实现真实语料断言脚本**

脚本接受：

```text
python shipping/scripts/validate_md_card_corpus.py --workspace shipping/workspace
```

并逐项断言：

```python
EXPECTED_CASES = {
    "三峡航运遇瓶颈": {
        "paper_title": "三峡航运遇瓶颈",
        "min_source_line": 25,
        "max_source_line": 44,
        "forbidden_text": ["堤溪大桥", "老城六百年不倒"],
    },
    "基于Arena的三峡船舶积压疏导策略效果研究": {
        "required_source_lines": [15, 23, 44, 71, 95],
        "max_source_line": 104,
        "forbidden_text": ["Key words:traffic control line"],
    },
    "基于运输化理论的三峡船闸过闸货运需求量增长趋势分析": {
        "required_headings": ["1、运输化与工业化的联系", "2、部分国家及地区水运货运量与经济发展数据分析", "3、三峡船闸过闸需求趋势分析", "4、小结"],
        "forbidden_text": ["[1]荣朝和"],
    },
    "考虑翻坝和天气的长江班轮运网鲁棒优化模型": {
        "forbidden_headings": ["2000 年，Yu 和 Li 提出了"],
        "required_headings": ["第1章 绪论", "第 7 章 结论与展望"],
    },
    "慎防信息不对称影响船闸管理": {
        "required_content_kinds": ["abstract", "prose"],
        "forbidden_text": ["【参考文献】", "【作者简介】"],
    },
    "长江上游地区产业布局及航运适应性研究": {
        "forbidden_paper_titles": ["专业硕士学位论文"],
        "minimum_table_cards": 1,
        "forbidden_text": ["[table removed]"],
    },
}
```

脚本还必须对全部论文执行通用断言：

```text
body_coverage_ratio == 1.0（red 文档除外）
所有 source_span 均在原文范围内
所有 extract 非空
所有 extract <= 1400 字符（带 table.row_too_long 的表格卡除外）
不存在 extract.trimmed
不存在 [table removed]
后置区域与材料 source_span 不相交
```

任一失败时打印中文错误并返回非零退出码；不得把失败转成 warning 后继续返回成功。

- [x] **Step 2: 重新运行真实 MD→Card**

Run:

```powershell
python shipping/main.py audit shipping/workspace --workspace shipping/workspace --topic "三峡船舶积压与长江航运组织" --run-id md-card-structure-v2-20260710
```

Expected: 生成新的结构、卡片和批量审计结果；不覆盖 normalized Markdown。

- [x] **Step 3: 运行真实语料验收**

Run:

```powershell
python shipping/scripts/validate_md_card_corpus.py --workspace shipping/workspace
```

Expected:

```text
真实语料验收通过：17 篇论文，正文覆盖率完整，未发现静默截断或后置内容污染。
```

- [x] **Step 4: 生成新材料审计和人工审核包**

Run:

```powershell
python shipping/main.py material-audit --workspace shipping/workspace --run-id material-quality-structure-v2-20260710
python shipping/main.py material-review --workspace shipping/workspace --run-id material-review-structure-v2-20260710
```

Expected: 报告包含范围、覆盖率、表格和重复内容统计，人工审核卡展示内容类型与标题路径。

- [x] **Step 5: 记录前后对比**

在 `shipping/docs/md-card-robustness-20260709.md` 追加：

```text
旧版/新版论文质量分布
错误文档范围数量
正文覆盖率不足数量
参考文献污染卡数量
表格删除卡数量
短尾卡数量
重复论文组数量
真实验收失败项
```

不把仍存在的问题改写为“可接受”，直接记录论文、material_id、source_span 和问题代码。

### Task 9: 完整验证

**Files:**
- Verify only

- [x] **Step 1: 运行全部单元测试**

Run: `python -m unittest discover -s shipping/tests -v`

Expected: 全部测试通过。

- [x] **Step 2: 编译检查**

Run: `python -m compileall -q shipping/src shipping/main.py shipping/scripts`

Expected: 退出码为 0，无输出。

- [x] **Step 3: 检查旧危险逻辑已停止使用**

Run:

```powershell
rg -n "_semantic_title|_bound_extract|\[table removed\]|TABLE_BLOCK_PATTERN" shipping/src
```

Expected: 生产代码无匹配；测试或历史文档中的匹配不影响结果。

- [x] **Step 4: 检查机器接口兼容性**

从 `_corpus/materials.jsonl` 随机抽取至少一张 prose、一张 abstract 和一张 table 卡，确认原有必需字段全部存在，新字段为附加字段，现有 LLM analyzer 可读取而无需修改。

## 五、实施后的判定标准

完成不以“测试通过”单独判定，必须同时满足：

1. `三峡航运遇瓶颈` 不再包含另外两篇新闻。
2. Arena 论文的引言、方法、模型、结果和结论均进入卡片。
3. `1、2、3、4、` 编号论文不再被判为无结构。
4. `2000 年...` 不再成为章节。
5. `参考文献：`、`【参考文献】`、带 HTML 的参考文献标题均能停止正文。
6. 现有 55 张删除表格的卡片降为 0，且表格内容可人工读取。
7. 所有非失败论文正文覆盖率为 1.0。
8. 审计报告能直接指出范围、覆盖、表格、短尾和重复文献问题。
9. MinerU 和 LLM 分析代码没有行为改动。

## 六、风险与明确取舍

- 标题层级无法在所有 MinerU 输出中完全恢复。本计划优先保证内容不丢和来源可追溯；层级推断不足时标记 silver，而不是伪造章节。
- 表格规范化不能恢复 MinerU 已经丢失的字符。这里只保证不再由 MD→Card 阶段主动删除现有表格内容。
- 多 H1 模糊匹配存在阈值判断，但候选得分会写入结构结果；阈值不足直接失败，不默认选第一段。
- 重复论文只在审计中暴露，不自动删除，避免把版本差异误判为重复。
- 本轮不让本地规则生成“研究方法”“结果分析”等语义标签；这些语义字段由后续 LLM 基于完整卡片生成。
