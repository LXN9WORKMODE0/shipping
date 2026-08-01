from __future__ import annotations

import unittest
from types import SimpleNamespace

from shipping_pipeline.review_direct_a2_contracts import (
    A2_DRAFT_SCHEMA_VERSION,
    ReviewDirectA2Config,
    ReviewDirectA2ContractError,
    build_direct_a2_schema,
    derive_direct_a2_input,
    validate_direct_a2_draft,
)


class ReviewDirectA2ContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ReviewDirectA2Config(
            schema_version="llm.review_direct_a2_config.v1",
            min_body_chars=6000,
            max_body_chars=7000,
            max_paragraphs_per_section=4,
            max_output_tokens=32768,
        )
        self.a2_input = derive_direct_a2_input(
            source_package=_package(),
            audited_body=_body(),
            framework=_framework(),
            config=self.config,
        )
        self.schema = build_direct_a2_schema(self.a2_input)

    def test_input_contains_full_text_but_excludes_b2_cognition(self) -> None:
        self.assertEqual(len(self.a2_input["papers"]), 14)
        self.assertIn("full_markdown", self.a2_input["papers"][0])
        self.assertIn("claim_ledger", self.a2_input["excluded_cognitive_inputs"])
        self.assertEqual(len(self.a2_input["framework"]["sections"]), 9)

    def test_validates_same_framework_and_body_range(self) -> None:
        result = validate_direct_a2_draft(
            _draft(), a2_input=self.a2_input, schema=self.schema
        )
        self.assertEqual(result["body_char_count"], 6300)
        self.assertEqual(len(result["chapters"]), 9)
        self.assertEqual(result["release_status"], "candidate_pending_unified_audit")

    def test_rejects_body_below_fair_range(self) -> None:
        draft = _draft()
        for section in draft["sections"]:
            section["paragraphs"][0]["text"] = "较短正文" * 30 + "。"
        with self.assertRaisesRegex(
            ReviewDirectA2ContractError, "body_length_invalid"
        ):
            validate_direct_a2_draft(
                draft, a2_input=self.a2_input, schema=self.schema
            )


def _framework():
    return {
        "framework_b2_id": "framework-b2",
        "working_title": "测试综述",
        "central_question": "测试问题",
        "sections": [
            {
                "section_id": f"section-{index}",
                "section_index": index,
                "title": f"第{index}节",
                "section_type": (
                    "introduction" if index == 1 else "conclusion" if index == 9 else "body"
                ),
            }
            for index in range(1, 10)
        ],
    }


def _package():
    papers = [
        {
            "paper_id": f"paper-{index}",
            "paper_title": f"论文{index}",
            "citation_key": f"ref_{index}",
            "bibliography": {},
            "full_markdown": f"# 论文{index}\n\n完整正文。",
        }
        for index in range(1, 15)
    ]
    return SimpleNamespace(
        run_id="package-run",
        output_sha256="sha256:package",
        package={"papers": papers},
    )


def _body():
    return SimpleNamespace(
        run_id="body-run",
        draft={
            "body_release_ready": True,
            "draft_id": "body-draft",
            "citation_metadata": [
                {"paper_id": f"paper-{index}"} for index in range(1, 15)
            ],
        },
    )


def _draft():
    text = ("公平对照测试正文" * 100)[:699] + "。"
    return {
        "schema_version": A2_DRAFT_SCHEMA_VERSION,
        "title": "测试综述",
        "sections": [
            {
                "section_id": f"section-{index}",
                "section_index": index,
                "title": f"第{index}节",
                "paragraphs": [
                    {
                        "paragraph_index": 1,
                        "text": text,
                        "citation_keys": ["ref_1", "ref_2"] if index == 2 else ["ref_1"],
                    }
                ],
            }
            for index in range(1, 10)
        ],
    }
