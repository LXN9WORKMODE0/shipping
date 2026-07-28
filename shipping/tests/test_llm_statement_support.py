from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_contracts import ContractViolation
from shipping_pipeline.llm_statement_support import (
    STATEMENT_SUPPORT_SYSTEM_PROMPT,
    STATEMENT_SUPPORT_SCHEMA_VERSION,
    build_statement_records,
    build_statement_support_schema,
    blocking_statement_support_reviews,
    enforce_statement_support_policy,
    validate_statement_support_review,
)


def analysis() -> dict:
    return {
        "research_focus": {"statement": "研究船闸能力。", "evidence_unit_ids": ["e1"]},
        "study_type": "modeling",
        "methods": [{"statement": "采用模型分析。", "evidence_unit_ids": ["e1"]}],
        "core_findings": [{"statement": "能力受限。", "evidence_unit_ids": ["e2"]}],
        "limitations": [],
        "review_uses": [
            {"statement": "可用于解释船舶积压。", "evidence_unit_ids": ["e2"]}
        ],
        "unresolved_questions": [],
    }


class StatementSupportTests(unittest.TestCase):
    def test_prompt_distinguishes_entailed_paraphrase_from_added_specificity(self) -> None:
        self.assertIn("“3种情景”", STATEMENT_SUPPORT_SYSTEM_PROMPT)
        self.assertIn("“多种情景”", STATEMENT_SUPPORT_SYSTEM_PROMPT)
        self.assertIn("仅写“小船”时补充具体吨位", STATEMENT_SUPPORT_SYSTEM_PROMPT)
        self.assertIn("模型使用 Frank-wolf 法", STATEMENT_SUPPORT_SYSTEM_PROMPT)
        self.assertIn("逐字引文只有公式而没有解释各项含义", STATEMENT_SUPPORT_SYSTEM_PROMPT)
        self.assertIn("reason 只能写最终依据的一句中文", STATEMENT_SUPPORT_SYSTEM_PROMPT)
        self.assertIn("required_support=direct 时只能选择", STATEMENT_SUPPORT_SYSTEM_PROMPT)

    def test_statement_records_cover_every_free_text_and_mark_review_inference(self) -> None:
        records = build_statement_records(analysis())

        self.assertEqual(len(records), 4)
        self.assertEqual(len({row["statement_id"] for row in records}), 4)
        self.assertEqual(
            [row["required_support"] for row in records],
            ["direct", "direct", "direct", "grounded_inference_allowed"],
        )
        self.assertEqual(records[0]["field"], "research_focus")

    def test_dynamic_schema_requires_each_statement_id_once(self) -> None:
        schema = build_statement_support_schema(["statement_" + "1" * 24, "statement_" + "2" * 24])
        rows = schema["properties"]["statement_reviews"]

        self.assertEqual(rows["minItems"], 2)
        self.assertEqual(rows["maxItems"], 2)
        self.assertEqual(
            rows["items"]["properties"]["statement_id"]["enum"],
            ["statement_" + "1" * 24, "statement_" + "2" * 24],
        )

    def test_policy_rejects_inference_for_direct_statement_but_allows_review_use(self) -> None:
        records = build_statement_records(analysis())
        payload = {
            "schema_version": STATEMENT_SUPPORT_SCHEMA_VERSION,
            "statement_reviews": [
                {
                    "statement_id": row["statement_id"],
                    "verdict": (
                        "grounded_inference"
                        if row["field"] in {"core_findings", "review_uses"}
                        else "directly_supported"
                    ),
                    "reason": "测试判定。",
                    "unsupported_fragments": [],
                }
                for row in records
            ],
        }
        validated = validate_statement_support_review(payload, records)

        with self.assertRaisesRegex(ContractViolation, "direct_statement_only_inferred"):
            enforce_statement_support_policy(validated, records)

        core = next(row for row in validated["statement_reviews"] if row["statement_id"] == records[2]["statement_id"])
        core["verdict"] = "directly_supported"
        enforce_statement_support_policy(validated, records)

    def test_policy_rejects_partially_supported_statement(self) -> None:
        records = build_statement_records(analysis())
        payload = {
            "schema_version": STATEMENT_SUPPORT_SCHEMA_VERSION,
            "statement_reviews": [
                {
                    "statement_id": row["statement_id"],
                    "verdict": "partially_supported" if index == 0 else "directly_supported",
                    "reason": "存在超出证据的片段。" if index == 0 else "证据直接支持。",
                    "unsupported_fragments": ["超出证据"] if index == 0 else [],
                }
                for index, row in enumerate(records)
            ],
        }
        validated = validate_statement_support_review(payload, records)

        with self.assertRaisesRegex(ContractViolation, "statement_not_fully_supported"):
            enforce_statement_support_policy(validated, records)

        blocking = blocking_statement_support_reviews(validated, records)
        self.assertEqual([row["statement_id"] for row in blocking], [records[0]["statement_id"]])
        self.assertEqual(blocking[0]["unsupported_fragments"], ["超出证据"])


if __name__ == "__main__":
    unittest.main()
