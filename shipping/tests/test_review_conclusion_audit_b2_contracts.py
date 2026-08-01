from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from shipping_pipeline.review_claim_audit_b2_contracts import (
    ReviewClaimAuditB2Config,
)
from shipping_pipeline.review_conclusion_audit_b2_contracts import (
    CONCLUSION_AUDIT_DRAFT_SCHEMA_VERSION,
    ReviewConclusionAuditB2ContractError,
    build_conclusion_audit_draft_schema,
    derive_conclusion_audit_input,
    validate_conclusion_audit_draft,
)


class ReviewConclusionAuditB2ContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.audit_input = derive_conclusion_audit_input(_source())
        self.config = ReviewClaimAuditB2Config(
            schema_version="llm.review_claim_audit_b2_config.v1",
            max_reason_chars=300,
            audit_max_output_tokens=4096,
        )
        self.schema = build_conclusion_audit_draft_schema(
            self.audit_input, self.config
        )

    def test_derives_stable_sentence_catalogue(self) -> None:
        self.assertEqual(len(self.audit_input["sentences"]), 4)
        self.assertEqual(len(self.audit_input["source_claims_by_paragraph"]), 4)
        self.assertTrue(
            all(
                len(row["source_claims"]) == 1
                for row in self.audit_input["source_claims_by_paragraph"]
            )
        )
        self.assertTrue(
            self.audit_input["audit_input_id"].startswith(
                "review_conclusion_audit_input_b2_"
            )
        )

    def test_validates_passed_audit(self) -> None:
        result = validate_conclusion_audit_draft(
            _draft(self.audit_input),
            audit_input=self.audit_input,
            schema=self.schema,
        )
        self.assertTrue(result["summary"]["publishable"])
        self.assertEqual(result["summary"]["blocking_sentence_count"], 0)

    def test_computes_blocking_risk(self) -> None:
        draft = _draft(self.audit_input)
        draft["sentence_audits"][1]["risk_labels"] = [
            "unsupported_inference"
        ]
        result = validate_conclusion_audit_draft(
            draft, audit_input=self.audit_input, schema=self.schema
        )
        self.assertFalse(result["summary"]["publishable"])
        self.assertEqual(result["summary"]["blocking_sentence_count"], 1)

    def test_rejects_supported_mixed_with_risk(self) -> None:
        draft = _draft(self.audit_input)
        draft["sentence_audits"][0]["risk_labels"] = [
            "supported",
            "qualified",
        ]
        with self.assertRaisesRegex(
            ReviewConclusionAuditB2ContractError, "supported_mixed"
        ):
            validate_conclusion_audit_draft(
                draft, audit_input=self.audit_input, schema=self.schema
            )

    def test_marks_out_of_paragraph_support_as_source_mismatch(self) -> None:
        draft = _draft(self.audit_input)
        draft["sentence_audits"][0]["supporting_claim_ids"] = ["claim_2"]
        result = validate_conclusion_audit_draft(
            draft, audit_input=self.audit_input, schema=self.schema
        )
        row = result["sentence_audits"][0]
        self.assertEqual(row["model_risk_labels"], ["supported"])
        self.assertEqual(row["risk_labels"], ["source_mismatch"])
        self.assertEqual(row["out_of_paragraph_claim_ids"], ["claim_2"])
        self.assertTrue(row["blocking"])


def _source():
    paragraphs = []
    claims = []
    for index in range(1, 5):
        claim_id = f"claim_{index}"
        paragraphs.append(
            {
                "paragraph_id": f"paragraph_{index}",
                "paragraph_index": index,
                "group": [
                    "overall_findings",
                    "path_comparison",
                    "corpus_limits",
                    "future_direction",
                ][index - 1],
                "claim_type": [
                    "direct_summary",
                    "multi_paper_synthesis",
                    "corpus_gap",
                    "normative_recommendation",
                ][index - 1],
                "text": f"第{index}段结论只表达已批准内容。",
                "source_claim_ids": [claim_id],
                "citation_keys": [f"ref_{index}"],
            }
        )
        claims.append(
            {
                "claim_id": claim_id,
                "section_index": index,
                "planned_claim": f"第{index}条来源Claim。",
                "claim_type": "direct_fact",
                "citation_keys": [f"ref_{index}"],
            }
        )
    conclusion = {
        "chapter_id": "conclusion-chapter",
        "section_id": "section-9",
        "section_index": 9,
        "title": "结论",
        "paragraphs": paragraphs,
        "implemented_source_claim_ids": [row["claim_id"] for row in claims],
    }
    conclusion_input = {
        "conclusion_input_id": "conclusion-input",
        "source": {"audited_assembly_run_id": "audited-run"},
        "eligible_claims": claims,
        "citation_metadata": [
            {"citation_key": f"ref_{index}"} for index in range(1, 5)
        ],
        "language_policy": {
            "bounded_phrases": ["在当前样本文献中"],
            "prohibited_phrases": ["学界一致认为"],
        },
    }
    return SimpleNamespace(
        run_id="conclusion-run",
        manifest_sha256="sha256:manifest",
        conclusion=conclusion,
        conclusion_input=conclusion_input,
    )


def _draft(audit_input):
    return {
        "schema_version": CONCLUSION_AUDIT_DRAFT_SCHEMA_VERSION,
        "sentence_audits": [
            {
                "sentence_id": sentence["sentence_id"],
                "risk_labels": ["supported"],
                "supporting_claim_ids": [
                    sentence["paragraph_source_claim_ids"][0]
                ],
                "reason": "句子完整落在来源Claim内。",
            }
            for sentence in audit_input["sentences"]
        ],
    }
