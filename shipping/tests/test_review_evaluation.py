from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.review_evaluation import (
    ReviewEvaluationError,
    build_structural_evaluation_schema,
    canonicalize_direct_review,
    canonicalize_evidence_review,
    compute_candidate_metrics,
    load_review_evaluation_config,
    validate_structural_evaluation,
)


class ReviewEvaluationTests(unittest.TestCase):
    def test_direct_review_is_flattened_with_stable_paragraph_indexes(self):
        review = {
            "title": "方案A",
            "sections": [
                {
                    "heading": "第一章",
                    "paragraphs": [
                        {"text": "甲。", "citation_keys": ["ref-a"]},
                        {"text": "乙。", "citation_keys": ["ref-b"]},
                    ],
                }
            ],
            "conclusion": {
                "text": "结论。",
                "citation_keys": ["ref-a", "ref-b"],
            },
        }
        result = canonicalize_direct_review(review)
        self.assertEqual(
            [row["paragraph_index"] for row in result["paragraphs"]],
            [1, 2, 3],
        )
        self.assertEqual(result["paragraphs"][2]["section_heading"], "结论")

    def test_evidence_review_maps_evidence_to_unique_citations(self):
        review = {
            "title": "方案C",
            "sections": [
                {
                    "heading": "章节",
                    "paragraphs": [
                        {
                            "text": "正文。",
                            "evidence_unit_ids": ["e1", "e2", "e3"],
                        }
                    ],
                }
            ],
            "conclusion": {
                "text": "结论。",
                "evidence_unit_ids": ["e2"],
            },
        }
        result = canonicalize_evidence_review(
            review,
            citation_by_evidence={
                "e1": "ref-a",
                "e2": "ref-a",
                "e3": "ref-b",
            },
        )
        self.assertEqual(
            result["paragraphs"][0]["citation_keys"],
            ["ref-a", "ref-b"],
        )

    def test_unknown_evidence_is_rejected(self):
        review = {
            "title": "方案C",
            "sections": [
                {
                    "heading": "章节",
                    "paragraphs": [
                        {"text": "正文。", "evidence_unit_ids": ["missing"]}
                    ],
                }
            ],
            "conclusion": {"text": "结论。", "evidence_unit_ids": []},
        }
        with self.assertRaisesRegex(
            ReviewEvaluationError,
            "evaluation.unknown_evidence",
        ):
            canonicalize_evidence_review(
                review,
                citation_by_evidence={},
            )

    def test_structural_schema_preserves_candidate_order(self):
        candidates = [
            {
                "candidate": label,
                "paragraphs": [{"paragraph_index": 1}],
            }
            for label in ("A", "B", "C")
        ]
        schema = build_structural_evaluation_schema(
            candidates=candidates,
            dimension_indexes=[1, 2],
        )
        payload = {
            "schema_version": "llm.review_structural_evaluation.v1",
            "candidates": [
                {
                    "candidate": label,
                    "covered_dimension_indexes": [1],
                    "cross_paper_comparison_paragraph_indexes": [1],
                    "corpus_gap_overreachs": [],
                    "structure_score": 4,
                    "comparison_score": 3,
                    "assessment": "结构清楚。",
                }
                for label in ("A", "B", "C")
            ],
        }
        result = validate_structural_evaluation(payload, schema=schema)
        self.assertEqual(
            [row["candidate"] for row in result["candidates"]],
            ["A", "B", "C"],
        )

    def test_explicit_non_overreach_entry_is_removed_and_recorded(self):
        candidates = [
            {
                "candidate": label,
                "paragraphs": [{"paragraph_index": 1}],
            }
            for label in ("A", "B", "C")
        ]
        schema = build_structural_evaluation_schema(
            candidates=candidates,
            dimension_indexes=[1],
        )
        payload = {
            "schema_version": "llm.review_structural_evaluation.v1",
            "candidates": [
                {
                    "candidate": label,
                    "covered_dimension_indexes": [1],
                    "cross_paper_comparison_paragraph_indexes": [],
                    "corpus_gap_overreachs": [
                        {
                            "paragraph_index": 1,
                            "reason": "该表述仅说明样本边界，未越界。",
                        }
                    ],
                    "structure_score": 3,
                    "comparison_score": 2,
                    "assessment": "结构一般。",
                }
                for label in ("A", "B", "C")
            ],
        }

        result = validate_structural_evaluation(payload, schema=schema)

        self.assertEqual(
            result["candidates"][0]["corpus_gap_overreachs"],
            [],
        )
        self.assertEqual(
            result["candidates"][0]["normalization_flags"],
            ["non_overreach_entry_removed:1"],
        )

    def test_metrics_keep_fact_audit_separate_from_structure_scores(self):
        candidate = {
            "paragraphs": [
                {
                    "text": "甲乙。",
                    "citation_keys": ["ref-a", "ref-b"],
                },
                {"text": "丙。", "citation_keys": ["ref-a"]},
            ]
        }
        audit = {
            "claim_audits": [
                {
                    "claim_type": "factual",
                    "importance": "core",
                    "status": "supported",
                    "source_assessments": [
                        {"source_tier": "evidence"},
                        {"source_tier": "none"},
                    ],
                    "result_type_check": "accurate",
                    "validation_level_check": "accurate",
                },
                {
                    "claim_type": "factual",
                    "importance": "supporting",
                    "status": "unsupported",
                    "source_assessments": [{"source_tier": "none"}],
                    "result_type_check": "overstated",
                    "validation_level_check": "overstated",
                },
            ]
        }
        structural = {
            "covered_dimension_indexes": [1],
            "cross_paper_comparison_paragraph_indexes": [1],
            "corpus_gap_overreachs": [],
            "structure_score": 5,
            "comparison_score": 5,
        }
        metrics = compute_candidate_metrics(
            candidate,
            audit,
            structural,
            paper_count=2,
            dimension_count=2,
        )
        self.assertEqual(metrics["factual_claim_support_rate"], 0.5)
        self.assertEqual(metrics["citation_paper_matching_rate"], 0.3333)
        self.assertEqual(metrics["structure_score"], 5)
        self.assertEqual(metrics["result_type_overstatement_count"], 1)

    def test_pending_human_config_cannot_contain_values(self):
        payload = {
            "schema_version": "llm.review_evaluation_config.v1",
            "experiment_id": "experiment",
            "topic": "主题",
            "review_goal": "目标",
            "expected_paper_count": 14,
            "source_package_run_id": "package",
            "a_direct_run_id": "a",
            "b_writing_run_id": "b",
            "b_audit_run_id": "b-audit",
            "c_draft_run_id": "c",
            "c_synthesis_run_id": "c-synthesis",
            "structural_max_output_tokens": 1000,
            "human_evaluation": {
                "status": "pending",
                "candidates": {
                    label: {
                        "editing_minutes": 1 if label == "A" else None,
                        "edited_char_ratio": None,
                    }
                    for label in ("A", "B", "C")
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReviewEvaluationError,
                "evaluation.human_pending_value_present",
            ):
                load_review_evaluation_config(path)


if __name__ == "__main__":
    unittest.main()
