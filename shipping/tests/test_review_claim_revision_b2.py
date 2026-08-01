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
from shipping_pipeline.review_claim_audit_b2 import ReviewClaimAuditB2RunSource
from shipping_pipeline.review_claim_audit_b2_adjudication import (
    ReviewClaimAuditB2AdjudicationRunSource,
)
from shipping_pipeline.review_claim_audit_b2_adjudication_contracts import (
    build_adjudication_draft_schema,
    derive_adjudication_input,
    validate_adjudication_draft,
)
from shipping_pipeline.review_claim_revision_b2 import (
    ReviewClaimRevisionB2Runner,
    load_review_claim_revision_b2_run,
)
from shipping_pipeline.review_claim_revision_b2_contracts import (
    ReviewClaimRevisionB2ContractError,
    build_revision_draft_schema,
    derive_revision_input,
    validate_revision_draft,
)
from shipping_pipeline.review_writing_b2 import ReviewWritingB2RunSource


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "deepseek-v4-pro-official.json"
)
AUDIT_CONFIG = PROJECT_ROOT / "config" / "review-claim-audit-b2-default.json"


class FakeTokenCounter:
    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        return TokenCount(
            prompt_tokens=800,
            encoded_prompt_sha256="sha256:" + "1" * 64,
        )


class FakeRevisionClient:
    provider = "openai-compatible"
    model = "deepseek-v4-pro"

    def __init__(self, draft: dict[str, Any]) -> None:
        self.draft = draft
        self.calls = 0

    def complete(
        self,
        task: str,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> ProviderResult:
        self.calls += 1
        if task != "review_claim_revision_b2":
            raise AssertionError(task)
        response = {
            "id": "revision-response",
            "model": self.model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(self.draft, ensure_ascii=False),
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
            raw_body=json.dumps(response, ensure_ascii=False).encode(),
            parsed_response=response,
            response_id=response["id"],
            response_model=self.model,
            system_fingerprint=None,
            finish_reason="stop",
            reasoning_content_chars=0,
            usage=dict(response["usage"]),
        )


class ReviewClaimRevisionB2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TEST_TMP_ROOT / f"revision_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self.source = _adjudication_source()
        self.revision_input = derive_revision_input(
            self.source.adjudication_input,
            self.source.adjudication,
            self.source.audit_source.audit_input,
            adjudication_run_id=self.source.run_id,
            adjudication_manifest_sha256=self.source.manifest_sha256,
        )
        self.schema = build_revision_draft_schema(
            self.revision_input, max_reason_chars=800
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_input_contains_full_paragraph_and_only_final_risks(self):
        row = self.revision_input["risk_sentences"][0]

        self.assertIn("后一支持句", row["paragraph_text"])
        self.assertEqual(row["final_risk_categories"], ["unplanned_claim"])
        self.assertEqual(len(self.revision_input["risk_sentences"]), 1)

    def test_validated_suggestions_remain_pending_and_not_applied(self):
        result = self._validate(_draft(self.revision_input))

        self.assertEqual(result["decision_status"], "pending")
        self.assertFalse(result["auto_applied"])
        self.assertEqual(result["summary"]["option_count"], 3)
        self.assertEqual(
            [
                option["option_type"]
                for option in result["suggestions"][0]["revision_options"]
            ],
            ["minimal_edit", "conservative_rewrite", "delete_or_split"],
        )

    def test_delete_option_cannot_retain_claim(self):
        draft = _draft(self.revision_input)
        option = draft["suggestions"][0]["revision_options"][2]
        option["operation"] = "delete"
        option["replacement_sentences"] = []

        with self.assertRaisesRegex(
            ReviewClaimRevisionB2ContractError,
            "revision_b2.schema_invalid",
        ):
            self._validate(draft)

    def test_rewrite_cannot_use_unbound_citation(self):
        draft = _draft(self.revision_input)
        draft["suggestions"][0]["revision_options"][0]["citation_keys"] = [
            "ref-other"
        ]

        with self.assertRaisesRegex(
            ReviewClaimRevisionB2ContractError,
            "revision_b2.schema_invalid",
        ):
            self._validate(draft)

    def test_options_cannot_repeat_same_replacement(self):
        draft = _draft(self.revision_input)
        options = draft["suggestions"][0]["revision_options"]
        options[1]["replacement_sentences"] = copy.deepcopy(
            options[0]["replacement_sentences"]
        )

        with self.assertRaisesRegex(
            ReviewClaimRevisionB2ContractError,
            "revision_b2.options_duplicate",
        ):
            self._validate(draft)

    def test_no_final_risk_is_rejected_before_api_planning(self):
        source = _adjudication_source()
        source.adjudication["adjudications"][0]["requires_revision"] = False

        with self.assertRaisesRegex(
            ReviewClaimRevisionB2ContractError,
            "revision_b2.no_risk_sentences",
        ):
            derive_revision_input(
                source.adjudication_input,
                source.adjudication,
                source.audit_source.audit_input,
                adjudication_run_id=source.run_id,
                adjudication_manifest_sha256=source.manifest_sha256,
            )

    def test_runner_publishes_suggestions_and_loader_replays(self):
        draft = _draft(self.revision_input)
        client = FakeRevisionClient(draft)
        loader = lambda workspace, run_id: self.source
        result = ReviewClaimRevisionB2Runner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
            adjudication_loader=loader,
        ).run(
            adjudication_run_id=self.source.run_id,
            run_id="revision-success",
            model_profile_path=MODEL_PROFILE,
            audit_config_path=AUDIT_CONFIG,
        )

        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["auto_applied"])
        self.assertEqual(client.calls, 1)
        loaded = load_review_claim_revision_b2_run(
            self.workspace,
            "revision-success",
            adjudication_loader=loader,
        )
        self.assertEqual(
            loaded.revision_suggestions["revision_suggestion_id"],
            result["revision_suggestion_id"],
        )

    def test_cli_exposes_revision_command(self):
        args = build_parser().parse_args(
            [
                "llm-review-claim-revision-b2",
                "--adjudication-run-id",
                "adjudication-run",
            ]
        )

        self.assertEqual(args.adjudication_run_id, "adjudication-run")

    def _validate(self, draft: dict[str, Any]) -> dict[str, Any]:
        return validate_revision_draft(
            draft, revision_input=self.revision_input, schema=self.schema
        )


def _draft(revision_input: dict[str, Any]) -> dict[str, Any]:
    source = revision_input["risk_sentences"][0]
    common = {
        "retained_claim_ids": ["claim-core"],
        "citation_keys": ["ref-core"],
        "rationale": "只保留计划Claim支持的内容。",
    }
    return {
        "schema_version": "llm.review_claim_revision_b2_draft.v1",
        "section_id": revision_input["section"]["section_id"],
        "suggestions": [
            {
                "sentence_id": source["sentence_id"],
                "revision_options": [
                    {
                        "option_type": "minimal_edit",
                        "operation": "replace",
                        "replacement_sentences": [
                            "该方法在有限仿真条件下报告了有效结果。"
                        ],
                        **copy.deepcopy(common),
                    },
                    {
                        "option_type": "conservative_rewrite",
                        "operation": "replace",
                        "replacement_sentences": [
                            "来源仅报告该方法在有限仿真条件下的结果。"
                        ],
                        **copy.deepcopy(common),
                    },
                    {
                        "option_type": "delete_or_split",
                        "operation": "split",
                        "replacement_sentences": [
                            "该方法接受了有限仿真。",
                            "仿真结果显示方法有效。",
                        ],
                        **copy.deepcopy(common),
                    },
                ],
            }
        ],
        "chapter_assessment": "三类候选均仅保留计划Claim支持内容。",
    }


def _adjudication_source() -> ReviewClaimAuditB2AdjudicationRunSource:
    audit_source = _audit_source()
    adjudication_input = derive_adjudication_input(
        audit_source.audit_input,
        audit_source.audit,
        audit_run_id=audit_source.run_id,
        audit_manifest_sha256=audit_source.manifest_sha256,
    )
    draft_schema = build_adjudication_draft_schema(
        adjudication_input, max_reason_chars=800
    )
    source = adjudication_input["risk_sentences"][0]
    adjudication = validate_adjudication_draft(
        {
            "schema_version": "llm.review_claim_audit_b2_adjudication_draft.v1",
            "section_id": adjudication_input["section"]["section_id"],
            "audit_id": adjudication_input["source"]["audit_id"],
            "adjudications": [
                {
                    "sentence_id": source["sentence_id"],
                    "verdict": "confirm",
                    "claim_reassessments": [
                        {
                            "claim_id": "claim-core",
                            "assessment": "supports_part_of_content",
                            "reason": "来源不支持全面有效。",
                        }
                    ],
                    "final_risk_categories": ["unplanned_claim"],
                    "uncovered_texts": ["全面有效"],
                    "reason": "全面有效超出计划Claim。",
                }
            ],
            "chapter_assessment": "风险成立。",
        },
        adjudication_input=adjudication_input,
        source_audit=audit_source.audit,
        schema=draft_schema,
    )
    return ReviewClaimAuditB2AdjudicationRunSource(
        run_id="adjudication-run",
        manifest_sha256="sha256:" + "2" * 64,
        output_sha256="sha256:" + "3" * 64,
        adjudication_input=adjudication_input,
        adjudication=adjudication,
        audit_source=audit_source,
    )


def _audit_source() -> ReviewClaimAuditB2RunSource:
    audit_input = {
        "source": {"writing_run_id": "writing-run", "chapter_id": "chapter-id"},
        "section": {
            "section_id": "section-id",
            "section_index": 2,
            "title": "测试章节",
        },
        "sentences": [
            {
                "sentence_id": "sentence-risk",
                "paragraph_id": "paragraph-1",
                "paragraph_index": 1,
                "sentence_index": 1,
                "text": "该方法在有限条件下仍被写成全面有效。",
                "paragraph_claim_ids": ["claim-core"],
                "paragraph_citation_keys": ["ref-core"],
            },
            {
                "sentence_id": "sentence-context",
                "paragraph_id": "paragraph-1",
                "paragraph_index": 1,
                "sentence_index": 2,
                "text": "后一支持句。",
                "paragraph_claim_ids": ["claim-core"],
                "paragraph_citation_keys": ["ref-core"],
            },
        ],
        "implemented_claims": [
            {
                "claim_id": "claim-core",
                "planned_claim": "该方法在有限仿真条件下报告有效结果。",
                "importance": "core",
                "claim_type": "direct_fact",
                "supporting_source_windows": ["window-core"],
                "citation_keys": ["ref-core"],
            }
        ],
        "source_windows": [
            {
                "window_id": "window-core",
                "citation_key": "ref-core",
                "window_type": "key_result",
                "text": "有限仿真显示有效。",
            }
        ],
        "citation_metadata": [],
    }
    audit = {
        "audit_id": "audit-id",
        "audit_input_id": "audit-input-id",
        "chapter_id": "chapter-id",
        "sentence_audits": [
            {
                "sentence_id": "sentence-risk",
                "paragraph_id": "paragraph-1",
                "paragraph_index": 1,
                "sentence_index": 1,
                "sentence_text": "该方法在有限条件下仍被写成全面有效。",
                "planned_claim_ids": ["claim-core"],
                "claim_assessments": [
                    {
                        "claim_id": "claim-core",
                        "assessment": "supports_part_of_content",
                        "reason": "来源仅支持有限场景。",
                    }
                ],
                "risk_categories": ["unplanned_claim"],
                "reason": "全面有效超出计划Claim。",
                "blocking": True,
                "requires_revision": True,
            },
            {
                "sentence_id": "sentence-context",
                "planned_claim_ids": ["claim-core"],
                "risk_categories": ["supported"],
                "blocking": False,
                "requires_revision": False,
            },
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
    writing_source = ReviewWritingB2RunSource(
        run_id="writing-run",
        manifest_sha256="sha256:" + "c" * 64,
        output_sha256="sha256:" + "d" * 64,
        writing_input={},
        chapter={"chapter_id": "chapter-id"},
        package_source=package_source,
    )
    return ReviewClaimAuditB2RunSource(
        run_id="audit-run",
        manifest_sha256="sha256:" + "e" * 64,
        output_sha256="sha256:" + "f" * 64,
        audit_input=audit_input,
        audit=audit,
        writing_source=writing_source,
    )


if __name__ == "__main__":
    unittest.main()
