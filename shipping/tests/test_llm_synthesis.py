from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_sections import AnalysisSection, SectionMap
from shipping_pipeline.llm_synthesis import (
    PAPER_SYNTHESIS_SYSTEM_PROMPT,
    PAPER_ROOT_SECTION_ID,
    assign_evidence_to_sections,
    build_final_evidence_coverage,
)


def _section_map() -> SectionMap:
    return SectionMap(
        paper_id="paper-one",
        generation_id="g1",
        sections=(
            AnalysisSection("chapter:1", "第1章", 1, 1, 9, None, ()),
            AnalysisSection("chapter:1/section:1.1", "1.1", 2, 10, 19, "chapter:1", ("m1",)),
            AnalysisSection("chapter:1/section:1.2", "1.2", 2, 20, 29, "chapter:1", ("m2",)),
            AnalysisSection("chapter:2", "第2章", 1, 30, 39, None, ("m3",)),
        ),
        material_to_section={
            "m1": "chapter:1/section:1.1",
            "m2": "chapter:1/section:1.2",
            "m3": "chapter:2",
        },
        source_sha256="sha256:test",
    )


def _unit(evidence_id: str, material_ids: list[str]) -> dict:
    return {
        "evidence_unit_id": evidence_id,
        "citations": [{"material_id": material_id, "quote": "证据"} for material_id in material_ids],
    }


class LLMSynthesisTests(unittest.TestCase):
    def test_paper_prompt_requires_empty_unresolved_questions_without_direct_evidence(self) -> None:
        self.assertIn(
            "没有证据明确陈述开放问题时，unresolved_questions 必须输出空数组",
            PAPER_SYNTHESIS_SYSTEM_PROMPT,
        )
        self.assertIn("一条只表达一个可核验命题", PAPER_SYNTHESIS_SYSTEM_PROMPT)
        self.assertIn("不能把“小船”具体化为证据未列出的吨位", PAPER_SYNTHESIS_SYSTEM_PROMPT)
        self.assertIn("不得追加“影响全面性”“精确度受限”等评价", PAPER_SYNTHESIS_SYSTEM_PROMPT)

    def test_evidence_uses_lca_and_cross_root_evidence_moves_to_paper_root(self) -> None:
        assignment = assign_evidence_to_sections(
            [_unit("same-root", ["m1", "m2"]), _unit("cross-root", ["m1", "m3"])],
            _section_map(),
        )

        self.assertEqual(assignment.evidence_to_section["same-root"], "chapter:1")
        self.assertEqual(assignment.evidence_to_section["cross-root"], PAPER_ROOT_SECTION_ID)
        self.assertEqual(set(assignment.cross_section_evidence_ids), {"same-root", "cross-root"})

    def test_final_coverage_is_complete_and_disjoint(self) -> None:
        analysis = {
            "evidence_dispositions": [
                {"evidence_unit_id": "e1", "disposition": "used"},
                {"evidence_unit_id": "e2", "disposition": "excluded"},
            ]
        }
        coverage = build_final_evidence_coverage(analysis, {"e1", "e2"})

        self.assertEqual(coverage["used_in_final_analysis_ids"], ["e1"])
        self.assertEqual(coverage["not_used_in_final_analysis_ids"], ["e2"])
        self.assertEqual(coverage["unresolved_evidence_unit_ids"], [])


if __name__ == "__main__":
    unittest.main()
