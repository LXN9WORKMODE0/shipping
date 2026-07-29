from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_analysis import AnalysisInputError
from shipping_pipeline.llm_json_stage import (
    JSONStageInputError,
    execute_json_stage,
)
from shipping_pipeline.llm_model_profile import load_model_profile
from shipping_pipeline.llm_projection import project_cards
from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_quotes import build_quote_candidates
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.topic_synthesis import (
    OUTLINE_SYSTEM_PROMPT,
    THEME_MAP_SYSTEM_PROMPT,
    TOPIC_SYNTHESIS_RESULT_SCHEMA_VERSION,
    TopicSynthesisRunner,
    create_topic_synthesis_snapshot,
)
from shipping_pipeline.topic_synthesis_contracts import (
    COLLECTION_SCHEMA_VERSION,
    EVIDENCE_MAP_SCHEMA_VERSION,
    OUTLINE_SCHEMA_VERSION,
)
from main import build_parser


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
SYNTHESIS_CONFIG = PROJECT_ROOT / "config" / "topic-synthesis-default.json"
TOPIC_REVIEW_CONFIG = PROJECT_ROOT / "config" / "topic-review-default.json"
TOPIC = "三峡船舶积压疏导策略与长江航运组织"


def make_workspace() -> Path:
    path = TEST_TMP_ROOT / f"topic_synthesis_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def write_source_run(
    workspace: Path,
    *,
    run_id: str,
    paper_id: str,
    paper_title: str,
    evidence_id: str,
    claim: str,
    status: str = "completed",
    schema_version: str = "llm.topic_review_run.v2",
) -> None:
    run_dir = workspace / "_topic_reviews" / "runs" / run_id
    (run_dir / "output").mkdir(parents=True)
    (run_dir / "evidence").mkdir(parents=True)
    (run_dir / "input").mkdir(parents=True)
    review_config = json.loads(TOPIC_REVIEW_CONFIG.read_text(encoding="utf-8"))
    generation_id = f"generation-{paper_id}"
    source_span = {
        "path": "normalized/document.md",
        "start_line": 12,
        "end_line": 13,
    }
    material = {
        "schema_version": "material.v2",
        "material_id": f"{paper_id}:card-one",
        "material_type": "evidence_card",
        "paper_id": paper_id,
        "paper_title": paper_title,
        "source_path": "normalized/document.md",
        "clean_title": "研究结果",
        "raw_title": "研究结果",
        "order": 1,
        "extract": claim,
        "source_span": source_span,
        "source_spans": [source_span],
        "heading_path": ["研究结果"],
        "content_kind": "prose",
        "confidence_flags": [],
        "quality_flags": [],
        "source_fingerprint": "sha256:test",
        "generation_id": generation_id,
    }
    quote = build_quote_candidates(project_cards([material]))[0]
    request_id = f"request-{evidence_id}"
    evidence = {
        "claim": claim,
        "evidence_type": "result",
        "citations": [
            {
                "material_id": f"{paper_id}:card-one",
                "quote_id": quote["quote_id"],
                "quote": claim,
                "source_ref": source_span,
                "source_spans": [source_span],
                "card_title": "研究结果",
                "confidence_flags": [],
                "quality_flags": [],
            }
        ],
        "relevance": "直接服务综述主题。",
        "confidence": "high",
        "caveats": [],
        "evidence_anchor_id": f"anchor-{evidence_id}",
        "evidence_unit_id": evidence_id,
        "request_id": request_id,
        "batch_id": request_id,
    }
    brief = {
        "schema_version": "llm.topic_brief.v2",
        "topic_contribution": {
            "statement": "论文提供了与当前主题直接相关的证据。",
            "evidence_unit_ids": [evidence_id],
        },
        "key_points": [
            {
                "point_type": "finding",
                "statement": claim,
                "evidence_unit_ids": [evidence_id],
            }
        ],
        "review_uses": [
            {
                "use_type": "support",
                "statement": "可用于组织跨论文论证。",
                "evidence_unit_ids": [evidence_id],
            }
        ],
        "cautions": [],
        "evidence_gaps": [],
    }
    revisit = None
    if status == "completed_with_revisit_failure":
        revisit = {
            "schema_version": "llm.topic_revisit_result.v1",
            "status": "failed",
            "trigger_reasons": ["brief_evidence_gap"],
            "gaps": [],
            "scanned_material_count": 1,
            "review_summary": "回查调用失败，保留基础简报。",
            "candidate_materials": [],
            "recovered_material_ids": [],
            "recovered_evidence_unit_ids": [],
            "remaining_evidence_gaps": [
                {
                    "gap_type": "implementation",
                    "question": "是否存在其他可行措施？",
                    "why_it_matters": "关系到策略覆盖是否完整。",
                }
            ],
            "failure": {
                "error_code": "ProviderCallError",
                "error_message": "网络错误",
            },
        }
    result = {
        "schema_version": "llm.topic_paper_result.v2",
        "paper_id": paper_id,
        "paper_title": paper_title,
        "topic": TOPIC,
        "paper_relevance": "core",
        "scope": {
            "schema_version": "llm.topic_scope.v1",
            "paper_relevance": "core",
            "relevance_reason": "论文直接讨论当前主题。",
            "topic_summary": "论文提供相关证据。",
            "selected_materials": [],
        },
        "brief": brief,
        "revisit": revisit,
        "evidence_failures": (
            [
                {
                    "material_id": f"{paper_id}:card-one",
                    "error_code": "citation.numeric_fact_unsupported",
                }
            ]
            if status == "completed_with_failures"
            else []
        ),
    }
    manifest = {
        "schema_version": schema_version,
        "run_id": run_id,
        "status": status,
        "paper_id": paper_id,
        "paper_title": paper_title,
        "topic": TOPIC,
        "generation_id": generation_id,
        "input_sha256": "sha256:input",
        "paper_relevance": "core",
        "evidence_unit_count": 1,
        "review_config": review_config,
        "failure_count": 1 if status != "completed" else 0,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "output" / "paper_brief.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "evidence" / "evidence_units.jsonl").write_text(
        json.dumps(evidence, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "input" / "materials.jsonl").write_text(
        json.dumps(material, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_collection(
    workspace: Path,
    run_ids: list[str],
    *,
    filename: str = "collection.json",
) -> Path:
    path = workspace / filename
    path.write_text(
        json.dumps(
            {
                "schema_version": COLLECTION_SCHEMA_VERSION,
                "topic": TOPIC,
                "source_run_ids": run_ids,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class FakeTokenCounter:
    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        prompt_tokens = max(1, len(json.dumps(messages, ensure_ascii=False)) // 4)
        return TokenCount(
            prompt_tokens=prompt_tokens,
            encoded_prompt_sha256=f"sha256:{prompt_tokens:064x}",
        )


class FakeSynthesisClient:
    provider = "openai-compatible"

    def __init__(
        self,
        *,
        invalid_outline: bool = False,
        repair_stage: str | None = None,
    ) -> None:
        self.model = "deepseek-ai/DeepSeek-V4-Pro"
        self.invalid_outline = invalid_outline
        self.repair_stage = repair_stage
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        task: str,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> ProviderResult:
        self.calls.append({"task": task, "request": request_payload, "context": context})
        user = json.loads(request_payload["messages"][1]["content"])
        is_correction = task.endswith("_correction")
        original_user = user["原始任务输入"] if is_correction else user
        if task in {
            "topic_synthesis_theme_map",
            "topic_synthesis_theme_map_correction",
        }:
            evidence_ids = [
                str(unit["evidence_unit_id"])
                for paper in original_user["论文简报投影"]
                for unit in paper["evidence"]
            ]
            assignment_ids = (
                evidence_ids[:-1]
                if self.repair_stage == "theme_map" and not is_correction
                else evidence_ids
            )
            content = {
                "schema_version": EVIDENCE_MAP_SCHEMA_VERSION,
                "themes": [
                    {
                        "title": "能力约束与组织需求",
                        "focus": "比较不同论文对通航约束的说明。",
                        "synthesis_value": "用于建立跨论文问题框架。",
                        "relation_type": "complementary",
                        "assignments": [
                            {
                                "evidence_unit_id": evidence_id,
                                "role": "main_support",
                            }
                            for evidence_id in assignment_ids
                        ],
                    }
                ],
                "unassigned_evidence": [],
                "corpus_gaps": [
                    {
                        "gap_type": "implementation",
                        "question": "不同疏导措施是否具有可比效果？",
                        "why_it_matters": "关系到策略选择能否形成统一判断。",
                    }
                ],
            }
        elif task in {
            "topic_synthesis_outline",
            "topic_synthesis_outline_correction",
        }:
            theme = original_user["主题矩阵"][0]
            evidence_ids = [
                str(row["evidence_unit_id"]) for row in theme["assignments"]
            ]
            selected_ids = evidence_ids
            content = {
                "schema_version": OUTLINE_SCHEMA_VERSION,
                "working_title": "三峡航运组织研究综述",
                "central_question": "现有研究如何解释通航约束并组织疏导路径？",
                "synthesis_units": [
                    {
                        "unit_index": 1,
                        "unit_type": (
                            "single_source_context"
                            if self.invalid_outline
                            else "complement"
                        ),
                        "synthesis_statement": "不同论文从相互补充的角度说明通航约束。",
                        "theme_ids": [str(theme["theme_id"])],
                        "evidence_unit_ids": selected_ids,
                        "gap_ids": [],
                    }
                ],
                "sections": [
                    {
                        "title": "通航约束",
                        "purpose": "综合比较不同来源的约束证据。",
                        "paragraphs": [
                            {
                                "synthesis_move": "比较不同论文对约束来源的观察。",
                                "synthesis_unit_indexes": (
                                    []
                                    if self.repair_stage == "outline"
                                    and not is_correction
                                    else [1]
                                ),
                            }
                        ],
                    }
                ],
                "search_directions": [
                    {
                        "priority": "high",
                        "question": "不同措施是否具有统一评价证据？",
                        "reason": "当前语料缺少直接比较。",
                        "related_theme_ids": [str(theme["theme_id"])],
                        "source_gap_ids": [
                            str(row["gap_id"])
                            for row in original_user["语料缺口"]
                        ],
                    }
                ],
            }
        else:
            raise AssertionError(f"未知任务：{task}")
        prompt_tokens = int(context["planned_input_tokens"])
        response = {
            "id": f"response-{len(self.calls)}",
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
                "completion_tokens": 128,
                "total_tokens": prompt_tokens + 128,
            },
        }
        return ProviderResult(
            raw_body=json.dumps(response, ensure_ascii=False).encode("utf-8"),
            parsed_response=response,
            response_id=str(response["id"]),
            response_model=self.model,
            system_fingerprint=None,
            finish_reason="stop",
            reasoning_content_chars=0,
            usage=dict(response["usage"]),
        )


class TopicSynthesisTests(unittest.TestCase):
    def test_theme_map_prompt_requires_terminal_assignments_to_be_exclusive(self):
        self.assertIn(
            "assignments 与 unassigned_evidence 是互斥终态",
            THEME_MAP_SYSTEM_PROMPT,
        )
        self.assertIn("不能同时执行", THEME_MAP_SYSTEM_PROMPT)

    def test_outline_prompt_forbids_duplicate_ids_within_one_unit(self):
        self.assertIn(
            "theme_ids、evidence_unit_ids 和 gap_ids 都不得出现重复元素",
            OUTLINE_SYSTEM_PROMPT,
        )

    def setUp(self) -> None:
        self.workspace = make_workspace()
        write_source_run(
            self.workspace,
            run_id="run-a",
            paper_id="paper-a",
            paper_title="论文甲",
            evidence_id="evidence_a",
            claim="论文甲指出船闸能力构成通航约束。",
        )
        write_source_run(
            self.workspace,
            run_id="run-b",
            paper_id="paper-b",
            paper_title="论文乙",
            evidence_id="evidence_b",
            claim="论文乙指出运输组织需要适应通航条件。",
        )
        self.collection = write_collection(self.workspace, ["run-a", "run-b"])

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_snapshot_revalidates_sources_and_builds_compact_projection(self):
        snapshot = create_topic_synthesis_snapshot(
            self.workspace,
            self.collection,
        )
        self.assertEqual(len(snapshot.sources), 2)
        self.assertEqual(len(snapshot.evidence_units), 2)
        self.assertEqual(
            set(snapshot.evidence_to_paper),
            {"evidence_a", "evidence_b"},
        )
        projection = snapshot.model_projection[0]
        self.assertNotIn("citations", projection["evidence"][0])
        self.assertTrue(projection["evidence"][0]["is_key_point"])

    def test_snapshot_rejects_source_evidence_without_archived_citation(self):
        evidence_path = (
            self.workspace
            / "_topic_reviews"
            / "runs"
            / "run-a"
            / "evidence"
            / "evidence_units.jsonl"
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["citations"] = []
        evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(AnalysisInputError, "缺少逐字引文"):
            create_topic_synthesis_snapshot(
                self.workspace,
                self.collection,
            )

    def test_cli_parser_exposes_topic_synthesis_as_separate_command(self):
        args = build_parser().parse_args(
            [
                "llm-topic-synthesis",
                "--collection",
                "collection.json",
            ]
        )
        self.assertEqual(args.command, "llm-topic-synthesis")
        self.assertEqual(args.collection, Path("collection.json"))
        self.assertEqual(args.synthesis_config, SYNTHESIS_CONFIG)

    def test_snapshot_rejects_duplicate_paper_and_old_schema(self):
        write_source_run(
            self.workspace,
            run_id="run-a-new",
            paper_id="paper-a",
            paper_title="论文甲",
            evidence_id="evidence_a_new",
            claim="论文甲补充说明通航约束。",
        )
        duplicate_collection = write_collection(
            self.workspace,
            ["run-a", "run-a-new"],
            filename="duplicate.json",
        )
        with self.assertRaisesRegex(AnalysisInputError, "同一论文"):
            create_topic_synthesis_snapshot(
                self.workspace,
                duplicate_collection,
            )

        write_source_run(
            self.workspace,
            run_id="run-old",
            paper_id="paper-old",
            paper_title="旧论文",
            evidence_id="evidence_old",
            claim="旧论文提供历史材料。",
            schema_version="llm.topic_review_run.v1",
        )
        old_collection = write_collection(
            self.workspace,
            ["run-a", "run-old"],
            filename="old.json",
        )
        with self.assertRaisesRegex(AnalysisInputError, "不是当前"):
            create_topic_synthesis_snapshot(self.workspace, old_collection)

    def test_completed_run_archives_two_stages_coverage_and_readable_report(self):
        client = FakeSynthesisClient()
        result = TopicSynthesisRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
        ).run(
            collection_path=self.collection,
            run_id="synthesis-success",
            model_profile_path=MODEL_PROFILE,
            synthesis_config_path=SYNTHESIS_CONFIG,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [row["task"] for row in client.calls],
            ["topic_synthesis_theme_map", "topic_synthesis_outline"],
        )
        self.assertEqual(result["request_count"], 2)
        self.assertGreater(result["usage"]["total_tokens"], 0)
        run_dir = Path(result["run_dir"])
        output = json.loads(
            (run_dir / "output" / "topic_synthesis.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            output["schema_version"],
            TOPIC_SYNTHESIS_RESULT_SCHEMA_VERSION,
        )
        self.assertEqual(output["coverage"]["source_evidence_count"], 2)
        self.assertEqual(output["coverage"]["theme_assignment_count"], 2)
        self.assertEqual(output["coverage"]["single_theme_evidence_count"], 2)
        self.assertEqual(output["coverage"]["multi_theme_evidence_count"], 0)
        self.assertEqual(
            output["coverage"]["max_observed_themes_per_evidence"],
            1,
        )
        self.assertEqual(output["coverage"]["outline_used_count"], 2)
        self.assertEqual(output["coverage"]["unplaced_synthesis_unit_count"], 0)
        self.assertEqual(output["coverage"]["unprioritized_gap_count"], 0)
        self.assertEqual(output["coverage"]["cross_paper_theme_count"], 1)
        self.assertEqual(
            output["coverage"]["cross_paper_synthesis_unit_count"],
            1,
        )
        self.assertEqual(output["coverage"]["quality_flags"], [])
        report = (run_dir / "review" / "topic_synthesis.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("论文甲指出船闸能力构成通航约束。", report)
        self.assertIn("normalized/document.md L12-L13", report)
        self.assertIn("## 覆盖审计", report)
        self.assertTrue((run_dir / "theme_map" / "raw_response.json").is_file())
        self.assertTrue((run_dir / "outline" / "raw_response.json").is_file())
        derivations = json.loads(
            (run_dir / "audit" / "contract_derivations.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            derivations["schema_version"],
            "llm.topic_synthesis_derivations.v1",
        )
        self.assertEqual(
            derivations["outline"]["paragraph_bindings"][0]["rule"],
            "evidence_units_only",
        )

    def test_outline_contract_failure_does_not_publish_partial_result(self):
        client = FakeSynthesisClient(invalid_outline=True)
        result = TopicSynthesisRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
        ).run(
            collection_path=self.collection,
            run_id="synthesis-failed",
            model_profile_path=MODEL_PROFILE,
            synthesis_config_path=SYNTHESIS_CONFIG,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("outline.unit_paper_count_invalid", result["failure_codes"])
        run_dir = Path(result["run_dir"])
        self.assertTrue(
            (run_dir / "theme_map" / "validated_theme_map.json").is_file()
        )
        self.assertFalse((run_dir / "output" / "topic_synthesis.json").exists())
        self.assertFalse((run_dir / "review" / "topic_synthesis.md").exists())
        manifest = json.loads(
            (run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["request_count"], 3)
        self.assertEqual(result["request_count"], 3)
        self.assertGreater(result["usage"]["total_tokens"], 0)
        self.assertTrue(
            (run_dir / "outline" / "correction" / "raw_response.json").is_file()
        )

    def test_theme_map_contract_error_is_repaired_once(self):
        client = FakeSynthesisClient(repair_stage="theme_map")
        result = TopicSynthesisRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
        ).run(
            collection_path=self.collection,
            run_id="synthesis-theme-map-repaired",
            model_profile_path=MODEL_PROFILE,
            synthesis_config_path=SYNTHESIS_CONFIG,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [row["task"] for row in client.calls],
            [
                "topic_synthesis_theme_map",
                "topic_synthesis_theme_map_correction",
                "topic_synthesis_outline",
            ],
        )
        run_dir = Path(result["run_dir"])
        correction = json.loads(
            (
                run_dir
                / "theme_map"
                / "correction"
                / "request.json"
            ).read_text(encoding="utf-8")
        )
        repair_input = json.loads(correction["messages"][1]["content"])
        self.assertEqual(
            repair_input["合同错误"]["error_code"],
            "evidence_map.coverage_mismatch",
        )
        self.assertEqual(result["request_count"], 3)

    def test_outline_schema_error_is_repaired_once(self):
        client = FakeSynthesisClient(repair_stage="outline")
        result = TopicSynthesisRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
        ).run(
            collection_path=self.collection,
            run_id="synthesis-outline-repaired",
            model_profile_path=MODEL_PROFILE,
            synthesis_config_path=SYNTHESIS_CONFIG,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [row["task"] for row in client.calls],
            [
                "topic_synthesis_theme_map",
                "topic_synthesis_outline",
                "topic_synthesis_outline_correction",
            ],
        )
        run_dir = Path(result["run_dir"])
        correction = json.loads(
            (
                run_dir
                / "outline"
                / "correction"
                / "request.json"
            ).read_text(encoding="utf-8")
        )
        repair_input = json.loads(correction["messages"][1]["content"])
        self.assertEqual(
            repair_input["合同错误"]["error_code"],
            "schema.topic_review_outline_invalid",
        )
        self.assertEqual(
            repair_input["合同错误"]["error_path"],
            "$.sections[0].paragraphs[0].synthesis_unit_indexes",
        )
        self.assertEqual(result["request_count"], 3)

    def test_publication_failure_removes_report_and_output(self):
        client = FakeSynthesisClient()
        with patch(
            "shipping_pipeline.topic_synthesis._atomic_write_json",
            side_effect=OSError("publish failed"),
        ):
            result = TopicSynthesisRunner(
                self.workspace,
                analysis_client=client,
                token_counter=FakeTokenCounter(),
            ).run(
                collection_path=self.collection,
                run_id="synthesis-publish-failed",
                model_profile_path=MODEL_PROFILE,
                synthesis_config_path=SYNTHESIS_CONFIG,
            )
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["review_path"])
        run_dir = Path(result["run_dir"])
        self.assertFalse((run_dir / "output" / "topic_synthesis.json").exists())
        self.assertFalse((run_dir / "review" / "topic_synthesis.md").exists())

    def test_budget_preflight_failure_archives_stage_result(self):
        run_dir = self.workspace / "budget-preflight"
        profile = replace(
            load_model_profile(MODEL_PROFILE),
            context_window_tokens=10,
            safety_margin_tokens=1,
        )
        with self.assertRaisesRegex(
            JSONStageInputError,
            "input.context_budget_exceeded",
        ):
            execute_json_stage(
                run_dir=run_dir,
                stage="budget",
                task_name="unused",
                directory_name=None,
                system_prompt="系统约束",
                user_prompt="需要被计数的输入内容",
                max_output_tokens=8,
                context={},
                client=FakeSynthesisClient(),
                profile=profile,
                token_counter=FakeTokenCounter(),
            )
        result = json.loads(
            (run_dir / "budget" / "result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["error_code"],
            "input.context_budget_exceeded",
        )
        self.assertIsInstance(result["planned_input_tokens"], int)
        self.assertTrue(result["prompt_sha256"].startswith("sha256:"))

    def test_partial_source_is_explicitly_carried_into_coverage(self):
        write_source_run(
            self.workspace,
            run_id="run-partial",
            paper_id="paper-partial",
            paper_title="部分完成论文",
            evidence_id="evidence_partial",
            claim="部分完成论文仍保留严格证据。",
            status="completed_with_revisit_failure",
        )
        collection = write_collection(
            self.workspace,
            ["run-a", "run-partial"],
            filename="partial.json",
        )
        client = FakeSynthesisClient()
        result = TopicSynthesisRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
        ).run(
            collection_path=collection,
            run_id="synthesis-partial-source",
            model_profile_path=MODEL_PROFILE,
            synthesis_config_path=SYNTHESIS_CONFIG,
        )
        self.assertEqual(result["status"], "completed")
        output = json.loads(
            (
                Path(result["run_dir"])
                / "output"
                / "topic_synthesis.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            output["coverage"]["partial_source_run_ids"],
            ["run-partial"],
        )
        report = (
            Path(result["run_dir"]) / "review" / "topic_synthesis.md"
        ).read_text(encoding="utf-8")
        self.assertIn("基础简报有效，但单篇自动回查未闭合", report)
        self.assertIn("部分完成论文", report)

    def test_evidence_partial_source_is_explicitly_carried_into_coverage(self):
        write_source_run(
            self.workspace,
            run_id="run-evidence-partial",
            paper_id="paper-evidence-partial",
            paper_title="部分证据论文",
            evidence_id="evidence_partial",
            claim="部分证据论文仍保留通过合同的证据。",
            status="completed_with_failures",
        )
        collection = write_collection(
            self.workspace,
            ["run-a", "run-evidence-partial"],
            filename="evidence-partial.json",
        )

        result = TopicSynthesisRunner(
            self.workspace,
            analysis_client=FakeSynthesisClient(),
            token_counter=FakeTokenCounter(),
        ).run(
            collection_path=collection,
            run_id="synthesis-evidence-partial-source",
            model_profile_path=MODEL_PROFILE,
            synthesis_config_path=SYNTHESIS_CONFIG,
        )

        self.assertEqual(result["status"], "completed")
        output = json.loads(
            (
                Path(result["run_dir"])
                / "output"
                / "topic_synthesis.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            output["coverage"]["partial_source_run_ids"],
            ["run-evidence-partial"],
        )
        report = (
            Path(result["run_dir"]) / "review" / "topic_synthesis.md"
        ).read_text(encoding="utf-8")
        self.assertIn("部分入选 Card 未通过 Evidence 合同", report)
        self.assertIn("失败 Card 保留在对应单篇运行的失败账本", report)
        self.assertIn("部分证据论文", report)


if __name__ == "__main__":
    unittest.main()
