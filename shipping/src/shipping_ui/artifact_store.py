from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import UIError
from .job_repository import JobRepository
from .project_repository import ProjectRepository


ArtifactStoreError = UIError


class ArtifactStore:
    PROJECT_SCHEMA = "review_ui_project.v1"
    PAPER_COLLECTION_SCHEMA = "review_pipeline_collection.v1"
    SYNTHESIS_COLLECTION_SCHEMA = "llm.topic_synthesis_collection.v1"
    PAPER_RUN_SCHEMA = None
    TOPIC_REVIEW_SCHEMA = "llm.topic_review_run.v2"
    TOPIC_SYNTHESIS_SCHEMA = "llm.topic_synthesis_run.v1"

    def __init__(
        self,
        project_root: str | Path,
        *,
        workspace: str | Path = "workspace",
        project_config_root: str | Path = "config/ui",
        project_repository: ProjectRepository | None = None,
        job_repository: JobRepository | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = self._resolve_inside(self.project_root, workspace)
        self.project_config_root = self._resolve_inside(
            self.project_root, project_config_root
        )
        self.project_repository = project_repository or ProjectRepository(
            self.project_root,
            workspace=workspace,
            legacy_config_root=project_config_root,
        )
        self.job_repository = job_repository

    def list_projects(
        self,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        projects = [
            self._project_summary(definition)
            for definition in self.project_repository.list()
            if include_archived or not definition.get("archived_at")
        ]
        return sorted(projects, key=lambda row: row["name"])

    def get_project(self, project_id: str) -> dict[str, Any]:
        definition = self.project_repository.get(project_id)
        topic_run_by_paper = self._topic_run_by_paper(
            definition.get("analysis_run_ids", [])
        )
        latest_analysis_results = self._latest_paper_job_results(
            project_id,
            "topic_brief",
        )
        papers = [
            self._paper_summary(
                {
                    "paper_id": row["paper_id"],
                    "workspace_paper_id": row.get(
                        "workspace_paper_id", row["paper_id"]
                    ),
                    "source": row["source"].get(
                        "original_filename", row["source"].get("path", "")
                    ),
                },
                topic_run_by_paper,
                latest_analysis_results,
            )
            for row in definition["papers"]
        ]
        synthesis = self._synthesis_summary(
            str(definition["synthesis_run_id"])
            if definition.get("synthesis_run_id")
            else None
        )
        status_counts: dict[str, int] = {}
        for paper in papers:
            status = str(paper["analysis"]["status"])
            status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "schema_version": definition["schema_version"],
            "project_id": definition["project_id"],
            "name": definition["name"],
            "description": definition.get("description", ""),
            "topic": definition["topic"],
            "revision": definition["revision"],
            "archived": bool(definition.get("archived_at")),
            "archived_at": definition.get("archived_at"),
            "current_collection_path": definition.get(
                "current_collection_path"
            ),
            "paper_count": len(papers),
            "card_count": sum(row["card"]["material_count"] for row in papers),
            "evidence_count": sum(
                row["analysis"]["evidence_unit_count"] for row in papers
            ),
            "analysis_status_counts": status_counts,
            "papers": papers,
            "synthesis": synthesis,
        }

    def get_paper(self, project_id: str, paper_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        summary = next(
            (row for row in project["papers"] if row["paper_id"] == paper_id),
            None,
        )
        if summary is None:
            raise ArtifactStoreError(
                "ui.paper_not_found",
                f"项目中不存在论文：{paper_id}",
            )
        paper_dir = self._resolve_inside(
            self.workspace, summary["workspace_paper_id"]
        )
        if summary["card"]["status"] != "completed":
            return {
                "summary": summary,
                "document": {
                    "path": "normalized/document.md",
                    "line_count": 0,
                    "lines": [],
                },
                "cards": [],
                "evidence": [],
                "failures": [],
                "brief_markdown": "",
                "issues": [],
                "coverage": {},
            }
        document_path = self._resolve_inside(paper_dir, "normalized/document.md")
        materials_path = self._resolve_inside(
            paper_dir, "materials/materials.jsonl"
        )
        document = self._read_text(document_path)
        cards = self._read_jsonl(materials_path)
        analysis_run_id = summary["analysis"]["run_id"]
        evidence: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        brief_markdown = ""
        if analysis_run_id:
            run_dir = self._topic_review_dir(analysis_run_id)
            evidence = self._read_jsonl(
                self._resolve_inside(run_dir, "evidence/evidence_units.jsonl")
            )
            failures = self._read_jsonl(
                self._resolve_inside(run_dir, "audit/evidence_failures.jsonl")
            )
            frozen_cards = {
                str(row["material_id"]): row
                for row in self._read_jsonl(
                    self._resolve_inside(run_dir, "input/materials.jsonl")
                )
            }
            for failure in failures:
                failure["material"] = frozen_cards.get(
                    str(failure.get("material_id", ""))
                )
            brief_markdown = self._read_text(
                self._resolve_inside(run_dir, "review/paper_brief.md")
            )
        return {
            "summary": summary,
            "document": {
                "path": "normalized/document.md",
                "line_count": len(document.splitlines()),
                "lines": document.splitlines(),
            },
            "cards": cards,
            "evidence": evidence,
            "failures": failures,
            "brief_markdown": brief_markdown,
            "issues": self._read_json(
                self._resolve_inside(paper_dir, "structure/quality.json")
            ).get("issues", []),
            "coverage": self._read_json(
                self._resolve_inside(paper_dir, "run.json")
            )
            .get("stages", {})
            .get("material", {})
            .get("coverage", {}),
        }

    def get_synthesis(self, project_id: str) -> dict[str, Any]:
        definition = self.project_repository.get(project_id)
        run_id_value = definition.get("synthesis_run_id")
        if not run_id_value:
            raise ArtifactStoreError(
                "ui.synthesis_not_found",
                "当前项目尚未运行跨论文综合。",
            )
        run_id = str(run_id_value)
        run_dir = self._topic_synthesis_dir(run_id)
        manifest = self._read_json(self._resolve_inside(run_dir, "manifest.json"))
        self._require_schema(
            manifest,
            self.TOPIC_SYNTHESIS_SCHEMA,
            "跨论文综合运行",
        )
        output = self._read_json(
            self._resolve_inside(run_dir, "output/topic_synthesis.json")
        )
        evidence_catalog: dict[str, dict[str, Any]] = {}
        for source_run_id in definition.get("analysis_run_ids", []):
            source_dir = self._topic_review_dir(str(source_run_id))
            source_manifest = self._read_json(
                self._resolve_inside(source_dir, "manifest.json")
            )
            for unit in self._read_jsonl(
                self._resolve_inside(source_dir, "evidence/evidence_units.jsonl")
            ):
                evidence_id = str(unit["evidence_unit_id"])
                evidence_catalog[evidence_id] = {
                    **unit,
                    "paper_id": source_manifest["paper_id"],
                    "paper_title": source_manifest["paper_title"],
                    "source_run_id": source_run_id,
                }
        return {
            "run_id": run_id,
            "manifest": manifest,
            "output": output,
            "evidence_catalog": evidence_catalog,
            "report_markdown": self._read_text(
                self._resolve_inside(run_dir, "review/topic_synthesis.md")
            ),
        }

    def list_runs(self, project_id: str | None = None) -> list[dict[str, Any]]:
        allowed_topic: str | None = None
        if project_id:
            allowed_topic = str(self.get_project(project_id)["topic"])
        records: list[dict[str, Any]] = []
        records.extend(
            self._scan_run_root(
                self._resolve_inside(self.workspace, "_pipeline_runs"),
                "pipeline",
                allowed_topic,
            )
        )
        records.extend(
            self._scan_run_root(
                self._resolve_inside(self.workspace, "_topic_reviews/runs"),
                "topic_review",
                allowed_topic,
            )
        )
        records.extend(
            self._scan_run_root(
                self._resolve_inside(self.workspace, "_topic_syntheses/runs"),
                "topic_synthesis",
                allowed_topic,
            )
        )
        return sorted(
            records,
            key=lambda row: self._timestamp_sort_key(row.get("started_at")),
            reverse=True,
        )

    def system_status(self) -> dict[str, Any]:
        projects = self.list_projects(include_archived=True)
        return {
            "mode": "card_jobs",
            "workspace": str(self.workspace),
            "workspace_available": self.workspace.exists(),
            "project_config_root": str(self.project_config_root),
            "project_count": sum(not row["archived"] for row in projects),
            "archived_project_count": sum(
                row["archived"] for row in projects
            ),
        }

    def _project_summary(
        self, definition: dict[str, Any]
    ) -> dict[str, Any]:
        project = self.get_project(str(definition["project_id"]))
        return {
            key: project[key]
            for key in (
                "schema_version",
                "project_id",
                "name",
                "description",
                "topic",
                "revision",
                "archived",
                "archived_at",
                "current_collection_path",
                "paper_count",
                "card_count",
                "evidence_count",
                "analysis_status_counts",
                "synthesis",
            )
        }

    def _paper_summary(
        self,
        paper: dict[str, Any],
        topic_run_by_paper: dict[str, dict[str, Any]],
        latest_analysis_results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        paper_id = str(paper["paper_id"])
        workspace_paper_id = str(
            paper.get("workspace_paper_id", paper_id)
        )
        paper_dir = self._resolve_inside(self.workspace, workspace_paper_id)
        run_path = self._resolve_inside(paper_dir, "run.json")
        run = self._read_json(run_path) if run_path.exists() else {}
        material = run.get("stages", {}).get("material", {})
        analysis_manifest = topic_run_by_paper.get(paper_id)
        analysis = {
            "run_id": None,
            "status": "not_run",
            "paper_relevance": None,
            "source_material_count": 0,
            "selected_material_count": 0,
            "evidence_unit_count": 0,
            "evidence_failure_count": 0,
            "revisit_status": None,
            "request_count": 0,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "failure_codes": [],
        }
        if analysis_manifest:
            analysis = {
                "run_id": analysis_manifest["run_id"],
                "status": analysis_manifest["status"],
                "paper_relevance": analysis_manifest.get("paper_relevance"),
                "source_material_count": analysis_manifest.get(
                    "source_material_count", 0
                ),
                "selected_material_count": analysis_manifest.get(
                    "selected_material_count", 0
                ),
                "evidence_unit_count": analysis_manifest.get(
                    "evidence_unit_count", 0
                ),
                "evidence_failure_count": analysis_manifest.get(
                    "evidence_failure_count", 0
                ),
                "revisit_status": analysis_manifest.get("revisit_status"),
                "request_count": self._request_count(analysis_manifest),
                "usage": analysis_manifest.get("usage", {}),
                "failure_codes": analysis_manifest.get("failure_codes", []),
            }
        elif latest_analysis_results.get(paper_id, {}).get("status") == "failed":
            latest_failure = latest_analysis_results[paper_id]
            analysis["status"] = "failed"
            analysis["run_id"] = latest_failure.get("analysis_run_id")
            analysis["request_count"] = int(
                latest_failure.get("request_count", 0)
            )
            analysis["usage"] = latest_failure.get("usage") or {}
            failure_code = latest_failure.get("failure_code")
            analysis["failure_codes"] = (
                [str(failure_code)] if failure_code else []
            )
        return {
            "paper_id": paper_id,
            "workspace_paper_id": workspace_paper_id,
            "source": paper["source"],
            "paper_title": (
                analysis_manifest.get("paper_title", paper_id)
                if analysis_manifest
                else paper_id
            ),
            "card": {
                "status": run.get("status", "not_run"),
                "generation_id": run.get("generation_id"),
                "structure_quality": run.get("structure_quality"),
                "block_count": material.get("block_count", 0),
                "material_count": material.get("material_count", 0),
                "issue_count": len(run.get("issues", [])),
                "coverage": material.get("coverage", {}),
                "content_kinds": material.get("content_kinds", []),
            },
            "analysis": analysis,
        }

    def _topic_run_by_paper(
        self, source_run_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for run_id in source_run_ids:
            run_dir = self._topic_review_dir(str(run_id))
            manifest = self._read_json(
                self._resolve_inside(run_dir, "manifest.json")
            )
            self._require_schema(
                manifest,
                self.TOPIC_REVIEW_SCHEMA,
                "单篇分析运行",
            )
            paper_id = str(manifest["paper_id"])
            if paper_id in records:
                raise ArtifactStoreError(
                    "ui.duplicate_paper_run",
                    f"项目中同一论文配置了多个单篇运行：{paper_id}",
                )
            records[paper_id] = manifest
        return records

    def _latest_paper_job_results(
        self,
        project_id: str,
        job_type: str,
    ) -> dict[str, dict[str, Any]]:
        if self.job_repository is None:
            return {}
        latest: dict[str, dict[str, Any]] = {}
        for job in self.job_repository.list(project_id):
            if job["job_type"] != job_type:
                continue
            for result in job["paper_results"]:
                paper_id = str(result["paper_id"])
                latest.setdefault(paper_id, result)
        return latest

    def _synthesis_summary(self, run_id: str | None) -> dict[str, Any]:
        if not run_id:
            return {
                "run_id": None,
                "status": "not_run",
                "theme_count": 0,
                "synthesis_unit_count": 0,
                "section_count": 0,
                "source_evidence_count": 0,
                "multi_theme_evidence_count": 0,
                "theme_unassigned_count": 0,
                "assigned_but_unused_count": 0,
                "partial_source_count": 0,
                "usage": {},
            }
        run_dir = self._topic_synthesis_dir(run_id)
        manifest = self._read_json(self._resolve_inside(run_dir, "manifest.json"))
        self._require_schema(
            manifest,
            self.TOPIC_SYNTHESIS_SCHEMA,
            "跨论文综合运行",
        )
        coverage = manifest.get("coverage", {})
        return {
            "run_id": run_id,
            "status": manifest["status"],
            "theme_count": manifest.get("theme_count", 0),
            "synthesis_unit_count": manifest.get("synthesis_unit_count", 0),
            "section_count": manifest.get("section_count", 0),
            "source_evidence_count": manifest.get("source_evidence_count", 0),
            "multi_theme_evidence_count": coverage.get(
                "multi_theme_evidence_count", 0
            ),
            "theme_unassigned_count": coverage.get(
                "theme_unassigned_count", 0
            ),
            "assigned_but_unused_count": coverage.get(
                "assigned_but_unused_count", 0
            ),
            "partial_source_count": len(
                coverage.get("partial_source_run_ids", [])
            ),
            "usage": manifest.get("usage", {}),
        }

    def _scan_run_root(
        self,
        root: Path,
        run_type: str,
        allowed_topic: str | None,
    ) -> list[dict[str, Any]]:
        if not root.exists():
            return []
        records: list[dict[str, Any]] = []
        for run_dir in root.iterdir():
            if not run_dir.is_dir():
                continue
            manifest_path = run_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = self._read_json(manifest_path)
            except ArtifactStoreError:
                continue
            topic = manifest.get("topic")
            if allowed_topic and topic != allowed_topic:
                continue
            usage = manifest.get("usage") or {}
            records.append(
                {
                    "run_id": manifest.get("run_id", run_dir.name),
                    "run_type": run_type,
                    "schema_version": manifest.get("schema_version"),
                    "status": manifest.get("status", "unknown"),
                    "topic": topic,
                    "paper_id": manifest.get("paper_id"),
                    "started_at": manifest.get("started_at"),
                    "finished_at": manifest.get("finished_at"),
                    "request_count": self._request_count(manifest),
                    "total_tokens": usage.get("total_tokens", 0),
                    "failure_count": manifest.get("failure_count", 0),
                    "failure_codes": manifest.get("failure_codes", []),
                }
            )
        return records

    def _load_project_config(
        self, project_id: str
    ) -> tuple[Path, dict[str, Any]]:
        matches: list[tuple[Path, dict[str, Any]]] = []
        if self.project_config_root.exists():
            for path in self.project_config_root.glob("*.json"):
                config = self._read_json(path)
                self._require_schema(config, self.PROJECT_SCHEMA, "界面项目")
                if str(config.get("project_id")) == project_id:
                    matches.append((path, config))
        if not matches:
            raise ArtifactStoreError(
                "ui.project_not_found",
                f"不存在综述任务：{project_id}",
            )
        if len(matches) > 1:
            raise ArtifactStoreError(
                "ui.project_id_duplicate",
                f"综述任务 ID 重复：{project_id}",
            )
        return matches[0]

    def _load_relative_json(
        self, owner_path: Path, relative_path: str
    ) -> dict[str, Any]:
        candidate = (owner_path.parent / relative_path).resolve()
        path = self._resolve_inside(self.project_root, candidate)
        return self._read_json(path)

    def _topic_review_dir(self, run_id: str) -> Path:
        return self._resolve_inside(
            self.workspace, Path("_topic_reviews") / "runs" / run_id
        )

    def _topic_synthesis_dir(self, run_id: str) -> Path:
        return self._resolve_inside(
            self.workspace, Path("_topic_syntheses") / "runs" / run_id
        )

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise ArtifactStoreError(
                "ui.artifact_missing",
                f"缺少产物：{path.relative_to(self.project_root)}",
            ) from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactStoreError(
                "ui.artifact_invalid",
                f"无法读取 JSON 产物：{path.relative_to(self.project_root)}",
            ) from exc
        if not isinstance(payload, dict):
            raise ArtifactStoreError(
                "ui.artifact_type_invalid",
                f"JSON 产物必须是对象：{path.relative_to(self.project_root)}",
            )
        return payload

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except FileNotFoundError as exc:
            raise ArtifactStoreError(
                "ui.artifact_missing",
                f"缺少产物：{path.relative_to(self.project_root)}",
            ) from exc
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ArtifactStoreError(
                    "ui.jsonl_invalid",
                    f"JSONL 第 {line_number} 行无效："
                    f"{path.relative_to(self.project_root)}",
                ) from exc
            if not isinstance(payload, dict):
                raise ArtifactStoreError(
                    "ui.jsonl_row_type_invalid",
                    f"JSONL 第 {line_number} 行不是对象："
                    f"{path.relative_to(self.project_root)}",
                )
            rows.append(payload)
        return rows

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8-sig")
        except FileNotFoundError as exc:
            raise ArtifactStoreError(
                "ui.artifact_missing",
                f"缺少产物：{path.relative_to(self.project_root)}",
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise ArtifactStoreError(
                "ui.artifact_invalid",
                f"无法读取文本产物：{path.relative_to(self.project_root)}",
            ) from exc

    @staticmethod
    def _require_schema(
        payload: dict[str, Any],
        expected: str | None,
        label: str,
    ) -> None:
        if expected is None:
            return
        actual = payload.get("schema_version")
        if actual != expected:
            raise ArtifactStoreError(
                "ui.schema_unsupported",
                f"{label} schema 不受支持：期望 {expected}，实际 {actual}",
            )

    @staticmethod
    def _timestamp_sort_key(value: Any) -> float:
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    @staticmethod
    def _request_count(manifest: dict[str, Any]) -> int:
        explicit = manifest.get("request_count")
        if isinstance(explicit, int):
            return explicit
        requests = manifest.get("requests")
        if isinstance(requests, list):
            return len(requests)
        if isinstance(requests, dict):
            value = requests.get("request_count")
            return value if isinstance(value, int) else 0
        return 0

    @staticmethod
    def _resolve_inside(base: Path, candidate: str | Path) -> Path:
        base = base.resolve()
        path = Path(candidate)
        resolved = path.resolve() if path.is_absolute() else (base / path).resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise ArtifactStoreError(
                "ui.path_outside_root",
                f"路径越过允许根目录：{candidate}",
            ) from exc
        return resolved
