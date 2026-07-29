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
from ..job_repository import INCOMPLETE_STATUSES, JobRepository
from ..project_repository import ProjectRepository


SUPPORTED_SOURCE_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}


@dataclass(frozen=True)
class SourceInput:
    paper_id: str
    filename: str
    stream: BinaryIO


class ProjectService:
    def __init__(
        self,
        repository: ProjectRepository,
        jobs: JobRepository | None = None,
    ) -> None:
        self.repository = repository
        self.jobs = jobs

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

    def set_archived(
        self,
        project_id: str,
        *,
        expected_revision: int,
        archived: bool,
    ) -> dict:
        if archived and self.jobs is not None:
            active = [
                job
                for job in self.jobs.list(project_id)
                if job["status"] in INCOMPLETE_STATUSES
            ]
            if active:
                raise UIError(
                    "ui.project_job_active",
                    f"项目仍有活动 Job，不能归档：{active[0]['job_id']}",
                )
        return self.repository.set_archived(
            project_id,
            expected_revision=expected_revision,
            archived=archived,
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
        project = self.repository.check_revision(
            project_id,
            expected_revision=expected_revision,
        )
        project_dir = self.repository.project_dir(project_id)
        staging = project_dir / "staging" / f"import-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        staged: list[tuple[Path, Path, dict]] = []
        results: list[dict] = []
        known_ids = {
            str(row["paper_id"]): row for row in project["papers"]
        }
        known_hashes = {
            str(row["source"]["sha256"]): row for row in project["papers"]
        }
        try:
            for index, source in enumerate(sources):
                paper_id = source.paper_id.strip()
                original_name = Path(source.filename).name
                if not paper_id:
                    results.append(
                        self._import_failure(
                            index,
                            paper_id,
                            original_name,
                            "ui.paper_id_required",
                            "论文 ID 不能为空。",
                        )
                    )
                    continue
                suffix = Path(original_name).suffix.lower()
                if suffix not in SUPPORTED_SOURCE_SUFFIXES:
                    results.append(
                        self._import_failure(
                            index,
                            paper_id,
                            original_name,
                            "ui.source_type_unsupported",
                            f"不支持的来源格式：{original_name}",
                        )
                    )
                    continue
                source_id = uuid.uuid4().hex
                staged_path = staging / f"{source_id}{suffix}"
                digest = hashlib.sha256()
                size = 0
                try:
                    with staged_path.open("wb") as target:
                        while chunk := source.stream.read(1024 * 1024):
                            target.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                except (OSError, ValueError) as exc:
                    results.append(
                        self._import_failure(
                            index,
                            paper_id,
                            original_name,
                            "ui.source_read_failed",
                            f"无法读取来源文件：{original_name}（{exc}）",
                        )
                    )
                    continue
                if size == 0:
                    results.append(
                        self._import_failure(
                            index,
                            paper_id,
                            original_name,
                            "ui.source_empty",
                            f"来源文件为空：{original_name}",
                        )
                    )
                    continue
                sha256 = f"sha256:{digest.hexdigest()}"
                duplicate = known_hashes.get(sha256)
                if duplicate is not None:
                    results.append(
                        {
                            "index": index,
                            "paper_id": paper_id,
                            "original_filename": original_name,
                            "status": "skipped_duplicate",
                            "sha256": sha256,
                            "size_bytes": size,
                            "duplicate_of_paper_id": duplicate["paper_id"],
                            "error_code": "ui.source_duplicate",
                            "message": (
                                "来源内容与论文 "
                                f"{duplicate['paper_id']} 相同，未重复导入。"
                            ),
                        }
                    )
                    continue
                duplicate_id = known_ids.get(paper_id)
                if duplicate_id is not None:
                    results.append(
                        self._import_failure(
                            index,
                            paper_id,
                            original_name,
                            "ui.paper_id_duplicate",
                            f"论文 ID 已被占用：{paper_id}",
                            sha256=sha256,
                            size_bytes=size,
                            duplicate_of_paper_id=paper_id,
                        )
                    )
                    continue
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
                        "sha256": sha256,
                        "size_bytes": size,
                        "added_at": datetime.now(UTC).isoformat(),
                    },
                }
                staged.append((staged_path, final_path, paper))
                known_ids[paper_id] = paper
                known_hashes[sha256] = paper
                results.append(
                    {
                        "index": index,
                        "paper_id": paper_id,
                        "original_filename": original_name,
                        "status": "imported",
                        "sha256": sha256,
                        "size_bytes": size,
                        "duplicate_of_paper_id": None,
                        "error_code": None,
                        "message": "已加入项目论文池。",
                    }
                )
            paper_ids = [row[2]["paper_id"] for row in staged]
            moved: list[Path] = []
            try:
                for staged_path, final_path, _paper in staged:
                    staged_path.replace(final_path)
                    moved.append(final_path)
                if staged:
                    project = self.repository.add_papers_and_freeze(
                        project_id,
                        expected_revision=expected_revision,
                        papers=[row[2] for row in staged],
                    )
            except Exception:
                for path in moved:
                    path.unlink(missing_ok=True)
                raise
            counts = {
                status: sum(row["status"] == status for row in results)
                for status in ("imported", "skipped_duplicate", "failed")
            }
            return {
                "project": project,
                "imported_paper_ids": paper_ids,
                "collection_path": project.get("current_collection_path"),
                "results": results,
                "counts": counts,
            }
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if staging.parent.exists() and not any(staging.parent.iterdir()):
                staging.parent.rmdir()

    @staticmethod
    def _import_failure(
        index: int,
        paper_id: str,
        original_filename: str,
        error_code: str,
        message: str,
        *,
        sha256: str | None = None,
        size_bytes: int | None = None,
        duplicate_of_paper_id: str | None = None,
    ) -> dict:
        return {
            "index": index,
            "paper_id": paper_id,
            "original_filename": original_filename,
            "status": "failed",
            "sha256": sha256,
            "size_bytes": size_bytes,
            "duplicate_of_paper_id": duplicate_of_paper_id,
            "error_code": error_code,
            "message": message,
        }

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
