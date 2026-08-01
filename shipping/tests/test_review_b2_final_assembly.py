from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from shipping_pipeline.review_b2_final_assembly import (
    ReviewB2FinalAssemblyError,
    build_final_review,
    validate_final_release,
)


class ReviewB2FinalAssemblyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.release = validate_final_release(
            {
                "schema_version": "llm.review_b2_final_release.v1",
                "audited_assembly_run_id": "body-run",
                "conclusion_run_id": "conclusion-run",
                "conclusion_audit_run_id": "audit-run",
            }
        )
        self.body, self.conclusion, self.audit = _sources()

    def test_builds_formal_review_after_both_gates_pass(self) -> None:
        review = build_final_review(
            "workspace",
            self.release,
            body_loader=lambda *_: self.body,
            conclusion_loader=lambda *_: self.conclusion,
            audit_loader=lambda *_: self.audit,
        )
        self.assertTrue(review["formal_review_published"])
        self.assertEqual(len(review["chapters"]), 9)
        self.assertEqual(review["quality_gate"]["status"], "passed")
        self.assertIn("## 参考文献", review["markdown"])
        self.assertIn("[@ref_1]", review["markdown"])

    def test_rejects_conclusion_from_another_body(self) -> None:
        self.conclusion.conclusion_input["source"][
            "audited_draft_id"
        ] = "another-draft"
        with self.assertRaisesRegex(
            ReviewB2FinalAssemblyError, "body_conclusion_chain_mismatch"
        ):
            build_final_review(
                "workspace",
                self.release,
                body_loader=lambda *_: self.body,
                conclusion_loader=lambda *_: self.conclusion,
                audit_loader=lambda *_: self.audit,
            )

    def test_rejects_blocking_conclusion_audit(self) -> None:
        self.audit.audit["summary"]["publishable"] = False
        self.audit.audit["summary"]["blocking_sentence_count"] = 1
        with self.assertRaisesRegex(
            ReviewB2FinalAssemblyError, "conclusion_audit_not_passed"
        ):
            build_final_review(
                "workspace",
                self.release,
                body_loader=lambda *_: self.body,
                conclusion_loader=lambda *_: self.conclusion,
                audit_loader=lambda *_: self.audit,
            )


def _sources():
    citation = {
        "citation_key": "ref_1",
        "paper_title": "测试论文",
        "authors": ["作者甲"],
        "year": 2024,
        "reference_type": "article",
        "journal": "测试期刊",
    }
    chapters = [
        {
            "chapter_id": f"chapter-{index}",
            "section_id": f"section-{index}",
            "section_index": index,
            "title": f"第{index}节",
            "paragraphs": [
                {
                    "paragraph_index": 1,
                    "text": f"第{index}节正文。",
                    "citation_keys": ["ref_1"],
                }
            ],
        }
        for index in range(1, 9)
    ]
    body = SimpleNamespace(
        run_id="body-run",
        draft={
            "draft_id": "body-draft",
            "release_set_id": "body-release",
            "working_title": "测试综述",
            "central_question": "测试问题",
            "chapters": chapters,
            "citation_metadata": [citation],
            "audit_summary": {
                "blocking_sentence_count": 0,
                "nonblocking_residual_count": 1,
            },
        },
    )
    conclusion = SimpleNamespace(
        run_id="conclusion-run",
        conclusion_input={
            "source": {
                "audited_assembly_run_id": "body-run",
                "audited_draft_id": "body-draft",
                "release_set_id": "body-release",
            }
        },
        conclusion={
            "chapter_id": "conclusion-chapter",
            "section_id": "section-9",
            "section_index": 9,
            "title": "结论",
            "paragraphs": [
                {
                    "paragraph_index": 1,
                    "text": "结论正文。",
                    "citation_keys": ["ref_1"],
                }
            ],
        },
    )
    audit = SimpleNamespace(
        run_id="audit-run",
        conclusion_source=conclusion,
        audit={
            "audit_id": "audit-id",
            "summary": {
                "publishable": True,
                "blocking_sentence_count": 0,
            },
        },
    )
    return body, conclusion, audit
