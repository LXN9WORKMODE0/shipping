from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_evidence_gate_stability import (
    build_evidence_claim_confirmation_context,
    build_evidence_claim_gate_decisions,
    effective_evidence_claim_reviews,
    has_ordered_verbatim_quote_support,
    plan_evidence_claim_review_batches,
)
from shipping_pipeline.llm_sections import AnalysisSection, SectionMap


def _evidence(index: int, material_ids: list[str]) -> dict:
    return {
        "evidence_unit_id": f"evidence_{index:024x}",
        "claim": f"观点{index}",
        "citations": [
            {"material_id": material_id, "quote": f"引文{index}"}
            for material_id in material_ids
        ],
        "confidence": "high",
        "caveats": [],
    }


def _review(evidence_id: str, verdict: str) -> dict:
    return {
        "evidence_unit_id": evidence_id,
        "verdict": verdict,
        "reason": "测试结论。",
        "unsupported_fragments": [] if verdict == "directly_supported" else ["观点"],
    }


class EvidenceClaimGateStabilityTests(unittest.TestCase):
    def test_planner_groups_by_section_and_caps_each_batch(self) -> None:
        section_map = SectionMap(
            paper_id="paper",
            generation_id="generation",
            sections=(
                AnalysisSection("chapter:1", "第一章", 1, 1, 20, None, ("m1", "m2")),
                AnalysisSection("chapter:2", "第二章", 1, 21, 40, None, ("m3", "m4")),
            ),
            material_to_section={"m1": "chapter:1", "m2": "chapter:1", "m3": "chapter:2", "m4": "chapter:2"},
            source_sha256="sha256:test",
        )
        evidence_units = [
            _evidence(1, ["m1"]),
            _evidence(2, ["m2"]),
            _evidence(3, ["m3"]),
            _evidence(4, ["m4"]),
            _evidence(5, ["m1", "m3"]),
        ]

        batches = plan_evidence_claim_review_batches(
            evidence_units,
            section_map=section_map,
            max_batch_size=1,
        )

        self.assertEqual(
            [(row.section_id, row.evidence_unit_ids) for row in batches],
            [
                ("chapter:1", (evidence_units[0]["evidence_unit_id"],)),
                ("chapter:1", (evidence_units[1]["evidence_unit_id"],)),
                ("chapter:2", (evidence_units[2]["evidence_unit_id"],)),
                ("chapter:2", (evidence_units[3]["evidence_unit_id"],)),
                ("paper:root", (evidence_units[4]["evidence_unit_id"],)),
            ],
        )

    def test_confirmation_context_contains_only_cited_and_adjacent_cards(self) -> None:
        materials = [
            {
                "material_id": f"m{index}",
                "order": index,
                "clean_title": f"标题{index}",
                "heading_path": [f"第{index}节"],
                "extract": f"原文{index}",
            }
            for index in range(1, 6)
        ]
        evidence = _evidence(1, ["m3"])
        first = _review(evidence["evidence_unit_id"], "partially_supported")

        context = build_evidence_claim_confirmation_context(
            target_evidence_unit=evidence,
            first_pass_review=first,
            materials=materials,
        )

        self.assertEqual(
            [row["material_id"] for row in context["context_cards"]],
            ["m2", "m3", "m4"],
        )
        self.assertEqual(
            [row["is_cited"] for row in context["context_cards"]],
            [False, True, False],
        )

    def test_decision_ledger_distinguishes_confirmed_and_unstable_blockers(self) -> None:
        first_reviews = [
            _review("evidence_000000000000000000000001", "directly_supported"),
            _review("evidence_000000000000000000000002", "partially_supported"),
            _review("evidence_000000000000000000000003", "unsupported"),
        ]
        confirmations = {
            first_reviews[1]["evidence_unit_id"]: _review(
                first_reviews[1]["evidence_unit_id"], "partially_supported"
            ),
            first_reviews[2]["evidence_unit_id"]: _review(
                first_reviews[2]["evidence_unit_id"], "directly_supported"
            ),
        }

        decisions = build_evidence_claim_gate_decisions(
            first_pass_reviews=first_reviews,
            confirmation_reviews=confirmations,
            first_request_ids={row["evidence_unit_id"]: "first" for row in first_reviews},
            confirmation_request_ids={key: "confirmation" for key in confirmations},
            accepted_override_ids=set(),
            accepted_override_reviews={},
        )

        self.assertEqual(
            [row["decision"] for row in decisions],
            ["passed_first_pass", "confirmed_blocker", "gate_disagreement"],
        )
        effective = effective_evidence_claim_reviews(decisions)
        self.assertEqual(effective[1], confirmations[first_reviews[1]["evidence_unit_id"]])
        self.assertEqual(effective[2], first_reviews[2])

    def test_ordered_verbatim_support_accepts_omitted_details_in_one_quote(self) -> None:
        evidence = _evidence(1, ["m1"])
        evidence["claim"] = "郭家沱码头道路条件欠佳，不能完全满足重载车辆运输需要。"
        evidence["citations"][0]["quote"] = (
            "郭家沱码头道路条件欠佳，坡度大，弯道急，不能完全满足重载车辆运输需要。"
        )

        self.assertTrue(has_ordered_verbatim_quote_support(evidence))

    def test_ordered_verbatim_support_rejects_removed_qualifier(self) -> None:
        evidence = _evidence(1, ["m1"])
        evidence["claim"] = "建议三地建立一体化信息网络，实行联网。"
        evidence["citations"][0]["quote"] = "建议三地考虑建立一体化信息网络，实行联网。"

        self.assertFalse(has_ordered_verbatim_quote_support(evidence))

    def test_ordered_verbatim_support_rejects_qualifier_before_first_clause(self) -> None:
        evidence = _evidence(1, ["m1"])
        evidence["claim"] = "运输需求增长。"
        evidence["citations"][0]["quote"] = "研究预计运输需求增长。"

        self.assertFalse(has_ordered_verbatim_quote_support(evidence))

    def test_deterministic_quote_support_becomes_effective_direct_review(self) -> None:
        evidence_id = "evidence_000000000000000000000001"
        first = _review(evidence_id, "partially_supported")
        decisions = build_evidence_claim_gate_decisions(
            first_pass_reviews=[first],
            confirmation_reviews={},
            first_request_ids={evidence_id: "first"},
            confirmation_request_ids={},
            accepted_override_ids=set(),
            accepted_override_reviews={},
            deterministic_quote_support_ids={evidence_id},
        )

        self.assertEqual(decisions[0]["decision"], "passed_quote_containment")
        effective = effective_evidence_claim_reviews(decisions)
        self.assertEqual(effective[0]["verdict"], "directly_supported")
        self.assertEqual(effective[0]["unsupported_fragments"], [])


if __name__ == "__main__":
    unittest.main()
