from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import build_parser
from shipping_pipeline.review_writing_b2_audited_assembly import (
    ReviewWritingB2AuditedAssemblyError,
    build_audited_draft,
    validate_audited_release_set,
)


class ReviewWritingB2AuditedAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release_set = validate_audited_release_set(_release_payload())
        self.chapters, self.audits, self.adjudications = _sources()

    def test_build_accepts_one_explicit_qualified_residual(self):
        draft = self._build()

        self.assertTrue(draft["body_release_ready"])
        self.assertTrue(draft["conclusion_input_ready"])
        self.assertEqual(
            draft["audit_summary"]["status"],
            "passed_with_nonblocking_residuals",
        )
        self.assertEqual(draft["audit_summary"]["blocking_sentence_count"], 0)

    def test_blocking_residual_is_rejected(self):
        adjudication = self.adjudications["adjudication-6"].adjudication
        adjudication["summary"]["final_blocking_sentence_count"] = 1

        with self.assertRaisesRegex(
            ReviewWritingB2AuditedAssemblyError,
            "audited_assembly_b2.blocking_risk_present",
        ):
            self._build()

    def test_audit_must_reference_selected_chapter(self):
        self.audits["audit-1"].writing_source = self.chapters["chapter-2"]

        with self.assertRaisesRegex(
            ReviewWritingB2AuditedAssemblyError,
            "audited_assembly_b2.source_chain_mismatch",
        ):
            self._build()

    def test_cli_exposes_audited_assembly_command(self):
        args = build_parser().parse_args(
            [
                "assemble-review-b2-audited",
                "--release-file",
                "release.json",
            ]
        )

        self.assertEqual(str(args.release_file), "release.json")

    def _build(self):
        return build_audited_draft(
            "workspace",
            self.release_set,
            chapter_loader=lambda workspace, run_id: self.chapters[run_id],
            audit_loader=lambda workspace, run_id: self.audits[run_id],
            adjudication_loader=lambda workspace, run_id: self.adjudications[
                run_id
            ],
        )


def _release_payload():
    chapters = []
    for index in range(1, 9):
        if index == 6:
            disposition = "accepted_nonblocking"
            adjudication_run_id = "adjudication-6"
        elif index == 7:
            disposition = "adjudication_passed"
            adjudication_run_id = "adjudication-7"
        else:
            disposition = "audit_passed"
            adjudication_run_id = None
        chapters.append(
            {
                "section_index": index,
                "chapter_run_id": f"chapter-{index}",
                "audit_run_id": f"audit-{index}",
                "adjudication_run_id": adjudication_run_id,
                "disposition": disposition,
            }
        )
    return {
        "schema_version": "llm.review_writing_b2_audited_release_set.v1",
        "allowed_nonblocking_risks": ["qualified"],
        "chapters": chapters,
    }


def _sources():
    sections = [
        {
            "section_id": f"section-{index}",
            "section_index": index,
            "section_type": "body",
        }
        for index in range(1, 9)
    ] + [
        {
            "section_id": "section-9",
            "section_index": 9,
            "section_type": "conclusion",
        }
    ]
    framework = {
        "framework_b2_id": "framework-id",
        "working_title": "测试综述",
        "central_question": "测试问题",
        "sections": sections,
    }
    chapters = {}
    audits = {}
    adjudications = {}
    for index in range(1, 9):
        writing_input = {
            "framework_outline": framework,
            "citation_metadata": [
                {
                    "citation_key": f"ref-{index}",
                    "paper_id": f"paper-{index}",
                }
            ],
        }
        chapter = {
            "chapter_id": f"chapter-id-{index}",
            "section_id": f"section-{index}",
            "section_index": index,
            "title": f"章节{index}",
            "paragraphs": [
                {
                    "paragraph_index": 1,
                    "text": f"章节{index}正文。",
                    "citation_keys": [f"ref-{index}"],
                    "implemented_claim_ids": [f"claim-{index}"],
                }
            ],
        }
        chapter_source = SimpleNamespace(
            run_id=f"chapter-{index}",
            writing_input=writing_input,
            chapter=chapter,
        )
        chapters[chapter_source.run_id] = chapter_source
        publishable = index not in {6, 7}
        audit_source = SimpleNamespace(
            run_id=f"audit-{index}",
            writing_source=chapter_source,
            audit={
                "audit_id": f"audit-id-{index}",
                "chapter_id": chapter["chapter_id"],
                "summary": {"publishable": publishable},
            },
        )
        audits[audit_source.run_id] = audit_source
    adjudications["adjudication-6"] = SimpleNamespace(
        audit_source=audits["audit-6"],
        adjudication={
            "adjudication_id": "adjudication-id-6",
            "summary": {
                "publishable_after_adjudication": False,
                "final_blocking_sentence_count": 0,
                "final_risk_counts": {"supported": 6, "qualified": 1},
            },
        },
    )
    adjudications["adjudication-7"] = SimpleNamespace(
        audit_source=audits["audit-7"],
        adjudication={
            "adjudication_id": "adjudication-id-7",
            "summary": {
                "publishable_after_adjudication": True,
                "final_blocking_sentence_count": 0,
                "final_risk_counts": {"supported": 7, "qualified": 0},
            },
        },
    )
    return chapters, audits, adjudications


if __name__ == "__main__":
    unittest.main()
