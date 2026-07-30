from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.research_understanding_contracts import (
    PAPER_UNDERSTANDING_SCHEMA_VERSION,
    ResearchUnderstandingContractError,
    build_paper_understanding_schema,
    load_research_understanding_config,
    validate_paper_understanding,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ResearchUnderstandingContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_research_understanding_config(
            PROJECT_ROOT / "config" / "research-understanding-default.json"
        )
        self.material_by_id = {
            "paper-one:card-1": {"material_id": "paper-one:card-1"},
            "paper-one:card-2": {"material_id": "paper-one:card-2"},
            "paper-one:card-3": {"material_id": "paper-one:card-3"},
        }
        self.evidence_by_id = {
            "evidence-one": {"evidence_unit_id": "evidence-one"},
        }
        self.schema = build_paper_understanding_schema(
            paper_id="paper-one",
            paper_title="论文一",
            topic="综述主题",
            material_ids=list(self.material_by_id),
            evidence_unit_ids=list(self.evidence_by_id),
            config=self.config,
        )

    def test_valid_payload_gets_program_generated_ids(self):
        validated = self._validate(self._payload())

        self.assertEqual(
            validated["schema_version"],
            PAPER_UNDERSTANDING_SCHEMA_VERSION,
        )
        self.assertTrue(validated["understanding_id"].startswith("understanding_"))
        self.assertTrue(
            validated["research_questions"][0]["research_question_id"].startswith(
                "question_"
            )
        )
        self.assertTrue(validated["methods"][0]["method_id"].startswith("method_"))
        self.assertTrue(
            validated["contributions"][0]["contribution_id"].startswith(
                "contribution_"
            )
        )

    def test_excluded_paper_must_not_have_review_roles(self):
        payload = self._payload()
        payload["paper_relevance"] = "exclude"
        payload["review_roles"] = []

        validated = self._validate(payload)

        self.assertEqual(validated["paper_relevance"], "exclude")
        self.assertEqual(validated["review_roles"], [])
        self.assertTrue(
            validated["limitations"][0]["limitation_id"].startswith("limitation_")
        )
        self.assertNotIn(
            "research_question_id",
            self._payload()["research_questions"][0],
        )

    def test_unknown_enum_is_rejected(self):
        payload = self._payload()
        payload["contributions"][0]["result_type"] = "real_world_effect"

        with self.assertRaisesRegex(
            ResearchUnderstandingContractError,
            "schema.paper_understanding_invalid",
        ):
            self._validate(payload)

    def test_empty_research_question_is_rejected(self):
        payload = self._payload()
        payload["research_questions"][0]["question"] = ""

        with self.assertRaisesRegex(
            ResearchUnderstandingContractError,
            "schema.paper_understanding_invalid",
        ):
            self._validate(payload)

    def test_contribution_requires_material_source(self):
        payload = self._payload()
        payload["contributions"][0]["material_ids"] = []

        with self.assertRaisesRegex(
            ResearchUnderstandingContractError,
            "schema.paper_understanding_invalid",
        ):
            self._validate(payload)

    def test_unknown_material_and_evidence_ids_are_rejected(self):
        unknown_material = self._payload()
        unknown_material["methods"][0]["material_ids"] = ["paper-one:missing"]
        with self.assertRaisesRegex(
            ResearchUnderstandingContractError,
            "schema.paper_understanding_invalid",
        ):
            self._validate(unknown_material)

        unknown_evidence = self._payload()
        unknown_evidence["contributions"][0]["evidence_unit_ids"] = [
            "evidence-missing"
        ]
        with self.assertRaisesRegex(
            ResearchUnderstandingContractError,
            "schema.paper_understanding_invalid",
        ):
            self._validate(unknown_evidence)

    def test_model_generated_ids_and_unknown_fields_are_rejected(self):
        payload = self._payload()
        payload["contributions"][0]["contribution_id"] = "model-id"

        with self.assertRaisesRegex(
            ResearchUnderstandingContractError,
            "schema.paper_understanding_invalid",
        ):
            self._validate(payload)

    def test_review_role_indexes_must_resolve(self):
        payload = self._payload()
        payload["review_roles"][0]["contribution_indexes"] = [2]

        with self.assertRaisesRegex(
            ResearchUnderstandingContractError,
            "understanding.review_role_contribution_unknown",
        ):
            self._validate(payload)

    def test_duplicate_semantic_items_are_rejected(self):
        payload = self._payload()
        payload["research_questions"].append(
            copy.deepcopy(payload["research_questions"][0])
        )

        with self.assertRaisesRegex(
            ResearchUnderstandingContractError,
            "understanding.duplicate_item",
        ):
            self._validate(payload)

    def _validate(self, payload: dict) -> dict:
        return validate_paper_understanding(
            payload,
            schema=self.schema,
            material_by_id=self.material_by_id,
            evidence_by_id=self.evidence_by_id,
        )

    @staticmethod
    def _payload() -> dict:
        return {
            "schema_version": PAPER_UNDERSTANDING_SCHEMA_VERSION,
            "paper_id": "paper-one",
            "paper_title": "论文一",
            "topic": "综述主题",
            "paper_relevance": "core",
            "research_questions": [
                {
                    "question": "论文如何提高船闸运行效率？",
                    "problem_category": "运行组织优化",
                    "material_ids": ["paper-one:card-1"],
                }
            ],
            "study_context": {
                "study_object": "三峡船闸",
                "data_sources": ["operational_records"],
                "time_scope": "2014年",
                "geographic_scope": "三峡坝区",
                "material_ids": ["paper-one:card-1"],
            },
            "methods": [
                {
                    "method_category": "descriptive_analysis",
                    "method_name": "运行数据比较",
                    "description": "比较不同组织方式的作业时间。",
                    "material_ids": ["paper-one:card-2"],
                }
            ],
            "contributions": [
                {
                    "statement": "缩短进闸时间具有运行挖潜空间。",
                    "result_type": "historical_observation",
                    "validation_level": "field_observation",
                    "evidence_strength": "moderate",
                    "strength_rationale": "提供现场运行数据，但没有对照试验。",
                    "material_ids": ["paper-one:card-2"],
                    "evidence_unit_ids": ["evidence-one"],
                }
            ],
            "limitations": [
                {
                    "statement": "结果未报告长期实施后的实际通过量变化。",
                    "basis": "reviewer_inferred",
                    "material_ids": ["paper-one:card-3"],
                }
            ],
            "review_roles": [
                {
                    "role": "method_comparison",
                    "reason": "可用于比较运行组织措施。",
                    "contribution_indexes": [1],
                }
            ],
            "unresolved_questions": [
                {
                    "question": "长期实施后实际通过量提高多少？",
                    "basis": "paper_scope_boundary",
                }
            ],
            "keywords": ["船闸调度", "运行效率"],
        }


if __name__ == "__main__":
    unittest.main()
