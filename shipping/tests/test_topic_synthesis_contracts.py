from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.topic_synthesis_contracts import (
    COLLECTION_SCHEMA_VERSION,
    EVIDENCE_MAP_SCHEMA_VERSION,
    OUTLINE_SCHEMA_VERSION,
    TopicSynthesisContractError,
    build_topic_evidence_map_schema,
    build_topic_review_outline_schema,
    load_topic_synthesis_config,
    validate_collection,
    validate_topic_evidence_map,
    validate_topic_review_outline,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TopicSynthesisContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_topic_synthesis_config(
            PROJECT_ROOT / "config" / "topic-synthesis-default.json"
        )
        self.evidence_to_paper = {
            "evidence_a": "paper-a",
            "evidence_b": "paper-b",
            "evidence_c": "paper-a",
        }

    def test_default_config_has_explicit_token_and_depth_limits(self):
        self.assertEqual(self.config.max_themes, 8)
        self.assertEqual(self.config.max_themes_per_evidence, 2)
        self.assertEqual(self.config.max_synthesis_units, 16)
        self.assertEqual(self.config.theme_map_output_base_tokens, 4096)
        self.assertEqual(self.config.outline_max_output_tokens, 65536)

    def test_collection_requires_two_unique_source_runs_and_exact_fields(self):
        valid = validate_collection(
            {
                "schema_version": COLLECTION_SCHEMA_VERSION,
                "topic": "综述主题",
                "source_run_ids": ["run-a", "run-b"],
            }
        )
        self.assertEqual(valid["source_run_ids"], ["run-a", "run-b"])

        duplicate = copy.deepcopy(valid)
        duplicate["source_run_ids"] = ["run-a", "run-a"]
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "collection.duplicate_source_run",
        ):
            validate_collection(duplicate)

        unknown = copy.deepcopy(valid)
        unknown["unexpected"] = True
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "schema.topic_synthesis_collection_invalid",
        ):
            validate_collection(unknown)

        path_escape = copy.deepcopy(valid)
        path_escape["source_run_ids"] = ["run-a", "../run-b"]
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "collection.source_run_id_invalid",
        ):
            validate_collection(path_escape)

    def test_evidence_map_covers_every_evidence_with_bounded_multi_theme_assignment(self):
        payload = self._evidence_map()
        validated = validate_topic_evidence_map(
            payload,
            topic="综述主题",
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        self.assertEqual(validated["schema_version"], EVIDENCE_MAP_SCHEMA_VERSION)
        self.assertTrue(validated["themes"][0]["theme_id"].startswith("theme_"))
        self.assertTrue(validated["corpus_gaps"][0]["gap_id"].startswith("gap_"))

        missing = copy.deepcopy(payload)
        missing["themes"] = missing["themes"][:1]
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "evidence_map.coverage_mismatch",
        ):
            validate_topic_evidence_map(
                missing,
                topic="综述主题",
                evidence_to_paper=self.evidence_to_paper,
                config=self.config,
            )

        multi_theme = copy.deepcopy(payload)
        multi_theme["themes"][1]["assignments"].append(
            {"evidence_unit_id": "evidence_a", "role": "context"}
        )
        multi_theme_validated = validate_topic_evidence_map(
            multi_theme,
            topic="综述主题",
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        self.assertEqual(
            sum(
                item["evidence_unit_id"] == "evidence_a"
                for theme in multi_theme_validated["themes"]
                for item in theme["assignments"]
            ),
            2,
        )

        duplicate_within_theme = copy.deepcopy(payload)
        duplicate_within_theme["themes"][1]["assignments"].append(
            {"evidence_unit_id": "evidence_c", "role": "context"}
        )
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "evidence_map.duplicate_assignment_within_theme",
        ):
            validate_topic_evidence_map(
                duplicate_within_theme,
                topic="综述主题",
                evidence_to_paper=self.evidence_to_paper,
                config=self.config,
            )

        over_limit = copy.deepcopy(multi_theme)
        over_limit["themes"].append(
            {
                "title": "运行风险",
                "focus": "识别运行中断和组织风险。",
                "synthesis_value": "补充风险管理视角。",
                "relation_type": "single_source",
                "assignments": [
                    {
                        "evidence_unit_id": "evidence_a",
                        "role": "qualification",
                    }
                ],
            }
        )
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "evidence_map.assignment_limit_exceeded",
        ):
            validate_topic_evidence_map(
                over_limit,
                topic="综述主题",
                evidence_to_paper=self.evidence_to_paper,
                config=self.config,
            )

        duplicate_gap = copy.deepcopy(payload)
        duplicate_gap["corpus_gaps"].append(
            copy.deepcopy(duplicate_gap["corpus_gaps"][0])
        )
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "evidence_map.duplicate_gap",
        ):
            validate_topic_evidence_map(
                duplicate_gap,
                topic="综述主题",
                evidence_to_paper=self.evidence_to_paper,
                config=self.config,
            )

    def test_single_source_relation_is_derived_from_distinct_papers(self):
        payload = self._evidence_map()
        one_paper_mapping = dict(self.evidence_to_paper)
        one_paper_mapping["evidence_b"] = "paper-a"
        validated = validate_topic_evidence_map(
            payload,
            topic="综述主题",
            evidence_to_paper=one_paper_mapping,
            config=self.config,
        )
        self.assertEqual(validated["themes"][0]["relation_type"], "single_source")
        self.assertEqual(
            validated["derivations"]["relation_types"][0]["input_relation_type"],
            "complementary",
        )

        invalid = self._evidence_map()
        invalid["themes"][0]["relation_type"] = "single_source"
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "evidence_map.relation_paper_count_invalid",
        ):
            validate_topic_evidence_map(
                invalid,
                topic="综述主题",
                evidence_to_paper=self.evidence_to_paper,
                config=self.config,
            )

    def test_navigation_fields_may_contain_digits(self):
        payload = self._evidence_map()
        payload["themes"][0]["synthesis_value"] = "用于串联二〇二〇年与2021年的研究。"
        payload["corpus_gaps"][0]["question"] = "是否覆盖3类组织措施？"
        validated = validate_topic_evidence_map(
            payload,
            topic="综述主题",
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        self.assertIn("2021", validated["themes"][0]["synthesis_value"])

    def test_outline_allows_no_search_direction_when_corpus_has_no_gap(self):
        evidence_map_payload = self._evidence_map()
        evidence_map_payload["corpus_gaps"] = []
        theme_map = validate_topic_evidence_map(
            evidence_map_payload,
            topic="综述主题",
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        payload = self._outline(theme_map)
        payload["search_directions"] = []
        validated = validate_topic_review_outline(
            payload,
            theme_map=theme_map,
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        self.assertEqual(validated["search_directions"], [])

    def test_dynamic_schemas_bind_input_ids(self):
        evidence_schema = build_topic_evidence_map_schema(
            list(self.evidence_to_paper),
            self.config,
        )
        assignment_enum = evidence_schema["properties"]["themes"]["items"][
            "properties"
        ]["assignments"]["items"]["properties"]["evidence_unit_id"]["enum"]
        self.assertEqual(set(assignment_enum), set(self.evidence_to_paper))

        theme_map = validate_topic_evidence_map(
            self._evidence_map(),
            topic="综述主题",
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        outline_schema = build_topic_review_outline_schema(
            theme_ids=[row["theme_id"] for row in theme_map["themes"]],
            assigned_evidence_ids=["evidence_a", "evidence_b"],
            gap_ids=[row["gap_id"] for row in theme_map["corpus_gaps"]],
            config=self.config,
        )
        unit_theme_enum = outline_schema["properties"]["synthesis_units"]["items"][
            "properties"
        ]["theme_ids"]["items"]["enum"]
        self.assertEqual(
            set(unit_theme_enum),
            {row["theme_id"] for row in theme_map["themes"]},
        )
        gap_enum = outline_schema["properties"]["search_directions"]["items"][
            "properties"
        ]["source_gap_ids"]["items"]["enum"]
        self.assertEqual(gap_enum, [theme_map["corpus_gaps"][0]["gap_id"]])
        section_schema = outline_schema["properties"]["sections"]["items"]
        paragraph_schema = section_schema["properties"]["paragraphs"]["items"]
        self.assertNotIn("theme_ids", section_schema["properties"])
        self.assertNotIn("paragraph_role", paragraph_schema["properties"])
        self.assertNotIn("evidence_unit_ids", paragraph_schema["properties"])

    def test_outline_requires_source_diversity_theme_coverage_and_valid_indexes(self):
        theme_map = validate_topic_evidence_map(
            self._evidence_map(),
            topic="综述主题",
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        payload = self._outline(theme_map)
        validated = validate_topic_review_outline(
            payload,
            theme_map=theme_map,
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        self.assertEqual(validated["schema_version"], OUTLINE_SCHEMA_VERSION)
        self.assertNotIn("unit_index", validated["synthesis_units"][0])
        self.assertTrue(
            validated["synthesis_units"][0]["synthesis_unit_id"].startswith(
                "synthesis_"
            )
        )
        self.assertIn(
            "synthesis_unit_ids",
            validated["sections"][0]["paragraphs"][0],
        )
        self.assertEqual(
            validated["sections"][0]["theme_ids"],
            [theme_map["themes"][0]["theme_id"]],
        )
        self.assertEqual(
            validated["sections"][0]["paragraphs"][0]["evidence_unit_ids"],
            ["evidence_a", "evidence_b"],
        )
        self.assertEqual(
            validated["sections"][0]["paragraphs"][0]["paragraph_role"],
            "evidence_synthesis",
        )

        one_paper = copy.deepcopy(payload)
        one_paper_mapping = dict(self.evidence_to_paper)
        one_paper_mapping["evidence_b"] = "paper-a"
        one_paper_validated = validate_topic_review_outline(
            one_paper,
            theme_map=theme_map,
            evidence_to_paper=one_paper_mapping,
            config=self.config,
        )
        self.assertEqual(
            one_paper_validated["synthesis_units"][0]["unit_type"],
            "single_source_context",
        )

        multi_paper_as_single = copy.deepcopy(payload)
        multi_paper_as_single["synthesis_units"][0][
            "unit_type"
        ] = "single_source_context"
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "outline.unit_paper_count_invalid",
        ):
            validate_topic_review_outline(
                multi_paper_as_single,
                theme_map=theme_map,
                evidence_to_paper=self.evidence_to_paper,
                config=self.config,
            )

        repeated_theme = copy.deepcopy(payload)
        repeated_theme["sections"].append(
            {
                "title": "综合讨论",
                "purpose": "回到需求与能力主题形成综合讨论。",
                "paragraphs": [
                    {
                        "synthesis_move": "从设施约束返回整体问题。",
                        "synthesis_unit_indexes": [1, 2],
                    }
                ],
            }
        )
        repeated_validated = validate_topic_review_outline(
            repeated_theme,
            theme_map=theme_map,
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        transition = repeated_validated["sections"][2]["paragraphs"][0]
        self.assertEqual(
            transition["evidence_unit_ids"],
            ["evidence_a", "evidence_b", "evidence_c"],
        )
        self.assertEqual(
            set(repeated_validated["sections"][2]["theme_ids"]),
            {row["theme_id"] for row in theme_map["themes"]},
        )

        bad_index = copy.deepcopy(payload)
        bad_index["synthesis_units"][1]["unit_index"] = 3
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "outline.unit_indexes_invalid",
        ):
            validate_topic_review_outline(
                bad_index,
                theme_map=theme_map,
                evidence_to_paper=self.evidence_to_paper,
                config=self.config,
            )

        wrong_theme_evidence = copy.deepcopy(payload)
        wrong_theme_evidence["synthesis_units"][1]["evidence_unit_ids"] = [
            "evidence_a"
        ]
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "outline.unit_evidence_outside_themes",
        ):
            validate_topic_review_outline(
                wrong_theme_evidence,
                theme_map=theme_map,
                evidence_to_paper=self.evidence_to_paper,
                config=self.config,
            )

        evidence_gap = copy.deepcopy(payload)
        evidence_gap["synthesis_units"].append(
            {
                "unit_index": 3,
                "unit_type": "corpus_gap",
                "synthesis_statement": "现有语料尚缺少措施横向比较。",
                "theme_ids": [theme_map["themes"][0]["theme_id"]],
                "evidence_unit_ids": [],
                "gap_ids": [theme_map["corpus_gaps"][0]["gap_id"]],
            }
        )
        evidence_gap["sections"][0]["paragraphs"][0][
            "synthesis_unit_indexes"
        ] = [1, 3]
        evidence_gap_validated = validate_topic_review_outline(
            evidence_gap,
            theme_map=theme_map,
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        derived_evidence_gap = evidence_gap_validated["sections"][0][
            "paragraphs"
        ][0]
        self.assertEqual(
            derived_evidence_gap["paragraph_role"],
            "evidence_gap_synthesis",
        )
        self.assertEqual(
            derived_evidence_gap["evidence_unit_ids"],
            ["evidence_a", "evidence_b"],
        )
        self.assertEqual(
            derived_evidence_gap["gap_ids"],
            [theme_map["corpus_gaps"][0]["gap_id"]],
        )

        gap_paragraph = copy.deepcopy(payload)
        gap_paragraph["synthesis_units"].append(
            {
                "unit_index": 3,
                "unit_type": "corpus_gap",
                "synthesis_statement": "现有语料尚缺少措施横向比较。",
                "theme_ids": [theme_map["themes"][0]["theme_id"]],
                "evidence_unit_ids": [],
                "gap_ids": [theme_map["corpus_gaps"][0]["gap_id"]],
            }
        )
        gap_paragraph["sections"].append(
            {
                "title": "研究缺口",
                "purpose": "归纳现有语料尚未回答的问题。",
                "paragraphs": [
                    {
                        "synthesis_move": "说明措施横向比较仍然不足。",
                        "synthesis_unit_indexes": [3],
                    }
                ],
            }
        )
        gap_validated = validate_topic_review_outline(
            gap_paragraph,
            theme_map=theme_map,
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        derived_gap = gap_validated["sections"][2]["paragraphs"][0]
        self.assertEqual(derived_gap["paragraph_role"], "research_gap")
        self.assertEqual(derived_gap["evidence_unit_ids"], [])
        self.assertEqual(
            derived_gap["gap_ids"],
            [theme_map["corpus_gaps"][0]["gap_id"]],
        )

        unplaced_candidate = copy.deepcopy(payload)
        unplaced_candidate["synthesis_units"].append(
            {
                "unit_index": 3,
                "unit_type": "complement",
                "synthesis_statement": "保留一个未安排进段落的同主题候选综合单元。",
                "theme_ids": [theme_map["themes"][0]["theme_id"]],
                "evidence_unit_ids": ["evidence_a", "evidence_b"],
                "gap_ids": [],
            }
        )
        unplaced_validated = validate_topic_review_outline(
            unplaced_candidate,
            theme_map=theme_map,
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        self.assertEqual(len(unplaced_validated["unplaced_synthesis_unit_ids"]), 1)
        self.assertEqual(
            unplaced_validated["derivations"]["unplaced_synthesis_units"][0][
                "source_unit_index"
            ],
            3,
        )

        non_gap_with_gap = copy.deepcopy(payload)
        non_gap_with_gap["synthesis_units"][0]["gap_ids"] = [
            theme_map["corpus_gaps"][0]["gap_id"]
        ]
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "schema.topic_review_outline_invalid",
        ):
            validate_topic_review_outline(
                non_gap_with_gap,
                theme_map=theme_map,
                evidence_to_paper=self.evidence_to_paper,
                config=self.config,
            )

        missing_theme_unit = copy.deepcopy(payload)
        missing_theme_unit["synthesis_units"] = missing_theme_unit[
            "synthesis_units"
        ][:1]
        with self.assertRaisesRegex(
            TopicSynthesisContractError,
            "outline.theme_unit_coverage_invalid",
        ):
            validate_topic_review_outline(
                missing_theme_unit,
                theme_map=theme_map,
                evidence_to_paper=self.evidence_to_paper,
                config=self.config,
            )

        missing_gap_direction = copy.deepcopy(payload)
        missing_gap_direction["search_directions"] = []
        no_priority = validate_topic_review_outline(
            missing_gap_direction,
            theme_map=theme_map,
            evidence_to_paper=self.evidence_to_paper,
            config=self.config,
        )
        self.assertEqual(
            no_priority["unprioritized_gap_ids"],
            [theme_map["corpus_gaps"][0]["gap_id"]],
        )

    def _evidence_map(self) -> dict:
        return {
            "schema_version": EVIDENCE_MAP_SCHEMA_VERSION,
            "themes": [
                {
                    "title": "需求与能力矛盾",
                    "focus": "识别需求增长与通航能力之间的关系。",
                    "synthesis_value": "用于组织瓶颈形成的跨论文证据。",
                    "relation_type": "complementary",
                    "assignments": [
                        {
                            "evidence_unit_id": "evidence_a",
                            "role": "main_support",
                        },
                        {
                            "evidence_unit_id": "evidence_b",
                            "role": "main_support",
                        },
                    ],
                },
                {
                    "title": "设施适应性",
                    "focus": "识别设施条件对通航组织的限制。",
                    "synthesis_value": "补充物理约束维度。",
                    "relation_type": "single_source",
                    "assignments": [
                        {
                            "evidence_unit_id": "evidence_c",
                            "role": "qualification",
                        }
                    ],
                },
            ],
            "unassigned_evidence": [],
            "corpus_gaps": [
                {
                    "gap_type": "implementation",
                    "question": "现有研究是否充分比较了不同组织措施？",
                    "why_it_matters": "关系到策略评价是否完整。",
                }
            ],
        }

    def _outline(self, theme_map: dict) -> dict:
        theme_a, theme_b = [row["theme_id"] for row in theme_map["themes"]]
        return {
            "schema_version": OUTLINE_SCHEMA_VERSION,
            "working_title": "三峡航运组织研究综述",
            "central_question": "现有研究如何解释积压并提出疏导路径？",
            "synthesis_units": [
                {
                    "unit_index": 1,
                    "unit_type": "complement",
                    "synthesis_statement": "不同研究从需求和能力两端解释瓶颈。",
                    "theme_ids": [theme_a],
                    "evidence_unit_ids": ["evidence_a", "evidence_b"],
                    "gap_ids": [],
                },
                {
                    "unit_index": 2,
                    "unit_type": "single_source_context",
                    "synthesis_statement": "设施条件构成需要单独说明的约束背景。",
                    "theme_ids": [theme_b],
                    "evidence_unit_ids": ["evidence_c"],
                    "gap_ids": [],
                },
            ],
            "sections": [
                {
                    "title": "需求与能力",
                    "purpose": "解释积压形成的供需基础。",
                    "paragraphs": [
                        {
                            "synthesis_move": "比较不同论文对需求和能力的观察。",
                            "synthesis_unit_indexes": [1],
                        }
                    ],
                },
                {
                    "title": "设施约束",
                    "purpose": "补充通航设施条件。",
                    "paragraphs": [
                        {
                            "synthesis_move": "说明设施条件为何构成独立约束。",
                            "synthesis_unit_indexes": [2],
                        }
                    ],
                },
            ],
            "search_directions": [
                {
                    "priority": "high",
                    "question": "不同疏导措施是否有可比效果证据？",
                    "reason": "当前语料缺少统一比较。",
                    "related_theme_ids": [theme_a, theme_b],
                    "source_gap_ids": (
                        [theme_map["corpus_gaps"][0]["gap_id"]]
                        if theme_map["corpus_gaps"]
                        else []
                    ),
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
