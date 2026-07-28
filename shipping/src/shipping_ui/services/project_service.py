from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from ..errors import UIError
from ..project_repository import ProjectRepository


SUPPORTED_SOURCE_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}


@dataclass(frozen=True)
class SourceInput:
    paper_id: str
    filename: str
    stream: BinaryIO


class ProjectService:
    def __init__(self, repository: ProjectRepository) -> None:
        self.repository = repository

    def create_project(
        self,
        *,
        project_id: str,
        name: str,
        topic: str,
        description: str = "",
    ) -> dict:
        return self.repository.create(
            project_id=project_id,
            name=name,
            topic=topic,
            description=description,
        )

    def import_sources(
        self,
        project_id: str,
        *,
        expected_revision: int,
        sources: list[SourceInput],
    ) -> dict:
        if not sources:
            raise UIError("ui.sources_empty", "至少需要选择一个来源文件。")
        project_dir = self.repository.project_dir(project_id)
        staging = project_dir / "staging" / f"import-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        staged: list[tuple[Path, Path, dict]] = []
        try:
            for source in sources:
                paper_id = source.paper_id.strip()
                if not paper_id:
                    raise UIError("ui.paper_id_required", "论文 ID 不能为空。")
                original_name = Path(source.filename).name
                suffix = Path(original_name).suffix.lower()
                if suffix not in SUPPORTED_SOURCE_SUFFIXES:
                    raise UIError(
                        "ui.source_type_unsupported",
                        f"不支持的来源格式：{original_name}",
                    )
                source_id = uuid.uuid4().hex
                staged_path = staging / f"{source_id}{suffix}"
                digest = hashlib.sha256()
                size = 0
                with staged_path.open("wb") as target:
                    while chunk := source.stream.read(1024 * 1024):
                        target.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                if size == 0:
                    raise UIError(
                        "ui.source_empty",
                        f"来源文件为空：{original_name}",
                    )
                final_path = project_dir / "sources" / staged_path.name
                workspace_paper_id = (
                    f"{project_id}--"
                    f"{hashlib.sha256(paper_id.encode('utf-8')).hexdigest()[:12]}"
                )
                paper = {
                    "paper_id": paper_id,
                    "workspace_paper_id": workspace_paper_id,
                    "paper_title": paper_id,
                    "source": {
                        "storage": "managed",
                        "path": str(final_path.relative_to(project_dir)).replace(
                            "\\", "/"
                        ),
                        "original_filename": original_name,
                        "sha256": f"sha256:{digest.hexdigest()}",
                        "size_bytes": size,
                        "added_at": datetime.now(UTC).isoformat(),
                    },
                }
                staged.append((staged_path, final_path, paper))
            paper_ids = [row[2]["paper_id"] for row in staged]
            if len(paper_ids) != len(set(paper_ids)):
                raise UIError(
                    "ui.paper_id_duplicate",
                    "本次导入包含重复论文 ID。",
                )
            hashes = [row[2]["source"]["sha256"] for row in staged]
            if len(hashes) != len(set(hashes)):
                raise UIError(
                    "ui.source_duplicate",
                    "本次导入包含内容相同的来源文件。",
                )
            moved: list[Path] = []
            try:
                for staged_path, final_path, _paper in staged:
                    staged_path.replace(final_path)
                    moved.append(final_path)
                project = self.repository.add_papers_and_freeze(
                    project_id,
                    expected_revision=expected_revision,
                    papers=[row[2] for row in staged],
                )
            except Exception:
                for path in moved:
                    path.unlink(missing_ok=True)
                raise
            return {
                "project": project,
                "imported_paper_ids": paper_ids,
                "collection_path": project["current_collection_path"],
            }
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if staging.parent.exists() and not any(staging.parent.iterdir()):
                staging.parent.rmdir()

    @staticmethod
    def parse_paper_ids(value: str, expected_count: int) -> list[str]:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise UIError(
                "ui.paper_ids_invalid",
                "paper_ids 必须是 JSON 字符串数组。",
            ) from exc
        if not isinstance(payload, list) or not all(
            isinstance(row, str) for row in payload
        ):
            raise UIError(
                "ui.paper_ids_invalid",
                "paper_ids 必须是 JSON 字符串数组。",
            )
        if len(payload) != expected_count:
            raise UIError(
                "ui.paper_ids_count_mismatch",
                "paper_ids 数量必须与文件数量一致。",
            )
        return payload
