from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_quotes import build_quote_candidates
from shipping_pipeline.llm_planner import build_evidence_prompts
from shipping_pipeline.llm_table_evidence import (
    build_table_numeric_scopes,
    build_table_percentage_composites,
    build_table_ranking_scopes,
)


def table_material(extract: str, *, content_kind: str = "table") -> dict:
    return {
        "material_id": "paper-one:table-001",
        "content_kind": content_kind,
        "extract": extract,
    }


class TableEvidenceTests(unittest.TestCase):
    def test_builds_percentage_composites_from_complete_header_and_row(self) -> None:
        material = table_material(
            "需求情况 | 公路运量(%) | 水路运量(%)\n模型M2 | 33.8 | 66.2"
        )
        candidates = build_quote_candidates([material])

        composites = build_table_percentage_composites(material, candidates)

        self.assertEqual(
            [(row.fact, row.row_label, row.column_label, row.column_index) for row in composites],
            [
                ("33.8%", "模型M2", "公路运量", 1),
                ("66.2%", "模型M2", "水路运量", 2),
            ],
        )
        self.assertTrue(all(row.header_quote_id != row.row_quote_id for row in composites))

    def test_rejects_mismatched_column_counts(self) -> None:
        material = table_material(
            "需求情况 | 公路运量(%) | 水路运量(%)\n模型M2 | 66.2"
        )
        candidates = build_quote_candidates([material])

        self.assertEqual(build_table_percentage_composites(material, candidates), [])

    def test_rejects_truncated_quote_rows(self) -> None:
        long_label = "超长场景" * 80
        material = table_material(
            "需求情况 | 公路运量(%) | 水路运量(%)\n"
            f"{long_label} | 33.8 | 66.2"
        )
        candidates = build_quote_candidates([material])

        self.assertEqual(build_table_percentage_composites(material, candidates), [])

    def test_rejects_non_table_card(self) -> None:
        material = table_material(
            "需求情况 | 公路运量(%) | 水路运量(%)\n模型M2 | 33.8 | 66.2",
            content_kind="prose",
        )
        candidates = build_quote_candidates([material])

        self.assertEqual(build_table_percentage_composites(material, candidates), [])

    def test_does_not_treat_percentage_data_row_as_a_header(self) -> None:
        material = table_material(
            "场景 | 公路占比(%) | 水路占比(%)\n"
            "方案A | 40% | 60%\n"
            "方案B | 35 | 65"
        )
        candidates = build_quote_candidates([material])
        actual_header = next(row["quote_id"] for row in candidates if "公路占比(%)" in row["text"])

        composites = build_table_percentage_composites(material, candidates)

        scheme_b = [row for row in composites if row.row_label == "方案B"]
        self.assertEqual({row.fact for row in scheme_b}, {"35%", "65%"})
        self.assertTrue(all(row.header_quote_id == actual_header for row in scheme_b))

    def test_does_not_reuse_header_across_blank_line(self) -> None:
        material = table_material(
            "场景 | 公路占比(%) | 水路占比(%)\n"
            "方案A | 40 | 60\n\n"
            "另一场景 | 指标甲 | 指标乙\n"
            "方案B | 44.4 | 55.6"
        )
        candidates = build_quote_candidates([material])

        composites = build_table_percentage_composites(material, candidates)

        self.assertEqual({row.fact for row in composites}, {"40%", "60%"})

    def test_builds_ranking_scope_from_complete_header_and_data_rows(self) -> None:
        material = table_material(
            "方案 | 目标值\n方案A | 10\n方案B | 20\n方案C | 15"
        )
        candidates = build_quote_candidates([material])
        quote_by_text = {row["text"]: row["quote_id"] for row in candidates}

        scopes = build_table_ranking_scopes(material, candidates)

        self.assertEqual(len(scopes), 1)
        self.assertEqual(scopes[0].header_quote_id, quote_by_text["方案 | 目标值"])
        self.assertEqual(
            scopes[0].data_row_quote_ids,
            (
                quote_by_text["方案A | 10"],
                quote_by_text["方案B | 20"],
                quote_by_text["方案C | 15"],
            ),
        )

    def test_builds_numeric_scope_from_two_level_header(self) -> None:
        material = table_material(
            "| 2007年 | 2008年 | 2009年 | 2010年\n"
            "南线 | 北线 | 合计 | 南线 | 北线 | 合计 | 南线 | 北线 | 合计 | 南线 | 北线 | 合计\n"
            "大风 | 46 | 46 | 92 | 46 | 52 | 98 | 5 | 13 | 18 | 50 | 84 | 134"
        )
        candidates = build_quote_candidates([material])
        quote_by_text = {row["text"]: row["quote_id"] for row in candidates}

        scopes = build_table_numeric_scopes(material, candidates)

        self.assertEqual(len(scopes), 1)
        self.assertEqual(
            scopes[0].header_quote_ids,
            (
                quote_by_text["| 2007年 | 2008年 | 2009年 | 2010年"],
                quote_by_text[
                    "南线 | 北线 | 合计 | 南线 | 北线 | 合计 | 南线 | 北线 | 合计 | 南线 | 北线 | 合计"
                ],
            ),
        )
        self.assertEqual(
            scopes[0].data_row_quote_ids,
            (
                quote_by_text[
                    "大风 | 46 | 46 | 92 | 46 | 52 | 98 | 5 | 13 | 18 | 50 | 84 | 134"
                ],
            ),
        )

    def test_evidence_prompt_exposes_all_required_two_level_headers(self) -> None:
        material = {
            **table_material(
                "| 2007年 | 2008年 | 2009年 | 2010年\n"
                "南线 | 北线 | 合计 | 南线 | 北线 | 合计 | 南线 | 北线 | 合计 | 南线 | 北线 | 合计\n"
                "合计 | 80 | 90 | 282 | 85 | 95 | 298 | 150 | 160 | 525 | 170 | 180 | 562"
            ),
            "title": "表格",
            "heading_path": ["分析", "停航天数"],
        }
        candidates = build_quote_candidates([material])
        quote_by_text = {row["text"]: row["quote_id"] for row in candidates}

        system_prompt, user_prompt = build_evidence_prompts(
            paper_id="paper-one",
            paper_title="示例论文",
            topic="三峡通航",
            projected_cards=[material],
            all_projected_cards=[material],
        )
        prompt_card = json.loads(user_prompt)["材料卡"][0]
        scope = prompt_card["表格数值引用范围"][0]

        self.assertIn("多级表头缺一不可", system_prompt)
        self.assertEqual(
            scope["required_header_quote_ids"],
            [
                quote_by_text["| 2007年 | 2008年 | 2009年 | 2010年"],
                quote_by_text[
                    "南线 | 北线 | 合计 | 南线 | 北线 | 合计 | 南线 | 北线 | 合计 | 南线 | 北线 | 合计"
                ],
            ],
        )
        self.assertEqual(
            scope["eligible_data_row_quote_ids"],
            [
                quote_by_text[
                    "合计 | 80 | 90 | 282 | 85 | 95 | 298 | 150 | 160 | 525 | 170 | 180 | 562"
                ]
            ],
        )


if __name__ == "__main__":
    unittest.main()
