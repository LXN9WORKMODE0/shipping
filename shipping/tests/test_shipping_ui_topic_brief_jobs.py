from __future__ import annotations

import io
import json
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
from shipping_ui.command_factory import (
    CardCommandFactory,
    TopicBriefCommandFactory,
)
from shipping_ui.errors import UIError
from shipping_ui.job_repository import JobRepository
from shipping_ui.project_repository import ProjectRepository
from shipping_ui.services.job_service import JobService
from shipping_ui.services.project_service import ProjectService, SourceInput


class FakeTopicBriefJobService(JobService):
    def _execute_topic_brief_paper(self, job_id, job_input, paper):
        if paper["paper_id"] == "失败论文":
            return {
                "paper_id": paper["paper_id"],
                "workspace_paper_id": paper["workspace_paper_id"],
                "status": "failed",
                "analysis_status": "failed",
                "analysis_run_id": paper["analysis_run_id"],
                "generation_id": paper["generation_id"],
                "started_at": self._now(),
                "finished_at": self._now(),
                "paper_relevance": None,
                "selected_material_count": 0,
                "evidence_unit_count": 0,
                "evidence_failure_count": 0,
                "request_count": 0,
                "usage": {},
                "failure_code": "test.analysis_failed",
                "failure_message": "测试用单篇分析失败。",
            }
        run_id = str(paper["analysis_run_id"])
        run_dir = self.workspace / "_topic_reviews" / "runs" / run_id
        (run_dir / "output").mkdir(parents=True)
        output = {
            "schema_version": "llm.topic_paper_result.v2",
            "paper_id": paper["paper_id"],
            "paper_title": paper["paper_id"],
            "topic": job_input["topic"],
            "paper_relevance": "supporting",
            "scope": {},
            "brief": {},
            "revisit": None,
            "evidence_failures": [],
            "evidence_units": [],
        }
        (run_dir / "output" / "paper_brief.json").write_text(
            json.dumps(output, ensure_ascii=False),
            encoding="utf-8",
        )
        manifest = {
            "schema_version": "llm.topic_review_run.v2",
            "run_id": run_id,
            "status": "completed",
            "started_at": self._now(),
            "finished_at": self._now(),
            "paper_id": paper["paper_id"],
            "paper_title": paper["paper_id"],
            "topic": job_input["topic"],
            "generation_id": paper["generation_id"],
            "input_sha256": paper["input_sha256"],
            "paper_relevance": "supporting",
            "source_material_count": paper["material_count"],
            "selected_material_count": 1,
            "evidence_unit_count": 1,
            "evidence_failure_count": 0,
            "revisit_status": "not_triggered",
            "requests": [{"request_id": "fake", "status": "completed"}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
            "failure_count": 0,
            "failure_codes": [],
            "outputs": {"paper_brief": "output/paper_brief.json"},
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "paper_id": paper["paper_id"],
            "workspace_paper_id": paper["workspace_paper_id"],
            "status": "completed",
            "analysis_status": "completed",
            "analysis_run_id": run_id,
            "generation_id": paper["generation_id"],
            "started_at": self._now(),
            "finished_at": self._now(),
            "paper_relevance": "supporting",
            "selected_material_count": 1,
            "evidence_unit_count": 1,
            "evidence_failure_count": 0,
            "request_count": 1,
            "usage": manifest["usage"],
            "failure_code": None,
            "failure_message": None,
            "cli_result": {
                "run_id": run_id,
                "paper_id": paper["paper_id"],
                "status": "completed",
            },
        }


class ControlledTopicBriefJobService(FakeTopicBriefJobService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def _execute_topic_brief_paper(self, job_id, job_input, paper):
        if paper["paper_id"] == "第一篇":
            self.first_started.set()
            if not self.release_first.wait(timeout=5):
                raise RuntimeError("测试未释放第一篇分析。")
        return super()._execute_topic_brief_paper(
            job_id,
            job_input,
            paper,
        )


class ShippingUITopicBriefJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.relative_root = (
            Path(".test_tmp") / f"ui-topic-job-{uuid.uuid4().hex}"
        )
        self.workspace_relative = self.relative_root / "workspace"
        self.root = PROJECT_ROOT / self.relative_root
        (PROJECT_ROOT / self.workspace_relative).mkdir(parents=True)
        self.env_relative = self.relative_root / "analysis.env"
        (PROJECT_ROOT / self.env_relative).write_text(
            "LLM_ANALYSIS_API_URL=https://example.invalid/v1\n"
            "LLM_ANALYSIS_API_KEY=test-only\n",
            encoding="utf-8",
        )
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
        self.service = FakeTopicBriefJobService(
            project_root=PROJECT_ROOT,
            workspace=self.workspace_relative,
            projects=self.projects,
            jobs=self.jobs,
            command_factory=CardCommandFactory(
                PROJECT_ROOT,
                workspace=self.workspace_relative,
            ),
            topic_brief_command_factory=TopicBriefCommandFactory(
                PROJECT_ROOT,
                workspace=self.workspace_relative,
                env_file=self.env_relative,
            ),
        )
        self.service.start()

    def tearDown(self) -> None:
        self.service.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_topic_job_freezes_isolated_cards_and_publishes_run(self):
        project = self.project_service.create_project(
            project_id="topic-job-review",
            name="分析 Job 测试",
            topic="通航调度",
        )
        project = self.project_service.import_sources(
            "topic-job-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="同名论文",
                    filename="paper.md",
                    stream=io.BytesIO(
                        (
                            "# 同名论文\n\n"
                            "## 摘要\n\n研究通航调度。\n\n"
                            "## 方法\n\n构建调度模型。\n\n"
                            "## 结论\n\n模型减少等待时间。\n"
                        ).encode("utf-8")
                    ),
                )
            ],
        )["project"]
        card_job = self.service.create_card_job(
            "topic-job-review",
            expected_revision=project["revision"],
            paper_ids=["同名论文"],
            pdf_provider="none",
        )
        self._wait(card_job["job_id"])

        corpus = PROJECT_ROOT / self.workspace_relative / "_corpus"
        with (corpus / "papers.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "paper_id": "同名论文",
                        "status": "completed",
                        "generation_id": "other-generation",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        preflight = self.service.preflight_topic_brief(
            "topic-job-review",
            expected_revision=project["revision"],
            paper_ids=["同名论文"],
        )
        self.assertEqual(preflight["model"], "deepseek-v4-pro")
        self.assertTrue(preflight["uses_external_service"])
        self.assertGreater(preflight["papers"][0]["material_count"], 0)
        self.assertIn("--workspace-id", preflight["commands"][0]["argv"])

        with self.assertRaises(UIError) as context:
            self.service.create_topic_brief_job(
                "topic-job-review",
                expected_revision=project["revision"],
                paper_ids=["同名论文"],
                external_service_confirmed=False,
            )
        self.assertEqual(
            context.exception.code,
            "ui.external_service_confirmation_required",
        )

        job = self.service.create_topic_brief_job(
            "topic-job-review",
            expected_revision=project["revision"],
            paper_ids=["同名论文"],
            external_service_confirmed=True,
        )
        completed = self._wait(job["job_id"])
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(completed["paper_results"][0]["request_count"], 1)

        store = ArtifactStore(
            PROJECT_ROOT,
            workspace=self.workspace_relative,
            project_config_root=self.relative_root / "legacy",
            project_repository=self.projects,
        )
        summary = store.get_project("topic-job-review")
        self.assertEqual(summary["revision"], project["revision"] + 1)
        self.assertEqual(summary["papers"][0]["analysis"]["status"], "completed")
        self.assertEqual(summary["evidence_count"], 1)
        run_record = next(
            row
            for row in self.jobs.list_run_records()
            if row["run_id"] == job["job_id"]
        )
        self.assertEqual(run_record["run_type"], "topic_brief")
        self.assertEqual(run_record["request_count"], 1)
        self.assertEqual(run_record["total_tokens"], 15)

    def test_partial_topic_job_publishes_successful_paper(self):
        project = self.project_service.create_project(
            project_id="pt-review",
            name="部分分析测试",
            topic="通航调度",
        )
        project = self.project_service.import_sources(
            "pt-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="同名论文",
                    filename="ok.md",
                    stream=io.BytesIO(
                        (
                            "# 同名论文\n\n"
                            "## 摘要\n\n研究通航调度问题。\n\n"
                            "## 方法\n\n构建调度模型。\n\n"
                            "## 结论\n\n模型减少等待时间。\n"
                        ).encode()
                    ),
                ),
                SourceInput(
                    paper_id="失败论文",
                    filename="fail.md",
                    stream=io.BytesIO(
                        (
                            "# 失败论文\n\n"
                            "## 摘要\n\n研究船闸运行问题。\n\n"
                            "## 1 方法\n\n分析历史数据。\n\n"
                            "## 2 结论\n\n提出运行建议。\n"
                        ).encode()
                    ),
                ),
            ],
        )["project"]
        card_job = self.service.create_card_job(
            "pt-review",
            expected_revision=project["revision"],
            paper_ids=["同名论文", "失败论文"],
            pdf_provider="none",
        )
        completed_card = self._wait(card_job["job_id"])
        self.assertEqual(completed_card["status"], "completed", completed_card)

        job = self.service.create_topic_brief_job(
            "pt-review",
            expected_revision=project["revision"],
            paper_ids=["同名论文", "失败论文"],
            external_service_confirmed=True,
        )
        current = self._wait(job["job_id"])

        self.assertEqual(current["status"], "completed_with_failures")
        self.assertEqual(current["progress"]["succeeded"], 1)
        self.assertEqual(current["progress"]["failed"], 1)
        published = self.projects.get("pt-review")
        self.assertEqual(len(published["analysis_run_ids"]), 1)
        manifest = json.loads(
            (
                PROJECT_ROOT
                / self.workspace_relative
                / "_topic_reviews"
                / "runs"
                / published["analysis_run_ids"][0]
                / "manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["paper_id"], "同名论文")
        summary = ArtifactStore(
            PROJECT_ROOT,
            workspace=self.workspace_relative,
            project_config_root=self.relative_root / "legacy",
            project_repository=self.projects,
            job_repository=self.jobs,
        ).get_project("pt-review")
        by_id = {row["paper_id"]: row for row in summary["papers"]}
        self.assertEqual(by_id["同名论文"]["analysis"]["status"], "completed")
        self.assertEqual(by_id["失败论文"]["analysis"]["status"], "failed")
        self.assertEqual(
            by_id["失败论文"]["analysis"]["failure_codes"],
            ["test.analysis_failed"],
        )

    def test_cancelled_topic_job_publishes_completed_paper_only(self):
        project = self.project_service.create_project(
            project_id="tc-review",
            name="分析取消测试",
            topic="通航调度",
        )
        project = self.project_service.import_sources(
            "tc-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id=paper_id,
                    filename=f"{paper_id}.md",
                    stream=io.BytesIO(
                        (
                            f"# {paper_id}\n\n"
                            "## 摘要\n\n研究通航调度问题。\n\n"
                            "## 方法\n\n构建调度模型。\n\n"
                            "## 结论\n\n模型减少等待时间。\n"
                        ).encode()
                    ),
                )
                for paper_id in ("第一篇", "第二篇")
            ],
        )["project"]
        card_job = self.service.create_card_job(
            "tc-review",
            expected_revision=project["revision"],
            paper_ids=["第一篇", "第二篇"],
            pdf_provider="none",
        )
        completed_card = self._wait(card_job["job_id"])
        self.assertEqual(completed_card["status"], "completed", completed_card)
        self.service.shutdown()
        controlled = ControlledTopicBriefJobService(
            project_root=PROJECT_ROOT,
            workspace=self.workspace_relative,
            projects=self.projects,
            jobs=self.jobs,
            command_factory=CardCommandFactory(
                PROJECT_ROOT,
                workspace=self.workspace_relative,
            ),
            topic_brief_command_factory=TopicBriefCommandFactory(
                PROJECT_ROOT,
                workspace=self.workspace_relative,
                env_file=self.env_relative,
            ),
        )
        self.service = controlled
        controlled.start()
        job = controlled.create_topic_brief_job(
            "tc-review",
            expected_revision=project["revision"],
            paper_ids=["第一篇", "第二篇"],
            external_service_confirmed=True,
        )
        self.assertTrue(controlled.first_started.wait(timeout=5))

        controlled.request_cancel(str(job["job_id"]))
        controlled.release_first.set()
        cancelled = self._wait(job["job_id"])

        self.assertEqual(cancelled["status"], "cancelled", cancelled)
        self.assertEqual(cancelled["progress"]["succeeded"], 1)
        published = self.projects.get("tc-review")
        self.assertEqual(len(published["analysis_run_ids"]), 1)
        manifest = json.loads(
            (
                PROJECT_ROOT
                / self.workspace_relative
                / "_topic_reviews"
                / "runs"
                / published["analysis_run_ids"][0]
                / "manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["paper_id"], "第一篇")
        retry = self.jobs.retry_candidates(str(job["job_id"]))
        self.assertEqual(retry["paper_ids"], ["第二篇"])

    def test_restart_reconciliation_publishes_recorded_analysis(self):
        project = self.project_service.create_project(
            project_id="tr-review",
            name="分析重启对账",
            topic="通航调度",
        )
        project = self.project_service.import_sources(
            "tr-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="对账论文",
                    filename="paper.md",
                    stream=io.BytesIO(
                        (
                            "# 对账论文\n\n"
                            "## 摘要\n\n研究通航调度。\n\n"
                            "## 方法\n\n构建调度模型。\n\n"
                            "## 结论\n\n模型减少等待时间。\n"
                        ).encode()
                    ),
                )
            ],
        )["project"]
        card_job = self.service.create_card_job(
            "tr-review",
            expected_revision=project["revision"],
            paper_ids=["对账论文"],
            pdf_provider="none",
        )
        self.assertEqual(self._wait(card_job["job_id"])["status"], "completed")
        frozen = self.service.preflight_topic_brief(
            "tr-review",
            expected_revision=project["revision"],
            paper_ids=["对账论文"],
        )
        self.service.shutdown()
        job = self.jobs.create(frozen)
        job_id = str(job["job_id"])
        self.jobs.transition(job_id, "running")
        result = self.service._execute_topic_brief_paper(
            job_id,
            frozen,
            frozen["papers"][0],
        )
        self.jobs.record_paper_result(job_id, result)
        restarted = FakeTopicBriefJobService(
            project_root=PROJECT_ROOT,
            workspace=self.workspace_relative,
            projects=self.projects,
            jobs=self.jobs,
            command_factory=CardCommandFactory(
                PROJECT_ROOT,
                workspace=self.workspace_relative,
            ),
            topic_brief_command_factory=TopicBriefCommandFactory(
                PROJECT_ROOT,
                workspace=self.workspace_relative,
                env_file=self.env_relative,
            ),
        )
        self.service = restarted

        reconciled = restarted.start()

        self.assertEqual(reconciled, [job_id])
        self.assertEqual(self.jobs.get(job_id)["status"], "interrupted")
        published = self.projects.get("tr-review")
        self.assertEqual(
            published["analysis_run_ids"],
            [result["analysis_run_id"]],
        )

    def _wait(self, job_id: str) -> dict:
        deadline = time.monotonic() + 20
        current = self.jobs.get(job_id)
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
            current = self.jobs.get(job_id)
        return current


if __name__ == "__main__":
    unittest.main()
