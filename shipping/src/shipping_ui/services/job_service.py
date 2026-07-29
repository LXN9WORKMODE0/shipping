from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from shipping_pipeline.llm_analysis import AnalysisInputError, create_input_snapshot
from shipping_pipeline.topic_synthesis import (
    create_topic_synthesis_snapshot_from_payload,
)

from ..command_factory import (
    CardCommandFactory,
    FullPipelineCommandFactory,
    TopicBriefCommandFactory,
    TopicSynthesisCommandFactory,
)
from ..errors import UIError
from ..job_repository import (
    CARD_JOB_INPUT_SCHEMA,
    FULL_PIPELINE_JOB_INPUT_SCHEMA,
    TOPIC_BRIEF_JOB_INPUT_SCHEMA,
    TOPIC_SYNTHESIS_JOB_INPUT_SCHEMA,
    JobRepository,
)
from ..project_repository import PROJECT_SCHEMA, ProjectRepository


SUPPORTED_CARD_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}


class JobCancelled(Exception):
    pass


class JobService:
    def __init__(
        self,
        *,
        project_root: str | Path,
        projects: ProjectRepository,
        jobs: JobRepository,
        command_factory: CardCommandFactory,
        topic_brief_command_factory: TopicBriefCommandFactory | None = None,
        topic_synthesis_command_factory: TopicSynthesisCommandFactory | None = None,
        full_pipeline_command_factory: FullPipelineCommandFactory | None = None,
        workspace: str | Path = "workspace",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = self._inside(self.project_root, workspace)
        self.projects = projects
        self.jobs = jobs
        self.command_factory = command_factory
        self.topic_brief_command_factory = (
            topic_brief_command_factory
            or TopicBriefCommandFactory(
                self.project_root,
                workspace=workspace,
                python_executable=command_factory.python_executable,
            )
        )
        self.topic_synthesis_command_factory = (
            topic_synthesis_command_factory
            or TopicSynthesisCommandFactory(
                self.project_root,
                workspace=workspace,
                python_executable=command_factory.python_executable,
            )
        )
        self.full_pipeline_command_factory = (
            full_pipeline_command_factory
            or FullPipelineCommandFactory(
                self.project_root,
                workspace=workspace,
                python_executable=command_factory.python_executable,
            )
        )
        self._executor: ThreadPoolExecutor | None = None
        self._lifecycle_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._active_processes: dict[str, subprocess.Popen[str]] = {}

    def start(self) -> list[str]:
        with self._lifecycle_lock:
            if self._executor is not None:
                return []
            self._reconcile_analysis_results()
            reconciled = self.jobs.reconcile_incomplete()
            self._executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="shipping-ui-job",
            )
            return reconciled

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.request_cancel(job_id)
        if job["status"] == "cancelled":
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] 排队中的 Job 已取消。",
            )
            return job
        self.jobs.append_log(
            job_id,
            f"[{self._now()}] 已记录取消请求，正在停止当前子进程。",
        )
        with self._process_lock:
            process = self._active_processes.get(job_id)
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except OSError as exc:
                    self.jobs.append_log(
                        job_id,
                        f"[{self._now()}] 子进程终止请求失败：{exc}",
                    )
        return self.jobs.get(job_id)

    def _reconcile_analysis_results(self) -> None:
        for job in self.jobs.list():
            if (
                job["status"] != "running"
                or job["job_type"] != "topic_brief"
                or not job["paper_results"]
            ):
                continue
            try:
                job_input = self.jobs.get_input(str(job["job_id"]))
                published = self._publish_topic_brief_results(
                    job_input,
                    list(job["paper_results"]),
                )
                self.jobs.append_log(
                    str(job["job_id"]),
                    f"[{self._now()}] 重启对账已发布 "
                    f"{len(job['paper_results'])} 个逐论文结果，"
                    f"revision={published['revision']}。",
                )
            except UIError as exc:
                self.jobs.append_log(
                    str(job["job_id"]),
                    f"[{self._now()}] 重启对账未能发布逐论文结果："
                    f"{exc.code}: {exc}",
                )

    def _begin_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job["status"] == "cancelled":
            return False
        if job["status"] == "cancel_requested":
            self.jobs.transition(job_id, "cancelled")
            return False
        self.jobs.transition(job_id, "running")
        return True

    def _raise_if_cancel_requested(self, job_id: str) -> None:
        if self.jobs.get(job_id)["status"] == "cancel_requested":
            raise JobCancelled()

    def _finish_cancelled(self, job_id: str) -> None:
        current = self.jobs.get(job_id)
        if current["status"] == "cancel_requested":
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] Job 已取消；已完成论文结果保持有效。",
            )
            self.jobs.transition(job_id, "cancelled")

    def preflight(
        self,
        project_id: str,
        *,
        expected_revision: int,
        paper_ids: list[str],
        pdf_provider: str,
    ) -> dict[str, Any]:
        project = self.projects.get(project_id)
        if project["schema_version"] != PROJECT_SCHEMA:
            raise UIError(
                "ui.project_migration_required",
                "旧版只读项目必须先迁移，才能创建 Job。",
            )
        if project.get("archived_at"):
            raise UIError(
                "ui.project_archived",
                "项目已归档，只能读取；恢复后才能创建 Job。",
            )
        if project["revision"] != expected_revision:
            raise UIError(
                "ui.project_revision_conflict",
                f"项目已被修改：期望 revision {expected_revision}，"
                f"当前为 {project['revision']}。",
            )
        normalized_ids = [str(value).strip() for value in paper_ids]
        if not normalized_ids or any(not value for value in normalized_ids):
            raise UIError(
                "ui.job_papers_empty",
                "至少需要选择一篇论文。",
            )
        if len(normalized_ids) != len(set(normalized_ids)):
            raise UIError(
                "ui.job_paper_duplicate",
                "Card job 不能重复选择同一论文。",
            )
        if pdf_provider not in {"none", "mineru"}:
            raise UIError(
                "ui.pdf_provider_invalid",
                f"PDF provider 不受支持：{pdf_provider}",
            )
        papers_by_id = {
            str(row["paper_id"]): row for row in project["papers"]
        }
        missing = [
            paper_id
            for paper_id in normalized_ids
            if paper_id not in papers_by_id
        ]
        if missing:
            raise UIError(
                "ui.job_paper_not_found",
                f"项目中不存在所选论文：{', '.join(missing)}",
            )
        collection_relative = project.get("current_collection_path")
        if not collection_relative:
            raise UIError(
                "ui.collection_missing",
                "项目没有当前 collection，不能创建 Job。",
            )
        collection_path = self._inside(
            self.projects.project_dir(project_id),
            str(collection_relative),
        )
        collection_sha = self._sha256(collection_path)
        selected: list[dict[str, Any]] = []
        commands: list[dict[str, Any]] = []
        for paper_id in normalized_ids:
            paper = papers_by_id[paper_id]
            source_path = self.projects.source_path(project_id, paper)
            suffix = source_path.suffix.lower()
            if suffix not in SUPPORTED_CARD_SUFFIXES:
                raise UIError(
                    "ui.source_type_unsupported",
                    f"不支持的来源格式：{source_path.name}",
                )
            if suffix == ".pdf" and pdf_provider != "mineru":
                raise UIError(
                    "ui.pdf_provider_required",
                    f"PDF 必须显式使用 MinerU：{paper_id}",
                )
            current_sha, current_size = self.projects.file_identity(source_path)
            expected_sha = str(paper["source"].get("sha256", ""))
            if current_sha != expected_sha:
                raise UIError(
                    "ui.source_identity_changed",
                    f"来源文件哈希已变化：{paper_id}",
                )
            workspace_paper_id = str(
                paper.get("workspace_paper_id", paper_id)
            )
            command = self.command_factory.build(
                paper_id=paper_id,
                workspace_paper_id=workspace_paper_id,
                source=source_path,
                topic=str(project["topic"]),
                pdf_provider=pdf_provider,
            )
            selected.append(
                {
                    "paper_id": paper_id,
                    "workspace_paper_id": workspace_paper_id,
                    "source_path": str(source_path),
                    "source_sha256": current_sha,
                    "source_size_bytes": current_size,
                    "source_suffix": suffix,
                }
            )
            commands.append(
                {
                    "paper_id": paper_id,
                    "argv": command.public_argv(),
                }
            )
        return {
            "schema_version": CARD_JOB_INPUT_SCHEMA,
            "project_id": project_id,
            "project_revision": project["revision"],
            "topic": project["topic"],
            "collection_path": str(collection_path),
            "collection_sha256": collection_sha,
            "pdf_provider": pdf_provider,
            "uses_external_service": any(
                row["source_suffix"] == ".pdf" for row in selected
            ),
            "papers": selected,
            "commands": commands,
            "created_at": self._now(),
        }

    def create_card_job(
        self,
        project_id: str,
        *,
        expected_revision: int,
        paper_ids: list[str],
        pdf_provider: str,
    ) -> dict[str, Any]:
        job_input = self.preflight(
            project_id,
            expected_revision=expected_revision,
            paper_ids=paper_ids,
            pdf_provider=pdf_provider,
        )
        self.start()
        job = self.jobs.create(job_input)
        executor = self._executor
        if executor is None:
            raise UIError(
                "ui.job_executor_unavailable",
                "Job 执行器未启动。",
            )
        try:
            executor.submit(self._execute_card_job, str(job["job_id"]))
        except RuntimeError as exc:
            self.jobs.transition(
                str(job["job_id"]),
                "failed",
                failure_code="ui.job_submit_failed",
                failure_message=str(exc),
            )
            raise UIError(
                "ui.job_submit_failed",
                f"Job 无法提交：{exc}",
            ) from exc
        return job

    def preflight_topic_brief(
        self,
        project_id: str,
        *,
        expected_revision: int,
        paper_ids: list[str],
    ) -> dict[str, Any]:
        project = self.projects.get(project_id)
        if project["schema_version"] != PROJECT_SCHEMA:
            raise UIError(
                "ui.project_migration_required",
                "旧版只读项目必须先迁移，才能创建 Job。",
            )
        if project.get("archived_at"):
            raise UIError(
                "ui.project_archived",
                "项目已归档，只能读取；恢复后才能创建 Job。",
            )
        if project["revision"] != expected_revision:
            raise UIError(
                "ui.project_revision_conflict",
                f"项目已被修改：期望 revision {expected_revision}，"
                f"当前为 {project['revision']}。",
            )
        normalized_ids = [str(value).strip() for value in paper_ids]
        if not normalized_ids or any(not value for value in normalized_ids):
            raise UIError("ui.job_papers_empty", "至少需要选择一篇论文。")
        if len(normalized_ids) != len(set(normalized_ids)):
            raise UIError(
                "ui.job_paper_duplicate",
                "分析 Job 不能重复选择同一论文。",
            )
        papers_by_id = {
            str(row["paper_id"]): row for row in project["papers"]
        }
        missing = [
            paper_id
            for paper_id in normalized_ids
            if paper_id not in papers_by_id
        ]
        if missing:
            raise UIError(
                "ui.job_paper_not_found",
                f"项目中不存在所选论文：{', '.join(missing)}",
            )
        self._require_analysis_environment()
        profile = self._read_json(
            self.topic_brief_command_factory.model_profile,
            code="ui.model_profile_invalid",
        )
        review_config = self._read_json(
            self.topic_brief_command_factory.review_config,
            code="ui.review_config_invalid",
        )
        if profile.get("provider") != "openai-compatible":
            raise UIError(
                "ui.model_profile_invalid",
                "M2.3 只接受 openai-compatible 模型配置。",
            )
        selected: list[dict[str, Any]] = []
        commands: list[dict[str, Any]] = []
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        for index, paper_id in enumerate(normalized_ids, start=1):
            paper = papers_by_id[paper_id]
            workspace_paper_id = str(
                paper.get("workspace_paper_id", paper_id)
            )
            frozen = self._freeze_card_input(
                paper_id,
                workspace_paper_id,
                str(project["topic"]),
            )
            run_id = (
                f"ui-brief-{timestamp}-{uuid4().hex[:8]}-{index:03d}"
            )
            command = self.topic_brief_command_factory.build(
                paper_id=paper_id,
                workspace_paper_id=workspace_paper_id,
                topic=str(project["topic"]),
                run_id=run_id,
            )
            selected.append(
                {
                    "paper_id": paper_id,
                    "workspace_paper_id": workspace_paper_id,
                    "analysis_run_id": run_id,
                    **frozen,
                }
            )
            commands.append(
                {
                    "paper_id": paper_id,
                    "analysis_run_id": run_id,
                    "argv": command.public_argv(),
                }
            )
        return {
            "schema_version": TOPIC_BRIEF_JOB_INPUT_SCHEMA,
            "project_id": project_id,
            "project_revision": project["revision"],
            "topic": project["topic"],
            "uses_external_service": True,
            "external_service": "DeepSeek（OpenAI 兼容接口）",
            "model_profile_id": profile.get("profile_id"),
            "model": profile.get("request_model"),
            "thinking": profile.get("thinking"),
            "model_profile_path": str(
                self.topic_brief_command_factory.model_profile
            ),
            "model_profile_sha256": self._sha256(
                self.topic_brief_command_factory.model_profile
            ),
            "review_config_path": str(
                self.topic_brief_command_factory.review_config
            ),
            "review_config_sha256": self._sha256(
                self.topic_brief_command_factory.review_config
            ),
            "review_config_schema": review_config.get("schema_version"),
            "papers": selected,
            "commands": commands,
            "created_at": self._now(),
        }

    def create_topic_brief_job(
        self,
        project_id: str,
        *,
        expected_revision: int,
        paper_ids: list[str],
        external_service_confirmed: bool,
    ) -> dict[str, Any]:
        if not external_service_confirmed:
            raise UIError(
                "ui.external_service_confirmation_required",
                "必须明确确认将所选 Card 内容发送给外部 DeepSeek 服务。",
            )
        job_input = self.preflight_topic_brief(
            project_id,
            expected_revision=expected_revision,
            paper_ids=paper_ids,
        )
        job_input["external_service_confirmed"] = True
        job_input["external_service_confirmed_at"] = self._now()
        self.start()
        job = self.jobs.create(job_input)
        executor = self._executor
        if executor is None:
            raise UIError(
                "ui.job_executor_unavailable",
                "Job 执行器未启动。",
            )
        try:
            executor.submit(
                self._execute_topic_brief_job,
                str(job["job_id"]),
            )
        except RuntimeError as exc:
            self.jobs.transition(
                str(job["job_id"]),
                "failed",
                failure_code="ui.job_submit_failed",
                failure_message=str(exc),
            )
            raise UIError(
                "ui.job_submit_failed",
                f"Job 无法提交：{exc}",
            ) from exc
        return job

    def preflight_topic_synthesis(
        self,
        project_id: str,
        *,
        expected_revision: int,
        source_run_ids: list[str],
    ) -> dict[str, Any]:
        project = self.projects.get(project_id)
        if project["schema_version"] != PROJECT_SCHEMA:
            raise UIError(
                "ui.project_migration_required",
                "旧版只读项目必须先迁移，才能创建 Job。",
            )
        if project.get("archived_at"):
            raise UIError(
                "ui.project_archived",
                "项目已归档，只能读取；恢复后才能创建 Job。",
            )
        if project["revision"] != expected_revision:
            raise UIError(
                "ui.project_revision_conflict",
                f"项目已被修改：期望 revision {expected_revision}，"
                f"当前为 {project['revision']}。",
            )
        normalized = [str(value).strip() for value in source_run_ids]
        if len(normalized) < 2 or any(not value for value in normalized):
            raise UIError(
                "ui.synthesis_sources_insufficient",
                "跨论文综合至少需要显式选择两个单篇分析 run。",
            )
        if len(normalized) != len(set(normalized)):
            raise UIError(
                "ui.synthesis_source_duplicate",
                "跨论文综合不能重复选择同一 run。",
            )
        configured = {str(value) for value in project["analysis_run_ids"]}
        unknown = [value for value in normalized if value not in configured]
        if unknown:
            raise UIError(
                "ui.synthesis_source_not_in_project",
                "所选 run 不属于项目当前分析索引：" + ", ".join(unknown),
            )
        self._require_analysis_environment(
            self.topic_synthesis_command_factory.env_path
        )
        collection = {
            "schema_version": "llm.topic_synthesis_collection.v1",
            "topic": project["topic"],
            "source_run_ids": normalized,
        }
        try:
            snapshot = create_topic_synthesis_snapshot_from_payload(
                self.workspace,
                collection,
            )
        except Exception as exc:
            raise UIError(
                "ui.synthesis_input_invalid",
                f"所选单篇 run 无法形成综合快照：{exc}",
            ) from exc
        profile = self._read_json(
            self.topic_synthesis_command_factory.model_profile,
            code="ui.model_profile_invalid",
        )
        config = self._read_json(
            self.topic_synthesis_command_factory.synthesis_config,
            code="ui.synthesis_config_invalid",
        )
        run_id = (
            "ui-synthesis-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            + "-"
            + uuid4().hex[:8]
        )
        return {
            "schema_version": TOPIC_SYNTHESIS_JOB_INPUT_SCHEMA,
            "project_id": project_id,
            "project_revision": project["revision"],
            "topic": project["topic"],
            "uses_external_service": True,
            "external_service": "DeepSeek（OpenAI 兼容接口）",
            "model_profile_id": profile.get("profile_id"),
            "model": profile.get("request_model"),
            "thinking": profile.get("thinking"),
            "model_profile_sha256": self._sha256(
                self.topic_synthesis_command_factory.model_profile
            ),
            "synthesis_config_schema": config.get("schema_version"),
            "synthesis_config_sha256": self._sha256(
                self.topic_synthesis_command_factory.synthesis_config
            ),
            "synthesis_run_id": run_id,
            "collection": collection,
            "collection_sha256": snapshot.collection_sha256,
            "input_sha256": snapshot.input_sha256,
            "source_paper_count": len(snapshot.sources),
            "source_evidence_count": len(snapshot.evidence_units),
            "source_runs": [row.record() for row in snapshot.sources],
            "created_at": self._now(),
        }

    def create_topic_synthesis_job(
        self,
        project_id: str,
        *,
        expected_revision: int,
        source_run_ids: list[str],
        external_service_confirmed: bool,
    ) -> dict[str, Any]:
        if not external_service_confirmed:
            raise UIError(
                "ui.external_service_confirmation_required",
                "必须明确确认将所选 Evidence 发送给外部 DeepSeek 服务。",
            )
        job_input = self.preflight_topic_synthesis(
            project_id,
            expected_revision=expected_revision,
            source_run_ids=source_run_ids,
        )
        job_input["external_service_confirmed"] = True
        job_input["external_service_confirmed_at"] = self._now()
        self.start()
        job = self.jobs.create(job_input)
        self.jobs.write_json_artifact(
            str(job["job_id"]),
            "input/synthesis_collection.json",
            job_input["collection"],
        )
        executor = self._executor
        if executor is None:
            raise UIError(
                "ui.job_executor_unavailable",
                "Job 执行器未启动。",
            )
        try:
            executor.submit(
                self._execute_topic_synthesis_job,
                str(job["job_id"]),
            )
        except RuntimeError as exc:
            self.jobs.transition(
                str(job["job_id"]),
                "failed",
                failure_code="ui.job_submit_failed",
                failure_message=str(exc),
            )
            raise UIError(
                "ui.job_submit_failed",
                f"Job 无法提交：{exc}",
            ) from exc
        return job

    def preflight_full_pipeline(
        self,
        project_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        project = self.projects.get(project_id)
        if project["schema_version"] != PROJECT_SCHEMA:
            raise UIError(
                "ui.project_migration_required",
                "旧版只读项目必须先迁移，才能创建 Job。",
            )
        if project.get("archived_at"):
            raise UIError(
                "ui.project_archived",
                "项目已归档，只能读取；恢复后才能创建 Job。",
            )
        if project["revision"] != expected_revision:
            raise UIError(
                "ui.project_revision_conflict",
                f"项目已被修改：期望 revision {expected_revision}，"
                f"当前为 {project['revision']}。",
            )
        if len(project["papers"]) < 2:
            raise UIError(
                "ui.full_pipeline_papers_insufficient",
                "完整流程至少需要两篇论文。",
            )
        papers: list[dict[str, Any]] = []
        collection_papers: list[dict[str, str]] = []
        has_pdf = False
        for paper in project["papers"]:
            paper_id = str(paper["paper_id"])
            workspace_paper_id = str(
                paper.get("workspace_paper_id") or paper_id
            )
            source_path = self.projects.source_path(project_id, paper)
            suffix = source_path.suffix.lower()
            if suffix not in SUPPORTED_CARD_SUFFIXES:
                raise UIError(
                    "ui.source_type_unsupported",
                    f"完整流程不支持来源类型：{source_path.name}",
                )
            if not source_path.is_file():
                raise UIError(
                    "ui.source_missing",
                    f"来源文件不存在：{source_path}",
                )
            has_pdf = has_pdf or suffix == ".pdf"
            source_sha256 = self._sha256(source_path)
            papers.append(
                {
                    "paper_id": paper_id,
                    "workspace_paper_id": workspace_paper_id,
                    "source_path": str(source_path),
                    "source_sha256": source_sha256,
                    "source_size_bytes": source_path.stat().st_size,
                    "source_suffix": suffix,
                }
            )
            collection_papers.append(
                {
                    "paper_id": paper_id,
                    "workspace_paper_id": workspace_paper_id,
                    "source": str(source_path),
                }
            )
        self._require_analysis_environment(
            self.full_pipeline_command_factory.env_path
        )
        profile = self._read_json(
            self.full_pipeline_command_factory.model_profile,
            code="ui.model_profile_invalid",
        )
        review_config = self._read_json(
            self.full_pipeline_command_factory.review_config,
            code="ui.review_config_invalid",
        )
        synthesis_config = self._read_json(
            self.full_pipeline_command_factory.synthesis_config,
            code="ui.synthesis_config_invalid",
        )
        collection = {
            "schema_version": "review_pipeline_collection.v1",
            "topic": project["topic"],
            "papers": collection_papers,
        }
        collection_bytes = (
            json.dumps(collection, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        run_id = (
            "ui-pipeline-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            + "-"
            + uuid4().hex[:8]
        )
        return {
            "schema_version": FULL_PIPELINE_JOB_INPUT_SCHEMA,
            "project_id": project_id,
            "project_revision": project["revision"],
            "topic": project["topic"],
            "uses_external_service": True,
            "external_services": (
                ["MinerU", "DeepSeek"] if has_pdf else ["DeepSeek"]
            ),
            "model_profile_id": profile.get("profile_id"),
            "model": profile.get("request_model"),
            "thinking": profile.get("thinking"),
            "model_profile_sha256": self._sha256(
                self.full_pipeline_command_factory.model_profile
            ),
            "review_config_schema": review_config.get("schema_version"),
            "review_config_sha256": self._sha256(
                self.full_pipeline_command_factory.review_config
            ),
            "synthesis_config_schema": synthesis_config.get(
                "schema_version"
            ),
            "synthesis_config_sha256": self._sha256(
                self.full_pipeline_command_factory.synthesis_config
            ),
            "pipeline_run_id": run_id,
            "pdf_provider": "mineru" if has_pdf else "none",
            "collection": collection,
            "collection_sha256": (
                "sha256:" + hashlib.sha256(collection_bytes).hexdigest()
            ),
            "papers": papers,
            "created_at": self._now(),
        }

    def create_full_pipeline_job(
        self,
        project_id: str,
        *,
        expected_revision: int,
        external_service_confirmed: bool,
    ) -> dict[str, Any]:
        if not external_service_confirmed:
            raise UIError(
                "ui.external_service_confirmation_required",
                "必须明确确认完整流程会将 PDF 发送给 MinerU（如有），"
                "并将 Card/Evidence 发送给 DeepSeek。",
            )
        job_input = self.preflight_full_pipeline(
            project_id,
            expected_revision=expected_revision,
        )
        job_input["external_service_confirmed"] = True
        job_input["external_service_confirmed_at"] = self._now()
        self.start()
        job = self.jobs.create(job_input)
        self.jobs.write_json_artifact(
            str(job["job_id"]),
            "input/pipeline_collection.json",
            job_input["collection"],
        )
        executor = self._executor
        if executor is None:
            raise UIError(
                "ui.job_executor_unavailable",
                "Job 执行器未启动。",
            )
        try:
            executor.submit(
                self._execute_full_pipeline_job,
                str(job["job_id"]),
            )
        except RuntimeError as exc:
            self.jobs.transition(
                str(job["job_id"]),
                "failed",
                failure_code="ui.job_submit_failed",
                failure_message=str(exc),
            )
            raise UIError(
                "ui.job_submit_failed",
                f"Job 无法提交：{exc}",
            ) from exc
        return job

    def _execute_full_pipeline_job(self, job_id: str) -> None:
        job_input: dict[str, Any] | None = None
        try:
            job_input = self.jobs.get_input(job_id)
            if not self._begin_job(job_id):
                return
            project = self.projects.get(str(job_input["project_id"]))
            if project["revision"] != job_input["project_revision"]:
                raise UIError(
                    "ui.project_revision_conflict",
                    "执行前项目 revision 与冻结输入不一致。",
                )
            for paper in job_input["papers"]:
                source_path = Path(str(paper["source_path"]))
                if self._sha256(source_path) != paper["source_sha256"]:
                    raise UIError(
                        "ui.source_identity_changed",
                        f"执行前来源哈希变化：{paper['paper_id']}",
                    )
            collection_path = (
                self.jobs.root
                / job_id
                / "input"
                / "pipeline_collection.json"
            )
            if self._sha256(collection_path) != job_input["collection_sha256"]:
                raise UIError(
                    "ui.full_pipeline_collection_changed",
                    "完整流程 collection 字节哈希与冻结输入不一致。",
                )
            command = self.full_pipeline_command_factory.build(
                collection=collection_path,
                run_id=str(job_input["pipeline_run_id"]),
                pdf_provider=str(job_input["pdf_provider"]),
            )
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] 完整流程开始："
                f"{len(job_input['papers'])} 篇论文，"
                f"PDF provider={job_input['pdf_provider']}。",
            )
            payload, return_code = self._run_process(
                job_id,
                command.argv,
                log_prefix="完整流程",
            )
            self._raise_if_cancel_requested(job_id)
            result = self._verify_full_pipeline_artifact(
                job_input,
                payload,
            )
            self.jobs.record_result(job_id, result)
            if return_code != 0 or result["status"] != "completed":
                if result["completed_card_count"] > 0:
                    self.projects.publish_pipeline_runs(
                        str(job_input["project_id"]),
                        expected_revision=int(job_input["project_revision"]),
                        analysis_run_ids=[],
                        synthesis_run_id=None,
                    )
                raise UIError(
                    str(
                        result["failure_codes"][0]
                        if result["failure_codes"]
                        else "ui.full_pipeline_process_exit_nonzero"
                    ),
                    f"完整流程未通过，退出码 {return_code}。",
                )
            published = self.projects.publish_pipeline_runs(
                str(job_input["project_id"]),
                expected_revision=int(job_input["project_revision"]),
                analysis_run_ids=list(result["analysis_run_ids"]),
                synthesis_run_id=str(result["synthesis_run_id"]),
            )
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] 完整流程完成并发布："
                f"{result['included_paper_count']} 篇纳入，"
                f"revision={published['revision']}。",
            )
            self.jobs.transition(job_id, "completed")
        except JobCancelled:
            self._finish_cancelled(job_id)
        except Exception as exc:
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] 完整流程 Job 异常："
                f"{type(exc).__name__}: {exc}",
            )
            if self.jobs.get(job_id)["status"] == "cancel_requested":
                self._finish_cancelled(job_id)
                return
            current = self.jobs.get(job_id)
            if current["status"] in {"queued", "running"}:
                code = (
                    exc.code
                    if isinstance(exc, UIError)
                    else "ui.full_pipeline_job_execution_failed"
                )
                if current.get("result") is None and job_input is not None:
                    self.jobs.record_result(
                        job_id,
                        {
                            "status": "failed",
                            "pipeline_run_id": job_input.get(
                                "pipeline_run_id"
                            ),
                            "paper_count": len(job_input.get("papers", [])),
                            "included_paper_count": 0,
                            "excluded_paper_count": 0,
                            "completed_card_count": 0,
                            "analysis_run_ids": [],
                            "synthesis_run_id": None,
                            "request_count": 0,
                            "usage": {},
                            "stages": {},
                            "failure_codes": [code],
                            "failure_code": code,
                            "failure_message": str(exc),
                        },
                    )
                self.jobs.transition(
                    job_id,
                    "failed",
                    failure_code=code,
                    failure_message=f"{type(exc).__name__}: {exc}",
                )

    def _execute_topic_synthesis_job(self, job_id: str) -> None:
        job_input: dict[str, Any] | None = None
        payload: dict[str, Any] | None = None
        try:
            job_input = self.jobs.get_input(job_id)
            if not self._begin_job(job_id):
                return
            project = self.projects.get(str(job_input["project_id"]))
            if project["revision"] != job_input["project_revision"]:
                raise UIError(
                    "ui.project_revision_conflict",
                    "执行前项目 revision 与冻结输入不一致。",
                )
            snapshot = create_topic_synthesis_snapshot_from_payload(
                self.workspace,
                job_input["collection"],
            )
            if (
                snapshot.collection_sha256 != job_input["collection_sha256"]
                or snapshot.input_sha256 != job_input["input_sha256"]
                or [row.record() for row in snapshot.sources]
                != job_input["source_runs"]
            ):
                raise UIError(
                    "ui.synthesis_input_changed",
                    "执行前单篇 run 内容或哈希与冻结输入不一致。",
                )
            collection_path = (
                self.jobs.root
                / job_id
                / "input"
                / "synthesis_collection.json"
            )
            command = self.topic_synthesis_command_factory.build(
                collection=collection_path,
                run_id=str(job_input["synthesis_run_id"]),
            )
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] 开始跨论文综合："
                f"{snapshot.sources.__len__()} 篇，"
                f"{len(snapshot.evidence_units)} 条 Evidence。",
            )
            process_payload, return_code = self._run_process(
                job_id,
                command.argv,
                log_prefix="综合",
            )
            payload = process_payload if isinstance(process_payload, dict) else None
            self._raise_if_cancel_requested(job_id)
            if return_code != 0:
                child_codes = (
                    payload.get("failure_codes", [])
                    if payload is not None
                    else []
                )
                child_code = (
                    str(child_codes[0])
                    if child_codes
                    else "ui.topic_synthesis_process_exit_nonzero"
                )
                raise UIError(
                    child_code,
                    f"llm-topic-synthesis 退出码为 {return_code}；"
                    f"底层失败：{', '.join(map(str, child_codes)) or '未报告'}。",
                )
            result = self._verify_topic_synthesis_artifact(
                job_input,
                payload,
            )
            self.jobs.record_result(job_id, result)
            published = self.projects.set_synthesis_run_id(
                str(job_input["project_id"]),
                expected_revision=int(job_input["project_revision"]),
                synthesis_run_id=str(job_input["synthesis_run_id"]),
            )
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] 综合完成并发布："
                f"{result['theme_count']} 主题，"
                f"{result['section_count']} 章节，"
                f"revision={published['revision']}。",
            )
            self.jobs.transition(job_id, "completed")
        except JobCancelled:
            self._finish_cancelled(job_id)
        except Exception as exc:
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] 跨论文综合 Job 异常："
                f"{type(exc).__name__}: {exc}",
            )
            if self.jobs.get(job_id)["status"] == "cancel_requested":
                self._finish_cancelled(job_id)
                return
            if job_input is not None:
                try:
                    self.projects.set_synthesis_run_id(
                        str(job_input["project_id"]),
                        expected_revision=int(job_input["project_revision"]),
                        synthesis_run_id=None,
                    )
                except UIError:
                    pass
            current = self.jobs.get(job_id)
            if current["status"] in {"queued", "running"}:
                code = exc.code if isinstance(exc, UIError) else (
                    "ui.topic_synthesis_job_execution_failed"
                )
                if current.get("result") is None and job_input is not None:
                    self.jobs.record_result(
                        job_id,
                        {
                            "status": "failed",
                            "synthesis_run_id": job_input.get(
                                "synthesis_run_id"
                            ),
                            "source_paper_count": job_input.get(
                                "source_paper_count", 0
                            ),
                            "source_evidence_count": job_input.get(
                                "source_evidence_count", 0
                            ),
                            "theme_count": int(
                                payload.get("theme_count", 0)
                                if payload is not None
                                else 0
                            ),
                            "synthesis_unit_count": int(
                                payload.get("synthesis_unit_count", 0)
                                if payload is not None
                                else 0
                            ),
                            "section_count": int(
                                payload.get("section_count", 0)
                                if payload is not None
                                else 0
                            ),
                            "request_count": int(
                                payload.get("request_count", 0)
                                if payload is not None
                                else 0
                            ),
                            "usage": dict(
                                payload.get("usage", {})
                                if payload is not None
                                else {}
                            ),
                            "failure_code": code,
                            "failure_message": str(exc),
                        },
                    )
                self.jobs.transition(
                    job_id,
                    "failed",
                    failure_code=code,
                    failure_message=f"{type(exc).__name__}: {exc}",
                )

    def _execute_topic_brief_job(self, job_id: str) -> None:
        try:
            job_input = self.jobs.get_input(job_id)
            if not self._begin_job(job_id):
                return
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] 单篇分析 Job 开始，共 "
                f"{len(job_input['papers'])} 篇论文。",
            )
            project = self.projects.get(str(job_input["project_id"]))
            if project["revision"] != job_input["project_revision"]:
                raise UIError(
                    "ui.project_revision_conflict",
                    "执行前项目 revision 与冻结输入不一致。",
                )
            results: list[dict[str, Any]] = []
            failure_codes: list[str] = []
            for paper in job_input["papers"]:
                self._raise_if_cancel_requested(job_id)
                paper_id = str(paper["paper_id"])
                self.jobs.set_current_paper(job_id, paper_id)
                result = self._execute_topic_brief_paper(
                    job_id,
                    job_input,
                    paper,
                )
                results.append(result)
                self.jobs.record_paper_result(job_id, result)
                if result["status"] != "completed":
                    failure_codes.append(str(result["failure_code"]))
                self._raise_if_cancel_requested(job_id)
            published = self._publish_topic_brief_results(job_input, results)
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] 项目分析索引已发布，revision="
                f"{published['revision']}。",
            )
            if failure_codes:
                succeeded = sum(
                    row["status"] == "completed" for row in results
                )
                status = (
                    "completed_with_failures" if succeeded else "failed"
                )
                self.jobs.append_log(
                    job_id,
                    f"[{self._now()}] 单篇分析 Job "
                    f"{'部分完成' if succeeded else '失败'}："
                    f"成功 {succeeded} 篇，失败 {len(failure_codes)} 篇。",
                )
                self.jobs.transition(
                    job_id,
                    status,
                    failure_code="ui.topic_brief_job_paper_failed",
                    failure_message=(
                        f"成功 {succeeded} 篇，失败 {len(failure_codes)} 篇："
                        + ", ".join(failure_codes)
                    ),
                )
            else:
                self.jobs.append_log(
                    job_id,
                    f"[{self._now()}] 单篇分析 Job 完成，"
                    "全部验证通过的 run 已发布。",
                )
                self.jobs.transition(job_id, "completed")
        except JobCancelled:
            current = self.jobs.get(job_id)
            if current["paper_results"]:
                try:
                    self._publish_topic_brief_results(
                        self.jobs.get_input(job_id),
                        list(current["paper_results"]),
                    )
                except UIError as exc:
                    self.jobs.append_log(
                        job_id,
                        f"[{self._now()}] 取消收束未能发布已完成分析："
                        f"{exc.code}: {exc}",
                    )
            self._finish_cancelled(job_id)
        except Exception as exc:
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] 单篇分析 Job 异常："
                f"{type(exc).__name__}: {exc}",
            )
            if self.jobs.get(job_id)["status"] == "cancel_requested":
                self._finish_cancelled(job_id)
                return
            current = self.jobs.get(job_id)
            if current["status"] in {"queued", "running"}:
                code = exc.code if isinstance(exc, UIError) else (
                    "ui.topic_brief_job_execution_failed"
                )
                self.jobs.transition(
                    job_id,
                    "failed",
                    failure_code=code,
                    failure_message=f"{type(exc).__name__}: {exc}",
                )

    def _execute_topic_brief_paper(
        self,
        job_id: str,
        job_input: dict[str, Any],
        paper: dict[str, Any],
    ) -> dict[str, Any]:
        started_at = self._now()
        paper_id = str(paper["paper_id"])
        workspace_paper_id = str(paper["workspace_paper_id"])
        try:
            current = self._freeze_card_input(
                paper_id,
                workspace_paper_id,
                str(job_input["topic"]),
            )
        except UIError as exc:
            return self._topic_paper_failure(
                paper,
                started_at,
                exc.code,
                str(exc),
            )
        frozen_keys = (
            "generation_id",
            "input_sha256",
            "run_sha256",
            "current_sha256",
            "materials_sha256",
            "material_count",
        )
        if any(current[key] != paper[key] for key in frozen_keys):
            return self._topic_paper_failure(
                paper,
                started_at,
                "ui.analysis_input_changed",
                "执行前 Card generation、数量或内容哈希与冻结输入不一致。",
            )
        command = self.topic_brief_command_factory.build(
            paper_id=paper_id,
            workspace_paper_id=workspace_paper_id,
            topic=str(job_input["topic"]),
            run_id=str(paper["analysis_run_id"]),
        )
        self.jobs.append_log(
            job_id,
            f"[{self._now()}] 开始分析：{paper_id}，"
            f"generation={paper['generation_id']}，"
            f"Card={paper['material_count']}。",
        )
        try:
            payload, return_code = self._run_process(
                job_id,
                command.argv,
                log_prefix=paper_id,
            )
        except JobCancelled:
            raise
        except UIError as exc:
            return self._topic_paper_failure(
                paper,
                started_at,
                "ui.topic_brief_process_failed",
                f"{type(exc).__name__}: {exc}",
            )
        if return_code != 0:
            return self._topic_paper_failure(
                paper,
                started_at,
                "ui.topic_brief_process_exit_nonzero",
                f"llm-topic-brief 退出码为 {return_code}。",
                payload=payload,
            )
        try:
            artifact = self._verify_topic_brief_artifact(
                paper,
                str(job_input["topic"]),
                payload,
            )
        except UIError as exc:
            return self._topic_paper_failure(
                paper,
                started_at,
                exc.code,
                str(exc),
                payload=payload,
            )
        self.jobs.append_log(
            job_id,
            f"[{self._now()}] 完成分析：{paper_id}，"
            f"status={artifact['analysis_status']}，"
            f"Evidence={artifact['evidence_unit_count']}，"
            f"tokens={artifact['usage'].get('total_tokens', 0)}。",
        )
        return {
            "paper_id": paper_id,
            "workspace_paper_id": workspace_paper_id,
            "status": "completed",
            "analysis_status": artifact["analysis_status"],
            "analysis_run_id": paper["analysis_run_id"],
            "generation_id": paper["generation_id"],
            "started_at": started_at,
            "finished_at": self._now(),
            "paper_relevance": artifact["paper_relevance"],
            "selected_material_count": artifact["selected_material_count"],
            "evidence_unit_count": artifact["evidence_unit_count"],
            "evidence_failure_count": artifact["evidence_failure_count"],
            "request_count": artifact["request_count"],
            "usage": artifact["usage"],
            "failure_code": None,
            "failure_message": None,
            "cli_result": payload,
        }

    def _verify_topic_brief_artifact(
        self,
        paper: dict[str, Any],
        topic: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if payload is None:
            raise UIError(
                "ui.topic_brief_result_missing",
                "llm-topic-brief 未输出机器可读结果。",
            )
        run_id = str(paper["analysis_run_id"])
        run_dir = self._inside(
            self.workspace,
            Path("_topic_reviews") / "runs" / run_id,
        )
        manifest = self._read_json(
            run_dir / "manifest.json",
            code="ui.topic_brief_manifest_invalid",
        )
        allowed_statuses = {
            "completed",
            "completed_with_failures",
            "completed_with_revisit_failure",
            "excluded",
        }
        status = str(manifest.get("status") or "")
        if (
            manifest.get("schema_version") != "llm.topic_review_run.v2"
            or manifest.get("run_id") != run_id
            or manifest.get("paper_id") != paper["paper_id"]
            or manifest.get("topic") != topic
            or manifest.get("generation_id") != paper["generation_id"]
            or manifest.get("input_sha256") != paper["input_sha256"]
            or status not in allowed_statuses
        ):
            raise UIError(
                "ui.topic_brief_manifest_mismatch",
                "单篇分析 manifest 与冻结输入或允许状态不一致。",
            )
        if (
            payload.get("run_id") != run_id
            or payload.get("paper_id") != paper["paper_id"]
            or payload.get("status") != status
        ):
            raise UIError(
                "ui.topic_brief_result_mismatch",
                "CLI 输出与单篇分析 manifest 不一致。",
            )
        output_relative = (manifest.get("outputs") or {}).get("paper_brief")
        if not isinstance(output_relative, str) or not output_relative:
            raise UIError(
                "ui.topic_brief_output_missing",
                "单篇分析 manifest 缺少 paper_brief 输出。",
            )
        output = self._read_json(
            self._inside(run_dir, output_relative),
            code="ui.topic_brief_output_invalid",
        )
        if (
            output.get("schema_version") != "llm.topic_paper_result.v2"
            or output.get("paper_id") != paper["paper_id"]
            or output.get("topic") != topic
        ):
            raise UIError(
                "ui.topic_brief_output_mismatch",
                "单篇分析输出与冻结论文身份或主题不一致。",
            )
        requests = manifest.get("requests")
        usage = manifest.get("usage")
        if not isinstance(requests, list) or not isinstance(usage, dict):
            raise UIError(
                "ui.topic_brief_audit_missing",
                "单篇分析 manifest 缺少请求或 token 审计。",
            )
        return {
            "analysis_status": status,
            "paper_relevance": manifest.get("paper_relevance"),
            "selected_material_count": int(
                manifest.get("selected_material_count", 0)
            ),
            "evidence_unit_count": int(
                manifest.get("evidence_unit_count", 0)
            ),
            "evidence_failure_count": int(
                manifest.get("evidence_failure_count", 0)
            ),
            "request_count": len(requests),
            "usage": usage,
        }

    def _publish_topic_brief_results(
        self,
        job_input: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        project_id = str(job_input["project_id"])
        project = self.projects.get(project_id)
        selected_ids = {str(row["paper_id"]) for row in results}
        retained: list[str] = []
        for run_id in project["analysis_run_ids"]:
            manifest = self._read_json(
                self._inside(
                    self.workspace,
                    Path("_topic_reviews") / "runs" / str(run_id) / "manifest.json",
                ),
                code="ui.topic_brief_manifest_invalid",
            )
            if str(manifest.get("paper_id")) not in selected_ids:
                retained.append(str(run_id))
        successful = [
            str(row["analysis_run_id"])
            for row in results
            if row["status"] == "completed"
        ]
        return self.projects.set_analysis_run_ids(
            project_id,
            expected_revision=int(job_input["project_revision"]),
            analysis_run_ids=[*retained, *successful],
        )

    def _run_process(
        self,
        job_id: str,
        argv: tuple[str, ...],
        *,
        log_prefix: str,
    ) -> tuple[dict[str, Any] | None, int]:
        payload: dict[str, Any] | None = None
        creationflags = (
            subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                argv,
                cwd=self.project_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="strict",
                creationflags=creationflags,
            )
            with self._process_lock:
                self._active_processes[job_id] = process
            if self.jobs.get(job_id)["status"] == "cancel_requested":
                process.terminate()
            if process.stdout is None:
                raise UIError(
                    "ui.job_stdout_missing",
                    "子进程没有可读取的标准输出。",
                )
            with process.stdout:
                for line in process.stdout:
                    clean = line.rstrip("\r\n")
                    self.jobs.append_log(
                        job_id,
                        f"[{log_prefix}] {clean}",
                    )
                    try:
                        candidate = json.loads(clean)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict):
                        payload = candidate
            return_code = process.wait()
            self._raise_if_cancel_requested(job_id)
            return payload, return_code
        except JobCancelled:
            raise
        except (OSError, UnicodeError, UIError) as exc:
            raise UIError(
                "ui.job_process_failed",
                f"{type(exc).__name__}: {exc}",
            ) from exc
        finally:
            with self._process_lock:
                if (
                    process is not None
                    and self._active_processes.get(job_id) is process
                ):
                    self._active_processes.pop(job_id, None)

    def _verify_full_pipeline_artifact(
        self,
        job_input: dict[str, Any],
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        run_id = str(job_input["pipeline_run_id"])
        run_dir = self._inside(
            self.workspace,
            Path("_pipeline_runs") / run_id,
        )
        manifest = self._read_json(
            run_dir / "manifest.json",
            code="ui.full_pipeline_manifest_invalid",
        )
        if (
            manifest.get("schema_version") != "review_pipeline_run.v1"
            or manifest.get("run_id") != run_id
            or manifest.get("topic") != job_input["topic"]
            or manifest.get("collection_sha256")
            != job_input["collection_sha256"]
        ):
            raise UIError(
                "ui.full_pipeline_manifest_mismatch",
                "完整流程 manifest 的身份、主题或 collection 哈希不一致。",
            )
        if payload is not None and (
            payload.get("run_id") != run_id
            or payload.get("status") != manifest.get("status")
        ):
            raise UIError(
                "ui.full_pipeline_result_mismatch",
                "完整流程 CLI 输出与 manifest 不一致。",
            )
        expected_papers = {
            str(row["paper_id"]): str(row["workspace_paper_id"])
            for row in job_input["papers"]
        }
        card_runs = manifest.get("card_runs")
        brief_runs = manifest.get("topic_brief_runs")
        if not isinstance(card_runs, list) or not isinstance(brief_runs, list):
            raise UIError(
                "ui.full_pipeline_manifest_mismatch",
                "完整流程 manifest 缺少阶段运行列表。",
            )
        actual_papers = {
            str(row.get("paper_id")): str(
                row.get("workspace_paper_id") or row.get("paper_id")
            )
            for row in card_runs
        }
        if actual_papers != expected_papers:
            raise UIError(
                "ui.full_pipeline_manifest_mismatch",
                "完整流程 Card 论文身份与冻结输入不一致。",
            )
        status = str(manifest.get("status") or "failed")
        synthesis = manifest.get("topic_synthesis_run")
        synthesis_run_id = (
            str(synthesis.get("run_id"))
            if isinstance(synthesis, dict) and synthesis.get("run_id")
            else None
        )
        if status == "completed":
            if not (run_dir / "output" / "pipeline_result.json").is_file():
                raise UIError(
                    "ui.full_pipeline_output_missing",
                    "完整流程完成但没有正式 pipeline_result.json。",
                )
            if not synthesis_run_id:
                raise UIError(
                    "ui.full_pipeline_manifest_mismatch",
                    "完整流程完成但缺少综合 run ID。",
                )
        usage = manifest.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        failure_codes = manifest.get("failure_codes")
        if not isinstance(failure_codes, list):
            failure_codes = []
        return {
            "status": status,
            "pipeline_run_id": run_id,
            "paper_count": int(manifest.get("paper_count", len(card_runs))),
            "included_paper_count": int(
                manifest.get("included_paper_count", 0)
            ),
            "excluded_paper_count": int(
                manifest.get("excluded_paper_count", 0)
            ),
            "completed_card_count": sum(
                row.get("status") == "completed" for row in card_runs
            ),
            "analysis_run_ids": [
                str(row["run_id"])
                for row in brief_runs
                if row.get("run_id")
            ],
            "synthesis_run_id": synthesis_run_id,
            "request_count": int(manifest.get("request_count", 0)),
            "usage": usage,
            "stages": manifest.get("stages", {}),
            "failure_codes": [str(value) for value in failure_codes],
            "failure_code": (
                str(failure_codes[0]) if failure_codes else None
            ),
            "failure_message": None,
        }

    def _verify_topic_synthesis_artifact(
        self,
        job_input: dict[str, Any],
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if payload is None:
            raise UIError(
                "ui.topic_synthesis_result_missing",
                "llm-topic-synthesis 未输出机器可读结果。",
            )
        run_id = str(job_input["synthesis_run_id"])
        run_dir = self._inside(
            self.workspace,
            Path("_topic_syntheses") / "runs" / run_id,
        )
        manifest = self._read_json(
            run_dir / "manifest.json",
            code="ui.topic_synthesis_manifest_invalid",
        )
        if (
            manifest.get("schema_version") != "llm.topic_synthesis_run.v1"
            or manifest.get("run_id") != run_id
            or manifest.get("status") != "completed"
            or manifest.get("topic") != job_input["topic"]
            or manifest.get("collection_sha256")
            != job_input["collection_sha256"]
            or manifest.get("input_sha256") != job_input["input_sha256"]
            or manifest.get("source_runs") != job_input["source_runs"]
        ):
            raise UIError(
                "ui.topic_synthesis_manifest_mismatch",
                "跨论文综合 manifest 与冻结输入不一致。",
            )
        if (
            payload.get("run_id") != run_id
            or payload.get("status") != "completed"
            or payload.get("topic") != job_input["topic"]
        ):
            raise UIError(
                "ui.topic_synthesis_result_mismatch",
                "CLI 输出与跨论文综合 manifest 不一致。",
            )
        output_relative = (manifest.get("outputs") or {}).get(
            "topic_synthesis"
        )
        if not isinstance(output_relative, str) or not output_relative:
            raise UIError(
                "ui.topic_synthesis_output_missing",
                "跨论文综合 manifest 缺少正式输出。",
            )
        output = self._read_json(
            self._inside(run_dir, output_relative),
            code="ui.topic_synthesis_output_invalid",
        )
        if (
            output.get("schema_version") != "llm.topic_synthesis_result.v1"
            or output.get("topic") != job_input["topic"]
            or [
                str(row.get("run_id"))
                for row in output.get("source_runs", [])
            ]
            != job_input["collection"]["source_run_ids"]
        ):
            raise UIError(
                "ui.topic_synthesis_output_mismatch",
                "跨论文综合输出与冻结来源 run 不一致。",
            )
        usage = manifest.get("usage")
        requests = manifest.get("requests")
        if not isinstance(usage, dict) or not isinstance(requests, list):
            raise UIError(
                "ui.topic_synthesis_audit_missing",
                "跨论文综合缺少请求或 Token 审计。",
            )
        return {
            "status": "completed",
            "synthesis_run_id": run_id,
            "started_at": manifest.get("started_at"),
            "finished_at": manifest.get("finished_at"),
            "source_paper_count": int(
                manifest.get("source_paper_count", 0)
            ),
            "source_evidence_count": int(
                manifest.get("source_evidence_count", 0)
            ),
            "theme_count": int(manifest.get("theme_count", 0)),
            "synthesis_unit_count": int(
                manifest.get("synthesis_unit_count", 0)
            ),
            "section_count": int(manifest.get("section_count", 0)),
            "request_count": len(requests),
            "usage": usage,
            "failure_code": None,
            "failure_message": None,
            "cli_result": payload,
        }

    def _execute_card_job(self, job_id: str) -> None:
        try:
            job_input = self.jobs.get_input(job_id)
            if not self._begin_job(job_id):
                return
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] Job 开始，共 {len(job_input['papers'])} 篇论文。",
            )
            failure_codes: list[str] = []
            for paper in job_input["papers"]:
                self._raise_if_cancel_requested(job_id)
                paper_id = str(paper["paper_id"])
                self.jobs.set_current_paper(job_id, paper_id)
                result = self._execute_paper(job_id, job_input, paper)
                self.jobs.record_paper_result(job_id, result)
                if result["status"] != "completed":
                    failure_codes.append(str(result["failure_code"]))
                self._raise_if_cancel_requested(job_id)
            if failure_codes:
                succeeded = len(job_input["papers"]) - len(failure_codes)
                self.jobs.append_log(
                    job_id,
                    f"[{self._now()}] Job "
                    f"{'部分完成' if succeeded else '失败'}："
                    f"成功 {succeeded} 篇，失败 {len(failure_codes)} 篇。",
                )
                self.jobs.transition(
                    job_id,
                    (
                        "completed_with_failures"
                        if succeeded
                        else "failed"
                    ),
                    failure_code="ui.card_job_paper_failed",
                    failure_message=(
                        f"成功 {succeeded} 篇，失败 {len(failure_codes)} 篇："
                        + ", ".join(failure_codes)
                    ),
                )
            else:
                self.jobs.append_log(
                    job_id,
                    f"[{self._now()}] Job 完成，全部论文 Card 已发布。",
                )
                self.jobs.transition(job_id, "completed")
        except JobCancelled:
            self._finish_cancelled(job_id)
        except Exception as exc:
            self.jobs.append_log(
                job_id,
                f"[{self._now()}] Job 执行异常：{type(exc).__name__}: {exc}",
            )
            if self.jobs.get(job_id)["status"] == "cancel_requested":
                self._finish_cancelled(job_id)
                return
            current = self.jobs.get(job_id)
            if current["status"] in {"queued", "running"}:
                self.jobs.transition(
                    job_id,
                    "failed",
                    failure_code="ui.card_job_execution_failed",
                    failure_message=f"{type(exc).__name__}: {exc}",
                )

    def _execute_paper(
        self,
        job_id: str,
        job_input: dict[str, Any],
        paper: dict[str, Any],
    ) -> dict[str, Any]:
        started_at = self._now()
        paper_id = str(paper["paper_id"])
        workspace_paper_id = str(paper["workspace_paper_id"])
        source_path = Path(str(paper["source_path"]))
        current_sha = self._sha256(source_path)
        if current_sha != paper["source_sha256"]:
            return self._paper_failure(
                paper_id,
                workspace_paper_id,
                started_at,
                "ui.source_identity_changed",
                "执行前来源文件哈希与冻结输入不一致。",
            )
        command = self.command_factory.build(
            paper_id=paper_id,
            workspace_paper_id=workspace_paper_id,
            source=source_path,
            topic=str(job_input["topic"]),
            pdf_provider=str(job_input["pdf_provider"]),
        )
        self.jobs.append_log(
            job_id,
            f"[{self._now()}] 开始处理：{paper_id}",
        )
        try:
            payload, return_code = self._run_process(
                job_id,
                command.argv,
                log_prefix=paper_id,
            )
        except JobCancelled:
            raise
        except UIError as exc:
            return self._paper_failure(
                paper_id,
                workspace_paper_id,
                started_at,
                "ui.card_process_failed",
                f"{type(exc).__name__}: {exc}",
            )
        if return_code != 0:
            return self._paper_failure(
                paper_id,
                workspace_paper_id,
                started_at,
                "ui.card_process_exit_nonzero",
                f"pipeline 退出码为 {return_code}。",
                payload=payload,
            )
        try:
            artifact = self._verify_card_artifact(
                paper_id,
                workspace_paper_id,
                payload,
            )
        except UIError as exc:
            return self._paper_failure(
                paper_id,
                workspace_paper_id,
                started_at,
                exc.code,
                str(exc),
                payload=payload,
            )
        self.jobs.append_log(
            job_id,
            f"[{self._now()}] 完成处理：{paper_id}，"
            f"generation={artifact['generation_id']}，"
            f"Card={artifact['material_count']}。",
        )
        return {
            "paper_id": paper_id,
            "workspace_paper_id": workspace_paper_id,
            "status": "completed",
            "started_at": started_at,
            "finished_at": self._now(),
            "generation_id": artifact["generation_id"],
            "structure_quality": artifact["structure_quality"],
            "material_count": artifact["material_count"],
            "issue_count": artifact["issue_count"],
            "failure_code": None,
            "failure_message": None,
            "cli_result": payload,
        }

    def _verify_card_artifact(
        self,
        expected_paper_id: str,
        workspace_paper_id: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if payload is None:
            raise UIError(
                "ui.card_result_missing",
                "pipeline 未输出机器可读结果。",
            )
        paper_id = str(payload.get("paper_id") or "")
        if paper_id != expected_paper_id:
            raise UIError(
                "ui.card_result_identity_mismatch",
                "pipeline 输出 paper_id 与冻结输入不一致。",
            )
        if payload.get("status") != "completed":
            raise UIError(
                "ui.card_result_not_completed",
                f"pipeline 输出状态不是 completed：{payload.get('status')}",
            )
        paper_dir = self._inside(self.workspace, workspace_paper_id)
        run_path = paper_dir / "run.json"
        try:
            run = json.loads(run_path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise UIError(
                "ui.card_manifest_invalid",
                f"无法读取 Card run manifest：{run_path}",
            ) from exc
        generation_id = str(run.get("generation_id") or "")
        if (
            run.get("paper_id") != paper_id
            or run.get("status") != "completed"
            or not generation_id
        ):
            raise UIError(
                "ui.card_manifest_mismatch",
                "Card run manifest 的论文身份、状态或 generation 无效。",
            )
        current_path = paper_dir / "materials" / "current.json"
        try:
            current = json.loads(current_path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise UIError(
                "ui.card_current_invalid",
                f"无法读取 Card current manifest：{current_path}",
            ) from exc
        if (
            current.get("status") != "completed"
            or current.get("generation_id") != generation_id
        ):
            raise UIError(
                "ui.card_generation_mismatch",
                "Card current manifest 与 run generation 不一致。",
            )
        material_count = int(
            run.get("stages", {})
            .get("material", {})
            .get("material_count", 0)
        )
        if material_count <= 0:
            raise UIError(
                "ui.card_materials_empty",
                "Card pipeline 完成但没有发布材料卡。",
            )
        return {
            "generation_id": generation_id,
            "structure_quality": run.get("structure_quality"),
            "material_count": material_count,
            "issue_count": len(run.get("issues", [])),
        }

    def _freeze_card_input(
        self,
        paper_id: str,
        workspace_paper_id: str,
        topic: str,
    ) -> dict[str, Any]:
        paper_dir = self._inside(self.workspace, workspace_paper_id)
        run_path = paper_dir / "run.json"
        current_path = paper_dir / "materials" / "current.json"
        run = self._read_json(run_path, code="ui.card_manifest_invalid")
        current = self._read_json(
            current_path,
            code="ui.card_current_invalid",
        )
        generation_id = str(run.get("generation_id") or "")
        if (
            run.get("paper_id") != paper_id
            or run.get("status") != "completed"
            or current.get("status") != "completed"
            or not generation_id
            or current.get("generation_id") != generation_id
        ):
            raise UIError(
                "ui.card_generation_mismatch",
                f"论文 Card 尚未完成或 generation 不一致：{paper_id}",
            )
        materials_relative = current.get("materials_path")
        if not isinstance(materials_relative, str) or not materials_relative:
            raise UIError(
                "ui.card_current_invalid",
                f"Card current manifest 缺少 materials_path：{paper_id}",
            )
        materials_path = self._inside(
            paper_dir / "materials",
            materials_relative,
        )
        try:
            snapshot = create_input_snapshot(
                self.workspace,
                paper_id=paper_id,
                topic=topic,
                workspace_paper_id=workspace_paper_id,
            )
        except (AnalysisInputError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise UIError(
                "ui.analysis_input_invalid",
                f"Card 无法形成单篇分析快照：{paper_id}：{exc}",
            ) from exc
        return {
            "generation_id": generation_id,
            "material_count": len(snapshot.materials),
            "input_sha256": snapshot.input_sha256,
            "run_path": str(run_path),
            "run_sha256": self._sha256(run_path),
            "current_path": str(current_path),
            "current_sha256": self._sha256(current_path),
            "materials_path": str(materials_path),
            "materials_sha256": self._sha256(materials_path),
            "materials_size_bytes": materials_path.stat().st_size,
        }

    def _require_analysis_environment(
        self,
        env_path: Path | None = None,
    ) -> None:
        env_path = env_path or self.topic_brief_command_factory.env_path
        values = {
            key: value
            for key, value in os.environ.items()
            if key in {"LLM_ANALYSIS_API_URL", "LLM_ANALYSIS_API_KEY"}
        }
        try:
            lines = env_path.read_text(encoding="utf-8-sig").splitlines()
        except (FileNotFoundError, OSError, UnicodeError) as exc:
            raise UIError(
                "ui.analysis_environment_invalid",
                f"无法读取分析环境文件：{env_path}",
            ) from exc
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key in {"LLM_ANALYSIS_API_URL", "LLM_ANALYSIS_API_KEY"}:
                values.setdefault(key, value.strip().strip("\"'"))
        missing = [
            key
            for key in ("LLM_ANALYSIS_API_URL", "LLM_ANALYSIS_API_KEY")
            if not values.get(key)
        ]
        if missing:
            raise UIError(
                "ui.analysis_environment_missing",
                "分析环境缺少必要配置：" + ", ".join(missing),
            )

    @staticmethod
    def _topic_paper_failure(
        paper: dict[str, Any],
        started_at: str,
        code: str,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "paper_id": str(paper["paper_id"]),
            "workspace_paper_id": str(paper["workspace_paper_id"]),
            "status": "failed",
            "analysis_status": "failed",
            "analysis_run_id": str(paper["analysis_run_id"]),
            "generation_id": str(paper["generation_id"]),
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "paper_relevance": None,
            "selected_material_count": 0,
            "evidence_unit_count": 0,
            "evidence_failure_count": 0,
            "request_count": 0,
            "usage": {},
            "failure_code": code,
            "failure_message": message,
            "cli_result": payload,
        }

    @staticmethod
    def _paper_failure(
        paper_id: str,
        workspace_paper_id: str,
        started_at: str,
        code: str,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "paper_id": paper_id,
            "workspace_paper_id": workspace_paper_id,
            "status": "failed",
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "generation_id": None,
            "structure_quality": None,
            "material_count": 0,
            "issue_count": 0,
            "failure_code": code,
            "failure_message": message,
            "cli_result": payload,
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise UIError(
                "ui.source_unreadable",
                f"无法读取文件：{path}",
            ) from exc
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _read_json(path: Path, *, code: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise UIError(code, f"无法读取 JSON：{path}") from exc
        if not isinstance(payload, dict):
            raise UIError(code, f"JSON 必须是对象：{path}")
        return payload

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _inside(base: Path, candidate: str | Path) -> Path:
        base = base.resolve()
        path = Path(candidate)
        resolved = path.resolve() if path.is_absolute() else (base / path).resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise UIError(
                "ui.path_outside_root",
                f"路径越过允许根目录：{candidate}",
            ) from exc
        return resolved
