from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.card_builder import build_material_cards
from shipping_pipeline.content_blocks import ContentBlock
from shipping_pipeline.markdown_structure import SourceSpan


def make_blocks(
    heading_path: tuple[str, ...],
    texts: list[str],
    start_line: int = 1,
    content_kind: str = "prose",
) -> list[ContentBlock]:
    return [
        ContentBlock(
            block_id=f"block_L{start_line + index - 1:05d}",
            group_id=f"prose_L{start_line + index - 1:05d}",
            content_kind=content_kind,
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
            start_line=20,
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
        cards = build_material_cards("paper", "论文", make_mixed_blocks())

        self.assertEqual({card["content_kind"] for card in cards}, {"prose", "table"})
        table = next(card for card in cards if card["content_kind"] == "table")
        self.assertIn("方案 | 等待时间", table["extract"])
        self.assertIn("表格", table["clean_title"])

    def test_merges_short_tail_only_when_hard_limit_allows_it(self) -> None:
        mergeable = make_blocks(("第一章",), ["甲" * 900, "乙" * 100], start_line=1)
        unmergeable = make_blocks(("第一章",), ["甲" * 1300, "乙" * 100], start_line=10)

        merged_cards = build_material_cards("merge", "论文", mergeable)
        split_cards = build_material_cards("split", "论文", unmergeable)

        self.assertEqual(len(merged_cards), 1)
        self.assertNotIn("card.short_tail", merged_cards[0]["quality_flags"])
        self.assertEqual(len(split_cards), 2)
        self.assertIn("card.short_tail", split_cards[-1]["quality_flags"])

    def test_uses_explicit_abstract_title_and_keeps_v2_fields(self) -> None:
        blocks = make_blocks(("摘要",), ["本文研究三峡船舶积压。"], content_kind="abstract")

        card = build_material_cards("paper", "论文标题", blocks)[0]

        self.assertEqual(card["schema_version"], "material.v2")
        self.assertEqual(card["material_type"], "evidence_card")
        self.assertEqual(card["content_kind"], "abstract")
        self.assertEqual(card["clean_title"], "论文标题 > 摘要")
        self.assertEqual(card["source_span"], card["content_ref"])
        self.assertTrue(card["source_fingerprint"].startswith("sha256:"))

    def test_rejects_overlong_prose_block_instead_of_trimming_it(self) -> None:
        blocks = make_blocks(("正文",), ["甲" * 1401])

        with self.assertRaisesRegex(ValueError, "1400"):
            build_material_cards("paper", "论文", blocks)


if __name__ == "__main__":
    unittest.main()
