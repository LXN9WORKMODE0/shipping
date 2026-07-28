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

from shipping_ui.errors import UIError
from shipping_ui.job_repository import JobRepository
from shipping_ui.services.run_comparison_service import RunComparisonService


class ShippingUIRunComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.relative_root = (
            Path(".test_tmp") / f"ui-run-comparison-{uuid.uuid4().hex}"
        )
        self.root = PROJECT_ROOT / self.relative_root
        self.workspace = self.relative_root / "workspace"
        (PROJECT_ROOT / self.workspace).mkdir(parents=True)
        self.jobs = JobRepository(PROJECT_ROOT, workspace=self.workspace)
        self.service = RunComparisonService(self.jobs)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_same_frozen_card_input_returns_metric_deltas(self):
        left = self._completed_card_job(
            pdf_provider="none",
            material_count=3,
            issue_count=1,
        )
        right = self._completed_card_job(
            pdf_provider="mineru",
            material_count=4,
            issue_count=0,
        )

        comparison = self.service.compare(
            "comparison-project",
            left_job_id=left,
            right_job_id=right,
        )

        self.assertTrue(comparison["comparable"])
        self.assertEqual(comparison["verdict"], "same_frozen_input")
        self.assertEqual(
            comparison["deltas"]["output"]["material_count"],
            1,
        )
        self.assertEqual(
            comparison["deltas"]["output"]["issue_count"],
            -1,
        )
        self.assertEqual(
            comparison["configuration_differences"][0]["field"],
            "pdf_provider",
        )

    def test_changed_card_generation_blocks_topic_brief_deltas(self):
        left = self.jobs.create(
            self._brief_input("generation-one", "sha256:input-one")
        )
        self.jobs.transition(str(left["job_id"]), "running")
        self.jobs.transition(str(left["job_id"]), "completed")
        right = self.jobs.create(
            self._brief_input("generation-two", "sha256:input-two")
        )
        self.jobs.transition(str(right["job_id"]), "running")
        self.jobs.transition(str(right["job_id"]), "completed")

        comparison = self.service.compare(
            "comparison-project",
            left_job_id=str(left["job_id"]),
            right_job_id=str(right["job_id"]),
        )

        self.assertFalse(comparison["comparable"])
        self.assertEqual(comparison["verdict"], "input_changed")
        self.assertIsNone(comparison["deltas"])
        self.assertEqual(
            {row["field"] for row in comparison["input_differences"]},
            {"generation_id", "input_sha256"},
        )

    def test_different_job_types_are_rejected(self):
        card = self.jobs.create(self._card_input("none"))
        self.jobs.transition(str(card["job_id"]), "running")
        self.jobs.transition(str(card["job_id"]), "completed")
        brief = self.jobs.create(
            self._brief_input("generation-one", "sha256:input-one")
        )

        with self.assertRaises(UIError) as context:
            self.service.compare(
                "comparison-project",
                left_job_id=str(card["job_id"]),
                right_job_id=str(brief["job_id"]),
            )

        self.assertEqual(
            context.exception.code,
            "ui.run_comparison_type_mismatch",
        )

    def _completed_card_job(
        self,
        *,
        pdf_provider: str,
        material_count: int,
        issue_count: int,
    ) -> str:
        job = self.jobs.create(self._card_input(pdf_provider))
        job_id = str(job["job_id"])
        self.jobs.transition(job_id, "running")
        self.jobs.record_paper_result(
            job_id,
            {
                "paper_id": "paper-one",
                "workspace_paper_id": "comparison-project--paper-one",
                "status": "completed",
                "material_count": material_count,
                "issue_count": issue_count,
                "failure_code": None,
            },
        )
        self.jobs.transition(job_id, "completed")
        return job_id

    @staticmethod
    def _card_input(pdf_provider: str) -> dict:
        return {
            "schema_version": "review_ui_card_job_input.v1",
            "project_id": "comparison-project",
            "project_revision": 1,
            "topic": "通航调度",
            "pdf_provider": pdf_provider,
            "papers": [
                {
                    "paper_id": "paper-one",
                    "source_sha256": "sha256:source-one",
                }
            ],
        }

    @staticmethod
    def _brief_input(generation_id: str, input_sha256: str) -> dict:
        return {
            "schema_version": "review_ui_topic_brief_job_input.v1",
            "project_id": "comparison-project",
            "project_revision": 1,
            "topic": "通航调度",
            "model_profile_id": "model-one",
            "model": "deepseek",
            "thinking": {"type": "disabled"},
            "review_config_sha256": "sha256:review-config",
            "papers": [
                {
                    "paper_id": "paper-one",
                    "generation_id": generation_id,
                    "input_sha256": input_sha256,
                    "materials_sha256": "sha256:materials-one",
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
