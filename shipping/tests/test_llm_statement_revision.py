from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_contracts import ContractViolation
from shipping_pipeline.llm_statement_revision import (
    STATEMENT_REVISION_SCHEMA_VERSION,
    build_statement_revision_prompts,
    build_statement_revision_schema,
    validate_and_apply_statement_revision,
)
from shipping_pipeline.llm_statement_support import build_statement_records


def original_analysis() -> dict:
    return {
        "schema_version": "llm.paper_analysis.v4",
        "research_focus": {"statement": "研究船闸能力。", "evidence_unit_ids": ["e1"]},
        "study_type": "modeling",
        "methods": [
            {"statement": "采用复杂模型分析并证明最优。", "evidence_unit_ids": ["e1"]}
        ],
        "core_findings": [{"statement": "能力受限。", "evidence_unit_ids": ["e2"]}],
        "limitations": [
            {"statement": "仅分析重箱，因此精度不足。", "evidence_unit_ids": ["e3"]}
        ],
        "review_uses": [{"statement": "可用于描述船闸能力。", "evidence_unit_ids": ["e2"]}],
        "unresolved_questions": [],
        "evidence_dispositions": [
            {"evidence_unit_id": "e1", "disposition": "used", "reason_code": "supports_claim"},
            {"evidence_unit_id": "e2", "disposition": "used", "reason_code": "supports_claim"},
            {"evidence_unit_id": "e3", "disposition": "used", "reason_code": "supports_claim"},
            {
                "evidence_unit_id": "e4",
                "disposition": "peripheral",
                "reason_code": "background_only",
            },
        ],
    }


def evidence_units() -> list[dict]:
    return [
        {"evidence_unit_id": evidence_id, "claim": evidence_id, "citations": []}
        for evidence_id in ["e1", "e2", "e3", "e4"]
    ]


def blocking_records() -> list[dict]:
    records = build_statement_records(original_analysis())
    selected = [row for row in records if row["field"] in {"methods", "limitations"}]
    return [
        {
            **row,
            "verdict": "partially_supported",
            "reason": "陈述含有不受支持内容。",
            "unsupported_fragments": ["不受支持内容"],
        }
        for row in selected
    ]


def valid_payload() -> dict:
    records = blocking_records()
    return {
        "schema_version": STATEMENT_REVISION_SCHEMA_VERSION,
        "revisions": [
            {
                "statement_id": records[0]["statement_id"],
                "action": "replace",
                "replacements": [
                    {"statement": "采用模型分析。", "evidence_unit_ids": ["e1"]}
                ],
            },
            {
                "statement_id": records[1]["statement_id"],
                "action": "delete",
                "replacements": [],
            },
        ],
        "released_evidence_dispositions": [
            {
                "evidence_unit_id": "e3",
                "disposition": "peripheral",
                "reason_code": "supporting_detail",
            }
        ],
    }


class StatementRevisionTests(unittest.TestCase):
    def test_dynamic_schema_binds_each_statement_to_original_evidence(self) -> None:
        records = blocking_records()
        schema = build_statement_revision_schema(records, ["e3"])
        variants = schema["properties"]["revisions"]["items"]["oneOf"]

        self.assertEqual(len(variants), 2)
        self.assertEqual(
            variants[0]["properties"]["replacements"]["items"]["properties"]
            ["evidence_unit_ids"]["items"]["enum"],
            ["e1"],
        )
        self.assertEqual(
            schema["properties"]["released_evidence_dispositions"]["items"]
            ["properties"]["evidence_unit_id"]["enum"],
            ["e3"],
        )

    def test_revision_replaces_deletes_releases_and_revalidates_full_analysis(self) -> None:
        validated, revised, released = validate_and_apply_statement_revision(
            valid_payload(),
            original_analysis=original_analysis(),
            blocking_records=blocking_records(),
            evidence_units=evidence_units(),
        )

        self.assertEqual(validated["schema_version"], STATEMENT_REVISION_SCHEMA_VERSION)
        self.assertEqual(revised["methods"][0]["statement"], "采用模型分析。")
        self.assertEqual(revised["limitations"], [])
        self.assertEqual(released, ["e3"])
        dispositions = {row["evidence_unit_id"]: row for row in revised["evidence_dispositions"]}
        self.assertEqual(dispositions["e3"]["reason_code"], "supporting_detail")
        self.assertEqual(dispositions["e4"]["reason_code"], "background_only")

    def test_revision_rejects_new_evidence_noop_and_release_mismatch(self) -> None:
        cases: list[tuple[dict, str]] = []
        new_evidence = valid_payload()
        new_evidence["revisions"][0]["replacements"][0]["evidence_unit_ids"] = ["e2"]
        cases.append((new_evidence, "schema.statement_revision_invalid"))

        no_change = valid_payload()
        no_change["revisions"][0]["replacements"] = [
            copy.deepcopy(original_analysis()["methods"][0])
        ]
        cases.append((no_change, "revision.statement_unchanged"))

        release_mismatch = valid_payload()
        release_mismatch["released_evidence_dispositions"] = []
        cases.append((release_mismatch, "released_evidence_disposition_invalid"))

        for payload, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ContractViolation, message):
                    validate_and_apply_statement_revision(
                        payload,
                        original_analysis=original_analysis(),
                        blocking_records=blocking_records(),
                        evidence_units=evidence_units(),
                    )

    def test_prompt_contains_only_failed_evidence_and_explicit_release_rules(self) -> None:
        system_prompt, user_prompt = build_statement_revision_prompts(
            paper_id="paper-one",
            paper_title="论文一",
            topic="船闸能力",
            original_analysis=original_analysis(),
            blocking_records=blocking_records(),
            evidence_units=evidence_units(),
        )
        prompt = json.loads(user_prompt)

        self.assertIn("禁止增加其他 evidence ID", system_prompt)
        self.assertIn("这是单次修订", system_prompt)
        self.assertEqual(
            {row["evidence_unit_id"] for row in prompt["失败陈述已引用证据"]},
            {"e1", "e3"},
        )
        self.assertEqual(prompt["可能因修订释放的证据ID"], ["e3"])


if __name__ == "__main__":
    unittest.main()
