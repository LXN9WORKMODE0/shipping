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
    ReviewClaimAuditB2RunSource,
)
from shipping_pipeline.review_claim_audit_b2_adjudication import (
    ReviewClaimAuditB2AdjudicationRunner,
    load_review_claim_audit_b2_adjudication_run,
)
from shipping_pipeline.review_claim_audit_b2_adjudication_contracts import (
    ReviewClaimAuditB2AdjudicationContractError,
    build_adjudication_draft_schema,
    derive_adjudication_input,
    validate_adjudication_draft,
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
            prompt_tokens=900,
            encoded_prompt_sha256="sha256:" + "1" * 64,
        )


class FakeAdjudicationClient:
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
        if task != "review_claim_audit_b2_adjudication":
            raise AssertionError(task)
        response = {
            "id": "adjudication-response",
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


class ReviewClaimAuditB2AdjudicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TEST_TMP_ROOT / f"adjudication_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self.audit_source = _audit_source()
        self.adjudication_input = derive_adjudication_input(
            self.audit_source.audit_input,
            self.audit_source.audit,
            audit_run_id=self.audit_source.run_id,
            audit_manifest_sha256=self.audit_source.manifest_sha256,
        )
        self.schema = build_adjudication_draft_schema(
            self.adjudication_input,
            max_reason_chars=800,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_dismissed_risk_makes_chapter_publishable(self):
        result = self._validate(_draft(self.adjudication_input, "dismiss"))

        self.assertEqual(result["summary"]["dismissed_count"], 1)
        self.assertEqual(result["summary"]["final_risk_sentence_count"], 0)
        self.assertTrue(
            result["summary"]["publishable_after_adjudication"]
        )

    def test_confirm_preserves_initial_risk(self):
        result = self._validate(_draft(self.adjudication_input, "confirm"))

        row = result["adjudications"][0]
        self.assertEqual(row["final_risk_categories"], ["unplanned_claim"])
        self.assertTrue(row["blocking"])
        self.assertEqual(row["recommended_actions"][0], "delete")

    def test_reclassify_requires_different_non_supported_risk(self):
        draft = _draft(self.adjudication_input, "reclassify")
        draft["adjudications"][0]["final_risk_categories"] = [
            "unplanned_claim"
        ]

        with self.assertRaisesRegex(
            ReviewClaimAuditB2AdjudicationContractError,
            "adjudication_b2.reclassify_unchanged",
        ):
            self._validate(draft)

    def test_confirm_cannot_change_risk_category(self):
        draft = _draft(self.adjudication_input, "confirm")
        draft["adjudications"][0]["final_risk_categories"] = ["qualified"]

        with self.assertRaisesRegex(
            ReviewClaimAuditB2AdjudicationContractError,
            "adjudication_b2.schema_invalid",
        ):
            self._validate(draft)

    def test_adjudication_cannot_remove_claim_coverage(self):
        draft = _draft(self.adjudication_input, "dismiss")
        draft["adjudications"][0]["claim_reassessments"][0][
            "assessment"
        ] = "not_relevant"

        with self.assertRaisesRegex(
            ReviewClaimAuditB2AdjudicationContractError,
            "adjudication_b2.claim_coverage_changed",
        ):
            self._validate(draft)

    def test_unplanned_fragment_cannot_already_exist_in_planned_claim(self):
        draft = _draft(self.adjudication_input, "confirm")
        draft["adjudications"][0]["uncovered_texts"] = ["有限条件下"]

        with self.assertRaisesRegex(
            ReviewClaimAuditB2AdjudicationContractError,
            "adjudication_b2.uncovered_text_already_planned",
        ):
            self._validate(draft)

    def test_source_audit_without_risk_is_rejected_before_planning(self):
        source = _audit_source()
        for row in source.audit["sentence_audits"]:
            row["requires_revision"] = False
            row["risk_categories"] = ["supported"]

        with self.assertRaisesRegex(
            ReviewClaimAuditB2AdjudicationContractError,
            "adjudication_b2.no_risk_sentences",
        ):
            derive_adjudication_input(
                source.audit_input,
                source.audit,
                audit_run_id=source.run_id,
                audit_manifest_sha256=source.manifest_sha256,
            )

    def test_runner_publishes_and_loader_replays(self):
        draft = _draft(self.adjudication_input, "dismiss")
        client = FakeAdjudicationClient(draft)
        loader = lambda workspace, run_id: self.audit_source
        result = ReviewClaimAuditB2AdjudicationRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
            audit_loader=loader,
        ).run(
            audit_run_id=self.audit_source.run_id,
            run_id="adjudication-success",
            model_profile_path=MODEL_PROFILE,
            audit_config_path=AUDIT_CONFIG,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(client.calls, 1)
        loaded = load_review_claim_audit_b2_adjudication_run(
            self.workspace,
            "adjudication-success",
            audit_loader=loader,
        )
        self.assertEqual(
            loaded.adjudication["adjudication_id"],
            result["adjudication_id"],
        )

    def test_cli_exposes_adjudication_command(self):
        args = build_parser().parse_args(
            [
                "llm-review-claim-audit-b2-adjudicate",
                "--audit-run-id",
                "audit-run",
            ]
        )

        self.assertEqual(args.audit_run_id, "audit-run")

    def _validate(self, draft: dict[str, Any]) -> dict[str, Any]:
        return validate_adjudication_draft(
            draft,
            adjudication_input=self.adjudication_input,
            source_audit=self.audit_source.audit,
            schema=self.schema,
        )


def _draft(adjudication_input: dict[str, Any], verdict: str) -> dict[str, Any]:
    source = adjudication_input["risk_sentences"][0]
    if verdict == "dismiss":
        risks = ["supported"]
        assessment = "supports_all_content"
    elif verdict == "confirm":
        risks = copy.deepcopy(source["initial_risk_categories"])
        assessment = "supports_part_of_content"
    else:
        risks = ["qualified"]
        assessment = "supports_part_of_content"
    return {
        "schema_version": "llm.review_claim_audit_b2_adjudication_draft.v1",
        "section_id": adjudication_input["section"]["section_id"],
        "audit_id": adjudication_input["source"]["audit_id"],
        "adjudications": [
            {
                "sentence_id": source["sentence_id"],
                "verdict": verdict,
                "claim_reassessments": [
                    {
                        "claim_id": claim_id,
                        "assessment": assessment,
                        "reason": "独立复核后的来源覆盖判断。",
                    }
                    for claim_id in source["paragraph_claim_ids"]
                ],
                "final_risk_categories": risks,
                "uncovered_texts": (
                    ["全面有效"] if "unplanned_claim" in risks else []
                ),
                "reason": "独立裁决已完成。",
            }
        ],
        "chapter_assessment": "风险句已完成独立裁决。",
    }


def _audit_source() -> ReviewClaimAuditB2RunSource:
    core_claim = _claim("claim-core", "core", "window-core")
    supporting_claim = _claim("claim-support", "supporting", "window-support")
    audit_input = {
        "source": {
            "writing_run_id": "writing-run",
            "chapter_id": "chapter-id",
        },
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
                "sentence_id": "sentence-supported",
                "paragraph_id": "paragraph-2",
                "paragraph_index": 2,
                "sentence_index": 1,
                "text": "论文报告了辅助结果。",
                "paragraph_claim_ids": ["claim-support"],
                "paragraph_citation_keys": ["ref-support"],
            },
        ],
        "implemented_claims": [core_claim, supporting_claim],
        "source_windows": [
            _window("window-core", "ref-core", "有限仿真显示有效。"),
            _window("window-support", "ref-support", "论文报告辅助结果。"),
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
                "sentence_id": "sentence-supported",
                "planned_claim_ids": ["claim-support"],
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


def _claim(
    claim_id: str,
    importance: str,
    window_id: str,
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "planned_claim": "该方法在有限条件下报告结果。",
        "importance": importance,
        "claim_type": "direct_fact",
        "supporting_source_windows": [window_id],
        "citation_keys": ["ref-" + claim_id.split("-")[-1]],
    }


def _window(window_id: str, citation_key: str, text: str) -> dict[str, Any]:
    return {
        "window_id": window_id,
        "citation_key": citation_key,
        "window_type": "key_result",
        "text": text,
    }


if __name__ == "__main__":
    unittest.main()
