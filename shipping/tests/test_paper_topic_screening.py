from __future__ import annotations

import unittest
from pathlib import Path

import main
from shipping_pipeline.paper_topic_screening import (
    PaperTopicScreeningError,
    _is_resume_rejection,
    _pool_summary,
    _validate_chinese_scope,
)


class PaperTopicScreeningTest(unittest.TestCase):
    def test_pool_summary_keeps_pending_and_blocked_visible(self) -> None:
        summary = _pool_summary([
            {"screening_status": "screened"},
            {"screening_status": "screening_failed"},
            {"screening_status": "pending_screening"},
            {"screening_status": "pending_card"},
            {"screening_status": "blocked"},
            {"screening_status": "duplicate_source"},
        ])
        self.assertEqual(summary["source_file_count"], 6)
        self.assertEqual(summary["screened_count"], 1)
        self.assertEqual(summary["pending_card_count"], 1)
        self.assertEqual(summary["blocked_count"], 1)

    def test_cli_exposes_paper_pool_screening(self) -> None:
        args = main.build_parser().parse_args([
            "llm-paper-pool-screen",
            "--inventory-run-id", "inventory",
            "--topic", "主题",
                "--max-papers", "3",
                "--resume-from-run-id", "previous",
        ])
        self.assertEqual(args.command, "llm-paper-pool-screen")
        self.assertEqual(args.max_papers, 3)
        self.assertEqual(args.resume_from_run_id, "previous")
        self.assertEqual(args.workspace, Path("workspace"))

    def test_screening_rejects_english_natural_language_fields(self) -> None:
        with self.assertRaisesRegex(PaperTopicScreeningError, "output_not_chinese"):
            _validate_chinese_scope({
                "relevance_reason": "Directly relevant.",
                "topic_summary": "论文与主题直接相关。",
                "selected_materials": [
                    {"selection_reason": "选择论文结论。"}
                ],
            })

    def test_card_generation_change_is_a_resume_rejection(self) -> None:
        error = PaperTopicScreeningError(
            "paper_screen.source_changed",
            "单篇筛选来源Card代际已变化。",
        )

        self.assertTrue(_is_resume_rejection(error))

    def test_unexpected_resume_error_still_aborts(self) -> None:
        error = PaperTopicScreeningError(
            "paper_screen.schema_invalid",
            "产物结构损坏。",
        )

        self.assertFalse(_is_resume_rejection(error))
