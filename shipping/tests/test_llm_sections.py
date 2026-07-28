from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_sections import SectionStructureError, build_section_map


WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
LARGE_PAPERS = (
    "长江上游地区产业布局及航运适应性研究",
    "考虑翻坝和天气的长江班轮运网鲁棒优化模型",
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _real_inputs(paper_id: str) -> tuple[Path, list[dict], str]:
    paper_dir = WORKSPACE / paper_id
    current = _read_json(paper_dir / "materials" / "current.json")
    materials = _read_jsonl(paper_dir / "materials" / current["materials_path"])
    return paper_dir / "structure" / "structure.json", materials, current["generation_id"]


class LLMSectionTests(unittest.TestCase):
    def test_real_large_papers_have_complete_nonoverlapping_section_partitions(self) -> None:
        for paper_id in LARGE_PAPERS:
            with self.subTest(paper_id=paper_id):
                structure_path, materials, generation_id = _real_inputs(paper_id)
                section_map = build_section_map(
                    structure_path,
                    materials,
                    paper_id=paper_id,
                    generation_id=generation_id,
                )

                self.assertEqual(set(section_map.material_to_section), {row["material_id"] for row in materials})
                self.assertEqual(sum(len(section.material_ids) for section in section_map.sections), len(materials))
                ordered = sorted(section_map.sections, key=lambda section: section.start_line)
                self.assertTrue(
                    all(left.end_line < right.start_line for left, right in zip(ordered, ordered[1:]))
                )
                chapters = [section for section in ordered if section.level == 1 and section.section_id.startswith("chapter:")]
                self.assertGreaterEqual(len(chapters), 6)
                self.assertTrue(all("章" in section.title or section.title.lower().startswith("chapter") for section in chapters))
                self.assertFalse(any(section.title in {"1. 重庆", "(1) 港口结构不同", "阶段一：交通的发生与吸引"} for section in ordered))

    def test_structure_without_explicit_chapter_anchor_is_rejected(self) -> None:
        structure = {
            "paper_id": "paper-one",
            "selected_segment": {"start_line": 1, "end_line": 30},
            "headings": [
                {"title_norm": "1. 重庆", "depth": 1, "start_line": 1, "ordinal_path": [1]},
                {"title_norm": "2. 四川", "depth": 1, "start_line": 10, "ordinal_path": [2]},
                {"title_norm": "Step 1", "depth": 1, "start_line": 20, "ordinal_path": []},
            ],
        }
        material = {
            "material_id": "m1",
            "paper_id": "paper-one",
            "generation_id": "g1",
            "source_span": {"start_line": 2, "end_line": 3},
        }

        with self.assertRaisesRegex(SectionStructureError, "planning.section_structure_unreliable"):
            build_section_map(structure, [material], paper_id="paper-one", generation_id="g1")

    def test_card_crossing_an_accepted_section_boundary_is_rejected(self) -> None:
        structure = {
            "paper_id": "paper-one",
            "selected_segment": {"start_line": 1, "end_line": 30},
            "headings": [
                {"title_norm": "第1章 绪论", "depth": 1, "start_line": 1, "ordinal_path": [1]},
                {"title_norm": "1.1 方法", "depth": 2, "start_line": 10, "ordinal_path": [1, 1]},
                {"title_norm": "1.2 结果", "depth": 2, "start_line": 20, "ordinal_path": [1, 2]},
            ],
        }
        material = {
            "material_id": "m1",
            "paper_id": "paper-one",
            "generation_id": "g1",
            "source_span": {"start_line": 18, "end_line": 21},
        }

        with self.assertRaisesRegex(SectionStructureError, "跨越章节边界"):
            build_section_map(structure, [material], paper_id="paper-one", generation_id="g1")


if __name__ == "__main__":
    unittest.main()
