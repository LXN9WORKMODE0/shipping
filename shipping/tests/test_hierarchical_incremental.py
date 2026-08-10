from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

import main
from shipping_pipeline.hierarchical_incremental import (
    INCREMENTAL_RUN_SCHEMA_VERSION,
    HierarchicalIncrementalRunner,
    IncrementalConfig,
    _load_global_retry,
    _load_reusable_adjudications,
    _load_resume_understanding,
    build_adjudication_schema,
    select_promotions,
    validate_adjudications,
)
from shipping_pipeline.llm_analysis import AnalysisInputError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"


def assignments():
    return [
        {
            "look_back_request_id": "request-a", "question": "是否有现场验证？",
            "reason": "决定可推广性。", "target_cluster_ids": ["dispatch"],
            "record_id": "record-a", "paper_id": "paper-a", "paper_title": "现场验证研究",
            "paper_relevance": "core", "h4_matched_reason": "包含现场数据。",
            "h4_expected_use": "验证部署效果。",
        },
        {
            "look_back_request_id": "request-a", "question": "是否有现场验证？",
            "reason": "决定可推广性。", "target_cluster_ids": ["dispatch"],
            "record_id": "record-b", "paper_id": "paper-b", "paper_title": "仿真研究",
            "paper_relevance": "core", "h4_matched_reason": "方法相近。",
            "h4_expected_use": "补充方法背景。",
        },
    ]


def adjudication_payload():
    return {
        "schema_version": "llm.hierarchical_candidate_adjudication.v1",
        "topic": "三峡枢纽通航能力的提升方法",
        "adjudications": [
            {
                "look_back_request_id": "request-a", "record_id": "record-a",
                "support_level": "direct", "confidence": "high",
                "reason": "报告了真实运行数据。", "supported_points": ["现场运行效率提高。"],
            },
            {
                "look_back_request_id": "request-a", "record_id": "record-b",
                "support_level": "partial", "confidence": "high",
                "reason": "只有仿真结果。", "supported_points": ["说明算法机制。"],
            },
        ],
    }


class FakeLandscapeRunner:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "run_id": kwargs["run_id"], "status": "completed",
            "landscape_id": "landscape-new", "coverage": {"source_paper_count": 3},
            "failure": None,
        }


class HierarchicalIncrementalTest(unittest.TestCase):
    def setUp(self):
        self.workspace = TEST_TMP_ROOT / f"incremental_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_cli_exposes_incremental_run(self):
        args = main.build_parser().parse_args([
            "hierarchical-landscape-incremental-run", "--lookback-run-id", "lookback-one"
        ])
        self.assertEqual(args.command, "hierarchical-landscape-incremental-run")

    def test_adjudication_requires_fixed_assignment_order(self):
        schema = build_adjudication_schema("三峡枢纽通航能力的提升方法", assignments())
        value = adjudication_payload()
        value["adjudications"].reverse()
        with self.assertRaisesRegex(AnalysisInputError, "Schema"):
            validate_adjudications(value, schema=schema, assignments=assignments())

    def test_only_direct_medium_or_high_is_promoted(self):
        schema = build_adjudication_schema("三峡枢纽通航能力的提升方法", assignments())
        rows = validate_adjudications(adjudication_payload(), schema=schema, assignments=assignments())
        config = IncrementalConfig("llm.hierarchical_incremental_config.v1", 4, 32768)
        promotions = select_promotions(rows, assignments(), config)
        self.assertEqual([row["record_id"] for row in promotions], ["record-a"])
        self.assertEqual(promotions[0]["target_cluster_ids"], ["dispatch"])

    def test_promotion_limit_is_applied_per_request(self):
        value = adjudication_payload()
        value["adjudications"][1]["support_level"] = "direct"
        value["adjudications"][1]["confidence"] = "medium"
        schema = build_adjudication_schema("三峡枢纽通航能力的提升方法", assignments())
        rows = validate_adjudications(value, schema=schema, assignments=assignments())
        config = IncrementalConfig("llm.hierarchical_incremental_config.v1", 1, 32768)
        self.assertEqual(len(select_promotions(rows, assignments(), config)), 1)

    def test_resume_reuses_only_same_lookback_understanding_batch(self):
        manifest_path = self.workspace / "_hierarchical_incremental_runs" / "runs" / "h5-old" / "manifest.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(json.dumps({
            "schema_version": INCREMENTAL_RUN_SCHEMA_VERSION,
            "status": "completed_partial_no_changes",
            "lookback_run_id": "lookback-one",
            "children": {"understanding_batch_run_id": "understanding-old"},
        }), encoding="utf-8")
        self.assertEqual(
            _load_resume_understanding(
                self.workspace, resume_from_run_id="h5-old", lookback_run_id="lookback-one"
            ),
            "understanding-old",
        )
        with self.assertRaisesRegex(AnalysisInputError, "不一致"):
            _load_resume_understanding(
                self.workspace, resume_from_run_id="h5-old", lookback_run_id="lookback-two"
            )

    def test_failed_global_can_reuse_completed_local_batch(self):
        parent = self.workspace / "_hierarchical_incremental_runs" / "runs" / "h5-failed"
        (parent / "output").mkdir(parents=True)
        (parent / "manifest.json").write_text(json.dumps({
            "schema_version": INCREMENTAL_RUN_SCHEMA_VERSION,
            "status": "failed", "lookback_run_id": "lookback-one",
            "children": {"understanding_batch_run_id": "understanding-one", "local_batch_run_id": "local-one", "global_run_id": "global-failed"},
        }), encoding="utf-8")
        (parent / "output" / "candidate_adjudications.jsonl").write_text(
            json.dumps({"record_id": "record-a"}) + "\n", encoding="utf-8"
        )
        (parent / "output" / "promotions.jsonl").write_text(
            json.dumps({"record_id": "record-a"}) + "\n", encoding="utf-8"
        )
        local = self.workspace / "_hierarchical_landscapes" / "local_runs" / "local-one"
        (local / "output").mkdir(parents=True)
        (local / "manifest.json").write_text(json.dumps({
            "schema_version": "llm.hierarchical_local_landscape_batch_run.v1",
            "status": "completed",
        }), encoding="utf-8")
        (local / "output" / "local_landscape_batch.json").write_text(json.dumps({
            "run_id": "local-one", "records": [{"cluster_id": "dispatch", "local_status": "completed"}]
        }), encoding="utf-8")
        retry = _load_global_retry(
            self.workspace, resume_from_run_id="h5-failed", lookback_run_id="lookback-one"
        )
        self.assertEqual(retry["local_batch_run_id"], "local-one")
        self.assertEqual(retry["promotions"][0]["record_id"], "record-a")

    def test_failed_incomplete_local_falls_back_to_understanding_resume(self):
        parent = self.workspace / "_hierarchical_incremental_runs" / "runs" / "h5-local-failed"
        parent.mkdir(parents=True)
        (parent / "manifest.json").write_text(json.dumps({
            "schema_version": INCREMENTAL_RUN_SCHEMA_VERSION,
            "status": "failed", "lookback_run_id": "lookback-one",
            "children": {"understanding_batch_run_id": "understanding-one", "local_batch_run_id": "local-failed"},
        }), encoding="utf-8")
        local = self.workspace / "_hierarchical_landscapes" / "local_runs" / "local-failed"
        (local / "output").mkdir(parents=True)
        (local / "manifest.json").write_text(json.dumps({
            "schema_version": "llm.hierarchical_local_landscape_batch_run.v1",
            "status": "completed_with_failures",
        }), encoding="utf-8")
        (local / "output" / "local_landscape_batch.json").write_text(json.dumps({
            "run_id": "local-failed", "records": [{"cluster_id": "engineering", "local_status": "failed"}]
        }), encoding="utf-8")
        self.assertIsNone(_load_global_retry(
            self.workspace, resume_from_run_id="h5-local-failed", lookback_run_id="lookback-one"
        ))
        self.assertEqual(_load_resume_understanding(
            self.workspace, resume_from_run_id="h5-local-failed", lookback_run_id="lookback-one"
        ), "understanding-one")

    def test_adjudication_reuse_requires_parent_ledger(self):
        parent = self.workspace / "_hierarchical_incremental_runs" / "runs" / "h5-parent"
        parent.mkdir(parents=True)
        (parent / "manifest.json").write_text(json.dumps({
            "children": {"understanding_batch_run_id": "understanding-parent"}
        }), encoding="utf-8")
        self.assertEqual(_load_reusable_adjudications(
            self.workspace,
            resume_from_run_id="h5-parent",
            assignments=assignments(),
            current_understandings={},
        ), [])

    def test_adjudication_reuse_requires_unchanged_understanding_generation(self):
        parent = self.workspace / "_hierarchical_incremental_runs" / "runs" / "h5-parent"
        (parent / "output").mkdir(parents=True)
        (parent / "manifest.json").write_text(json.dumps({
            "children": {"understanding_batch_run_id": "understanding-parent"}
        }), encoding="utf-8")
        decision = {
            **adjudication_payload()["adjudications"][0],
            "paper_id": "paper-a", "paper_title": "现场验证研究",
            "target_cluster_ids": ["dispatch"],
        }
        (parent / "output" / "candidate_adjudications.jsonl").write_text(
            json.dumps(decision, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        batch = self.workspace / "_paper_understanding_batches" / "runs" / "understanding-parent" / "output"
        batch.mkdir(parents=True)
        (batch / "paper_understanding_batch.json").write_text(json.dumps({
            "records": [{
                "record_id": "record-a", "understanding_status": "understood",
                "understanding_run_id": "understanding-paper-a",
            }]
        }), encoding="utf-8")
        understanding = self.workspace / "_paper_understandings" / "runs" / "understanding-paper-a"
        (understanding / "output").mkdir(parents=True)
        (understanding / "manifest.json").write_text(
            json.dumps({"status": "completed"}), encoding="utf-8"
        )
        (understanding / "output" / "paper_understanding.json").write_text(
            json.dumps({"paper_id": "paper-a"}), encoding="utf-8"
        )

        reused = _load_reusable_adjudications(
            self.workspace, resume_from_run_id="h5-parent",
            assignments=[assignments()[0]],
            current_understandings={
                "record-a": {"run_id": "understanding-paper-a", "understanding": {}}
            },
        )
        self.assertEqual(reused, [decision])
        changed = _load_reusable_adjudications(
            self.workspace, resume_from_run_id="h5-parent",
            assignments=[assignments()[0]],
            current_understandings={
                "record-a": {"run_id": "understanding-paper-a-v2", "understanding": {}}
            },
        )
        self.assertEqual(changed, [])

    def test_local_increment_only_recomputes_affected_cluster(self):
        collections = self.workspace / "collections"
        collections.mkdir()
        baseline_records = []
        for cluster_id in ("baseline", "dispatch"):
            collection_path = collections / f"{cluster_id}.json"
            collection_path.write_text(json.dumps({
                "schema_version": "llm.research_landscape_collection.v1",
                "topic": "三峡枢纽通航能力的提升方法", "review_goal": "形成综述",
                "corpus_scope": "试点", "source_understanding_run_ids": [f"old-{cluster_id}-1", f"old-{cluster_id}-2"],
            }, ensure_ascii=False), encoding="utf-8")
            manifest_path = self.workspace / "_research_landscapes" / "runs" / f"landscape-{cluster_id}" / "manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps({"collection_path": str(collection_path)}), encoding="utf-8")
            baseline_records.append({
                "cluster_id": cluster_id, "cluster_title": cluster_id,
                "paper_count": 2, "local_status": "completed",
                "landscape_run_id": f"landscape-{cluster_id}",
                "landscape_id": f"id-{cluster_id}", "coverage": {}, "failure": None,
            })
        lineage = {
            "lineage": {"baseline_local_batch_run_id": "local-old"},
            "baseline_local": {
                "plan_run_id": "plan-old", "plan_sha256": "sha256:plan",
                "topic": "三峡枢纽通航能力的提升方法", "review_goal": "形成综述",
                "summary": {"source_paper_count": 4, "assigned_paper_count": 4},
                "records": baseline_records,
            },
        }
        fake = FakeLandscapeRunner()
        runner = HierarchicalIncrementalRunner(self.workspace, landscape_runner=fake)
        result = runner._run_incremental_local(
            run_dir=self.workspace / "_hierarchical_incremental_runs" / "runs" / "h5-one",
            local_run_id="h5-one--local", lineage=lineage,
            promotions=[{
                "record_id": "record-a", "target_cluster_ids": ["dispatch"]
            }],
            understandings={"record-a": {"run_id": "understanding-new"}},
            provider="openai-compatible", api_url=None, api_key_env="KEY",
            model_profile_path=PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json",
            landscape_config_path=PROJECT_ROOT / "config" / "research-landscape-default.json",
            timeout=10,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(fake.calls), 1)
        collection = json.loads(Path(fake.calls[0]["collection_path"]).read_text(encoding="utf-8"))
        self.assertIn("understanding-new", collection["source_understanding_run_ids"])
        reused = next(row for row in result["records"] if row["cluster_id"] == "baseline")
        self.assertEqual(reused["reused_from_run_id"], "local-old")


if __name__ == "__main__":
    unittest.main()
