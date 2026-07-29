from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..job_repository import JobRepository
from ..services.job_service import JobService
from ..services.run_comparison_service import RunComparisonService


class CardJobRequest(BaseModel):
    expected_revision: int
    paper_ids: list[str] = Field(min_length=1)
    pdf_provider: Literal["none", "mineru"] = "none"


class TopicBriefJobRequest(BaseModel):
    expected_revision: int
    paper_ids: list[str] = Field(min_length=1)
    external_service_confirmed: bool = False


class TopicSynthesisJobRequest(BaseModel):
    expected_revision: int
    source_run_ids: list[str] = Field(min_length=2)
    external_service_confirmed: bool = False


class FullPipelineJobRequest(BaseModel):
    expected_revision: int
    external_service_confirmed: bool = False


def build_job_router(
    jobs: JobRepository,
    service: JobService,
    comparisons: RunComparisonService,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["jobs"])

    @router.post("/projects/{project_id}/card-jobs/preflight")
    def preflight_card_job(
        project_id: str,
        request: CardJobRequest,
    ) -> dict:
        return service.preflight(
            project_id,
            expected_revision=request.expected_revision,
            paper_ids=request.paper_ids,
            pdf_provider=request.pdf_provider,
        )

    @router.post("/projects/{project_id}/card-jobs")
    def create_card_job(
        project_id: str,
        request: CardJobRequest,
    ) -> dict:
        return service.create_card_job(
            project_id,
            expected_revision=request.expected_revision,
            paper_ids=request.paper_ids,
            pdf_provider=request.pdf_provider,
        )

    @router.post("/projects/{project_id}/topic-brief-jobs/preflight")
    def preflight_topic_brief_job(
        project_id: str,
        request: TopicBriefJobRequest,
    ) -> dict:
        return service.preflight_topic_brief(
            project_id,
            expected_revision=request.expected_revision,
            paper_ids=request.paper_ids,
        )

    @router.post("/projects/{project_id}/topic-brief-jobs")
    def create_topic_brief_job(
        project_id: str,
        request: TopicBriefJobRequest,
    ) -> dict:
        return service.create_topic_brief_job(
            project_id,
            expected_revision=request.expected_revision,
            paper_ids=request.paper_ids,
            external_service_confirmed=request.external_service_confirmed,
        )

    @router.post("/projects/{project_id}/topic-synthesis-jobs/preflight")
    def preflight_topic_synthesis_job(
        project_id: str,
        request: TopicSynthesisJobRequest,
    ) -> dict:
        return service.preflight_topic_synthesis(
            project_id,
            expected_revision=request.expected_revision,
            source_run_ids=request.source_run_ids,
        )

    @router.post("/projects/{project_id}/topic-synthesis-jobs")
    def create_topic_synthesis_job(
        project_id: str,
        request: TopicSynthesisJobRequest,
    ) -> dict:
        return service.create_topic_synthesis_job(
            project_id,
            expected_revision=request.expected_revision,
            source_run_ids=request.source_run_ids,
            external_service_confirmed=request.external_service_confirmed,
        )

    @router.post("/projects/{project_id}/full-pipeline-jobs/preflight")
    def preflight_full_pipeline_job(
        project_id: str,
        request: FullPipelineJobRequest,
    ) -> dict:
        return service.preflight_full_pipeline(
            project_id,
            expected_revision=request.expected_revision,
        )

    @router.post("/projects/{project_id}/full-pipeline-jobs")
    def create_full_pipeline_job(
        project_id: str,
        request: FullPipelineJobRequest,
    ) -> dict:
        return service.create_full_pipeline_job(
            project_id,
            expected_revision=request.expected_revision,
            external_service_confirmed=request.external_service_confirmed,
        )

    @router.get("/jobs")
    def list_jobs(project_id: str | None = None) -> list[dict]:
        return jobs.list(project_id)

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        return jobs.get(job_id)

    @router.get("/jobs/{job_id}/log")
    def get_job_log(
        job_id: str,
        after_line: int = Query(default=0, ge=0),
    ) -> dict:
        return jobs.read_log(job_id, after_line=after_line)

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict:
        return service.request_cancel(job_id)

    @router.get("/jobs/{job_id}/retry-candidates")
    def retry_candidates(job_id: str) -> dict:
        return jobs.retry_candidates(job_id)

    @router.get("/projects/{project_id}/run-comparison")
    def compare_runs(
        project_id: str,
        left_job_id: str = Query(min_length=1),
        right_job_id: str = Query(min_length=1),
    ) -> dict:
        return comparisons.compare(
            project_id,
            left_job_id=left_job_id,
            right_job_id=right_job_id,
        )

    return router
