from __future__ import annotations

import io
import shutil
import sys
import time
import unittest
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_ui.artifact_store import ArtifactStore
from shipping_ui.command_factory import CardCommandFactory
from shipping_ui.job_repository import JobRepository
from shipping_ui.project_repository import ProjectRepository
from shipping_ui.services.job_service import JobService
from shipping_ui.services.project_service import ProjectService, SourceInput


class ShippingUICardJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.relative_root = (
            Path(".test_tmp") / f"ui-card-job-{uuid.uuid4().hex}"
        )
        self.workspace_relative = self.relative_root / "workspace"
        self.root = PROJECT_ROOT / self.relative_root
        (PROJECT_ROOT / self.workspace_relative).mkdir(parents=True)
        self.projects = ProjectRepository(
            PROJECT_ROOT,
            workspace=self.workspace_relative,
            legacy_config_root=self.relative_root / "legacy",
        )
        self.project_service = ProjectService(self.projects)
        self.jobs = JobRepository(
            PROJECT_ROOT,
            workspace=self.workspace_relative,
        )
        self.job_service = JobService(
            project_root=PROJECT_ROOT,
            workspace=self.workspace_relative,
            projects=self.projects,
            jobs=self.jobs,
            command_factory=CardCommandFactory(
                PROJECT_ROOT,
                workspace=self.workspace_relative,
            ),
        )
        self.job_service.start()

    def tearDown(self) -> None:
        self.job_service.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_real_markdown_job_publishes_card_and_updates_artifact_store(self):
        project = self.project_service.create_project(
            project_id="card-job-review",
            name="Card Job 测试",
            topic="通航调度",
        )
        imported = self.project_service.import_sources(
            "card-job-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="通航调度研究",
                    filename="paper-one.md",
                    stream=io.BytesIO(
                        (
                            "# 通航调度研究\n\n"
                            "## 摘要\n\n"
                            "本文研究通航调度问题。\n\n"
                            "## 1 方法\n\n"
                            "构建调度模型并比较不同方案。\n\n"
                            "## 2 结论\n\n"
                            "优化方案能够减少等待时间。\n"
                        ).encode("utf-8")
                    ),
                )
            ],
        )["project"]

        job = self.job_service.create_card_job(
            "card-job-review",
            expected_revision=imported["revision"],
            paper_ids=["通航调度研究"],
            pdf_provider="none",
        )
        deadline = time.monotonic() + 20
        current = self.jobs.get(str(job["job_id"]))
        while current["status"] not in {"completed", "failed"}:
            if time.monotonic() >= deadline:
                self.fail("真实 Markdown Card job 未在 20 秒内结束。")
            time.sleep(0.05)
            current = self.jobs.get(str(job["job_id"]))

        self.assertEqual(current["status"], "completed", current)
        self.assertEqual(current["progress"]["succeeded"], 1)
        self.assertEqual(current["paper_results"][0]["status"], "completed")
        self.assertGreater(current["paper_results"][0]["material_count"], 0)
        store = ArtifactStore(
            PROJECT_ROOT,
            workspace=self.workspace_relative,
            project_config_root=self.relative_root / "legacy",
            project_repository=self.projects,
        )
        summary = store.get_project("card-job-review")
        self.assertEqual(summary["paper_count"], 1)
        self.assertEqual(summary["papers"][0]["card"]["status"], "completed")
        self.assertGreater(summary["card_count"], 0)

    def test_reconcile_marks_incomplete_job_failed_without_resuming(self):
        project = self.project_service.create_project(
            project_id="reconcile-review",
            name="中断测试",
            topic="测试主题",
        )
        imported = self.project_service.import_sources(
            "reconcile-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-one",
                    filename="paper-one.md",
                    stream=io.BytesIO(b"# Paper\n\nText."),
                )
            ],
        )["project"]
        frozen = self.job_service.preflight(
            "reconcile-review",
            expected_revision=imported["revision"],
            paper_ids=["paper-one"],
            pdf_provider="none",
        )
        job = self.jobs.create(frozen)
        input_bytes = (
            self.jobs.root / str(job["job_id"]) / "input.json"
        ).read_bytes()
        self.assertNotIn(b"\r\n", input_bytes)

        reconciled = self.jobs.reconcile_incomplete()

        self.assertEqual(reconciled, [job["job_id"]])
        failed = self.jobs.get(str(job["job_id"]))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failure_code"], "ui.job_interrupted")


if __name__ == "__main__":
    unittest.main()
