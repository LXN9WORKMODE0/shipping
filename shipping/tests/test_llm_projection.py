from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_projection import ProjectionError, project_card, project_cards


def full_material(material_id: str = "m1") -> dict:
    return {
        "schema_version": "material.v2",
        "material_id": material_id,
        "material_type": "evidence_card",
        "paper_id": "paper-one",
        "paper_title": "论文一",
        "content_kind": "prose",
        "heading_path": ["一、研究方法", "1.1 数据"],
        "clean_title": "研究方法（片段 1/2）",
        "raw_title": "研究方法",
        "extract": "本文采用仿真方法分析船舶积压。",
        "summary": "重复摘要不应进入模型。",
        "lead_excerpt": "重复前导文本不应进入模型。",
        "source_span": {"path": "normalized/document.md", "start_line": 10, "end_line": 12},
        "source_fingerprint": "sha256:abc",
        "generation_id": "generation-one",
        "confidence_flags": ["parse.a", "shared"],
        "quality_flags": ["quality.b", "shared"],
    }


class LLMProjectionTests(unittest.TestCase):
    def test_project_card_only_exposes_model_fields(self) -> None:
        projected = project_card(full_material())

        self.assertEqual(
            set(projected),
            {"material_id", "content_kind", "heading_path", "title", "extract", "parse_flags"},
        )
        self.assertEqual(projected["parse_flags"], ["parse.a", "shared", "quality.b"])
        self.assertEqual(projected["heading_path"], ["一、研究方法", "1.1 数据"])
        for hidden in (
            "source_span",
            "source_fingerprint",
            "generation_id",
            "summary",
            "lead_excerpt",
        ):
            self.assertNotIn(hidden, projected)

    def test_project_cards_preserves_order_and_rejects_duplicate_ids(self) -> None:
        projected = project_cards([full_material("m2"), full_material("m1")])
        self.assertEqual([row["material_id"] for row in projected], ["m2", "m1"])

        with self.assertRaisesRegex(ProjectionError, "重复 material_id"):
            project_cards([full_material("m1"), full_material("m1")])

    def test_invalid_required_text_and_container_types_are_rejected(self) -> None:
        cases = {
            "extract": ("extract", ""),
            "material_id": ("material_id", ""),
            "content_kind": ("content_kind", None),
            "heading_path": ("heading_path", "section"),
            "confidence_flags": ("confidence_flags", "parse.a"),
        }
        for name, (field, value) in cases.items():
            with self.subTest(name=name):
                material = full_material()
                material[field] = value
                with self.assertRaises(ProjectionError):
                    project_card(material)


if __name__ == "__main__":
    unittest.main()
