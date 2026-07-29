from __future__ import annotations

import copy
import unittest

from scripts.generate_review_draft_experiment import (
    MAX_BODY_CHARS,
    MIN_BODY_CHARS,
    SCHEMA_VERSION,
    _build_schema,
    _render,
    _validate,
    _validate_draft_contract,
)


class ReviewDraftExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.outline = {
            "sections": [
                {
                    "title": "运行组织",
                    "paragraphs": [
                        {
                            "paragraph_role": "evidence_synthesis",
                            "evidence_unit_ids": ["evidence_1"],
                            "gap_ids": [],
                        }
                    ],
                }
            ]
        }
        self.schema = _build_schema(
            outline=self.outline,
            evidence_ids=["evidence_1"],
            gap_ids=["gap_1"],
            review_title="测试主题：基于1篇样本文献的主题综述",
        )
        self.payload = {
            "schema_version": SCHEMA_VERSION,
            "title": "测试主题：基于1篇样本文献的主题综述",
            "abstract": "摘要" * 75,
            "keywords": ["船闸", "调度", "通航"],
            "sections": [
                {
                    "heading": "运行组织",
                    "paragraphs": [
                        {
                            "paragraph_type": "evidence_synthesis",
                            "text": "证据综合" * 50,
                            "evidence_unit_ids": ["evidence_1"],
                            "gap_ids": [],
                        }
                    ],
                }
            ],
            "conclusion": {
                "paragraph_type": "evidence_synthesis",
                "text": "研究结论" * 50,
                "evidence_unit_ids": ["evidence_1"],
                "gap_ids": [],
            },
        }

    def test_dynamic_schema_locks_outline_and_evidence_bindings(self) -> None:
        _validate(self.payload, self.schema)

        changed = copy.deepcopy(self.payload)
        changed["sections"][0]["paragraphs"][0]["evidence_unit_ids"] = []
        with self.assertRaisesRegex(ValueError, "review_draft.schema_invalid"):
            _validate(changed, self.schema)

    def test_body_length_contract_rejects_short_text(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["sections"][0]["paragraphs"][0]["text"] = "证" * (
            MIN_BODY_CHARS - 1
        )
        with self.assertRaisesRegex(ValueError, "body_length_invalid"):
            _validate_draft_contract(payload)

        payload["sections"][0]["paragraphs"][0]["text"] = "证" * MIN_BODY_CHARS
        self.assertEqual(_validate_draft_contract(payload), MIN_BODY_CHARS)

        payload["sections"][0]["paragraphs"][0]["text"] = "证" * (
            MAX_BODY_CHARS + 1
        )
        with self.assertRaisesRegex(ValueError, "body_length_invalid"):
            _validate_draft_contract(payload)

    def test_render_exposes_corpus_scope_and_human_source(self) -> None:
        rendered = _render(
            self.payload,
            {"evidence_1": {"claim": "缩短作业时间可提高运行效率。"}},
            {"evidence_1": "论文甲"},
            {
                "source_paper_count": 1,
                "used_paper_count": 1,
                "partial_source_count": 0,
                "source_evidence_count": 1,
                "used_evidence_count": 1,
            },
        )
        self.assertIn("## 资料范围说明", rendered)
        self.assertIn("1 篇论文", rendered)
        self.assertIn("证据来源：论文甲", rendered)
        self.assertIn("缩短作业时间可提高运行效率", rendered)


if __name__ == "__main__":
    unittest.main()
