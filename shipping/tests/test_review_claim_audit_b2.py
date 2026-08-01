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
    B2PackageV2RunSource,
)
from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.review_claim_audit_b2 import (
    ReviewClaimAuditB2Runner,
    load_review_claim_audit_b2_run,
)
from shipping_pipeline.review_claim_audit_b2_contracts import (
    ReviewClaimAuditB2ContractError,
    build_audit_draft_schema,
    build_sentence_catalogue,
    derive_audit_input,
    load_review_claim_audit_b2_config,
    validate_audit_draft,
)
from shipping_pipeline.review_claim_audit_b2_report import (
    render_review_claim_audit_b2_report,
)
from shipping_pipeline.review_writing_b2 import ReviewWritingB2RunSource


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "deepseek-v4-pro-official.json"
)
AUDIT_CONFIG = (
    PROJECT_ROOT / "config" / "review-claim-audit-b2-default.json"
)


class FakeTokenCounter:
    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        return TokenCount(
            prompt_tokens=1200,
            encoded_prompt_sha256="sha256:" + "1" * 64,
        )


class FakeAuditClient:
    provider = "openai-compatible"
    model = "deepseek-v4-pro"

    def __init__(self, draft: dict[str, Any]) -> None:
        self.draft = draft
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        task: str,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> ProviderResult:
        self.calls.append(copy.deepcopy(context))
        if task != "review_claim_audit_b2":
            raise AssertionError(task)
        response = {
            "id": "audit-b2-response",
            "model": self.model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            self.draft,
                            ensure_ascii=False,
                        ),
                        "reasoning_content": "",
                    },
                }
            ],
            "usage": {
                "prompt_tokens": context["planned_input_tokens"],
                "completion_tokens": 600,
                "total_tokens": context["planned_input_tokens"] + 600,
            },
        }
        return ProviderResult(
            raw_body=json.dumps(response, ensure_ascii=False).encode(),
            parsed_response=response,
            response_id=response["id"],
            response_model=self.model,
            system_fingerprint=None,
            finish_reason="stop",
            reasoning_content_chars=0,
            usage=dict(response["usage"]),
        )


class ReviewClaimAuditB2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TEST_TMP_ROOT / f"audit_b2_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self.writing_source = _writing_source()
        self.config = load_review_claim_audit_b2_config(AUDIT_CONFIG)
        self.audit_input = derive_audit_input(
            self.writing_source.writing_input,
            self.writing_source.chapter,
            writing_run_id=self.writing_source.run_id,
            writing_manifest_sha256=self.writing_source.manifest_sha256,
        )
        self.schema = build_audit_draft_schema(
            self.audit_input,
            self.config,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_sentence_catalogue_is_deterministic(self):
        first = build_sentence_catalogue(self.writing_source.chapter)
        second = build_sentence_catalogue(self.writing_source.chapter)

        self.assertEqual(first, second)
        self.assertEqual(
            [row["text"] for row in first],
            ["仿真结果显示效率提高10%。", "本节比较两种方法。"],
        )

    def test_semicolon_does_not_split_comparative_sentence(self):
        chapter = copy.deepcopy(self.writing_source.chapter)
        chapter["paragraphs"][0]["text"] = "前者采用仿真；后者采用实测。"

        catalogue = build_sentence_catalogue(chapter)

        self.assertEqual(len(catalogue), 1)
        self.assertEqual(catalogue[0]["text"], "前者采用仿真；后者采用实测。")

    def test_supported_audit_is_publishable(self):
        audit = self._validate(_supported_draft(self.audit_input))

        self.assertTrue(audit["summary"]["audit_passed"])
        self.assertTrue(audit["summary"]["publishable"])
        self.assertEqual(audit["summary"]["mapped_claim_count"], 2)
        self.assertEqual(
            audit["sentence_audits"][0]["planned_claim_ids"],
            ["claim-simulation"],
        )

    def test_supported_cannot_be_mixed_with_risk(self):
        draft = _supported_draft(self.audit_input)
        draft["sentence_audits"][0]["risk_categories"] = [
            "supported",
            "qualified",
        ]

        with self.assertRaisesRegex(
            ReviewClaimAuditB2ContractError,
            "audit_b2.schema_invalid",
        ):
            self._validate(draft)

    def test_supported_allows_secondary_partial_claim_mapping(self):
        draft = _supported_draft(self.audit_input)
        draft["sentence_audits"][0]["claim_assessments"][1][
            "assessment"
        ] = "supports_part_of_content"

        audit = self._validate(draft)

        self.assertTrue(audit["summary"]["publishable"])

    def test_navigation_importance_is_program_derived(self):
        draft = _supported_draft(self.audit_input)
        first = draft["sentence_audits"][0]
        for assessment in first["claim_assessments"]:
            assessment["assessment"] = "not_relevant"
        second = draft["sentence_audits"][1]
        for assessment in second["claim_assessments"]:
            assessment["assessment"] = "supports_all_content"

        audit = self._validate(draft)

        self.assertEqual(
            audit["sentence_audits"][0]["importance"],
            "navigation",
        )

    def test_unmapped_substantive_sentence_must_report_unplanned_claim(self):
        draft = _supported_draft(self.audit_input)
        sentence = draft["sentence_audits"][0]
        for assessment in sentence["claim_assessments"]:
            assessment["assessment"] = "not_relevant"
        sentence["risk_categories"] = ["qualified"]

        with self.assertRaisesRegex(
            ReviewClaimAuditB2ContractError,
            "audit_b2.unplanned_claim_not_reported",
        ):
            self._validate(draft)

    def test_core_source_mismatch_is_blocking(self):
        draft = _supported_draft(self.audit_input)
        sentence = draft["sentence_audits"][0]
        sentence["risk_categories"] = ["source_mismatch"]

        audit = self._validate(draft)

        result = audit["sentence_audits"][0]
        self.assertEqual(result["importance"], "core")
        self.assertTrue(result["blocking"])
        self.assertFalse(audit["summary"]["publishable"])

    def test_supporting_source_mismatch_requires_revision_but_not_blocking(self):
        draft = _supported_draft(self.audit_input)
        sentence = draft["sentence_audits"][1]
        sentence["risk_categories"] = ["source_mismatch"]

        audit = self._validate(draft)

        result = audit["sentence_audits"][1]
        self.assertEqual(result["importance"], "supporting")
        self.assertFalse(result["blocking"])
        self.assertTrue(result["requires_revision"])
        self.assertFalse(audit["summary"]["publishable"])

    def test_implemented_claim_must_map_to_at_least_one_sentence(self):
        draft = _supported_draft(self.audit_input)
        sentence = draft["sentence_audits"][1]
        for assessment in sentence["claim_assessments"]:
            assessment["assessment"] = "not_relevant"
        sentence["risk_categories"] = ["unplanned_claim"]

        with self.assertRaisesRegex(
            ReviewClaimAuditB2ContractError,
            "audit_b2.implemented_claim_not_mapped",
        ):
            self._validate(draft)

    def test_runner_publishes_and_loader_replays(self):
        draft = _supported_draft(self.audit_input)
        client = FakeAuditClient(draft)
        loader = lambda workspace, run_id: self.writing_source
        result = ReviewClaimAuditB2Runner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
            writing_loader=loader,
        ).run(
            writing_run_id=self.writing_source.run_id,
            run_id="audit-b2-success",
            model_profile_path=MODEL_PROFILE,
            audit_config_path=AUDIT_CONFIG,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(client.calls), 1)
        loaded = load_review_claim_audit_b2_run(
            self.workspace,
            "audit-b2-success",
            writing_loader=loader,
        )
        self.assertEqual(loaded.audit["audit_id"], result["audit_id"])

    def test_report_expands_risky_claim_and_source_window(self):
        draft = _supported_draft(self.audit_input)
        sentence = draft["sentence_audits"][0]
        sentence["risk_categories"] = ["qualified"]
        audit = self._validate(draft)
        manifest = {
            "run_id": "audit-run",
            "writing_run_id": self.writing_source.run_id,
            "status": "completed",
        }

        report = render_review_claim_audit_b2_report(
            manifest,
            audit,
            self.audit_input,
        )

        self.assertIn("仿真结果显示效率提高10%", report)
        self.assertIn("window-a", report)
        self.assertIn("Source A reports 10%", report)

    def test_cli_exposes_b2_audit_command(self):
        args = build_parser().parse_args(
            [
                "llm-review-claim-audit-b2",
                "--writing-run-id",
                "writing-run",
            ]
        )

        self.assertEqual(args.writing_run_id, "writing-run")

    def _validate(self, draft: dict[str, Any]) -> dict[str, Any]:
        return validate_audit_draft(
            draft,
            audit_input=self.audit_input,
            schema=self.schema,
        )


def _supported_draft(audit_input: dict[str, Any]) -> dict[str, Any]:
    sentence_audits = []
    expressed_by_sentence = ["claim-simulation", "claim-comparison"]
    for sentence, expressed in zip(
        audit_input["sentences"],
        expressed_by_sentence,
        strict=True,
    ):
        sentence_audits.append(
            {
                "sentence_id": sentence["sentence_id"],
                "paragraph_id": sentence["paragraph_id"],
                "sentence_index": sentence["sentence_index"],
                "claim_assessments": [
                    {
                        "claim_id": claim_id,
                        "assessment": (
                            "supports_all_content"
                            if claim_id == expressed
                            else "not_relevant"
                        ),
                        "reason": "来源和计划Claim与当前句匹配。",
                    }
                    for claim_id in sentence["paragraph_claim_ids"]
                ],
                "risk_categories": ["supported"],
                "reason": "句子完整保留计划Claim的范围和验证限定。",
            }
        )
    return {
        "schema_version": "llm.review_claim_audit_b2_draft.v1",
        "section_id": audit_input["section"]["section_id"],
        "chapter_id": audit_input["source"]["chapter_id"],
        "sentence_audits": sentence_audits,
        "chapter_assessment": "两个句子均由计划Claim和绑定窗口支持。",
    }


def _writing_source() -> ReviewWritingB2RunSource:
    claims = [
        _claim(
            claim_id="claim-simulation",
            planned_claim="仿真结果显示效率提高10%。",
            importance="core",
            claim_type="direct_fact",
            window_ids=["window-a"],
            citation_keys=["ref-a"],
        ),
        _claim(
            claim_id="claim-comparison",
            planned_claim="本次纳入的文献显示两种方法存在差异。",
            importance="supporting",
            claim_type="cross_paper_synthesis",
            window_ids=["window-a", "window-b"],
            citation_keys=["ref-a", "ref-b"],
        ),
    ]
    writing_input = {
        "writing_input_id": "writing-input-id",
        "approved_claims": claims,
        "selected_source_windows": [
            _window("window-a", "paper-a", "ref-a", "Source A reports 10%."),
            _window("window-b", "paper-b", "ref-b", "Source B uses another method."),
        ],
        "citation_metadata": [
            _citation("paper-a", "ref-a"),
            _citation("paper-b", "ref-b"),
        ],
    }
    chapter = {
        "chapter_id": "chapter-id",
        "section_id": "section-id",
        "section_index": 2,
        "title": "测试章节",
        "paragraphs": [
            {
                "paragraph_id": "paragraph-id",
                "paragraph_index": 1,
                "text": "仿真结果显示效率提高10%。本节比较两种方法。",
                "implemented_claim_ids": [
                    "claim-simulation",
                    "claim-comparison",
                ],
                "citation_keys": ["ref-a", "ref-b"],
            }
        ],
    }
    package_source = B2PackageV2RunSource(
        run_id="package-run",
        manifest_sha256="sha256:" + "a" * 64,
        output_sha256="sha256:" + "b" * 64,
        package={"package_id": "package-id"},
        token_budget={
            "model_profile_id": "deepseek-v4-pro-official",
            "planned_max_output_tokens": 32768,
        },
    )
    return ReviewWritingB2RunSource(
        run_id="writing-run",
        manifest_sha256="sha256:" + "c" * 64,
        output_sha256="sha256:" + "d" * 64,
        writing_input=writing_input,
        chapter=chapter,
        package_source=package_source,
    )


def _claim(
    *,
    claim_id: str,
    planned_claim: str,
    importance: str,
    claim_type: str,
    window_ids: list[str],
    citation_keys: list[str],
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "planned_claim": planned_claim,
        "importance": importance,
        "claim_type": claim_type,
        "supporting_source_windows": window_ids,
        "citation_keys": citation_keys,
        "result_type": "quantitative_result",
        "validation_level": "simulation_validated",
        "allowed_strength": "source_reported_only",
        "required_qualifier": (
            "本次纳入的文献"
            if claim_type == "cross_paper_synthesis"
            else ""
        ),
        "prohibited_phrasings": [],
    }


def _window(
    window_id: str,
    paper_id: str,
    citation_key: str,
    text: str,
) -> dict[str, Any]:
    return {
        "window_id": window_id,
        "paper_id": paper_id,
        "citation_key": citation_key,
        "window_type": "key_result",
        "text": text,
    }


def _citation(paper_id: str, citation_key: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "citation_key": citation_key,
        "paper_title": paper_id,
        "authors": ["Author"],
        "year": 2025,
    }


if __name__ == "__main__":
    unittest.main()
