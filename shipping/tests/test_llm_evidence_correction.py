from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_contracts import EVIDENCE_BATCH_SCHEMA_VERSION, ContractViolation
from shipping_pipeline.llm_evidence_correction import (
    EVIDENCE_CORRECTION_SCHEMA_VERSION,
    EvidenceCorrectionUnavailable,
    collect_correctable_material_failures,
    validate_and_apply_evidence_correction,
)
from shipping_pipeline.llm_projection import project_cards
from shipping_pipeline.llm_quotes import build_quote_candidates


def material(material_id: str, extract: str) -> dict:
    return {
        "schema_version": "material.v2",
        "material_id": material_id,
        "material_type": "evidence_card",
        "paper_id": "paper-one",
        "paper_title": "论文一",
        "clean_title": "运量结果",
        "raw_title": "运量结果",
        "order": 1,
        "extract": extract,
        "heading_path": ["结果"],
        "content_kind": "prose",
        "quality_flags": [],
        "confidence_flags": [],
        "generation_id": "generation-one",
    }


def evidence_result(material_id: str, claim: str, quote_id: str) -> dict:
    return {
        "material_id": material_id,
        "disposition": "evidence",
        "reason_code": "direct_evidence",
        "evidence_units": [
            {
                "claim": claim,
                "evidence_type": "result",
                "citations": [{"quote_id": quote_id}],
                "relevance": "用于比较方案运量。",
                "confidence": "high",
                "caveats": [],
            }
        ],
    }


class EvidenceCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.material = material(
            "paper-one:card-001",
            "该方案运量为5000吨。\n对照方案运量为3000吨。\n扩展方案运量为6000吨。",
        )
        self.candidates = build_quote_candidates(project_cards([self.material]))
        self.quote_by_number = {
            number: next(row["quote_id"] for row in self.candidates if number in row["text"])
            for number in ("3000", "5000", "6000")
        }
        self.source = {
            "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
            "material_results": [
                evidence_result(
                    self.material["material_id"],
                    "该方案运量为5000吨。",
                    self.quote_by_number["3000"],
                )
            ],
        }

    def correction(self, *, claim: str, quote_id: str, action: str = "replace") -> dict:
        replacements = []
        if action == "replace":
            replacements = [{"claim": claim, "citations": [{"quote_id": quote_id}]}]
        return {
            "schema_version": EVIDENCE_CORRECTION_SCHEMA_VERSION,
            "material_corrections": [
                {
                    "material_id": self.material["material_id"],
                    "unit_corrections": [
                        {
                            "source_evidence_index": 0,
                            "action": action,
                            "replacements": replacements,
                            "reason": "按逐字引文修正。",
                        }
                    ],
                }
            ],
        }

    def test_collects_only_locally_correctable_material_results(self) -> None:
        failures = collect_correctable_material_failures(
            self.source,
            [self.material],
            self.candidates,
        )

        self.assertEqual([row["material_id"] for row in failures], [self.material["material_id"]])
        self.assertEqual(
            failures[0]["error_code"],
            "citation.evidence_claim_numeric_fact_unsupported",
        )

    def test_collects_all_invalid_units_in_one_material_instead_of_only_the_first(self) -> None:
        source = {
            "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
            "material_results": [
                {
                    **self.source["material_results"][0],
                    "evidence_units": [
                        self.source["material_results"][0]["evidence_units"][0],
                        evidence_result(
                            self.material["material_id"],
                            "扩展方案运量为6000吨。",
                            self.quote_by_number["3000"],
                        )["evidence_units"][0],
                    ],
                }
            ],
        }

        failures = collect_correctable_material_failures(
            source,
            [self.material],
            self.candidates,
        )

        self.assertEqual(len(failures), 1)
        self.assertEqual(
            [row["source_evidence_index"] for row in failures[0]["violations"]],
            [0, 1],
        )

    def test_applies_rebinding_and_preserves_noneditable_metadata(self) -> None:
        applied = validate_and_apply_evidence_correction(
            self.correction(
                claim="该方案运量为5000吨。",
                quote_id=self.quote_by_number["5000"],
            ),
            source_payload=self.source,
            target_material_ids=[self.material["material_id"]],
            materials=[self.material],
            quote_candidates=self.candidates,
        )

        unit = applied["merged_payload"]["material_results"][0]["evidence_units"][0]
        self.assertEqual(unit["evidence_type"], "result")
        self.assertEqual(unit["relevance"], "用于比较方案运量。")
        self.assertEqual(unit["confidence"], "high")
        self.assertEqual(unit["citations"], [{"quote_id": self.quote_by_number["5000"]}])

    def test_rejects_new_numeric_fact_even_when_another_quote_supports_it(self) -> None:
        with self.assertRaises(ContractViolation) as raised:
            validate_and_apply_evidence_correction(
                self.correction(
                    claim="扩展方案运量为6000吨。",
                    quote_id=self.quote_by_number["6000"],
                ),
                source_payload=self.source,
                target_material_ids=[self.material["material_id"]],
                materials=[self.material],
                quote_candidates=self.candidates,
            )

        self.assertEqual(raised.exception.code, "correction.new_numeric_fact")

    def test_deleting_the_only_invalid_unit_explicitly_changes_result_to_context(self) -> None:
        applied = validate_and_apply_evidence_correction(
            self.correction(claim="未使用", quote_id=self.quote_by_number["5000"], action="delete"),
            source_payload=self.source,
            target_material_ids=[self.material["material_id"]],
            materials=[self.material],
            quote_candidates=self.candidates,
        )

        result = applied["merged_payload"]["material_results"][0]
        self.assertEqual(result["disposition"], "context")
        self.assertEqual(result["reason_code"], "context_only")
        self.assertEqual(result["evidence_units"], [])

    def test_does_not_offer_correction_for_uneditable_schema_error(self) -> None:
        source = self.source.copy()
        source["material_results"] = [self.source["material_results"][0].copy()]
        source["material_results"][0]["evidence_units"] = [
            self.source["material_results"][0]["evidence_units"][0].copy()
        ]
        source["material_results"][0]["evidence_units"][0]["confidence"] = "certain"

        with self.assertRaises(EvidenceCorrectionUnavailable) as raised:
            collect_correctable_material_failures(source, [self.material], self.candidates)

        self.assertEqual(raised.exception.code, "correction.no_local_correctable_failure")

    def test_known_failed_table_unit_can_retain_percentage_with_required_composite(self) -> None:
        percentage_material = material(
            "paper-one:card-percent",
            "需求情况 | 公路运量(%) | 水路运量(%)\n模型M2 | 33.8 | 66.2",
        )
        percentage_material["content_kind"] = "table"
        candidates = build_quote_candidates(project_cards([percentage_material]))
        header_quote = next(row["quote_id"] for row in candidates if "水路运量(%)" in row["text"])
        row_quote = next(row["quote_id"] for row in candidates if "66.2" in row["text"])
        source = {
            "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
            "material_results": [
                evidence_result(
                    percentage_material["material_id"],
                    "模型M2的水路运量为66.2%。",
                    row_quote,
                )
            ],
        }
        failures = collect_correctable_material_failures(
            source,
            [percentage_material],
            candidates,
        )
        self.assertEqual(
            failures[0]["violations"][0]["forbidden_claim_fragments"],
            [],
        )
        self.assertEqual(
            failures[0]["violations"][0]["table_composite_candidates"],
            [
                {
                    "fact": "66.2%",
                    "material_id": percentage_material["material_id"],
                    "header_quote_id": header_quote,
                    "row_quote_id": row_quote,
                    "row_label": "模型M2",
                    "column_label": "水路运量",
                    "column_index": 2,
                    "row_value": "66.2",
                }
            ],
        )
        keep = {
            "schema_version": EVIDENCE_CORRECTION_SCHEMA_VERSION,
            "material_corrections": [
                {
                    "material_id": percentage_material["material_id"],
                    "unit_corrections": [
                        {
                            "source_evidence_index": 0,
                            "action": "keep",
                            "replacements": [],
                            "reason": "错误地保留。",
                        }
                    ],
                }
            ],
        }
        with self.assertRaises(ContractViolation) as keep_error:
            validate_and_apply_evidence_correction(
                keep,
                source_payload=source,
                target_material_ids=[percentage_material["material_id"]],
                materials=[percentage_material],
                quote_candidates=candidates,
                failures=failures,
            )
        self.assertEqual(keep_error.exception.code, "schema.evidence_batch_correction_invalid")

        missing_header = {
            "schema_version": EVIDENCE_CORRECTION_SCHEMA_VERSION,
            "material_corrections": [
                {
                    "material_id": percentage_material["material_id"],
                    "unit_corrections": [
                        {
                            "source_evidence_index": 0,
                            "action": "replace",
                            "replacements": [
                                {
                                    "claim": "模型M2的水路运量为66.2%。",
                                    "citations": [{"quote_id": row_quote}],
                                }
                            ],
                            "reason": "错误地遗漏表头引用。",
                        }
                    ],
                }
            ],
        }
        with self.assertRaises(ContractViolation) as missing_header_error:
            validate_and_apply_evidence_correction(
                missing_header,
                source_payload=source,
                target_material_ids=[percentage_material["material_id"]],
                materials=[percentage_material],
                quote_candidates=candidates,
                failures=failures,
            )
        self.assertEqual(
            missing_header_error.exception.code,
            "schema.evidence_batch_correction_invalid",
        )

        corrected = {
            **missing_header,
            "material_corrections": [
                {
                    **missing_header["material_corrections"][0],
                    "unit_corrections": [
                        {
                            **missing_header["material_corrections"][0]["unit_corrections"][0],
                            "replacements": [
                                {
                                    "claim": "模型M2的水路运量为66.2%。",
                                    "citations": [
                                        {"quote_id": header_quote},
                                        {"quote_id": row_quote},
                                    ],
                                }
                            ],
                            "reason": "同时引用单位表头和对应数据行。",
                        }
                    ],
                }
            ],
        }
        applied = validate_and_apply_evidence_correction(
            corrected,
            source_payload=source,
            target_material_ids=[percentage_material["material_id"]],
            materials=[percentage_material],
            quote_candidates=candidates,
            failures=failures,
        )
        self.assertEqual(
            applied["merged_payload"]["material_results"][0]["evidence_units"][0]["claim"],
            "模型M2的水路运量为66.2%。",
        )

    def test_failure_discovery_accepts_only_unique_structured_row_alias(self) -> None:
        percentage_material = material(
            "paper-one:card-percent-alias",
            "需求情况 | 公路运量(%) | 水路运量(%)\n不确定情况(M3) | 43.3 | 56.7",
        )
        percentage_material["content_kind"] = "table"
        candidates = build_quote_candidates(project_cards([percentage_material]))
        row_quote = next(row["quote_id"] for row in candidates if "56.7" in row["text"])
        source = {
            "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
            "material_results": [
                evidence_result(
                    percentage_material["material_id"],
                    "模型M3的水路运量为56.7%。",
                    row_quote,
                )
            ],
        }

        failures = collect_correctable_material_failures(
            source,
            [percentage_material],
            candidates,
        )

        violation = failures[0]["violations"][0]
        self.assertEqual(violation["forbidden_claim_fragments"], [])
        self.assertEqual(violation["table_composite_candidates"][0]["row_label"], "不确定情况(M3)")

    def test_duplicate_table_fact_is_delegated_to_one_source_unit_before_prompting(self) -> None:
        percentage_material = material(
            "paper-one:card-percent-owner",
            "需求情况 | 公路运量(%) | 水路运量(%)\n"
            "模型M2 | 33.8 | 66.2\n"
            "不确定情况(M3) | 43.3 | 56.7",
        )
        percentage_material["content_kind"] = "table"
        candidates = build_quote_candidates(project_cards([percentage_material]))
        m2_quote = next(row["quote_id"] for row in candidates if "66.2" in row["text"])
        m3_quote = next(row["quote_id"] for row in candidates if "56.7" in row["text"])
        result = evidence_result(
            percentage_material["material_id"],
            "模型M2的水路运量最高，为66.2%；模型M3最低，为56.7%。",
            m2_quote,
        )
        result["evidence_units"][0]["citations"].append({"quote_id": m3_quote})
        second = evidence_result(
            percentage_material["material_id"],
            "不确定情况(M3)的公路运量为43.3%，水路运量为56.7%。",
            m3_quote,
        )["evidence_units"][0]
        result["evidence_units"].append(second)
        source = {
            "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
            "material_results": [result],
        }

        failures = collect_correctable_material_failures(
            source,
            [percentage_material],
            candidates,
        )

        by_index = {
            row["source_evidence_index"]: row
            for row in failures[0]["violations"]
        }
        self.assertIn("56.7%", by_index[0]["forbidden_claim_fragments"])
        self.assertEqual(
            by_index[0]["delegated_table_composite_facts"][0]["owner_source_evidence_index"],
            1,
        )
        owned = {
            row["fact"]: row["owner_source_evidence_index"]
            for row in by_index[1]["table_composite_candidates"]
            if "owner_source_evidence_index" in row
        }
        self.assertEqual(owned, {"56.7%": 1})


if __name__ == "__main__":
    unittest.main()
