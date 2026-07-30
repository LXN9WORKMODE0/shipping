from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.research_landscape_contracts import (
    RESEARCH_LANDSCAPE_SCHEMA_VERSION,
    ResearchLandscapeContractError,
    build_research_landscape_generation_schema,
    build_research_landscape_schema,
    load_research_landscape_config,
    validate_research_landscape,
    validate_research_landscape_collection,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ResearchLandscapeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_research_landscape_config(
            PROJECT_ROOT / "config" / "research-landscape-default.json"
        )
        self.contribution_to_paper = {
            "contribution-a1": "paper-a",
            "contribution-a2": "paper-a",
            "contribution-b1": "paper-b",
            "contribution-c1": "paper-c",
        }
        self.schema = build_research_landscape_schema(
            topic="三峡枢纽通航能力提升",
            review_goal="比较提升方法、验证水平和适用边界",
            paper_ids=["paper-a", "paper-b", "paper-c"],
            contribution_to_paper=self.contribution_to_paper,
            corpus_scope="targeted_sample",
            config=self.config,
        )

    def test_collection_requires_explicit_unique_run_ids(self):
        payload = {
            "schema_version": "llm.research_landscape_collection.v1",
            "topic": "三峡枢纽通航能力提升",
            "review_goal": "比较不同提升方法",
            "corpus_scope": "targeted_sample",
            "source_understanding_run_ids": ["run-a", "run-b"],
        }

        validated = validate_research_landscape_collection(payload)

        self.assertEqual(
            validated["source_understanding_run_ids"],
            ["run-a", "run-b"],
        )
        duplicate = copy.deepcopy(payload)
        duplicate["source_understanding_run_ids"] = ["run-a", "run-a"]
        with self.assertRaisesRegex(
            ResearchLandscapeContractError,
            "collection.duplicate_source_run",
        ):
            validate_research_landscape_collection(duplicate)

    def test_valid_landscape_gets_program_generated_ids(self):
        validated = self._validate(self._payload())

        self.assertEqual(
            validated["schema_version"],
            RESEARCH_LANDSCAPE_SCHEMA_VERSION,
        )
        self.assertTrue(validated["landscape_id"].startswith("landscape_"))
        self.assertTrue(validated["dimensions"][0]["dimension_id"].startswith("dimension_"))
        self.assertTrue(validated["relations"][0]["relation_id"].startswith("relation_"))
        self.assertTrue(
            validated["research_evolution"][0]["evolution_id"].startswith(
                "evolution_"
            )
        )
        self.assertTrue(
            validated["disagreements"][0]["disagreement_id"].startswith(
                "disagreement_"
            )
        )
        self.assertTrue(
            validated["corpus_gaps"][0]["corpus_gap_id"].startswith(
                "corpus_gap_"
            )
        )

    def test_every_paper_must_be_dimensioned_or_unmapped(self):
        payload = self._payload()
        payload["unmapped_papers"] = []

        with self.assertRaisesRegex(
            ResearchLandscapeContractError,
            "landscape.paper_coverage_incomplete",
        ):
            self._validate(payload)

    def test_paper_cannot_be_dimensioned_and_unmapped(self):
        payload = self._payload()
        payload["unmapped_papers"] = [
            {"paper_id": "paper-a", "reason": "不适合当前维度。"}
        ]

        with self.assertRaisesRegex(
            ResearchLandscapeContractError,
            "landscape.paper_coverage_overlap",
        ):
            self._validate(payload)

    def test_relation_requires_two_distinct_papers(self):
        payload = self._payload()
        payload["relations"][0]["to_paper_id"] = "paper-a"

        with self.assertRaisesRegex(
            ResearchLandscapeContractError,
            "landscape.relation_same_paper",
        ):
            self._validate(payload)

    def test_relation_requires_contribution_from_each_paper(self):
        payload = self._payload()
        payload["relations"][0]["supporting_contribution_ids"] = [
            "contribution-a1",
            "contribution-a2",
        ]

        with self.assertRaisesRegex(
            ResearchLandscapeContractError,
            "landscape.relation_source_incomplete",
        ):
            self._validate(payload)

    def test_generation_schema_encodes_contribution_ownership(self):
        generation_schema = build_research_landscape_generation_schema(
            self.schema,
            contribution_to_paper=self.contribution_to_paper,
        )
        valid = self._payload()
        Draft202012Validator(generation_schema).validate(valid)

        invalid_dimension = self._payload()
        invalid_dimension["dimensions"][0]["contribution_ids"] = [
            "contribution-a1"
        ]
        self.assertTrue(
            list(
                Draft202012Validator(generation_schema).iter_errors(
                    invalid_dimension
                )
            )
        )

        invalid_relation = self._payload()
        invalid_relation["relations"][0][
            "supporting_contribution_ids"
        ] = ["contribution-a1", "contribution-a2"]
        self.assertTrue(
            list(
                Draft202012Validator(generation_schema).iter_errors(
                    invalid_relation
                )
            )
        )

    def test_dimension_rejects_contribution_owned_by_other_paper(self):
        payload = self._payload()
        payload["dimensions"][0]["paper_ids"] = ["paper-a"]
        payload["dimensions"][0]["contribution_ids"] = [
            "contribution-a1",
            "contribution-b1",
        ]

        with self.assertRaisesRegex(
            ResearchLandscapeContractError,
            "landscape.contribution_owner_mismatch",
        ):
            self._validate(payload)

    def test_targeted_sample_rejects_field_gap_candidates(self):
        payload = self._payload()
        payload["field_gap_candidates"] = [
            {
                "question": "领域是否缺少长期验证？",
                "why_it_matters": "影响外推。",
                "supporting_corpus_gap_indexes": [1],
                "caveat": "需要系统检索确认。",
            }
        ]

        with self.assertRaisesRegex(
            ResearchLandscapeContractError,
            "schema.research_landscape_invalid",
        ):
            self._validate(payload)

    def test_disagreement_requires_distinct_papers_across_positions(self):
        payload = self._payload()
        payload["disagreements"][0]["positions"][1] = {
            "paper_ids": ["paper-a"],
            "statement": "另一立场。",
            "contribution_ids": ["contribution-a2"],
        }

        with self.assertRaisesRegex(
            ResearchLandscapeContractError,
            "landscape.disagreement_paper_overlap",
        ):
            self._validate(payload)

    def test_evolution_rejects_unproven_causal_inheritance(self):
        payload = self._payload()
        payload["research_evolution"][0]["statement"] = (
            "早期系统设计为后续智能平台奠定框架。"
        )

        with self.assertRaisesRegex(
            ResearchLandscapeContractError,
            "landscape.evolution_causal_claim",
        ):
            self._validate(payload)

    def _validate(self, payload: dict):
        return validate_research_landscape(
            payload,
            schema=self.schema,
            contribution_to_paper=self.contribution_to_paper,
        )

    def _payload(self) -> dict:
        return {
            "schema_version": "llm.research_landscape.v1",
            "topic": "三峡枢纽通航能力提升",
            "review_goal": "比较提升方法、验证水平和适用边界",
            "corpus_scope": "targeted_sample",
            "central_problem": "如何提升枢纽通航能力并判断方法成熟度？",
            "dimensions": [
                {
                    "dimension_index": 1,
                    "title": "运行组织优化",
                    "question": "如何通过调度提升效率？",
                    "paper_ids": ["paper-a", "paper-b"],
                    "contribution_ids": [
                        "contribution-a1",
                        "contribution-b1",
                    ],
                }
            ],
            "relations": [
                {
                    "relation_type": "complements",
                    "from_paper_id": "paper-a",
                    "to_paper_id": "paper-b",
                    "statement": "一篇提出方法，另一篇提供运行观察。",
                    "supporting_contribution_ids": [
                        "contribution-a1",
                        "contribution-b1",
                    ],
                }
            ],
            "research_evolution": [
                {
                    "period": "2000-2020",
                    "statement": "关注点由运行诊断扩展到优化方法。",
                    "paper_ids": ["paper-a", "paper-b"],
                    "contribution_ids": [
                        "contribution-a1",
                        "contribution-b1",
                    ],
                }
            ],
            "disagreements": [
                {
                    "question": "是否应优先扩建设施？",
                    "positions": [
                        {
                            "paper_ids": ["paper-a"],
                            "statement": "优先优化调度。",
                            "contribution_ids": ["contribution-a1"],
                        },
                        {
                            "paper_ids": ["paper-b"],
                            "statement": "需要设施与调度协同。",
                            "contribution_ids": ["contribution-b1"],
                        },
                    ],
                }
            ],
            "corpus_gaps": [
                {
                    "question": "缺少哪些长期工程验证？",
                    "why_it_matters": "影响方法外推。",
                    "related_dimension_indexes": [1],
                }
            ],
            "unmapped_papers": [
                {
                    "paper_id": "paper-c",
                    "reason": "只提供外围背景，无法进入当前维度。",
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
