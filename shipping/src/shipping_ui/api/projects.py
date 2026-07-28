from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from ..artifact_store import ArtifactStore
from ..project_repository import ProjectRepository
from ..services.project_service import ProjectService, SourceInput


class CreateProjectRequest(BaseModel):
    project_id: str
    name: str
    topic: str
    description: str = ""


class UpdateProjectRequest(BaseModel):
    expected_revision: int
    name: str | None = None
    topic: str | None = None
    description: str | None = None


class FreezeCollectionRequest(BaseModel):
    expected_revision: int


def build_project_router(
    repository: ProjectRepository,
    service: ProjectService,
    artifacts: ArtifactStore,
) -> APIRouter:
    router = APIRouter(prefix="/api/projects", tags=["projects"])

    @router.post("")
    def create_project(request: CreateProjectRequest) -> dict:
        service.create_project(
            project_id=request.project_id,
            name=request.name,
            topic=request.topic,
            description=request.description,
        )
        return artifacts.get_project(request.project_id)

    @router.get("/{project_id}/definition")
    def project_definition(project_id: str) -> dict:
        return repository.get(project_id)

    @router.patch("/{project_id}")
    def update_project(
        project_id: str,
        request: UpdateProjectRequest,
    ) -> dict:
        repository.update_metadata(
            project_id,
            expected_revision=request.expected_revision,
            name=request.name,
            topic=request.topic,
            description=request.description,
        )
        return artifacts.get_project(project_id)

    @router.post("/{project_id}/sources")
    def import_sources(
        project_id: str,
        files: Annotated[list[UploadFile], File()],
        paper_ids: Annotated[str, Form()],
        expected_revision: Annotated[int, Form()],
    ) -> dict:
        resolved_ids = service.parse_paper_ids(paper_ids, len(files))
        result = service.import_sources(
            project_id,
            expected_revision=expected_revision,
            sources=[
                SourceInput(
                    paper_id=paper_id,
                    filename=file.filename or "unnamed",
                    stream=file.file,
                )
                for paper_id, file in zip(resolved_ids, files, strict=True)
            ],
        )
        return {
            **result,
            "summary": artifacts.get_project(project_id),
        }

    @router.post("/{project_id}/collections")
    def freeze_collection(
        project_id: str,
        request: FreezeCollectionRequest,
    ) -> dict:
        return repository.freeze_collection(
            project_id,
            expected_revision=request.expected_revision,
        )

    @router.post("/{project_id}/migrate")
    def migrate_project(project_id: str) -> dict:
        repository.migrate_legacy(project_id)
        return artifacts.get_project(project_id)

    return router
