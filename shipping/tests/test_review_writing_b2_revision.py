from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import build_parser
from shipping_pipeline.review_writing_b2_revision import (
    ReviewWritingB2RevisionRunner,
    load_review_writing_b2_revision_run,
)
from shipping_pipeline.review_writing_b2_revision_contracts import (
    ReviewWritingB2RevisionContractError,
    apply_revision_decision,
    validate_revision_decision_set,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"


class ReviewWritingB2RevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TEST_TMP_ROOT / f"writing_revision_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self.source = _suggestion_source()
        self.decision_payload = {
            "schema_version": "llm.review_writing_b2_revision_decisions.v1",
            "decision_basis": "user_confirmed_recommendation_set",
            "confirmed_on": "2026-08-01",
            "decisions": [
                {
                    "revision_suggestion_run_id": "suggestion-run",
                    "selections": [
                        {
                            "sentence_id": "sentence-risk",
                            "option_id": "option-delete",
                        }
                    ],
                }
            ],
        }
        self.decision_set = validate_revision_decision_set(
            self.decision_payload
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_delete_rebuilds_paragraph_and_preserves_old_chapter(self):
        original = self.source.adjudication_source.audit_source.writing_source.chapter
        revised = self._apply(self.decision_set)

        self.assertEqual(revised["paragraphs"][0]["text"], "保留句。")
        self.assertEqual(
            original["paragraphs"][0]["text"], "风险句。保留句。"
        )
        self.assertNotEqual(revised["chapter_id"], original["chapter_id"])
        self.assertEqual(
            revised["revision_audit"]["deleted_sentence_count"], 1
        )

    def test_user_confirmed_delete_does_not_require_model_option(self):
        decision = json.loads(json.dumps(self.decision_payload))
        decision["decisions"][0]["selections"][0][
            "option_id"
        ] = "user_confirmed_delete"

        revised = self._apply(validate_revision_decision_set(decision))

        change = revised["revision_audit"]["changes"][0]
        self.assertEqual(change["operation"], "delete")
        self.assertEqual(change["option_type"], "user_confirmed_delete")
        self.assertEqual(change["replacement_sentences"], [])

    def test_adjudication_confirmed_delete_records_distinct_basis(self):
        decision = json.loads(json.dumps(self.decision_payload))
        decision["decision_basis"] = "adjudication_exact_removal"
        decision["decisions"][0]["selections"][0][
            "option_id"
        ] = "adjudication_confirmed_delete"

        decision_set = validate_revision_decision_set(decision)
        revised = self._apply(decision_set)

        self.assertEqual(
            decision_set["decision_basis"], "adjudication_exact_removal"
        )
        self.assertEqual(
            revised["revision_audit"]["changes"][0]["option_type"],
            "adjudication_confirmed_delete",
        )

    def test_decision_must_cover_every_risk_sentence_in_order(self):
        decision = json.loads(json.dumps(self.decision_payload))
        decision["decisions"][0]["selections"] = [
            {"sentence_id": "unknown", "option_id": "option-delete"}
        ]

        with self.assertRaisesRegex(
            ReviewWritingB2RevisionContractError,
            "writing_revision_b2.selection_coverage_invalid",
        ):
            self._apply(validate_revision_decision_set(decision))

    def test_option_must_belong_to_selected_sentence(self):
        decision = json.loads(json.dumps(self.decision_payload))
        decision["decisions"][0]["selections"][0]["option_id"] = "missing"

        with self.assertRaisesRegex(
            ReviewWritingB2RevisionContractError,
            "writing_revision_b2.option_not_found",
        ):
            self._apply(validate_revision_decision_set(decision))

    def test_paragraph_catalogue_must_replay_exactly(self):
        self.source.adjudication_source.audit_source.audit_input["sentences"][1][
            "text"
        ] = "不一致。"

        with self.assertRaisesRegex(
            ReviewWritingB2RevisionContractError,
            "writing_revision_b2.paragraph_replay_mismatch",
        ):
            self._apply(self.decision_set)

    def test_runner_publishes_and_loader_replays(self):
        decision_file = self.workspace / "decision.json"
        decision_file.write_text(
            json.dumps(self.decision_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        loader = lambda workspace, run_id: self.source
        result = ReviewWritingB2RevisionRunner(
            self.workspace, suggestion_loader=loader
        ).run(
            revision_suggestion_run_id="suggestion-run",
            decision_file=decision_file,
            run_id="revision-run",
        )

        self.assertEqual(result["status"], "completed")
        loaded = load_review_writing_b2_revision_run(
            self.workspace, "revision-run", suggestion_loader=loader
        )
        self.assertEqual(loaded.chapter["chapter_id"], result["chapter_id"])
        self.assertEqual(loaded.run_id, "revision-run")

    def test_cli_exposes_apply_revision_command(self):
        args = build_parser().parse_args(
            [
                "apply-review-writing-revision-b2",
                "--revision-suggestion-run-id",
                "suggestion-run",
                "--decision-file",
                "decision.json",
            ]
        )

        self.assertEqual(args.revision_suggestion_run_id, "suggestion-run")

    def _apply(self, decision_set):
        return apply_revision_decision(
            decision_set=decision_set,
            suggestion_run_id=self.source.run_id,
            suggestion_manifest_sha256=self.source.manifest_sha256,
            revision_input=self.source.revision_input,
            suggestions=self.source.revision_suggestions,
            source_chapter=self.source.adjudication_source.audit_source.writing_source.chapter,
            source_audit_input=self.source.adjudication_source.audit_source.audit_input,
        )


def _suggestion_source():
    chapter = {
        "writing_input_id": "writing-input",
        "chapter_id": "chapter-old",
        "section_id": "section-id",
        "section_index": 1,
        "title": "测试章节",
        "paragraphs": [
            {
                "paragraph_id": "paragraph-old",
                "paragraph_index": 1,
                "text": "风险句。保留句。",
                "implemented_claim_ids": ["claim-1"],
                "citation_keys": ["ref-1"],
            }
        ],
    }
    audit_input = {
        "sentences": [
            {
                "sentence_id": "sentence-risk",
                "paragraph_id": "paragraph-old",
                "paragraph_index": 1,
                "sentence_index": 1,
                "text": "风险句。",
            },
            {
                "sentence_id": "sentence-keep",
                "paragraph_id": "paragraph-old",
                "paragraph_index": 1,
                "sentence_index": 2,
                "text": "保留句。",
            },
        ]
    }
    writing_source = SimpleNamespace(
        run_id="writing-run",
        chapter=chapter,
        writing_input={"writing_input_id": "writing-input"},
        package_source=SimpleNamespace(token_budget={}),
    )
    audit_source = SimpleNamespace(
        audit_input=audit_input,
        writing_source=writing_source,
    )
    adjudication_source = SimpleNamespace(audit_source=audit_source)
    suggestions = {
        "revision_suggestion_id": "suggestion-id",
        "suggestions": [
            {
                "sentence_id": "sentence-risk",
                "revision_options": [
                    {
                        "option_id": "option-delete",
                        "option_type": "delete_or_split",
                        "operation": "delete",
                        "replacement_sentences": [],
                        "retained_claim_ids": [],
                        "citation_keys": [],
                        "rationale": "删除重复风险句。",
                    },
                    {
                        "option_id": "option-replace",
                        "option_type": "conservative_rewrite",
                        "operation": "replace",
                        "replacement_sentences": ["保守替换句。"],
                        "retained_claim_ids": ["claim-1"],
                        "citation_keys": ["ref-1"],
                        "rationale": "仅保留支持内容。",
                    },
                ],
            }
        ],
    }
    return SimpleNamespace(
        run_id="suggestion-run",
        manifest_sha256="sha256:" + "a" * 64,
        revision_input={
            "source": {"writing_run_id": "writing-run"},
        },
        revision_suggestions=suggestions,
        adjudication_source=adjudication_source,
    )


if __name__ == "__main__":
    unittest.main()
