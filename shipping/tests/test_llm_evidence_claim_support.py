from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_contracts import ContractViolation
from shipping_pipeline.llm_evidence_claim_support import (
    EVIDENCE_CLAIM_SUPPORT_SCHEMA_VERSION,
    build_evidence_claim_support_prompts,
    build_evidence_claim_support_schema,
    enforce_evidence_claim_support_policy,
    validate_evidence_claim_support_review,
)


def evidence_unit(index: int) -> dict:
    return {
        "evidence_unit_id": f"evidence_{index:024x}",
        "claim": "式4.25第一项为期望收益，第二项为罚函数。",
        "citations": [{"quote": "Max: R = A - B (4.25)"}],
        "confidence": "medium",
        "caveats": ["引文没有解释两项含义，需要结合上下文。"],
    }


class EvidenceClaimSupportTests(unittest.TestCase):
    def test_dynamic_schema_requires_every_evidence_id_once(self) -> None:
        ids = [f"evidence_{index:024x}" for index in (1, 2)]
        schema = build_evidence_claim_support_schema(ids)
        rows = schema["properties"]["evidence_claim_reviews"]
        self.assertEqual(rows["minItems"], 2)
        self.assertEqual(rows["maxItems"], 2)
        self.assertEqual(rows["items"]["properties"]["evidence_unit_id"]["enum"], ids)

    def test_prompt_excludes_relevance_and_marks_formula_interpretation_boundary(self) -> None:
        system, user = build_evidence_claim_support_prompts(
            paper_id="paper-one",
            paper_title="论文一",
            evidence_units=[evidence_unit(1)],
        )
        payload = json.loads(user)
        self.assertNotIn("relevance", payload["待核验证据单元"][0])
        self.assertIn("公式只能直接支持公式的存在", system)
        self.assertIn("语义蕴含", system)
        self.assertIn("几乎不可能", system)
        self.assertIn("步骤如下", system)
        self.assertIn("存在性陈述”必须判 directly_supported", system)
        self.assertIn("不能推翻 quotes 已明确表达的内容", system)
        self.assertIn("拟研究、计划、尝试", system)
        self.assertIn("禁止保留初步判断、自我纠正", system)
        self.assertIn("caveats", json.dumps(payload, ensure_ascii=False))

    def test_validation_rejects_missing_duplicate_and_unknown_ids(self) -> None:
        units = [evidence_unit(1), evidence_unit(2)]
        row = {
            "evidence_unit_id": units[0]["evidence_unit_id"],
            "verdict": "directly_supported",
            "reason": "引文直接支持。",
            "unsupported_fragments": [],
        }
        for reviews in ([row], [row, row]):
            with self.assertRaises(ContractViolation):
                validate_evidence_claim_support_review(
                    {
                        "schema_version": EVIDENCE_CLAIM_SUPPORT_SCHEMA_VERSION,
                        "evidence_claim_reviews": reviews,
                    },
                    units,
                )

    def test_policy_blocks_formula_claim_that_is_only_partially_supported(self) -> None:
        units = [evidence_unit(1)]
        validated = validate_evidence_claim_support_review(
            {
                "schema_version": EVIDENCE_CLAIM_SUPPORT_SCHEMA_VERSION,
                "evidence_claim_reviews": [
                    {
                        "evidence_unit_id": units[0]["evidence_unit_id"],
                        "verdict": "partially_supported",
                        "reason": "公式没有解释两项含义。",
                        "unsupported_fragments": ["第一项为期望收益，第二项为罚函数"],
                    }
                ],
            },
            units,
        )
        with self.assertRaisesRegex(
            ContractViolation,
            "support.evidence_claim_not_fully_supported",
        ):
            enforce_evidence_claim_support_policy(validated)


if __name__ == "__main__":
    unittest.main()
