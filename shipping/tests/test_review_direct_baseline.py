from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.review_direct_baseline import (
    DirectReviewBaselineError,
    build_direct_review_schema,
    validate_direct_review,
)


class DirectReviewBaselineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = build_direct_review_schema(
            citation_keys=["ref-a", "ref-b"],
            min_body_chars=1000,
            max_body_chars=5000,
        )
        paragraph = {
            "text": "甲" * 210,
            "citation_keys": ["ref-a", "ref-b"],
        }
        self.payload = {
            "schema_version": "llm.review_direct_baseline.v1",
            "title": "测试综述",
            "abstract": "摘要" * 80,
            "keywords": ["甲", "乙", "丙"],
            "sections": [
                {
                    "heading": f"章节{index}",
                    "paragraphs": [copy.deepcopy(paragraph) for _ in range(2)],
                }
                for index in range(1, 5)
            ],
            "conclusion": copy.deepcopy(paragraph),
        }

    def test_valid_review_reports_body_chars(self):
        result = validate_direct_review(self.payload, schema=self.schema)
        self.assertEqual(result["body_char_count"], 210 * 9)

    def test_unknown_citation_is_rejected(self):
        payload = copy.deepcopy(self.payload)
        payload["sections"][0]["paragraphs"][0]["citation_keys"] = ["ref-x"]
        with self.assertRaisesRegex(
            DirectReviewBaselineError,
            "direct_review.schema_invalid",
        ):
            validate_direct_review(payload, schema=self.schema)

    def test_review_requires_cross_paper_paragraph(self):
        payload = copy.deepcopy(self.payload)
        for section in payload["sections"]:
            for paragraph in section["paragraphs"]:
                paragraph["citation_keys"] = ["ref-a"]
        payload["conclusion"]["citation_keys"] = ["ref-a"]
        with self.assertRaisesRegex(
            DirectReviewBaselineError,
            "direct_review.no_cross_paper_paragraph",
        ):
            validate_direct_review(payload, schema=self.schema)

    def test_body_length_is_enforced_after_schema(self):
        schema = build_direct_review_schema(
            citation_keys=["ref-a", "ref-b"],
            min_body_chars=3000,
            max_body_chars=5000,
        )
        with self.assertRaisesRegex(
            DirectReviewBaselineError,
            "direct_review.body_length_invalid",
        ):
            validate_direct_review(self.payload, schema=schema)


if __name__ == "__main__":
    unittest.main()
