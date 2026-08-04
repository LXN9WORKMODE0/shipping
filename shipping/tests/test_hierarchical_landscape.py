from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

import main
from shipping_pipeline.hierarchical_landscape import (
    HierarchicalLocalLandscapeBatchRunner,
    HierarchicalLandscapeError,
    HierarchicalLandscapePlanRunner,
    build_hierarchical_plan,
)


TEST_TMP_ROOT = Path(__file__).resolve().parent / ".tmp"


def fake_snapshot():
    sources = tuple(
        SimpleNamespace(
            paper_id=f"paper-{name}",
            paper_title=f"论文{name}",
            run_id=f"understanding-{name}",
        )
        for name in ("a", "b", "c", "d")
    )
    return SimpleNamespace(
        topic="三峡枢纽通航能力的提升方法",
        review_goal="比较不同提升路径的效果与验证边界",
        corpus_scope="targeted_sample",
        collection={
            "schema_version": "llm.research_landscape_collection.v1",
            "topic": "三峡枢纽通航能力的提升方法",
            "review_goal": "比较不同提升路径的效果与验证边界",
            "corpus_scope": "targeted_sample",
            "source_understanding_run_ids": [row.run_id for row in sources],
        },
        sources=sources,
        input_sha256="sha256:source",
    )


def valid_routing():
    return {
        "schema_version": "llm.hierarchical_landscape_routing.v1",
        "topic": "三峡枢纽通航能力的提升方法",
        "review_goal": "比较不同提升路径的效果与验证边界",
        "clusters": [
            {
                "cluster_id": "dispatch",
                "title": "调度优化",
                "question": "调度方法如何提升通航效率？",
                "paper_ids": ["paper-a", "paper-b", "paper-c"],
            },
            {
                "cluster_id": "facility",
                "title": "设施协同",
                "question": "设施协同如何影响通过能力？",
                "paper_ids": ["paper-c", "paper-d"],
            },
        ],
        "unassigned_papers": [],
    }


class HierarchicalLandscapeTest(unittest.TestCase):
    def setUp(self):
        self.workspace = TEST_TMP_ROOT / f"hierarchical_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_cli_exposes_hierarchical_plan(self):
        args = main.build_parser().parse_args([
            "hierarchical-landscape-plan",
            "--collection", "collection.json",
            "--routing", "routing.json",
        ])
        self.assertEqual(args.command, "hierarchical-landscape-plan")
        self.assertEqual(args.max_papers_per_cluster, 40)
        self.assertEqual(args.max_memberships_per_paper, 2)

    def test_plan_generates_local_collections_and_complete_coverage(self):
        routing_path = self.workspace / "routing.json"
        routing_path.write_text(json.dumps(valid_routing(), ensure_ascii=False), encoding="utf-8")
        runner = HierarchicalLandscapePlanRunner(
            self.workspace,
            snapshot_loader=lambda workspace, collection_path: fake_snapshot(),
        )
        result = runner.run(
            collection_path=self.workspace / "collection.json",
            routing_path=routing_path,
            run_id="plan-one",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"]["coverage_ratio"], 1.0)
        self.assertEqual(result["summary"]["multi_cluster_paper_count"], 1)
        root = self.workspace / "_hierarchical_landscapes" / "runs" / "plan-one"
        local = json.loads((root / "local_collections" / "dispatch.json").read_text(encoding="utf-8"))
        self.assertEqual(local["source_understanding_run_ids"], [
            "understanding-a", "understanding-b", "understanding-c"
        ])
        self.assertTrue((root / "audit" / "paper_memberships.jsonl").exists())
        self.assertTrue((root / "input" / "paper_identity_catalog.jsonl").exists())

    def test_runner_records_failed_routing_without_publishing_plan(self):
        routing = valid_routing()
        routing["clusters"][0]["paper_ids"][0] = "展示标题不是paper-id"
        routing_path = self.workspace / "bad-routing.json"
        routing_path.write_text(json.dumps(routing, ensure_ascii=False), encoding="utf-8")
        result = HierarchicalLandscapePlanRunner(
            self.workspace,
            snapshot_loader=lambda workspace, collection_path: fake_snapshot(),
        ).run(
            collection_path=self.workspace / "collection.json",
            routing_path=routing_path,
            run_id="plan-failed",
        )
        self.assertEqual(result["status"], "failed")
        root = self.workspace / "_hierarchical_landscapes" / "runs" / "plan-failed"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["failure"]["error_code"], "hierarchical_plan.paper_unknown")
        self.assertFalse((root / "output" / "hierarchical_plan.json").exists())

    def test_silent_missing_paper_is_rejected(self):
        routing = valid_routing()
        routing["clusters"][1]["paper_ids"] = ["paper-c", "paper-a"]
        with self.assertRaisesRegex(HierarchicalLandscapeError, "silently_missing"):
            build_hierarchical_plan(
                fake_snapshot(), routing,
                max_papers_per_cluster=40,
                max_memberships_per_paper=2,
            )

    def test_explicit_unassigned_paper_closes_coverage(self):
        routing = valid_routing()
        routing["clusters"][1]["paper_ids"] = ["paper-c", "paper-a"]
        routing["unassigned_papers"] = [
            {"paper_id": "paper-d", "reason": "当前主题证据不足，保留回看。"}
        ]
        plan = build_hierarchical_plan(
            fake_snapshot(), routing,
            max_papers_per_cluster=40,
            max_memberships_per_paper=2,
        )
        self.assertEqual(plan["coverage"]["coverage_ratio"], 1.0)
        self.assertEqual(plan["coverage"]["unassigned_paper_count"], 1)

    def test_membership_overflow_is_rejected(self):
        routing = valid_routing()
        routing["clusters"].append({
            "cluster_id": "third",
            "title": "第三主题",
            "question": "第三个问题是什么？",
            "paper_ids": ["paper-a", "paper-c"],
        })
        with self.assertRaisesRegex(HierarchicalLandscapeError, "membership_overflow"):
            build_hierarchical_plan(
                fake_snapshot(), routing,
                max_papers_per_cluster=40,
                max_memberships_per_paper=2,
            )

    def test_local_batch_isolates_failure_and_supports_resume(self):
        routing_path = self.workspace / "routing.json"
        routing_path.write_text(json.dumps(valid_routing(), ensure_ascii=False), encoding="utf-8")
        HierarchicalLandscapePlanRunner(
            self.workspace,
            snapshot_loader=lambda workspace, collection_path: fake_snapshot(),
        ).run(
            collection_path=self.workspace / "collection.json",
            routing_path=routing_path,
            run_id="plan-local",
        )
        first_runner = FakeLandscapeRunner(self.workspace, fail_cluster="facility")
        first = HierarchicalLocalLandscapeBatchRunner(
            self.workspace, landscape_runner=first_runner
        ).run(plan_run_id="plan-local", run_id="local-one")
        self.assertEqual(first["status"], "completed_with_failures")
        self.assertEqual(first["summary"]["completed_cluster_count"], 1)
        second_runner = FakeLandscapeRunner(self.workspace)
        second = HierarchicalLocalLandscapeBatchRunner(
            self.workspace, landscape_runner=second_runner
        ).run(
            plan_run_id="plan-local",
            run_id="local-two",
            resume_from_run_id="local-one",
        )
        self.assertEqual(second["status"], "completed")
        self.assertEqual(len(second_runner.calls), 1)


class FakeLandscapeRunner:
    def __init__(self, workspace: Path, fail_cluster: str | None = None):
        self.workspace = workspace
        self.fail_cluster = fail_cluster
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        collection = json.loads(Path(kwargs["collection_path"]).read_text(encoding="utf-8"))
        cluster_id = Path(kwargs["collection_path"]).stem
        failed = cluster_id == self.fail_cluster
        run_dir = self.workspace / "_research_landscapes" / "runs" / kwargs["run_id"]
        (run_dir / "output").mkdir(parents=True)
        manifest = {
            "schema_version": "llm.research_landscape_run.v1",
            "run_id": kwargs["run_id"],
            "status": "failed" if failed else "completed",
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        if not failed:
            (run_dir / "output" / "research_landscape.json").write_text("{}", encoding="utf-8")
        return {
            "run_id": kwargs["run_id"],
            "status": manifest["status"],
            "landscape_id": f"landscape-{cluster_id}" if not failed else None,
            "coverage": {"source_paper_count": len(collection["source_understanding_run_ids"])} if not failed else None,
            "failure": {"error_code": "test.failure"} if failed else None,
        }


if __name__ == "__main__":
    unittest.main()
