from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_model_profile import load_model_profile
from shipping_pipeline.llm_planner import plan_paper_analysis
from shipping_pipeline.llm_sections import build_section_map
from shipping_pipeline.llm_tokenizer import DeepSeekV4TokenCounter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT_ROOT / "workspace"
PROFILE = load_model_profile(PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json")


def _counter() -> DeepSeekV4TokenCounter:
    return DeepSeekV4TokenCounter(PROFILE, PROJECT_ROOT / ".model_cache")


def _material(index: int, count: int) -> dict:
    return {
        "material_id": f"paper-small:card-{index:03d}",
        "paper_id": "paper-small",
        "paper_title": "小型论文",
        "generation_id": "generation-small",
        "content_kind": "prose",
        "heading_path": [f"第{index}节"],
        "clean_title": f"第{index}节",
        "extract": f"第{index}张材料给出研究方法和结论。" + "研究数据" * (20 + count),
        "confidence_flags": [],
        "quality_flags": [],
        "source_span": {"start_line": index * 2, "end_line": index * 2 + 1},
    }


def _read_real(paper_id: str) -> tuple[list[dict], str, Path]:
    paper_dir = WORKSPACE / paper_id
    current = json.loads((paper_dir / "materials" / "current.json").read_text(encoding="utf-8-sig"))
    materials_path = paper_dir / "materials" / current["materials_path"]
    materials = [
        json.loads(line) for line in materials_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()
    ]
    return materials, current["generation_id"], paper_dir / "structure" / "structure.json"


class LLMPlannerTests(unittest.TestCase):
    def test_small_and_medium_papers_are_one_full_paper_batch_without_reading_structure(self) -> None:
        counter = _counter()
        missing_structure = PROJECT_ROOT / ".test_tmp" / "must-not-be-read.json"
        for count in (3, 19, 23):
            with self.subTest(count=count):
                materials = [_material(index, count) for index in range(1, count + 1)]
                plan = plan_paper_analysis(
                    paper_id="paper-small",
                    paper_title="小型论文",
                    topic="船舶运输",
                    generation_id="generation-small",
                    materials=materials,
                    profile=PROFILE,
                    token_counter=counter,
                    structure_path=missing_structure,
                )

                self.assertEqual(plan.scope, "full_paper")
                self.assertEqual(plan.strategy, "single_evidence_batch")
                self.assertEqual(len(plan.evidence_batches), 1)
                self.assertEqual(
                    plan.evidence_batches[0].material_ids,
                    tuple(row["material_id"] for row in materials),
                )

    def test_real_large_papers_split_only_inside_one_paper_and_one_root_section(self) -> None:
        counter = _counter()
        for paper_id in (
            "长江上游地区产业布局及航运适应性研究",
            "考虑翻坝和天气的长江班轮运网鲁棒优化模型",
        ):
            with self.subTest(paper_id=paper_id):
                materials, generation_id, structure_path = _read_real(paper_id)
                plan = plan_paper_analysis(
                    paper_id=paper_id,
                    paper_title=paper_id,
                    topic="长江航运与物流",
                    generation_id=generation_id,
                    materials=materials,
                    profile=PROFILE,
                    token_counter=counter,
                    structure_path=structure_path,
                )
                section_map = build_section_map(
                    structure_path,
                    materials,
                    paper_id=paper_id,
                    generation_id=generation_id,
                )
                sections = {section.section_id: section for section in section_map.sections}

                self.assertEqual(plan.strategy, "section_evidence_batches")
                self.assertGreater(len(plan.evidence_batches), 1)
                self.assertEqual(
                    tuple(material_id for batch in plan.evidence_batches for material_id in batch.material_ids),
                    tuple(row["material_id"] for row in materials),
                )
                for batch in plan.evidence_batches:
                    self.assertTrue(batch.split_reason)
                    self.assertLessEqual(
                        len(batch.material_ids),
                        PROFILE.large_paper_evidence_batch_max_cards,
                    )
                    self.assertLessEqual(
                        batch.input_tokens + batch.max_output_tokens + batch.safety_margin_tokens,
                        PROFILE.context_window_tokens,
                    )
                    roots = set()
                    for material_id in batch.material_ids:
                        section_id = section_map.material_to_section[material_id]
                        while sections[section_id].parent_section_id is not None:
                            section_id = str(sections[section_id].parent_section_id)
                        roots.add(section_id)
                    self.assertEqual(len(roots), 1)

    def test_request_and_plan_ids_are_stable(self) -> None:
        materials = [_material(index, 3) for index in range(1, 4)]
        kwargs = {
            "paper_id": "paper-small",
            "paper_title": "小型论文",
            "topic": "船舶运输",
            "generation_id": "generation-small",
            "materials": materials,
            "profile": PROFILE,
            "token_counter": _counter(),
        }

        first = plan_paper_analysis(**kwargs)
        second = plan_paper_analysis(**kwargs)

        self.assertEqual(first.plan_sha256, second.plan_sha256)
        self.assertEqual(first.evidence_batches[0].request_id, second.evidence_batches[0].request_id)
        self.assertNotIn("chars", json.dumps(first.to_dict(), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
