from __future__ import annotations

import copy
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import build_parser
from shipping_pipeline.chapter_knowledge_package_b2_v2 import (
    B2PackageV2Error,
    _build_package,
    _validate_package,
)
from shipping_pipeline.claim_ledger import (
    ClaimLedgerRunSource,
    ClaimLedgerRunner,
)
from shipping_pipeline.claim_ledger_contracts import (
    CLAIM_LEDGER_DRAFT_SCHEMA_VERSION,
    adjudicate_claim_ledger,
    build_claim_ledger_draft_schema,
    validate_claim_ledger_draft,
)
from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.review_framework_b2 import ReviewFrameworkB2RunSource
from shipping_pipeline.review_framework_b2_contracts import (
    ReviewFrameworkB2ContractError,
    derive_review_framework_b2,
)
from shipping_pipeline.source_window_contracts import stable_id
from shipping_pipeline.source_window_selection import SourceWindowRunSource


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
)


class FakeTokenCounter:
    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        return TokenCount(
            prompt_tokens=1000,
            encoded_prompt_sha256="sha256:" + "1" * 64,
        )


class FakeClaimClient:
    provider = "openai-compatible"
    model = "deepseek-ai/DeepSeek-V4-Pro"

    def __init__(self, claims: list[dict[str, Any]]) -> None:
        self.claims = claims

    def complete(
        self,
        task: str,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> ProviderResult:
        if task != "claim_ledger":
            raise AssertionError(task)
        content = {
            "schema_version": CLAIM_LEDGER_DRAFT_SCHEMA_VERSION,
            "section_id": context["section_id"],
            "claims": self.claims,
        }
        response = {
            "id": "response-claim-ledger",
            "model": self.model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(content, ensure_ascii=False),
                        "reasoning_content": "",
                    },
                }
            ],
            "usage": {
                "prompt_tokens": context["planned_input_tokens"],
                "completion_tokens": 300,
                "total_tokens": context["planned_input_tokens"] + 300,
            },
        }
        return ProviderResult(
            raw_body=json.dumps(response, ensure_ascii=False).encode("utf-8"),
            parsed_response=response,
            response_id=response["id"],
            response_model=self.model,
            system_fingerprint=None,
            finish_reason="stop",
            reasoning_content_chars=0,
            usage=dict(response["usage"]),
        )


class ClaimLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TEST_TMP_ROOT / f"claim_ledger_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self.framework = derive_review_framework_b2(
            _framework_v1(),
            source_run_id="framework-v1",
            source_manifest_sha256="sha256:" + "a" * 64,
            target_total_chars=6000,
        )
        self.selection = _source_selection(self.framework["sections"][1])
        self.framework_source = ReviewFrameworkB2RunSource(
            run_id="framework-b2",
            manifest_sha256="sha256:" + "b" * 64,
            output_sha256="sha256:" + "c" * 64,
            framework=self.framework,
        )
        self.source = SourceWindowRunSource(
            run_id="source-window",
            manifest_sha256="sha256:" + "d" * 64,
            output_sha256="sha256:" + "e" * 64,
            selection=self.selection,
            framework=_framework_v1(),
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_framework_b2_has_closed_budgets_and_disjoint_material_sets(self):
        self.assertEqual(
            sum(row["target_chars"] for row in self.framework["sections"]),
            6000,
        )
        for section in self.framework["sections"]:
            groups = section["material_selection"].values()
            flattened = [paper for group in groups for paper in group]
            self.assertEqual(len(flattened), len(set(flattened)))

    def test_framework_b2_requires_one_introduction_and_conclusion(self):
        source = _framework_v1()
        source["sections"][0]["section_type"] = "body"

        with self.assertRaisesRegex(
            ReviewFrameworkB2ContractError,
            "恰好包含一个",
        ):
            derive_review_framework_b2(
                source,
                source_run_id="framework-v1",
                source_manifest_sha256="sha256:" + "a" * 64,
                target_total_chars=6000,
            )

    def test_cross_paper_claim_is_approved_with_stable_id(self):
        draft = self._draft([_cross_claim(self.selection)])
        schema = self._schema()
        validated = validate_claim_ledger_draft(draft, schema)

        first, rejected = adjudicate_claim_ledger(
            validated,
            framework_b2=self.framework,
            source_selection=self.selection,
        )
        second, _ = adjudicate_claim_ledger(
            self._draft(
                [
                    _supporting_direct_claim(self.selection),
                    _cross_claim(self.selection),
                ]
            ),
            framework_b2=self.framework,
            source_selection=self.selection,
        )

        self.assertEqual(rejected, [])
        claim_id = first["approved_claims"][0]["claim_id"]
        self.assertIn(
            claim_id,
            [row["claim_id"] for row in second["approved_claims"]],
        )

    def test_direct_fact_without_window_is_rejected(self):
        claim = _direct_claim(self.selection)
        claim["supporting_source_windows"] = []
        ledger, rejected = self._adjudicate([claim])

        self.assertTrue(ledger["core_rejected"])
        self.assertEqual(
            rejected[0]["errors"][0]["code"],
            "claim_ledger.paper_window_missing",
        )

    def test_cross_paper_claim_with_one_paper_is_rejected(self):
        claim = _cross_claim(self.selection)
        claim["supporting_papers"] = ["paper-a"]
        claim["supporting_source_windows"] = ["window-a"]
        claim["citation_keys"] = ["ref_a"]

        ledger, rejected = self._adjudicate([claim])

        self.assertTrue(ledger["core_rejected"])
        self.assertIn(
            "claim_ledger.supporting_papers_insufficient",
            [row["code"] for row in rejected[0]["errors"]],
        )

    def test_corpus_gap_without_scope_qualifier_is_rejected(self):
        claim = _corpus_gap()
        claim["planned_claim"] = "该领域缺少长期工程验证。"
        ledger, rejected = self._adjudicate([claim])

        self.assertTrue(ledger["core_rejected"])
        self.assertIn(
            "claim_ledger.required_qualifier_missing",
            [row["code"] for row in rejected[0]["errors"]],
        )

    def test_citation_paper_mismatch_is_rejected(self):
        claim = _direct_claim(self.selection)
        claim["citation_keys"] = ["ref_b"]
        _, rejected = self._adjudicate([claim])

        self.assertIn(
            "claim_ledger.citation_paper_mismatch",
            [row["code"] for row in rejected[0]["errors"]],
        )

    def test_direct_fact_result_type_must_match_bound_window(self):
        claim = _direct_claim(self.selection)
        claim["result_type"] = "engineering_implementation"

        _, rejected = self._adjudicate([claim])

        self.assertIn(
            "claim_ledger.result_type_source_mismatch",
            [row["code"] for row in rejected[0]["errors"]],
        )

    def test_schema_rejects_core_claims_over_budget(self):
        claims = [_direct_claim(self.selection) for _ in range(5)]
        with self.assertRaisesRegex(
            Exception,
            "Too many items match",
        ):
            validate_claim_ledger_draft(
                self._draft(claims),
                self._schema(),
            )

    def test_runner_publishes_only_when_core_claims_pass(self):
        result = self._run(
            "claim-ledger-success",
            [_cross_claim(self.selection)],
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["approved_claim_count"], 1)
        self.assertTrue(Path(result["output_path"]).is_file())

    def test_runner_failure_keeps_rejected_ledger_without_output(self):
        invalid = _direct_claim(self.selection)
        invalid["supporting_source_windows"] = []
        result = self._run("claim-ledger-failed", [invalid])

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["failure"]["error_code"],
            "claim_ledger.core_candidate_rejected",
        )
        self.assertIsNone(result["output_path"])
        self.assertTrue(
            (
                Path(result["run_dir"])
                / "audit"
                / "rejected_claims.jsonl"
            ).is_file()
        )

    def test_cli_requires_explicit_framework_and_source_runs(self):
        args = build_parser().parse_args(
            [
                "llm-claim-ledger",
                "--framework-b2-run-id",
                "framework-b2",
                "--source-window-run-id",
                "source-window",
            ]
        )
        self.assertEqual(args.framework_b2_run_id, "framework-b2")
        self.assertEqual(args.source_window_run_id, "source-window")

    def test_b2_v2_package_contains_only_approved_ledger(self):
        ledger, rejected = self._adjudicate(
            [
                _cross_claim(self.selection),
                {
                    **_supporting_direct_claim(self.selection),
                    "supporting_source_windows": [],
                },
            ]
        )
        self.assertEqual(len(rejected), 1)
        ledger_source = ClaimLedgerRunSource(
            run_id="claim-ledger",
            manifest_sha256="sha256:" + "f" * 64,
            output_sha256="sha256:" + "0" * 64,
            ledger=ledger,
            framework_b2=self.framework,
            source_selection=self.selection,
        )

        package = _build_package(self.source, ledger_source)

        self.assertFalse(package["writing_ready"])
        self.assertTrue(package["claim_planning_ready"])
        self.assertEqual(
            len(package["approved_claim_ledger"]["approved_claims"]),
            1,
        )
        self.assertNotIn(
            "rejected_claims",
            package["approved_claim_ledger"],
        )

    def test_b2_v2_package_rejects_forbidden_structural_key(self):
        package = self._package()
        package["approved_claim_ledger"]["rejected_claims"] = []

        with self.assertRaisesRegex(
            B2PackageV2Error,
            r"\$\.approved_claim_ledger\.rejected_claims",
        ):
            _validate_package(package)

    def test_b2_v2_package_allows_forbidden_word_in_claim_text(self):
        package = self._package()
        package["approved_claim_ledger"]["approved_claims"][0][
            "planned_claim"
        ] = '该研究没有公开名为"cards"的数据字段。'
        package["approved_claim_ledger"]["approved_claims"][0][
            "claim_id"
        ] = stable_id(
            "claim",
            {
                key: value
                for key, value in package["approved_claim_ledger"][
                    "approved_claims"
                ][0].items()
                if key != "claim_id"
            },
        )
        package["approved_claim_ledger"]["ledger_id"] = stable_id(
            "claim_ledger",
            {
                key: value
                for key, value in package["approved_claim_ledger"].items()
                if key != "ledger_id"
            },
        )
        package["package_id"] = stable_id(
            "package_b2",
            {
                key: value
                for key, value in package.items()
                if key != "package_id"
            },
        )

        validated = _validate_package(package)

        self.assertIn(
            '"cards"',
            validated["approved_claim_ledger"]["approved_claims"][0][
                "planned_claim"
            ],
        )

    def test_b2_v2_cli_requires_source_and_ledger_runs(self):
        args = build_parser().parse_args(
            [
                "chapter-knowledge-package-b2",
                "--source-window-run-id",
                "source-window",
                "--claim-ledger-run-id",
                "claim-ledger",
            ]
        )
        self.assertEqual(args.source_window_run_id, "source-window")
        self.assertEqual(args.claim_ledger_run_id, "claim-ledger")

    def _schema(self) -> dict[str, Any]:
        section = self.framework["sections"][1]
        return build_claim_ledger_draft_schema(
            section=section,
            paper_ids=section["paper_ids"],
            window_ids=["window-a", "window-b"],
            citation_keys=section["citation_keys"],
        )

    def _draft(self, claims: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "schema_version": CLAIM_LEDGER_DRAFT_SCHEMA_VERSION,
            "section_id": self.framework["sections"][1]["section_id"],
            "claims": copy.deepcopy(claims),
        }

    def _adjudicate(
        self,
        claims: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return adjudicate_claim_ledger(
            self._draft(claims),
            framework_b2=self.framework,
            source_selection=self.selection,
        )

    def _run(
        self,
        run_id: str,
        claims: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return ClaimLedgerRunner(
            self.workspace,
            analysis_client=FakeClaimClient(claims),
            token_counter=FakeTokenCounter(),
            framework_loader=lambda workspace, value: self.framework_source,
            source_loader=lambda workspace, value: self.source,
        ).run(
            framework_b2_run_id="framework-b2",
            source_window_run_id="source-window",
            run_id=run_id,
            model_profile_path=MODEL_PROFILE,
        )

    def _package(self) -> dict[str, Any]:
        ledger, rejected = self._adjudicate(
            [_cross_claim(self.selection)]
        )
        self.assertEqual(rejected, [])
        ledger_source = ClaimLedgerRunSource(
            run_id="claim-ledger",
            manifest_sha256="sha256:" + "f" * 64,
            output_sha256="sha256:" + "0" * 64,
            ledger=ledger,
            framework_b2=self.framework,
            source_selection=self.selection,
        )
        return _build_package(self.source, ledger_source)


def _framework_v1() -> dict[str, Any]:
    sections = []
    for index, section_type in enumerate(
        ("introduction", "body", "conclusion"),
        start=1,
    ):
        paper_ids = ["paper-a", "paper-b"]
        section = {
            "section_index": index,
            "section_id": f"section-{index}",
            "title": f"章节{index}",
            "section_type": section_type,
            "question": "不同方法如何作用？",
            "purpose": "比较方法和边界。",
            "dimension_indexes": [1],
            "required_comparisons": (
                ["比较两种方法"] if section_type == "body" else []
            ),
            "controversy_ids": [],
            "corpus_gap_indexes": [],
            "corpus_limitations": ["本次语料有限。"],
            "paper_ids": paper_ids,
            "contribution_ids": ["contribution-a", "contribution-b"],
            "citation_keys": ["ref_a", "ref_b"],
            "bibliography_status": "complete",
        }
        sections.append(section)
    result = {
        "schema_version": "llm.review_framework.v1",
        "framework_id": "framework-v1-id",
        "topic": "通航能力",
        "review_goal": "比较方法和边界",
        "corpus_scope": "targeted_sample",
        "source_landscape_id": "landscape-1",
        "working_title": "通航能力综述",
        "central_question": "如何提升通航能力？",
        "sections": sections,
        "unused_papers": [],
        "bibliography_issues": [],
    }
    return result


def _source_selection(section: dict[str, Any]) -> dict[str, Any]:
    windows = [
        _window("window-a", "paper-a", "ref_a", "contribution-a"),
        _window("window-b", "paper-b", "ref_b", "contribution-b"),
    ]
    value = {
        "schema_version": "llm.source_window_selection.v1",
        "source": {
            "framework_run_id": "framework-v1",
            "framework_id": "framework-v1-id",
        },
        "section": {
            key: copy.deepcopy(section[key])
            for key in (
                "section_index",
                "section_id",
                "title",
                "section_type",
                "question",
                "purpose",
                "dimension_indexes",
                "required_comparisons",
                "controversy_ids",
                "corpus_gap_indexes",
                "corpus_limitations",
                "paper_ids",
                "contribution_ids",
                "citation_keys",
                "bibliography_status",
            )
        },
        "material_tier": "tier_1",
        "understanding_projections": [],
        "selected_source_windows": windows,
        "expanded_markdown_sources": [],
        "citation_metadata": [
            {"paper_id": "paper-a", "citation_key": "ref_a"},
            {"paper_id": "paper-b", "citation_key": "ref_b"},
        ],
        "coverage": {
            "paper_count": 2,
            "window_count": 2,
        },
    }
    value["selection_id"] = stable_id("source_selection", value)
    return value


def _window(
    window_id: str,
    paper_id: str,
    citation_key: str,
    contribution_id: str,
) -> dict[str, Any]:
    return {
        "window_id": window_id,
        "paper_id": paper_id,
        "citation_key": citation_key,
        "text": f"{paper_id}报告了方法结果。",
        "window_type": "result_context",
        "result_types": ["simulation_result"],
        "validation_levels": ["simulation"],
        "contribution_ids": [contribution_id],
    }


def _base_claim() -> dict[str, Any]:
    return {
        "claim_type": "direct_fact",
        "planned_claim": "论文A报告了仿真结果。",
        "section_role": "core_argument",
        "importance": "core",
        "supporting_papers": ["paper-a"],
        "supporting_source_windows": ["window-a"],
        "citation_keys": ["ref_a"],
        "support_mode": "source_reported",
        "result_type": "simulation_result",
        "validation_level": "simulation",
        "allowed_strength": "source_reported_only",
        "audit_notes": "",
    }


def _direct_claim(selection: dict[str, Any]) -> dict[str, Any]:
    return _base_claim()


def _supporting_direct_claim(selection: dict[str, Any]) -> dict[str, Any]:
    claim = _base_claim()
    claim["planned_claim"] = "论文B也报告了仿真结果。"
    claim["importance"] = "supporting"
    claim["supporting_papers"] = ["paper-b"]
    claim["supporting_source_windows"] = ["window-b"]
    claim["citation_keys"] = ["ref_b"]
    return claim


def _cross_claim(selection: dict[str, Any]) -> dict[str, Any]:
    claim = _base_claim()
    claim.update(
        {
            "claim_type": "cross_paper_synthesis",
            "planned_claim": "本次纳入的文献显示，两类方法形成互补路径。",
            "section_role": "comparison",
            "supporting_papers": ["paper-a", "paper-b"],
            "supporting_source_windows": ["window-a", "window-b"],
            "citation_keys": ["ref_a", "ref_b"],
            "support_mode": "corpus_level_inference",
            "result_type": "mixed",
            "validation_level": "mixed",
            "allowed_strength": "targeted_corpus_only",
        }
    )
    return claim


def _corpus_gap() -> dict[str, Any]:
    claim = _base_claim()
    claim.update(
        {
            "claim_type": "corpus_gap",
            "planned_claim": "本次语料尚未充分覆盖长期工程验证。",
            "supporting_papers": [],
            "supporting_source_windows": [],
            "citation_keys": [],
            "support_mode": "corpus_scope_observation",
            "result_type": "not_applicable",
            "validation_level": "not_applicable",
            "allowed_strength": "targeted_corpus_only",
        }
    )
    return claim
