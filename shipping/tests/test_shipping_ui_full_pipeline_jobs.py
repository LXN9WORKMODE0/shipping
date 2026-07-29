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

from shipping_ui.command_factory import (
    CardCommandFactory,
    FullPipelineCommandFactory,
)
from shipping_ui.job_repository import JobRepository
from shipping_ui.project_repository import ProjectRepository
from shipping_ui.services.job_service import JobService
from shipping_ui.services.project_service import ProjectService, SourceInput


class ShippingUIFullPipelineJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.relative_root = (
            Path(".test_tmp") / f"ui-full-pipeline-{uuid.uuid4().hex}"
        )
        self.root = PROJECT_ROOT / self.relative_root
        self.workspace = self.relative_root / "workspace"
        (PROJECT_ROOT / self.workspace).mkdir(parents=True)
        self.env_file = self.relative_root / "analysis.env"
        (PROJECT_ROOT / self.env_file).write_text(
            "LLM_ANALYSIS_API_URL=https://example.invalid/v1\n"
            "LLM_ANALYSIS_API_KEY=test-only\n",
            encoding="utf-8",
        )
        self.projects = ProjectRepository(
            PROJECT_ROOT,
            workspace=self.workspace,
            legacy_config_root=self.relative_root / "legacy",
        )
        self.project_service = ProjectService(self.projects)
        self.jobs = JobRepository(PROJECT_ROOT, workspace=self.workspace)
        card_factory = CardCommandFactory(
            PROJECT_ROOT,
            workspace=self.workspace,
        )
        self.service = JobService(
            project_root=PROJECT_ROOT,
            workspace=self.workspace,
            projects=self.projects,
            jobs=self.jobs,
            command_factory=card_factory,
            full_pipeline_command_factory=FullPipelineCommandFactory(
                PROJECT_ROOT,
                workspace=self.workspace,
                python_executable=card_factory.python_executable,
                env_file=self.env_file,
            ),
        )
        self.service.start()

    def tearDown(self) -> None:
        self.service.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_card_failure_blocks_external_stages_and_keeps_auditable_job(self):
        project = self.project_service.create_project(
            project_id="full-pipeline-barrier",
            name="完整流程屏障测试",
            topic="通航调度",
        )
        imported = self.project_service.import_sources(
            "full-pipeline-barrier",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-valid",
                    filename="valid.md",
                    stream=io.BytesIO(
                        (
                            "# paper-valid\n\n"
                            "## 摘要\n\n研究通航调度。\n\n"
                            "## 1 方法\n\n构建调度模型。\n\n"
                            "## 2 结论\n\n模型能够减少等待时间。\n"
                        ).encode("utf-8")
                    ),
                ),
                SourceInput(
                    paper_id="paper-empty",
                    filename="empty.md",
                    stream=io.BytesIO(b"# paper-empty\n"),
                ),
            ],
        )["project"]

        preflight = self.service.preflight_full_pipeline(
            "full-pipeline-barrier",
            expected_revision=imported["revision"],
        )
        self.assertEqual(len(preflight["papers"]), 2)
        self.assertEqual(preflight["pdf_provider"], "none")
        self.assertNotEqual(
            preflight["papers"][0]["paper_id"],
            preflight["papers"][0]["workspace_paper_id"],
        )

        job = self.service.create_full_pipeline_job(
            "full-pipeline-barrier",
            expected_revision=imported["revision"],
            external_service_confirmed=True,
        )
        deadline = time.monotonic() + 30
        current = self.jobs.get(str(job["job_id"]))
        while current["status"] not in {
            "completed",
            "completed_with_failures",
            "cancelled",
            "interrupted",
            "failed",
        }:
            if time.monotonic() >= deadline:
                self.fail("完整流程屏障 Job 未在 30 秒内结束。")
            time.sleep(0.05)
            current = self.jobs.get(str(job["job_id"]))

        self.assertEqual(current["status"], "failed", current)
        self.assertEqual(
            current["failure_code"],
            "pipeline.card_stage_failed",
        )
        self.assertEqual(current["result"]["request_count"], 0)
        self.assertEqual(
            current["result"]["stages"]["topic_briefs"]["status"],
            "pending",
        )
        pipeline_run_id = current["result"]["pipeline_run_id"]
        run_dir = (
            PROJECT_ROOT
            / self.workspace
            / "_pipeline_runs"
            / pipeline_run_id
        )
        self.assertTrue((run_dir / "manifest.json").is_file())
        self.assertFalse(
            (run_dir / "output" / "pipeline_result.json").exists()
        )


if __name__ == "__main__":
    unittest.main()
