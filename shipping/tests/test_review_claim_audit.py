from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.review_claim_audit_contracts import (
    ReviewClaimAuditError,
    build_claim_catalogue,
    build_chapter_claim_audit_schema,
    load_review_claim_audit_config,
    validate_chapter_claim_audit,
)
from shipping_pipeline.review_claim_audit_report import (
    render_claim_audit_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config" / "review-claim-audit-default.json"


class ReviewClaimAuditContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_review_claim_audit_config(CONFIG)
        self.chapter = {
            "section_index": 1,
            "paragraphs": [
                {
                    "paragraph_index": 1,
                    "text": "仿真结果显示效率提高10%。本节比较两种方法。",
                    "citation_keys": ["ref-a"],
                }
            ],
        }
        self.package = {
            "papers": [
                {
                    "paper_id": "paper-a",
                    "citation_key": "ref-a",
                    "full_markdown": "仿真结果显示效率提高10%。",
                    "cards": [
                        {
                            "material_id": "material-a",
                            "title": "仿真结果",
                            "extract": "仿真结果显示效率提高10%。",
                            "source": {"start_line": 10, "end_line": 10},
                        }
                    ],
                    "evidence_units": [
                        {
                            "evidence_unit_id": "evidence-a",
                            "statement": "仿真效率提高。",
                            "citations": [
                                {"quote": "仿真结果显示效率提高10%。"}
                            ],
                        }
                    ],
                }
            ]
        }
        self.catalogue = build_claim_catalogue(self.chapter)
        self.schema = build_chapter_claim_audit_schema(
            section_index=1,
            claim_count=len(self.catalogue),
            citation_keys=["ref-a"],
            config=self.config,
        )
        self.payload = {
            "schema_version": "llm.review_chapter_claim_audit.v3",
            "section_index": 1,
            "claim_audits": [
                {
                    "paragraph_index": 1,
                    "claim_index": 1,
                    "claim_type": "factual",
                    "importance": "core",
                    "status": "supported",
                    "reason": "Evidence直接支持且保留仿真限定。",
                    "citation_keys": ["ref-a"],
                    "source_assessments": [
                        {
                            "citation_key": "ref-a",
                            "source_tier": "evidence",
                            "reason": "已有直接Evidence。",
                        }
                    ],
                    "result_type_check": "accurate",
                    "validation_level_check": "accurate",
                },
                {
                    "paragraph_index": 1,
                    "claim_index": 2,
                    "claim_type": "navigation_synthesis",
                    "importance": "navigation",
                    "status": "not_applicable",
                    "reason": "仅说明本节结构。",
                    "citation_keys": [],
                    "source_assessments": [],
                    "result_type_check": "not_applicable",
                    "validation_level_check": "not_applicable",
                },
            ],
            "chapter_assessment": "核心事实有支持。",
        }

    def test_catalogue_is_deterministic_and_preserves_source_text(self):
        second = build_claim_catalogue(self.chapter)
        self.assertEqual(self.catalogue, second)
        self.assertEqual(
            [row["claim_text"] for row in self.catalogue],
            ["仿真结果显示效率提高10%。", "本节比较两种方法。"],
        )

    def test_valid_audit_reattaches_claim_text_and_evidence(self):
        result = self._validate(self.payload)
        claim = result["claim_audits"][0]
        self.assertEqual(claim["claim_text"], "仿真结果显示效率提高10%。")
        self.assertTrue(claim["claim_id"].startswith("claim_"))
        resolved = claim["source_assessments"][0]["resolved_sources"]
        self.assertEqual(resolved[0]["evidence_unit_id"], "evidence-a")

    def test_claim_order_must_match_program_catalogue(self):
        payload = copy.deepcopy(self.payload)
        payload["claim_audits"][0]["claim_index"] = 2
        with self.assertRaisesRegex(
            ReviewClaimAuditError,
            "audit.claim_order_invalid",
        ):
            self._validate(payload)

    def test_claim_cannot_add_paragraph_external_citation(self):
        package = copy.deepcopy(self.package)
        package["papers"].append(
            {
                "paper_id": "paper-b",
                "citation_key": "ref-b",
                "full_markdown": "其他论文。",
                "cards": [],
                "evidence_units": [],
            }
        )
        schema = build_chapter_claim_audit_schema(
            section_index=1,
            claim_count=2,
            citation_keys=["ref-a", "ref-b"],
            config=self.config,
        )
        payload = copy.deepcopy(self.payload)
        payload["claim_audits"][0]["citation_keys"] = ["ref-b"]
        payload["claim_audits"][0]["source_assessments"][0][
            "citation_key"
        ] = "ref-b"
        result = validate_chapter_claim_audit(
            payload,
            schema=schema,
            catalogue=self.catalogue,
            package=package,
        )
        claim = result["claim_audits"][0]
        self.assertEqual(claim["status"], "unsupported")
        self.assertIn(
            "citation_outside_paragraph_removed:ref-b",
            claim["normalization_flags"],
        )

    def test_supported_claim_requires_non_none_source_tier(self):
        payload = copy.deepcopy(self.payload)
        payload["claim_audits"][0]["source_assessments"][0][
            "source_tier"
        ] = "none"
        result = self._validate(payload)
        claim = result["claim_audits"][0]
        self.assertEqual(claim["status"], "unsupported")
        self.assertIn(
            "missing_source_tier_changed_to_unsupported",
            claim["normalization_flags"],
        )

    def test_unsupported_claim_cannot_claim_source_tier(self):
        payload = copy.deepcopy(self.payload)
        payload["claim_audits"][0]["status"] = "unsupported"
        result = self._validate(payload)
        claim = result["claim_audits"][0]
        self.assertEqual(
            claim["source_assessments"][0]["source_tier"],
            "none",
        )
        self.assertIn(
            "unsupported_source_tiers_cleared",
            claim["normalization_flags"],
        )

    def test_factual_navigation_importance_is_conservatively_normalized(self):
        payload = copy.deepcopy(self.payload)
        payload["claim_audits"][0]["importance"] = "navigation"
        result = self._validate(payload)
        claim = result["claim_audits"][0]
        self.assertEqual(claim["importance"], "supporting")
        self.assertIn(
            "factual_navigation_importance_changed_to_supporting",
            claim["normalization_flags"],
        )

    def test_review_synthesis_does_not_require_paper_source(self):
        payload = copy.deepcopy(self.payload)
        payload["claim_audits"][0].update(
            {
                "claim_type": "review_synthesis",
                "status": "not_applicable",
                "citation_keys": [],
                "source_assessments": [],
                "result_type_check": "not_applicable",
                "validation_level_check": "not_applicable",
            }
        )

        result = self._validate(payload)

        claim = result["claim_audits"][0]
        self.assertEqual(claim["claim_type"], "review_synthesis")
        self.assertEqual(claim["status"], "not_applicable")
        self.assertEqual(claim["source_assessments"], [])

    def test_review_synthesis_rejects_navigation_importance(self):
        payload = copy.deepcopy(self.payload)
        payload["claim_audits"][0].update(
            {
                "claim_type": "review_synthesis",
                "importance": "navigation",
                "status": "not_applicable",
                "citation_keys": [],
                "source_assessments": [],
                "result_type_check": "not_applicable",
                "validation_level_check": "not_applicable",
            }
        )

        with self.assertRaisesRegex(
            ReviewClaimAuditError,
            "audit.review_synthesis_contract_invalid",
        ):
            self._validate(payload)

    def test_human_report_limits_source_previews_without_changing_audit(self):
        audit = self._validate(self.payload)
        audit["claim_audits"][0]["status"] = "qualified"
        evidence_rows = audit["claim_audits"][0]["source_assessments"][0][
            "resolved_sources"
        ]
        evidence_rows.extend(
            {
                "evidence_unit_id": f"evidence-{index}",
                "statement": f"补充证据{index}",
                "citations": [{"quote": f"补充证据{index}"}],
            }
            for index in range(2, 6)
        )
        manifest = {
            "run_id": "audit-run",
            "writing_run_id": "writing-run",
            "status": "completed",
            "publishable": True,
            "summary": {
                "factual_claim_count": 1,
                "supported_claim_count": 1,
                "qualified_claim_count": 0,
                "unsupported_claim_count": 0,
                "core_unsupported_claim_count": 0,
                "result_type_overstatement_count": 0,
                "validation_level_overstatement_count": 0,
                "normalized_claim_count": 0,
            },
            "failures": [],
        }
        source = SimpleNamespace(
            package={"section": {"section_index": 1, "title": "测试章节"}}
        )

        report = render_claim_audit_report(
            manifest,
            audits=[audit],
            package_sources=[source],
        )

        self.assertIn("本报告仅展开需关注Claim", report)
        self.assertIn("另有2条完整记录", report)
        self.assertNotIn("`evidence-5`", report)
        self.assertEqual(len(evidence_rows), 5)

    def _validate(self, payload: dict) -> dict:
        return validate_chapter_claim_audit(
            payload,
            schema=self.schema,
            catalogue=self.catalogue,
            package=self.package,
        )


if __name__ == "__main__":
    unittest.main()
