from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import main
from shipping_pipeline.hierarchical_review_audit import (
    _claim_catalogue,
    _deterministic_audit,
    _group_by_section,
)


class HierarchicalReviewAuditTests(unittest.TestCase):
    def test_cli_exposes_audit(self) -> None:
        args = main.build_parser().parse_args([
            "hierarchical-review-audit", "--writing-run-id", "writing-one"
        ])
        self.assertEqual(args.writing_run_id, "writing-one")

    def test_claim_catalogue_extracts_numbers_and_sections(self) -> None:
        review = {
            "introduction": [{"text": "处理量提高20%。", "citation_keys": ["P001"]}],
            "chapters": [{"title": "主题", "paragraphs": [{"text": "无数字。", "citation_keys": ["P001"]}]}],
            "discussion": [], "conclusion": [],
        }
        rows = _claim_catalogue(review)
        self.assertEqual(rows[0]["claim_id"], "claim_001")
        self.assertIn("20%", rows[0]["numeric_facts"])
        self.assertEqual(list(_group_by_section(rows)), ["section_01", "section_02"])

    def test_deterministic_audit_exposes_unsupported_number(self) -> None:
        claim = {"claim_id": "claim_001", "numeric_facts": ["20%", "30吨"], "citation_keys": ["P001"]}
        result = _deterministic_audit(claim, {"P001": {"statement": "提高20%。"}})
        self.assertEqual(result["unsupported_numeric_facts"], ["30吨"])


if __name__ == "__main__":
    unittest.main()
