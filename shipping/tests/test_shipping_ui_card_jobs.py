from __future__ import annotations

import io
import shutil
import sys
import threading
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
from shipping_ui.services.job_service import JobCancelled, JobService
from shipping_ui.services.project_service import ProjectService, SourceInput


class PartialCardJobService(JobService):
    def _execute_paper(self, job_id, job_input, paper):
        if paper["paper_id"] != "paper-fail":
            return super()._execute_paper(job_id, job_input, paper)
        return {
            "paper_id": paper["paper_id"],
            "workspace_paper_id": paper["workspace_paper_id"],
            "status": "failed",
            "started_at": self._now(),
            "finished_at": self._now(),
            "generation_id": None,
            "structure_quality": None,
            "material_count": 0,
            "issue_count": 0,
            "failure_code": "test.card_failed",
            "failure_message": "测试用单篇失败。",
        }


class ControlledCardJobService(JobService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def _execute_paper(self, job_id, job_input, paper):
        if paper["paper_id"] == "paper-one":
            self.first_started.set()
            if not self.release_first.wait(timeout=5):
                raise RuntimeError("测试未释放第一篇论文。")
        return {
            "paper_id": paper["paper_id"],
            "workspace_paper_id": paper["workspace_paper_id"],
            "status": "completed",
            "started_at": self._now(),
            "finished_at": self._now(),
            "generation_id": f"generation-{paper['paper_id']}",
            "structure_quality": "green",
            "material_count": 1,
            "issue_count": 0,
            "failure_code": None,
            "failure_message": None,
        }


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
        self.job_service = PartialCardJobService(
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
        while current["status"] not in {
            "completed",
            "completed_with_failures",
            "cancelled",
            "interrupted",
            "failed",
        }:
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

    def test_partial_card_job_keeps_successful_paper_usable(self):
        project = self.project_service.create_project(
            project_id="pc-review",
            name="部分 Card 测试",
            topic="通航调度",
        )
        imported = self.project_service.import_sources(
            "pc-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="通航调度研究",
                    filename="paper-ok.md",
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
                ),
                SourceInput(
                    paper_id="paper-fail",
                    filename="paper-fail.md",
                    stream=io.BytesIO(b"# Paper Fail\n\nContent."),
                ),
            ],
        )["project"]

        job = self.job_service.create_card_job(
            "pc-review",
            expected_revision=imported["revision"],
            paper_ids=["通航调度研究", "paper-fail"],
            pdf_provider="none",
        )
        current = self._wait(job["job_id"])

        self.assertEqual(
            current["status"],
            "completed_with_failures",
            {"job": current, "log": self.jobs.read_log(job["job_id"])},
        )
        self.assertEqual(current["progress"]["succeeded"], 1)
        self.assertEqual(current["progress"]["failed"], 1)
        summary = ArtifactStore(
            PROJECT_ROOT,
            workspace=self.workspace_relative,
            project_config_root=self.relative_root / "legacy",
            project_repository=self.projects,
        ).get_project("pc-review")
        by_id = {row["paper_id"]: row for row in summary["papers"]}
        self.assertEqual(
            by_id["通航调度研究"]["card"]["status"], "completed"
        )
        self.assertNotEqual(
            by_id["paper-fail"]["card"]["status"], "completed"
        )

    def test_reconcile_marks_incomplete_job_interrupted_without_resuming(self):
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
        interrupted = self.jobs.get(str(job["job_id"]))
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(
            interrupted["failure_code"], "ui.job_interrupted"
        )

    def test_cancel_keeps_completed_paper_and_skips_remaining_papers(self):
        self.job_service.shutdown()
        controlled = ControlledCardJobService(
            project_root=PROJECT_ROOT,
            workspace=self.workspace_relative,
            projects=self.projects,
            jobs=self.jobs,
            command_factory=CardCommandFactory(
                PROJECT_ROOT,
                workspace=self.workspace_relative,
            ),
        )
        self.job_service = controlled
        controlled.start()
        project = self.project_service.create_project(
            project_id="cancel-review",
            name="取消测试",
            topic="测试主题",
        )
        imported = self.project_service.import_sources(
            "cancel-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-one",
                    filename="one.md",
                    stream=io.BytesIO(b"# One\n\nText."),
                ),
                SourceInput(
                    paper_id="paper-two",
                    filename="two.md",
                    stream=io.BytesIO(b"# Two\n\nText."),
                ),
            ],
        )["project"]
        job = controlled.create_card_job(
            "cancel-review",
            expected_revision=imported["revision"],
            paper_ids=["paper-one", "paper-two"],
            pdf_provider="none",
        )
        self.assertTrue(controlled.first_started.wait(timeout=5))

        requested = controlled.request_cancel(str(job["job_id"]))
        self.assertEqual(requested["status"], "cancel_requested")
        controlled.release_first.set()
        current = self._wait(job["job_id"])

        self.assertEqual(current["status"], "cancelled", current)
        self.assertEqual(current["progress"]["completed"], 1)
        self.assertEqual(current["progress"]["succeeded"], 1)
        self.assertEqual(current["progress"]["failed"], 0)
        self.assertEqual(
            [row["paper_id"] for row in current["paper_results"]],
            ["paper-one"],
        )
        retry = self.jobs.retry_candidates(str(job["job_id"]))
        self.assertEqual(retry["paper_ids"], ["paper-two"])

    def test_queued_cancel_is_terminal_and_retryable(self):
        project = self.project_service.create_project(
            project_id="queued-cancel",
            name="排队取消测试",
            topic="测试主题",
        )
        imported = self.project_service.import_sources(
            "queued-cancel",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-one",
                    filename="one.md",
                    stream=io.BytesIO(b"# One\n\nText."),
                )
            ],
        )["project"]
        frozen = self.job_service.preflight(
            "queued-cancel",
            expected_revision=imported["revision"],
            paper_ids=["paper-one"],
            pdf_provider="none",
        )
        job = self.jobs.create(frozen)

        cancelled = self.job_service.request_cancel(str(job["job_id"]))

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(
            self.jobs.retry_candidates(str(job["job_id"]))["paper_ids"],
            ["paper-one"],
        )

    def test_reconcile_finishes_pending_cancel_as_cancelled(self):
        project = self.project_service.create_project(
            project_id="cancel-reconcile",
            name="取消对账测试",
            topic="测试主题",
        )
        imported = self.project_service.import_sources(
            "cancel-reconcile",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-one",
                    filename="one.md",
                    stream=io.BytesIO(b"# One\n\nText."),
                )
            ],
        )["project"]
        frozen = self.job_service.preflight(
            "cancel-reconcile",
            expected_revision=imported["revision"],
            paper_ids=["paper-one"],
            pdf_provider="none",
        )
        job = self.jobs.create(frozen)
        self.jobs.transition(str(job["job_id"]), "running")
        self.jobs.request_cancel(str(job["job_id"]))

        reconciled = self.jobs.reconcile_incomplete()

        self.assertEqual(reconciled, [job["job_id"]])
        self.assertEqual(
            self.jobs.get(str(job["job_id"]))["status"],
            "cancelled",
        )

    def test_cancel_terminates_registered_child_process(self):
        project = self.project_service.create_project(
            project_id="process-cancel",
            name="子进程取消测试",
            topic="测试主题",
        )
        imported = self.project_service.import_sources(
            "process-cancel",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-one",
                    filename="one.md",
                    stream=io.BytesIO(b"# One\n\nText."),
                )
            ],
        )["project"]
        frozen = self.job_service.preflight(
            "process-cancel",
            expected_revision=imported["revision"],
            paper_ids=["paper-one"],
            pdf_provider="none",
        )
        job = self.jobs.create(frozen)
        job_id = str(job["job_id"])
        self.jobs.transition(job_id, "running")
        errors: list[Exception] = []

        def run_child() -> None:
            try:
                self.job_service._run_process(
                    job_id,
                    (
                        str(
                            self.job_service.command_factory
                            .python_executable
                        ),
                        "-c",
                        (
                            "import time; "
                            "print('started', flush=True); "
                            "time.sleep(30)"
                        ),
                    ),
                    log_prefix="测试进程",
                )
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=run_child)
        thread.start()
        deadline = time.monotonic() + 5
        while job_id not in self.job_service._active_processes:
            if time.monotonic() >= deadline:
                self.fail("测试子进程未登记。")
            time.sleep(0.01)

        self.job_service.request_cancel(job_id)
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], JobCancelled)
        self.job_service._finish_cancelled(job_id)
        self.assertEqual(self.jobs.get(job_id)["status"], "cancelled")

    def _wait(self, job_id: str) -> dict:
        deadline = time.monotonic() + 20
        current = self.jobs.get(str(job_id))
        while current["status"] not in {
            "completed",
            "completed_with_failures",
            "cancelled",
            "interrupted",
            "failed",
        }:
            if time.monotonic() >= deadline:
                self.fail(f"Job 未在 20 秒内结束：{job_id}")
            time.sleep(0.05)
            current = self.jobs.get(str(job_id))
        return current


if __name__ == "__main__":
    unittest.main()
