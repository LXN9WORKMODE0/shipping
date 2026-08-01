from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm_analysis import (
    DEFAULT_MODEL_PROFILE,
    DEFAULT_TOKENIZER_CACHE,
    AnalysisInputError,
    create_input_snapshot,
)
from .llm_json_stage import execute_json_stage
from .llm_model_profile import load_model_profile
from .llm_projection import project_cards
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .paper_pool_inventory import load_paper_pool_inventory
from .topic_review import (
    DEFAULT_TOPIC_REVIEW_CONFIG,
    SCOPE_SYSTEM_PROMPT,
    build_scope_projection,
    build_topic_scope_prompts,
)
from .topic_review_contracts import load_topic_review_config, validate_topic_scope


PAPER_TOPIC_SCREENING_ROOT = "_paper_topic_screenings"
PAPER_POOL_SCREENING_ROOT = "_paper_pool_screenings"
PAPER_TOPIC_SCREENING_RUN_SCHEMA_VERSION = "paper_topic.screening_run.v1"
PAPER_POOL_SCREENING_RUN_SCHEMA_VERSION = "paper_pool.screening_run.v1"


class PaperTopicScreeningError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


class PaperTopicScreeningRunner:
    def __init__(self, workspace: str | Path, *, analysis_client=None, token_counter=None) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        paper_id: str,
        workspace_paper_id: str,
        topic: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 1800,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        review_config_path: str | Path = DEFAULT_TOPIC_REVIEW_CONFIG,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / PAPER_TOPIC_SCREENING_ROOT / "runs" / resolved
        if run_dir.exists():
            raise PaperTopicScreeningError("paper_screen.run_exists", f"run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot = profile = config = stage = screening = None
        try:
            snapshot = create_input_snapshot(
                self.workspace,
                paper_id=paper_id,
                topic=topic,
                workspace_paper_id=workspace_paper_id,
            )
            profile = load_model_profile(Path(model_profile_path))
            config = load_topic_review_config(Path(review_config_path))
            if provider != profile.provider:
                raise PaperTopicScreeningError("paper_screen.provider_mismatch", "provider不匹配。")
            projected = project_cards(snapshot.materials)
            aliased, alias_map = build_scope_projection(projected)
            system_prompt, user_prompt = build_topic_scope_prompts(snapshot, aliased, config)
            _write_json(run_dir / "input" / "paper.json", {
                "paper_id": snapshot.paper_id,
                "paper_title": snapshot.paper_title,
                "workspace_paper_id": workspace_paper_id,
                "topic": snapshot.topic,
                "generation_id": snapshot.generation_id,
                "input_sha256": snapshot.input_sha256,
                "material_count": len(snapshot.materials),
            })
            _write_jsonl(run_dir / "input" / "materials.jsonl", list(snapshot.materials))
            _write_jsonl(run_dir / "input" / "projected_cards.jsonl", projected)
            _write_json(run_dir / "input" / "scope_aliases.json", alias_map)
            _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
            _write_json(run_dir / "input" / "review_config.json", config.to_dict())
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url, api_key_env=api_key_env,
                model=profile.request_model, timeout=timeout,
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(
                profile, DEFAULT_TOKENIZER_CACHE
            )
            parsed, stage = execute_json_stage(
                run_dir=run_dir,
                stage="topic_scope",
                task_name="paper_topic_screening",
                directory_name="scope",
                system_prompt=SCOPE_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_output_tokens=config.scope_max_output_tokens,
                context={
                    "paper_id": snapshot.paper_id,
                    "workspace_paper_id": workspace_paper_id,
                    "topic": snapshot.topic,
                    "generation_id": snapshot.generation_id,
                    "scope_alias_to_material_id": alias_map,
                },
                client=client,
                profile=profile,
                token_counter=counter,
            )
            validated = validate_topic_scope(
                parsed, {str(row["material_id"]) for row in aliased}, config
            )
            scope = {
                **validated,
                "selected_materials": [
                    {**row, "material_id": alias_map[str(row["material_id"])]}
                    for row in validated["selected_materials"]
                ],
            }
            _validate_chinese_scope(scope)
            screening = {
                "schema_version": "paper_topic.screening.v1",
                "screening_id": _stable_id("screening", {
                    "paper_id": snapshot.paper_id,
                    "generation_id": snapshot.generation_id,
                    "topic_sha256": snapshot.topic_sha256,
                    "scope": scope,
                }),
                "paper_id": snapshot.paper_id,
                "paper_title": snapshot.paper_title,
                "workspace_paper_id": workspace_paper_id,
                "generation_id": snapshot.generation_id,
                "topic": snapshot.topic,
                "paper_relevance": scope["paper_relevance"],
                "relevance_reason": scope["relevance_reason"],
                "topic_summary": scope["topic_summary"],
                "selected_materials": scope["selected_materials"],
            }
            _write_json(run_dir / "scope" / "validated_scope.json", scope)
            output_path = run_dir / "output" / "paper_topic_screening.json"
            _write_json(output_path, screening)
            _write_text(run_dir / "review" / "paper_topic_screening.md", _render_screening(screening))
            manifest = _screen_manifest(
                resolved, "completed", started_at, snapshot, workspace_paper_id,
                screening, stage, _sha256_file(output_path), None
            )
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _screen_manifest(
                resolved, "failed", started_at, snapshot, workspace_paper_id,
                screening, stage, None, failure
            )
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": manifest["status"],
            "paper_id": paper_id,
            "workspace_paper_id": workspace_paper_id,
            "paper_relevance": screening["paper_relevance"] if screening else None,
            "output_path": (
                str(run_dir / "output" / "paper_topic_screening.json")
                if screening else None
            ),
            "failure": manifest["failure"],
        }


def load_paper_topic_screening(
    workspace: str | Path, run_id: str
) -> dict[str, Any]:
    _safe_segment(run_id)
    workspace_path = Path(workspace)
    run_dir = workspace_path / PAPER_TOPIC_SCREENING_ROOT / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json")
    output_path = run_dir / "output" / "paper_topic_screening.json"
    output = _read_json(output_path)
    paper = _read_json(run_dir / "input" / "paper.json")
    if (
        manifest.get("schema_version") != PAPER_TOPIC_SCREENING_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
        or manifest.get("output_sha256") != _sha256_file(output_path)
        or output.get("schema_version") != "paper_topic.screening.v1"
        or output.get("paper_id") != manifest.get("paper_id")
        or output.get("workspace_paper_id") != manifest.get("workspace_paper_id")
        or output.get("generation_id") != manifest.get("generation_id")
        or output.get("topic") != paper.get("topic")
    ):
        raise PaperTopicScreeningError(
            "paper_screen.replay_mismatch", "单篇主题筛选运行无法重放。"
        )
    _validate_chinese_scope(output)
    snapshot = create_input_snapshot(
        workspace_path,
        paper_id=str(output["paper_id"]),
        topic=str(output["topic"]),
        workspace_paper_id=str(output["workspace_paper_id"]),
    )
    if (
        snapshot.generation_id != output["generation_id"]
        or snapshot.input_sha256 != paper.get("input_sha256")
    ):
        raise PaperTopicScreeningError(
            "paper_screen.source_changed", "单篇筛选来源Card代际已变化。"
        )
    return output


class PaperPoolScreeningRunner:
    def __init__(self, workspace: str | Path, *, paper_runner=None) -> None:
        self.workspace = Path(workspace)
        self.paper_runner = paper_runner or PaperTopicScreeningRunner(self.workspace)

    def run(
        self,
        *,
        inventory_run_id: str,
        topic: str,
        run_id: str | None = None,
        max_papers: int | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 1800,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        review_config_path: str | Path = DEFAULT_TOPIC_REVIEW_CONFIG,
        resume_from_run_id: str | None = None,
    ) -> dict[str, Any]:
        if not topic.strip():
            raise PaperTopicScreeningError("paper_pool_screen.topic_empty", "综述主题不能为空。")
        if max_papers is not None and max_papers < 1:
            raise PaperTopicScreeningError("paper_pool_screen.limit_invalid", "max_papers必须>=1。")
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / PAPER_POOL_SCREENING_ROOT / "runs" / resolved
        if run_dir.exists():
            raise PaperTopicScreeningError("paper_pool_screen.run_exists", f"run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        inventory = load_paper_pool_inventory(self.workspace, inventory_run_id)
        ready = [
            row for row in inventory["records"]
            if row["processing_status"] == "card_ready" and row["is_canonical_content"]
        ]
        resumed_children: dict[str, dict[str, Any]] = {}
        resume_rejections: list[dict[str, str]] = []
        if resume_from_run_id is not None:
            _safe_segment(resume_from_run_id)
            resume_dir = self.workspace / PAPER_POOL_SCREENING_ROOT / "runs" / resume_from_run_id
            old_request = _read_json(resume_dir / "input" / "request.json")
            old_output = _read_json(resume_dir / "output" / "paper_pool_screening.json")
            old_inventory = load_paper_pool_inventory(
                self.workspace, str(old_request.get("inventory_run_id") or "")
            )
            if (
                old_request.get("topic") != topic.strip()
                or old_output.get("topic") != topic.strip()
                or old_inventory.get("source_dir") != inventory.get("source_dir")
            ):
                raise PaperTopicScreeningError(
                    "paper_pool_screen.resume_mismatch",
                    "续跑来源与当前论文池或主题不一致。",
                )
            old_records = {
                str(row["record_id"]): row for row in old_inventory["records"]
            }
            current_records = {
                str(row["record_id"]): row for row in inventory["records"]
            }
            for old_row in old_output["records"]:
                if old_row.get("screening_status") != "screened":
                    continue
                record_id = str(old_row["record_id"])
                old_source = old_records.get(record_id)
                current_source = current_records.get(record_id)
                if (
                    old_source is None
                    or current_source is None
                    or old_source.get("sha256") != current_source.get("sha256")
                    or old_source.get("content_id") != current_source.get("content_id")
                    or current_source.get("processing_status") != "card_ready"
                ):
                    raise PaperTopicScreeningError(
                        "paper_pool_screen.reused_source_changed",
                        f"已筛选论文的源身份或Card状态变化：{record_id}",
                    )
                child_run_id = str(old_row.get("screening_run_id") or "")
                try:
                    screening = load_paper_topic_screening(self.workspace, child_run_id)
                except PaperTopicScreeningError as exc:
                    if exc.code != "paper_screen.output_not_chinese":
                        raise
                    resume_rejections.append({
                        "record_id": record_id,
                        "screening_run_id": child_run_id,
                        "reason_code": exc.code,
                        "reason": str(exc),
                    })
                    continue
                exact_runs = current_source["exact_card_runs"]
                if (
                    len(exact_runs) != 1
                    or exact_runs[0]["workspace_paper_id"] != screening["workspace_paper_id"]
                    or exact_runs[0]["generation_id"] != screening["generation_id"]
                ):
                    raise PaperTopicScreeningError(
                        "paper_pool_screen.reused_card_changed",
                        f"已筛选论文的Card身份变化：{record_id}",
                    )
                resumed_children[record_id] = {
                    "record_id": record_id,
                    "run_id": child_run_id,
                    "status": "completed",
                    "paper_id": screening["paper_id"],
                    "workspace_paper_id": screening["workspace_paper_id"],
                    "paper_relevance": screening["paper_relevance"],
                    "output_path": str(
                        self.workspace / PAPER_TOPIC_SCREENING_ROOT / "runs"
                        / child_run_id / "output" / "paper_topic_screening.json"
                    ),
                    "failure": None,
                    "reused_from_run_id": resume_from_run_id,
                }
        remaining = [row for row in ready if row["record_id"] not in resumed_children]
        selected = remaining[:max_papers] if max_papers is not None else remaining
        selected_ids = {row["record_id"] for row in selected}
        _write_json(run_dir / "input" / "request.json", {
            "inventory_run_id": inventory_run_id,
            "inventory_sha256": inventory["inventory_sha256"],
            "topic": topic.strip(),
            "max_papers": max_papers,
            "resume_from_run_id": resume_from_run_id,
            "selected_record_ids": [row["record_id"] for row in selected],
            "reused_record_ids": sorted(resumed_children),
            "resume_rejections": resume_rejections,
        })
        _write_jsonl(run_dir / "audit" / "resume_rejections.jsonl", resume_rejections)
        child_runs = list(resumed_children.values())
        for index, row in enumerate(selected, start=1):
            exact = row["exact_card_runs"][0]
            child_run_id = f"{resolved}--paper-{index:04d}"
            result = self.paper_runner.run(
                paper_id=exact["paper_id"],
                workspace_paper_id=exact["workspace_paper_id"],
                topic=topic.strip(),
                run_id=child_run_id,
                provider=provider,
                api_url=api_url,
                api_key_env=api_key_env,
                timeout=timeout,
                model_profile_path=model_profile_path,
                review_config_path=review_config_path,
            )
            child_runs.append({"record_id": row["record_id"], **result})
            _write_jsonl(run_dir / "runs" / "child_runs.jsonl", child_runs)
        child_by_record = {row["record_id"]: row for row in child_runs}
        records = []
        for row in inventory["records"]:
            child = child_by_record.get(row["record_id"])
            if child is not None:
                screening_status = "screened" if child["status"] == "completed" else "screening_failed"
            elif row["record_id"] in selected_ids:
                screening_status = "screening_failed"
            elif row["processing_status"] == "card_ready":
                screening_status = "pending_screening"
            elif row["processing_status"] == "needs_card":
                screening_status = "pending_card"
            elif row["processing_status"] == "duplicate_source":
                screening_status = "duplicate_source"
            else:
                screening_status = "blocked"
            records.append({
                "record_id": row["record_id"],
                "content_id": row["content_id"],
                "relative_path": row["relative_path"],
                "processing_status": row["processing_status"],
                "screening_status": screening_status,
                "screening_run_id": child["run_id"] if child else None,
                "paper_relevance": child["paper_relevance"] if child else None,
                "failure": child["failure"] if child else None,
            })
        summary = _pool_summary(records)
        status = (
            "completed_with_failures" if summary["screening_failed_count"]
            else "completed_partial" if summary["pending_screening_count"]
            else "completed"
        )
        output = {
            "schema_version": "paper_pool.screening.v1",
            "run_id": resolved,
            "inventory_run_id": inventory_run_id,
            "topic": topic.strip(),
            "pool_complete": summary["screened_count"] == inventory["summary"]["unique_content_count"],
            "summary": summary,
            "records": records,
        }
        _write_json(run_dir / "output" / "paper_pool_screening.json", output)
        _write_jsonl(run_dir / "output" / "paper_pool_screening.jsonl", records)
        _write_text(run_dir / "review" / "paper_pool_screening.md", _render_pool(output))
        manifest = {
            "schema_version": PAPER_POOL_SCREENING_RUN_SCHEMA_VERSION,
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
            "pool_complete": output["pool_complete"],
            "summary": summary,
            "report_path": str(run_dir / "review" / "paper_pool_screening.md"),
            "failure": None,
        }


def _pool_summary(records):
    return {
        "source_file_count": len(records),
        "screened_count": sum(row["screening_status"] == "screened" for row in records),
        "screening_failed_count": sum(row["screening_status"] == "screening_failed" for row in records),
        "pending_screening_count": sum(row["screening_status"] == "pending_screening" for row in records),
        "pending_card_count": sum(row["screening_status"] == "pending_card" for row in records),
        "blocked_count": sum(row["screening_status"] == "blocked" for row in records),
        "duplicate_source_count": sum(row["screening_status"] == "duplicate_source" for row in records),
    }


def _screen_manifest(run_id, status, started_at, snapshot, workspace_id, screening, stage, output_sha, failure):
    return {
        "schema_version": PAPER_TOPIC_SCREENING_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "paper_id": snapshot.paper_id if snapshot else None,
        "workspace_paper_id": workspace_id,
        "generation_id": snapshot.generation_id if snapshot else None,
        "topic_sha256": snapshot.topic_sha256 if snapshot else None,
        "input_sha256": snapshot.input_sha256 if snapshot else None,
        "screening_id": screening["screening_id"] if screening else None,
        "output_sha256": output_sha,
        "stage_result": stage,
        "failure": failure,
    }


def _render_screening(row):
    lines = [
        "# 单篇论文主题筛选", "",
        f"- 论文：{row['paper_title']}",
        f"- 相关性：`{row['paper_relevance']}`",
        f"- 理由：{row['relevance_reason']}",
        f"- 主题摘要：{row['topic_summary']}",
        f"- 代表Card：{len(row['selected_materials'])}", "",
    ]
    return "\n".join(lines)


def _render_pool(output):
    s = output["summary"]
    return "\n".join([
        "# 全论文池主题筛选账本", "",
        f"- 主题：{output['topic']}",
        f"- 论文池是否全部完成：`{output['pool_complete']}`",
        f"- 已筛选：{s['screened_count']}",
        f"- 筛选失败：{s['screening_failed_count']}",
        f"- 待筛选：{s['pending_screening_count']}",
        f"- 待制卡：{s['pending_card_count']}",
        f"- 阻断：{s['blocked_count']}",
        f"- 重复源：{s['duplicate_source_count']}", "",
        "完整逐文件状态见 `output/paper_pool_screening.jsonl`。", "",
    ])


def _stable_id(prefix, value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _validate_chinese_scope(scope):
    fields = [
        ("relevance_reason", scope.get("relevance_reason")),
        ("topic_summary", scope.get("topic_summary")),
    ]
    fields.extend(
        (f"selected_materials[{index}].selection_reason", row.get("selection_reason"))
        for index, row in enumerate(scope.get("selected_materials") or [])
    )
    invalid = [name for name, value in fields if not _contains_cjk(value)]
    if invalid:
        raise PaperTopicScreeningError(
            "paper_screen.output_not_chinese",
            f"筛选自然语言字段必须使用中文：{invalid}",
        )


def _contains_cjk(value):
    return isinstance(value, str) and any("\u4e00" <= char <= "\u9fff" for char in value)


def _safe_segment(value):
    if not value or Path(value).name != value or value in {".", ".."}:
        raise PaperTopicScreeningError("paper_screen.run_id_invalid", "run_id不是安全路径段。")


def _sha256_file(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperTopicScreeningError(
            "paper_screen.json_read_failed", f"无法读取JSON：{path}"
        ) from exc
    if not isinstance(value, dict):
        raise PaperTopicScreeningError(
            "paper_screen.json_object_required", "JSON必须是对象。"
        )
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
