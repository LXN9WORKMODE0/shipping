from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .llm_analysis import DEFAULT_MODEL_PROFILE
from .paper_topic_screening import (
    PAPER_POOL_SCREENING_ROOT,
    load_paper_topic_screening,
)
from .research_understanding import (
    DEFAULT_UNDERSTANDING_CONFIG,
    PaperUnderstandingRunner,
)


PAPER_UNDERSTANDING_BATCH_ROOT = "_paper_understanding_batches"
PAPER_UNDERSTANDING_BATCH_RUN_SCHEMA_VERSION = (
    "llm.paper_understanding_batch_run.v1"
)
ALLOWED_RELEVANCE = frozenset({"core", "supporting", "peripheral"})


class PaperUnderstandingBatchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


class PaperUnderstandingBatchRunner:
    def __init__(
        self, workspace: str | Path, *, paper_runner=None, screening_loader=None
    ) -> None:
        self.workspace = Path(workspace)
        self.paper_runner = paper_runner or PaperUnderstandingRunner(self.workspace)
        self.screening_loader = screening_loader or load_paper_topic_screening

    def run(
        self,
        *,
        screening_run_id: str,
        run_id: str | None = None,
        relevance: Iterable[str] = ("core",),
        record_ids: Iterable[str] | None = None,
        max_papers: int | None = None,
        resume_from_run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        understanding_config_path: str | Path = DEFAULT_UNDERSTANDING_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        selected_relevance = tuple(dict.fromkeys(str(value) for value in relevance))
        invalid = sorted(set(selected_relevance) - ALLOWED_RELEVANCE)
        if not selected_relevance or invalid:
            raise PaperUnderstandingBatchError(
                "understanding_batch.relevance_invalid",
                f"纳入等级无效：{invalid or list(selected_relevance)}",
            )
        if max_papers is not None and max_papers < 1:
            raise PaperUnderstandingBatchError(
                "understanding_batch.limit_invalid", "max_papers必须>=1。"
            )
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved, "run_id")
        _safe_segment(screening_run_id, "screening_run_id")
        run_dir = self.workspace / PAPER_UNDERSTANDING_BATCH_ROOT / "runs" / resolved
        if run_dir.exists():
            raise PaperUnderstandingBatchError(
                "understanding_batch.run_exists", f"run_id已存在：{resolved}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        screening = _load_pool_screening(self.workspace, screening_run_id)
        candidates = _load_candidates(
            self.workspace,
            screening,
            selected_relevance=selected_relevance,
            screening_loader=self.screening_loader,
        )
        requested_record_ids = tuple(
            dict.fromkeys(str(value) for value in (record_ids or ()))
        )
        if requested_record_ids:
            candidate_ids = {row["record_id"] for row in candidates}
            unavailable = [
                record_id
                for record_id in requested_record_ids
                if record_id not in candidate_ids
            ]
            if unavailable:
                raise PaperUnderstandingBatchError(
                    "understanding_batch.record_unavailable",
                    f"指定论文不在当前筛选入选集中：{unavailable}",
                )
            requested_set = set(requested_record_ids)
            candidates = [
                row for row in candidates if row["record_id"] in requested_set
            ]
            order = {
                record_id: index
                for index, record_id in enumerate(requested_record_ids)
            }
            candidates.sort(key=lambda row: order[row["record_id"]])
        reused, resume_rejections = self._load_reusable(
            resume_from_run_id=resume_from_run_id,
            screening=screening,
            selected_relevance=selected_relevance,
            candidates=candidates,
        )
        remaining = [row for row in candidates if row["record_id"] not in reused]
        selected = remaining[:max_papers] if max_papers is not None else remaining
        request = {
            "screening_run_id": screening_run_id,
            "topic": screening["topic"],
            "selected_relevance": list(selected_relevance),
            "requested_record_ids": list(requested_record_ids),
            "max_papers": max_papers,
            "resume_from_run_id": resume_from_run_id,
            "candidate_record_ids": [row["record_id"] for row in candidates],
            "selected_record_ids": [row["record_id"] for row in selected],
            "reused_record_ids": sorted(reused),
        }
        _write_json(run_dir / "input" / "request.json", request)
        _write_jsonl(run_dir / "audit" / "resume_rejections.jsonl", resume_rejections)

        child_runs = list(reused.values())
        for index, row in enumerate(selected, start=1):
            child_run_id = f"{resolved}--paper-{index:04d}"
            try:
                result = self.paper_runner.run(
                    paper_id=row["paper_id"],
                    workspace_paper_id=row["workspace_paper_id"],
                    topic=screening["topic"],
                    run_id=child_run_id,
                    provider=provider,
                    api_url=api_url,
                    api_key_env=api_key_env,
                    model_profile_path=model_profile_path,
                    understanding_config_path=understanding_config_path,
                    timeout=timeout,
                )
            except Exception as exc:
                result = {
                    "run_id": child_run_id,
                    "status": "failed",
                    "paper_id": row["paper_id"],
                    "workspace_paper_id": row["workspace_paper_id"],
                    "failure": {
                        "error_code": getattr(exc, "code", type(exc).__name__),
                        "error_message": str(exc),
                        "stage": "paper_runner",
                    },
                }
            child_runs.append({
                "record_id": row["record_id"],
                "screening_run_id": row["screening_run_id"],
                "paper_relevance": row["paper_relevance"],
                "generation_id": row["generation_id"],
                **result,
            })
            _write_jsonl(run_dir / "runs" / "child_runs.jsonl", child_runs)

        child_by_record = {str(row["record_id"]): row for row in child_runs}
        candidate_by_record = {str(row["record_id"]): row for row in candidates}
        records = []
        for source in screening["records"]:
            record_id = str(source["record_id"])
            candidate = candidate_by_record.get(record_id)
            child = child_by_record.get(record_id)
            if child:
                status = "understood" if child["status"] == "completed" else "understanding_failed"
            elif candidate:
                status = "pending_understanding"
            elif source.get("screening_status") != "screened":
                status = "screening_unavailable"
            else:
                status = "not_selected"
            records.append({
                "record_id": record_id,
                "relative_path": source.get("relative_path"),
                "paper_relevance": source.get("paper_relevance"),
                "understanding_status": status,
                "understanding_run_id": child.get("run_id") if child else None,
                "generation_id": (
                    child.get("generation_id") if child else
                    candidate.get("generation_id") if candidate else None
                ),
                "failure": child.get("failure") if child else None,
            })
        summary = _summarize(records)
        status = (
            "completed_with_failures" if summary["understanding_failed_count"]
            else "completed_partial" if summary["pending_understanding_count"]
            else "completed"
        )
        output = {
            "schema_version": "llm.paper_understanding_batch.v1",
            "run_id": resolved,
            "screening_run_id": screening_run_id,
            "topic": screening["topic"],
            "selected_relevance": list(selected_relevance),
            "requested_record_ids": list(requested_record_ids),
            "summary": summary,
            "records": records,
            "successful_understanding_run_ids": [
                row["understanding_run_id"]
                for row in records
                if row["understanding_status"] == "understood"
            ],
        }
        _write_json(run_dir / "output" / "paper_understanding_batch.json", output)
        _write_jsonl(run_dir / "output" / "paper_understanding_batch.jsonl", records)
        _write_text(run_dir / "review" / "paper_understanding_batch.md", _render(output))
        manifest = {
            "schema_version": PAPER_UNDERSTANDING_BATCH_RUN_SCHEMA_VERSION,
            "run_id": resolved,
            "status": status,
            "started_at": started_at,
            "finished_at": _now(),
            "screening_run_id": screening_run_id,
            "resume_from_run_id": resume_from_run_id,
            "summary": summary,
            "failure": None,
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": status,
            "summary": summary,
            "report_path": str(run_dir / "review" / "paper_understanding_batch.md"),
            "failure": None,
        }

    def _load_reusable(
        self,
        *,
        resume_from_run_id: str | None,
        screening: dict[str, Any],
        selected_relevance: tuple[str, ...],
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
        if resume_from_run_id is None:
            return {}, []
        _safe_segment(resume_from_run_id, "resume_from_run_id")
        old_dir = self.workspace / PAPER_UNDERSTANDING_BATCH_ROOT / "runs" / resume_from_run_id
        old_request = _read_json(old_dir / "input" / "request.json")
        old_output = _read_json(old_dir / "output" / "paper_understanding_batch.json")
        if (
            old_request.get("topic") != screening["topic"]
            or tuple(old_request.get("selected_relevance") or ()) != selected_relevance
        ):
            raise PaperUnderstandingBatchError(
                "understanding_batch.resume_mismatch",
                "续跑主题或纳入等级与当前批次不一致。",
            )
        current = {row["record_id"]: row for row in candidates}
        reused: dict[str, dict[str, Any]] = {}
        rejected: list[dict[str, str]] = []
        for row in old_output["records"]:
            if row.get("understanding_status") != "understood":
                continue
            record_id = str(row["record_id"])
            candidate = current.get(record_id)
            if candidate is None or candidate["generation_id"] != row.get("generation_id"):
                rejected.append({
                    "record_id": record_id,
                    "understanding_run_id": str(row.get("understanding_run_id") or ""),
                    "reason_code": "understanding_batch.reused_generation_changed",
                    "reason": "筛选选择或Card代际已变化，拒绝复用旧理解结果。",
                })
                continue
            run_id = str(row["understanding_run_id"])
            manifest = _read_json(
                self.workspace / "_paper_understandings" / "runs" / run_id / "manifest.json"
            )
            if manifest.get("status") != "completed" or manifest.get("generation_id") != candidate["generation_id"]:
                rejected.append({
                    "record_id": record_id,
                    "understanding_run_id": run_id,
                    "reason_code": "understanding_batch.reused_run_invalid",
                    "reason": "旧理解运行不可用或无法匹配当前Card代际。",
                })
                continue
            reused[record_id] = {
                "record_id": record_id,
                "screening_run_id": candidate["screening_run_id"],
                "paper_relevance": candidate["paper_relevance"],
                "generation_id": candidate["generation_id"],
                "run_id": run_id,
                "status": "completed",
                "paper_id": candidate["paper_id"],
                "workspace_paper_id": candidate["workspace_paper_id"],
                "failure": None,
                "reused_from_run_id": resume_from_run_id,
            }
        return reused, rejected


def _load_pool_screening(workspace: Path, run_id: str) -> dict[str, Any]:
    run_dir = workspace / PAPER_POOL_SCREENING_ROOT / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json")
    output = _read_json(run_dir / "output" / "paper_pool_screening.json")
    if manifest.get("status") not in {"completed", "completed_with_failures"}:
        raise PaperUnderstandingBatchError(
            "understanding_batch.screening_incomplete", "筛选运行尚未完成。"
        )
    if output.get("run_id") != run_id or not str(output.get("topic") or "").strip():
        raise PaperUnderstandingBatchError(
            "understanding_batch.screening_invalid", "筛选运行身份或主题无效。"
        )
    return output


def _load_candidates(
    workspace: Path,
    screening: dict[str, Any],
    *,
    selected_relevance: tuple[str, ...],
    screening_loader,
) -> list[dict[str, Any]]:
    result = []
    for row in screening["records"]:
        if row.get("screening_status") != "screened" or row.get("paper_relevance") not in selected_relevance:
            continue
        child = screening_loader(workspace, str(row["screening_run_id"]))
        if child["paper_relevance"] != row["paper_relevance"]:
            raise PaperUnderstandingBatchError(
                "understanding_batch.screening_replay_mismatch",
                f"单篇筛选与论文池账本不一致：{row['record_id']}",
            )
        result.append({
            "record_id": str(row["record_id"]),
            "screening_run_id": str(row["screening_run_id"]),
            "paper_relevance": str(row["paper_relevance"]),
            "paper_id": str(child["paper_id"]),
            "workspace_paper_id": str(child["workspace_paper_id"]),
            "generation_id": str(child["generation_id"]),
        })
    return result


def _summarize(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "source_record_count": len(records),
        "selected_count": sum(row["understanding_status"] in {"understood", "understanding_failed", "pending_understanding"} for row in records),
        "understood_count": sum(row["understanding_status"] == "understood" for row in records),
        "understanding_failed_count": sum(row["understanding_status"] == "understanding_failed" for row in records),
        "pending_understanding_count": sum(row["understanding_status"] == "pending_understanding" for row in records),
        "not_selected_count": sum(row["understanding_status"] == "not_selected" for row in records),
        "screening_unavailable_count": sum(row["understanding_status"] == "screening_unavailable" for row in records),
    }


def _render(output: dict[str, Any]) -> str:
    summary = output["summary"]
    return "\n".join([
        "# Paper Understanding批处理账本", "",
        f"- 综述主题：{output['topic']}",
        f"- 纳入等级：`{', '.join(output['selected_relevance'])}`",
        f"- 入选论文：{summary['selected_count']}",
        f"- 理解完成：{summary['understood_count']}",
        f"- 理解失败：{summary['understanding_failed_count']}",
        f"- 待处理：{summary['pending_understanding_count']}",
        f"- 未入选：{summary['not_selected_count']}",
        f"- 筛选不可用：{summary['screening_unavailable_count']}", "",
        "跨论文综合只应读取 `successful_understanding_run_ids`；失败和未入选论文仍保留在逐篇账本中。", "",
    ])


def _safe_segment(value: str, field: str) -> str:
    if not value or value in {".", ".."} or any(char in value for char in "/\\"):
        raise PaperUnderstandingBatchError(
            "understanding_batch.path_invalid", f"{field}不是安全路径段。"
        )
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperUnderstandingBatchError(
            "understanding_batch.artifact_invalid", f"无法读取JSON产物：{path}"
        ) from exc
    if not isinstance(value, dict):
        raise PaperUnderstandingBatchError(
            "understanding_batch.artifact_invalid", f"JSON产物必须是对象：{path}"
        )
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()
