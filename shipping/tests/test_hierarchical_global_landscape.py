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
    build_global_relation_review_schema,
    build_global_relation_selection_schema,
    HierarchicalGlobalLandscapeRunner,
    HierarchicalGlobalSnapshot,
    restore_hierarchical_global_dimension_ids,
    normalize_global_relation_selection,
    normalize_global_relation_statement,
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
        "schema_version": "llm.hierarchical_global_landscape.v7",
        "topic": TOPIC,
        "review_goal": GOAL,
        "central_problem": "如何从运行瓶颈出发选择经过充分验证的提升方法？",
        "global_dimensions": [
            {
                "global_dimension_index": 1,
                "title": "问题与方法",
                "question": "运行瓶颈与调度方法如何对应？",
            },
            {
                "global_dimension_index": 2,
                "title": "验证边界",
                "question": "方法验证水平如何？",
            },
        ],
        "cross_cluster_relations": [
            {
                "relation_type": "complements",
                "statement": "运行瓶颈描述与调度方法研究分别回答问题和方法。",
                "supporting_local_dimension_ids": ["dim-op-2", "dim-dis-1"],
            }
        ],
        "global_gaps": [
            {"question": "工程部署效果如何？", "why_it_matters": "决定推广边界。", "related_global_dimension_indexes": [2]}
        ],
        "unmapped_local_dimensions": [],
        "local_dimension_accounting": [
            {"local_dimension_id": "dim-op-1", "disposition": "mapped", "global_dimension_index": 1, "reason": None},
            {"local_dimension_id": "dim-op-2", "disposition": "mapped", "global_dimension_index": 1, "reason": None},
            {"local_dimension_id": "dim-dis-1", "disposition": "mapped", "global_dimension_index": 1, "reason": None},
            {"local_dimension_id": "dim-dis-2", "disposition": "mapped", "global_dimension_index": 2, "reason": None}
        ],
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
        if task == "hierarchical_global_landscape":
            content = global_payload()
            content["cross_cluster_relations"] = []
        elif task == "hierarchical_global_relation_selection":
            content = {
                "schema_version": "llm.hierarchical_global_relation_selection.v1",
                "selected_cluster_pair_ids": ["operation::dispatch"],
            }
        elif task == "hierarchical_global_relations":
            content = {
                "schema_version": "llm.hierarchical_global_relations.v1",
                "relations": [{
                    "cluster_pair_id": "operation::dispatch",
                    "relation": {
                        "relation_type": "complements",
                        "statement": "运行瓶颈描述与调度方法分别回答问题和方法。",
                        "left_local_dimension_id": "D0002",
                        "right_local_dimension_id": "D0003",
                    },
                }],
            }
        else:
            raise AssertionError(task)
        response = {
            "id": "response-global", "model": self.model,
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content, ensure_ascii=False), "reasoning_content": ""}}],
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
        payload["local_dimension_accounting"][0]["local_dimension_id"] = "dim-op-2"
        with self.assertRaisesRegex(HierarchicalGlobalContractError, "schema_invalid"):
            validate_hierarchical_global_landscape(payload, schema=schema, clusters=local_clusters())

    def test_validator_derives_global_dimension_ownership(self):
        config = load_hierarchical_global_config(GLOBAL_CONFIG)
        schema = build_hierarchical_global_schema(topic=TOPIC, review_goal=GOAL, clusters=local_clusters(), config=config)
        result = validate_hierarchical_global_landscape(global_payload(), schema=schema, clusters=local_clusters())
        self.assertEqual(result["global_dimensions"][0]["cluster_ids"], ["dispatch", "operation"])
        self.assertEqual(result["global_dimensions"][0]["paper_ids"], ["paper-a", "paper-b", "paper-c"])
        self.assertEqual(result["cross_cluster_relations"][0]["from_cluster_id"], "dispatch")
        self.assertEqual(result["cross_cluster_relations"][0]["to_cluster_id"], "operation")

    def test_validator_rejects_incomplete_dimension_accounting(self):
        config = load_hierarchical_global_config(GLOBAL_CONFIG)
        schema = build_hierarchical_global_schema(topic=TOPIC, review_goal=GOAL, clusters=local_clusters(), config=config)
        payload = global_payload()
        payload["local_dimension_accounting"].pop()
        with self.assertRaisesRegex(HierarchicalGlobalContractError, "schema_invalid"):
            validate_hierarchical_global_landscape(payload, schema=schema, clusters=local_clusters())

    def test_validator_rejects_relation_without_both_cluster_sources(self):
        config = load_hierarchical_global_config(GLOBAL_CONFIG)
        schema = build_hierarchical_global_schema(topic=TOPIC, review_goal=GOAL, clusters=local_clusters(), config=config)
        payload = global_payload()
        payload["cross_cluster_relations"][0]["supporting_local_dimension_ids"] = ["dim-op-1", "dim-op-2"]
        with self.assertRaisesRegex(HierarchicalGlobalContractError, "relation_support_invalid"):
            validate_hierarchical_global_landscape(payload, schema=schema, clusters=local_clusters())

    def test_restore_replaces_all_local_dimension_reference_fields(self):
        payload = {
            "global_dimensions": [{"local_dimension_ids": ["D0001"]}],
            "cross_cluster_relations": [{"supporting_local_dimension_ids": ["D0001", "D0002"]}],
            "unmapped_local_dimensions": [{"local_dimension_id": "D0002"}],
            "local_dimension_accounting": [{"local_dimension_id": "D0001"}],
            "central_problem": "D0001只是正文，不应替换",
        }
        restored = restore_hierarchical_global_dimension_ids(
            payload,
            alias_to_dimension={"D0001": "dimension-long-a", "D0002": "dimension-long-b"},
        )
        self.assertEqual(restored["global_dimensions"][0]["local_dimension_ids"], ["dimension-long-a"])
        self.assertEqual(
            restored["cross_cluster_relations"][0]["supporting_local_dimension_ids"],
            ["dimension-long-a", "dimension-long-b"],
        )
        self.assertEqual(restored["unmapped_local_dimensions"][0]["local_dimension_id"], "dimension-long-b")
        self.assertEqual(restored["local_dimension_accounting"][0]["local_dimension_id"], "dimension-long-a")
        self.assertEqual(restored["central_problem"], "D0001只是正文，不应替换")

    def test_relation_review_schema_fixes_cluster_pair_sides(self):
        selection = build_global_relation_selection_schema(self.snapshot, max_relations=1)
        self.assertEqual(
            selection["properties"]["selected_cluster_pair_ids"]["maxItems"], 1
        )
        schema = build_global_relation_review_schema(
            self.snapshot, selected_pair_ids=["operation::dispatch"]
        )
        relation = schema["properties"]["relations"]["prefixItems"][0]
        self.assertEqual(relation["properties"]["cluster_pair_id"]["const"], "operation::dispatch")
        value = relation["properties"]["relation"]["properties"]
        self.assertEqual(value["left_local_dimension_id"]["enum"], ["dim-op-1", "dim-op-2"])
        self.assertEqual(value["right_local_dimension_id"]["enum"], ["dim-dis-1", "dim-dis-2"])

    def test_relation_selection_normalizes_reversed_pair_order(self):
        schema = build_global_relation_selection_schema(self.snapshot, max_relations=1)
        value = normalize_global_relation_selection({
            "schema_version": "llm.hierarchical_global_relation_selection.v1",
            "selected_cluster_pair_ids": ["dispatch::operation"],
        }, schema)
        self.assertEqual(value["selected_cluster_pair_ids"], ["operation::dispatch"])

    def test_relation_statement_expands_pair_pronouns(self):
        value = normalize_global_relation_statement({
            "relations": [{"relation": {
                "statement": "前者与后者互补，两者范围不同。",
                "left_local_dimension_id": "dim-dis-1",
                "right_local_dimension_id": "dim-op-1",
            }}]
        }, pair_id="operation::dispatch", snapshot=self.snapshot)
        relation = value["relations"][0]["relation"]
        statement = relation["statement"]
        self.assertEqual(statement, "运行基线与调度优化互补，这两类研究范围不同。")
        self.assertEqual(relation["left_local_dimension_id"], "dim-op-1")
        self.assertEqual(relation["right_local_dimension_id"], "dim-dis-1")

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
