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

from shipping_pipeline.llm_analysis import AnalysisInputError
from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.research_understanding import (
    PaperUnderstandingRunner,
    create_paper_understanding_snapshot,
)
from main import build_parser, main as cli_main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
)
UNDERSTANDING_CONFIG = (
    PROJECT_ROOT / "config" / "research-understanding-default.json"
)


def make_workspace() -> Path:
    path = TEST_TMP_ROOT / f"research_understanding_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def make_material(index: int, generation_id: str = "generation-one") -> dict[str, Any]:
    text = f"材料{index}说明三峡船闸运行能力与调度方法。"
    return {
        "schema_version": "material.v2",
        "material_id": f"paper-one:card-{index}",
        "material_type": "evidence_card",
        "paper_id": "paper-one",
        "paper_title": "论文一",
        "source_path": "normalized/document.md",
        "clean_title": f"第{index}节",
        "raw_title": f"第{index}节",
        "order": index,
        "extract": text,
        "source_span": {
            "path": "normalized/document.md",
            "start_line": index * 10,
            "end_line": index * 10 + 1,
        },
        "source_spans": [],
        "heading_path": [f"第{index}节"],
        "content_kind": "prose",
        "confidence_flags": [],
        "quality_flags": [],
        "source_fingerprint": f"sha256:fingerprint-{index}",
        "generation_id": generation_id,
    }


def write_isolated_paper(
    workspace: Path,
    materials: list[dict[str, Any]],
    *,
    workspace_paper_id: str = "paper-one-workspace",
    document_text: str = "# 论文一\n\n完整Markdown正文。\n",
) -> None:
    paper_dir = workspace / workspace_paper_id
    generation_id = str(materials[0]["generation_id"])
    materials_rel = Path("generations") / generation_id / "materials.jsonl"
    materials_path = paper_dir / "materials" / materials_rel
    materials_path.parent.mkdir(parents=True)
    materials_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in materials),
        encoding="utf-8",
    )
    (paper_dir / "materials" / "current.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "generation_id": generation_id,
                "materials_path": str(materials_rel).replace("\\", "/"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (paper_dir / "run.json").write_text(
        json.dumps(
            {
                "paper_id": "paper-one",
                "status": "completed",
                "generation_id": generation_id,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    normalized = paper_dir / "normalized"
    normalized.mkdir(parents=True)
    (normalized / "document.md").write_text(document_text, encoding="utf-8")


def write_topic_review(
    workspace: Path,
    materials: list[dict[str, Any]],
    *,
    run_id: str = "topic-review-one",
    paper_id: str = "paper-one",
    topic: str = "三峡枢纽通航能力提升",
    generation_id: str = "generation-one",
    citation_material_id: str = "paper-one:card-1",
) -> None:
    run_dir = workspace / "_topic_reviews" / "runs" / run_id
    (run_dir / "evidence").mkdir(parents=True)
    (run_dir / "input").mkdir(parents=True)
    evidence = {
        "claim": "材料说明三峡船闸运行能力。",
        "evidence_type": "result",
        "citations": [
            {
                "material_id": citation_material_id,
                "quote_id": "quote-one",
                "quote": "三峡船闸运行能力",
                "source_ref": {
                    "path": "normalized/document.md",
                    "start_line": 10,
                    "end_line": 11,
                },
                "source_spans": [],
                "card_title": "第1节",
                "confidence_flags": [],
                "quality_flags": [],
            }
        ],
        "relevance": "直接相关。",
        "confidence": "high",
        "caveats": [],
        "evidence_anchor_id": "anchor-one",
        "evidence_unit_id": "evidence-one",
        "request_id": "request-one",
        "batch_id": "batch-one",
    }
    (run_dir / "evidence" / "evidence_units.jsonl").write_text(
        json.dumps(evidence, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "input" / "materials.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in materials),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "llm.topic_review_run.v2",
                "run_id": run_id,
                "status": "completed",
                "paper_id": paper_id,
                "paper_title": "论文一",
                "topic": topic,
                "generation_id": generation_id,
                "input_sha256": "sha256:fixture",
                "evidence_unit_count": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class FakeTokenCounter:
    def __init__(self, prompt_tokens: int | None = None) -> None:
        self.prompt_tokens = prompt_tokens

    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        prompt_tokens = self.prompt_tokens
        if prompt_tokens is None:
            prompt_tokens = max(1, len(json.dumps(messages, ensure_ascii=False)) // 4)
        return TokenCount(
            prompt_tokens=prompt_tokens,
            encoded_prompt_sha256=f"sha256:{prompt_tokens:064x}",
        )


class FakeUnderstandingClient:
    provider = "openai-compatible"

    def __init__(self) -> None:
        self.model = "deepseek-ai/DeepSeek-V4-Pro"
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        task: str,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> ProviderResult:
        self.calls.append(
            {"task": task, "request": request_payload, "context": context}
        )
        payload = json.loads(request_payload["messages"][1]["content"])
        cards = payload["全部Card"]
        evidence = payload["已有Evidence"]
        content = {
            "schema_version": "llm.paper_understanding.v1",
            "paper_id": "paper-one",
            "paper_title": "论文一",
            "topic": "三峡枢纽通航能力提升",
            "paper_relevance": "core",
            "research_questions": [
                {
                    "question": "如何提升三峡船闸运行能力？",
                    "problem_category": "运行组织优化",
                    "material_ids": [cards[0]["material_id"]],
                }
            ],
            "study_context": {
                "study_object": "三峡船闸",
                "data_sources": ["operational_records"],
                "time_scope": "",
                "geographic_scope": "三峡坝区",
                "material_ids": [cards[0]["material_id"]],
            },
            "methods": [
                {
                    "method_category": "simulation",
                    "method_name": "运行仿真",
                    "description": "比较调度方案。",
                    "material_ids": [cards[1]["material_id"]],
                }
            ],
            "contributions": [
                {
                    "statement": "给出船闸运行能力提升方法。",
                    "result_type": "simulation_result",
                    "validation_level": "simulation",
                    "evidence_strength": "moderate",
                    "strength_rationale": "结果来自仿真，尚无工程应用验证。",
                    "material_ids": [cards[1]["material_id"]],
                    "evidence_unit_ids": (
                        [evidence[0]["evidence_unit_id"]] if evidence else []
                    ),
                }
            ],
            "limitations": [
                {
                    "statement": "验证限于仿真场景。",
                    "basis": "reviewer_inferred",
                    "material_ids": [cards[1]["material_id"]],
                }
            ],
            "review_roles": [
                {
                    "role": "method_comparison",
                    "reason": "可与其他调度方法比较。",
                    "contribution_indexes": [1],
                }
            ],
            "unresolved_questions": [
                {
                    "question": "工程条件下效果是否稳定？",
                    "basis": "missing_validation",
                }
            ],
            "keywords": ["船闸调度", "运行仿真"],
        }
        prompt_tokens = int(context["planned_input_tokens"])
        response = {
            "id": "response-understanding",
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
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 256,
                "total_tokens": prompt_tokens + 256,
            },
        }
        return ProviderResult(
            raw_body=json.dumps(response, ensure_ascii=False).encode("utf-8"),
            parsed_response=response,
            response_id="response-understanding",
            response_model=self.model,
            system_fingerprint=None,
            finish_reason="stop",
            reasoning_content_chars=0,
            usage=dict(response["usage"]),
        )


class ResearchUnderstandingTests(unittest.TestCase):
    def test_cli_parser_requires_explicit_workspace_paper_id(self):
        args = build_parser().parse_args(
            [
                "llm-paper-understanding",
                "--paper-id",
                "paper-one",
                "--workspace-paper-id",
                "paper-one-workspace",
                "--topic",
                "三峡枢纽通航能力提升",
            ]
        )

        self.assertEqual(args.command, "llm-paper-understanding")
        self.assertEqual(args.workspace_paper_id, "paper-one-workspace")

    def test_cli_returns_zero_for_completed_and_one_for_failed(self):
        base_args = [
            "llm-paper-understanding",
            "--paper-id",
            "paper-one",
            "--workspace-paper-id",
            "paper-one-workspace",
            "--topic",
            "三峡枢纽通航能力提升",
        ]
        with patch("main.PaperUnderstandingRunner") as runner:
            runner.return_value.run.return_value = {
                "status": "completed",
                "run_id": "one",
            }
            self.assertEqual(cli_main(base_args), 0)
            runner.return_value.run.return_value = {
                "status": "failed",
                "run_id": "two",
            }
            self.assertEqual(cli_main(base_args), 1)

    def setUp(self) -> None:
        self.workspace = make_workspace()
        self.materials = [make_material(1), make_material(2)]
        write_isolated_paper(self.workspace, self.materials)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_snapshot_includes_complete_markdown_cards_and_no_guessed_evidence(self):
        snapshot = create_paper_understanding_snapshot(
            self.workspace,
            paper_id="paper-one",
            workspace_paper_id="paper-one-workspace",
            topic="三峡枢纽通航能力提升",
        )

        self.assertEqual(snapshot.document_text, "# 论文一\n\n完整Markdown正文。\n")
        self.assertEqual(len(snapshot.materials), 2)
        self.assertEqual(len(snapshot.projected_cards), 2)
        self.assertEqual(snapshot.evidence_units, ())
        self.assertIsNone(snapshot.source_topic_review_run_id)
        self.assertTrue(snapshot.document_sha256.startswith("sha256:"))
        self.assertTrue(snapshot.input_sha256.startswith("sha256:"))

    def test_snapshot_rejects_missing_document(self):
        (
            self.workspace
            / "paper-one-workspace"
            / "normalized"
            / "document.md"
        ).unlink()

        with self.assertRaisesRegex(AnalysisInputError, "document.md"):
            create_paper_understanding_snapshot(
                self.workspace,
                paper_id="paper-one",
                workspace_paper_id="paper-one-workspace",
                topic="三峡枢纽通航能力提升",
            )

    def test_snapshot_rejects_card_generation_mismatch(self):
        current = (
            self.workspace
            / "paper-one-workspace"
            / "materials"
            / "current.json"
        )
        payload = json.loads(current.read_text(encoding="utf-8"))
        payload["generation_id"] = "generation-two"
        current.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(AnalysisInputError, "generation"):
            create_paper_understanding_snapshot(
                self.workspace,
                paper_id="paper-one",
                workspace_paper_id="paper-one-workspace",
                topic="三峡枢纽通航能力提升",
            )

    def test_snapshot_rejects_topic_review_identity_mismatch(self):
        write_topic_review(
            self.workspace,
            self.materials,
            paper_id="paper-two",
        )

        with self.assertRaisesRegex(AnalysisInputError, "论文身份"):
            create_paper_understanding_snapshot(
                self.workspace,
                paper_id="paper-one",
                workspace_paper_id="paper-one-workspace",
                topic="三峡枢纽通航能力提升",
                source_topic_review_run_id="topic-review-one",
            )

    def test_snapshot_rejects_evidence_citing_unknown_card(self):
        write_topic_review(
            self.workspace,
            self.materials,
            citation_material_id="paper-one:missing-card",
        )

        with self.assertRaisesRegex(AnalysisInputError, "未知Card"):
            create_paper_understanding_snapshot(
                self.workspace,
                paper_id="paper-one",
                workspace_paper_id="paper-one-workspace",
                topic="三峡枢纽通航能力提升",
                source_topic_review_run_id="topic-review-one",
            )

    def test_runner_sends_complete_inputs_and_publishes_audited_output(self):
        write_topic_review(self.workspace, self.materials)
        client = FakeUnderstandingClient()

        result = PaperUnderstandingRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
        ).run(
            paper_id="paper-one",
            workspace_paper_id="paper-one-workspace",
            topic="三峡枢纽通航能力提升",
            source_topic_review_run_id="topic-review-one",
            run_id="understanding-one",
            model_profile_path=MODEL_PROFILE,
            understanding_config_path=UNDERSTANDING_CONFIG,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(client.calls), 1)
        prompt = json.loads(client.calls[0]["request"]["messages"][1]["content"])
        self.assertEqual(prompt["完整Markdown"], "# 论文一\n\n完整Markdown正文。\n")
        self.assertEqual(
            [row["material_id"] for row in prompt["全部Card"]],
            ["paper-one:card-1", "paper-one:card-2"],
        )
        self.assertEqual(
            [row["evidence_unit_id"] for row in prompt["已有Evidence"]],
            ["evidence-one"],
        )
        run_dir = Path(result["run_dir"])
        self.assertTrue((run_dir / "input" / "document.md").exists())
        self.assertTrue((run_dir / "output" / "paper_understanding.json").exists())
        self.assertTrue((run_dir / "audit" / "source_bindings.jsonl").exists())
        self.assertTrue((run_dir / "review" / "paper_understanding.md").exists())

    def test_runner_context_overflow_fails_without_formal_output(self):
        client = FakeUnderstandingClient()
        result = PaperUnderstandingRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(prompt_tokens=999_999_999),
        ).run(
            paper_id="paper-one",
            workspace_paper_id="paper-one-workspace",
            topic="三峡枢纽通航能力提升",
            run_id="understanding-overflow",
            model_profile_path=MODEL_PROFILE,
            understanding_config_path=UNDERSTANDING_CONFIG,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(client.calls, [])
        run_dir = Path(result["run_dir"])
        self.assertFalse((run_dir / "output" / "paper_understanding.json").exists())
        failure = json.loads(
            (run_dir / "audit" / "failures.jsonl").read_text(encoding="utf-8")
        )
        self.assertEqual(failure["error_code"], "input.context_budget_exceeded")

    def test_runner_rejects_existing_immutable_run_id(self):
        runner = PaperUnderstandingRunner(
            self.workspace,
            analysis_client=FakeUnderstandingClient(),
            token_counter=FakeTokenCounter(),
        )
        kwargs = {
            "paper_id": "paper-one",
            "workspace_paper_id": "paper-one-workspace",
            "topic": "三峡枢纽通航能力提升",
            "run_id": "same-run",
            "model_profile_path": MODEL_PROFILE,
            "understanding_config_path": UNDERSTANDING_CONFIG,
        }
        first = runner.run(**kwargs)
        self.assertEqual(first["status"], "completed")

        with self.assertRaisesRegex(AnalysisInputError, "不能覆盖"):
            runner.run(**kwargs)


if __name__ == "__main__":
    unittest.main()
