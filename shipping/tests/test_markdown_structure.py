from __future__ import annotations

import unittest
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

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
        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.start_line, 5)
        self.assertEqual(result.selected_segment.end_line, 8)
        self.assertEqual(result.selected_segment.match_score, 1.0)
        self.assertNotEqual(result.quality_label, "red")

    def test_rejects_ambiguous_multiple_h1_segments(self) -> None:
        lines = """# 第一篇文章

正文一。

# 第二篇文章

正文二。
""".splitlines()

        result = build_document_map("目标论文", lines)

        self.assertEqual(result.quality_label, "red")
        self.assertIsNone(result.selected_segment)
        self.assertIn(
            "parse.document_scope_ambiguous",
            [issue["code"] for issue in result.issues],
        )

    def test_rejects_single_h1_that_does_not_match_expected_identity(self) -> None:
        lines = """# MinerU 识别标题

正文内容。
""".splitlines()

        result = build_document_map("来源文件名", lines)

        self.assertEqual(result.quality_label, "red")
        self.assertIsNotNone(result.selected_segment)
        self.assertIn(
            "parse.document_identity_mismatch",
            [issue["code"] for issue in result.issues],
        )

    def test_keeps_degree_front_matter_h1s_in_one_document(self) -> None:
        lines = """分类号 U651

# 全日制应用型硕士研究生学位论文

# 考虑翻坝和天气的长江班轮运网鲁棒优化模型

# 考虑翻坝和天气的 长江班轮运网鲁棒优化模型

# Robust optimization of liner shipping network

# Dissertation Submitted for the degree of Master

## 暴虹利

作者与导师信息。

## 摘要

摘要正文。

## 第1章 绪论

正文内容。
""".splitlines()

        result = build_document_map("考虑翻坝和天气的长江班轮运网鲁棒优化模型", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.paper_title, "考虑翻坝和天气的长江班轮运网鲁棒优化模型")
        self.assertEqual(result.selected_segment.start_line, 1)
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertNotEqual(result.quality_label, "red")
        first_body = next(region for region in result.regions if region.role == "body")
        self.assertEqual(first_body.heading_path, ("第1章 绪论",))

    def test_treats_internal_h1_chapter_as_structure_in_expanded_degree_document(self) -> None:
        lines = """# 学位论文题目

# 学位论文

## 摘要

摘要正文。

## 第1章 绪论

第一章正文。

# 第2章 仿真结果

第二章正文。
""".splitlines()

        result = build_document_map("学位论文题目", lines)
        chapter_line = lines.index("# 第2章 仿真结果") + 1
        assignment = next(
            row for row in result.line_ledger if row.line_number == chapter_line
        )

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertEqual(assignment.role, "scope_marker")
        self.assertFalse(assignment.included_in_materials)
        self.assertIn(
            ("第2章 仿真结果",),
            [region.heading_path for region in result.regions if region.role == "body"],
        )

    def test_expands_degree_document_when_exact_title_is_h2(self) -> None:
        lines = """# Central South University

# 硕士学术学位论文

## 三峡升船机与船闸梯级枢纽联合调度算法研究

作者信息。

# Cascade Hub Navigation Co-scheduling

## 摘要

摘要正文。

## 第1章 绪论

正文内容。
""".splitlines()

        result = build_document_map(
            "三峡升船机与船闸梯级枢纽联合调度算法研究",
            lines,
        )

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.title_level, 2)
        self.assertEqual(result.selected_segment.start_line, 1)
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertNotEqual(result.quality_label, "red")

    def test_expands_degree_document_with_non_h1_degree_marker(self) -> None:
        lines = """# 目标论文

## 硕士学位论文

# Foreign Title

## 摘要

摘要正文。

## 第1章 绪论

正文内容。
""".splitlines()

        result = build_document_map("目标论文", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.start_line, 1)
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertNotEqual(result.quality_label, "red")

    def test_does_not_end_degree_body_at_front_acknowledgements(self) -> None:
        lines = """# 博士学位论文

# 目标论文

## 致谢

前置致谢。

## 摘要

摘要正文。

## 1 绪论

正文内容。

## 参考文献

文献列表。
""".splitlines()

        result = build_document_map("目标论文", lines)

        body_regions = [region for region in result.regions if region.role == "body"]
        back_regions = [
            region for region in result.regions if region.role == "back_matter"
        ]
        self.assertTrue(any(region.span.start_line == 13 for region in body_regions))
        self.assertEqual([region.span.start_line for region in back_regions], [17])
        self.assertNotEqual(result.quality_label, "red")

    def test_expands_degree_document_with_trailing_h1_after_h2_chapters(self) -> None:
        lines = """申请工学博士学位论文

# 三峡枢纽河段应急通航控制技术研究

# 学位论文

## 题目 三峡枢纽河段应急通航控制技术研究

# Research on Emergency Navigation

## 摘要

摘要正文。

## 第1章 绪论

正文内容。

# 攻读博士学位期间的科研成果及参加的科研项目

成果列表。
""".splitlines()

        result = build_document_map("三峡枢纽河段应急通航控制技术研究", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.start_line, 1)
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertNotEqual(result.quality_label, "red")

    def test_uses_spaced_degree_cover_title_for_document_identity(self) -> None:
        lines = """# 硕 士 学 位 论 文（

论 文 题 目 ： 三 峡 枢 纽 过 坝 货 运 量 预 测 及 路 径 选 择 研 究

## 摘要

摘要正文。

## 第一章 绪论

正文内容。
""".splitlines()

        result = build_document_map("三峡枢纽过坝货运量预测及路径选择研究", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.start_line, 1)
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertIn(
            "parse.degree_cover_title_match",
            [issue["code"] for issue in result.issues],
        )
        self.assertNotEqual(result.quality_label, "red")

    def test_does_not_use_low_confidence_degree_cover_title(self) -> None:
        lines = """# 硕士学位论文

论文题目：另一项完全不同的研究

## 第一章 绪论

正文内容。
""".splitlines()

        result = build_document_map("目标论文", lines)

        self.assertIn(
            "parse.document_identity_mismatch",
            [issue["code"] for issue in result.issues],
        )
        self.assertEqual(result.quality_label, "red")

    def test_ignores_single_character_ocr_h1s_as_document_boundaries(self) -> None:
        lines = """# 工

来源信息。

# 三

一、研究背景

正文内容。

二、结论

主要结论。
""".splitlines()

        result = build_document_map("三峡工程研究", lines)

        self.assertIsNotNone(result.selected_segment)
        self.assertNotIn("parse.document_scope_ambiguous", [issue["code"] for issue in result.issues])
        self.assertIn("parse.no_h1", [issue["code"] for issue in result.issues])

    def test_uses_requested_title_when_multi_h1_match_is_fuzzy(self) -> None:
        lines = """# 基于 的三峡船舶积压疏导策略效果研究

正文。

# Research on ship backlog based on Arena

English abstract.
""".splitlines()

        result = build_document_map("基于Arena的三峡船舶积压疏导策略效果研究", lines)

        self.assertEqual(result.paper_title, "基于Arena的三峡船舶积压疏导策略效果研究")
        self.assertIn("parse.document_title_fuzzy_match", [issue["code"] for issue in result.issues])

    def test_merges_bilingual_title_front_matter_before_body(self) -> None:
        lines = """# 三峡葛洲坝联合调度模型

摘要：中文摘要。

关键词：联合调度

# Co-scheduling Model of the Three Gorges and Gezhouba Dams

Abstract: English abstract.

Key words: co-scheduling

## 1 引言

正文内容。

## 2 模型

模型内容。
""".splitlines()

        result = build_document_map("三峡葛洲坝联合调度模型", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertEqual(result.selected_segment.companion_h1_lines, (7,))
        self.assertIn(
            "parse.bilingual_title_scope_merged",
            [issue["code"] for issue in result.issues],
        )
        self.assertEqual(
            [
                region.heading_path
                for region in result.regions
                if region.role == "body" and region.heading_path
            ],
            [("1 引言",), ("2 模型",)],
        )
        companion_assignment = next(
            row for row in result.line_ledger if row.line_number == 7
        )
        self.assertEqual(companion_assignment.role, "scope_marker")

    def test_does_not_merge_two_articles_when_first_segment_has_body(self) -> None:
        lines = """# 第一篇论文

摘要：第一篇摘要。

## 1 引言

第一篇正文。

# Second Paper

Abstract: second abstract.

## 1 Introduction

Second body.
""".splitlines()

        result = build_document_map("第一篇论文", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, 8)
        self.assertNotIn(
            "parse.bilingual_title_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_merges_bilingual_scope_when_english_abstract_uses_html(self) -> None:
        lines = """# 三峡联合调度快速算法

摘要：中文摘要。

关键词：联合调度

# Fast Algorithm for Co-scheduling

<sup>Abstract：</sup>English abstract.

Key words: co-scheduling

## 0 引言

正文内容。
""".splitlines()

        result = build_document_map("三峡联合调度快速算法", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertIn(
            "parse.bilingual_title_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_merges_adjacent_ocr_title_fragments_by_combined_identity(self) -> None:
        lines = """# 三 峡临时船问

# 友其改建冲沙问议计

摘要：论文摘要。

## 1 工程设计

正文内容。
""".splitlines()

        result = build_document_map("三峡临时船闸及其改建冲沙闸设计", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.paper_title, "三峡临时船闸及其改建冲沙闸设计")
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertEqual(result.selected_segment.companion_h1_lines, (3,))
        self.assertIn(
            "parse.fragmented_title_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_rejects_adjacent_h1_fragments_without_combined_identity_match(self) -> None:
        lines = """# 无关碎片一

# 无关碎片二

## 1 正文

正文内容。
""".splitlines()

        result = build_document_map("三峡临时船闸及其改建冲沙闸设计", lines)

        self.assertIsNone(result.selected_segment)
        self.assertIn(
            "parse.document_scope_ambiguous",
            [issue["code"] for issue in result.issues],
        )

    def test_merges_empty_exact_title_with_adjacent_body_segment(self) -> None:
        lines = """# 三峡工程施工通航期淤积问题的试验研究

# Experimental Study on Sediment Problems of Navigation

作者信息。

摘要：论文摘要。

## 0 引言

正文内容。
""".splitlines()

        result = build_document_map("三峡工程施工通航期淤积问题的试验研究", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertEqual(result.selected_segment.companion_h1_lines, (3,))
        self.assertIn(
            "parse.empty_title_companion_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_merges_empty_title_across_abstract_companion_to_body(self) -> None:
        lines = """# 目标论文

# Foreign Title

## 摘要

摘要正文。

# Foreign Title

## 1 绪论

正文。
""".splitlines()

        result = build_document_map("目标论文", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertEqual(result.selected_segment.companion_h1_lines, (3, 9))
        self.assertIn(
            "parse.empty_title_companion_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_merges_fragmented_exact_title_without_structured_body(self) -> None:
        lines = """# 优化三峡临时船闸通航方式

# 单向运行 定时换向

短讯正文。
""".splitlines()

        result = build_document_map("单向运行 定时换向——优化三峡临时船闸通航方式", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertIn(
            "parse.fragmented_title_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_merges_exact_fragmented_title_across_ocr_column_content(self) -> None:
        lines = """# 客运翻坝货运过闸

双栏 OCR 提前插入的正文。

# 以航补航企稳货源

作者。

## 一、现状

后续正文。
""".splitlines()

        result = build_document_map("客运翻坝货运过闸 以航补航企稳货源", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertIn(
            "parse.fragmented_title_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_does_not_merge_low_confidence_empty_title_with_next_article(self) -> None:
        lines = """# 三峡工程施工通航期淤积研究

# 另一篇完整文章

## 1 引言

另一篇正文。
""".splitlines()

        result = build_document_map("完全不同的来源题名", lines)

        self.assertIsNone(result.selected_segment)
        self.assertNotIn(
            "parse.empty_title_companion_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_merges_high_confidence_abstract_front_matter_with_body_companion(self) -> None:
        lines = """# 目标论文

摘要：中文摘要。

关键词：测试

# Garbled Foreign Title

乱码外文摘要。

## 1 研究方法

正文。
""".splitlines()

        result = build_document_map("目标论文", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertEqual(result.selected_segment.companion_h1_lines, (7,))
        self.assertIn(
            "parse.bilingual_title_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_merges_fragmented_foreign_title_after_ocr_damaged_abstract(self) -> None:
        lines = """# 三峡水库航运效益分析

关键词：三峡水库；航运

摘，要，摘要被 OCR 损坏。

# Benefit analysis of navigation

# in the Three Gorges Reservoir

作者与外文摘要。

## 1 计算条件

正文。
""".splitlines()

        result = build_document_map("三峡水库航运效益分析", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertEqual(result.selected_segment.companion_h1_lines, (7, 9))
        self.assertIn(
            "parse.bilingual_title_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_merges_foreign_title_after_bracketed_front_matter_labels(self) -> None:
        lines = """# 目标论文

【摘 要】中文摘要。

[关键词]三峡；航运

# Foreign title

Abstract: English abstract.

## 1 引言

正文。
""".splitlines()

        result = build_document_map("目标论文", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.end_line, len(lines))
        self.assertEqual(result.selected_segment.companion_h1_lines, (7,))
        self.assertIn(
            "parse.bilingual_title_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_does_not_merge_low_confidence_abstract_with_next_article(self) -> None:
        lines = """# 近似题名

摘要：第一篇摘要。

# 另一篇文章

## 1 正文

第二篇正文。
""".splitlines()

        result = build_document_map("完全不同的目标论文", lines)

        self.assertNotIn(
            "parse.bilingual_title_scope_merged",
            [issue["code"] for issue in result.issues],
        )

    def test_selects_exact_h2_article_from_mixed_heading_level_bundle(self) -> None:
        lines = """## 三峡永久船闸水力学问题研究

目标文章正文。

## 同步辐射光电子谱研究

无关文章正文。

# Au 在低温 PVF 衬底上的分形生长

另一篇无关文章正文。
""".splitlines()

        result = build_document_map("三峡永久船闸水力学问题研究", lines)

        self.assertIsNotNone(result.selected_segment)
        assert result.selected_segment is not None
        self.assertEqual(result.selected_segment.title_level, 2)
        self.assertEqual(result.selected_segment.start_line, 1)
        self.assertEqual(result.selected_segment.end_line, 4)
        self.assertNotEqual(result.quality_label, "red")
        self.assertIn(
            "parse.document_title_non_h1_match",
            [issue["code"] for issue in result.issues],
        )
        self.assertNotIn(
            "无关文章正文。",
            "\n".join(
                lines[region.span.start_line - 1]
                for region in result.regions
            ),
        )

    def test_rejects_duplicate_exact_h2_article_titles(self) -> None:
        lines = """## 目标论文

第一份正文。

## 目标论文

第二份正文。

# 无关论文

无关正文。
""".splitlines()

        result = build_document_map("目标论文", lines)

        self.assertEqual(result.quality_label, "red")
        self.assertIn(
            "parse.document_identity_mismatch",
            [issue["code"] for issue in result.issues],
        )


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

    def test_keeps_unnumbered_markdown_headings_as_structure_boundaries(self) -> None:
        lines = """# 论文

## 引言

研究背景。

## 仿真模型构建

模型正文。

## 2.1 参数设置

参数正文。
""".splitlines()

        result = build_document_map("论文", lines)

        self.assertEqual(
            [heading.raw_title for heading in result.headings],
            ["引言", "仿真模型构建", "2.1 参数设置"],
        )
        self.assertEqual([heading.depth for heading in result.headings], [1, 1, 2])

    def test_promotes_plain_numbered_headings_only_as_a_sequence(self) -> None:
        lines = """# 论文

1 引言

研究背景。

2 方法

研究方法。

3 结论

主要结论。
""".splitlines()

        result = build_document_map("论文", lines)

        self.assertEqual([heading.raw_title for heading in result.headings], ["1 引言", "2 方法", "3 结论"])
        self.assertTrue(all(heading.source == "plain" for heading in result.headings))
        self.assertIn("parse.inferred_headings", [issue["code"] for issue in result.issues])
        self.assertEqual(result.quality_label, "silver")

    def test_does_not_promote_isolated_year_sentence_to_heading(self) -> None:
        lines = """# 论文

## 第1章 绪论

2000 年，Yu 和 Li 提出了一个模型：

公式正文。

## 第2章 方法

方法正文。
""".splitlines()

        result = build_document_map("论文", lines)

        self.assertNotIn(
            "2000 年，Yu 和 Li 提出了一个模型：",
            [item.raw_title for item in result.headings],
        )

    def test_does_not_promote_numbered_entries_inside_explicit_toc(self) -> None:
        lines = """# 学位论文

## 目录

第一章 绪论....1

第二章 方法....8

第三章 结论....20

## 第一章 绪论

正文内容。
""".splitlines()

        result = build_document_map("学位论文", lines)

        self.assertEqual(
            [heading.raw_title for heading in result.headings],
            ["目录", "第一章 绪论"],
        )


class RegionTests(unittest.TestCase):
    def test_line_ledger_classifies_every_nonempty_line_exactly_once(self) -> None:
        lines = """# 论文

作者，某某大学

摘要：摘要正文。

关键词：船闸；排队

没有标题的引言正文不得遗漏。

## 1 方法

方法正文。
""".splitlines()

        result = build_document_map("论文", lines)
        ledger = {item.line_number: item for item in result.line_ledger}
        nonempty_lines = {index for index, line in enumerate(lines, start=1) if line.strip()}

        self.assertEqual(set(ledger), nonempty_lines)
        self.assertTrue(all(item.assignment_count == 1 for item in ledger.values()))
        self.assertEqual(ledger[1].role, "scope_marker")
        self.assertEqual(ledger[9].role, "body")
        self.assertEqual(ledger[11].role, "scope_marker")
        self.assertNotEqual(result.quality_label, "red")

    def test_recognizes_ocr_interrupted_abstract_and_keyword_labels(self) -> None:
        lines = """# 论文

摘 要: 中文摘要。

中文摘要续段。

关键词: 三峡；物流

中图分类号: F127

Abstr act: English abstract.

Key wor ds: shipping; logistics

## 1 正文

正文内容。
""".splitlines()

        result = build_document_map("论文", lines)
        roles = {item.line_number: item.role for item in result.line_ledger}

        self.assertEqual(roles[3], "abstract")
        self.assertEqual(roles[5], "abstract")
        self.assertEqual(roles[7], "keywords")
        self.assertEqual(roles[9], "front_matter")
        self.assertEqual(roles[11], "abstract")
        self.assertEqual(roles[13], "keywords")
        self.assertEqual(roles[15], "scope_marker")
        self.assertEqual(roles[17], "body")

    def test_plain_spaced_reference_heading_starts_back_matter(self) -> None:
        lines = """# 论文

## 1 结果

正文证据。

参 考 文 献

[1] 禁止进入材料。
""".splitlines()

        result = build_document_map("论文", lines)
        back = next(region for region in result.regions if region.role == "back_matter")

        self.assertEqual(back.span.start_line, 7)
        self.assertEqual({item.role for item in result.line_ledger if item.line_number >= 7}, {"back_matter"})

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
                abstract = next(region for region in result.regions if region.role == "abstract")
                self.assertTrue(abstract.included_in_materials)
                body_end = max(region.span.end_line for region in result.regions if region.role == "body")
                back_start = min(region.span.start_line for region in result.regions if region.role == "back_matter")
                self.assertLess(body_end, back_start)

    def test_exposes_unclassified_front_matter_without_including_it(self) -> None:
        lines = """# 论文

张三，某某大学交通学院

## 1、研究方法

这是正文。
""".splitlines()

        result = build_document_map("论文", lines)

        front = next(region for region in result.regions if region.role == "front_matter")
        self.assertFalse(front.included_in_materials)
        self.assertEqual((front.span.start_line, front.span.end_line), (3, 3))
        self.assertIn("parse.unclassified_front_matter", [issue["code"] for issue in result.issues])
        self.assertEqual(result.quality_label, "silver")

    def test_marks_unheaded_text_as_unstructured_body(self) -> None:
        lines = """# 论文

这是一篇没有章节标题的短文正文。

## 参考文献

[1] 参考文献内容。
""".splitlines()

        result = build_document_map("论文", lines)

        body = next(region for region in result.regions if region.role == "body")
        back = next(region for region in result.regions if region.role == "back_matter")
        self.assertTrue(body.included_in_materials)
        self.assertLess(body.span.end_line, back.span.start_line)
        self.assertIn("parse.no_structured_body", [issue["code"] for issue in result.issues])

    def test_recognizes_inline_abstract_without_colon_and_spaced_keywords(self) -> None:
        lines = """# 论文

张三，某某大学

摘要 本文研究三峡船舶积压问题。

关 键 词 ：船闸；排队

## 引言

正文内容。
""".splitlines()

        result = build_document_map("论文", lines)

        abstract = next(region for region in result.regions if region.role == "abstract")
        keywords = next(region for region in result.regions if region.role == "keywords")
        body = next(region for region in result.regions if region.role == "body")
        self.assertEqual((abstract.span.start_line, abstract.span.end_line), (5, 5))
        self.assertEqual((keywords.span.start_line, keywords.span.end_line), (7, 7))
        self.assertEqual(body.span.start_line, 9)

    def test_keeps_unheaded_body_after_inline_keywords(self) -> None:
        lines = """# Paper

Abstract: This paper studies ship backlog.

Keywords: ship lock; queue.

The body discusses methods, results, and limitations.
""".splitlines()

        result = build_document_map("paper", lines)

        abstract = next(region for region in result.regions if region.role == "abstract")
        keywords = next(region for region in result.regions if region.role == "keywords")
        body = next(region for region in result.regions if region.role == "body")
        self.assertEqual((abstract.span.start_line, abstract.span.end_line), (3, 3))
        self.assertEqual((keywords.span.start_line, keywords.span.end_line), (5, 5))
        self.assertEqual((body.span.start_line, body.span.end_line), (7, 7))


if __name__ == "__main__":
    unittest.main()
