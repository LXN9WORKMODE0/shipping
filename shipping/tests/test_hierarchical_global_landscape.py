from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

import main
from shipping_pipeline.hierarchical_global_contracts import (
    HierarchicalGlobalContractError,
    build_hierarchical_global_schema,
    load_hierarchical_global_config,
    validate_hierarchical_global_landscape,
)
from shipping_pipeline.hierarchical_global_landscape import (
    HierarchicalGlobalLandscapeRunner,
    HierarchicalGlobalSnapshot,
)
from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_tokenizer import TokenCount


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
GLOBAL_CONFIG = PROJECT_ROOT / "config" / "hierarchical-global-landscape-default.json"
TOPIC = "三峡枢纽通航能力的提升方法"
GOAL = "比较不同提升路径的方法、效果和验证边界"


def local_clusters():
    return [
        {
            "cluster_id": "operation",
            "cluster_title": "运行基线",
            "landscape_run_id": "local-operation",
            "landscape_sha256": "sha256:operation",
            "landscape": {
                "landscape_id": "landscape-operation",
                "central_problem": "运行需求超过能力。",
                "dimensions": [
                    {"dimension_id": "dim-op-1", "title": "运行负荷", "question": "负荷如何？", "paper_ids": ["paper-a"], "contribution_ids": ["contrib-a"]},
                    {"dimension_id": "dim-op-2", "title": "调度瓶颈", "question": "瓶颈何在？", "paper_ids": ["paper-b"], "contribution_ids": ["contrib-b"]},
                ],
                "relations": [], "research_evolution": [], "disagreements": [],
                "corpus_gaps": [{"question": "长期变化如何？", "why_it_matters": "需要更新数据。", "related_dimension_indexes": [1]}],
                "unmapped_papers": [],
            },
        },
        {
            "cluster_id": "dispatch",
            "cluster_title": "调度优化",
            "landscape_run_id": "local-dispatch",
            "landscape_sha256": "sha256:dispatch",
            "landscape": {
                "landscape_id": "landscape-dispatch",
                "central_problem": "如何优化调度。",
                "dimensions": [
                    {"dimension_id": "dim-dis-1", "title": "算法方法", "question": "如何优化？", "paper_ids": ["paper-c"], "contribution_ids": ["contrib-c"]},
                    {"dimension_id": "dim-dis-2", "title": "验证边界", "question": "如何验证？", "paper_ids": ["paper-d"], "contribution_ids": ["contrib-d"]},
                ],
                "relations": [], "research_evolution": [], "disagreements": [],
                "corpus_gaps": [{"question": "工程效果如何？", "why_it_matters": "决定可用性。", "related_dimension_indexes": [2]}],
                "unmapped_papers": [],
            },
        },
    ]


def global_payload():
    return {
        "schema_version": "llm.hierarchical_global_landscape.v1",
        "topic": TOPIC,
        "review_goal": GOAL,
        "central_problem": "如何从运行瓶颈出发选择经过充分验证的提升方法？",
        "global_dimensions": [
            {
                "global_dimension_index": 1,
                "title": "问题与方法",
                "question": "运行瓶颈与调度方法如何对应？",
                "cluster_ids": ["operation", "dispatch"],
                "local_dimension_ids": ["dim-op-1", "dim-op-2", "dim-dis-1"],
                "paper_ids": ["paper-a", "paper-b", "paper-c"],
            },
            {
                "global_dimension_index": 2,
                "title": "验证边界",
                "question": "方法验证水平如何？",
                "cluster_ids": ["dispatch"],
                "local_dimension_ids": ["dim-dis-2"],
                "paper_ids": ["paper-d"],
            },
        ],
        "cross_cluster_relations": [
            {
                "relation_type": "complements",
                "from_cluster_id": "operation",
                "to_cluster_id": "dispatch",
                "statement": "运行瓶颈描述与调度方法研究分别回答问题和方法。",
                "supporting_local_dimension_ids": ["dim-op-2", "dim-dis-1"],
            }
        ],
        "global_gaps": [
            {"question": "工程部署效果如何？", "why_it_matters": "决定推广边界。", "related_global_dimension_indexes": [2]}
        ],
        "unmapped_local_dimensions": [],
        "look_back_requests": [
            {
                "question": "是否存在现场调度验证？",
                "reason": "当前局部图谱主要是仿真证据。",
                "target_cluster_ids": ["dispatch"],
                "desired_source_levels": ["core", "supporting"],
                "priority": "high",
            }
        ],
    }


class FakeCounter:
    def count_messages(self, messages):
        return TokenCount(prompt_tokens=max(1, len(json.dumps(messages, ensure_ascii=False)) // 4), encoded_prompt_sha256="sha256:fake")


class FakeGlobalClient:
    provider = "openai-compatible"
    model = "deepseek-ai/DeepSeek-V4-Pro"

    def complete(self, task, request_payload, context):
        if task != "hierarchical_global_landscape":
            raise AssertionError(task)
        response = {
            "id": "response-global", "model": self.model,
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(global_payload(), ensure_ascii=False), "reasoning_content": ""}}],
            "usage": {"prompt_tokens": context["planned_input_tokens"], "completion_tokens": 512, "total_tokens": context["planned_input_tokens"] + 512},
        }
        return ProviderResult(
            raw_body=json.dumps(response, ensure_ascii=False).encode(),
            parsed_response=response, response_id="response-global",
            response_model=self.model, system_fingerprint=None,
            finish_reason="stop", reasoning_content_chars=0,
            usage=response["usage"],
        )


class HierarchicalGlobalLandscapeTest(unittest.TestCase):
    def setUp(self):
        self.workspace = TEST_TMP_ROOT / f"global_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self.snapshot = HierarchicalGlobalSnapshot(
            topic=TOPIC, review_goal=GOAL,
            local_batch_run_id="local-batch",
            local_batch_sha256="sha256:batch",
            clusters=tuple(local_clusters()), input_sha256="sha256:input",
        )

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_cli_exposes_global_run(self):
        args = main.build_parser().parse_args([
            "hierarchical-landscape-global-run", "--local-batch-run-id", "local-batch"
        ])
        self.assertEqual(args.command, "hierarchical-landscape-global-run")

    def test_validator_rejects_silent_local_dimension_loss(self):
        config = load_hierarchical_global_config(GLOBAL_CONFIG)
        schema = build_hierarchical_global_schema(topic=TOPIC, review_goal=GOAL, clusters=local_clusters(), config=config)
        payload = global_payload()
        payload["global_dimensions"][0]["local_dimension_ids"].remove("dim-op-1")
        payload["global_dimensions"][0]["paper_ids"].remove("paper-a")
        with self.assertRaisesRegex(HierarchicalGlobalContractError, "silently_missing"):
            validate_hierarchical_global_landscape(payload, schema=schema, clusters=local_clusters())

    def test_validator_rejects_relation_without_both_cluster_sources(self):
        config = load_hierarchical_global_config(GLOBAL_CONFIG)
        schema = build_hierarchical_global_schema(topic=TOPIC, review_goal=GOAL, clusters=local_clusters(), config=config)
        payload = global_payload()
        payload["cross_cluster_relations"][0]["supporting_local_dimension_ids"] = ["dim-op-1", "dim-op-2"]
        with self.assertRaisesRegex(HierarchicalGlobalContractError, "relation_support_invalid"):
            validate_hierarchical_global_landscape(payload, schema=schema, clusters=local_clusters())

    def test_runner_publishes_global_coverage_and_lookback_ledger(self):
        result = HierarchicalGlobalLandscapeRunner(
            self.workspace,
            analysis_client=FakeGlobalClient(), token_counter=FakeCounter(),
            snapshot_loader=lambda workspace, local_batch_run_id: self.snapshot,
        ).run(
            local_batch_run_id="local-batch", run_id="global-one",
            model_profile_path=MODEL_PROFILE, global_config_path=GLOBAL_CONFIG,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["coverage"]["coverage_ratio"], 1.0)
        root = Path(result["run_dir"])
        output = json.loads((root / "output" / "hierarchical_global_landscape.json").read_text(encoding="utf-8"))
        self.assertTrue(output["global_landscape_id"].startswith("global_landscape_"))
        self.assertTrue((root / "audit" / "look_back_requests.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
