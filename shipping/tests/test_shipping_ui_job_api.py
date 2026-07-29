from __future__ import annotations

import json
import shutil
import sys
import time
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_ui.app import create_app


class ShippingUIJobAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.relative_root = Path(".test_tmp") / f"ui-job-api-{uuid.uuid4().hex}"
        self.workspace = self.relative_root / "workspace"
        (PROJECT_ROOT / self.workspace).mkdir(parents=True)
        self.analysis_env = self.relative_root / "analysis.env"
        (PROJECT_ROOT / self.analysis_env).write_text(
            "LLM_ANALYSIS_API_URL=https://example.invalid/v1\n"
            "LLM_ANALYSIS_API_KEY=test-only\n",
            encoding="utf-8",
        )
        self.client = TestClient(
            create_app(
                PROJECT_ROOT,
                workspace=self.workspace,
                legacy_config_root=self.relative_root / "legacy",
                topic_brief_env_file=self.analysis_env,
            )
        )
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        shutil.rmtree(PROJECT_ROOT / self.relative_root, ignore_errors=True)

    def test_http_card_job_runs_real_markdown_to_completion(self):
        created = self.client.post(
            "/api/projects",
            json={
                "project_id": "http-card-review",
                "name": "HTTP Card Job",
                "topic": "通航调度",
            },
        ).json()
        imported = self.client.post(
            "/api/projects/http-card-review/sources",
            data={
                "paper_ids": json.dumps(["通航调度研究"]),
                "expected_revision": str(created["revision"]),
            },
            files={
                "files": (
                    "通航调度研究.md",
                    (
                        "# 通航调度研究\n\n"
                        "## 摘要\n\n研究通航调度方法。\n\n"
                        "## 1 方法\n\n构建模型并比较方案。\n\n"
                        "## 2 结论\n\n优化方案减少等待时间。\n"
                    ).encode("utf-8"),
                    "text/markdown",
                )
            },
        ).json()["summary"]
        request = {
            "expected_revision": imported["revision"],
            "paper_ids": ["通航调度研究"],
            "pdf_provider": "none",
        }

        preflight = self.client.post(
            "/api/projects/http-card-review/card-jobs/preflight",
            json=request,
        )
        self.assertEqual(preflight.status_code, 200)
        self.assertFalse(preflight.json()["uses_external_service"])

        created_job = self.client.post(
            "/api/projects/http-card-review/card-jobs",
            json=request,
        )
        self.assertEqual(created_job.status_code, 200)
        job_id = created_job.json()["job_id"]
        deadline = time.monotonic() + 20
        job = self.client.get(f"/api/jobs/{job_id}").json()
        while job["status"] not in {
            "completed",
            "completed_with_failures",
            "cancelled",
            "interrupted",
            "failed",
        }:
            if time.monotonic() >= deadline:
                self.fail("HTTP Card job 未在 20 秒内结束。")
            time.sleep(0.05)
            job = self.client.get(f"/api/jobs/{job_id}").json()

        self.assertEqual(job["status"], "completed", job)
        project = self.client.get(
            "/api/projects/http-card-review"
        ).json()
        self.assertEqual(project["papers"][0]["card"]["status"], "completed")
        self.assertGreater(project["card_count"], 0)
        log = self.client.get(f"/api/jobs/{job_id}/log").json()
        self.assertTrue(any("Job 完成" in line for line in log["lines"]))
        runs = self.client.get("/api/runs").json()
        card_run = next(row for row in runs if row["run_id"] == job_id)
        self.assertEqual(card_run["run_type"], "card_build")
        self.assertEqual(card_run["status"], "completed")
        retry = self.client.get(
            f"/api/jobs/{job_id}/retry-candidates"
        )
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["paper_ids"], [])

        topic_request = {
            "expected_revision": imported["revision"],
            "paper_ids": ["通航调度研究"],
            "external_service_confirmed": False,
        }
        topic_preflight = self.client.post(
            "/api/projects/http-card-review/topic-brief-jobs/preflight",
            json=topic_request,
        )
        self.assertEqual(topic_preflight.status_code, 200)
        self.assertTrue(topic_preflight.json()["uses_external_service"])
        self.assertGreater(
            topic_preflight.json()["papers"][0]["material_count"],
            0,
        )
        rejected = self.client.post(
            "/api/projects/http-card-review/topic-brief-jobs",
            json=topic_request,
        )
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(
            rejected.json()["error_code"],
            "ui.external_service_confirmation_required",
        )


if __name__ == "__main__":
    unittest.main()
