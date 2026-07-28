from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_contracts import ContractViolation
from shipping_pipeline.llm_statement_role import (
    STATEMENT_ROLE_SCHEMA_VERSION,
    build_statement_role_prompts,
    build_statement_role_schema,
    enforce_statement_role_policy,
    validate_statement_role_review,
)


def record(index: int, field: str, statement: str) -> dict:
    return {
        "statement_id": f"statement_{index:024x}",
        "field": field,
        "index": index,
        "required_support": "grounded_inference_allowed",
        "statement": statement,
        "evidence_unit_ids": [f"evidence_{index:024x}"],
    }


class StatementRoleTests(unittest.TestCase):
    def test_dynamic_schema_requires_every_statement_id_once(self) -> None:
        ids = [f"statement_{index:024x}" for index in (1, 2)]
        schema = build_statement_role_schema(ids)
        rows = schema["properties"]["statement_role_reviews"]
        self.assertEqual(rows["minItems"], 2)
        self.assertEqual(rows["maxItems"], 2)
        self.assertEqual(rows["items"]["properties"]["statement_id"]["enum"], ids)

    def test_prompt_defines_review_use_as_use_not_paper_fact(self) -> None:
        system, user = build_statement_role_prompts(
            paper_id="paper-one",
            paper_title="论文一",
            topic="三峡船舶积压",
            study_type="policy_analysis",
            research_focus="对滚装运输发展进行SWOT分析。",
            statement_records=[record(1, "review_uses", "论文构建了三个模型。")],
        )
        self.assertIn("只复述", system)
        self.assertIn("字段职责不是互斥分类", system)
        self.assertIn("收窄天气影响范围，应判 field_aligned", system)
        self.assertIn("SWOT分析", system)
        prompt_input = json.loads(user)
        self.assertEqual(prompt_input["论文"]["study_type"], "policy_analysis")
        self.assertEqual(prompt_input["论文"]["research_focus"], "对滚装运输发展进行SWOT分析。")
        self.assertEqual(prompt_input["待核验陈述"][0]["field"], "review_uses")
        self.assertNotIn("evidence_unit_ids", prompt_input["待核验陈述"][0])

    def test_validation_rejects_missing_and_duplicate_ids(self) -> None:
        records = [
            record(1, "review_uses", "可用于综述的方法比较。"),
            record(2, "methods", "论文使用仿真模型。"),
        ]
        row = {
            "statement_id": records[0]["statement_id"],
            "verdict": "field_aligned",
            "reason": "陈述说明综述用途。",
            "misaligned_fragments": [],
        }
        for reviews in ([row], [row, row]):
            with self.assertRaises(ContractViolation):
                validate_statement_role_review(
                    {
                        "schema_version": STATEMENT_ROLE_SCHEMA_VERSION,
                        "statement_role_reviews": reviews,
                    },
                    records,
                )

    def test_policy_blocks_review_use_that_only_repeats_paper_content(self) -> None:
        records = [record(1, "review_uses", "论文构建了三个优化模型。")]
        validated = validate_statement_role_review(
            {
                "schema_version": STATEMENT_ROLE_SCHEMA_VERSION,
                "statement_role_reviews": [
                    {
                        "statement_id": records[0]["statement_id"],
                        "verdict": "misclassified",
                        "reason": "只复述论文内容，没有说明综述用途。",
                        "misaligned_fragments": ["论文构建了三个优化模型"],
                    }
                ],
            },
            records,
        )
        with self.assertRaisesRegex(ContractViolation, "role.statement_misclassified"):
            enforce_statement_role_policy(validated)


if __name__ == "__main__":
    unittest.main()
