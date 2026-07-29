from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import UIError


PROJECT_SCHEMA = "review_ui_project.v2"
LEGACY_PROJECT_SCHEMA = "review_ui_project.v1"
COLLECTION_SCHEMA = "review_pipeline_collection.v1"
_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")


class ProjectRepository:
    def __init__(
        self,
        project_root: str | Path,
        *,
        workspace: str | Path = "workspace",
        legacy_config_root: str | Path = "config/ui",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = self._inside(self.project_root, workspace)
        self.root = self._inside(self.workspace, "_ui/projects")
        self.legacy_config_root = self._inside(
            self.project_root, legacy_config_root
        )

    def list(self) -> list[dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        if self.root.exists():
            for path in sorted(self.root.glob("*/project.json")):
                project = self._read_json(path)
                self._validate_project(project)
                records[str(project["project_id"])] = project
        if self.legacy_config_root.exists():
            for path in sorted(self.legacy_config_root.glob("*.json")):
                legacy = self._normalize_legacy(path)
                records.setdefault(str(legacy["project_id"]), legacy)
        return sorted(records.values(), key=lambda row: str(row["name"]))

    def get(self, project_id: str) -> dict[str, Any]:
        self._validate_project_id(project_id)
        path = self._project_path(project_id)
        if path.exists():
            project = self._read_json(path)
            self._validate_project(project)
            return project
        if self.legacy_config_root.exists():
            matches = [
                self._normalize_legacy(candidate)
                for candidate in self.legacy_config_root.glob("*.json")
                if self._read_json(candidate).get("project_id") == project_id
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise UIError(
                    "ui.project_id_duplicate",
                    f"综述任务 ID 重复：{project_id}",
                )
        raise UIError("ui.project_not_found", f"不存在综述任务：{project_id}")

    def create(
        self,
        *,
        project_id: str,
        name: str,
        topic: str,
        description: str = "",
    ) -> dict[str, Any]:
        self._validate_project_id(project_id)
        name = self._required_text(name, "任务名称")
        topic = self._required_text(topic, "综述主题")
        project_dir = self._project_dir(project_id)
        if project_dir.exists() or any(
            row["project_id"] == project_id for row in self.list()
        ):
            raise UIError(
                "ui.project_exists",
                f"综述任务已存在：{project_id}",
            )
        now = self._now()
        project = {
            "schema_version": PROJECT_SCHEMA,
            "project_id": project_id,
            "name": name,
            "description": description.strip(),
            "topic": topic,
            "revision": 1,
            "papers": [],
            "analysis_run_ids": [],
            "synthesis_run_id": None,
            "current_collection_path": None,
            "created_at": now,
            "updated_at": now,
            "legacy_origin": None,
            "archived_at": None,
        }
        project_dir.mkdir(parents=True, exist_ok=False)
        (project_dir / "sources").mkdir()
        (project_dir / "collections").mkdir()
        self._atomic_json(self._project_path(project_id), project)
        return project

    def update_metadata(
        self,
        project_id: str,
        *,
        expected_revision: int,
        name: str | None = None,
        topic: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        project = self._require_mutable(project_id, expected_revision)
        changed = False
        if name is not None:
            normalized_name = self._required_text(name, "任务名称")
            changed = changed or normalized_name != project["name"]
            project["name"] = normalized_name
        if topic is not None:
            normalized_topic = self._required_text(topic, "综述主题")
            topic_changed = normalized_topic != project["topic"]
            changed = changed or topic_changed
            project["topic"] = normalized_topic
        if description is not None:
            normalized_description = description.strip()
            changed = changed or normalized_description != project["description"]
            project["description"] = normalized_description
        if not changed:
            return project
        project["revision"] += 1
        project["updated_at"] = self._now()
        if topic is not None and topic_changed and project["papers"]:
            collection_path = self._write_collection(project)
            project["current_collection_path"] = str(
                collection_path.relative_to(self._project_dir(project_id))
            ).replace("\\", "/")
        self._atomic_json(self._project_path(project_id), project)
        return project

    def add_papers_and_freeze(
        self,
        project_id: str,
        *,
        expected_revision: int,
        papers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not papers:
            raise UIError("ui.sources_empty", "至少需要导入一个来源文件。")
        project = self._require_mutable(project_id, expected_revision)
        existing_ids = {str(row["paper_id"]) for row in project["papers"]}
        existing_hashes = {
            str(row["source"]["sha256"]) for row in project["papers"]
        }
        batch_ids: set[str] = set()
        batch_hashes: set[str] = set()
        for paper in papers:
            paper_id = self._required_text(paper.get("paper_id"), "论文 ID")
            sha256 = str(paper.get("source", {}).get("sha256", ""))
            if paper_id in existing_ids or paper_id in batch_ids:
                raise UIError(
                    "ui.paper_id_duplicate",
                    f"论文 ID 重复：{paper_id}",
                )
            if not sha256.startswith("sha256:"):
                raise UIError(
                    "ui.source_sha_invalid",
                    f"来源缺少有效 SHA-256：{paper_id}",
                )
            if sha256 in existing_hashes or sha256 in batch_hashes:
                raise UIError(
                    "ui.source_duplicate",
                    f"来源文件内容重复：{paper_id}",
                )
            batch_ids.add(paper_id)
            batch_hashes.add(sha256)
        project["papers"].extend(papers)
        project["revision"] += 1
        project["updated_at"] = self._now()
        collection_path = self._write_collection(project)
        project["current_collection_path"] = str(
            collection_path.relative_to(self._project_dir(project_id))
        ).replace("\\", "/")
        self._atomic_json(self._project_path(project_id), project)
        return project

    def check_revision(
        self,
        project_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        return self._require_mutable(project_id, expected_revision)

    def set_archived(
        self,
        project_id: str,
        *,
        expected_revision: int,
        archived: bool,
    ) -> dict[str, Any]:
        project = self.get(project_id)
        if project["schema_version"] != PROJECT_SCHEMA:
            raise UIError(
                "ui.project_migration_required",
                "旧版只读项目必须先迁移，才能归档。",
            )
        if project["revision"] != expected_revision:
            raise UIError(
                "ui.project_revision_conflict",
                f"项目已被修改：期望 revision {expected_revision}，"
                f"当前为 {project['revision']}。",
            )
        currently_archived = bool(project.get("archived_at"))
        if currently_archived == archived:
            return project
        project = json.loads(json.dumps(project, ensure_ascii=False))
        project["archived_at"] = self._now() if archived else None
        project["revision"] += 1
        project["updated_at"] = self._now()
        self._atomic_json(self._project_path(project_id), project)
        return project

    def freeze_collection(
        self,
        project_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        project = self._require_mutable(project_id, expected_revision)
        if not project["papers"]:
            raise UIError(
                "ui.collection_empty",
                "没有论文，无法冻结 collection。",
            )
        collection_path = self._write_collection(project)
        relative = str(
            collection_path.relative_to(self._project_dir(project_id))
        ).replace("\\", "/")
        if project.get("current_collection_path") != relative:
            project["current_collection_path"] = relative
            project["revision"] += 1
            project["updated_at"] = self._now()
            self._atomic_json(self._project_path(project_id), project)
        return {
            "project": project,
            "collection_path": str(collection_path),
            "collection": self._read_json(collection_path),
        }

    def set_analysis_run_ids(
        self,
        project_id: str,
        *,
        expected_revision: int,
        analysis_run_ids: list[str],
    ) -> dict[str, Any]:
        project = self._require_mutable(project_id, expected_revision)
        normalized = [str(value).strip() for value in analysis_run_ids]
        if any(
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            for value in normalized
        ):
            raise UIError(
                "ui.analysis_run_id_invalid",
                "单篇分析 run ID 无效。",
            )
        if len(normalized) != len(set(normalized)):
            raise UIError(
                "ui.analysis_run_id_duplicate",
                "单篇分析 run ID 不能重复。",
            )
        if normalized == project["analysis_run_ids"]:
            return project
        project["analysis_run_ids"] = normalized
        project["synthesis_run_id"] = None
        project["revision"] += 1
        project["updated_at"] = self._now()
        self._atomic_json(self._project_path(project_id), project)
        return project

    def set_synthesis_run_id(
        self,
        project_id: str,
        *,
        expected_revision: int,
        synthesis_run_id: str | None,
    ) -> dict[str, Any]:
        project = self._require_mutable(project_id, expected_revision)
        normalized = (
            str(synthesis_run_id).strip()
            if synthesis_run_id is not None
            else None
        )
        if normalized is not None and (
            not normalized
            or normalized in {".", ".."}
            or "/" in normalized
            or "\\" in normalized
        ):
            raise UIError(
                "ui.synthesis_run_id_invalid",
                "跨论文综合 run ID 无效。",
            )
        if normalized == project["synthesis_run_id"]:
            return project
        project["synthesis_run_id"] = normalized
        project["revision"] += 1
        project["updated_at"] = self._now()
        self._atomic_json(self._project_path(project_id), project)
        return project

    def publish_pipeline_runs(
        self,
        project_id: str,
        *,
        expected_revision: int,
        analysis_run_ids: list[str],
        synthesis_run_id: str | None,
    ) -> dict[str, Any]:
        project = self._require_mutable(project_id, expected_revision)
        normalized_analysis = [str(value).strip() for value in analysis_run_ids]
        normalized_synthesis = (
            str(synthesis_run_id).strip()
            if synthesis_run_id is not None
            else None
        )
        identifiers = [
            *normalized_analysis,
            *([normalized_synthesis] if normalized_synthesis else []),
        ]
        if any(
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            for value in identifiers
        ):
            raise UIError(
                "ui.pipeline_run_id_invalid",
                "完整流程包含无效的子 run ID。",
            )
        if len(normalized_analysis) != len(set(normalized_analysis)):
            raise UIError(
                "ui.analysis_run_id_duplicate",
                "单篇分析 run ID 不能重复。",
            )
        if (
            normalized_analysis == project["analysis_run_ids"]
            and normalized_synthesis == project["synthesis_run_id"]
        ):
            return project
        project["analysis_run_ids"] = normalized_analysis
        project["synthesis_run_id"] = normalized_synthesis
        project["revision"] += 1
        project["updated_at"] = self._now()
        self._atomic_json(self._project_path(project_id), project)
        return project

    def migrate_legacy(self, project_id: str) -> dict[str, Any]:
        current = self.get(project_id)
        if current["schema_version"] == PROJECT_SCHEMA:
            return current
        project_dir = self._project_dir(project_id)
        if project_dir.exists():
            raise UIError(
                "ui.project_storage_exists",
                f"项目存储目录已存在：{project_id}",
            )
        now = self._now()
        project = {
            "schema_version": PROJECT_SCHEMA,
            "project_id": project_id,
            "name": current["name"],
            "description": current.get("description", ""),
            "topic": current["topic"],
            "revision": 1,
            "papers": current["papers"],
            "analysis_run_ids": current["analysis_run_ids"],
            "synthesis_run_id": current["synthesis_run_id"],
            "current_collection_path": None,
            "created_at": now,
            "updated_at": now,
            "legacy_origin": current["legacy_origin"],
            "archived_at": None,
        }
        project_dir.mkdir(parents=True, exist_ok=False)
        (project_dir / "sources").mkdir()
        (project_dir / "collections").mkdir()
        collection_path = self._write_collection(project)
        project["current_collection_path"] = str(
            collection_path.relative_to(project_dir)
        ).replace("\\", "/")
        self._atomic_json(self._project_path(project_id), project)
        return project

    def project_dir(self, project_id: str) -> Path:
        self._validate_project_id(project_id)
        return self._project_dir(project_id)

    def source_path(
        self,
        project_id: str,
        paper: dict[str, Any],
    ) -> Path:
        self._validate_project_id(project_id)
        return self._source_absolute(project_id, paper)

    def file_identity(self, path: str | Path) -> tuple[str | None, int | None]:
        return self._file_identity(Path(path).resolve())

    def _write_collection(self, project: dict[str, Any]) -> Path:
        project_id = str(project["project_id"])
        collection = {
            "schema_version": COLLECTION_SCHEMA,
            "topic": project["topic"],
            "papers": [
                {
                    "paper_id": row.get(
                        "workspace_paper_id", row["paper_id"]
                    ),
                    "source": str(self._source_absolute(project_id, row)),
                }
                for row in project["papers"]
            ],
        }
        canonical = json.dumps(
            collection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        path = (
            self._project_dir(project_id)
            / "collections"
            / f"collection-{digest[:16]}.json"
        )
        if not path.exists():
            self._atomic_json(path, collection)
        else:
            existing = self._read_json(path)
            if existing != collection:
                raise UIError(
                    "ui.collection_hash_collision",
                    f"collection 哈希冲突：{path.name}",
                )
        return path

    def _source_absolute(self, project_id: str, paper: dict[str, Any]) -> Path:
        source = paper["source"]
        path = Path(str(source["path"]))
        if source["storage"] == "managed":
            return self._inside(self._project_dir(project_id), path)
        if source["storage"] == "external":
            return path.resolve()
        raise UIError(
            "ui.source_storage_invalid",
            f"未知来源存储类型：{source['storage']}",
        )

    def _normalize_legacy(self, path: Path) -> dict[str, Any]:
        config = self._read_json(path)
        if config.get("schema_version") != LEGACY_PROJECT_SCHEMA:
            raise UIError(
                "ui.schema_unsupported",
                f"界面项目 schema 不受支持：{config.get('schema_version')}",
            )
        paper_collection_path = self._inside(
            self.project_root,
            (path.parent / str(config["paper_collection"])).resolve(),
        )
        synthesis_collection_path = self._inside(
            self.project_root,
            (path.parent / str(config["synthesis_collection"])).resolve(),
        )
        paper_collection = self._read_json(paper_collection_path)
        synthesis_collection = self._read_json(synthesis_collection_path)
        papers = []
        for row in paper_collection["papers"]:
            source_path = Path(str(row["source"]))
            resolved_source = (
                source_path.resolve()
                if source_path.is_absolute()
                else (paper_collection_path.parent / source_path).resolve()
            )
            source_sha, size = self._file_identity(resolved_source)
            papers.append(
                {
                    "paper_id": row["paper_id"],
                    "paper_title": row["paper_id"],
                    "source": {
                        "storage": "external",
                        "path": str(resolved_source),
                        "original_filename": resolved_source.name,
                        "sha256": source_sha,
                        "size_bytes": size,
                        "added_at": None,
                    },
                }
            )
        return {
            "schema_version": LEGACY_PROJECT_SCHEMA,
            "project_id": config["project_id"],
            "name": config["name"],
            "description": config.get("description", ""),
            "topic": paper_collection["topic"],
            "revision": 0,
            "papers": papers,
            "analysis_run_ids": synthesis_collection["source_run_ids"],
            "synthesis_run_id": config.get("synthesis_run_id"),
            "current_collection_path": str(paper_collection_path),
            "created_at": None,
            "updated_at": None,
            "legacy_origin": str(path.relative_to(self.project_root)).replace(
                "\\", "/"
            ),
            "archived_at": None,
        }

    def _require_mutable(
        self, project_id: str, expected_revision: int
    ) -> dict[str, Any]:
        project = self.get(project_id)
        if project["schema_version"] != PROJECT_SCHEMA:
            raise UIError(
                "ui.project_migration_required",
                "旧版只读项目必须先迁移，才能修改。",
            )
        if project["revision"] != expected_revision:
            raise UIError(
                "ui.project_revision_conflict",
                f"项目已被修改：期望 revision {expected_revision}，"
                f"当前为 {project['revision']}。",
            )
        if project.get("archived_at"):
            raise UIError(
                "ui.project_archived",
                "项目已归档，只能读取；恢复后才能修改或运行。",
            )
        return json.loads(json.dumps(project, ensure_ascii=False))

    def _validate_project(self, project: dict[str, Any]) -> None:
        if project.get("schema_version") != PROJECT_SCHEMA:
            raise UIError(
                "ui.schema_unsupported",
                f"界面项目 schema 不受支持：{project.get('schema_version')}",
            )
        self._validate_project_id(str(project.get("project_id", "")))
        if not isinstance(project.get("revision"), int):
            raise UIError("ui.project_invalid", "项目 revision 必须是整数。")
        if not isinstance(project.get("papers"), list):
            raise UIError("ui.project_invalid", "项目 papers 必须是数组。")

    @staticmethod
    def _required_text(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise UIError("ui.input_required", f"{label}不能为空。")
        return text

    @staticmethod
    def _validate_project_id(project_id: str) -> None:
        if not _PROJECT_ID_RE.fullmatch(project_id):
            raise UIError(
                "ui.project_id_invalid",
                "项目 ID 必须以小写字母开头，只能包含小写字母、数字和单个连字符，"
                "长度为 3-64。",
            )

    def _project_dir(self, project_id: str) -> Path:
        return self._inside(self.root, project_id)

    def _project_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    @staticmethod
    def _file_identity(path: Path) -> tuple[str | None, int | None]:
        if not path.is_file():
            return None, None
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return f"sha256:{digest.hexdigest()}", size

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _atomic_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise UIError("ui.artifact_missing", f"缺少文件：{path}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise UIError("ui.artifact_invalid", f"无法读取 JSON：{path}") from exc
        if not isinstance(payload, dict):
            raise UIError("ui.artifact_invalid", f"JSON 必须是对象：{path}")
        return payload

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
