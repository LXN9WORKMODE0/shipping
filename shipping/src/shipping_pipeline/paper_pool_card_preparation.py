from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paper_pool_inventory import load_paper_pool_inventory
from .pipeline import LiteraturePipeline


PAPER_POOL_CARD_ROOT = "_paper_pool_card_preparations"
PAPER_POOL_CARD_RUN_SCHEMA_VERSION = "paper_pool.card_preparation_run.v1"


class PaperPoolCardPreparationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


class PaperPoolCardPreparationRunner:
    def __init__(self, workspace: str | Path, *, pdf_converter=None, card_runner=None) -> None:
        self.workspace = Path(workspace)
        self.card_runner = card_runner or LiteraturePipeline(
            self.workspace, pdf_converter=pdf_converter
        )

    def run(
        self,
        *,
        inventory_run_id: str,
        topic: str,
        run_id: str | None = None,
        max_papers: int | None = None,
        resume_from_run_id: str | None = None,
    ) -> dict[str, Any]:
        if not topic.strip():
            raise PaperPoolCardPreparationError("paper_pool_card.topic_empty", "综述主题不能为空。")
        if max_papers is not None and max_papers < 1:
            raise PaperPoolCardPreparationError("paper_pool_card.limit_invalid", "max_papers必须>=1。")
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / PAPER_POOL_CARD_ROOT / "runs" / resolved
        if run_dir.exists():
            raise PaperPoolCardPreparationError("paper_pool_card.run_exists", f"run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        inventory = load_paper_pool_inventory(self.workspace, inventory_run_id)
        source_root = Path(inventory["source_dir"])
        candidates = [
            row for row in inventory["records"]
            if row["processing_status"] == "needs_card" and row["is_canonical_content"]
        ]
        reused = {}
        if resume_from_run_id is not None:
            _safe_segment(resume_from_run_id)
            old_dir = self.workspace / PAPER_POOL_CARD_ROOT / "runs" / resume_from_run_id
            old_request = _read_json(old_dir / "input" / "request.json")
            old_output = _read_json(old_dir / "output" / "paper_pool_card_preparation.json")
            if (
                old_request.get("inventory_run_id") != inventory_run_id
                or old_request.get("inventory_sha256") != inventory["inventory_sha256"]
                or old_request.get("topic") != topic.strip()
            ):
                raise PaperPoolCardPreparationError(
                    "paper_pool_card.resume_mismatch", "续跑来源与论文池或主题不一致。"
                )
            for row in old_output["card_runs"]:
                if row.get("status") != "completed":
                    continue
                current = _read_json(
                    self.workspace / str(row["workspace_paper_id"])
                    / "materials" / "current.json"
                )
                if (
                    current.get("status") != "completed"
                    or current.get("generation_id") != row.get("generation_id")
                ):
                    raise PaperPoolCardPreparationError(
                        "paper_pool_card.reused_generation_changed",
                        f"已完成Card代际变化：{row['workspace_paper_id']}",
                    )
                reused[str(row["record_id"])] = {**row, "reused_from_run_id": resume_from_run_id}
        remaining = [row for row in candidates if row["record_id"] not in reused]
        selected = remaining[:max_papers] if max_papers is not None else remaining
        _write_json(run_dir / "input" / "request.json", {
            "inventory_run_id": inventory_run_id,
            "inventory_sha256": inventory["inventory_sha256"],
            "source_dir": inventory["source_dir"],
            "topic": topic.strip(),
            "max_papers": max_papers,
            "resume_from_run_id": resume_from_run_id,
            "selected_record_ids": [row["record_id"] for row in selected],
            "reused_record_ids": sorted(reused),
        })
        card_runs = list(reused.values())
        for row in selected:
            result = self._run_one(source_root, row, topic.strip())
            card_runs.append(result)
            _write_jsonl(run_dir / "runs" / "card_runs.jsonl", card_runs)
        completed_ids = {
            row["record_id"] for row in card_runs if row["status"] == "completed"
        }
        failed = [row for row in card_runs if row["status"] != "completed"]
        pending_count = len(candidates) - len(completed_ids)
        status = (
            "completed_with_failures" if failed
            else "completed_partial" if pending_count
            else "completed"
        )
        summary = {
            "candidate_count": len(candidates),
            "completed_count": len(completed_ids),
            "failed_count": len(failed),
            "pending_count": pending_count,
            "blocked_inventory_count": inventory["summary"]["canonical_blocked_count"],
        }
        output = {
            "schema_version": "paper_pool.card_preparation.v1",
            "run_id": resolved,
            "inventory_run_id": inventory_run_id,
            "topic": topic.strip(),
            "summary": summary,
            "card_runs": card_runs,
        }
        _write_json(run_dir / "output" / "paper_pool_card_preparation.json", output)
        _write_text(run_dir / "review" / "paper_pool_card_preparation.md", _render(output))
        manifest = {
            "schema_version": PAPER_POOL_CARD_RUN_SCHEMA_VERSION,
            "run_id": resolved,
            "status": status,
            "started_at": started_at,
            "finished_at": _now(),
            "inventory_run_id": inventory_run_id,
            "resume_from_run_id": resume_from_run_id,
            "summary": summary,
            "failure": None,
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": status,
            "summary": summary,
            "report_path": str(run_dir / "review" / "paper_pool_card_preparation.md"),
            "failure": None,
        }

    def _run_one(self, source_root: Path, row: dict[str, Any], topic: str) -> dict[str, Any]:
        source = (source_root / row["relative_path"]).resolve()
        try:
            source.relative_to(source_root.resolve())
        except ValueError as exc:
            raise PaperPoolCardPreparationError(
                "paper_pool_card.source_escape", "论文源路径越过源目录。"
            ) from exc
        workspace_paper_id = "pool--" + row["content_id"].removeprefix("paper_")
        try:
            result = self.card_runner.run(
                source,
                paper_id=row["paper_title"],
                workspace_id=workspace_paper_id,
                review_topic=topic,
            )
            run = _read_json(self.workspace / workspace_paper_id / "run.json")
            return {
                "record_id": row["record_id"],
                "content_id": row["content_id"],
                "relative_path": row["relative_path"],
                "paper_id": row["paper_title"],
                "workspace_paper_id": workspace_paper_id,
                "status": str(result.status),
                "structure_quality": str(result.structure_quality),
                "issue_count": int(result.issue_count),
                "generation_id": run.get("generation_id"),
                "material_count": int(
                    ((run.get("stages") or {}).get("material") or {}).get("material_count") or 0
                ),
                "failure": None,
            }
        except Exception as exc:
            return {
                "record_id": row["record_id"],
                "content_id": row["content_id"],
                "relative_path": row["relative_path"],
                "paper_id": row["paper_title"],
                "workspace_paper_id": workspace_paper_id,
                "status": "failed",
                "structure_quality": "red",
                "issue_count": 1,
                "generation_id": None,
                "material_count": 0,
                "failure": {
                    "error_code": getattr(exc, "code", type(exc).__name__),
                    "error_message": str(exc),
                },
            }


def _render(output):
    s = output["summary"]
    return "\n".join([
        "# 全论文池Card准备报告", "",
        f"- 候选：{s['candidate_count']}",
        f"- 已完成：{s['completed_count']}",
        f"- 失败：{s['failed_count']}",
        f"- 待处理：{s['pending_count']}",
        f"- 清点阶段阻断：{s['blocked_inventory_count']}", "",
        "失败不会阻断其他论文，后续续跑只复用代际未变化的成功Card。", "",
    ])


def _safe_segment(value):
    if not value or Path(value).name != value or value in {".", ".."}:
        raise PaperPoolCardPreparationError("paper_pool_card.run_id_invalid", "run_id不是安全路径段。")


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperPoolCardPreparationError(
            "paper_pool_card.json_read_failed", f"无法读取JSON：{path}"
        ) from exc
    if not isinstance(value, dict):
        raise PaperPoolCardPreparationError("paper_pool_card.json_object_required", "JSON必须是对象。")
    return value


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _now():
    return datetime.now(UTC).isoformat()
