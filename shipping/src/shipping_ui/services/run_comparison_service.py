from __future__ import annotations

from datetime import datetime
from typing import Any

from ..errors import UIError
from ..job_repository import JobRepository


RUN_COMPARISON_SCHEMA = "review_ui_run_comparison.v1"


class RunComparisonService:
    def __init__(self, jobs: JobRepository) -> None:
        self.jobs = jobs

    def compare(
        self,
        project_id: str,
        *,
        left_job_id: str,
        right_job_id: str,
    ) -> dict[str, Any]:
        if left_job_id == right_job_id:
            raise UIError(
                "ui.run_comparison_same_job",
                "必须选择两个不同的 Job。",
            )
        left_job = self.jobs.get(left_job_id)
        right_job = self.jobs.get(right_job_id)
        if (
            left_job["project_id"] != project_id
            or right_job["project_id"] != project_id
        ):
            raise UIError(
                "ui.run_comparison_project_mismatch",
                "只能比较当前项目中的 Job。",
            )
        if left_job["job_type"] != right_job["job_type"]:
            raise UIError(
                "ui.run_comparison_type_mismatch",
                "只能比较同一处理阶段的 Job。",
            )

        left_input = self.jobs.get_input(left_job_id)
        right_input = self.jobs.get_input(right_job_id)
        input_differences = self._input_differences(
            str(left_job["job_type"]),
            left_input,
            right_input,
        )
        configuration_differences = self._configuration_differences(
            str(left_job["job_type"]),
            left_input,
            right_input,
        )
        left_metrics = self._metrics(left_job)
        right_metrics = self._metrics(right_job)
        comparable = not input_differences
        return {
            "schema_version": RUN_COMPARISON_SCHEMA,
            "project_id": project_id,
            "job_type": left_job["job_type"],
            "comparable": comparable,
            "verdict": (
                "same_frozen_input" if comparable else "input_changed"
            ),
            "input_differences": input_differences,
            "configuration_differences": configuration_differences,
            "left": {
                "job_id": left_job_id,
                "input_schema": left_input["schema_version"],
                "metrics": left_metrics,
            },
            "right": {
                "job_id": right_job_id,
                "input_schema": right_input["schema_version"],
                "metrics": right_metrics,
            },
            "deltas": (
                self._metric_deltas(left_metrics, right_metrics)
                if comparable
                else None
            ),
        }

    def _input_differences(
        self,
        job_type: str,
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> list[dict[str, Any]]:
        differences: list[dict[str, Any]] = []
        self._compare_value(
            differences,
            "topic_changed",
            "综述主题不同。",
            "topic",
            left.get("topic"),
            right.get("topic"),
        )
        if job_type in {"card_build", "full_pipeline"}:
            self._compare_papers(
                differences,
                left.get("papers", []),
                right.get("papers", []),
                identity_fields=("source_sha256",),
            )
        elif job_type == "topic_brief":
            self._compare_papers(
                differences,
                left.get("papers", []),
                right.get("papers", []),
                identity_fields=(
                    "generation_id",
                    "input_sha256",
                    "materials_sha256",
                ),
            )
        elif job_type == "topic_synthesis":
            self._compare_papers(
                differences,
                left.get("source_runs", []),
                right.get("source_runs", []),
                identity_fields=(
                    "generation_id",
                    "evidence_sha256",
                    "materials_sha256",
                ),
            )
        else:
            raise UIError(
                "ui.run_comparison_type_unsupported",
                f"当前阶段不支持比较：{job_type}",
            )
        return differences

    def _compare_papers(
        self,
        differences: list[dict[str, Any]],
        left_rows: list[dict[str, Any]],
        right_rows: list[dict[str, Any]],
        *,
        identity_fields: tuple[str, ...],
    ) -> None:
        left = {str(row["paper_id"]): row for row in left_rows}
        right = {str(row["paper_id"]): row for row in right_rows}
        if set(left) != set(right):
            differences.append(
                {
                    "code": "paper_set_changed",
                    "message": "论文集合不同。",
                    "paper_id": None,
                    "field": "paper_ids",
                    "left": sorted(left),
                    "right": sorted(right),
                }
            )
        for paper_id in sorted(set(left) & set(right)):
            for field in identity_fields:
                self._compare_value(
                    differences,
                    f"paper_{field}_changed",
                    f"论文 {paper_id} 的 {field} 不同。",
                    field,
                    left[paper_id].get(field),
                    right[paper_id].get(field),
                    paper_id=paper_id,
                )

    @staticmethod
    def _compare_value(
        differences: list[dict[str, Any]],
        code: str,
        message: str,
        field: str,
        left: Any,
        right: Any,
        *,
        paper_id: str | None = None,
    ) -> None:
        if left == right:
            return
        differences.append(
            {
                "code": code,
                "message": message,
                "paper_id": paper_id,
                "field": field,
                "left": left,
                "right": right,
            }
        )

    def _configuration_differences(
        self,
        job_type: str,
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> list[dict[str, Any]]:
        fields = (
            ("pdf_provider",)
            if job_type == "card_build"
            else (
                "pdf_provider",
                "model_profile_id",
                "model",
                "thinking",
                "review_config_sha256",
                "synthesis_config_sha256",
            )
            if job_type == "full_pipeline"
            else (
                "model_profile_id",
                "model",
                "thinking",
                (
                    "review_config_sha256"
                    if job_type == "topic_brief"
                    else "synthesis_config_sha256"
                ),
            )
        )
        differences: list[dict[str, Any]] = []
        for field in fields:
            self._compare_value(
                differences,
                "configuration_changed",
                f"执行配置 {field} 不同。",
                field,
                left.get(field),
                right.get(field),
            )
        return differences

    @classmethod
    def _metrics(cls, job: dict[str, Any]) -> dict[str, Any]:
        paper_results = job.get("paper_results", [])
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        request_count = sum(
            int(row.get("request_count", 0)) for row in paper_results
        ) + int(result.get("request_count", 0))
        total_tokens = sum(
            int((row.get("usage") or {}).get("total_tokens", 0))
            for row in paper_results
        ) + int((result.get("usage") or {}).get("total_tokens", 0))
        output: dict[str, int] = {}
        if job["job_type"] == "card_build":
            output = {
                "material_count": sum(
                    int(row.get("material_count", 0))
                    for row in paper_results
                ),
                "issue_count": sum(
                    int(row.get("issue_count", 0))
                    for row in paper_results
                ),
            }
        elif job["job_type"] == "topic_brief":
            output = {
                "evidence_unit_count": sum(
                    int(row.get("evidence_unit_count", 0))
                    for row in paper_results
                ),
                "evidence_failure_count": sum(
                    int(row.get("evidence_failure_count", 0))
                    for row in paper_results
                ),
            }
        elif job["job_type"] == "topic_synthesis":
            output = {
                "theme_count": int(result.get("theme_count", 0)),
                "synthesis_unit_count": int(
                    result.get("synthesis_unit_count", 0)
                ),
                "section_count": int(result.get("section_count", 0)),
            }
        elif job["job_type"] == "full_pipeline":
            output = {
                "paper_count": int(result.get("paper_count", 0)),
                "included_paper_count": int(
                    result.get("included_paper_count", 0)
                ),
                "excluded_paper_count": int(
                    result.get("excluded_paper_count", 0)
                ),
                "completed_card_count": int(
                    result.get("completed_card_count", 0)
                ),
            }
        return {
            "status": job["status"],
            "duration_seconds": cls._duration_seconds(job),
            "request_count": request_count,
            "total_tokens": total_tokens,
            "failure_count": int(job["progress"]["failed"]),
            "output": output,
        }

    @staticmethod
    def _duration_seconds(job: dict[str, Any]) -> float | None:
        started_at = job.get("started_at")
        finished_at = job.get("finished_at")
        if not started_at or not finished_at:
            return None
        try:
            duration = datetime.fromisoformat(finished_at) - datetime.fromisoformat(
                started_at
            )
        except (TypeError, ValueError):
            return None
        return round(duration.total_seconds(), 3)

    @staticmethod
    def _metric_deltas(
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> dict[str, Any]:
        left_duration = left["duration_seconds"]
        right_duration = right["duration_seconds"]
        output_keys = set(left["output"]) | set(right["output"])
        return {
            "duration_seconds": (
                None
                if left_duration is None or right_duration is None
                else round(right_duration - left_duration, 3)
            ),
            "request_count": right["request_count"] - left["request_count"],
            "total_tokens": right["total_tokens"] - left["total_tokens"],
            "failure_count": right["failure_count"] - left["failure_count"],
            "output": {
                key: int(right["output"].get(key, 0))
                - int(left["output"].get(key, 0))
                for key in sorted(output_keys)
            },
        }
