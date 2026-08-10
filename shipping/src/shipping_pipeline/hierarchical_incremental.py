from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .hierarchical_global_landscape import (
    DEFAULT_HIERARCHICAL_GLOBAL_CONFIG,
    HIERARCHICAL_GLOBAL_ROOT,
    HIERARCHICAL_GLOBAL_RUN_SCHEMA_VERSION,
    HierarchicalGlobalLandscapeRunner,
)
from .hierarchical_landscape import (
    HIERARCHICAL_LANDSCAPE_ROOT,
    HIERARCHICAL_LOCAL_BATCH_RUN_SCHEMA_VERSION,
)
from .hierarchical_lookback import (
    DEFAULT_LOOKBACK_CONFIG,
    LOOKBACK_RUN_SCHEMA_VERSION,
    build_lookback_schema,
    create_lookback_snapshot,
    load_lookback_config,
    validate_lookback,
)
from .llm_analysis import DEFAULT_MODEL_PROFILE, DEFAULT_TOKENIZER_CACHE, AnalysisInputError
from .llm_json_stage import execute_json_stage
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_model_profile import load_model_profile
from .llm_tokenizer import DeepSeekV4TokenCounter
from .paper_understanding_batch import PaperUnderstandingBatchRunner
from .research_landscape import (
    DEFAULT_RESEARCH_LANDSCAPE_CONFIG,
    ResearchLandscapeRunner,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INCREMENTAL_CONFIG = PROJECT_ROOT / "config" / "hierarchical-incremental-default.json"
HIERARCHICAL_INCREMENTAL_ROOT = "_hierarchical_incremental_runs"
INCREMENTAL_RUN_SCHEMA_VERSION = "llm.hierarchical_incremental_run.v1"
INCREMENTAL_CONFIG_SCHEMA_VERSION = "llm.hierarchical_incremental_config.v1"
ADJUDICATION_SCHEMA_VERSION = "llm.hierarchical_candidate_adjudication.v1"


ADJUDICATION_SYSTEM_PROMPT = """你是层级文献综合的候选晋级裁决器。输入包含回看请求、H4候选匹配理由和候选论文的完整Paper Understanding。
你必须逐项裁决输入中的每个候选分配，不得遗漏、重复或增加候选。support_level只能是direct、partial、not_supported：direct表示论文自身结果可直接回答回看请求；partial表示只能提供背景、间接线索、预测或相邻问题；not_supported表示不能回答。
不得因为论文题目相近而判定direct。reason必须指出Paper Understanding中的具体方法、数据、结果或局限。只输出符合JSON Schema的对象，不输出Markdown或机器ID。"""


@dataclass(frozen=True)
class IncrementalConfig:
    schema_version: str
    max_promotions_per_request: int
    adjudication_max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "max_promotions_per_request": self.max_promotions_per_request,
            "adjudication_max_output_tokens": self.adjudication_max_output_tokens,
        }


class HierarchicalIncrementalRunner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        understanding_batch_runner: Any | None = None,
        landscape_runner: Any | None = None,
        global_runner: Any | None = None,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.understanding_batch_runner = understanding_batch_runner or PaperUnderstandingBatchRunner(self.workspace)
        self.landscape_runner = landscape_runner or ResearchLandscapeRunner(self.workspace)
        self.global_runner = global_runner or HierarchicalGlobalLandscapeRunner(self.workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        lookback_run_id: str,
        run_id: str | None = None,
        resume_from_run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        understanding_config_path: str | Path,
        landscape_config_path: str | Path = DEFAULT_RESEARCH_LANDSCAPE_CONFIG,
        global_config_path: str | Path = DEFAULT_HIERARCHICAL_GLOBAL_CONFIG,
        incremental_config_path: str | Path = DEFAULT_INCREMENTAL_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        _safe_segment(lookback_run_id)
        if resume_from_run_id is not None:
            _safe_segment(resume_from_run_id)
        run_dir = self.workspace / HIERARCHICAL_INCREMENTAL_ROOT / "runs" / resolved
        if run_dir.exists():
            raise AnalysisInputError(f"H5运行ID已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        failure = None
        summary = None
        children: dict[str, Any] = {}
        try:
            config = load_incremental_config(Path(incremental_config_path))
            lineage = _load_lineage(self.workspace, lookback_run_id)
            global_retry = _load_global_retry(
                self.workspace,
                resume_from_run_id=resume_from_run_id,
                lookback_run_id=lookback_run_id,
            )
            if global_retry is not None:
                return self._retry_global_only(
                    run_dir=run_dir,
                    run_id=resolved,
                    started_at=started_at,
                    lookback_run_id=lookback_run_id,
                    resume_from_run_id=str(resume_from_run_id),
                    lineage=lineage,
                    retry=global_retry,
                    provider=provider,
                    api_url=api_url,
                    api_key_env=api_key_env,
                    model_profile_path=model_profile_path,
                    global_config_path=global_config_path,
                    timeout=timeout,
                )
            resume_understanding_run_id = _load_resume_understanding(
                self.workspace,
                resume_from_run_id=resume_from_run_id,
                lookback_run_id=lookback_run_id,
            )
            assignments = _candidate_assignments(lineage)
            _write_json(run_dir / "input" / "lineage.json", lineage["lineage"])
            _write_jsonl(run_dir / "input" / "candidate_assignments.jsonl", assignments)
            _write_json(run_dir / "input" / "incremental_config.json", config.to_dict())
            if not assignments:
                summary = _summary([], [], [], lineage["baseline_local"]["records"])
                _publish(run_dir, resolved, lookback_run_id, lineage, [], [], None, summary)
                status = "completed_no_changes"
            else:
                record_ids = list(dict.fromkeys(row["record_id"] for row in assignments))
                relevance = list(dict.fromkeys(row["paper_relevance"] for row in assignments))
                understanding_run_id = f"{resolved}--understanding"
                understanding_result = self.understanding_batch_runner.run(
                    screening_run_id=lineage["lookback_manifest"]["screening_run_id"],
                    run_id=understanding_run_id,
                    relevance=relevance,
                    record_ids=record_ids,
                    resume_from_run_id=resume_understanding_run_id,
                    provider=provider,
                    api_url=api_url,
                    api_key_env=api_key_env,
                    model_profile_path=model_profile_path,
                    understanding_config_path=understanding_config_path,
                    timeout=timeout,
                )
                children["understanding_batch_run_id"] = understanding_run_id
                understandings, unavailable = _load_understandings(self.workspace, understanding_run_id, assignments)
                _write_jsonl(run_dir / "audit" / "understanding_unavailable.jsonl", unavailable)
                adjudicable = [row for row in assignments if row["record_id"] in understandings]
                reused_adjudications = _load_reusable_adjudications(
                    self.workspace,
                    resume_from_run_id=resume_from_run_id,
                    assignments=adjudicable,
                    current_understandings=understandings,
                )
                reused_keys = {
                    (row["look_back_request_id"], row["record_id"])
                    for row in reused_adjudications
                }
                pending_adjudication = [
                    row for row in adjudicable
                    if (row["look_back_request_id"], row["record_id"]) not in reused_keys
                ]
                new_adjudications = self._adjudicate(
                    run_dir=run_dir,
                    topic=lineage["lookback_output"]["topic"],
                    assignments=pending_adjudication,
                    understandings=understandings,
                    config=config,
                    provider=provider,
                    api_url=api_url,
                    api_key_env=api_key_env,
                    model_profile_path=Path(model_profile_path),
                    timeout=timeout,
                ) if pending_adjudication else []
                adjudication_by_key = {
                    (row["look_back_request_id"], row["record_id"]): row
                    for row in reused_adjudications + new_adjudications
                }
                adjudications = [
                    adjudication_by_key[(row["look_back_request_id"], row["record_id"])]
                    for row in adjudicable
                ]
                _write_jsonl(run_dir / "audit" / "reused_adjudications.jsonl", reused_adjudications)
                promotions = select_promotions(adjudications, assignments, config)
                _write_jsonl(run_dir / "output" / "candidate_adjudications.jsonl", adjudications)
                _write_jsonl(run_dir / "output" / "promotions.jsonl", promotions)
                if not promotions:
                    summary = _summary(assignments, adjudications, promotions, lineage["baseline_local"]["records"])
                    _publish(run_dir, resolved, lookback_run_id, lineage, adjudications, promotions, None, summary)
                    status = (
                        "completed_partial_no_changes"
                        if summary["unadjudicated_assignment_count"]
                        else "completed_no_changes"
                    )
                else:
                    local_run_id = f"{resolved}--local"
                    local_result = self._run_incremental_local(
                        run_dir=run_dir,
                        local_run_id=local_run_id,
                        lineage=lineage,
                        promotions=promotions,
                        understandings=understandings,
                        provider=provider,
                        api_url=api_url,
                        api_key_env=api_key_env,
                        model_profile_path=model_profile_path,
                        landscape_config_path=landscape_config_path,
                        timeout=timeout,
                        resume_from_run_id=resume_from_run_id,
                    )
                    children["local_batch_run_id"] = local_run_id
                    if local_result["status"] != "completed":
                        raise AnalysisInputError("受影响主题簇未全部重算成功，禁止触发全局归并。")
                    global_run_id = f"{resolved}--global"
                    global_result = self.global_runner.run(
                        local_batch_run_id=local_run_id,
                        run_id=global_run_id,
                        provider=provider,
                        api_url=api_url,
                        api_key_env=api_key_env,
                        model_profile_path=model_profile_path,
                        global_config_path=global_config_path,
                        timeout=timeout,
                    )
                    children["global_run_id"] = global_run_id
                    if global_result["status"] != "completed":
                        raise AnalysisInputError("增量局部批次已完成，但新全局归并失败。")
                    summary = _summary(assignments, adjudications, promotions, local_result["records"])
                    _publish(run_dir, resolved, lookback_run_id, lineage, adjudications, promotions, children, summary)
                    status = (
                        "completed_partial"
                        if summary["unadjudicated_assignment_count"]
                        else "completed"
                    )
        except Exception as exc:
            status = "failed"
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "stage": "hierarchical_incremental",
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
        manifest = {
            "schema_version": INCREMENTAL_RUN_SCHEMA_VERSION,
            "run_id": resolved,
            "status": status,
            "started_at": started_at,
            "finished_at": _now(),
            "lookback_run_id": lookback_run_id,
            "resume_from_run_id": resume_from_run_id,
            "children": children,
            "summary": summary,
            "failure": failure,
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {"run_id": resolved, "status": status, "summary": summary, "children": children, "failure": failure, "run_dir": str(run_dir)}

    def _retry_global_only(
        self,
        *,
        run_dir: Path,
        run_id: str,
        started_at: str,
        lookback_run_id: str,
        resume_from_run_id: str,
        lineage: dict[str, Any],
        retry: dict[str, Any],
        provider: str,
        api_url: str | None,
        api_key_env: str,
        model_profile_path: str | Path,
        global_config_path: str | Path,
        timeout: int,
    ) -> dict[str, Any]:
        children = dict(retry["children"])
        global_run_id = f"{run_id}--global"
        result = self.global_runner.run(
            local_batch_run_id=retry["local_batch_run_id"],
            run_id=global_run_id,
            provider=provider,
            api_url=api_url,
            api_key_env=api_key_env,
            model_profile_path=model_profile_path,
            global_config_path=global_config_path,
            timeout=timeout,
        )
        children["global_run_id"] = global_run_id
        assignments = _candidate_assignments(lineage)
        summary = _summary(
            assignments, retry["adjudications"], retry["promotions"], retry["local_records"]
        )
        if result["status"] == "completed":
            status = "completed_partial" if summary["unadjudicated_assignment_count"] else "completed"
            failure = None
            _publish(
                run_dir, run_id, lookback_run_id, lineage,
                retry["adjudications"], retry["promotions"], children, summary,
            )
        else:
            status = "failed"
            failure = {
                "error_code": "hierarchical_incremental.global_retry_failed",
                "error_message": "已复用局部增量批次，但全局归并重试仍失败。",
                "stage": "global_retry", "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
        manifest = {
            "schema_version": INCREMENTAL_RUN_SCHEMA_VERSION,
            "run_id": run_id, "status": status,
            "started_at": started_at, "finished_at": _now(),
            "lookback_run_id": lookback_run_id,
            "resume_from_run_id": resume_from_run_id,
            "resume_stage": "global_only",
            "children": children, "summary": summary, "failure": failure,
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": run_id, "status": status, "summary": summary,
            "children": children, "failure": failure, "run_dir": str(run_dir),
        }

    def _adjudicate(
        self,
        *,
        run_dir: Path,
        topic: str,
        assignments: list[dict[str, Any]],
        understandings: dict[str, dict[str, Any]],
        config: IncrementalConfig,
        provider: str,
        api_url: str | None,
        api_key_env: str,
        model_profile_path: Path,
        timeout: int,
    ) -> list[dict[str, Any]]:
        profile = load_model_profile(model_profile_path)
        if provider != profile.provider:
            raise AnalysisInputError("provider与model profile不一致。")
        schema = build_adjudication_schema(topic, assignments)
        prompt = json.dumps({
            "任务": "复核H4候选是否直接回答对应回看请求",
            "综述主题": topic,
            "候选分配": [
                {**row, "paper_understanding": understandings[row["record_id"]]["understanding"]}
                for row in assignments
            ],
            "输出JSONSchema": schema,
        }, ensure_ascii=False, separators=(",", ":"))
        client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
            api_url=api_url, api_key_env=api_key_env, model=profile.request_model, timeout=timeout
        )
        counter = self.token_counter or DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
        parsed, _ = execute_json_stage(
            run_dir=run_dir,
            stage="candidate_adjudication",
            task_name="hierarchical_candidate_adjudication",
            directory_name="candidate_adjudication",
            system_prompt=ADJUDICATION_SYSTEM_PROMPT,
            user_prompt=prompt,
            max_output_tokens=config.adjudication_max_output_tokens,
            context={"assignment_count": len(assignments)},
            client=client,
            profile=profile,
            token_counter=counter,
        )
        return validate_adjudications(parsed, schema=schema, assignments=assignments)

    def _run_incremental_local(
        self,
        *,
        run_dir: Path,
        local_run_id: str,
        lineage: dict[str, Any],
        promotions: list[dict[str, Any]],
        understandings: dict[str, dict[str, Any]],
        provider: str,
        api_url: str | None,
        api_key_env: str,
        model_profile_path: str | Path,
        landscape_config_path: str | Path,
        timeout: int,
        resume_from_run_id: str | None = None,
    ) -> dict[str, Any]:
        baseline = lineage["baseline_local"]
        affected = {cluster_id for row in promotions for cluster_id in row["target_cluster_ids"]}
        promoted_by_cluster: dict[str, list[str]] = {cluster_id: [] for cluster_id in affected}
        for row in promotions:
            understanding_run_id = understandings[row["record_id"]]["run_id"]
            for cluster_id in row["target_cluster_ids"]:
                if understanding_run_id not in promoted_by_cluster[cluster_id]:
                    promoted_by_cluster[cluster_id].append(understanding_run_id)
        local_dir = self.workspace / HIERARCHICAL_LANDSCAPE_ROOT / "local_runs" / local_run_id
        if local_dir.exists():
            raise AnalysisInputError(f"H5局部运行ID已存在：{local_run_id}")
        local_dir.mkdir(parents=True)
        reusable_clusters = _load_reusable_incremental_clusters(
            self.workspace, resume_from_run_id=resume_from_run_id
        )
        records = []
        for index, old in enumerate(baseline["records"], start=1):
            cluster_id = str(old["cluster_id"])
            if cluster_id not in affected:
                records.append({**old, "reused_from_run_id": lineage["lineage"]["baseline_local_batch_run_id"]})
                continue
            old_manifest = _read_json(
                self.workspace / "_research_landscapes" / "runs" / str(old["landscape_run_id"]) / "manifest.json"
            )
            old_collection = _read_json(Path(str(old_manifest["collection_path"])))
            source_ids = list(old_collection["source_understanding_run_ids"])
            source_ids.extend(run_id for run_id in promoted_by_cluster[cluster_id] if run_id not in source_ids)
            reusable = reusable_clusters.get(cluster_id)
            if reusable is not None and reusable["source_understanding_run_ids"] == source_ids:
                records.append({
                    **reusable["record"],
                    "reused_from_run_id": reusable["parent_local_batch_run_id"],
                })
                continue
            collection = {**old_collection, "source_understanding_run_ids": source_ids}
            collection_path = run_dir / "input" / "local_collections" / f"{cluster_id}.json"
            _write_json(collection_path, collection)
            child_run_id = f"{local_run_id}--cluster-{index:03d}-{cluster_id}"
            result = self.landscape_runner.run(
                collection_path=collection_path,
                run_id=child_run_id,
                provider=provider,
                api_url=api_url,
                api_key_env=api_key_env,
                model_profile_path=model_profile_path,
                landscape_config_path=landscape_config_path,
                timeout=timeout,
            )
            records.append({
                "cluster_id": cluster_id,
                "cluster_title": old["cluster_title"],
                "paper_count": len(source_ids),
                "local_status": "completed" if result["status"] == "completed" else "failed",
                "landscape_run_id": result["run_id"],
                "landscape_id": result.get("landscape_id"),
                "coverage": result.get("coverage"),
                "failure": result.get("failure"),
                "promoted_understanding_run_ids": promoted_by_cluster[cluster_id],
            })
        completed = sum(row["local_status"] == "completed" for row in records)
        failed = sum(row["local_status"] == "failed" for row in records)
        output = {
            "schema_version": "llm.hierarchical_local_landscape_batch.v1",
            "run_id": local_run_id,
            "plan_run_id": baseline["plan_run_id"],
            "plan_sha256": baseline["plan_sha256"],
            "topic": baseline["topic"],
            "review_goal": baseline["review_goal"],
            "summary": {
                "cluster_count": len(records), "completed_cluster_count": completed,
                "failed_cluster_count": failed, "pending_cluster_count": 0,
                "source_paper_count": baseline["summary"]["source_paper_count"] + len({row["record_id"] for row in promotions}),
                "assigned_paper_count": baseline["summary"]["assigned_paper_count"] + len({row["record_id"] for row in promotions}),
            },
            "records": records,
            "successful_landscape_run_ids": [row["landscape_run_id"] for row in records if row["local_status"] == "completed"],
            "incremental_parent_local_batch_run_id": lineage["lineage"]["baseline_local_batch_run_id"],
        }
        status = "completed" if not failed else "completed_with_failures"
        _write_json(local_dir / "output" / "local_landscape_batch.json", output)
        _write_jsonl(local_dir / "output" / "local_landscape_batch.jsonl", records)
        _write_json(local_dir / "manifest.json", {
            "schema_version": HIERARCHICAL_LOCAL_BATCH_RUN_SCHEMA_VERSION,
            "run_id": local_run_id,
            "status": status,
            "started_at": _now(), "finished_at": _now(),
            "plan_run_id": baseline["plan_run_id"], "plan_sha256": baseline["plan_sha256"],
            "resume_from_run_id": lineage["lineage"]["baseline_local_batch_run_id"],
            "summary": output["summary"], "failure": None,
        })
        return {"run_id": local_run_id, "status": status, "records": records, "summary": output["summary"]}


def load_incremental_config(path: Path) -> IncrementalConfig:
    raw = _read_json(path)
    if raw.get("schema_version") != INCREMENTAL_CONFIG_SCHEMA_VERSION or set(raw) != {
        "schema_version", "max_promotions_per_request", "adjudication_max_output_tokens"
    }:
        raise AnalysisInputError("H5配置格式无效。")
    config = IncrementalConfig(**raw)
    if config.max_promotions_per_request < 1 or config.adjudication_max_output_tokens < 1:
        raise AnalysisInputError("H5配置数值必须为正整数。")
    return config


def build_adjudication_schema(topic: str, assignments: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for row in assignments:
        items.append({
            "type": "object", "additionalProperties": False,
            "required": ["look_back_request_id", "record_id", "support_level", "confidence", "reason", "supported_points"],
            "properties": {
                "look_back_request_id": {"const": row["look_back_request_id"]},
                "record_id": {"const": row["record_id"]},
                "support_level": {"enum": ["direct", "partial", "not_supported"]},
                "confidence": {"enum": ["high", "medium", "low"]},
                "reason": {"type": "string", "minLength": 1, "maxLength": 1200},
                "supported_points": {"type": "array", "maxItems": 6, "items": {"type": "string", "minLength": 1, "maxLength": 600}},
            },
        })
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "topic", "adjudications"],
        "properties": {
            "schema_version": {"const": ADJUDICATION_SCHEMA_VERSION},
            "topic": {"const": topic},
            "adjudications": {"type": "array", "minItems": len(items), "maxItems": len(items), "prefixItems": items, "items": False},
        },
    }


def validate_adjudications(payload: object, *, schema: dict[str, Any], assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        raise AnalysisInputError(f"候选晋级裁决Schema无效：{errors[0].message}")
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    rows = value["adjudications"]
    for row, source in zip(rows, assignments, strict=True):
        row["target_cluster_ids"] = list(source["target_cluster_ids"])
        row["paper_title"] = source["paper_title"]
        row["adjudication_id"] = _stable_id("candidate_adjudication", row)
    return rows


def select_promotions(adjudications: list[dict[str, Any]], assignments: list[dict[str, Any]], config: IncrementalConfig) -> list[dict[str, Any]]:
    source_by_key = {(row["look_back_request_id"], row["record_id"]): row for row in assignments}
    accepted = [row for row in adjudications if row["support_level"] == "direct" and row["confidence"] in {"high", "medium"}]
    rank = {"high": 0, "medium": 1}
    accepted.sort(key=lambda row: (row["look_back_request_id"], rank[row["confidence"]], row["record_id"]))
    counts: dict[str, int] = {}
    promotions = []
    for row in accepted:
        request_id = row["look_back_request_id"]
        if counts.get(request_id, 0) >= config.max_promotions_per_request:
            continue
        source = source_by_key[(request_id, row["record_id"])]
        promotions.append({
            "promotion_id": _stable_id("promotion", row),
            "look_back_request_id": request_id,
            "record_id": row["record_id"],
            "paper_id": source["paper_id"],
            "paper_title": source["paper_title"],
            "target_cluster_ids": list(source["target_cluster_ids"]),
            "confidence": row["confidence"],
            "reason": row["reason"],
        })
        counts[request_id] = counts.get(request_id, 0) + 1
    return promotions


def _load_lineage(workspace: Path, lookback_run_id: str) -> dict[str, Any]:
    lookback_dir = workspace / "_hierarchical_lookbacks" / "runs" / lookback_run_id
    lookback_manifest = _read_json(lookback_dir / "manifest.json")
    lookback_output = _read_json(lookback_dir / "output" / "hierarchical_lookback.json")
    if lookback_manifest.get("schema_version") != LOOKBACK_RUN_SCHEMA_VERSION or lookback_manifest.get("status") != "completed":
        raise AnalysisInputError("H4回看运行不可用。")
    lookback_snapshot = create_lookback_snapshot(
        workspace,
        global_run_id=str(lookback_manifest["global_run_id"]),
        screening_run_id=str(lookback_manifest["screening_run_id"]),
    )
    lookback_config = load_incremental_lookback_config()
    lookback_schema = build_lookback_schema(lookback_snapshot, lookback_config)
    replayed_lookback = validate_lookback(
        _strip_lookback_ids(lookback_output),
        schema=lookback_schema,
        snapshot=lookback_snapshot,
        config=lookback_config,
    )
    if (
        replayed_lookback != lookback_output
        or lookback_snapshot.input_sha256 != lookback_manifest.get("input_sha256")
    ):
        raise AnalysisInputError("H4回看运行无法严格重放。")
    global_run_id = str(lookback_manifest["global_run_id"])
    global_manifest = _read_json(workspace / HIERARCHICAL_GLOBAL_ROOT / "runs" / global_run_id / "manifest.json")
    if global_manifest.get("schema_version") != HIERARCHICAL_GLOBAL_RUN_SCHEMA_VERSION or global_manifest.get("status") != "completed":
        raise AnalysisInputError("H4来源全局运行不可用。")
    local_run_id = str(global_manifest["local_batch_run_id"])
    local_dir = workspace / HIERARCHICAL_LANDSCAPE_ROOT / "local_runs" / local_run_id
    local_manifest = _read_json(local_dir / "manifest.json")
    baseline_local = _read_json(local_dir / "output" / "local_landscape_batch.json")
    if local_manifest.get("schema_version") != HIERARCHICAL_LOCAL_BATCH_RUN_SCHEMA_VERSION or local_manifest.get("status") != "completed":
        raise AnalysisInputError("H4来源局部批次不可用。")
    global_output = _read_json(workspace / HIERARCHICAL_GLOBAL_ROOT / "runs" / global_run_id / "output" / "hierarchical_global_landscape.json")
    return {
        "lookback_manifest": lookback_manifest,
        "lookback_output": lookback_output,
        "global_output": global_output,
        "baseline_local": baseline_local,
        "lineage": {
            "lookback_run_id": lookback_run_id,
            "baseline_global_run_id": global_run_id,
            "baseline_local_batch_run_id": local_run_id,
        },
    }


def _load_resume_understanding(
    workspace: Path, *, resume_from_run_id: str | None, lookback_run_id: str
) -> str | None:
    if resume_from_run_id is None:
        return None
    manifest = _read_json(
        workspace / HIERARCHICAL_INCREMENTAL_ROOT / "runs" / resume_from_run_id / "manifest.json"
    )
    if (
        manifest.get("schema_version") != INCREMENTAL_RUN_SCHEMA_VERSION
        or manifest.get("lookback_run_id") != lookback_run_id
        or manifest.get("status") not in {
            "completed", "completed_no_changes", "completed_partial",
            "completed_partial_no_changes", "failed",
        }
    ):
        raise AnalysisInputError("H5续跑父代际不可用或来源H4不一致。")
    batch_run_id = manifest.get("children", {}).get("understanding_batch_run_id")
    if not batch_run_id:
        raise AnalysisInputError("H5续跑父代际没有可复用的Understanding批次。")
    return str(batch_run_id)


def _load_reusable_adjudications(
    workspace: Path,
    *,
    resume_from_run_id: str | None,
    assignments: list[dict[str, Any]],
    current_understandings: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if resume_from_run_id is None:
        return []
    parent_dir = workspace / HIERARCHICAL_INCREMENTAL_ROOT / "runs" / resume_from_run_id
    manifest = _read_json(parent_dir / "manifest.json")
    parent_batch_run_id = manifest.get("children", {}).get("understanding_batch_run_id")
    adjudication_path = parent_dir / "output" / "candidate_adjudications.jsonl"
    if not parent_batch_run_id or not adjudication_path.exists():
        return []
    parent_understandings, _ = _load_understandings(
        workspace, str(parent_batch_run_id), assignments
    )
    unchanged_records = {
        record_id for record_id, current in current_understandings.items()
        if record_id in parent_understandings
        and current["run_id"] == parent_understandings[record_id]["run_id"]
    }
    allowed_keys = {
        (row["look_back_request_id"], row["record_id"])
        for row in assignments if row["record_id"] in unchanged_records
    }
    reusable = []
    seen = set()
    for row in _read_jsonl(adjudication_path):
        key = (row.get("look_back_request_id"), row.get("record_id"))
        if key not in allowed_keys or key in seen:
            continue
        if row.get("support_level") not in {"direct", "partial", "not_supported"} or row.get("confidence") not in {"high", "medium", "low"}:
            continue
        reusable.append(row)
        seen.add(key)
    return reusable


def _load_reusable_incremental_clusters(
    workspace: Path,
    *,
    resume_from_run_id: str | None,
) -> dict[str, dict[str, Any]]:
    if resume_from_run_id is None:
        return {}
    parent_dir = workspace / HIERARCHICAL_INCREMENTAL_ROOT / "runs" / resume_from_run_id
    parent_manifest = _read_json(parent_dir / "manifest.json")
    parent_local_batch_run_id = parent_manifest.get("children", {}).get("local_batch_run_id")
    if not parent_local_batch_run_id:
        return {}
    local_dir = workspace / HIERARCHICAL_LANDSCAPE_ROOT / "local_runs" / str(parent_local_batch_run_id)
    local_output_path = local_dir / "output" / "local_landscape_batch.json"
    if not local_output_path.exists():
        return {}
    local_output = _read_json(local_output_path)
    reusable: dict[str, dict[str, Any]] = {}
    for row in local_output.get("records", []):
        if row.get("local_status") != "completed" or not row.get("promoted_understanding_run_ids"):
            continue
        landscape_run_id = str(row.get("landscape_run_id", ""))
        landscape_dir = workspace / "_research_landscapes" / "runs" / landscape_run_id
        manifest_path = landscape_dir / "manifest.json"
        output_path = landscape_dir / "output" / "research_landscape.json"
        if not manifest_path.exists() or not output_path.exists():
            continue
        landscape_manifest = _read_json(manifest_path)
        collection_path = landscape_manifest.get("collection_path")
        if landscape_manifest.get("status") != "completed" or not collection_path:
            continue
        collection = _read_json(Path(str(collection_path)))
        reusable[str(row["cluster_id"])] = {
            "record": row,
            "source_understanding_run_ids": list(collection["source_understanding_run_ids"]),
            "parent_local_batch_run_id": str(parent_local_batch_run_id),
        }
    return reusable


def _load_global_retry(
    workspace: Path, *, resume_from_run_id: str | None, lookback_run_id: str
) -> dict[str, Any] | None:
    if resume_from_run_id is None:
        return None
    parent_dir = workspace / HIERARCHICAL_INCREMENTAL_ROOT / "runs" / resume_from_run_id
    manifest = _read_json(parent_dir / "manifest.json")
    if manifest.get("status") != "failed":
        return None
    children = manifest.get("children") or {}
    local_batch_run_id = children.get("local_batch_run_id")
    if (
        manifest.get("schema_version") != INCREMENTAL_RUN_SCHEMA_VERSION
        or manifest.get("lookback_run_id") != lookback_run_id
        or not local_batch_run_id
    ):
        raise AnalysisInputError("H5全局重试父代际无效或来源H4不一致。")
    local_dir = workspace / HIERARCHICAL_LANDSCAPE_ROOT / "local_runs" / str(local_batch_run_id)
    local_manifest = _read_json(local_dir / "manifest.json")
    local_output = _read_json(local_dir / "output" / "local_landscape_batch.json")
    if (
        local_manifest.get("schema_version") != HIERARCHICAL_LOCAL_BATCH_RUN_SCHEMA_VERSION
        or local_output.get("run_id") != local_batch_run_id
    ):
        raise AnalysisInputError("H5父代际局部批次身份无效。")
    if (
        local_manifest.get("status") != "completed"
        or any(row.get("local_status") != "completed" for row in local_output.get("records", []))
    ):
        return None
    adjudications = _read_jsonl(parent_dir / "output" / "candidate_adjudications.jsonl")
    promotions = _read_jsonl(parent_dir / "output" / "promotions.jsonl")
    if not promotions:
        raise AnalysisInputError("H5父代际没有晋级论文，不应执行全局阶段重试。")
    return {
        "children": children,
        "local_batch_run_id": str(local_batch_run_id),
        "local_records": local_output["records"],
        "adjudications": adjudications,
        "promotions": promotions,
    }


def load_incremental_lookback_config():
    return load_lookback_config(DEFAULT_LOOKBACK_CONFIG)


def _strip_lookback_ids(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value, ensure_ascii=False))
    result.pop("lookback_id", None)
    for request in result.get("request_results", []):
        request.pop("request_result_id", None)
        for candidate in request.get("candidates", []):
            for field in (
                "candidate_assignment_id", "paper_id", "paper_title",
                "screening_run_id", "paper_relevance",
            ):
                candidate.pop(field, None)
    return result


def _candidate_assignments(lineage: dict[str, Any]) -> list[dict[str, Any]]:
    request_by_id = {row["look_back_request_id"]: row for row in lineage["global_output"]["look_back_requests"]}
    rows = []
    for result in lineage["lookback_output"]["request_results"]:
        request = request_by_id.get(result["look_back_request_id"])
        if request is None:
            raise AnalysisInputError("H4候选引用了来源全局运行中不存在的回看请求。")
        for candidate in result["candidates"]:
            rows.append({
                "look_back_request_id": result["look_back_request_id"],
                "question": request["question"],
                "reason": request["reason"],
                "target_cluster_ids": list(request["target_cluster_ids"]),
                "record_id": candidate["record_id"],
                "paper_id": candidate["paper_id"],
                "paper_title": candidate["paper_title"],
                "paper_relevance": candidate["paper_relevance"],
                "h4_matched_reason": candidate["matched_reason"],
                "h4_expected_use": candidate["expected_use"],
            })
    return rows


def _load_understandings(workspace: Path, batch_run_id: str, assignments: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    batch = _read_json(workspace / "_paper_understanding_batches" / "runs" / batch_run_id / "output" / "paper_understanding_batch.json")
    assignment_ids = {row["record_id"] for row in assignments}
    results = {}
    unavailable = []
    for row in batch["records"]:
        if row["record_id"] not in assignment_ids:
            continue
        if row["understanding_status"] != "understood":
            unavailable.append({"record_id": row["record_id"], "status": row["understanding_status"], "failure": row.get("failure")})
            continue
        run_id = str(row["understanding_run_id"])
        run_dir = workspace / "_paper_understandings" / "runs" / run_id
        manifest = _read_json(run_dir / "manifest.json")
        understanding = _read_json(run_dir / "output" / "paper_understanding.json")
        if manifest.get("status") != "completed":
            unavailable.append({"record_id": row["record_id"], "status": "manifest_not_completed"})
            continue
        results[row["record_id"]] = {"run_id": run_id, "understanding": understanding}
    return results, unavailable


def _summary(assignments: list[dict[str, Any]], adjudications: list[dict[str, Any]], promotions: list[dict[str, Any]], local_records: list[dict[str, Any]]) -> dict[str, int]:
    affected = {cluster_id for row in promotions for cluster_id in row["target_cluster_ids"]}
    return {
        "candidate_assignment_count": len(assignments),
        "adjudicated_assignment_count": len(adjudications),
        "unadjudicated_assignment_count": len(assignments) - len(adjudications),
        "promotion_count": len(promotions),
        "promoted_paper_count": len({row["record_id"] for row in promotions}),
        "affected_cluster_count": len(affected),
        "reused_cluster_count": sum(row["cluster_id"] not in affected for row in local_records),
    }


def _publish(run_dir: Path, run_id: str, lookback_run_id: str, lineage: dict[str, Any], adjudications: list[dict[str, Any]], promotions: list[dict[str, Any]], children: dict[str, Any] | None, summary: dict[str, int]) -> None:
    output = {
        "schema_version": "llm.hierarchical_incremental.v1", "run_id": run_id,
        "lookback_run_id": lookback_run_id, "lineage": lineage["lineage"],
        "summary": summary, "adjudications": adjudications, "promotions": promotions,
        "children": children or {},
    }
    _write_json(run_dir / "output" / "hierarchical_incremental.json", output)
    lines = ["# 候选晋级与主题增量重算", "", f"- 候选分配：{summary['candidate_assignment_count']}", f"- 已裁决：{summary['adjudicated_assignment_count']}", f"- 因Understanding不可用而未裁决：{summary['unadjudicated_assignment_count']}", f"- 晋级论文：{summary['promoted_paper_count']}", f"- 受影响主题簇：{summary['affected_cluster_count']}", f"- 复用主题簇：{summary['reused_cluster_count']}", ""]
    for row in adjudications:
        lines.extend([f"## {row['paper_title']}", "", f"- 裁决：{row['support_level']} / {row['confidence']}", f"- 原因：{row['reason']}", f"- 目标簇：{', '.join(row['target_cluster_ids'])}", ""])
    _write_text(run_dir / "review" / "hierarchical_incremental.md", "\n".join(lines))


def _safe_segment(value: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise AnalysisInputError("运行ID不是安全路径段。")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"无法读取JSON：{path}") from exc
    if not isinstance(value, dict):
        raise AnalysisInputError("JSON必须是对象。")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"无法读取JSONL：{path}") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise AnalysisInputError("JSONL每行必须是对象。")
    return rows


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _stable_id(prefix: str, value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + "_" + hashlib.sha256(serialized.encode()).hexdigest()[:20]


def _now() -> str:
    return datetime.now(UTC).isoformat()
