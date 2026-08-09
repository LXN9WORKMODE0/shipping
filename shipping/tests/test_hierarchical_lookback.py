from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

import main
from shipping_pipeline.hierarchical_lookback import (
    HierarchicalLookbackRunner,
    LookbackSnapshot,
    build_lookback_schema,
    load_lookback_config,
    validate_lookback,
)
from shipping_pipeline.llm_analysis import AnalysisInputError
from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_tokenizer import TokenCount


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
CONFIG = PROJECT_ROOT / "config" / "hierarchical-lookback-default.json"


def snapshot():
    return LookbackSnapshot(
        topic="三峡枢纽通航能力的提升方法",
        global_run_id="global-one", global_input_sha256="sha256:global",
        requests=(
            {"look_back_request_id": "request-1", "question": "是否有现场验证？", "reason": "当前只有仿真。", "target_cluster_ids": ["dispatch"], "desired_source_levels": ["core"]},
            {"look_back_request_id": "request-2", "question": "是否有长期数据？", "reason": "缺少后续统计。", "target_cluster_ids": ["baseline"], "desired_source_levels": ["core", "supporting"]},
        ),
        candidates=(
            {"record_id": "record-a", "paper_id": "paper-a", "paper_title": "现场调度研究", "screening_run_id": "screen-a", "paper_relevance": "core", "relevance_reason": "包含现场试验。", "topic_summary": "研究现场调度效果。", "selected_material_reasons": ["报告现场效果。"]},
            {"record_id": "record-b", "paper_id": "paper-b", "paper_title": "年度运行分析", "screening_run_id": "screen-b", "paper_relevance": "supporting", "relevance_reason": "包含年度数据。", "topic_summary": "分析后续年度运行。", "selected_material_reasons": ["提供年度统计。"]},
        ),
        input_sha256="sha256:input",
    )


def payload():
    return {
        "schema_version": "llm.hierarchical_lookback.v1",
        "topic": "三峡枢纽通航能力的提升方法",
        "request_results": [
            {"look_back_request_id": "request-1", "candidates": [{"record_id": "record-a", "matched_reason": "筛选摘要明确提到现场试验。", "expected_use": "核查仿真向现场转化。", "confidence": "high"}], "no_match_reason": None},
            {"look_back_request_id": "request-2", "candidates": [{"record_id": "record-b", "matched_reason": "筛选摘要包含后续年度统计。", "expected_use": "补充长期趋势。", "confidence": "high"}], "no_match_reason": None},
        ],
    }


class FakeCounter:
    def count_messages(self, messages):
        return TokenCount(prompt_tokens=max(1, len(json.dumps(messages, ensure_ascii=False)) // 4), encoded_prompt_sha256="sha256:fake")


class FakeClient:
    provider = "openai-compatible"
    model = "deepseek-ai/DeepSeek-V4-Pro"
    def complete(self, task, request_payload, context):
        self.request_payload = request_payload
        response = {"id": "response-lookback", "model": self.model, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload(), ensure_ascii=False), "reasoning_content": ""}}], "usage": {"prompt_tokens": context["planned_input_tokens"], "completion_tokens": 256, "total_tokens": context["planned_input_tokens"] + 256}}
        return ProviderResult(raw_body=json.dumps(response, ensure_ascii=False).encode(), parsed_response=response, response_id="response-lookback", response_model=self.model, system_fingerprint=None, finish_reason="stop", reasoning_content_chars=0, usage=response["usage"])


class HierarchicalLookbackTest(unittest.TestCase):
    def setUp(self):
        self.workspace = TEST_TMP_ROOT / f"lookback_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_cli_exposes_lookback(self):
        args = main.build_parser().parse_args(["hierarchical-landscape-lookback", "--global-run-id", "global", "--screening-run-id", "screening"])
        self.assertEqual(args.command, "hierarchical-landscape-lookback")

    def test_validator_enriches_candidates_and_publishes_stable_ids(self):
        config = load_lookback_config(CONFIG)
        result = validate_lookback(payload(), schema=build_lookback_schema(snapshot(), config), snapshot=snapshot(), config=config)
        candidate = result["request_results"][0]["candidates"][0]
        self.assertEqual(candidate["paper_title"], "现场调度研究")
        self.assertTrue(candidate["candidate_assignment_id"].startswith("candidate_assignment_"))

    def test_validator_rejects_relevance_outside_request(self):
        value = payload()
        value["request_results"][0]["candidates"][0]["record_id"] = "record-b"
        config = load_lookback_config(CONFIG)
        with self.assertRaisesRegex(AnalysisInputError, "Schema"):
            validate_lookback(value, schema=build_lookback_schema(snapshot(), config), snapshot=snapshot(), config=config)

    def test_validator_rejects_reordered_requests(self):
        value = payload()
        value["request_results"].reverse()
        config = load_lookback_config(CONFIG)
        with self.assertRaisesRegex(AnalysisInputError, "Schema"):
            validate_lookback(value, schema=build_lookback_schema(snapshot(), config), snapshot=snapshot(), config=config)

    def test_no_candidate_requires_reason(self):
        value = payload()
        value["request_results"][1]["candidates"] = []
        value["request_results"][1]["no_match_reason"] = None
        config = load_lookback_config(CONFIG)
        with self.assertRaisesRegex(AnalysisInputError, "Schema"):
            validate_lookback(value, schema=build_lookback_schema(snapshot(), config), snapshot=snapshot(), config=config)

    def test_runner_writes_reviewable_candidate_ledger(self):
        client = FakeClient()
        result = HierarchicalLookbackRunner(
            self.workspace, analysis_client=client, token_counter=FakeCounter(),
            snapshot_loader=lambda workspace, global_run_id, screening_run_id: snapshot(),
        ).run(global_run_id="global", screening_run_id="screening", run_id="lookback-one", model_profile_path=MODEL_PROFILE, lookback_config_path=CONFIG)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"]["unique_candidate_count"], 2)
        self.assertTrue((Path(result["run_dir"]) / "review" / "hierarchical_lookback.md").exists())


if __name__ == "__main__":
    unittest.main()
