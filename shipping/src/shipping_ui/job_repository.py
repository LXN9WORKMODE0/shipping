from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import UIError


JOB_SCHEMA = "review_ui_job.v1"
CARD_JOB_INPUT_SCHEMA = "review_ui_card_job_input.v1"
TOPIC_BRIEF_JOB_INPUT_SCHEMA = "review_ui_topic_brief_job_input.v1"
TOPIC_SYNTHESIS_JOB_INPUT_SCHEMA = "review_ui_topic_synthesis_job_input.v1"
FULL_PIPELINE_JOB_INPUT_SCHEMA = "review_ui_full_pipeline_job_input.v1"
JOB_INPUT_TYPES = {
    CARD_JOB_INPUT_SCHEMA: ("card_build", "card"),
    TOPIC_BRIEF_JOB_INPUT_SCHEMA: ("topic_brief", "brief"),
    TOPIC_SYNTHESIS_JOB_INPUT_SCHEMA: ("topic_synthesis", "synthesis"),
    FULL_PIPELINE_JOB_INPUT_SCHEMA: ("full_pipeline", "pipeline"),
}
TERMINAL_STATUSES = {
    "completed",
    "completed_with_failures",
    "failed",
    "cancelled",
    "interrupted",
}
ALLOWED_TRANSITIONS = {
    "queued": {"running", "cancelled", "interrupted", "failed"},
    "running": {
        "cancel_requested",
        "completed",
        "completed_with_failures",
        "cancelled",
        "interrupted",
        "failed",
    },
    "cancel_requested": {"cancelled", "interrupted", "failed"},
}
INCOMPLETE_STATUSES = {"queued", "running", "cancel_requested"}


class JobRepository:
    def __init__(
        self,
        project_root: str | Path,
        *,
        workspace: str | Path = "workspace",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = self._inside(self.project_root, workspace)
        self.root = self._inside(self.workspace, "_ui/jobs")
        self._lock = threading.RLock()

    def create(self, job_input: dict[str, Any]) -> dict[str, Any]:
        input_schema = str(job_input.get("schema_version") or "")
        job_definition = JOB_INPUT_TYPES.get(input_schema)
        if job_definition is None:
            raise UIError(
                "ui.job_input_schema_invalid",
                "Job 输入 schema 不受支持。",
            )
        job_type, job_prefix = job_definition
        now = self._now()
        job_id = (
            job_prefix
            + "-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            + "-"
            + uuid4().hex[:8]
        )
        job_dir = self.root / job_id
        with self._lock:
            active = [
                row
                for row in self.list()
                if row["status"] in INCOMPLETE_STATUSES
            ]
            if active:
                raise UIError(
                    "ui.job_active_exists",
                    f"已有 Job 正在排队或运行：{active[0]['job_id']}",
                )
            job_dir.mkdir(parents=True, exist_ok=False)
            (job_dir / "logs").mkdir()
            self._atomic_json(job_dir / "input.json", job_input)
            job = {
                "schema_version": JOB_SCHEMA,
                "job_id": job_id,
                "job_type": job_type,
                "project_id": job_input["project_id"],
                "project_revision": job_input["project_revision"],
                "status": "queued",
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "input_path": "input.json",
                "log_path": "logs/job.log",
                "progress": {
                    "total": (
                        len(job_input["papers"])
                        if job_type in {"card_build", "topic_brief"}
                        else 1
                    ),
                    "completed": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "current_paper_id": None,
                },
                "paper_results": [],
                "result": None,
                "failure_code": None,
                "failure_message": None,
                "cancel_requested_at": None,
                "cancelled_at": None,
                "interrupted_at": None,
            }
            self._atomic_json(job_dir / "job.json", job)
            (job_dir / "logs" / "job.log").write_text("", encoding="utf-8")
        return job

    def get(self, job_id: str) -> dict[str, Any]:
        self._validate_job_id(job_id)
        with self._lock:
            job = self._read_json(self.root / job_id / "job.json")
        self._validate_job(job)
        return job

    def get_input(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        with self._lock:
            payload = self._read_json(
                self.root / job_id / str(job["input_path"])
            )
        input_schema = str(payload.get("schema_version") or "")
        expected_type = JOB_INPUT_TYPES.get(input_schema, (None, None))[0]
        if expected_type is None or expected_type != job["job_type"]:
            raise UIError(
                "ui.job_input_schema_invalid",
                f"Job 输入 schema 不受支持：{job_id}",
            )
        return payload

    def list(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        records: list[dict[str, Any]] = []
        with self._lock:
            paths = sorted(self.root.glob("*/job.json"))
            for path in paths:
                job = self._read_json(path)
                self._validate_job(job)
                if project_id and job["project_id"] != project_id:
                    continue
                records.append(job)
        return sorted(
            records,
            key=lambda row: str(row["created_at"]),
            reverse=True,
        )

    def list_run_records(
        self,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for job in self.list(project_id):
            job_input = self.get_input(str(job["job_id"]))
            failure_code = job.get("failure_code")
            papers = job_input.get("papers", [])
            request_count = sum(
                int(row.get("request_count", 0))
                for row in job.get("paper_results", [])
            )
            total_tokens = sum(
                int((row.get("usage") or {}).get("total_tokens", 0))
                for row in job.get("paper_results", [])
            )
            if isinstance(job.get("result"), dict):
                request_count += int(job["result"].get("request_count", 0))
                total_tokens += int(
                    (job["result"].get("usage") or {}).get("total_tokens", 0)
                )
            records.append(
                {
                    "run_id": job["job_id"],
                    "run_type": job["job_type"],
                    "schema_version": job["schema_version"],
                    "status": job["status"],
                    "topic": job_input["topic"],
                    "paper_id": (
                        papers[0]["paper_id"]
                        if len(papers) == 1
                        else None
                    ),
                    "project_id": job["project_id"],
                    "started_at": job.get("started_at")
                    or job.get("created_at"),
                    "finished_at": job.get("finished_at"),
                    "request_count": request_count,
                    "total_tokens": total_tokens,
                    "failure_count": job["progress"]["failed"],
                    "failure_codes": (
                        [failure_code] if failure_code else []
                    ),
                }
            )
        return records

    def transition(
        self,
        job_id: str,
        status: str,
        *,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            current = str(job["status"])
            if status not in ALLOWED_TRANSITIONS.get(current, set()):
                raise UIError(
                    "ui.job_transition_invalid",
                    f"Job 状态不能从 {current} 变为 {status}：{job_id}",
                )
            now = self._now()
            job["status"] = status
            if status == "running":
                job["started_at"] = now
            if status == "cancel_requested":
                job["cancel_requested_at"] = now
            if status == "cancelled":
                job["cancelled_at"] = now
            if status == "interrupted":
                job["interrupted_at"] = now
            if status in TERMINAL_STATUSES:
                job["finished_at"] = now
                job["progress"]["current_paper_id"] = None
            job["failure_code"] = failure_code
            job["failure_message"] = failure_message
            self._atomic_json(self.root / job_id / "job.json", job)
            return job

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            status = str(job["status"])
            if status == "cancel_requested":
                return job
            if status == "queued":
                return self.transition(job_id, "cancelled")
            if status == "running":
                progress = job["progress"]
                if (
                    progress["total"] > 0
                    and progress["completed"] >= progress["total"]
                ):
                    raise UIError(
                        "ui.job_finalizing",
                        "Job 已完成全部计算，正在发布最终状态，不能取消。",
                    )
                return self.transition(job_id, "cancel_requested")
            raise UIError(
                "ui.job_not_cancellable",
                f"Job 当前状态不能取消：{status}。",
            )

    def retry_candidates(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["job_type"] not in {"card_build", "topic_brief"}:
            raise UIError(
                "ui.job_retry_not_paper_scoped",
                "只有 Card 和单篇分析 Job 支持逐论文重跑。",
            )
        if job["status"] not in TERMINAL_STATUSES:
            raise UIError(
                "ui.job_retry_not_ready",
                "Job 尚未结束，不能计算重跑论文。",
            )
        job_input = self.get_input(job_id)
        completed_ids = {
            str(row["paper_id"])
            for row in job["paper_results"]
            if row["status"] == "completed"
        }
        candidates = [
            str(row["paper_id"])
            for row in job_input["papers"]
            if str(row["paper_id"]) not in completed_ids
        ]
        return {
            "job_id": job_id,
            "job_type": job["job_type"],
            "project_id": job["project_id"],
            "paper_ids": candidates,
            "completed_paper_ids": sorted(completed_ids),
            "candidate_count": len(candidates),
        }

    def record_paper_result(
        self,
        job_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            if job["status"] not in {"running", "cancel_requested"}:
                raise UIError(
                    "ui.job_not_running",
                    f"Job 不在运行或取消收束状态：{job_id}",
                )
            existing = {
                str(row["paper_id"]) for row in job["paper_results"]
            }
            if result["paper_id"] in existing:
                raise UIError(
                    "ui.job_paper_result_duplicate",
                    f"Job 论文结果重复：{result['paper_id']}",
                )
            job["paper_results"].append(result)
            progress = job["progress"]
            progress["completed"] += 1
            progress["succeeded" if result["status"] == "completed" else "failed"] += 1
            progress["current_paper_id"] = None
            self._atomic_json(self.root / job_id / "job.json", job)
            return job

    def set_current_paper(
        self,
        job_id: str,
        paper_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            if job["status"] != "running":
                raise UIError(
                    "ui.job_not_running",
                    f"Job 不在运行状态：{job_id}",
                )
            job["progress"]["current_paper_id"] = paper_id
            self._atomic_json(self.root / job_id / "job.json", job)
            return job

    def record_result(
        self,
        job_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            if job["status"] != "running":
                raise UIError(
                    "ui.job_not_running",
                    f"Job 不在运行状态：{job_id}",
                )
            if job.get("result") is not None:
                raise UIError(
                    "ui.job_result_duplicate",
                    f"Job 结果重复：{job_id}",
                )
            job["result"] = result
            job["progress"]["completed"] = 1
            job["progress"][
                "succeeded" if result["status"] == "completed" else "failed"
            ] = 1
            self._atomic_json(self.root / job_id / "job.json", job)
            return job

    def write_json_artifact(
        self,
        job_id: str,
        relative_path: str | Path,
        payload: object,
    ) -> Path:
        self._validate_job_id(job_id)
        path = self._inside(self.root / job_id, relative_path)
        with self._lock:
            self._atomic_json(path, payload)
        return path

    def append_log(self, job_id: str, text: str) -> None:
        self._validate_job_id(job_id)
        with self._lock:
            path = self.root / job_id / "logs" / "job.log"
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(text.rstrip("\r\n") + "\n")

    def read_log(
        self,
        job_id: str,
        *,
        after_line: int = 0,
    ) -> dict[str, Any]:
        self._validate_job_id(job_id)
        if after_line < 0:
            raise UIError(
                "ui.log_cursor_invalid",
                "日志游标不能为负数。",
            )
        with self._lock:
            path = self.root / job_id / "logs" / "job.log"
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError as exc:
                raise UIError(
                    "ui.job_log_missing",
                    f"Job 日志不存在：{job_id}",
                ) from exc
        return {
            "job_id": job_id,
            "after_line": after_line,
            "next_line": len(lines),
            "lines": lines[after_line:],
        }

    def reconcile_incomplete(self) -> list[str]:
        reconciled: list[str] = []
        for job in self.list():
            if job["status"] not in INCOMPLETE_STATUSES:
                continue
            status = (
                "cancelled"
                if job["status"] == "cancel_requested"
                else "interrupted"
            )
            self.transition(
                str(job["job_id"]),
                status,
                failure_code=(
                    None if status == "cancelled" else "ui.job_interrupted"
                ),
                failure_message=(
                    None
                    if status == "cancelled"
                    else "服务上次停止时 Job 尚未结束；本次不自动恢复。"
                ),
            )
            reconciled.append(str(job["job_id"]))
        return reconciled

    def _validate_job(self, job: dict[str, Any]) -> None:
        if job.get("schema_version") != JOB_SCHEMA:
            raise UIError(
                "ui.job_schema_invalid",
                f"Job schema 不受支持：{job.get('schema_version')}",
            )
        self._validate_job_id(str(job.get("job_id", "")))
        if job.get("status") not in {
            "queued",
            "running",
            "completed",
            "completed_with_failures",
            "cancel_requested",
            "cancelled",
            "interrupted",
            "failed",
        }:
            raise UIError(
                "ui.job_status_invalid",
                f"Job 状态无效：{job.get('status')}",
            )
        job_type = str(job.get("job_type") or "")
        if job_type not in {value[0] for value in JOB_INPUT_TYPES.values()}:
            raise UIError(
                "ui.job_type_invalid",
                f"Job 类型无效：{job_type}",
            )

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if (
            not (
                job_id.startswith("card-")
                or job_id.startswith("brief-")
                or job_id.startswith("synthesis-")
                or job_id.startswith("pipeline-")
            )
            or "/" in job_id
            or "\\" in job_id
            or len(job_id) > 64
        ):
            raise UIError("ui.job_id_invalid", f"Job ID 无效：{job_id}")

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _atomic_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".tmp-{uuid4().hex}")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        try:
            for attempt in range(5):
                try:
                    temporary.replace(path)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01 * (attempt + 1))
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise UIError("ui.job_not_found", f"Job 不存在：{path.parent.name}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise UIError("ui.job_invalid", f"无法读取 Job：{path}") from exc
        if not isinstance(payload, dict):
            raise UIError("ui.job_invalid", f"Job JSON 必须是对象：{path}")
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
