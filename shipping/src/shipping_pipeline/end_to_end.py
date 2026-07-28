from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .llm_analysis import DEFAULT_MODEL_PROFILE
from .pipeline import LiteraturePipeline
from .topic_review import DEFAULT_TOPIC_REVIEW_CONFIG, TopicReviewRunner
from .topic_synthesis import DEFAULT_TOPIC_SYNTHESIS_CONFIG, TopicSynthesisRunner
from .topic_synthesis_contracts import COLLECTION_SCHEMA_VERSION


END_TO_END_COLLECTION_SCHEMA_VERSION = "review_pipeline_collection.v1"
END_TO_END_RUN_SCHEMA_VERSION = "review_pipeline_run.v1"
END_TO_END_RESULT_SCHEMA_VERSION = "review_pipeline_result.v1"

ACCEPTED_BRIEF_STATUSES = {
    "completed",
    "completed_with_failures",
    "completed_with_revisit_failure",
}


class EndToEndPipelineInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class EndToEndPipelineRunner:
    """严格串联来源解析、制卡、单篇简报和跨论文综合。"""

    def __init__(
        self,
        workspace: str | Path,
        *,
        pdf_converter: Any | None = None,
        card_runner: Any | None = None,
        topic_review_runner: Any | None = None,
        topic_synthesis_runner: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.card_runner = card_runner or LiteraturePipeline(
            self.workspace,
            pdf_converter=pdf_converter,
        )
        self.topic_review_runner = topic_review_runner or TopicReviewRunner(
            self.workspace
        )
        self.topic_synthesis_runner = (
            topic_synthesis_runner or TopicSynthesisRunner(self.workspace)
        )

    def run(
        self,
        *,
        collection_path: str | Path,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 120,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        review_config_path: str | Path = DEFAULT_TOPIC_REVIEW_CONFIG,
        synthesis_config_path: str | Path = DEFAULT_TOPIC_SYNTHESIS_CONFIG,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _validate_run_id(resolved_run_id)
        run_dir = self.workspace / "_pipeline_runs" / resolved_run_id
        if run_dir.exists():
            raise EndToEndPipelineInputError(
                "pipeline.run_id_exists",
                f"整套 pipeline run_id 已存在，不能覆盖不可变运行："
                f"{resolved_run_id}",
            )
        run_dir.mkdir(parents=True)

        started_at = _now()
        collection: dict[str, Any] | None = None
        input_snapshot: dict[str, Any] | None = None
        stages: dict[str, dict[str, Any]] = {
            "cards": {"status": "pending"},
            "topic_briefs": {"status": "pending"},
            "topic_synthesis": {"status": "pending"},
        }
        card_runs: list[dict[str, Any]] = []
        brief_runs: list[dict[str, Any]] = []
        synthesis_run: dict[str, Any] | None = None
        failures: list[dict[str, str]] = []

        def checkpoint() -> None:
            _write_manifest(
                run_dir,
                _build_manifest(
                    run_id=resolved_run_id,
                    status="running",
                    started_at=started_at,
                    collection=collection,
                    input_snapshot=input_snapshot,
                    stages=stages,
                    card_runs=card_runs,
                    brief_runs=brief_runs,
                    synthesis_run=synthesis_run,
                    failures=failures,
                ),
            )

        try:
            collection, collection_sha256 = load_end_to_end_collection(
                Path(collection_path)
            )
            input_snapshot = _build_input_snapshot(
                collection,
                collection_path=Path(collection_path),
                collection_sha256=collection_sha256,
                model_profile_path=Path(model_profile_path),
                review_config_path=Path(review_config_path),
                synthesis_config_path=Path(synthesis_config_path),
            )
            _write_json(run_dir / "input" / "collection.json", input_snapshot)
            checkpoint()

            stages["cards"] = {
                "status": "running",
                "paper_count": len(input_snapshot["papers"]),
            }
            checkpoint()
            for paper in input_snapshot["papers"]:
                card_runs.append(
                    self._run_card_stage(
                        paper,
                        topic=str(collection["topic"]),
                    )
                )
                checkpoint()
            card_failures = [
                row for row in card_runs if row["status"] != "completed"
            ]
            stages["cards"] = {
                "status": "failed" if card_failures else "completed",
                "paper_count": len(card_runs),
                "completed_count": len(card_runs) - len(card_failures),
                "failed_count": len(card_failures),
            }
            checkpoint()
            if card_failures:
                failures.append(
                    {
                        "error_code": "pipeline.card_stage_failed",
                        "error_message": (
                            "至少一篇论文未完成 PDF/MD 到 Card，"
                            "未调用任何 LLM。"
                        ),
                    }
                )
                return _finish(
                    run_dir=run_dir,
                    run_id=resolved_run_id,
                    status="failed",
                    started_at=started_at,
                    collection=collection,
                    input_snapshot=input_snapshot,
                    stages=stages,
                    card_runs=card_runs,
                    brief_runs=brief_runs,
                    synthesis_run=synthesis_run,
                    failures=failures,
                )

            stages["topic_briefs"] = {
                "status": "running",
                "paper_count": len(card_runs),
            }
            checkpoint()
            for index, paper in enumerate(input_snapshot["papers"], start=1):
                brief_run_id = f"{resolved_run_id}--brief-{index:03d}"
                brief_runs.append(
                    self._run_brief_stage(
                        paper_id=str(paper["paper_id"]),
                        workspace_paper_id=str(
                            paper["workspace_paper_id"]
                        ),
                        topic=str(collection["topic"]),
                        run_id=brief_run_id,
                        provider=provider,
                        api_url=api_url,
                        api_key_env=api_key_env,
                        timeout=timeout,
                        model_profile_path=Path(model_profile_path),
                        review_config_path=Path(review_config_path),
                    )
                )
                checkpoint()
            failed_briefs = [
                row for row in brief_runs if row["status"] == "failed"
            ]
            included_briefs = [
                row
                for row in brief_runs
                if row["status"] in ACCEPTED_BRIEF_STATUSES
            ]
            excluded_briefs = [
                row for row in brief_runs if row["status"] == "excluded"
            ]
            unknown_briefs = [
                row
                for row in brief_runs
                if row["status"]
                not in ACCEPTED_BRIEF_STATUSES | {"excluded", "failed"}
            ]
            stages["topic_briefs"] = {
                "status": (
                    "failed"
                    if failed_briefs or unknown_briefs
                    else "completed"
                ),
                "paper_count": len(brief_runs),
                "included_count": len(included_briefs),
                "excluded_count": len(excluded_briefs),
                "failed_count": len(failed_briefs) + len(unknown_briefs),
            }
            checkpoint()
            if failed_briefs or unknown_briefs:
                failures.append(
                    {
                        "error_code": "pipeline.topic_brief_stage_failed",
                        "error_message": (
                            "至少一篇应处理论文未形成可用主题简报，"
                            "未使用不完整子集执行跨论文综合。"
                        ),
                    }
                )
                return _finish(
                    run_dir=run_dir,
                    run_id=resolved_run_id,
                    status="failed",
                    started_at=started_at,
                    collection=collection,
                    input_snapshot=input_snapshot,
                    stages=stages,
                    card_runs=card_runs,
                    brief_runs=brief_runs,
                    synthesis_run=synthesis_run,
                    failures=failures,
                )
            if len(included_briefs) < 2:
                failures.append(
                    {
                        "error_code": "pipeline.insufficient_included_papers",
                        "error_message": (
                            "完成相关性排除后少于两篇可用论文，"
                            "不能执行跨论文综合。"
                        ),
                    }
                )
                stages["topic_synthesis"] = {
                    "status": "blocked",
                    "reason": "insufficient_included_papers",
                }
                return _finish(
                    run_dir=run_dir,
                    run_id=resolved_run_id,
                    status="failed",
                    started_at=started_at,
                    collection=collection,
                    input_snapshot=input_snapshot,
                    stages=stages,
                    card_runs=card_runs,
                    brief_runs=brief_runs,
                    synthesis_run=synthesis_run,
                    failures=failures,
                )

            synthesis_collection_path = (
                run_dir / "input" / "topic_synthesis_collection.json"
            )
            _write_json(
                synthesis_collection_path,
                {
                    "schema_version": COLLECTION_SCHEMA_VERSION,
                    "topic": collection["topic"],
                    "source_run_ids": [
                        row["run_id"] for row in included_briefs
                    ],
                },
            )
            stages["topic_synthesis"] = {
                "status": "running",
                "included_paper_count": len(included_briefs),
            }
            checkpoint()
            synthesis_run_id = f"{resolved_run_id}--synthesis"
            synthesis_run = self._run_synthesis_stage(
                collection_path=synthesis_collection_path,
                run_id=synthesis_run_id,
                provider=provider,
                api_url=api_url,
                api_key_env=api_key_env,
                timeout=timeout,
                model_profile_path=Path(model_profile_path),
                synthesis_config_path=Path(synthesis_config_path),
            )
            stages["topic_synthesis"] = {
                "status": synthesis_run["status"],
                "included_paper_count": len(included_briefs),
                "run_id": synthesis_run_id,
            }
            checkpoint()
            if synthesis_run["status"] != "completed":
                failures.append(
                    {
                        "error_code": "pipeline.topic_synthesis_failed",
                        "error_message": (
                            "跨论文主题综合未通过，顶层 pipeline 不发布正式结果。"
                        ),
                    }
                )
                return _finish(
                    run_dir=run_dir,
                    run_id=resolved_run_id,
                    status="failed",
                    started_at=started_at,
                    collection=collection,
                    input_snapshot=input_snapshot,
                    stages=stages,
                    card_runs=card_runs,
                    brief_runs=brief_runs,
                    synthesis_run=synthesis_run,
                    failures=failures,
                )

            return _finish(
                run_dir=run_dir,
                run_id=resolved_run_id,
                status="completed",
                started_at=started_at,
                collection=collection,
                input_snapshot=input_snapshot,
                stages=stages,
                card_runs=card_runs,
                brief_runs=brief_runs,
                synthesis_run=synthesis_run,
                failures=failures,
            )
        except Exception as exc:
            failures.append(
                {
                    "error_code": str(
                        getattr(exc, "code", type(exc).__name__)
                    ),
                    "error_message": str(exc),
                }
            )
            return _finish(
                run_dir=run_dir,
                run_id=resolved_run_id,
                status="failed",
                started_at=started_at,
                collection=collection,
                input_snapshot=input_snapshot,
                stages=stages,
                card_runs=card_runs,
                brief_runs=brief_runs,
                synthesis_run=synthesis_run,
                failures=failures,
            )

    def _run_card_stage(
        self,
        paper: dict[str, Any],
        *,
        topic: str,
    ) -> dict[str, Any]:
        paper_id = str(paper["paper_id"])
        workspace_paper_id = str(paper["workspace_paper_id"])
        try:
            result = self.card_runner.run(
                Path(str(paper["resolved_source"])),
                paper_id=paper_id,
                workspace_id=workspace_paper_id,
                review_topic=topic,
            )
            generation_id = None
            material_count = 0
            convert_provider = None
            run_record_path = (
                self.workspace / workspace_paper_id / "run.json"
            )
            if run_record_path.is_file():
                payload = json.loads(
                    run_record_path.read_text(encoding="utf-8-sig")
                )
                generation_id = payload.get("generation_id")
                stages = payload.get("stages", {})
                if isinstance(stages, dict):
                    convert = stages.get("convert", {})
                    material = stages.get("material", {})
                    if isinstance(convert, dict):
                        convert_provider = convert.get("provider")
                    if isinstance(material, dict):
                        material_count = int(
                            material.get("material_count") or 0
                        )
            return {
                "paper_id": paper_id,
                "workspace_paper_id": workspace_paper_id,
                "source": paper["source"],
                "resolved_source": paper["resolved_source"],
                "source_sha256": paper["source_sha256"],
                "status": str(result.status),
                "structure_quality": str(result.structure_quality),
                "issue_count": int(result.issue_count),
                "generation_id": generation_id,
                "convert_provider": convert_provider,
                "material_count": material_count,
                "workspace_path": str(result.workspace_path),
                "failure": None,
            }
        except Exception as exc:
            return {
                "paper_id": paper_id,
                "workspace_paper_id": workspace_paper_id,
                "source": paper["source"],
                "resolved_source": paper["resolved_source"],
                "source_sha256": paper["source_sha256"],
                "status": "failed",
                "structure_quality": "red",
                "issue_count": 1,
                "generation_id": None,
                "convert_provider": None,
                "material_count": 0,
                "workspace_path": str(
                    self.workspace / workspace_paper_id
                ),
                "failure": {
                    "error_code": str(
                        getattr(exc, "code", type(exc).__name__)
                    ),
                    "error_message": str(exc),
                },
            }

    def _run_brief_stage(self, **kwargs: Any) -> dict[str, Any]:
        paper_id = str(kwargs["paper_id"])
        run_id = str(kwargs["run_id"])
        try:
            result = self.topic_review_runner.run(**kwargs)
            child_manifest = _read_optional_json(
                self.workspace
                / "_topic_reviews"
                / "runs"
                / run_id
                / "manifest.json"
            )
            if not result.get("usage") and not child_manifest:
                raise EndToEndPipelineInputError(
                    "pipeline.topic_brief_manifest_missing",
                    f"主题简报子运行缺少可读取 manifest：{run_id}",
                )
            return {
                "paper_id": paper_id,
                "run_id": run_id,
                "status": str(result.get("status", "failed")),
                "failure_codes": list(result.get("failure_codes", [])),
                "run_dir": result.get("run_dir"),
                "manifest_path": result.get("manifest_path"),
                "review_path": result.get("review_path"),
                "paper_relevance": result.get("paper_relevance"),
                "evidence_unit_count": int(
                    result.get("evidence_unit_count") or 0
                ),
                "revisit_status": result.get("revisit_status"),
                "request_count": int(
                    result.get("request_count")
                    or len(child_manifest.get("requests", []))
                ),
                "usage": dict(
                    result.get("usage")
                    or child_manifest.get("usage")
                    or _empty_usage()
                ),
            }
        except Exception as exc:
            return {
                "paper_id": paper_id,
                "run_id": run_id,
                "status": "failed",
                "failure_codes": [
                    str(getattr(exc, "code", type(exc).__name__))
                ],
                "run_dir": str(
                    self.workspace / "_topic_reviews" / "runs" / run_id
                ),
                "manifest_path": None,
                "review_path": None,
                "paper_relevance": None,
                "evidence_unit_count": 0,
                "revisit_status": None,
                "request_count": 0,
                "usage": _empty_usage(),
            }

    def _run_synthesis_stage(self, **kwargs: Any) -> dict[str, Any]:
        run_id = str(kwargs["run_id"])
        try:
            result = self.topic_synthesis_runner.run(**kwargs)
            child_manifest = _read_optional_json(
                self.workspace
                / "_topic_syntheses"
                / "runs"
                / run_id
                / "manifest.json"
            )
            if not result.get("usage") and not child_manifest:
                raise EndToEndPipelineInputError(
                    "pipeline.topic_synthesis_manifest_missing",
                    f"跨论文综合子运行缺少可读取 manifest：{run_id}",
                )
            return {
                "run_id": run_id,
                "status": str(result.get("status", "failed")),
                "failure_codes": list(result.get("failure_codes", [])),
                "run_dir": result.get("run_dir"),
                "manifest_path": result.get("manifest_path"),
                "review_path": result.get("review_path"),
                "request_count": int(
                    result.get("request_count")
                    or len(child_manifest.get("requests", []))
                ),
                "usage": dict(
                    result.get("usage")
                    or child_manifest.get("usage")
                    or _empty_usage()
                ),
            }
        except Exception as exc:
            return {
                "run_id": run_id,
                "status": "failed",
                "failure_codes": [
                    str(getattr(exc, "code", type(exc).__name__))
                ],
                "run_dir": str(
                    self.workspace / "_topic_syntheses" / "runs" / run_id
                ),
                "manifest_path": None,
                "review_path": None,
                "request_count": 0,
                "usage": _empty_usage(),
            }


def load_end_to_end_collection(
    path: Path,
) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EndToEndPipelineInputError(
            "pipeline.collection_read_failed",
            f"无法读取整套 pipeline collection：{path}",
        ) from exc
    if not isinstance(payload, dict):
        raise EndToEndPipelineInputError(
            "pipeline.collection_invalid",
            "整套 pipeline collection 必须是 JSON 对象。",
        )
    required = {"schema_version", "topic", "papers"}
    if set(payload) != required:
        raise EndToEndPipelineInputError(
            "pipeline.collection_fields_invalid",
            f"collection 字段不匹配：missing={sorted(required - set(payload))}, "
            f"unknown={sorted(set(payload) - required)}",
        )
    if payload.get("schema_version") != END_TO_END_COLLECTION_SCHEMA_VERSION:
        raise EndToEndPipelineInputError(
            "pipeline.collection_schema_invalid",
            f"collection schema_version 必须是 "
            f"{END_TO_END_COLLECTION_SCHEMA_VERSION!r}。",
        )
    topic = payload.get("topic")
    papers = payload.get("papers")
    if not isinstance(topic, str) or not topic.strip():
        raise EndToEndPipelineInputError(
            "pipeline.topic_invalid",
            "综述主题不能为空。",
        )
    if not isinstance(papers, list) or len(papers) < 2:
        raise EndToEndPipelineInputError(
            "pipeline.paper_count_invalid",
            "整套 pipeline 至少需要两篇显式论文来源。",
        )
    normalized_papers: list[dict[str, str]] = []
    for index, paper in enumerate(papers):
        allowed_fields = {"paper_id", "workspace_paper_id", "source"}
        if (
            not isinstance(paper, dict)
            or not {"paper_id", "source"}.issubset(paper)
            or not set(paper).issubset(allowed_fields)
        ):
            raise EndToEndPipelineInputError(
                "pipeline.paper_fields_invalid",
                f"papers[{index}] 必须包含 paper_id 和 source，"
                "可选 workspace_paper_id。",
            )
        paper_id = paper.get("paper_id")
        workspace_paper_id = paper.get(
            "workspace_paper_id", paper_id
        )
        source = paper.get("source")
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise EndToEndPipelineInputError(
                "pipeline.paper_id_invalid",
                f"papers[{index}].paper_id 必须是非空字符串。",
            )
        if (
            paper_id in {".", ".."}
            or "/" in paper_id
            or "\\" in paper_id
        ):
            raise EndToEndPipelineInputError(
                "pipeline.paper_id_invalid",
                f"papers[{index}].paper_id 不能包含路径。",
            )
        if (
            not isinstance(workspace_paper_id, str)
            or not workspace_paper_id.strip()
            or workspace_paper_id in {".", ".."}
            or "/" in workspace_paper_id
            or "\\" in workspace_paper_id
        ):
            raise EndToEndPipelineInputError(
                "pipeline.workspace_paper_id_invalid",
                f"papers[{index}].workspace_paper_id 必须是无路径的非空字符串。",
            )
        if not isinstance(source, str) or not source.strip():
            raise EndToEndPipelineInputError(
                "pipeline.source_invalid",
                f"papers[{index}].source 必须是非空字符串。",
            )
        normalized_papers.append(
            {
                "paper_id": paper_id,
                "workspace_paper_id": workspace_paper_id,
                "source": source,
            }
        )
    paper_ids = [row["paper_id"] for row in normalized_papers]
    if len(paper_ids) != len(set(paper_ids)):
        raise EndToEndPipelineInputError(
            "pipeline.duplicate_paper_id",
            "collection 中的 paper_id 不得重复。",
        )
    workspace_paper_ids = [
        row["workspace_paper_id"] for row in normalized_papers
    ]
    if len(workspace_paper_ids) != len(set(workspace_paper_ids)):
        raise EndToEndPipelineInputError(
            "pipeline.duplicate_workspace_paper_id",
            "collection 中的 workspace_paper_id 不得重复。",
        )
    sources = [row["source"] for row in normalized_papers]
    if len(sources) != len(set(sources)):
        raise EndToEndPipelineInputError(
            "pipeline.duplicate_source",
            "collection 中的 source 不得重复。",
        )
    return (
        {
            "schema_version": END_TO_END_COLLECTION_SCHEMA_VERSION,
            "topic": topic,
            "papers": normalized_papers,
        },
        f"sha256:{hashlib.sha256(raw).hexdigest()}",
    )


def _build_input_snapshot(
    collection: dict[str, Any],
    *,
    collection_path: Path,
    collection_sha256: str,
    model_profile_path: Path,
    review_config_path: Path,
    synthesis_config_path: Path,
) -> dict[str, Any]:
    base = collection_path.resolve().parent
    papers = []
    for paper in collection["papers"]:
        source = Path(str(paper["source"]))
        resolved = source if source.is_absolute() else base / source
        resolved = resolved.resolve()
        papers.append(
            {
                "paper_id": paper["paper_id"],
                "workspace_paper_id": paper["workspace_paper_id"],
                "source": paper["source"],
                "resolved_source": str(resolved),
                "source_sha256": (
                    _sha256_file(resolved) if resolved.is_file() else None
                ),
            }
        )
    return {
        "schema_version": END_TO_END_COLLECTION_SCHEMA_VERSION,
        "collection_path": str(collection_path.resolve()),
        "collection_sha256": collection_sha256,
        "topic": collection["topic"],
        "papers": papers,
        "model_profile": _file_identity(model_profile_path),
        "review_config": _file_identity(review_config_path),
        "synthesis_config": _file_identity(synthesis_config_path),
    }


def _file_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "sha256": _sha256_file(resolved) if resolved.is_file() else None,
    }


def _finish(
    *,
    run_dir: Path,
    run_id: str,
    status: str,
    started_at: str,
    collection: dict[str, Any] | None,
    input_snapshot: dict[str, Any] | None,
    stages: dict[str, dict[str, Any]],
    card_runs: list[dict[str, Any]],
    brief_runs: list[dict[str, Any]],
    synthesis_run: dict[str, Any] | None,
    failures: list[dict[str, str]],
) -> dict[str, Any]:
    output_path = run_dir / "output" / "pipeline_result.json"
    manifest = _build_manifest(
        run_id=run_id,
        status=status,
        started_at=started_at,
        collection=collection,
        input_snapshot=input_snapshot,
        stages=stages,
        card_runs=card_runs,
        brief_runs=brief_runs,
        synthesis_run=synthesis_run,
        failures=failures,
    )
    if status == "completed":
        included = [
            row
            for row in brief_runs
            if row["status"] in ACCEPTED_BRIEF_STATUSES
        ]
        excluded = [row for row in brief_runs if row["status"] == "excluded"]
        _atomic_write_json(
            output_path,
            {
                "schema_version": END_TO_END_RESULT_SCHEMA_VERSION,
                "run_id": run_id,
                "topic": collection["topic"] if collection else "",
                "paper_count": len(card_runs),
                "included_paper_count": len(included),
                "excluded_paper_count": len(excluded),
                "card_runs": card_runs,
                "topic_brief_runs": brief_runs,
                "topic_synthesis_run": synthesis_run,
                "request_count": manifest["request_count"],
                "usage": manifest["usage"],
                "final_review_path": (
                    synthesis_run.get("review_path")
                    if synthesis_run is not None
                    else None
                ),
            },
        )
    elif output_path.exists():
        output_path.unlink()

    _atomic_write_text(
        run_dir / "review" / "pipeline_summary.md",
        _render_summary(manifest),
    )
    _write_manifest(run_dir, manifest)
    return {
        "run_id": run_id,
        "status": status,
        "topic": collection["topic"] if collection else None,
        "paper_count": len(card_runs),
        "included_paper_count": sum(
            row["status"] in ACCEPTED_BRIEF_STATUSES for row in brief_runs
        ),
        "excluded_paper_count": sum(
            row["status"] == "excluded" for row in brief_runs
        ),
        "request_count": manifest["request_count"],
        "usage": manifest["usage"],
        "failure_codes": [row["error_code"] for row in failures],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "review_path": str(run_dir / "review" / "pipeline_summary.md"),
        "final_review_path": (
            synthesis_run.get("review_path")
            if status == "completed" and synthesis_run is not None
            else None
        ),
    }


def _build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    collection: dict[str, Any] | None,
    input_snapshot: dict[str, Any] | None,
    stages: dict[str, dict[str, Any]],
    card_runs: list[dict[str, Any]],
    brief_runs: list[dict[str, Any]],
    synthesis_run: dict[str, Any] | None,
    failures: list[dict[str, str]],
) -> dict[str, Any]:
    usage_rows = [row.get("usage", {}) for row in brief_runs]
    if synthesis_run is not None:
        usage_rows.append(synthesis_run.get("usage", {}))
    return {
        "schema_version": END_TO_END_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": None if status == "running" else _now(),
        "topic": collection["topic"] if collection else None,
        "collection_sha256": (
            input_snapshot["collection_sha256"]
            if input_snapshot is not None
            else None
        ),
        "paper_count": len(card_runs),
        "included_paper_count": sum(
            row["status"] in ACCEPTED_BRIEF_STATUSES for row in brief_runs
        ),
        "excluded_paper_count": sum(
            row["status"] == "excluded" for row in brief_runs
        ),
        "stages": stages,
        "card_runs": card_runs,
        "topic_brief_runs": brief_runs,
        "topic_synthesis_run": synthesis_run,
        "request_count": sum(
            int(row.get("request_count") or 0) for row in brief_runs
        )
        + (
            int(synthesis_run.get("request_count") or 0)
            if synthesis_run is not None
            else 0
        ),
        "usage": {
            key: sum(int(row.get(key) or 0) for row in usage_rows)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            )
        },
        "failure_count": len(failures),
        "failure_codes": [row["error_code"] for row in failures],
        "failures": failures,
        "outputs": (
            {
                "pipeline_result": "output/pipeline_result.json",
                "pipeline_summary": "review/pipeline_summary.md",
                "final_review": (
                    synthesis_run.get("review_path")
                    if synthesis_run is not None
                    else None
                ),
            }
            if status == "completed"
            else {"pipeline_summary": "review/pipeline_summary.md"}
        ),
    }


def _render_summary(manifest: dict[str, Any]) -> str:
    lines = [
        "# 整套文献综述 Pipeline 运行摘要",
        "",
        f"- 运行 ID：{manifest['run_id']}",
        f"- 状态：{manifest['status']}",
        f"- 综述主题：{manifest.get('topic') or '未读取'}",
        f"- 来源论文：{manifest['paper_count']}",
        f"- 纳入综合：{manifest['included_paper_count']}",
        f"- 排除：{manifest['excluded_paper_count']}",
        f"- LLM 请求：{manifest['request_count']}",
        f"- 总 Token：{manifest['usage']['total_tokens']}",
        "",
        "## 阶段状态",
        "",
    ]
    for name, stage in manifest["stages"].items():
        lines.append(f"- {name}：{stage.get('status', 'unknown')}")
    lines.extend(["", "## 论文处理", ""])
    if manifest["card_runs"]:
        brief_by_paper = {
            row["paper_id"]: row for row in manifest["topic_brief_runs"]
        }
        for card in manifest["card_runs"]:
            brief = brief_by_paper.get(str(card["paper_id"]))
            brief_status = brief["status"] if brief is not None else "未运行"
            lines.append(
                f"- {card['paper_id']}：Card={card['status']}，"
                f"材料卡={card.get('material_count', 0)}，简报={brief_status}"
            )
    else:
        lines.append("- 尚未处理论文。")
    if manifest["failures"]:
        lines.extend(["", "## 失败记录", ""])
        for failure in manifest["failures"]:
            lines.append(
                f"- {failure['error_code']}：{failure['error_message']}"
            )
    synthesis = manifest.get("topic_synthesis_run")
    if synthesis is not None:
        lines.extend(
            [
                "",
                "## 跨论文综合",
                "",
                f"- 状态：{synthesis['status']}",
                f"- 运行 ID：{synthesis['run_id']}",
                f"- 最终报告：{synthesis.get('review_path') or '未发布'}",
            ]
        )
    return "\n".join(lines) + "\n"


def _validate_run_id(run_id: str) -> None:
    if (
        not run_id
        or run_id in {".", ".."}
        or "/" in run_id
        or "\\" in run_id
    ):
        raise EndToEndPipelineInputError(
            "pipeline.run_id_invalid",
            "整套 pipeline run_id 必须是单个非空目录名。",
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _empty_usage() -> dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def _write_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    _write_json(run_dir / "manifest.json", manifest)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _atomic_write_json(path: Path, payload: object) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _now() -> str:
    return datetime.now(UTC).isoformat()
