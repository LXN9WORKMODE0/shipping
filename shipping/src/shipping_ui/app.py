from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.jobs import build_job_router
from .api.projects import build_project_router
from .artifact_store import ArtifactStore, ArtifactStoreError
from .command_factory import (
    CardCommandFactory,
    FullPipelineCommandFactory,
    TopicBriefCommandFactory,
    TopicSynthesisCommandFactory,
)
from .job_repository import JobRepository
from .project_repository import ProjectRepository
from .services.job_service import JobService
from .services.governance_service import GovernanceService
from .services.project_service import ProjectService
from .services.run_comparison_service import RunComparisonService


def create_app(
    project_root: str | Path | None = None,
    *,
    workspace: str | Path = "workspace",
    legacy_config_root: str | Path = "config/ui",
    topic_brief_env_file: str | Path = ".env",
) -> FastAPI:
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    project_repository = ProjectRepository(
        root,
        workspace=workspace,
        legacy_config_root=legacy_config_root,
    )
    job_repository = JobRepository(root, workspace=workspace)
    project_service = ProjectService(project_repository, job_repository)
    governance_service = GovernanceService(
        root,
        job_repository,
        workspace=workspace,
        env_file=topic_brief_env_file,
    )
    run_comparison_service = RunComparisonService(job_repository)
    job_service = JobService(
        project_root=root,
        workspace=workspace,
        projects=project_repository,
        jobs=job_repository,
        command_factory=CardCommandFactory(root, workspace=workspace),
        topic_brief_command_factory=TopicBriefCommandFactory(
            root,
            workspace=workspace,
            env_file=topic_brief_env_file,
        ),
        topic_synthesis_command_factory=TopicSynthesisCommandFactory(
            root,
            workspace=workspace,
            env_file=topic_brief_env_file,
        ),
        full_pipeline_command_factory=FullPipelineCommandFactory(
            root,
            workspace=workspace,
            env_file=topic_brief_env_file,
        ),
    )
    store = ArtifactStore(
        root,
        workspace=workspace,
        project_config_root=legacy_config_root,
        project_repository=project_repository,
        job_repository=job_repository,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        job_service.start()
        yield
        job_service.shutdown()

    app = FastAPI(
        title="文献综述工作台",
        version="0.2.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["*"],
    )
    app.include_router(
        build_project_router(project_repository, project_service, store)
    )
    app.include_router(
        build_job_router(
            job_repository,
            job_service,
            run_comparison_service,
        )
    )

    @app.exception_handler(ArtifactStoreError)
    async def artifact_error_handler(_request, exc: ArtifactStoreError):
        return _error_response(exc)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "pipeline_jobs"}

    @app.get("/api/projects")
    def projects(
        include_archived: bool = Query(default=False),
    ) -> list[dict]:
        return store.list_projects(include_archived=include_archived)

    @app.get("/api/projects/{project_id}")
    def project(project_id: str) -> dict:
        return store.get_project(project_id)

    @app.get("/api/projects/{project_id}/papers/{paper_id}")
    def paper(project_id: str, paper_id: str) -> dict:
        return store.get_paper(project_id, paper_id)

    @app.get("/api/projects/{project_id}/synthesis")
    def synthesis(project_id: str) -> dict:
        return store.get_synthesis(project_id)

    @app.get("/api/runs")
    def runs(project_id: str | None = None) -> list[dict]:
        records = [
            *job_repository.list_run_records(project_id),
            *store.list_runs(project_id),
        ]
        return sorted(
            records,
            key=lambda row: str(row.get("started_at") or ""),
            reverse=True,
        )

    @app.get("/api/system/status")
    def system_status() -> dict:
        status = store.system_status()
        status["job_count"] = len(job_repository.list())
        status["active_job_count"] = sum(
            row["status"] in {"queued", "running", "cancel_requested"}
            for row in job_repository.list()
        )
        return status

    @app.get("/api/governance/summary")
    def governance_summary() -> dict:
        return governance_service.summary()

    frontend_dist = root / "ui" / "frontend" / "dist"
    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=assets_dir),
            name="assets",
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API 不存在。")
        index_path = frontend_dist / "index.html"
        if not index_path.exists():
            raise HTTPException(
                status_code=503,
                detail="前端尚未构建，请先运行 pnpm build。",
            )
        return FileResponse(index_path)

    return app


def _error_response(exc: ArtifactStoreError):
    from fastapi.responses import JSONResponse

    status = 404 if exc.code.endswith("_not_found") else 422
    return JSONResponse(
        status_code=status,
        content={"error_code": exc.code, "error_message": str(exc)},
    )
