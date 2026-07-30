from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.review_framework_contracts import (
    REVIEW_FRAMEWORK_DRAFT_SCHEMA_VERSION,
    REVIEW_FRAMEWORK_SCHEMA_VERSION,
    ReviewFrameworkContractError,
    build_review_framework_draft_schema,
    derive_review_framework,
    validate_review_framework_draft,
)


class ReviewFrameworkContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.landscape = {
            "schema_version": "llm.research_landscape.v1",
            "landscape_id": "landscape-source-1",
            "topic": "三峡枢纽通航能力提升",
            "review_goal": "比较提升方法、验证水平和适用边界",
            "corpus_scope": "targeted_sample",
            "dimensions": [
                {
                    "dimension_index": 1,
                    "dimension_id": "dimension-1",
                    "title": "运行组织",
                    "paper_ids": ["paper-b", "paper-a"],
                    "contribution_ids": [
                        "contribution-b1",
                        "contribution-a1",
                    ],
                },
                {
                    "dimension_index": 2,
                    "dimension_id": "dimension-2",
                    "title": "工程扩能",
                    "paper_ids": ["paper-c", "paper-a"],
                    "contribution_ids": [
                        "contribution-c1",
                        "contribution-a2",
                    ],
                },
            ],
            "disagreements": [
                {
                    "disagreement_id": "controversy-1",
                    "question": "优先改善调度还是工程能力？",
                    "positions": [
                        {
                            "paper_ids": ["paper-d"],
                            "statement": "优先改善调度。",
                            "contribution_ids": ["contribution-d1"],
                        },
                        {
                            "paper_ids": ["paper-a"],
                            "statement": "工程与调度需要协同。",
                            "contribution_ids": ["contribution-a3"],
                        },
                    ],
                }
            ],
            "corpus_gaps": [
                {
                    "corpus_gap_id": "gap-1",
                    "question": "长期效果如何？",
                    "why_it_matters": "影响外推。",
                    "related_dimension_indexes": [1, 2],
                }
            ],
            "unmapped_papers": [],
        }
        self.schema = build_review_framework_draft_schema(
            topic=self.landscape["topic"],
            review_goal=self.landscape["review_goal"],
            dimension_indexes=[1, 2],
            controversy_ids=["controversy-1"],
            corpus_gap_indexes=[1],
        )
        self.references = {
            "paper-a": self._reference("paper-a", "ref-a"),
            "paper-b": self._reference("paper-b", "ref-b"),
            "paper-c": self._reference("paper-c", "ref-c"),
            "paper-d": self._reference("paper-d", "ref-d"),
        }

    def test_valid_draft_derives_public_framework(self):
        Draft202012Validator.check_schema(self.schema)

        result = derive_review_framework(
            self._draft(),
            landscape=self.landscape,
            reference_by_paper=self.references,
            source_landscape_id="landscape-source-1",
        )

        self.assertEqual(
            self.schema["properties"]["schema_version"]["const"],
            REVIEW_FRAMEWORK_DRAFT_SCHEMA_VERSION,
        )
        self.assertEqual(
            result["schema_version"],
            REVIEW_FRAMEWORK_SCHEMA_VERSION,
        )
        self.assertEqual(result["topic"], self.landscape["topic"])
        self.assertEqual(result["review_goal"], self.landscape["review_goal"])
        self.assertEqual(result["corpus_scope"], "targeted_sample")
        self.assertEqual(
            result["source_landscape_id"],
            "landscape-source-1",
        )
        self.assertTrue(result["framework_id"].startswith("framework_"))
        self.assertEqual(result["unused_papers"], [])
        self.assertEqual(result["bibliography_issues"], [])
        self.assertTrue(
            all(
                section["section_id"].startswith("section_")
                for section in result["sections"]
            )
        )

    def test_explicit_review_goal_may_refine_landscape_goal(self):
        draft = self._draft()
        draft["review_goal"] = "重点比较工程应用成熟度"

        result = derive_review_framework(
            draft,
            landscape=self.landscape,
            reference_by_paper=self.references,
            source_landscape_id="landscape-source-1",
        )

        self.assertEqual(
            result["review_goal"],
            "重点比较工程应用成熟度",
        )

    def test_comparison_cannot_name_paper_outside_section(self):
        draft = self._draft()
        draft["sections"][0]["required_comparisons"] = [
            "比较paper-c与本节论文的结论。"
        ]

        with self.assertRaises(
            ReviewFrameworkContractError,
        ) as raised:
            derive_review_framework(
                draft,
                landscape=self.landscape,
                reference_by_paper=self.references,
                source_landscape_id="landscape-source-1",
            )

        self.assertEqual(
            raised.exception.code,
            "framework.comparison_source_outside_section",
        )

    def test_unknown_enum_and_indexes_are_rejected(self):
        invalid_type = self._draft()
        invalid_type["sections"][1]["section_type"] = "discussion"
        with self.assertRaisesRegex(
            ReviewFrameworkContractError,
            "schema.review_framework_draft_invalid",
        ):
            validate_review_framework_draft(
                invalid_type,
                self.schema,
                self.landscape,
            )

        for field, value in (
            ("dimension_indexes", [99]),
            ("controversy_ids", ["unknown-controversy"]),
            ("corpus_gap_indexes", [99]),
        ):
            invalid_index = self._draft()
            invalid_index["sections"][1][field] = value
            with self.assertRaisesRegex(
                ReviewFrameworkContractError,
                "schema.review_framework_draft_invalid",
            ):
                validate_review_framework_draft(
                    invalid_index,
                    self.schema,
                    self.landscape,
                )

    def test_model_generated_machine_id_is_rejected(self):
        draft = self._draft()
        draft["framework_id"] = "model-id"
        draft["sections"][0]["section_id"] = "model-section-id"

        with self.assertRaisesRegex(
            ReviewFrameworkContractError,
            "schema.review_framework_draft_invalid",
        ):
            validate_review_framework_draft(
                draft,
                self.schema,
                self.landscape,
            )

    def test_section_indexes_and_type_order_are_enforced(self):
        invalid_index = self._draft()
        invalid_index["sections"][1]["section_index"] = 3
        with self.assertRaisesRegex(
            ReviewFrameworkContractError,
            "framework.section_indexes_invalid",
        ):
            validate_review_framework_draft(
                invalid_index,
                self.schema,
                self.landscape,
            )

        invalid_order = self._draft()
        invalid_order["sections"][0]["section_type"] = "body"
        invalid_order["sections"][0]["required_comparisons"] = ["建立范围。"]
        invalid_order["sections"][1]["section_type"] = "introduction"
        with self.assertRaisesRegex(
            ReviewFrameworkContractError,
            "framework.introduction_order_invalid",
        ):
            validate_review_framework_draft(
                invalid_order,
                self.schema,
                self.landscape,
            )

    def test_all_landscape_dimensions_must_enter_a_body(self):
        draft = self._draft()
        draft["sections"][1]["dimension_indexes"] = [1]

        with self.assertRaisesRegex(
            ReviewFrameworkContractError,
            "framework.body_dimension_coverage_incomplete",
        ):
            validate_review_framework_draft(
                draft,
                self.schema,
                self.landscape,
            )

    def test_body_requires_comparison(self):
        draft = self._draft()
        draft["sections"][1]["required_comparisons"] = []

        with self.assertRaisesRegex(
            ReviewFrameworkContractError,
            "schema.review_framework_draft_invalid",
        ):
            validate_review_framework_draft(
                draft,
                self.schema,
                self.landscape,
            )

    def test_missing_bibliography_mapping_reports_code_and_path(self):
        references = copy.deepcopy(self.references)
        del references["paper-c"]

        with self.assertRaises(
            ReviewFrameworkContractError,
        ) as raised:
            derive_review_framework(
                self._draft(),
                landscape=self.landscape,
                reference_by_paper=references,
                source_landscape_id="landscape-source-1",
            )

        self.assertEqual(raised.exception.code, "framework.reference_missing")
        self.assertIn("paper-c", raised.exception.path)

    def test_ids_are_stable_across_reference_mapping_order(self):
        first = derive_review_framework(
            self._draft(),
            landscape=self.landscape,
            reference_by_paper=self.references,
            source_landscape_id="landscape-source-1",
        )
        reversed_references = dict(reversed(list(self.references.items())))
        second = derive_review_framework(
            copy.deepcopy(self._draft()),
            landscape=copy.deepcopy(self.landscape),
            reference_by_paper=reversed_references,
            source_landscape_id="landscape-source-1",
        )

        self.assertEqual(first["framework_id"], second["framework_id"])
        self.assertEqual(
            [row["section_id"] for row in first["sections"]],
            [row["section_id"] for row in second["sections"]],
        )

        changed_bibliography = copy.deepcopy(self.references)
        changed_bibliography["paper-c"]["status"] = "partial"
        changed_bibliography["paper-c"]["missing_fields"] = ["year"]
        third = derive_review_framework(
            self._draft(),
            landscape=self.landscape,
            reference_by_paper=changed_bibliography,
            source_landscape_id="landscape-source-1",
        )
        self.assertEqual(first["framework_id"], third["framework_id"])
        self.assertEqual(
            [row["section_id"] for row in first["sections"]],
            [row["section_id"] for row in third["sections"]],
        )

    def test_papers_and_contributions_are_derived_deterministically(self):
        draft = self._draft()
        draft["sections"][1]["dimension_indexes"] = [2, 1]
        result = derive_review_framework(
            draft,
            landscape=self.landscape,
            reference_by_paper=self.references,
            source_landscape_id="landscape-source-1",
        )

        body = result["sections"][1]
        self.assertEqual(body["dimension_indexes"], [1, 2])
        self.assertEqual(
            body["paper_ids"],
            ["paper-b", "paper-a", "paper-c", "paper-d"],
        )
        self.assertEqual(
            body["contribution_ids"],
            [
                "contribution-b1",
                "contribution-a1",
                "contribution-c1",
                "contribution-a2",
                "contribution-d1",
                "contribution-a3",
            ],
        )
        self.assertEqual(
            body["citation_keys"],
            ["ref-b", "ref-a", "ref-c", "ref-d"],
        )

    def test_schema_is_valid_without_controversies_or_corpus_gaps(self):
        schema = build_review_framework_draft_schema(
            topic=self.landscape["topic"],
            review_goal=self.landscape["review_goal"],
            dimension_indexes=[1, 2],
            controversy_ids=[],
            corpus_gap_indexes=[],
        )

        Draft202012Validator.check_schema(schema)
        draft = self._draft()
        draft["sections"][1]["controversy_ids"] = []
        draft["sections"][1]["corpus_gap_indexes"] = []
        draft["sections"][2]["corpus_gap_indexes"] = []
        Draft202012Validator(schema).validate(draft)

    def test_partial_reference_is_exposed_in_section_and_top_level(self):
        references = copy.deepcopy(self.references)
        references["paper-c"]["status"] = "partial"
        references["paper-c"]["missing_fields"] = ["year"]
        result = derive_review_framework(
            self._draft(),
            landscape=self.landscape,
            reference_by_paper=references,
            source_landscape_id="landscape-source-1",
        )

        self.assertEqual(
            result["sections"][1]["bibliography_status"],
            "partial",
        )
        self.assertEqual(
            result["bibliography_issues"],
            [
                {
                    "paper_id": "paper-c",
                    "citation_key": "ref-c",
                    "status": "partial",
                    "missing_fields": ["year"],
                    "warnings": [],
                }
            ],
        )

    def _draft(self) -> dict:
        return {
            "schema_version": REVIEW_FRAMEWORK_DRAFT_SCHEMA_VERSION,
            "topic": self.landscape["topic"],
            "review_goal": self.landscape["review_goal"],
            "working_title": "三峡枢纽通航能力提升方法综述",
            "central_question": "不同提升方法如何互补且边界何在？",
            "sections": [
                {
                    "section_index": 1,
                    "title": "引言",
                    "section_type": "introduction",
                    "question": "综述面对什么问题？",
                    "purpose": "界定主题和语料范围。",
                    "dimension_indexes": [1],
                    "required_comparisons": [],
                    "controversy_ids": [],
                    "corpus_gap_indexes": [],
                    "corpus_limitations": ["语料不是系统检索样本。"],
                },
                {
                    "section_index": 2,
                    "title": "提升路径比较",
                    "section_type": "body",
                    "question": "组织优化和工程扩能如何互补？",
                    "purpose": "比较机制、验证水平和适用边界。",
                    "dimension_indexes": [1, 2],
                    "required_comparisons": ["比较作用机制和验证水平。"],
                    "controversy_ids": ["controversy-1"],
                    "corpus_gap_indexes": [1],
                    "corpus_limitations": ["缺少长期跟踪研究。"],
                },
                {
                    "section_index": 3,
                    "title": "结论",
                    "section_type": "conclusion",
                    "question": "可以形成哪些审慎结论？",
                    "purpose": "汇总发现并说明边界。",
                    "dimension_indexes": [2],
                    "required_comparisons": [],
                    "controversy_ids": [],
                    "corpus_gap_indexes": [1],
                    "corpus_limitations": ["结论限于当前语料。"],
                },
            ],
        }

    @staticmethod
    def _reference(
        paper_id: str,
        citation_key: str,
    ) -> dict:
        return {
            "paper_id": paper_id,
            "citation_key": citation_key,
            "status": "complete",
            "missing_fields": [],
            "warnings": [],
        }


if __name__ == "__main__":
    unittest.main()
