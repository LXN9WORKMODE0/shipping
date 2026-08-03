from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .paper_pool_inventory import load_paper_pool_inventory
from .pipeline import LiteraturePipeline


PAPER_POOL_CARD_ROOT = "_paper_pool_card_preparations"
PAPER_POOL_CARD_RUN_SCHEMA_VERSION = "paper_pool.card_preparation_run.v2"
PAPER_POOL_CARD_OUTPUT_SCHEMA_VERSION = "paper_pool.card_preparation.v2"
PAPER_POOL_CARD_CONTROL_SCHEMA_VERSION = "paper_pool.card_preparation_control.v1"
MAX_CARD_WORKERS = 8
CONTROL_ACTIONS = {"run", "pause", "cancel"}


class PaperPoolCardPreparationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


class _StartRateLimiter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self._lock = threading.Lock()
        self._next_start = 0.0

    def wait_for_slot(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_start - now)
            if delay:
                time.sleep(delay)
            started = time.monotonic()
            self._next_start = started + self.interval_seconds


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
        max_workers: int = 1,
        min_start_interval_seconds: float = 0.0,
    ) -> dict[str, Any]:
        normalized_topic = topic.strip()
        if not normalized_topic:
            raise PaperPoolCardPreparationError("paper_pool_card.topic_empty", "综述主题不能为空。")
        if max_papers is not None and max_papers < 1:
            raise PaperPoolCardPreparationError("paper_pool_card.limit_invalid", "max_papers必须>=1。")
        if not 1 <= max_workers <= MAX_CARD_WORKERS:
            raise PaperPoolCardPreparationError(
                "paper_pool_card.workers_invalid",
                f"max_workers必须在1到{MAX_CARD_WORKERS}之间。",
            )
        if min_start_interval_seconds < 0:
            raise PaperPoolCardPreparationError(
                "paper_pool_card.start_interval_invalid",
                "min_start_interval_seconds不能为负数。",
            )

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
        candidate_by_id = {str(row["record_id"]): row for row in candidates}
        reused = self._load_reused_rows(
            inventory=inventory,
            inventory_run_id=inventory_run_id,
            topic=normalized_topic,
            resume_from_run_id=resume_from_run_id,
            candidate_by_id=candidate_by_id,
        )
        remaining = [row for row in candidates if row["record_id"] not in reused]
        selected = remaining[:max_papers] if max_papers is not None else remaining
        request = {
            "inventory_run_id": inventory_run_id,
            "inventory_sha256": inventory["inventory_sha256"],
            "source_dir": inventory["source_dir"],
            "topic": normalized_topic,
            "max_papers": max_papers,
            "resume_from_run_id": resume_from_run_id,
            "max_workers": max_workers,
            "min_start_interval_seconds": min_start_interval_seconds,
            "selected_record_ids": [row["record_id"] for row in selected],
            "reused_record_ids": sorted(reused),
        }
        _write_json(run_dir / "input" / "request.json", request)
        _write_json(
            run_dir / "control" / "request.json",
            {
                "schema_version": PAPER_POOL_CARD_CONTROL_SCHEMA_VERSION,
                "run_id": resolved,
                "action": "run",
                "requested_at": started_at,
            },
        )
        result_by_id = dict(reused)
        order = {str(row["record_id"]): index for index, row in enumerate(candidates)}
        self._write_checkpoint(run_dir, result_by_id, order)
        self._write_running_manifest(
            run_dir,
            run_id=resolved,
            started_at=started_at,
            inventory_run_id=inventory_run_id,
            resume_from_run_id=resume_from_run_id,
            candidate_count=len(candidates),
            selected_count=len(selected),
            reused_count=len(reused),
            result_by_id=result_by_id,
        )

        terminal_action: str | None = None
        limiter = _StartRateLimiter(min_start_interval_seconds)
        futures: dict[Future[dict[str, Any]], dict[str, Any]] = {}
        next_index = 0
        executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="paper-pool-card",
        )
        try:
            while next_index < len(selected) or futures:
                action = _read_control_action(run_dir / "control" / "request.json", resolved)
                if action in {"pause", "cancel"}:
                    terminal_action = action
                while (
                    terminal_action is None
                    and next_index < len(selected)
                    and len(futures) < max_workers
                ):
                    row = selected[next_index]
                    next_index += 1
                    future = executor.submit(
                        self._run_one_limited,
                        limiter,
                        source_root,
                        row,
                        normalized_topic,
                    )
                    futures[future] = row
                if not futures:
                    break
                done, _ = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    row = futures.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = self._failure_result(row, exc)
                    result_by_id[str(row["record_id"])] = result
                    self._write_checkpoint(run_dir, result_by_id, order)
                    self._write_running_manifest(
                        run_dir,
                        run_id=resolved,
                        started_at=started_at,
                        inventory_run_id=inventory_run_id,
                        resume_from_run_id=resume_from_run_id,
                        candidate_count=len(candidates),
                        selected_count=len(selected),
                        reused_count=len(reused),
                        result_by_id=result_by_id,
                    )
        except KeyboardInterrupt:
            terminal_action = "interrupted"
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=False)

        card_runs = _ordered_rows(result_by_id, order)
        completed_ids = {
            str(row["record_id"]) for row in card_runs if row["status"] == "completed"
        }
        failed = [row for row in card_runs if row["status"] != "completed"]
        pending_count = len(candidates) - len(completed_ids)
        attempted_count = len(card_runs) - len(reused)
        if terminal_action == "pause":
            status = "paused"
        elif terminal_action == "cancel":
            status = "cancelled"
        elif failed:
            status = "completed_with_failures"
        elif pending_count:
            status = "completed_partial"
        else:
            status = "completed"
        summary = {
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "attempted_count": attempted_count,
            "reused_count": len(reused),
            "completed_count": len(completed_ids),
            "failed_count": len(failed),
            "pending_count": pending_count,
            "unstarted_selected_count": len(selected) - attempted_count,
            "blocked_inventory_count": inventory["summary"]["canonical_blocked_count"],
        }
        output = {
            "schema_version": PAPER_POOL_CARD_OUTPUT_SCHEMA_VERSION,
            "run_id": resolved,
            "inventory_run_id": inventory_run_id,
            "topic": normalized_topic,
            "status": status,
            "control_action": terminal_action,
            "execution": {
                "max_workers": max_workers,
                "min_start_interval_seconds": min_start_interval_seconds,
            },
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
            "execution": output["execution"],
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

    def _load_reused_rows(
        self,
        *,
        inventory: dict[str, Any],
        inventory_run_id: str,
        topic: str,
        resume_from_run_id: str | None,
        candidate_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if resume_from_run_id is None:
            return {}
        _safe_segment(resume_from_run_id)
        old_dir = self.workspace / PAPER_POOL_CARD_ROOT / "runs" / resume_from_run_id
        old_request = _read_json(old_dir / "input" / "request.json")
        if (
            old_request.get("inventory_run_id") != inventory_run_id
            or old_request.get("inventory_sha256") != inventory["inventory_sha256"]
            or old_request.get("topic") != topic
        ):
            raise PaperPoolCardPreparationError(
                "paper_pool_card.resume_mismatch", "续跑来源与论文池或主题不一致。"
            )
        checkpoint_path = old_dir / "runs" / "card_runs.jsonl"
        if checkpoint_path.exists():
            old_rows = _read_jsonl(checkpoint_path)
        else:
            old_output = _read_json(old_dir / "output" / "paper_pool_card_preparation.json")
            old_rows = old_output.get("card_runs")
            if not isinstance(old_rows, list):
                raise PaperPoolCardPreparationError(
                    "paper_pool_card.resume_rows_invalid", "续跑来源缺少Card结果列表。"
                )
        reused: dict[str, dict[str, Any]] = {}
        for row in old_rows:
            if not isinstance(row, dict):
                raise PaperPoolCardPreparationError(
                    "paper_pool_card.resume_rows_invalid", "续跑Card结果必须是对象。"
                )
            if row.get("status") != "completed":
                continue
            record_id = str(row.get("record_id") or "")
            current_inventory_row = candidate_by_id.get(record_id)
            if current_inventory_row is None:
                raise PaperPoolCardPreparationError(
                    "paper_pool_card.resume_record_missing",
                    f"续跑成功记录已不在当前待制卡集合：{record_id}",
                )
            expected_workspace_id = "pool--" + str(
                current_inventory_row["content_id"]
            ).removeprefix("paper_")
            if (
                row.get("content_id") != current_inventory_row["content_id"]
                or row.get("workspace_paper_id") != expected_workspace_id
            ):
                raise PaperPoolCardPreparationError(
                    "paper_pool_card.resume_identity_mismatch",
                    f"续跑记录身份与当前清单不一致：{record_id}",
                )
            if record_id in reused:
                raise PaperPoolCardPreparationError(
                    "paper_pool_card.resume_record_duplicate",
                    f"续跑来源包含重复成功记录：{record_id}",
                )
            current = _read_json(
                self.workspace / expected_workspace_id / "materials" / "current.json"
            )
            if (
                current.get("status") != "completed"
                or current.get("generation_id") != row.get("generation_id")
            ):
                raise PaperPoolCardPreparationError(
                    "paper_pool_card.reused_generation_changed",
                    f"已完成Card代际变化：{expected_workspace_id}",
                )
            reused[record_id] = {
                **row,
                "reused_from_run_id": resume_from_run_id,
            }
        return reused

    def _run_one_limited(
        self,
        limiter: _StartRateLimiter,
        source_root: Path,
        row: dict[str, Any],
        topic: str,
    ) -> dict[str, Any]:
        limiter.wait_for_slot()
        return self._run_one(source_root, row, topic)

    def _run_one(self, source_root: Path, row: dict[str, Any], topic: str) -> dict[str, Any]:
        started_at = _now()
        source = (source_root / row["relative_path"]).resolve()
        try:
            source.relative_to(source_root.resolve())
        except ValueError as exc:
            raise PaperPoolCardPreparationError(
                "paper_pool_card.source_escape", "论文源路径越过源目录。"
            ) from exc
        workspace_paper_id = "pool--" + row["content_id"].removeprefix("paper_")
        try:
            if source.stat().st_size != int(row["size_bytes"]) or _sha256_file(source) != row["sha256"]:
                raise PaperPoolCardPreparationError(
                    "paper_pool_card.source_identity_changed",
                    f"来源文件与清点快照不一致：{row['relative_path']}",
                )
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
                "started_at": started_at,
                "finished_at": _now(),
                "failure": None,
            }
        except Exception as exc:
            return self._failure_result(row, exc, started_at=started_at)

    def _failure_result(
        self,
        row: dict[str, Any],
        exc: Exception,
        *,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        workspace_paper_id = "pool--" + row["content_id"].removeprefix("paper_")
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
            "started_at": started_at or _now(),
            "finished_at": _now(),
            "failure": {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
            },
        }

    @staticmethod
    def _write_checkpoint(
        run_dir: Path,
        result_by_id: dict[str, dict[str, Any]],
        order: dict[str, int],
    ) -> None:
        _write_jsonl(
            run_dir / "runs" / "card_runs.jsonl",
            _ordered_rows(result_by_id, order),
        )

    @staticmethod
    def _write_running_manifest(
        run_dir: Path,
        *,
        run_id: str,
        started_at: str,
        inventory_run_id: str,
        resume_from_run_id: str | None,
        candidate_count: int,
        selected_count: int,
        reused_count: int,
        result_by_id: dict[str, dict[str, Any]],
    ) -> None:
        completed_count = sum(row["status"] == "completed" for row in result_by_id.values())
        failed_count = len(result_by_id) - completed_count
        _write_json(
            run_dir / "manifest.json",
            {
                "schema_version": PAPER_POOL_CARD_RUN_SCHEMA_VERSION,
                "run_id": run_id,
                "status": "running",
                "started_at": started_at,
                "finished_at": None,
                "inventory_run_id": inventory_run_id,
                "resume_from_run_id": resume_from_run_id,
                "progress": {
                    "candidate_count": candidate_count,
                    "selected_count": selected_count,
                    "reused_count": reused_count,
                    "attempted_count": len(result_by_id) - reused_count,
                    "completed_count": completed_count,
                    "failed_count": failed_count,
                },
                "failure": None,
            },
        )


def request_paper_pool_card_action(
    workspace: str | Path,
    *,
    run_id: str,
    action: str,
) -> dict[str, Any]:
    _safe_segment(run_id)
    if action not in {"pause", "cancel"}:
        raise PaperPoolCardPreparationError(
            "paper_pool_card.control_action_invalid", "控制动作只能是pause或cancel。"
        )
    run_dir = Path(workspace) / PAPER_POOL_CARD_ROOT / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json")
    if manifest.get("status") != "running":
        raise PaperPoolCardPreparationError(
            "paper_pool_card.control_not_running",
            f"只能控制运行中的批次，当前状态：{manifest.get('status')}",
        )
    payload = {
        "schema_version": PAPER_POOL_CARD_CONTROL_SCHEMA_VERSION,
        "run_id": run_id,
        "action": action,
        "requested_at": _now(),
    }
    _write_json(run_dir / "control" / "request.json", payload)
    return payload


def _read_control_action(path: Path, run_id: str) -> str:
    payload = _read_json(path)
    if (
        payload.get("schema_version") != PAPER_POOL_CARD_CONTROL_SCHEMA_VERSION
        or payload.get("run_id") != run_id
        or payload.get("action") not in CONTROL_ACTIONS
    ):
        raise PaperPoolCardPreparationError(
            "paper_pool_card.control_invalid", "批次控制文件无效。"
        )
    return str(payload["action"])


def _ordered_rows(
    result_by_id: dict[str, dict[str, Any]], order: dict[str, int]
) -> list[dict[str, Any]]:
    return sorted(
        result_by_id.values(),
        key=lambda row: order.get(str(row["record_id"]), len(order)),
    )


def _render(output: dict[str, Any]) -> str:
    summary = output["summary"]
    execution = output["execution"]
    return "\n".join([
        "# 全论文池Card准备报告", "",
        f"- 运行状态：`{output['status']}`",
        f"- 候选：{summary['candidate_count']}",
        f"- 本批选择：{summary['selected_count']}",
        f"- 本批实际执行：{summary['attempted_count']}",
        f"- 复用检查点：{summary['reused_count']}",
        f"- 已完成：{summary['completed_count']}",
        f"- 失败：{summary['failed_count']}",
        f"- 待处理：{summary['pending_count']}",
        f"- 本批未启动：{summary['unstarted_selected_count']}",
        f"- 清点阶段阻断：{summary['blocked_inventory_count']}", "",
        "## 执行控制", "",
        f"- 最大并发：{execution['max_workers']}",
        f"- 相邻任务最小启动间隔：{execution['min_start_interval_seconds']}秒",
        f"- 收束动作：`{output['control_action'] or 'none'}`", "",
        "失败、暂停和取消都不会撤销已完成Card。续跑只复用检查点中代际未变化的成功结果。", "",
    ])


def _safe_segment(value: str) -> None:
    if not value or Path(value).name != value or value in {".", ".."}:
        raise PaperPoolCardPreparationError("paper_pool_card.run_id_invalid", "run_id不是安全路径段。")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PaperPoolCardPreparationError(
            "paper_pool_card.source_unreadable", f"无法读取来源文件：{path}"
        ) from exc
    return "sha256:" + digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperPoolCardPreparationError(
            "paper_pool_card.json_read_failed", f"无法读取JSON：{path}"
        ) from exc
    if not isinstance(value, dict):
        raise PaperPoolCardPreparationError("paper_pool_card.json_object_required", "JSON必须是对象。")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperPoolCardPreparationError(
            "paper_pool_card.jsonl_read_failed", f"无法读取JSONL：{path}"
        ) from exc
    if any(not isinstance(row, dict) for row in rows):
        raise PaperPoolCardPreparationError(
            "paper_pool_card.jsonl_object_required", "JSONL每行必须是对象。"
        )
    return rows


def _write_json(path: Path, value: Any) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _atomic_write_text(
        path,
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
    )


def _write_text(path: Path, value: str) -> None:
    _atomic_write_text(path, value)


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _now() -> str:
    return datetime.now(UTC).isoformat()
