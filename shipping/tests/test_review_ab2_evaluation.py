from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import main
from shipping_pipeline.review_ab2_evaluation import (
    ReviewAB2EvaluationError,
    _automatic_gates,
    _blind_order,
    _compare_structural_assessments,
    _extract_ab2_dimensions,
    _summarize_structural_stability,
    load_ab2_evaluation_config,
)


class ReviewAB2EvaluationTest(unittest.TestCase):
    def test_blind_order_is_deterministic_and_complete(self) -> None:
        first = _blind_order("experiment")
        self.assertEqual(first, _blind_order("experiment"))
        self.assertEqual(set(first), {"A2", "B2"})

    def test_automatic_gates_apply_five_point_tolerance(self) -> None:
        metrics = {
            "A2": {"core_factual_claim_support_rate": 0.95},
            "B2": {
                "core_factual_claim_support_rate": 0.91,
                "core_unsupported_claim_count": 0,
                "result_type_overstatement_count": 0,
                "validation_level_overstatement_count": 0,
                "structure_score": 4,
                "comparison_score": 4,
            },
        }
        self.assertTrue(_automatic_gates(metrics)["passed"])

    def test_framework_titles_are_frozen_as_evaluation_dimensions(self) -> None:
        dimensions = _extract_ab2_dimensions(
            {
                "sections": [
                    {"section_type": "introduction", "title": "引言"},
                    {"section_type": "body", "title": "原因辨析"},
                    {"section_type": "conclusion", "title": "结论"},
                ]
            }
        )
        self.assertEqual(
            dimensions,
            [{"dimension_index": 1, "title": "原因辨析", "question": "原因辨析"}],
        )

    def test_config_requires_pending_human_cost(self) -> None:
        payload = {
            "schema_version": "llm.review_ab2_evaluation_config.v1",
            "experiment_id": "experiment",
            "source_package_run_id": "package",
            "audited_assembly_run_id": "body",
            "a2_run_id": "a2",
            "b2_final_run_id": "b2",
            "structural_max_output_tokens": 4096,
            "human_evaluation": {
                "status": "completed",
                "editing_minutes": {"A2": 1, "B2": 1},
                "edited_char_ratio": {"A2": 0.1, "B2": 0.1},
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                ReviewAB2EvaluationError, "human_state_invalid"
            ):
                load_ab2_evaluation_config(path)

    def test_cli_exposes_ab2_evaluation(self) -> None:
        args = main.build_parser().parse_args(
            ["llm-review-evaluate-ab2", "--config", "experiment.json"]
        )
        self.assertEqual(args.command, "llm-review-evaluate-ab2")

    def test_structural_comparison_detects_stable_scores_and_selection_drift(self) -> None:
        original = {
            "structure_score": 4,
            "comparison_score": 3,
            "covered_dimension_indexes": [1, 2],
            "cross_paper_comparison_paragraph_indexes": [1, 2],
            "corpus_gap_overreachs": [{"paragraph_index": 4, "reason": "越界"}],
        }
        reversed_result = {
            "structure_score": 4,
            "comparison_score": 3,
            "covered_dimension_indexes": [2, 1],
            "cross_paper_comparison_paragraph_indexes": [2, 3],
            "corpus_gap_overreachs": [],
        }
        comparison = _compare_structural_assessments(original, reversed_result)
        self.assertTrue(comparison["score_stable"])
        self.assertTrue(comparison["dimension_selection_stable"])
        self.assertEqual(comparison["comparison_paragraph_overlap"]["jaccard"], 0.3333)
        self.assertEqual(comparison["corpus_gap_overreach_overlap"]["jaccard"], 0.0)

    def test_structural_comparison_detects_score_order_effect(self) -> None:
        original = {
            "structure_score": 4,
            "comparison_score": 4,
            "covered_dimension_indexes": [1],
            "cross_paper_comparison_paragraph_indexes": [],
            "corpus_gap_overreachs": [],
        }
        reversed_result = dict(original, structure_score=3)
        comparison = _compare_structural_assessments(original, reversed_result)
        self.assertFalse(comparison["score_stable"])
        self.assertEqual(comparison["score_delta"]["structure"], -1)

    def test_stability_summary_distinguishes_ranking_from_threshold(self) -> None:
        comparisons = {
            "A2": {
                "score_stable": False,
                "original_scores": {"structure": 4, "comparison": 4},
                "reversed_scores": {"structure": 5, "comparison": 5},
            },
            "B2": {
                "score_stable": False,
                "original_scores": {"structure": 3, "comparison": 3},
                "reversed_scores": {"structure": 4, "comparison": 4},
            },
        }
        summary = _summarize_structural_stability(comparisons)
        self.assertFalse(summary["score_stable"])
        self.assertTrue(summary["relative_ranking_stable"])
        self.assertFalse(summary["threshold_classification_stable"])
        self.assertEqual(
            summary["decision_status"], "order_sensitive_threshold_requires_human"
        )

    def test_cli_exposes_ab2_stability_evaluation(self) -> None:
        args = main.build_parser().parse_args(
            [
                "llm-review-evaluate-ab2-stability",
                "--source-evaluation-run-id",
                "evaluation-v4",
            ]
        )
        self.assertEqual(args.command, "llm-review-evaluate-ab2-stability")
        self.assertEqual(args.source_evaluation_run_id, "evaluation-v4")
