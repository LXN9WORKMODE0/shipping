from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.content_blocks import build_blocks_for_document_map, build_content_blocks
from shipping_pipeline.markdown_structure import build_document_map


class ContentBlockTests(unittest.TestCase):
    def test_preserves_html_table_cells_as_table_block(self) -> None:
        lines = """## 2 结果

<table>
<tr><th>方案</th><th>等待时间</th></tr>
<tr><td>A</td><td>12.5</td></tr>
</table>
""".splitlines()

        blocks = build_content_blocks(lines, start_line=1, end_line=len(lines), heading_path=("2 结果",))

        table = next(item for item in blocks if item.content_kind == "table")
        self.assertIn("方案 | 等待时间", table.text)
        self.assertIn("A | 12.5", table.text)
        self.assertNotIn("[table removed]", table.text)
        self.assertEqual((table.source_spans[0].start_line, table.source_spans[0].end_line), (3, 6))

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

    def test_long_line_character_offsets_use_original_markdown_coordinates(self) -> None:
        body = "原文坐标内容。" * 300
        raw_line = "  " + body
        lines = ["## 第一章 绪论", "", raw_line]

        prose = [
            block
            for block in build_content_blocks(lines, 1, 3, ("第一章 绪论",))
            if block.content_kind == "prose"
        ]

        for block in prose:
            span = block.source_spans[0]
            self.assertIsNotNone(span.start_char)
            self.assertIsNotNone(span.end_char)
            assert span.start_char is not None and span.end_char is not None
            self.assertEqual(raw_line[span.start_char : span.end_char], block.text)

    def test_builds_blocks_only_for_included_document_regions(self) -> None:
        lines = """# 论文

张三，某某大学

摘要 本文研究船舶积压。

关键词：船闸；排队

## 1、方法

正文证据。

## 参考文献

[1] 禁止进入材料。
""".splitlines()
        document_map = build_document_map("论文", lines)

        blocks = build_blocks_for_document_map(lines, document_map)
        combined = "\n".join(block.text for block in blocks)

        self.assertIn("本文研究船舶积压", combined)
        self.assertIn("正文证据", combined)
        self.assertNotIn("某某大学", combined)
        self.assertNotIn("关键词", combined)
        self.assertNotIn("禁止进入材料", combined)
        self.assertEqual({block.content_kind for block in blocks}, {"abstract", "prose"})

    def test_plain_inferred_heading_is_not_repeated_in_prose(self) -> None:
        lines = """# 论文

1 引言

这是引言正文。

2 方法

这是方法正文。
""".splitlines()
        document_map = build_document_map("论文", lines)

        blocks = build_blocks_for_document_map(lines, document_map)
        prose = [block for block in blocks if block.content_kind == "prose"]

        self.assertEqual([block.text for block in prose], ["这是引言正文。", "这是方法正文。"])
        self.assertEqual([block.heading_path for block in prose], [("1 引言",), ("2 方法",)])

    def test_inline_abstract_removes_only_the_structural_label(self) -> None:
        lines = """# 论文

摘要 本文研究船舶积压，摘要内容应完整保留。

## 1 引言

正文。
""".splitlines()
        document_map = build_document_map("论文", lines)

        blocks = build_blocks_for_document_map(lines, document_map)
        abstract = next(block for block in blocks if block.content_kind == "abstract")

        self.assertEqual(abstract.text, "本文研究船舶积压，摘要内容应完整保留。")
        self.assertEqual(abstract.source_spans[0].start_line, 3)

    def test_exposes_malformed_table_without_erasing_source_text(self) -> None:
        lines = ["## 结果", "", "<table>可见的异常表格内容</table>"]

        blocks = build_content_blocks(lines, 1, 3, ("结果",))

        table = next(item for item in blocks if item.content_kind == "table")
        self.assertIn("可见的异常表格内容", table.text)
        self.assertIn("table.parse_failed", table.quality_flags)

    def test_numeric_comparisons_are_not_removed_as_html(self) -> None:
        lines = ["## 结果", "", "0<K<=0.1 and K-E>0.1"]

        blocks = build_content_blocks(lines, 1, 3, ("结果",))
        prose = next(block for block in blocks if block.content_kind == "prose")

        self.assertEqual(prose.text, "0<K<=0.1 and K-E>0.1")
        self.assertNotIn("clean.removed_markup", prose.quality_flags)

    def test_unclosed_table_does_not_swallow_following_prose(self) -> None:
        lines = ["## 结果", "", "<table><tr><td>A</td></tr>", "后续正文不得消失。"]

        blocks = build_content_blocks(lines, 1, 4, ("结果",))
        table = next(block for block in blocks if block.content_kind == "table")
        prose = next(block for block in blocks if block.content_kind == "prose")

        self.assertIn("A", table.text)
        self.assertIn("table.parse_failed", table.quality_flags)
        self.assertEqual(prose.text, "后续正文不得消失。")
        self.assertEqual((prose.source_spans[0].start_line, prose.source_spans[0].end_line), (4, 4))


if __name__ == "__main__":
    unittest.main()
