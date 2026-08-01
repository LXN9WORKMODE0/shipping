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
from shipping_pipeline.claim_ledger import ClaimLedgerRunSource
from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.review_writing_b2 import (
    ReviewWritingB2RunSource,
    ReviewWritingB2Runner,
    load_review_writing_b2_run,
)
from shipping_pipeline.review_writing_b2_assembly import (
    ReviewWritingB2Assembler,
    ReviewWritingB2AssemblyError,
    build_phase3a_draft,
    load_review_writing_b2_assembly,
)
from shipping_pipeline.review_writing_b2_contracts import (
    CHAPTER_DRAFT_SCHEMA_VERSION,
    ReviewWritingB2ContractError,
    build_chapter_draft_schema,
    derive_writing_input,
    validate_chapter_draft,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "deepseek-v4-pro-official.json"
)
WRITING_CONFIG = PROJECT_ROOT / "config" / "review-writing-default.json"


class FakeTokenCounter:
    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        return TokenCount(
            prompt_tokens=1000,
            encoded_prompt_sha256="sha256:" + "1" * 64,
        )


class FakeWritingClient:
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
        if task != "review_writing_b2":
            raise AssertionError(task)
        response = {
            "id": "writing-b2-response",
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
                "completion_tokens": 500,
                "total_tokens": context["planned_input_tokens"] + 500,
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


class ReviewWritingB2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TEST_TMP_ROOT / f"writing_b2_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self.framework = _framework()
        self.package_source, self.claim_source = _sources(
            self.framework,
            section_index=2,
        )
        self.writing_input = derive_writing_input(
            self.package_source.package,
            self.framework,
            package_run_id=self.package_source.run_id,
            package_manifest_sha256=self.package_source.manifest_sha256,
            max_paragraph_chars=4000,
        )
        self.schema = build_chapter_draft_schema(self.writing_input)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_valid_chapter_implements_all_core_claims(self):
        chapter = self._validate(_draft(self.writing_input))

        self.assertEqual(
            chapter["implementation_audit"]["implemented_core_claim_count"],
            2,
        )
        self.assertEqual(
            chapter["implementation_audit"]["omitted_supporting_claim_ids"],
            [],
        )
        self.assertEqual(len(chapter["paragraphs"]), 2)

    def test_missing_core_claim_is_rejected(self):
        draft = _draft(self.writing_input)
        draft["paragraphs"][1]["implemented_claim_ids"] = []
        draft["paragraphs"][1]["citation_keys"] = []

        with self.assertRaisesRegex(
            ReviewWritingB2ContractError,
            "writing_b2.core_claims_missing",
        ):
            self._validate(draft)

    def test_claim_cannot_be_implemented_twice(self):
        draft = _draft(self.writing_input)
        duplicate = draft["paragraphs"][0]["implemented_claim_ids"][0]
        draft["paragraphs"][1]["implemented_claim_ids"].append(duplicate)

        with self.assertRaisesRegex(
            ReviewWritingB2ContractError,
            "writing_b2.claim_implemented_twice",
        ):
            self._validate(draft)

    def test_paragraph_citations_must_equal_claim_union(self):
        draft = _draft(self.writing_input)
        draft["paragraphs"][1]["citation_keys"].append("ref_extra")

        with self.assertRaisesRegex(
            ReviewWritingB2ContractError,
            "writing_b2.paragraph_citation_mismatch",
        ):
            self._validate(draft)

    def test_required_qualifier_must_remain_in_text(self):
        draft = _draft(self.writing_input)
        target = next(
            row
            for row in draft["paragraphs"]
            if "claim_cross" in row["implemented_claim_ids"]
        )
        target["text"] = "乙" * 80

        with self.assertRaisesRegex(
            ReviewWritingB2ContractError,
            "writing_b2.required_qualifier_missing",
        ):
            self._validate(draft)

    def test_paragraph_may_be_shorter_than_equal_share_when_chapter_passes(self):
        draft = _draft(self.writing_input)
        plans = self.writing_input["paragraph_plan"]
        average_min = (
            self.writing_input["writing_constraints"]["min_chars"]
            + len(plans)
            - 1
        ) // len(plans)
        self.assertLess(plans[0]["required_min_chars"], average_min)
        self.assertEqual(
            plans[0]["generation_target_chars"],
            100,
        )
        draft["paragraphs"][0]["text"] = (
            "".join(plans[0]["required_qualifiers"]) + "A" * 100
        )
        draft["paragraphs"][1]["text"] = (
            "".join(plans[1]["required_qualifiers"]) + "B" * 45
        )

        chapter = self._validate(draft)

        self.assertGreaterEqual(
            sum(len(row["text"]) for row in chapter["paragraphs"]),
            self.writing_input["writing_constraints"]["min_chars"],
        )

    def test_runner_publishes_and_loader_replays(self):
        client = FakeWritingClient(_draft(self.writing_input))
        result = self._runner(client).run(
            package_run_id=self.package_source.run_id,
            run_id="writing-b2-success",
            model_profile_path=MODEL_PROFILE,
            writing_config_path=WRITING_CONFIG,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(client.calls), 1)
        loaded = load_review_writing_b2_run(
            self.workspace,
            "writing-b2-success",
            package_loader=lambda workspace, run_id: self.package_source,
            claim_loader=lambda workspace, run_id: self.claim_source,
        )
        self.assertEqual(loaded.chapter["chapter_id"], result["chapter_id"])

    def test_conclusion_fails_before_api_call(self):
        package, claim = _sources(self.framework, section_index=3)
        client = FakeWritingClient({})
        runner = ReviewWritingB2Runner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
            package_loader=lambda workspace, run_id: package,
            claim_loader=lambda workspace, run_id: claim,
        )

        result = runner.run(
            package_run_id=package.run_id,
            run_id="writing-b2-conclusion",
            model_profile_path=MODEL_PROFILE,
            writing_config_path=WRITING_CONFIG,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(client.calls, [])
        self.assertEqual(
            result["failure"]["error_code"],
            "writing_b2.conclusion_not_ready",
        )

    def test_phase3a_assembly_requires_all_non_conclusion_sections(self):
        intro = _chapter_source(self.framework, section_index=1)
        body = _chapter_source(self.framework, section_index=2)

        draft = build_phase3a_draft([intro, body])

        self.assertFalse(draft["formal_review_published"])
        self.assertEqual(
            draft["conclusion"]["status"],
            "not_ready_requires_audited_core_claims",
        )
        with self.assertRaisesRegex(
            ReviewWritingB2AssemblyError,
            "section_set_invalid",
        ):
            build_phase3a_draft([body])

    def test_phase3a_assembly_publishes_and_loader_replays(self):
        sources = {
            source.run_id: source
            for source in (
                _chapter_source(self.framework, section_index=1),
                _chapter_source(self.framework, section_index=2),
            )
        }
        loader = lambda workspace, run_id: sources[run_id]
        result = ReviewWritingB2Assembler(
            self.workspace,
            chapter_loader=loader,
        ).build(
            chapter_run_ids=list(sources),
            run_id="phase3a-success",
        )

        self.assertEqual(result["status"], "completed")
        loaded = load_review_writing_b2_assembly(
            self.workspace,
            "phase3a-success",
            chapter_loader=loader,
        )
        self.assertEqual(loaded.draft["draft_id"], result["draft_id"])
        self.assertFalse(loaded.draft["formal_review_published"])

    def test_cli_exposes_independent_chapter_and_assembly_commands(self):
        chapter = build_parser().parse_args(
            [
                "llm-review-writing-b2",
                "--package-run-id",
                "package-2",
            ]
        )
        assembly = build_parser().parse_args(
            [
                "assemble-review-b2-phase3a",
                "--chapter-run-id",
                "chapter-1",
                "--chapter-run-id",
                "chapter-2",
            ]
        )
        self.assertEqual(chapter.package_run_id, "package-2")
        self.assertEqual(
            assembly.chapter_run_ids,
            ["chapter-1", "chapter-2"],
        )

    def _validate(self, draft: dict[str, Any]) -> dict[str, Any]:
        return validate_chapter_draft(
            draft,
            writing_input=self.writing_input,
            schema=self.schema,
        )

    def _runner(self, client: FakeWritingClient) -> ReviewWritingB2Runner:
        return ReviewWritingB2Runner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
            package_loader=lambda workspace, run_id: self.package_source,
            claim_loader=lambda workspace, run_id: self.claim_source,
        )


def _framework() -> dict[str, Any]:
    sections = []
    for index, section_type in enumerate(
        ("introduction", "body", "conclusion"),
        start=1,
    ):
        sections.append(
            {
                "section_index": index,
                "section_id": f"section-{index}",
                "title": f"章节{index}",
                "section_type": section_type,
                "question": "如何形成判断？",
                "purpose": "形成受约束综述。",
                "required_comparisons": [],
                "corpus_limitations": ["仅限测试语料。"],
                "target_chars": 200,
                "max_paragraphs": 2,
                "claim_budget": {
                    "core_max": 2,
                    "supporting_max": 2,
                    "cross_paper_synthesis_max": 1,
                },
                "high_risk_claim_types": ["cross_paper_synthesis"],
                "conclusion_strength_limit": "targeted_corpus_only",
                "paper_ids": ["paper-a", "paper-b"],
                "citation_keys": ["ref_a", "ref_b"],
            }
        )
    return {
        "framework_id": "framework-b2-id",
        "working_title": "测试综述",
        "central_question": "如何形成可靠综述？",
        "sections": sections,
    }


def _sources(
    framework: dict[str, Any],
    *,
    section_index: int,
) -> tuple[B2PackageV2RunSource, ClaimLedgerRunSource]:
    section = framework["sections"][section_index - 1]
    claims = _claims(section["section_id"])
    ledger = {
        "ledger_id": f"ledger-{section_index}",
        "framework_b2_id": framework["framework_id"],
        "section_id": section["section_id"],
        "approved_claims": claims,
    }
    package = {
        "package_id": f"package-{section_index}",
        "claim_planning_ready": True,
        "writing_ready": False,
        "source": {
            "claim_ledger_run_id": f"ledger-run-{section_index}",
            "claim_ledger_id": ledger["ledger_id"],
            "framework_b2_id": framework["framework_id"],
        },
        "section_task": copy.deepcopy(section),
        "understanding_projections": [],
        "selected_source_windows": [],
        "citation_metadata": [
            _citation("paper-a", "ref_a"),
            _citation("paper-b", "ref_b"),
        ],
        "approved_claim_ledger": ledger,
    }
    package_source = B2PackageV2RunSource(
        run_id=f"package-run-{section_index}",
        manifest_sha256="sha256:" + str(section_index) * 64,
        output_sha256="sha256:" + "a" * 64,
        package=package,
        token_budget={
            "model_profile_id": "deepseek-v4-pro-official",
            "planned_max_output_tokens": 32768,
        },
    )
    claim_source = ClaimLedgerRunSource(
        run_id=f"ledger-run-{section_index}",
        manifest_sha256="sha256:" + "b" * 64,
        output_sha256="sha256:" + "c" * 64,
        ledger=ledger,
        framework_b2=copy.deepcopy(framework),
        source_selection={},
    )
    return package_source, claim_source


def _claims(section_id: str) -> list[dict[str, Any]]:
    common = {
        "schema_version": "llm.claim_ledger_claim.v1",
        "section_id": section_id,
        "section_role": "core_argument",
        "supporting_papers": ["paper-a"],
        "supporting_source_windows": ["window-a"],
        "support_mode": "source_reported",
        "result_type": "quantitative_result",
        "validation_level": "simulation_validated",
        "allowed_strength": "source_reported_only",
        "audit_notes": "测试。",
        "prohibited_phrasings": [],
    }
    return [
        {
            **common,
            "claim_id": "claim_direct",
            "claim_type": "direct_fact",
            "planned_claim": "论文A报告了测试结果。",
            "importance": "core",
            "citation_keys": ["ref_a"],
            "required_qualifier": "",
        },
        {
            **common,
            "claim_id": "claim_cross",
            "claim_type": "cross_paper_synthesis",
            "planned_claim": "本次纳入的文献显示两类方法具有互补性。",
            "importance": "core",
            "supporting_papers": ["paper-a", "paper-b"],
            "supporting_source_windows": ["window-a", "window-b"],
            "citation_keys": ["ref_a", "ref_b"],
            "support_mode": "corpus_level_inference",
            "result_type": "mixed",
            "validation_level": "mixed",
            "allowed_strength": "targeted_corpus_only",
            "required_qualifier": "本次纳入的文献",
            "prohibited_phrasings": ["已形成共识"],
        },
        {
            **common,
            "claim_id": "claim_supporting",
            "claim_type": "direct_fact",
            "planned_claim": "论文B补充了背景条件。",
            "importance": "supporting",
            "supporting_papers": ["paper-b"],
            "supporting_source_windows": ["window-b"],
            "citation_keys": ["ref_b"],
            "required_qualifier": "",
        },
    ]


def _draft(writing_input: dict[str, Any]) -> dict[str, Any]:
    section = writing_input["section_task"]
    claims = {
        row["claim_id"]: row for row in writing_input["approved_claims"]
    }
    paragraphs = []
    for plan in writing_input["paragraph_plan"]:
        qualifiers = []
        for claim_id in plan["required_claim_ids"]:
            qualifier = claims[claim_id]["required_qualifier"]
            if qualifier and qualifier not in qualifiers:
                qualifiers.append(qualifier)
        paragraphs.append(
            {
                "paragraph_index": plan["paragraph_index"],
                "text": "".join(qualifiers) + "甲" * 80,
                "implemented_claim_ids": plan["required_claim_ids"],
                "citation_keys": plan["required_citation_keys"],
            }
        )
    return {
        "schema_version": CHAPTER_DRAFT_SCHEMA_VERSION,
        "section_id": section["section_id"],
        "section_index": section["section_index"],
        "title": section["title"],
        "paragraphs": paragraphs,
    }


def _chapter_source(
    framework: dict[str, Any],
    *,
    section_index: int,
) -> ReviewWritingB2RunSource:
    package, claim = _sources(framework, section_index=section_index)
    writing_input = derive_writing_input(
        package.package,
        framework,
        package_run_id=package.run_id,
        package_manifest_sha256=package.manifest_sha256,
        max_paragraph_chars=4000,
    )
    chapter = validate_chapter_draft(
        _draft(writing_input),
        writing_input=writing_input,
        schema=build_chapter_draft_schema(writing_input),
    )
    return ReviewWritingB2RunSource(
        run_id=f"chapter-run-{section_index}",
        manifest_sha256="sha256:" + "d" * 64,
        output_sha256="sha256:" + "e" * 64,
        writing_input=writing_input,
        chapter=chapter,
        package_source=package,
    )


def _citation(paper_id: str, key: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "citation_key": key,
        "paper_title": paper_id,
        "authors": ["作者"],
        "year": 2025,
        "reference_type": "article",
        "journal": "测试学报",
        "institution": None,
        "degree": None,
        "doi": None,
        "bibliography_status": "complete",
    }


if __name__ == "__main__":
    unittest.main()
