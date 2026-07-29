from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..job_repository import INCOMPLETE_STATUSES, TERMINAL_STATUSES, JobRepository


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUCCESS_STATUSES = {"completed", "completed_with_failures"}


class GovernanceService:
    def __init__(
        self,
        project_root: str | Path,
        jobs: JobRepository,
        *,
        workspace: str | Path = "workspace",
        env_file: str | Path = ".env",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = self._inside(self.project_root, workspace)
        self.env_path = self._inside(self.project_root, env_file)
        self.jobs = jobs

    def summary(self) -> dict[str, Any]:
        jobs = self.jobs.list()
        stage_rows: dict[str, dict[str, Any]] = {}
        model_rows: dict[str, dict[str, Any]] = {}
        failure_codes: Counter[str] = Counter()
        totals = self._empty_metrics()
        totals.update(
            {
                "job_count": len(jobs),
                "active_job_count": 0,
                "terminal_job_count": 0,
                "status_counts": dict(Counter(str(row["status"]) for row in jobs)),
            }
        )

        for job in jobs:
            job_input = self.jobs.get_input(str(job["job_id"]))
            metrics = self._job_metrics(job)
            self._accumulate(totals, metrics)
            status = str(job["status"])
            if status in INCOMPLETE_STATUSES:
                totals["active_job_count"] += 1
            if status in TERMINAL_STATUSES:
                totals["terminal_job_count"] += 1

            stage = stage_rows.setdefault(
                str(job["job_type"]),
                {
                    "job_type": str(job["job_type"]),
                    "job_count": 0,
                    "successful_job_count": 0,
                    "cancelled_job_count": 0,
                    "interrupted_job_count": 0,
                    "failed_job_count": 0,
                    **self._empty_metrics(),
                },
            )
            stage["job_count"] += 1
            if status in _SUCCESS_STATUSES:
                stage["successful_job_count"] += 1
            elif status == "cancelled":
                stage["cancelled_job_count"] += 1
            elif status == "interrupted":
                stage["interrupted_job_count"] += 1
            elif status == "failed":
                stage["failed_job_count"] += 1
            self._accumulate(stage, metrics)

            model = str(job_input.get("model") or "").strip()
            if model:
                profile_id = str(job_input.get("model_profile_id") or "").strip()
                model_key = f"{profile_id}\0{model}"
                model_row = model_rows.setdefault(
                    model_key,
                    {
                        "model_profile_id": profile_id,
                        "model": model,
                        "job_count": 0,
                        **self._empty_metrics(),
                    },
                )
                model_row["job_count"] += 1
                self._accumulate(model_row, metrics)

            for code in self._job_failure_codes(job):
                failure_codes[code] += 1

        self._finish_metrics(totals)
        for row in [*stage_rows.values(), *model_rows.values()]:
            self._finish_metrics(row)

        return {
            "schema_version": "review_ui_governance.v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "totals": totals,
            "by_stage": sorted(
                stage_rows.values(),
                key=lambda row: str(row["job_type"]),
            ),
            "by_model": sorted(
                model_rows.values(),
                key=lambda row: (
                    str(row["model_profile_id"]),
                    str(row["model"]),
                ),
            ),
            "failure_codes": [
                {"code": code, "count": count}
                for code, count in failure_codes.most_common()
            ],
            "configuration": self.configuration_health(),
        }

    def configuration_health(self) -> dict[str, Any]:
        checks: list[dict[str, str]] = []
        self._file_check(
            checks,
            "pipeline_entry",
            "Pipeline 入口",
            self.project_root / "main.py",
            required=True,
        )
        self._file_check(
            checks,
            "python_executable",
            "Python 解释器",
            Path(sys.executable),
            required=True,
        )

        if self.workspace.is_dir() and os.access(self.workspace, os.W_OK):
            checks.append(
                self._check("workspace", "工作区", "ok", "目录存在且可写。")
            )
        else:
            checks.append(
                self._check(
                    "workspace",
                    "工作区",
                    "error",
                    "目录不存在或不可写。",
                )
            )

        env_values: dict[str, str] = {}
        if not self.env_path.is_file():
            checks.append(
                self._check("env_file", "环境文件", "error", ".env 不存在。")
            )
        else:
            try:
                env_values = self._read_env_values(self.env_path)
            except (OSError, UnicodeError, ValueError) as exc:
                checks.append(
                    self._check(
                        "env_file",
                        "环境文件",
                        "error",
                        f".env 无法解析：{exc}",
                    )
                )
            else:
                checks.append(
                    self._check("env_file", "环境文件", "ok", ".env 语法有效。")
                )

        merged_env = dict(env_values)
        for key, value in os.environ.items():
            if str(value).strip():
                merged_env[key] = value
        if not str(merged_env.get("MINERU_API_URL", "")).strip():
            merged_env["MINERU_API_URL"] = "https://mineru.net"
        self._credential_check(
            checks,
            "llm",
            "LLM 服务",
            merged_env,
            ("LLM_ANALYSIS_API_URL", "LLM_ANALYSIS_API_KEY"),
        )
        self._credential_check(
            checks,
            "mineru",
            "MinerU 服务",
            merged_env,
            ("MINERU_API_URL", "MINERU_API_KEY"),
        )
        self._json_file_check(
            checks,
            "model_profile",
            "默认模型配置",
            self.project_root
            / "config"
            / "models"
            / "deepseek-v4-pro-official.json",
            required_keys=("profile_id", "request_model", "context_window_tokens"),
        )
        self._json_file_check(
            checks,
            "topic_review_config",
            "单篇分析配置",
            self.project_root / "config" / "topic-review-default.json",
            required_keys=("schema_version",),
        )
        self._json_file_check(
            checks,
            "topic_synthesis_config",
            "跨论文综合配置",
            self.project_root / "config" / "topic-synthesis-default.json",
            required_keys=("schema_version",),
        )
        statuses = Counter(row["status"] for row in checks)
        overall = (
            "error"
            if statuses["error"]
            else "warning"
            if statuses["warning"]
            else "ok"
        )
        return {
            "overall_status": overall,
            "ok_count": statuses["ok"],
            "warning_count": statuses["warning"],
            "error_count": statuses["error"],
            "checks": checks,
        }

    @staticmethod
    def _job_metrics(job: dict[str, Any]) -> dict[str, Any]:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        request_count = 0
        for result in job.get("paper_results", []):
            request_count += int(result.get("request_count", 0))
            GovernanceService._add_usage(usage, result.get("usage"))
        result = job.get("result")
        if isinstance(result, dict):
            request_count += int(result.get("request_count", 0))
            GovernanceService._add_usage(usage, result.get("usage"))
        progress = job["progress"]
        return {
            "request_count": request_count,
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"],
            "duration_seconds": GovernanceService._duration_seconds(
                job.get("started_at"),
                job.get("finished_at"),
            ),
            "successful_paper_count": int(progress["succeeded"]),
            "failed_paper_count": int(progress["failed"]),
        }

    @staticmethod
    def _empty_metrics() -> dict[str, Any]:
        return {
            "request_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "duration_seconds": 0.0,
            "successful_paper_count": 0,
            "failed_paper_count": 0,
        }

    @staticmethod
    def _accumulate(target: dict[str, Any], metrics: dict[str, Any]) -> None:
        for key in GovernanceService._empty_metrics():
            target[key] += metrics[key]

    @staticmethod
    def _finish_metrics(target: dict[str, Any]) -> None:
        processed = (
            int(target["successful_paper_count"])
            + int(target["failed_paper_count"])
        )
        target["paper_failure_rate"] = (
            int(target["failed_paper_count"]) / processed if processed else 0.0
        )
        target["duration_seconds"] = round(float(target["duration_seconds"]), 3)

    @staticmethod
    def _add_usage(target: dict[str, int], usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for key in target:
            value = usage.get(key, 0)
            if type(value) is int and value >= 0:
                target[key] += value

    @staticmethod
    def _duration_seconds(started_at: Any, finished_at: Any) -> float:
        if not started_at or not finished_at:
            return 0.0
        try:
            started = datetime.fromisoformat(str(started_at))
            finished = datetime.fromisoformat(str(finished_at))
        except ValueError:
            return 0.0
        return max(0.0, (finished - started).total_seconds())

    @staticmethod
    def _job_failure_codes(job: dict[str, Any]) -> list[str]:
        codes: list[str] = []
        if job.get("failure_code"):
            codes.append(str(job["failure_code"]))
        for result in job.get("paper_results", []):
            if result.get("failure_code"):
                codes.append(str(result["failure_code"]))
        return codes

    @staticmethod
    def _read_env_values(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(),
            start=1,
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                raise ValueError(f"第 {line_number} 行缺少等号")
            key, value = line.split("=", 1)
            key = key.strip()
            if not _ENV_KEY_RE.fullmatch(key):
                raise ValueError(f"第 {line_number} 行 key 无效")
            value = value.strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {'"', "'"}
            ):
                value = value[1:-1]
            values[key] = value
        return values

    @staticmethod
    def _credential_check(
        checks: list[dict[str, str]],
        check_id: str,
        label: str,
        values: dict[str, str],
        keys: tuple[str, ...],
    ) -> None:
        missing = [key for key in keys if not str(values.get(key, "")).strip()]
        checks.append(
            GovernanceService._check(
                check_id,
                label,
                "warning" if missing else "ok",
                (
                    "缺少必要配置：" + "、".join(missing)
                    if missing
                    else "必要地址与凭据均已配置。"
                ),
            )
        )

    @staticmethod
    def _file_check(
        checks: list[dict[str, str]],
        check_id: str,
        label: str,
        path: Path,
        *,
        required: bool,
    ) -> None:
        exists = path.is_file()
        checks.append(
            GovernanceService._check(
                check_id,
                label,
                "ok" if exists else "error" if required else "warning",
                "文件存在。" if exists else "文件不存在。",
            )
        )

    @staticmethod
    def _json_file_check(
        checks: list[dict[str, str]],
        check_id: str,
        label: str,
        path: Path,
        *,
        required_keys: tuple[str, ...],
    ) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("根节点不是对象")
            missing = [key for key in required_keys if key not in payload]
            if missing:
                raise ValueError("缺少字段：" + "、".join(missing))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            checks.append(
                GovernanceService._check(
                    check_id,
                    label,
                    "error",
                    f"配置无效：{exc}",
                )
            )
        else:
            checks.append(
                GovernanceService._check(check_id, label, "ok", "配置有效。")
            )

    @staticmethod
    def _check(
        check_id: str,
        label: str,
        status: str,
        detail: str,
    ) -> dict[str, str]:
        return {
            "check_id": check_id,
            "label": label,
            "status": status,
            "detail": detail,
        }

    @staticmethod
    def _inside(base: Path, candidate: str | Path) -> Path:
        base = base.resolve()
        path = Path(candidate)
        resolved = path.resolve() if path.is_absolute() else (base / path).resolve()
        resolved.relative_to(base)
        return resolved
