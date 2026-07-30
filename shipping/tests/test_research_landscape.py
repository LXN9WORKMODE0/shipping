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

from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.research_landscape import (
    RESEARCH_LANDSCAPE_SYSTEM_PROMPT,
    ResearchLandscapeRunner,
    build_research_landscape_prompt,
    create_research_landscape_snapshot,
)
from shipping_pipeline.research_landscape_contracts import (
    build_research_landscape_schema,
    load_research_landscape_config,
)
from shipping_pipeline.research_understanding import PaperUnderstandingRunner
from main import build_parser, main as cli_main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
)
UNDERSTANDING_CONFIG = (
    PROJECT_ROOT / "config" / "research-understanding-default.json"
)
LANDSCAPE_CONFIG = (
    PROJECT_ROOT / "config" / "research-landscape-default.json"
)
TOPIC = "三峡枢纽通航能力提升"


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


class FakeUnderstandingClient:
    provider = "openai-compatible"

    def __init__(self) -> None:
        self.model = "deepseek-ai/DeepSeek-V4-Pro"

    def complete(
        self,
        task: str,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> ProviderResult:
        if task != "paper_understanding":
            raise AssertionError(task)
        prompt = json.loads(request_payload["messages"][1]["content"])
        paper = prompt["论文"]
        card = prompt["全部Card"][0]
        content = {
            "schema_version": "llm.paper_understanding.v1",
            "paper_id": paper["paper_id"],
            "paper_title": paper["paper_title"],
            "topic": TOPIC,
            "paper_relevance": "core",
            "research_questions": [
                {
                    "question": f"{paper['paper_title']}研究什么提升方法？",
                    "problem_category": "运行优化",
                    "material_ids": [card["material_id"]],
                }
            ],
            "study_context": {
                "study_object": "三峡船闸运行",
                "data_sources": ["simulation_output"],
                "time_scope": "",
                "geographic_scope": "三峡坝区",
                "material_ids": [card["material_id"]],
            },
            "methods": [
                {
                    "method_category": "simulation",
                    "method_name": "运行仿真",
                    "description": "比较不同运行方法。",
                    "material_ids": [card["material_id"]],
                }
            ],
            "contributions": [
                {
                    "statement": f"{paper['paper_title']}给出一种仿真提升方法。",
                    "result_type": "simulation_result",
                    "validation_level": "simulation",
                    "evidence_strength": "moderate",
                    "strength_rationale": "仅在仿真场景中验证。",
                    "material_ids": [card["material_id"]],
                    "evidence_unit_ids": [],
                }
            ],
            "limitations": [
                {
                    "statement": "缺少工程应用验证。",
                    "basis": "reviewer_inferred",
                    "material_ids": [card["material_id"]],
                }
            ],
            "review_roles": [
                {
                    "role": "method_comparison",
                    "reason": "可比较仿真方法。",
                    "contribution_indexes": [1],
                }
            ],
            "unresolved_questions": [
                {
                    "question": "工程应用效果如何？",
                    "basis": "missing_validation",
                }
            ],
            "keywords": ["船闸", "仿真"],
        }
        return _provider_result(
            content,
            model=self.model,
            prompt_tokens=int(context["planned_input_tokens"]),
        )


class FakeLandscapeClient:
    provider = "openai-compatible"

    def __init__(self, *, include_field_gap: bool = False) -> None:
        self.model = "deepseek-ai/DeepSeek-V4-Pro"
        self.include_field_gap = include_field_gap
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        task: str,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> ProviderResult:
        if task != "research_landscape":
            raise AssertionError(task)
        self.calls.append(
            {"request": request_payload, "context": context}
        )
        prompt = json.loads(request_payload["messages"][1]["content"])
        papers = prompt["论文认知"]
        paper_ids = [row["paper_id"] for row in papers]
        contribution_ids = [
            row["contributions"][0]["contribution_id"] for row in papers
        ]
        content: dict[str, Any] = {
            "schema_version": "llm.research_landscape.v1",
            "topic": TOPIC,
            "review_goal": "比较不同方法的验证水平和适用边界",
            "corpus_scope": "targeted_sample",
            "central_problem": "不同提升方法如何作用，其验证水平如何？",
            "dimensions": [
                {
                    "dimension_index": 1,
                    "title": "运行优化方法",
                    "question": "如何通过运行优化提升通航能力？",
                    "paper_ids": paper_ids,
                    "contribution_ids": contribution_ids,
                }
            ],
            "relations": [
                {
                    "relation_type": "complements",
                    "from_paper_id": paper_ids[0],
                    "to_paper_id": paper_ids[1],
                    "statement": "两篇论文从不同角度提供仿真方法。",
                    "supporting_contribution_ids": contribution_ids,
                }
            ],
            "research_evolution": [
                {
                    "period": "2000-2020",
                    "statement": "本次语料显示仿真方法逐步用于运行优化。",
                    "paper_ids": paper_ids,
                    "contribution_ids": contribution_ids,
                }
            ],
            "disagreements": [],
            "corpus_gaps": [
                {
                    "question": "这些方法的工程应用效果如何？",
                    "why_it_matters": "决定方法能否推广。",
                    "related_dimension_indexes": [1],
                }
            ],
            "unmapped_papers": [],
        }
        if self.include_field_gap:
            content["field_gap_candidates"] = [
                {
                    "question": "领域是否缺少工程验证？",
                    "why_it_matters": "影响推广。",
                    "supporting_corpus_gap_indexes": [1],
                    "caveat": "需系统检索确认。",
                }
            ]
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


def make_material(paper_id: str) -> dict[str, Any]:
    text = f"{paper_id}说明一种三峡船闸运行优化方法。"
    return {
        "schema_version": "material.v2",
        "material_id": f"{paper_id}:card-1",
        "material_type": "evidence_card",
        "paper_id": paper_id,
        "paper_title": paper_id,
        "source_path": "normalized/document.md",
        "clean_title": "方法",
        "raw_title": "方法",
        "order": 1,
        "extract": text,
        "source_span": {
            "path": "normalized/document.md",
            "start_line": 3,
            "end_line": 4,
        },
        "source_spans": [],
        "heading_path": ["方法"],
        "content_kind": "prose",
        "confidence_flags": [],
        "quality_flags": [],
        "source_fingerprint": f"sha256:{paper_id}-fingerprint",
        "generation_id": f"{paper_id}-generation",
    }


def write_paper(workspace: Path, paper_id: str) -> str:
    workspace_id = f"workspace-{paper_id}"
    paper_dir = workspace / workspace_id
    material = make_material(paper_id)
    generation_id = str(material["generation_id"])
    relative = Path("generations") / generation_id / "materials.jsonl"
    materials_path = paper_dir / "materials" / relative
    materials_path.parent.mkdir(parents=True)
    materials_path.write_text(
        json.dumps(material, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (paper_dir / "materials" / "current.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "generation_id": generation_id,
                "materials_path": str(relative).replace("\\", "/"),
            }
        ),
        encoding="utf-8",
    )
    (paper_dir / "run.json").write_text(
        json.dumps(
            {
                "paper_id": paper_id,
                "status": "completed",
                "generation_id": generation_id,
            }
        ),
        encoding="utf-8",
    )
    normalized = paper_dir / "normalized"
    normalized.mkdir(parents=True)
    (normalized / "document.md").write_text(
        f"# {paper_id}\n\n{material['extract']}\n",
        encoding="utf-8",
    )
    return workspace_id


class ResearchLandscapeTests(unittest.TestCase):
    def test_prompt_rejects_silence_as_contrast_and_causal_evolution(self):
        self.assertIn(
            "未提及某方案、未表态或证据较少，不构成contrasts",
            RESEARCH_LANDSCAPE_SYSTEM_PROMPT,
        )
        self.assertIn(
            "禁止使用“奠定基础”",
            RESEARCH_LANDSCAPE_SYSTEM_PROMPT,
        )
        self.assertIn(
            "不能只列论文而不提供该论文贡献",
            RESEARCH_LANDSCAPE_SYSTEM_PROMPT,
        )

    def test_cli_parser_accepts_explicit_collection(self):
        args = build_parser().parse_args(
            [
                "llm-research-landscape",
                "--collection",
                "collection.json",
            ]
        )

        self.assertEqual(args.command, "llm-research-landscape")
        self.assertEqual(args.collection, Path("collection.json"))

    def test_cli_returns_zero_only_for_completed(self):
        argv = [
            "llm-research-landscape",
            "--collection",
            "collection.json",
        ]
        with patch("main.ResearchLandscapeRunner") as runner:
            runner.return_value.run.return_value = {
                "status": "completed",
                "run_id": "landscape-one",
            }
            self.assertEqual(cli_main(argv), 0)
            runner.return_value.run.return_value = {
                "status": "failed",
                "run_id": "landscape-two",
            }
            self.assertEqual(cli_main(argv), 1)

    def setUp(self) -> None:
        self.workspace = (
            TEST_TMP_ROOT / f"research_landscape_{uuid.uuid4().hex}"
        )
        self.workspace.mkdir(parents=True)
        self.source_runs = []
        for paper_id in ("paper-a", "paper-b"):
            workspace_id = write_paper(self.workspace, paper_id)
            result = PaperUnderstandingRunner(
                self.workspace,
                analysis_client=FakeUnderstandingClient(),
                token_counter=FakeTokenCounter(),
            ).run(
                paper_id=paper_id,
                workspace_paper_id=workspace_id,
                topic=TOPIC,
                run_id=f"understanding-{paper_id}",
                model_profile_path=MODEL_PROFILE,
                understanding_config_path=UNDERSTANDING_CONFIG,
            )
            self.assertEqual(result["status"], "completed")
            self.source_runs.append(str(result["run_id"]))
        self.collection_path = self.workspace / "collection.json"
        self.write_collection(self.source_runs)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def write_collection(self, run_ids: list[str]) -> None:
        self.collection_path.write_text(
            json.dumps(
                {
                    "schema_version": "llm.research_landscape_collection.v1",
                    "topic": TOPIC,
                    "review_goal": "比较不同方法的验证水平和适用边界",
                    "corpus_scope": "targeted_sample",
                    "source_understanding_run_ids": run_ids,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_snapshot_revalidates_explicit_sources(self):
        snapshot = create_research_landscape_snapshot(
            self.workspace,
            collection_path=self.collection_path,
        )

        self.assertEqual(len(snapshot.sources), 2)
        self.assertEqual(
            [source.paper_id for source in snapshot.sources],
            ["paper-a", "paper-b"],
        )
        self.assertTrue(snapshot.input_sha256.startswith("sha256:"))

    def test_snapshot_rejects_tampered_understanding_output(self):
        path = (
            self.workspace
            / "_paper_understandings"
            / "runs"
            / self.source_runs[0]
            / "output"
            / "paper_understanding.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["central_problem"] = "篡改"
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(Exception, "不能由原始响应重放"):
            create_research_landscape_snapshot(
                self.workspace,
                collection_path=self.collection_path,
            )

    def test_snapshot_rejects_duplicate_paper_sources(self):
        result = PaperUnderstandingRunner(
            self.workspace,
            analysis_client=FakeUnderstandingClient(),
            token_counter=FakeTokenCounter(),
        ).run(
            paper_id="paper-a",
            workspace_paper_id="workspace-paper-a",
            topic=TOPIC,
            run_id="understanding-paper-a-second",
            model_profile_path=MODEL_PROFILE,
            understanding_config_path=UNDERSTANDING_CONFIG,
        )
        self.assertEqual(result["status"], "completed")
        self.write_collection(
            ["understanding-paper-a", "understanding-paper-a-second"]
        )

        with self.assertRaisesRegex(Exception, "同一paper_id"):
            create_research_landscape_snapshot(
                self.workspace,
                collection_path=self.collection_path,
            )

    def test_runner_sends_understandings_without_markdown_or_cards(self):
        client = FakeLandscapeClient()
        result = ResearchLandscapeRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
        ).run(
            collection_path=self.collection_path,
            run_id="landscape-one",
            model_profile_path=MODEL_PROFILE,
            landscape_config_path=LANDSCAPE_CONFIG,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["covered_paper_count"], 2)
        self.assertEqual(len(client.calls), 1)
        prompt_text = client.calls[0]["request"]["messages"][1]["content"]
        self.assertIn("论文认知", prompt_text)
        self.assertNotIn("完整Markdown", prompt_text)
        self.assertNotIn("全部Card", prompt_text)
        run_dir = Path(result["run_dir"])
        self.assertTrue((run_dir / "output" / "research_landscape.json").exists())
        report = (run_dir / "review" / "research_landscape.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("paper-a", report)
        self.assertIn("仿真结果", report)
        self.assertIn("L3-L4", report)

    def test_targeted_sample_field_gap_failure_does_not_publish(self):
        result = ResearchLandscapeRunner(
            self.workspace,
            analysis_client=FakeLandscapeClient(include_field_gap=True),
            token_counter=FakeTokenCounter(),
        ).run(
            collection_path=self.collection_path,
            run_id="landscape-field-gap",
            model_profile_path=MODEL_PROFILE,
            landscape_config_path=LANDSCAPE_CONFIG,
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(
            (
                Path(result["run_dir"])
                / "output"
                / "research_landscape.json"
            ).exists()
        )

    def test_prompt_requires_unmapped_exclusivity_and_direct_support(self):
        snapshot = create_research_landscape_snapshot(
            self.workspace,
            collection_path=self.collection_path,
        )
        contribution_to_paper = {
            contribution["contribution_id"]: source.paper_id
            for source in snapshot.sources
            for contribution in source.understanding["contributions"]
        }
        schema = build_research_landscape_schema(
            topic=snapshot.topic,
            review_goal=snapshot.review_goal,
            paper_ids=[source.paper_id for source in snapshot.sources],
            contribution_to_paper=contribution_to_paper,
            corpus_scope=snapshot.corpus_scope,
            config=load_research_landscape_config(LANDSCAPE_CONFIG),
        )
        prompt = (
            RESEARCH_LANDSCAPE_SYSTEM_PROMPT
            + build_research_landscape_prompt(snapshot, schema=schema)
        )

        self.assertIn("已经进入一个或多个dimension的论文禁止", prompt)
        self.assertIn("所引贡献可直接支持", prompt)
        self.assertIn("不得把“标准化”自行等同于“大型化”", prompt)
        self.assertIn("原因解释处于不同层次", prompt)
        self.assertIn("不得为填充字段制造争议", prompt)
        self.assertIn("不得自行声称“可整合”", prompt)
        self.assertIn('"贡献所有权索引"', prompt)
        self.assertIn('"输出前逐项核对"', prompt)
        self.assertIn("contribution_677a8b93f4fa75ce30dc", prompt)

    def test_completed_manifest_records_collection_path(self):
        result = ResearchLandscapeRunner(
            self.workspace,
            analysis_client=FakeLandscapeClient(),
            token_counter=FakeTokenCounter(),
        ).run(
            collection_path=self.collection_path,
            run_id="landscape-collection-path",
            model_profile_path=MODEL_PROFILE,
            landscape_config_path=LANDSCAPE_CONFIG,
        )

        self.assertEqual(result["status"], "completed")
        manifest = json.loads(
            Path(result["manifest_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["collection_path"],
            str(self.collection_path),
        )

    def test_context_overflow_fails_before_provider_call(self):
        client = FakeLandscapeClient()
        result = ResearchLandscapeRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(prompt_tokens=999_999_999),
        ).run(
            collection_path=self.collection_path,
            run_id="landscape-overflow",
            model_profile_path=MODEL_PROFILE,
            landscape_config_path=LANDSCAPE_CONFIG,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(client.calls, [])
        self.assertEqual(
            result["failure"]["error_code"],
            "input.context_budget_exceeded",
        )


if __name__ == "__main__":
    unittest.main()
