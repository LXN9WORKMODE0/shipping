from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.topic_review_contracts import (
    TOPIC_BRIEF_SCHEMA_VERSION,
    TOPIC_REVISIT_SCHEMA_VERSION,
    TOPIC_SCOPE_SCHEMA_VERSION,
    TopicReviewContractError,
    build_topic_brief_schema,
    build_topic_revisit_schema,
    build_topic_scope_schema,
    load_topic_review_config,
    validate_topic_brief,
    validate_topic_revisit,
    validate_topic_scope,
)


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "topic-review-default.json"


def scope_payload(relevance: str = "supporting", selected: list[str] | None = None) -> dict:
    ids = ["m1"] if selected is None else selected
    return {
        "schema_version": TOPIC_SCOPE_SCHEMA_VERSION,
        "paper_relevance": relevance,
        "relevance_reason": "论文提供当前主题的局部证据。",
        "topic_summary": "论文讨论三峡船闸运行问题。",
        "selected_materials": [
            {
                "material_id": material_id,
                "priority": "primary",
                "intended_use": "finding",
                "selection_reason": "直接讨论综述主题。",
            }
            for material_id in ids
        ],
    }


def evidence_unit(evidence_id: str = "evidence-one") -> dict:
    return {
        "evidence_unit_id": evidence_id,
        "claim": "三峡船闸日均运行13闸次。",
        "evidence_type": "result",
        "citations": [
            {
                "material_id": "m1",
                "quote_id": "quote-one",
                "quote": "三峡船闸日均运行13闸次。",
            }
        ],
        "relevance": "可用于说明通航压力。",
        "confidence": "high",
        "caveats": [],
    }


def brief_payload(evidence_id: str = "evidence-one", statement: str = "三峡船闸日均运行13闸次。") -> dict:
    return {
        "schema_version": TOPIC_BRIEF_SCHEMA_VERSION,
        "topic_contribution": {
            "statement": "论文提供了船闸运行压力的量化材料。",
            "evidence_unit_ids": [evidence_id],
        },
        "key_points": [
            {
                "point_type": "finding",
                "statement": statement,
                "evidence_unit_ids": [evidence_id],
            }
        ],
        "review_uses": [
            {
                "use_type": "support",
                "statement": "可用于说明三峡船闸面临通航压力。",
                "evidence_unit_ids": [evidence_id],
            }
        ],
        "cautions": [],
        "evidence_gaps": [],
    }


class TopicReviewContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_topic_review_config(CONFIG_PATH)

    def test_default_config_has_explicit_depth_limits(self) -> None:
        self.assertEqual(6, self.config.max_selected_materials["core"])
        self.assertEqual(0, self.config.max_selected_materials["exclude"])
        self.assertEqual(1, self.config.max_evidence_units_per_material)
        self.assertEqual(2, self.config.max_revisit_materials["core"])
        self.assertEqual(0, self.config.max_revisit_materials["peripheral"])
        self.assertEqual(2, self.config.max_evidence_gaps)

    def test_scope_rejects_selection_beyond_depth_limit(self) -> None:
        payload = scope_payload("peripheral", ["m1", "m2", "m3"])
        with self.assertRaisesRegex(TopicReviewContractError, "scope.selection_limit_exceeded"):
            validate_topic_scope(payload, {"m1", "m2", "m3"}, self.config)

    def test_scope_prompt_schema_binds_limit_to_relevance(self) -> None:
        schema = build_topic_scope_schema(
            ["m1", "m2", "m3", "m4", "m5", "m6"],
            config=self.config,
        )
        limits = {
            row["if"]["properties"]["paper_relevance"]["const"]: row["then"][
                "properties"
            ]["selected_materials"]
            for row in schema["allOf"]
        }
        self.assertEqual(limits["core"], {"minItems": 1, "maxItems": 6})
        self.assertEqual(
            limits["supporting"],
            {"minItems": 1, "maxItems": 4},
        )
        self.assertEqual(
            limits["peripheral"],
            {"minItems": 1, "maxItems": 2},
        )
        self.assertEqual(limits["exclude"], {"minItems": 0, "maxItems": 0})

    def test_scope_rejects_duplicate_and_unknown_materials(self) -> None:
        duplicate = scope_payload("supporting", ["m1", "m1"])
        with self.assertRaisesRegex(TopicReviewContractError, "scope.duplicate_material"):
            validate_topic_scope(duplicate, {"m1"}, self.config)
        unknown = scope_payload("supporting", ["missing"])
        with self.assertRaisesRegex(TopicReviewContractError, "schema.topic_scope_invalid"):
            validate_topic_scope(unknown, {"m1"}, self.config)

    def test_scope_requires_selection_except_for_excluded_paper(self) -> None:
        with self.assertRaisesRegex(TopicReviewContractError, "scope.selection_missing"):
            validate_topic_scope(scope_payload("core", []), {"m1"}, self.config)
        excluded = validate_topic_scope(scope_payload("exclude", []), {"m1"}, self.config)
        self.assertEqual("exclude", excluded["paper_relevance"])
        with self.assertRaisesRegex(TopicReviewContractError, "scope.selection_limit_exceeded"):
            validate_topic_scope(scope_payload("exclude", ["m1"]), {"m1"}, self.config)

    def test_brief_schema_binds_ids_and_depth_limit(self) -> None:
        schema = build_topic_brief_schema(
            ["e1", "e2"],
            relevance="peripheral",
            config=self.config,
        )
        self.assertEqual(2, schema["properties"]["key_points"]["maxItems"])
        ids = schema["properties"]["topic_contribution"]["properties"]["evidence_unit_ids"]
        self.assertEqual(["e1", "e2"], ids["items"]["enum"])

        bound = build_topic_brief_schema(
            ["e1", "e2"],
            relevance="peripheral",
            config=self.config,
            evidence_claims_by_id={"e1": "观点一。", "e2": "观点二。"},
        )
        alternatives = bound["properties"]["key_points"]["items"]["oneOf"]
        self.assertEqual("观点一。", alternatives[0]["properties"]["statement"]["const"])
        self.assertEqual(["e2"], alternatives[1]["properties"]["evidence_unit_ids"]["const"])

        required = build_topic_brief_schema(
            ["e1", "e2"],
            relevance="peripheral",
            config=self.config,
            evidence_claims_by_id={"e1": "观点一。", "e2": "观点二。"},
            required_key_point_evidence_ids=["e2"],
        )
        contains = required["properties"]["key_points"]["allOf"][0]["contains"]
        self.assertEqual(
            ["e2"],
            contains["properties"]["evidence_unit_ids"]["const"],
        )

    def test_brief_rejects_unknown_evidence_and_unsupported_number(self) -> None:
        with self.assertRaisesRegex(TopicReviewContractError, "schema.topic_brief_invalid"):
            validate_topic_brief(
                brief_payload("missing"),
                [evidence_unit()],
                relevance="supporting",
                config=self.config,
            )
        unsupported_number = brief_payload()
        unsupported_number["review_uses"][0]["statement"] = "可用于说明2010年的通航压力。"
        with self.assertRaisesRegex(TopicReviewContractError, "brief.numeric_fact_unsupported"):
            validate_topic_brief(
                unsupported_number,
                [evidence_unit()],
                relevance="supporting",
                config=self.config,
            )

    def test_brief_accepts_evidence_backed_number_in_navigation(self) -> None:
        payload = brief_payload()
        payload["cautions"] = [
            {
                "statement": "该结论来自日均13闸次的运行材料。",
                "evidence_unit_ids": ["evidence-one"],
            }
        ]
        validated = validate_topic_brief(
            payload,
            [evidence_unit()],
            relevance="supporting",
            config=self.config,
        )
        self.assertEqual(
            validated["cautions"][0]["statement"],
            "该结论来自日均13闸次的运行材料。",
        )

    def test_brief_gap_number_must_be_backed_by_verified_evidence(self) -> None:
        supported = brief_payload()
        supported["evidence_gaps"] = [
            {
                "gap_type": "effect",
                "question": "日均13闸次后对等待时间的影响如何？",
                "why_it_matters": "用于评价调度效果。",
            }
        ]
        validated = validate_topic_brief(
            supported,
            [evidence_unit()],
            relevance="supporting",
            config=self.config,
        )
        self.assertIn("13", validated["evidence_gaps"][0]["question"])

        unsupported = brief_payload()
        unsupported["evidence_gaps"] = [
            {
                "gap_type": "effect",
                "question": "日均30闸次后对等待时间的影响如何？",
                "why_it_matters": "用于评价调度效果。",
            }
        ]
        with self.assertRaisesRegex(
            TopicReviewContractError,
            "brief.gap_numeric_fact_unsupported",
        ):
            validate_topic_brief(
                unsupported,
                [evidence_unit()],
                relevance="supporting",
                config=self.config,
            )

    def test_brief_accepts_evidence_backed_content(self) -> None:
        validated = validate_topic_brief(
            brief_payload(),
            [evidence_unit()],
            relevance="supporting",
            config=self.config,
        )
        self.assertEqual(TOPIC_BRIEF_SCHEMA_VERSION, validated["schema_version"])

    def test_key_point_must_copy_one_unique_evidence_claim(self) -> None:
        paraphrased = brief_payload()
        paraphrased["key_points"][0]["statement"] = "三峡船闸每天运行13闸次。"
        with self.assertRaisesRegex(TopicReviewContractError, "schema.topic_brief_invalid"):
            validate_topic_brief(
                paraphrased,
                [evidence_unit()],
                relevance="supporting",
                config=self.config,
            )

        duplicate = brief_payload()
        duplicate["key_points"].append(dict(duplicate["key_points"][0]))
        with self.assertRaisesRegex(TopicReviewContractError, "brief.duplicate_key_point_evidence"):
            validate_topic_brief(
                duplicate,
                [evidence_unit()],
                relevance="supporting",
                config=self.config,
            )

    def test_config_rejects_unknown_fields(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        payload["unknown"] = True
        path = Path(__file__).resolve().parents[1] / ".test_tmp" / "bad-topic-config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            with self.assertRaisesRegex(TopicReviewContractError, "config.fields_invalid"):
                load_topic_review_config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_brief_accepts_data_as_a_key_point_type(self) -> None:
        payload = brief_payload()
        payload["key_points"][0]["point_type"] = "data"
        validated = validate_topic_brief(
            payload,
            [evidence_unit()],
            relevance="supporting",
            config=self.config,
        )
        self.assertEqual(validated["key_points"][0]["point_type"], "data")

    def test_brief_accepts_definition_as_a_key_point_type(self) -> None:
        payload = brief_payload()
        payload["key_points"][0]["point_type"] = "definition"
        validated = validate_topic_brief(
            payload,
            [evidence_unit()],
            relevance="supporting",
            config=self.config,
        )
        self.assertEqual(validated["key_points"][0]["point_type"], "definition")

    def test_brief_accepts_result_as_a_key_point_type(self) -> None:
        payload = brief_payload()
        payload["key_points"][0]["point_type"] = "result"
        validated = validate_topic_brief(
            payload,
            [evidence_unit()],
            relevance="supporting",
            config=self.config,
        )
        self.assertEqual(validated["key_points"][0]["point_type"], "result")

    def test_brief_accepts_mechanism_and_recommendation_as_key_point_types(self) -> None:
        for point_type in ("mechanism", "recommendation"):
            with self.subTest(point_type=point_type):
                payload = brief_payload()
                payload["key_points"][0]["point_type"] = point_type
                validated = validate_topic_brief(
                    payload,
                    [evidence_unit()],
                    relevance="supporting",
                    config=self.config,
                )
                self.assertEqual(validated["key_points"][0]["point_type"], point_type)

    def test_config_rejects_revisit_limit_above_final_point_limit(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        payload["max_revisit_materials"]["supporting"] = 5
        path = Path(__file__).resolve().parents[1] / ".test_tmp" / "bad-revisit-limit.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            with self.assertRaisesRegex(TopicReviewContractError, "config.revisit_limit_invalid"):
                load_topic_review_config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_revisit_schema_binds_omitted_materials_gaps_and_limit(self) -> None:
        schema = build_topic_revisit_schema(
            ["m2", "m3"],
            ["gap-1"],
            max_materials=1,
        )
        selected = schema["properties"]["selected_materials"]
        self.assertEqual(1, selected["maxItems"])
        self.assertEqual(
            ["m2", "m3"],
            selected["items"]["properties"]["material_id"]["enum"],
        )
        self.assertEqual(
            ["gap-1"],
            selected["items"]["properties"]["gap_ids"]["items"]["enum"],
        )

    def test_revisit_rejects_status_mismatch_and_duplicate_material(self) -> None:
        base = {
            "schema_version": TOPIC_REVISIT_SCHEMA_VERSION,
            "status": "selected",
            "review_summary": "发现一个可补充的主题维度。",
            "selected_materials": [
                {
                    "material_id": "m2",
                    "gap_ids": ["gap-1"],
                    "intended_use": "finding",
                    "selection_reason": "直接回答缺口。",
                    "novelty_reason": "现有证据未覆盖该内容。",
                }
            ],
        }
        validated = validate_topic_revisit(
            base,
            {"m2", "m3"},
            {"gap-1"},
            max_materials=2,
        )
        self.assertEqual("selected", validated["status"])

        mismatch = dict(base)
        mismatch["status"] = "no_candidate"
        with self.assertRaisesRegex(
            TopicReviewContractError,
            "revisit.no_candidate_has_selection",
        ):
            validate_topic_revisit(
                mismatch,
                {"m2", "m3"},
                {"gap-1"},
                max_materials=2,
            )

        duplicate = dict(base)
        duplicate["selected_materials"] = [
            dict(base["selected_materials"][0]),
            dict(base["selected_materials"][0]),
        ]
        with self.assertRaisesRegex(TopicReviewContractError, "revisit.duplicate_material"):
            validate_topic_revisit(
                duplicate,
                {"m2", "m3"},
                {"gap-1"},
                max_materials=2,
            )


if __name__ == "__main__":
    unittest.main()
