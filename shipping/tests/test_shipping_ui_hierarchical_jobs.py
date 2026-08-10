from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_ui.job_repository import (
    HIERARCHICAL_INCREMENTAL_JOB_INPUT_SCHEMA,
    JobRepository,
)
from shipping_ui.services.job_service import JobService


class ShippingUIHierarchicalJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.relative_root = Path(".test_tmp") / f"ui-landscape-{uuid.uuid4().hex}"
        self.root = PROJECT_ROOT / self.relative_root
        self.workspace = self.relative_root / "workspace"
        (PROJECT_ROOT / self.workspace).mkdir(parents=True)
        self.jobs = JobRepository(PROJECT_ROOT, workspace=self.workspace)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_repository_persists_hierarchical_stage_progress(self) -> None:
        job = self.jobs.create({
            "schema_version": HIERARCHICAL_INCREMENTAL_JOB_INPUT_SCHEMA,
            "project_id": "review-one",
            "project_revision": 1,
            "topic": "通航能力",
            "lookback_run_id": "lookback-one",
            "incremental_run_id": "incremental-one",
        })
        self.assertTrue(job["job_id"].startswith("landscape-"))
        self.jobs.transition(job["job_id"], "running")
        stages = [
            {"stage_id": "understanding", "label": "候选论文理解", "status": "completed"},
            {"stage_id": "local", "label": "受影响主题重算", "status": "running"},
            {"stage_id": "global", "label": "全局研究景观", "status": "pending"},
        ]
        updated = self.jobs.set_stage_progress(
            job["job_id"], current_stage="local", stages=stages
        )
        self.assertEqual(updated["progress"]["current_stage"], "local")
        self.assertEqual(updated["progress"]["completed"], 1)
        self.assertEqual(updated["progress"]["succeeded"], 1)
        self.assertEqual(updated["progress"]["stages"], stages)

    def test_stage_status_comes_from_manifest_not_directory_presence(self) -> None:
        service = JobService.__new__(JobService)
        service.workspace = PROJECT_ROOT / self.workspace
        manifest = service.workspace / "stage" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"status":"failed"}\n', encoding="utf-8")
        self.assertEqual(service._hierarchical_stage_status(manifest), "failed")
        manifest.write_text('{"status":"completed_partial"}\n', encoding="utf-8")
        self.assertEqual(service._hierarchical_stage_status(manifest), "completed")


if __name__ == "__main__":
    unittest.main()
