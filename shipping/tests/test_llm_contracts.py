from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_contracts import (
    ASSESSMENT_REASON_CODES,
    ContractViolation,
    EVIDENCE_BATCH_SCHEMA_VERSION,
    MAX_EVIDENCE_CITATIONS,
    MAX_EVIDENCE_UNITS_PER_MATERIAL,
    NON_USED_EVIDENCE_DISPOSITION_SCHEMA_VERSION,
    PAPER_ANALYSIS_SCHEMA_VERSION,
    PAPER_ANALYSIS_DRAFT_SCHEMA_VERSION,
    SECTION_SUMMARY_SCHEMA_VERSION,
    assign_evidence_unit_ids,
    build_evidence_anchor_id,
    build_evidence_unit_id,
    build_evidence_batch_schema,
    build_paper_analysis_schema,
    build_non_used_evidence_disposition_schema,
    build_section_summary_schema,
    finalize_paper_analysis,
    validate_evidence_batch,
    validate_paper_analysis,
    validate_paper_analysis_draft,
    validate_non_used_evidence_dispositions,
    validate_section_summary,
    verify_material_coverage,
)
from shipping_pipeline.llm_quotes import build_quote_candidates


def material(material_id: str, extract: str = "本文采用仿真方法分析船舶积压，并给出等待时间。") -> dict:
    return {
        "material_id": material_id,
        "paper_id": "paper-one",
        "paper_title": "论文一",
        "clean_title": "研究方法",
        "extract": extract,
        "content_kind": "prose",
        "source_span": {"path": "normalized/document.md", "start_line": 10, "end_line": 12},
        "confidence_flags": [],
        "quality_flags": [],
    }


def quote_candidates(materials: list[dict] | None = None) -> list[dict]:
    selected = materials or [material("m1")]
    return build_quote_candidates(
        [
            {
                "material_id": row["material_id"],
                "content_kind": row.get("content_kind", "prose"),
                "extract": row["extract"],
            }
            for row in selected
        ]
    )


def quote_id(material_id: str = "m1") -> str:
    return next(row["quote_id"] for row in quote_candidates([material(material_id)]) if row["material_id"] == material_id)


def valid_batch() -> dict:
    return {
        "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
        "material_results": [
            {
                "material_id": "m1",
                "disposition": "evidence",
                "reason_code": "direct_evidence",
                "evidence_units": [
                    {
                        "claim": "论文采用仿真方法研究船舶积压。",
                        "evidence_type": "method",
                        "citations": [{"quote_id": quote_id()}],
                        "relevance": "可用于综述的方法比较。",
                        "confidence": "high",
                        "caveats": [],
                    }
                ],
            }
        ],
    }


def model_unit(payload: dict) -> dict:
    return payload["material_results"][0]["evidence_units"][0]


class LLMContractTests(unittest.TestCase):
    def test_dynamic_evidence_schema_binds_batch_and_output_size(self) -> None:
        schema = build_evidence_batch_schema(3)
        results = schema["properties"]["material_results"]
        evidence_units = results["items"]["properties"]["evidence_units"]

        self.assertEqual(results["minItems"], 3)
        self.assertEqual(results["maxItems"], 3)
        self.assertEqual(
            evidence_units["maxItems"],
            MAX_EVIDENCE_UNITS_PER_MATERIAL,
        )
        self.assertEqual(
            evidence_units["items"]["properties"]["citations"]["maxItems"],
            MAX_EVIDENCE_CITATIONS,
        )
        self.assertEqual(
            results["items"]["properties"]["reason_code"]["enum"],
            ASSESSMENT_REASON_CODES,
        )
        with self.assertRaisesRegex(ValueError, "batch_card_count"):
            build_evidence_batch_schema(0)

    def test_dynamic_evidence_schema_binds_quote_ids_to_each_material(self) -> None:
        first_quote = "quote_111111111111111111111111"
        second_quote = "quote_222222222222222222222222"
        schema = build_evidence_batch_schema(
            2,
            material_ids=["m1", "m2"],
            quote_candidate_ids_by_material={"m1": [first_quote], "m2": [second_quote]},
        )

        bindings = {
            rule["if"]["properties"]["material_id"]["const"]: rule["then"]["properties"]
            ["evidence_units"]["items"]["properties"]["citations"]["items"]["properties"]
            ["quote_id"]["enum"]
            for rule in schema["properties"]["material_results"]["items"]["allOf"]
            if "material_id" in rule.get("if", {}).get("properties", {})
        }

        self.assertEqual(bindings, {"m1": [first_quote], "m2": [second_quote]})

    def test_evidence_claim_rejects_numeric_facts_absent_from_selected_quotes(self) -> None:
        materials = [material("m1", "三峡船闸实际通过能力约3 000万吨。")]
        candidates = quote_candidates(materials)
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "三峡船闸设计能力5000万吨，实际通过能力约3000万吨。"
        unit["citations"] = [{"quote_id": candidates[0]["quote_id"]}]

        with self.assertRaisesRegex(
            ContractViolation,
            "evidence_claim_numeric_fact_unsupported",
        ):
            validate_evidence_batch(payload, materials, candidates)

    def test_evidence_claim_accepts_numeric_facts_with_spacing_variants(self) -> None:
        materials = [material("m1", "设计能力为5 000万吨，实际约3 000万吨，降低50%,随后稳定。")] 
        candidates = quote_candidates(materials)
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "设计能力为5000万吨，实际约3000万吨，降低50%。"
        unit["citations"] = [{"quote_id": candidates[0]["quote_id"]}]

        validated = validate_evidence_batch(payload, materials, candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_evidence_claim_accepts_decimal_with_thousands_separator(self) -> None:
        materials = [material("m1", "2010年下行货运量达到4,280.9万吨，占设计能力85.6%。")]
        candidates = quote_candidates(materials)
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "2010年下行货运量达到4280.9万吨，占设计能力85.6%。"
        unit["citations"] = [{"quote_id": candidates[0]["quote_id"]}]

        validated = validate_evidence_batch(payload, materials, candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_evidence_claim_accepts_ocr_spaced_digits_inside_math(self) -> None:
        materials = [material("m1", "计算得通过能力为 $P = 5 ~ 1 5 2$ 万t。")]
        candidates = quote_candidates(materials)
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "计算得到通过能力5152万t。"
        unit["citations"] = [{"quote_id": candidates[0]["quote_id"]}]

        validated = validate_evidence_batch(payload, materials, candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_evidence_claim_accepts_ocr_spaced_decimal_inside_math(self) -> None:
        materials = [
            material(
                "m1",
                r"综合指数为 $S = K_R \times 0 . 2 5 + K_A \times 0 . 3 0$。",
            )
        ]
        candidates = quote_candidates(materials)
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "综合指数中两个权重分别为0.25和0.30。"
        unit["citations"] = [{"quote_id": candidates[0]["quote_id"]}]

        validated = validate_evidence_batch(payload, materials, candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_evidence_claim_accepts_tex_percent_and_spaced_formula_digits(self) -> None:
        source = material("m1", r"增长 $21.0\%$，公式 y = x / 3 0 \quad \forall x\tag{4.2}。")
        candidates = quote_candidates([source])
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "增长21.0%，公式分母为30。"
        unit["citations"] = [{"quote_id": candidates[0]["quote_id"]}]

        validated = validate_evidence_batch(payload, [source], candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_evidence_claim_accepts_number_split_across_adjacent_quotes(self) -> None:
        source = material("m1", "吞吐量677.2万TEU。")
        candidates = [
            {
                "quote_id": "quote_111111111111111111111111",
                "material_id": "m1",
                "text": "吞吐量677.",
                "start_char": 0,
                "end_char": 7,
            },
            {
                "quote_id": "quote_222222222222222222222222",
                "material_id": "m1",
                "text": "2万TEU。",
                "start_char": 7,
                "end_char": 13,
            },
        ]
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "吞吐量677.2万TEU。"
        unit["citations"] = [{"quote_id": row["quote_id"]} for row in candidates]

        validated = validate_evidence_batch(payload, [source], candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_table_header_and_row_can_jointly_support_one_percentage(self) -> None:
        source = material(
            "m1",
            "需求情况 | 公路运量(%) | 水路运量(%)\n模型M2 | 33.8 | 66.2",
        )
        source["content_kind"] = "table"
        candidates = quote_candidates([source])
        header = next(row for row in candidates if "水路运量(%)" in row["text"])
        data_row = next(row for row in candidates if "模型M2" in row["text"])
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "模型M2的水路运量为66.2%。"
        unit["citations"] = [
            {"quote_id": header["quote_id"]},
            {"quote_id": data_row["quote_id"]},
        ]

        validated = validate_evidence_batch(payload, [source], candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_table_percentage_rejects_row_without_header(self) -> None:
        source = material(
            "m1",
            "需求情况 | 公路运量(%) | 水路运量(%)\n模型M2 | 33.8 | 66.2",
        )
        source["content_kind"] = "table"
        candidates = quote_candidates([source])
        data_row = next(row for row in candidates if "模型M2" in row["text"])
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "模型M2的水路运量为66.2%。"
        unit["citations"] = [{"quote_id": data_row["quote_id"]}]

        with self.assertRaisesRegex(ContractViolation, "evidence_claim_numeric_fact_unsupported"):
            validate_evidence_batch(payload, [source], candidates)

    def test_table_percentage_rejects_wrong_column_label(self) -> None:
        source = material(
            "m1",
            "需求情况 | 公路运量(%) | 水路运量(%)\n模型M2 | 33.8 | 66.2",
        )
        source["content_kind"] = "table"
        candidates = quote_candidates([source])
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "模型M2的公路运量为66.2%。"
        unit["citations"] = [{"quote_id": row["quote_id"]} for row in candidates]

        with self.assertRaisesRegex(ContractViolation, "evidence_claim_numeric_fact_unsupported"):
            validate_evidence_batch(payload, [source], candidates)

    def test_table_percentage_final_claim_requires_full_row_label_not_alias(self) -> None:
        source = material(
            "m1",
            "需求情况 | 公路运量(%) | 水路运量(%)\n不确定情况(M3) | 43.3 | 56.7",
        )
        source["content_kind"] = "table"
        candidates = quote_candidates([source])
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "模型M3的水路运量为56.7%。"
        unit["citations"] = [{"quote_id": row["quote_id"]} for row in candidates]

        with self.assertRaisesRegex(ContractViolation, "evidence_claim_numeric_fact_unsupported"):
            validate_evidence_batch(payload, [source], candidates)

    def test_table_percentage_requires_atomic_evidence(self) -> None:
        source = material(
            "m1",
            "需求情况 | 公路运量(%) | 水路运量(%)\n不确定情况(M3) | 43.3 | 56.7",
        )
        source["content_kind"] = "table"
        candidates = quote_candidates([source])
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "不确定情况(M3)的公路运量为43.3%，水路运量为56.7%。"
        unit["citations"] = [{"quote_id": row["quote_id"]} for row in candidates]

        with self.assertRaisesRegex(ContractViolation, "evidence_claim_numeric_fact_unsupported"):
            validate_evidence_batch(payload, [source], candidates)

    def test_table_ranking_claim_requires_header_and_all_data_rows(self) -> None:
        source = material(
            "m1",
            "方案 | 目标值\n方案A | 10\n方案B | 20\n方案C | 15",
        )
        source["content_kind"] = "table"
        candidates = quote_candidates([source])
        quote_by_text = {row["text"]: row["quote_id"] for row in candidates}
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "方案B的目标值最高，为20；方案A最低，为10。"
        unit["citations"] = [
            {"quote_id": quote_by_text["方案A | 10"]},
            {"quote_id": quote_by_text["方案B | 20"]},
        ]

        with self.assertRaisesRegex(ContractViolation, "table_ranking_scope_incomplete"):
            validate_evidence_batch(payload, [source], candidates)

        unit["citations"] = [{"quote_id": row["quote_id"]} for row in candidates]
        validated = validate_evidence_batch(payload, [source], candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_numeric_table_claim_requires_its_header(self) -> None:
        source = material("m1", "方案 | 相对差\n模型M2 | 4.89%")
        source["content_kind"] = "table"
        candidates = quote_candidates([source])
        quote_by_text = {row["text"]: row["quote_id"] for row in candidates}
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "模型M2的相对差为4.89%。"
        unit["citations"] = [{"quote_id": quote_by_text["模型M2 | 4.89%"]}]

        with self.assertRaisesRegex(ContractViolation, "table_numeric_header_missing"):
            validate_evidence_batch(payload, [source], candidates)

        unit["citations"] = [{"quote_id": row["quote_id"]} for row in candidates]
        validated = validate_evidence_batch(payload, [source], candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_numeric_table_claim_accepts_complete_two_level_header(self) -> None:
        source = material(
            "m1",
            "| 2007年 | 2008年 | 2009年 | 2010年\n"
            "南线 | 北线 | 合计 | 南线 | 北线 | 合计 | 南线 | 北线 | 合计 | 南线 | 北线 | 合计\n"
            "大风 | 46 | 46 | 92 | 46 | 52 | 98 | 5 | 13 | 18 | 50 | 84 | 134",
        )
        source["content_kind"] = "table"
        candidates = quote_candidates([source])
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "2007年大风停航为南线46次、北线46次，合计92次。"
        unit["citations"] = [{"quote_id": row["quote_id"]} for row in candidates]

        validated = validate_evidence_batch(payload, [source], candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_semantically_duplicate_table_composite_is_rejected_across_units(self) -> None:
        source = material(
            "m1",
            "需求情况 | 公路运量(%) | 水路运量(%)\n不确定情况(M3) | 43.3 | 56.7",
        )
        source["content_kind"] = "table"
        candidates = quote_candidates([source])
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "不确定情况(M3)的水路运量为56.7%。"
        unit["citations"] = [{"quote_id": row["quote_id"]} for row in candidates]
        duplicate = copy.deepcopy(unit)
        duplicate["claim"] = "在不确定情况(M3)下，水路运量达到56.7%。"
        payload["material_results"][0]["evidence_units"].append(duplicate)

        with self.assertRaisesRegex(ContractViolation, "duplicate_table_composite_fact"):
            validate_evidence_batch(payload, [source], candidates)

    def test_evidence_claim_rejects_dropped_modal_qualifier(self) -> None:
        source = material("m1", "建议三地考虑建立一体化信息网络，实行联网。")
        candidates = quote_candidates([source])
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "建议三地建立一体化信息网络，实行联网。"
        unit["citations"] = [{"quote_id": candidates[0]["quote_id"]}]

        with self.assertRaisesRegex(
            ContractViolation,
            "citation.evidence_claim_qualifier_dropped",
        ):
            validate_evidence_batch(payload, [source], candidates)

    def test_evidence_claim_accepts_preserved_modal_qualifier(self) -> None:
        source = material("m1", "建议三地考虑建立一体化信息网络，实行联网。")
        candidates = quote_candidates([source])
        payload = valid_batch()
        unit = model_unit(payload)
        unit["claim"] = "建议三地考虑建立一体化信息网络，实行联网。"
        unit["citations"] = [{"quote_id": candidates[0]["quote_id"]}]

        validated = validate_evidence_batch(payload, [source], candidates)

        self.assertEqual(validated["evidence_units"][0]["claim"], unit["claim"])

    def test_dynamic_synthesis_schemas_bind_ids_counts_and_section(self) -> None:
        paper_schema = build_paper_analysis_schema(["e1", "e2"])
        paper_properties = paper_schema["properties"]
        dispositions = paper_properties["evidence_dispositions"]

        self.assertEqual(dispositions["minItems"], 2)
        self.assertEqual(dispositions["maxItems"], 2)
        self.assertEqual(
            dispositions["items"]["properties"]["evidence_unit_id"]["enum"],
            ["e1", "e2"],
        )
        self.assertEqual(
            paper_properties["research_focus"]["properties"]["evidence_unit_ids"]
            ["items"]["enum"],
            ["e1", "e2"],
        )
        for field in ("methods", "unresolved_questions"):
            self.assertEqual(
                paper_properties[field]["items"]["properties"]["evidence_unit_ids"]
                ["items"]["enum"],
                ["e1", "e2"],
            )
        self.assertEqual(
            paper_properties["core_findings"]["items"]["properties"]["evidence_unit_ids"]
            ["items"]["enum"],
            ["e1", "e2"],
        )

        section_schema = build_section_summary_schema("chapter:1", ["e1", "e2"])
        section_properties = section_schema["properties"]
        self.assertEqual(section_properties["section_id"], {"const": "chapter:1"})
        self.assertEqual(section_properties["evidence_dispositions"]["minItems"], 2)
        self.assertEqual(section_properties["evidence_dispositions"]["maxItems"], 2)
        self.assertEqual(
            section_properties["claims"]["items"]["properties"]["evidence_unit_ids"]
            ["items"]["enum"],
            ["e1", "e2"],
        )

    def test_paper_draft_and_non_used_classification_merge_without_duplicate_used_state(self) -> None:
        draft = {
            "schema_version": PAPER_ANALYSIS_DRAFT_SCHEMA_VERSION,
            "research_focus": {"statement": "研究船闸能力。", "evidence_unit_ids": ["e1"]},
            "study_type": "modeling",
            "methods": [{"statement": "模型分析", "evidence_unit_ids": ["e1"]}],
            "core_findings": [{"statement": "能力受限。", "evidence_unit_ids": ["e1"]}],
            "limitations": [],
            "review_uses": [],
            "unresolved_questions": [],
        }
        evidence_units = [
            {"evidence_unit_id": "e1", "claim": "能力受限。", "citations": [{"quote": "能力受限。"}]},
            {"evidence_unit_id": "e2", "claim": "背景材料。", "citations": [{"quote": "背景材料。"}]},
        ]
        validated_draft = validate_paper_analysis_draft(
            draft,
            {"e1", "e2"},
            evidence_units=evidence_units,
        )
        classification = {
            "schema_version": NON_USED_EVIDENCE_DISPOSITION_SCHEMA_VERSION,
            "non_used_evidence_dispositions": [
                {
                    "evidence_unit_id": "e2",
                    "disposition": "peripheral",
                    "reason_code": "background_only",
                }
            ],
        }
        validated_classification = validate_non_used_evidence_dispositions(classification, {"e2"})
        final = finalize_paper_analysis(
            validated_draft,
            validated_classification["non_used_evidence_dispositions"],
            evidence_units,
        )

        self.assertEqual(final["schema_version"], PAPER_ANALYSIS_SCHEMA_VERSION)
        self.assertEqual(
            final["evidence_dispositions"],
            [
                {"evidence_unit_id": "e1", "disposition": "used", "reason_code": "supports_claim"},
                {
                    "evidence_unit_id": "e2",
                    "disposition": "peripheral",
                    "reason_code": "background_only",
                },
            ],
        )
        schema = build_non_used_evidence_disposition_schema(["e2"])
        rows = schema["properties"]["non_used_evidence_dispositions"]
        self.assertEqual(rows["minItems"], 1)
        self.assertEqual(rows["maxItems"], 1)
        self.assertNotIn("used", rows["items"]["properties"]["disposition"]["enum"])

    def test_paper_analysis_rejects_untraceable_free_text_fields(self) -> None:
        valid = {
            "schema_version": PAPER_ANALYSIS_SCHEMA_VERSION,
            "research_focus": {"statement": "研究焦点。", "evidence_unit_ids": ["e1"]},
            "study_type": "modeling",
            "methods": [{"statement": "研究方法。", "evidence_unit_ids": ["e1"]}],
            "core_findings": [],
            "limitations": [],
            "review_uses": [],
            "unresolved_questions": [
                {"statement": "明确提出的未决问题。", "evidence_unit_ids": ["e1"]}
            ],
            "evidence_dispositions": [
                {
                    "evidence_unit_id": "e1",
                    "disposition": "used",
                    "reason_code": "supports_claim",
                }
            ],
        }

        for field, invalid_value in (
            ("research_focus", "没有证据引用的研究焦点。"),
            ("methods", ["没有证据引用的方法。"]),
            ("unresolved_questions", ["没有证据引用的未决问题。"]),
        ):
            with self.subTest(field=field):
                payload = copy.deepcopy(valid)
                payload[field] = invalid_value
                with self.assertRaisesRegex(ContractViolation, field):
                    validate_paper_analysis(payload, {"e1"})

    def test_non_used_classification_accepts_supporting_detail_only_as_peripheral(self) -> None:
        payload = {
            "schema_version": NON_USED_EVIDENCE_DISPOSITION_SCHEMA_VERSION,
            "non_used_evidence_dispositions": [
                {
                    "evidence_unit_id": "e1",
                    "disposition": "peripheral",
                    "reason_code": "supporting_detail",
                }
            ],
        }

        validated = validate_non_used_evidence_dispositions(payload, {"e1"})
        self.assertEqual(
            validated["non_used_evidence_dispositions"][0]["reason_code"],
            "supporting_detail",
        )

    def test_valid_evidence_batch_passes_and_assigns_system_ids(self) -> None:
        materials = [material("m1")]

        payload = validate_evidence_batch(valid_batch(), materials, quote_candidates(materials))
        units = assign_evidence_unit_ids(
            "batch_0003",
            payload["evidence_units"],
            materials,
            generation_id="generation-one",
        )

        self.assertTrue(units[0]["evidence_anchor_id"].startswith("anchor_"))
        self.assertTrue(units[0]["evidence_unit_id"].startswith("evidence_"))
        self.assertEqual(units[0]["request_id"], "batch_0003")
        self.assertEqual(units[0]["citations"][0]["source_ref"]["start_line"], 10)
        self.assertEqual(units[0]["citations"][0]["quote"], materials[0]["extract"])
        self.assertEqual(payload["material_assessments"][0]["disposition"], "evidence")
        self.assertNotIn("evidence_unit_id", payload["evidence_units"][0])

    def test_evidence_identity_is_independent_of_request_and_response_order(self) -> None:
        unit = validate_evidence_batch(
            valid_batch(), [material("m1")], quote_candidates()
        )["evidence_units"][0]
        reversed_citations = copy.deepcopy(unit)
        reversed_citations["citations"] = list(reversed(reversed_citations["citations"]))
        first = assign_evidence_unit_ids(
            "request-a",
            [unit],
            [material("m1")],
            generation_id="generation-one",
        )[0]
        second = assign_evidence_unit_ids(
            "request-b",
            [reversed_citations],
            [material("m1")],
            generation_id="generation-one",
        )[0]

        self.assertEqual(first["evidence_anchor_id"], second["evidence_anchor_id"])
        self.assertEqual(first["evidence_unit_id"], second["evidence_unit_id"])
        self.assertNotEqual(first["request_id"], second["request_id"])

        changed_claim = copy.deepcopy(unit)
        changed_claim["claim"] = "  论文采用仿真方法   研究船舶等待时间。 "
        changed = assign_evidence_unit_ids(
            "request-c",
            [changed_claim],
            [material("m1")],
            generation_id="generation-one",
        )[0]
        self.assertEqual(first["evidence_anchor_id"], changed["evidence_anchor_id"])
        self.assertNotEqual(first["evidence_unit_id"], changed["evidence_unit_id"])

    def test_duplicate_evidence_units_in_one_response_are_rejected(self) -> None:
        unit = validate_evidence_batch(
            valid_batch(), [material("m1")], quote_candidates()
        )["evidence_units"][0]
        with self.assertRaisesRegex(ContractViolation, "duplicate_evidence_unit"):
            assign_evidence_unit_ids(
                "request-a",
                [unit, copy.deepcopy(unit)],
                [material("m1")],
                generation_id="generation-one",
            )

    def test_identity_helpers_normalize_claim_but_not_generation(self) -> None:
        citations = [{"material_id": "m1", "quote": "逐字证据"}]
        anchor = build_evidence_anchor_id("g1", "result", citations)
        self.assertEqual(
            build_evidence_unit_id(anchor, "同一  观点"),
            build_evidence_unit_id(anchor, " 同一 观点 "),
        )
        self.assertNotEqual(anchor, build_evidence_anchor_id("g2", "result", citations))

    def test_schema_rejects_invalid_enum_type_unknown_field_and_empty_text(self) -> None:
        cases = []
        invalid_enum = copy.deepcopy(valid_batch())
        model_unit(invalid_enum)["confidence"] = "certain"
        cases.append((invalid_enum, "confidence"))
        invalid_type = copy.deepcopy(valid_batch())
        model_unit(invalid_type)["caveats"] = "无"
        cases.append((invalid_type, "caveats"))
        unknown_field = copy.deepcopy(valid_batch())
        model_unit(unknown_field)["model_note"] = "extra"
        cases.append((unknown_field, "model_note"))
        empty_claim = copy.deepcopy(valid_batch())
        model_unit(empty_claim)["claim"] = ""
        cases.append((empty_claim, "claim"))

        for payload, expected_path in cases:
            with self.subTest(expected_path=expected_path):
                with self.assertRaisesRegex(ContractViolation, expected_path):
                    validate_evidence_batch(payload, [material("m1")], quote_candidates())

    def test_schema_rejects_text_and_collection_overflow(self) -> None:
        cases = []
        long_claim = copy.deepcopy(valid_batch())
        model_unit(long_claim)["claim"] = "证" * 321
        cases.append((long_claim, "claim"))
        long_relevance = copy.deepcopy(valid_batch())
        model_unit(long_relevance)["relevance"] = "证" * 241
        cases.append((long_relevance, "relevance"))
        invalid_quote_id = copy.deepcopy(valid_batch())
        model_unit(invalid_quote_id)["citations"][0]["quote_id"] = "model_generated_id"
        cases.append((invalid_quote_id, "quote_id"))
        too_many_citations = copy.deepcopy(valid_batch())
        model_unit(too_many_citations)["citations"] *= 5
        cases.append((too_many_citations, "citations"))
        too_many_caveats = copy.deepcopy(valid_batch())
        model_unit(too_many_caveats)["caveats"] = ["限制"] * 5
        cases.append((too_many_caveats, "caveats"))

        for payload, expected_path in cases:
            with self.subTest(expected_path=expected_path):
                with self.assertRaisesRegex(ContractViolation, expected_path):
                    validate_evidence_batch(payload, [material("m1")], quote_candidates())

    def test_assessment_disposition_and_reason_code_must_match(self) -> None:
        cases = [
            ("evidence", "context_only"),
            ("context", "direct_evidence"),
            ("not_relevant", "parse_damage"),
            ("unusable", "off_topic"),
        ]
        for disposition, reason_code in cases:
            with self.subTest(disposition=disposition, reason_code=reason_code):
                payload = copy.deepcopy(valid_batch())
                payload["material_results"][0].update(
                    {"disposition": disposition, "reason_code": reason_code}
                )
                if disposition != "evidence":
                    payload["material_results"][0]["evidence_units"] = []
                with self.assertRaisesRegex(ContractViolation, "reason_code"):
                    validate_evidence_batch(payload, [material("m1")], quote_candidates())

    def test_batch_rejects_missing_duplicate_unknown_materials_and_inexact_quotes(self) -> None:
        missing = copy.deepcopy(valid_batch())
        with self.assertRaisesRegex(ContractViolation, "missing_material_ids"):
            materials = [material("m1"), material("m2")]
            validate_evidence_batch(missing, materials, quote_candidates(materials))

        duplicate = copy.deepcopy(valid_batch())
        duplicate["material_results"].append(copy.deepcopy(duplicate["material_results"][0]))
        with self.assertRaisesRegex(ContractViolation, "duplicate_material_ids"):
            validate_evidence_batch(duplicate, [material("m1")], quote_candidates())

        unknown = copy.deepcopy(valid_batch())
        unknown["material_results"][0]["material_id"] = "outside"
        with self.assertRaisesRegex(ContractViolation, "material_id"):
            validate_evidence_batch(unknown, [material("m1")], quote_candidates())

        generated = copy.deepcopy(valid_batch())
        model_unit(generated)["citations"][0]["quote_id"] = "quote_model_generated"
        with self.assertRaisesRegex(ContractViolation, "quote_id"):
            validate_evidence_batch(generated, [material("m1")], quote_candidates())

        materials = [material("m1"), material("m2")]
        mismatch = copy.deepcopy(valid_batch())
        mismatch["material_results"].append(
            {
                "material_id": "m2",
                "disposition": "context",
                "reason_code": "context_only",
                "evidence_units": [],
            }
        )
        model_unit(mismatch)["citations"][0]["quote_id"] = quote_id("m2")
        with self.assertRaisesRegex(ContractViolation, "quote_material_mismatch"):
            validate_evidence_batch(mismatch, materials, quote_candidates(materials))

        tampered_candidates = quote_candidates()
        tampered_candidates[0]["text"] = "模型改写的引文"
        with self.assertRaisesRegex(ContractViolation, "quote_candidate_not_exact"):
            validate_evidence_batch(valid_batch(), [material("m1")], tampered_candidates)

    def test_evidence_disposition_requires_a_citation_from_that_material(self) -> None:
        payload = copy.deepcopy(valid_batch())
        payload["material_results"][0]["evidence_units"] = []

        with self.assertRaisesRegex(ContractViolation, "evidence_units"):
            validate_evidence_batch(payload, [material("m1")], quote_candidates())

        non_evidence_with_unit = copy.deepcopy(valid_batch())
        non_evidence_with_unit["material_results"][0].update(
            {"disposition": "context", "reason_code": "context_only"}
        )
        with self.assertRaisesRegex(ContractViolation, "evidence_units"):
            validate_evidence_batch(non_evidence_with_unit, [material("m1")], quote_candidates())

    def test_material_coverage_reports_missing_duplicate_and_unknown_ids(self) -> None:
        report = verify_material_coverage(
            ["m1", "m2"],
            [
                {"material_id": "m1"},
                {"material_id": "m1"},
                {"material_id": "outside"},
            ],
        )

        self.assertEqual(report["missing_material_ids"], ["m2"])
        self.assertEqual(report["duplicate_material_ids"], ["m1"])
        self.assertEqual(report["unknown_material_ids"], ["outside"])

    def test_paper_analysis_requires_valid_evidence_references(self) -> None:
        payload = {
            "schema_version": PAPER_ANALYSIS_SCHEMA_VERSION,
            "research_focus": {
                "statement": "研究船舶积压的疏导方法。",
                "evidence_unit_ids": ["batch_0001:evidence_0001"],
            },
            "study_type": "simulation",
            "methods": [
                {
                    "statement": "仿真分析",
                    "evidence_unit_ids": ["batch_0001:evidence_0001"],
                }
            ],
            "core_findings": [
                {"statement": "优化调度能够降低等待时间。", "evidence_unit_ids": ["batch_0001:evidence_0001"]}
            ],
            "limitations": [],
            "review_uses": [
                {"statement": "可用于比较疏导策略。", "evidence_unit_ids": ["batch_0001:evidence_0001"]}
            ],
            "unresolved_questions": [
                {
                    "statement": "论文明确提出长期运行数据仍需补充。",
                    "evidence_unit_ids": ["batch_0001:evidence_0001"],
                }
            ],
            "evidence_dispositions": [
                {
                    "evidence_unit_id": "batch_0001:evidence_0001",
                    "disposition": "used",
                    "reason_code": "supports_claim",
                }
            ],
        }

        self.assertEqual(
            validate_paper_analysis(payload, {"batch_0001:evidence_0001"})["study_type"],
            "simulation",
        )
        payload["core_findings"][0]["evidence_unit_ids"] = ["missing"]
        with self.assertRaisesRegex(ContractViolation, "unknown evidence_unit_id"):
            validate_paper_analysis(payload, {"batch_0001:evidence_0001"})

    def test_section_summary_requires_complete_unique_dispositions(self) -> None:
        payload = {
            "schema_version": SECTION_SUMMARY_SCHEMA_VERSION,
            "section_id": "chapter:1/section:1.1",
            "section_focus": "船闸能力约束",
            "claims": [{"statement": "能力约束延长等待。", "evidence_unit_ids": ["e1"]}],
            "evidence_dispositions": [
                {"evidence_unit_id": "e1", "disposition": "used", "reason_code": "supports_claim"},
                {
                    "evidence_unit_id": "e2",
                    "disposition": "peripheral",
                    "reason_code": "background_only",
                },
            ],
        }

        validated = validate_section_summary(
            payload,
            section_id="chapter:1/section:1.1",
            evidence_ids={"e1", "e2"},
        )
        self.assertEqual(validated["section_focus"], "船闸能力约束")

        missing = copy.deepcopy(payload)
        missing["evidence_dispositions"].pop()
        with self.assertRaisesRegex(ContractViolation, r"missing=\['e2'\]"):
            validate_section_summary(missing, section_id="chapter:1/section:1.1", evidence_ids={"e1", "e2"})

        duplicate = copy.deepcopy(payload)
        duplicate["evidence_dispositions"].append(copy.deepcopy(duplicate["evidence_dispositions"][0]))
        with self.assertRaisesRegex(ContractViolation, r"duplicate=\['e1'\]"):
            validate_section_summary(duplicate, section_id="chapter:1/section:1.1", evidence_ids={"e1", "e2"})

    def test_paper_analysis_rejects_missing_or_inconsistent_evidence_dispositions(self) -> None:
        payload = {
            "schema_version": PAPER_ANALYSIS_SCHEMA_VERSION,
            "research_focus": {"statement": "研究船闸能力。", "evidence_unit_ids": ["e1"]},
            "study_type": "modeling",
            "methods": [{"statement": "模型分析", "evidence_unit_ids": ["e1"]}],
            "core_findings": [{"statement": "能力受限。", "evidence_unit_ids": ["e1"]}],
            "limitations": [],
            "review_uses": [],
            "unresolved_questions": [],
            "evidence_dispositions": [
                {"evidence_unit_id": "e1", "disposition": "used", "reason_code": "supports_claim"},
                {"evidence_unit_id": "e2", "disposition": "excluded", "reason_code": "off_topic"},
            ],
        }
        validate_paper_analysis(payload, {"e1", "e2"})

        inconsistent = copy.deepcopy(payload)
        inconsistent["evidence_dispositions"][0].update(
            {"disposition": "redundant", "reason_code": "duplicate_support"}
        )
        with self.assertRaisesRegex(ContractViolation, "non_used_evidence_referenced"):
            validate_paper_analysis(inconsistent, {"e1", "e2"})

    def test_paper_analysis_rejects_numeric_facts_not_supported_by_its_citations(self) -> None:
        payload = {
            "schema_version": PAPER_ANALYSIS_SCHEMA_VERSION,
            "research_focus": {"statement": "研究运输组织优化。", "evidence_unit_ids": ["e1"]},
            "study_type": "modeling",
            "methods": [{"statement": "模型分析", "evidence_unit_ids": ["e1"]}],
            "core_findings": [
                {
                    "statement": "模型利润为4,244万元，水运占比为66.2%。",
                    "evidence_unit_ids": ["e1"],
                }
            ],
            "limitations": [],
            "review_uses": [],
            "unresolved_questions": [],
            "evidence_dispositions": [
                {"evidence_unit_id": "e1", "disposition": "used", "reason_code": "supports_claim"}
            ],
        }
        evidence_units = [
            {
                "evidence_unit_id": "e1",
                "claim": "模型利润为4,244万元。",
                "citations": [{"quote": "模型求得网络运营利润为4,244万元。"}],
            }
        ]

        with self.assertRaisesRegex(ContractViolation, "numeric_fact_unsupported") as raised:
            validate_paper_analysis(payload, {"e1"}, evidence_units=evidence_units)
        self.assertIn("66.2%", raised.exception.detail)
        self.assertNotIn("4244", raised.exception.detail)

    def test_numeric_support_accepts_the_same_value_with_an_attached_unit(self) -> None:
        payload = {
            "schema_version": PAPER_ANALYSIS_SCHEMA_VERSION,
            "research_focus": {"statement": "研究运输需求。", "evidence_unit_ids": ["e1"]},
            "study_type": "quantitative",
            "methods": [{"statement": "统计分析", "evidence_unit_ids": ["e1"]}],
            "core_findings": [
                {"statement": "年运输需求为175,550 TEU。", "evidence_unit_ids": ["e1"]}
            ],
            "limitations": [],
            "review_uses": [],
            "unresolved_questions": [],
            "evidence_dispositions": [
                {"evidence_unit_id": "e1", "disposition": "used", "reason_code": "supports_claim"}
            ],
        }
        evidence_units = [
            {
                "evidence_unit_id": "e1",
                "claim": "年运输需求量为175,550TEU。",
                "citations": [{"quote": "年集装箱总运输需求量为175,550TEU。"}],
            }
        ]

        validated = validate_paper_analysis(payload, {"e1"}, evidence_units=evidence_units)
        self.assertEqual(validated["core_findings"][0]["statement"], "年运输需求为175,550 TEU。")


if __name__ == "__main__":
    unittest.main()
