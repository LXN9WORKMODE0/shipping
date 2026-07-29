from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_ui.job_repository import JobRepository
from shipping_ui.services.governance_service import GovernanceService


class ShippingUIGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / ".test_tmp" / f"ui-governance-{uuid.uuid4().hex}"
        (self.root / "workspace").mkdir(parents=True)
        self.jobs = JobRepository(self.root)
        self._write_configuration()
        self.service = GovernanceService(self.root, self.jobs)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_summary_aggregates_ui_jobs_once(self) -> None:
        brief = self.jobs.create(
            {
                "schema_version": "review_ui_topic_brief_job_input.v1",
                "project_id": "governance-review",
                "project_revision": 1,
                "topic": "测试主题",
                "model_profile_id": "deepseek-v4-pro-official",
                "model": "deepseek-v4-pro",
                "papers": [
                    {"paper_id": "paper-1"},
                    {"paper_id": "paper-2"},
                ],
            }
        )
        self.jobs.transition(brief["job_id"], "running")
        self.jobs.record_paper_result(
            brief["job_id"],
            {
                "paper_id": "paper-1",
                "status": "completed",
                "request_count": 2,
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )
        self.jobs.record_paper_result(
            brief["job_id"],
            {
                "paper_id": "paper-2",
                "status": "failed",
                "request_count": 1,
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
                "failure_code": "provider.timeout",
            },
        )
        self.jobs.transition(brief["job_id"], "completed_with_failures")
        self._set_times(
            brief["job_id"],
            "2026-07-29T01:00:00+00:00",
            "2026-07-29T01:00:15+00:00",
        )

        card = self.jobs.create(
            {
                "schema_version": "review_ui_card_job_input.v1",
                "project_id": "governance-review",
                "project_revision": 1,
                "topic": "测试主题",
                "pdf_provider": "none",
                "papers": [{"paper_id": "paper-3"}],
            }
        )
        self.jobs.request_cancel(card["job_id"])

        summary = self.service.summary()
        totals = summary["totals"]
        self.assertEqual(totals["job_count"], 2)
        self.assertEqual(totals["terminal_job_count"], 2)
        self.assertEqual(totals["request_count"], 3)
        self.assertEqual(totals["prompt_tokens"], 110)
        self.assertEqual(totals["completion_tokens"], 25)
        self.assertEqual(totals["total_tokens"], 135)
        self.assertEqual(totals["duration_seconds"], 15.0)
        self.assertEqual(totals["successful_paper_count"], 1)
        self.assertEqual(totals["failed_paper_count"], 1)
        self.assertEqual(totals["paper_failure_rate"], 0.5)
        self.assertEqual(len(summary["by_model"]), 1)
        self.assertEqual(summary["by_model"][0]["model"], "deepseek-v4-pro")
        self.assertEqual(
            summary["failure_codes"],
            [{"code": "provider.timeout", "count": 1}],
        )

    def test_configuration_health_never_returns_secret_values(self) -> None:
        health = self.service.configuration_health()
        serialized = json.dumps(health, ensure_ascii=False)
        self.assertEqual(health["overall_status"], "ok")
        self.assertNotIn("llm-secret-value", serialized)
        self.assertNotIn("mineru-secret-value", serialized)
        self.assertTrue(all(row["status"] == "ok" for row in health["checks"]))

    def _write_configuration(self) -> None:
        (self.root / "main.py").write_text("pass\n", encoding="utf-8")
        (self.root / ".env").write_text(
            "\n".join(
                [
                    "LLM_ANALYSIS_API_URL=https://llm.invalid/v1",
                    "LLM_ANALYSIS_API_KEY=llm-secret-value",
                    "MINERU_API_KEY=mineru-secret-value",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        model_dir = self.root / "config" / "models"
        model_dir.mkdir(parents=True)
        (model_dir / "deepseek-v4-pro-official.json").write_text(
            json.dumps(
                {
                    "profile_id": "deepseek-v4-pro-official",
                    "request_model": "deepseek-v4-pro",
                    "context_window_tokens": 1_000_000,
                }
            ),
            encoding="utf-8",
        )
        for name in ("topic-review-default.json", "topic-synthesis-default.json"):
            (self.root / "config" / name).write_text(
                '{"schema_version":"test.v1"}\n',
                encoding="utf-8",
            )

    def _set_times(self, job_id: str, started_at: str, finished_at: str) -> None:
        path = self.root / "workspace" / "_ui" / "jobs" / job_id / "job.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["started_at"] = started_at
        payload["finished_at"] = finished_at
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
