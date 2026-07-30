from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import build_parser, main as cli_main
from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.review_framework import (
    REFERENCE_CATALOG_RUN_SCHEMA_VERSION,
    RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION,
    ReviewFrameworkRunner,
    ReviewFrameworkSnapshot,
    _load_landscape_source,
    _load_reference_catalog_source,
    build_review_framework_prompt,
)
from shipping_pipeline.review_framework_contracts import (
    build_review_framework_draft_schema,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
)
TOPIC = "三峡枢纽通航能力提升"
REVIEW_GOAL = "比较提升方法、验证水平和适用边界"


class FakeTokenCounter:
    def __init__(self, prompt_tokens: int | None = None) -> None:
        self.prompt_tokens = prompt_tokens

    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        prompt_tokens = self.prompt_tokens
        if prompt_tokens is None:
            prompt_tokens = max(
                1,
                len(json.dumps(messages, ensure_ascii=False)) // 4,
            )
        return TokenCount(
            prompt_tokens=prompt_tokens,
            encoded_prompt_sha256=f"sha256:{prompt_tokens:064x}",
        )


class FakeFrameworkClient:
    provider = "openai-compatible"

    def __init__(self, *, omit_second_dimension: bool = False) -> None:
        self.model = "deepseek-ai/DeepSeek-V4-Pro"
        self.omit_second_dimension = omit_second_dimension
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        task: str,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> ProviderResult:
        if task != "review_framework":
            raise AssertionError(task)
        self.calls.append(
            {"request": request_payload, "context": context}
        )
        body_dimensions = [1] if self.omit_second_dimension else [1, 2]
        content = {
            "schema_version": "llm.review_framework_draft.v1",
            "topic": TOPIC,
            "review_goal": REVIEW_GOAL,
            "working_title": "三峡枢纽通航能力提升方法综述",
            "central_question": "不同提升方法如何作用且证据边界如何？",
            "sections": [
                {
                    "section_index": 1,
                    "title": "引言",
                    "section_type": "introduction",
                    "question": "为何需要比较提升方法？",
                    "purpose": "界定问题和语料范围。",
                    "dimension_indexes": [1, 2],
                    "required_comparisons": [],
                    "controversy_ids": [],
                    "corpus_gap_indexes": [],
                    "corpus_limitations": ["仅覆盖本次显式语料。"],
                },
                {
                    "section_index": 2,
                    "title": "方法与证据比较",
                    "section_type": "body",
                    "question": "两类方法的机制和验证水平有何差异？",
                    "purpose": "完成跨论文方法比较。",
                    "dimension_indexes": body_dimensions,
                    "required_comparisons": [
                        "比较两类方法的作用机制和验证水平。"
                    ],
                    "controversy_ids": ["disagreement-1"],
                    "corpus_gap_indexes": [1],
                    "corpus_limitations": ["缺少长期工程验证。"],
                },
                {
                    "section_index": 3,
                    "title": "结论",
                    "section_type": "conclusion",
                    "question": "现有证据支持哪些判断？",
                    "purpose": "总结方法边界和后续问题。",
                    "dimension_indexes": [1, 2],
                    "required_comparisons": [],
                    "controversy_ids": [],
                    "corpus_gap_indexes": [1],
                    "corpus_limitations": ["结论限于本次语料。"],
                },
            ],
        }
        return _provider_result(
            content,
            model=self.model,
            prompt_tokens=int(context["planned_input_tokens"]),
        )


def _provider_result(
    content: dict[str, Any],
    *,
    model: str,
    prompt_tokens: int,
) -> ProviderResult:
    response = {
        "id": f"response-{uuid.uuid4().hex[:8]}",
        "model": model,
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
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 256,
            "total_tokens": prompt_tokens + 256,
        },
    }
    return ProviderResult(
        raw_body=json.dumps(response, ensure_ascii=False).encode("utf-8"),
        parsed_response=response,
        response_id=str(response["id"]),
        response_model=model,
        system_fingerprint=None,
        finish_reason="stop",
        reasoning_content_chars=0,
        usage=dict(response["usage"]),
    )


def make_snapshot() -> ReviewFrameworkSnapshot:
    landscape = {
        "schema_version": "llm.research_landscape.v1",
        "landscape_id": "landscape-test",
        "topic": TOPIC,
        "review_goal": REVIEW_GOAL,
        "corpus_scope": "targeted_sample",
        "central_problem": "如何提升通航能力？",
        "dimensions": [
            {
                "dimension_index": 1,
                "dimension_id": "dimension-1",
                "title": "调度优化",
                "question": "如何优化调度？",
                "paper_ids": ["paper-a"],
                "contribution_ids": ["contribution-a"],
            },
            {
                "dimension_index": 2,
                "dimension_id": "dimension-2",
                "title": "工程扩能",
                "question": "如何实施工程扩能？",
                "paper_ids": ["paper-b"],
                "contribution_ids": ["contribution-b"],
            },
        ],
        "relations": [],
        "research_evolution": [],
        "disagreements": [
            {
                "disagreement_id": "disagreement-1",
                "question": "优先采用哪类方法？",
                "positions": [
                    {
                        "paper_ids": ["paper-a"],
                        "statement": "优先调度。",
                        "contribution_ids": ["contribution-a"],
                    },
                    {
                        "paper_ids": ["paper-b"],
                        "statement": "优先工程。",
                        "contribution_ids": ["contribution-b"],
                    },
                ],
            }
        ],
        "corpus_gaps": [
            {
                "corpus_gap_id": "gap-1",
                "question": "组合效果如何？",
                "why_it_matters": "影响方案选择。",
                "related_dimension_indexes": [1, 2],
            }
        ],
        "unmapped_papers": [],
    }
    references = {
        "paper-a": {
            "paper_id": "paper-a",
            "reference_id": "ref-a",
            "citation_key": "ref-a",
            "title": "论文A",
            "status": "complete",
            "missing_fields": [],
        },
        "paper-b": {
            "paper_id": "paper-b",
            "reference_id": "ref-b",
            "citation_key": "ref-b",
            "title": "论文B",
            "status": "partial",
            "missing_fields": ["pages"],
        },
    }
    return ReviewFrameworkSnapshot(
        topic=TOPIC,
        review_goal=REVIEW_GOAL,
        corpus_scope="targeted_sample",
        landscape_run_id="landscape-run",
        landscape_manifest_sha256="sha256:landscape-manifest",
        landscape_output_sha256="sha256:landscape-output",
        landscape=landscape,
        reference_catalog_run_id="reference-run",
        reference_manifest_sha256="sha256:reference-manifest",
        reference_catalog_sha256="sha256:reference-output",
        reference_by_paper=references,
        input_sha256="sha256:framework-input",
    )


class ReviewFrameworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = (
            TEST_TMP_ROOT / f"review_framework_{uuid.uuid4().hex}"
        )
        self.workspace.mkdir(parents=True)
        self.snapshot = make_snapshot()

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def loader(self, *args: Any, **kwargs: Any) -> ReviewFrameworkSnapshot:
        return self.snapshot

    def test_runner_derives_sources_and_publishes_review(self):
        client = FakeFrameworkClient()
        result = ReviewFrameworkRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
            snapshot_loader=self.loader,
        ).run(
            landscape_run_id="landscape-run",
            reference_catalog_run_id="reference-run",
            run_id="framework-one",
            model_profile_path=MODEL_PROFILE,
        )

        self.assertEqual(result["status"], "completed")
        framework = json.loads(
            Path(result["output_path"]).read_text(encoding="utf-8")
        )
        body = framework["sections"][1]
        self.assertEqual(body["paper_ids"], ["paper-a", "paper-b"])
        self.assertEqual(
            body["contribution_ids"],
            ["contribution-a", "contribution-b"],
        )
        self.assertEqual(body["citation_keys"], ["ref-a", "ref-b"])
        self.assertEqual(len(framework["bibliography_issues"]), 1)
        self.assertEqual(framework["unused_papers"], [])
        report = Path(result["review_path"]).read_text(encoding="utf-8")
        self.assertIn("paper-a", report)
        self.assertIn("ref-b", report)
        prompt = client.calls[0]["request"]["messages"][1]["content"]
        self.assertIn("Research Landscape", prompt)
        self.assertIn("题录状态", prompt)
        self.assertNotIn("完整Markdown", prompt)

    def test_missing_body_dimension_fails_without_publishing(self):
        result = ReviewFrameworkRunner(
            self.workspace,
            analysis_client=FakeFrameworkClient(
                omit_second_dimension=True
            ),
            token_counter=FakeTokenCounter(),
            snapshot_loader=self.loader,
        ).run(
            landscape_run_id="landscape-run",
            reference_catalog_run_id="reference-run",
            run_id="framework-missing-dimension",
            model_profile_path=MODEL_PROFILE,
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(
            (
                Path(result["run_dir"])
                / "output"
                / "review_framework.json"
            ).exists()
        )
        self.assertIn("dimension", result["failure"]["error_message"])

    def test_context_overflow_fails_before_provider_call(self):
        client = FakeFrameworkClient()
        result = ReviewFrameworkRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(prompt_tokens=999_999_999),
            snapshot_loader=self.loader,
        ).run(
            landscape_run_id="landscape-run",
            reference_catalog_run_id="reference-run",
            run_id="framework-overflow",
            model_profile_path=MODEL_PROFILE,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(client.calls, [])
        self.assertEqual(
            result["failure"]["error_code"],
            "input.context_budget_exceeded",
        )

    def test_prompt_contains_only_framework_inputs(self):
        schema = build_review_framework_draft_schema(
            topic=TOPIC,
            review_goal=REVIEW_GOAL,
            dimension_indexes=[1, 2],
            controversy_ids=["disagreement-1"],
            corpus_gap_indexes=[1],
        )
        prompt = build_review_framework_prompt(
            self.snapshot,
            schema=schema,
        )

        self.assertIn('"Research Landscape"', prompt)
        self.assertIn('"题录状态"', prompt)
        self.assertNotIn("materials.jsonl", prompt)
        self.assertNotIn("document.md", prompt)
        self.assertIn("点名的每篇论文都属于本节所选dimension", prompt)

    def test_cli_requires_explicit_source_runs(self):
        args = build_parser().parse_args(
            [
                "llm-review-framework",
                "--landscape-run-id",
                "landscape-run",
                "--reference-catalog-run-id",
                "reference-run",
            ]
        )

        self.assertEqual(args.command, "llm-review-framework")
        self.assertEqual(args.landscape_run_id, "landscape-run")
        self.assertEqual(args.reference_catalog_run_id, "reference-run")

    def test_cli_returns_zero_only_for_completed(self):
        argv = [
            "llm-review-framework",
            "--landscape-run-id",
            "landscape-run",
            "--reference-catalog-run-id",
            "reference-run",
        ]
        with patch("main.ReviewFrameworkRunner") as runner:
            runner.return_value.run.return_value = {
                "status": "completed",
                "run_id": "framework-one",
            }
            self.assertEqual(cli_main(argv), 0)
            runner.return_value.run.return_value = {
                "status": "failed",
                "run_id": "framework-two",
            }
            self.assertEqual(cli_main(argv), 1)

    def test_landscape_directory_and_manifest_identity_must_match(self):
        run_dir = (
            self.workspace
            / "_research_landscapes"
            / "runs"
            / "requested-landscape"
        )
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION,
                    "run_id": "other-landscape",
                    "status": "completed",
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            Exception,
            "manifest.run_id不一致",
        ):
            _load_landscape_source(
                self.workspace,
                "requested-landscape",
            )

    def test_reference_directory_and_manifest_identity_must_match(self):
        run_dir = (
            self.workspace
            / "_reference_catalogs"
            / "runs"
            / "requested-reference"
        )
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": REFERENCE_CATALOG_RUN_SCHEMA_VERSION,
                    "run_id": "other-reference",
                    "status": "completed",
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            Exception,
            "manifest.run_id不一致",
        ):
            _load_reference_catalog_source(
                self.workspace,
                "requested-reference",
                expected_topic=TOPIC,
                expected_paper_ids={"paper-a"},
            )


if __name__ == "__main__":
    unittest.main()
