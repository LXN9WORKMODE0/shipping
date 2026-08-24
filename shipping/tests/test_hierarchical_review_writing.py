from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import main
from shipping_pipeline.hierarchical_review_writing import (
    HierarchicalReviewWritingError,
    MAX_BODY_CHARS,
    MIN_BODY_CHARS,
    _argument_plan_coverage,
    _argument_plan_schema,
    _build_temporal_audit,
    _build_generation_source,
    _extract_observation_end_year,
    _historical_present_markers,
    _normalize_academic_terms,
    _normalize_argument_ids_to_citations,
    _normalize_repeated_ids,
    _restore_citation_names,
    _schema,
    _validate_argument_plan,
)


class HierarchicalReviewWritingTests(unittest.TestCase):
    def test_cli_exposes_hierarchical_review_writer(self) -> None:
        args = main.build_parser().parse_args([
            "hierarchical-review-write",
            "--global-run-id",
            "global-one",
            "--reference-catalog-run-id",
            "references-one",
        ])
        self.assertEqual(args.command, "hierarchical-review-write")
        self.assertEqual(args.global_run_id, "global-one")
        self.assertEqual(args.reference_catalog_run_id, "references-one")
        self.assertEqual(args.writing_profile, "skill_guided")
        self.assertIsNone(args.review_as_of_year)

    def test_citation_aliases_are_restored_to_human_readable_titles(self) -> None:
        value = {
            "introduction": [{"text": "P001与P002采用不同方法。", "citation_keys": ["P001", "P002"]}],
            "chapters": [],
            "discussion": [],
            "conclusion": [],
        }
        source = {"citation_map": [
            {"citation_key": "P001", "paper_title": "论文甲"},
            {"citation_key": "P002", "paper_title": "论文乙"},
        ]}
        self.assertEqual(_restore_citation_names(value, source), 2)
        self.assertEqual(value["introduction"][0]["text"], "《论文甲》与《论文乙》采用不同方法。")

    def test_paragraph_schema_accepts_concise_chinese_paragraphs(self) -> None:
        self.assertEqual((MIN_BODY_CHARS, MAX_BODY_CHARS), (6000, 24000))
        schema = _schema({"global_dimensions": [{"title": "主题一"}]})
        paragraph = schema["properties"]["conclusion"]["items"]
        self.assertEqual(paragraph["properties"]["text"]["minLength"], 40)
        self.assertEqual(
            schema["properties"]["discussion"]["items"]["properties"]["text"]["minLength"],
            80,
        )
        self.assertEqual(schema["properties"]["introduction"]["items"]["properties"]["text"]["minLength"], 50)
        self.assertEqual(schema["properties"]["introduction"]["items"]["properties"]["argument_ids"]["minItems"], 0)
        self.assertEqual(schema["properties"]["introduction"]["items"]["properties"]["citation_keys"]["minItems"], 0)
        self.assertEqual(paragraph["properties"]["citation_keys"]["maxItems"], 24)
        self.assertEqual(paragraph["properties"]["citation_keys"]["minItems"], 0)
        self.assertIn("P[0-9]{3}", paragraph["properties"]["text"]["pattern"])
        self.assertIn("argument_", paragraph["properties"]["text"]["pattern"])
        self.assertIn("证明", paragraph["properties"]["text"]["pattern"])
        self.assertIn("普遍", paragraph["properties"]["text"]["pattern"])
        self.assertIn("根本", paragraph["properties"]["text"]["pattern"])
        self.assertEqual(paragraph["properties"]["argument_ids"]["minItems"], 1)
        self.assertEqual(
            schema["properties"]["discussion"]["items"]["properties"]["citation_keys"]["minItems"],
            1,
        )

    def test_generation_schema_allows_audited_term_normalization(self) -> None:
        schema = _schema({"global_dimensions": [{"title": "主题一"}]}, forbid_academic_terms=False)
        pattern = schema["properties"]["abstract"]["pattern"]
        self.assertNotIn("证明", pattern)
        self.assertIn("argument_", pattern)
        value = {
            "abstract": "模型不能证明这是根本原因，相关判断也并非普遍成立。",
            "introduction": [{"text": "试验证明该方法从根本上改变了结果。"}],
            "chapters": [],
            "discussion": [],
            "conclusion": [],
        }
        audit = _normalize_academic_terms(value)
        self.assertEqual(audit["replacement_count"], 5)
        combined = value["abstract"] + value["introduction"][0]["text"]
        self.assertFalse(any(token in combined for token in ("证明", "普遍", "根本")))

    def test_current_fact_must_prefer_latest_comparable_evidence(self) -> None:
        source = {"citation_map": [
            {"citation_key": "P001", "publication_year": 2019, "observation_end_year": 2019},
            {"citation_key": "P002", "publication_year": 2025, "observation_end_year": 2025},
        ], "temporal_audit": _temporal_audit(latest_keys=["P002"])}
        schema = _argument_plan_schema({"global_dimensions": [{"title": "主题一"}]})
        plan = _plan("current_status", [
            {"citation_key": "P001", "role": "current_preferred"},
            {"citation_key": "P002", "role": "corroborating"},
        ])
        with self.assertRaisesRegex(HierarchicalReviewWritingError, "未选择最新可比证据"):
            _validate_argument_plan(plan, source, schema)
        plan["chapters"][0]["arguments"][0]["evidence"][0]["role"] = "historical_context"
        plan["chapters"][0]["arguments"][0]["evidence"][1]["role"] = "current_preferred"
        _validate_argument_plan(plan, source, schema)

    def test_method_result_does_not_mechanically_prefer_newest_paper(self) -> None:
        source = {"citation_map": [
            {"citation_key": "P001", "publication_year": 2019, "observation_end_year": 2019},
            {"citation_key": "P002", "publication_year": 2025, "observation_end_year": 2025},
        ], "temporal_audit": _temporal_audit()}
        schema = _argument_plan_schema({"global_dimensions": [{"title": "主题一"}]})
        plan = _plan("method_result", [
            {"citation_key": "P001", "role": "historical_primary"},
            {"citation_key": "P002", "role": "non_comparable"},
        ])
        _validate_argument_plan(plan, source, schema)

    def test_recommendation_is_not_treated_as_current_state(self) -> None:
        source = {"citation_map": [
            {"citation_key": "P001", "publication_year": 2019, "observation_end_year": None},
        ], "temporal_audit": _temporal_audit()}
        schema = _argument_plan_schema({"global_dimensions": [{"title": "主题一"}]})
        plan = _plan("recommendation", [{"citation_key": "P001", "role": "historical_primary"}])
        _validate_argument_plan(plan, source, schema)

    def test_argument_plan_coverage_reports_unused_claims(self) -> None:
        output = {"introduction": [{"argument_ids": ["A001"]}], "chapters": [], "discussion": [], "conclusion": []}
        plan = _plan("method_result", [{"citation_key": "P001", "role": "corroborating"}])
        plan["chapters"][0]["arguments"][1]["argument_id"] = "A002"
        audit = _argument_plan_coverage(output, plan)
        self.assertEqual(audit["unused_argument_ids"], ["A002"])
        self.assertEqual(audit["coverage_ratio"], 0.5)

    def test_generation_source_contains_only_plan_approved_papers(self) -> None:
        plan = _plan("method_result", [{"citation_key": "P001", "role": "corroborating"}])
        source = {
            "global_landscape": {"topic": "测试主题"},
            "citation_map": [
                {"citation_key": "P001", "paper_id": "paper-one"},
                {"citation_key": "P002", "paper_id": "paper-two"},
            ],
            "papers": [
                {"citation_key": "P001", "paper_id": "paper-one"},
                {"citation_key": "P002", "paper_id": "paper-two"},
            ],
            "temporal_audit": _temporal_audit(),
        }
        projected = _build_generation_source(source, plan)
        self.assertEqual(projected["paper_count"], 1)
        self.assertEqual([row["citation_key"] for row in projected["papers"]], ["P001"])

    def test_repeated_ids_are_deduplicated_without_reordering(self) -> None:
        value = {
            "introduction": [{
                "text": "测试段落",
                "argument_ids": ["A001", "A002", "A001"],
                "citation_keys": ["P002", "P001", "P002"],
            }],
            "chapters": [],
            "discussion": [],
            "conclusion": [],
        }
        audit = _normalize_repeated_ids(value)
        self.assertEqual(audit["removed_duplicate_count"], 2)
        self.assertEqual(value["introduction"][0]["argument_ids"], ["A001", "A002"])
        self.assertEqual(value["introduction"][0]["citation_keys"], ["P002", "P001"])

    def test_argument_ids_without_paragraph_citation_support_are_removed(self) -> None:
        plan = _plan("method_result", [{"citation_key": "P001", "role": "corroborating"}])
        plan["chapters"][0]["arguments"][1]["evidence"] = [{"citation_key": "P002", "role": "corroborating"}]
        value = {
            "introduction": [{
                "text": "测试段落",
                "argument_ids": ["A001", "A002"],
                "citation_keys": ["P001"],
            }],
            "chapters": [],
            "discussion": [],
            "conclusion": [],
        }
        self.assertEqual(_normalize_argument_ids_to_citations(value, plan), 1)
        self.assertEqual(value["introduction"][0]["argument_ids"], ["A001"])

    def test_observation_year_comes_from_time_scope_not_publication_year(self) -> None:
        paper = {"study_context": {"time_scope": "使用2016—2019年运行数据，并预测至2035年"}}
        self.assertEqual(_extract_observation_end_year(paper, 2025), 2019)

    def test_stale_system_baseline_is_disclosed_for_review_year(self) -> None:
        paper = {
            "citation_key": "P008",
            "paper_id": "system-review",
            "observation_end_year": 2022,
            "study_context": {"data_sources": ["operational_records"]},
            "methods": [{"method_category": "descriptive_analysis"}],
        }
        audit = _build_temporal_audit([paper], 2026)
        self.assertFalse(audit["current_baseline_supported"])
        self.assertEqual(audit["latest_system_observation_year"], 2022)
        self.assertIn("不能据此判断2026年的实时运行状态", audit["temporal_scope_statement"])

    def test_historical_claim_rejects_present_tense_markers(self) -> None:
        self.assertEqual(_historical_present_markers("截至2021年的结果表明短期内仍会持续。"), ["短期内"])
        self.assertEqual(_historical_present_markers("截至2021年的结果不能说明短期内仍会持续。"), [])
        self.assertEqual(_historical_present_markers("该结果仅适用于2016—2021年。"), [])
        self.assertEqual(_historical_present_markers("该数据截至2021年，不能表示当前状态，也不代表当前设备状态。"), [])
        self.assertEqual(_historical_present_markers("该数据截至2021年，不反映当前状态。"), [])
        self.assertEqual(_historical_present_markers("历史观察不得称为当前现状。"), [])


def _plan(fact_kind: str, evidence: list[dict[str, str]]) -> dict:
    def argument(argument_id: str) -> dict:
        return {
            "argument_id": argument_id,
            "conclusion": "这一证据足以形成清楚而且可以直接写入正文的判断。",
            "fact_kind": fact_kind,
            "evidence": evidence,
            "update_rationale": "这些论文描述相同对象，现按事实类型和可比范围处理新旧关系。",
            "boundary_required": False,
            "boundary": None,
        }
    return {"chapters": [{"chapter_index": 1, "title": "主题一", "thesis": "本章围绕同一主题形成明确判断并组织相关证据。", "arguments": [argument("A001"), argument("A002")]}]}


def _temporal_audit(*, latest_keys: list[str] | None = None) -> dict:
    return {
        "review_as_of_year": 2026,
        "freshness_min_year": 2024,
        "latest_system_baseline_citation_keys": latest_keys or [],
        "current_baseline_supported": False,
        "temporal_scope_statement": "测试时间范围声明。",
    }


if __name__ == "__main__":
    unittest.main()
