from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from .hierarchical_global_contracts import (
    GLOBAL_LANDSCAPE_SCHEMA_VERSION,
    HierarchicalGlobalConfig,
    build_hierarchical_global_schema,
    validate_hierarchical_global_landscape,
)
from .hierarchical_global_landscape import (
    DEFAULT_HIERARCHICAL_GLOBAL_CONFIG,
    HIERARCHICAL_GLOBAL_ROOT,
    HIERARCHICAL_GLOBAL_RUN_SCHEMA_VERSION,
    _strip_landscape_machine_ids,
    create_hierarchical_global_snapshot,
)
from .llm_analysis import DEFAULT_MODEL_PROFILE, DEFAULT_TOKENIZER_CACHE, AnalysisInputError
from .llm_json_stage import execute_json_stage
from .llm_model_profile import load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .paper_topic_screening import PAPER_POOL_SCREENING_ROOT, load_paper_topic_screening


LOOKBACK_ROOT = "_hierarchical_lookbacks"
LOOKBACK_RUN_SCHEMA_VERSION = "llm.hierarchical_lookback_run.v1"
LOOKBACK_OUTPUT_SCHEMA_VERSION = "llm.hierarchical_lookback.v1"
LOOKBACK_CONFIG_SCHEMA_VERSION = "llm.hierarchical_lookback_config.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOOKBACK_CONFIG = PROJECT_ROOT / "config" / "hierarchical-lookback-default.json"

LOOKBACK_SYSTEM_PROMPT = """你是层级文献综合的回看候选匹配器。输入是全局Landscape提出的回看请求，以及已经完成单篇主题筛选的候选论文投影。
只输出符合JSON Schema的对象。每个请求必须出现且仅出现一次；没有合适论文时输出空candidates，不得牵强匹配。候选只能使用输入中的record_id，并且相关性等级必须符合请求的desired_source_levels。
matched_reason只说明候选论文的筛选摘要如何回答请求，expected_use说明补入后预期核查什么。不得声称候选论文已经证明筛选摘要未明确表达的现场效果。一篇候选论文最多匹配配置允许的请求数。不得生成机器ID。"""
LOOKBACK_SYSTEM_PROMPT += "\n明确判断为不匹配、无法使用或未提供所需信息的论文，绝对不能放入candidates；这种情况只写入no_match_reason。"


@dataclass(frozen=True)
class LookbackConfig:
    schema_version: str
    max_candidates_per_request: int
    max_requests_per_candidate: int
    lookback_max_output_tokens: int

    def to_dict(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class LookbackSnapshot:
    topic: str
    global_run_id: str
    global_input_sha256: str
    requests: tuple[dict[str, Any], ...]
    candidates: tuple[dict[str, Any], ...]
    input_sha256: str


class HierarchicalLookbackRunner:
    def __init__(self, workspace: str | Path, *, analysis_client=None, token_counter=None, snapshot_loader=None) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.snapshot_loader = snapshot_loader or create_lookback_snapshot

    def run(
        self,
        *,
        global_run_id: str,
        screening_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        lookback_config_path: str | Path = DEFAULT_LOOKBACK_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / LOOKBACK_ROOT / "runs" / resolved
        if run_dir.exists():
            raise AnalysisInputError(f"回看run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot = profile = config = stage = output = None
        try:
            snapshot = self.snapshot_loader(
                self.workspace,
                global_run_id=global_run_id,
                screening_run_id=screening_run_id,
            )
            config = load_lookback_config(Path(lookback_config_path))
            profile = load_model_profile(Path(model_profile_path))
            if provider != profile.provider:
                raise AnalysisInputError("provider与model profile不一致。")
            schema = build_lookback_schema(snapshot, config)
            prompt = build_lookback_prompt(snapshot, schema=schema, config=config)
            _write_json(run_dir / "input" / "requests.json", list(snapshot.requests))
            _write_jsonl(run_dir / "input" / "candidate_projections.jsonl", snapshot.candidates)
            _write_json(run_dir / "input" / "output_schema.json", schema)
            _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
            _write_json(run_dir / "input" / "lookback_config.json", config.to_dict())
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url, api_key_env=api_key_env,
                model=profile.request_model, timeout=timeout,
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
            parsed, stage = execute_json_stage(
                run_dir=run_dir, stage="lookback", task_name="hierarchical_lookback",
                directory_name="lookback", system_prompt=LOOKBACK_SYSTEM_PROMPT,
                user_prompt=prompt, max_output_tokens=config.lookback_max_output_tokens,
                context={
                    "global_run_id": global_run_id,
                    "request_ids": [row["look_back_request_id"] for row in snapshot.requests],
                    "candidate_record_ids": [row["record_id"] for row in snapshot.candidates],
                    "input_sha256": snapshot.input_sha256,
                },
                client=client, profile=profile, token_counter=counter,
            )
            output = validate_lookback(parsed, schema=schema, snapshot=snapshot, config=config)
            summary = _summary(output)
            _write_json(run_dir / "lookback" / "validated_lookback.json", output)
            _write_json(run_dir / "output" / "hierarchical_lookback.json", output)
            _write_jsonl(
                run_dir / "output" / "candidate_assignments.jsonl",
                [
                    {"look_back_request_id": request["look_back_request_id"], **candidate}
                    for request in output["request_results"]
                    for candidate in request["candidates"]
                ],
            )
            _write_text(run_dir / "review" / "hierarchical_lookback.md", _render(output, summary))
            status = "completed"
            failure = None
        except Exception as exc:
            status = "failed"
            summary = None
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc), "stage": "lookback", "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
        manifest = {
            "schema_version": LOOKBACK_RUN_SCHEMA_VERSION,
            "run_id": resolved, "status": status,
            "started_at": started_at, "finished_at": _now(),
            "global_run_id": global_run_id, "screening_run_id": screening_run_id,
            "input_sha256": snapshot.input_sha256 if snapshot else None,
            "provider": profile.provider if profile else None,
            "model": profile.request_model if profile else None,
            "stage_result": stage, "summary": summary, "failure": failure,
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved, "status": status, "summary": summary,
            "failure": failure, "run_dir": str(run_dir),
            "output_path": str(run_dir / "output" / "hierarchical_lookback.json") if output else None,
            "review_path": str(run_dir / "review" / "hierarchical_lookback.md") if output else None,
        }


def create_lookback_snapshot(
    workspace: str | Path, *, global_run_id: str, screening_run_id: str
) -> LookbackSnapshot:
    workspace = Path(workspace)
    global_output, global_input_sha = _load_global(workspace, global_run_id)
    pool_dir = workspace / PAPER_POOL_SCREENING_ROOT / "runs" / screening_run_id
    pool_manifest = _read_json(pool_dir / "manifest.json")
    pool = _read_json(pool_dir / "output" / "paper_pool_screening.json")
    if pool_manifest.get("status") != "completed" or pool.get("run_id") != screening_run_id or pool.get("topic") != global_output["topic"]:
        raise AnalysisInputError("论文池筛选运行不完整或主题不一致。")
    current_papers = {
        str(paper_id)
        for dimension in global_output["global_dimensions"]
        for paper_id in dimension["paper_ids"]
    }
    desired_levels = {
        level
        for request in global_output["look_back_requests"]
        for level in request["desired_source_levels"]
    }
    candidates = []
    for row in pool["records"]:
        if row.get("screening_status") != "screened" or row.get("paper_relevance") not in desired_levels:
            continue
        screening = load_paper_topic_screening(workspace, str(row["screening_run_id"]))
        if screening["paper_id"] in current_papers:
            continue
        candidates.append({
            "record_id": str(row["record_id"]),
            "paper_id": str(screening["paper_id"]),
            "paper_title": str(screening["paper_title"]),
            "screening_run_id": str(row["screening_run_id"]),
            "paper_relevance": str(screening["paper_relevance"]),
            "relevance_reason": str(screening["relevance_reason"]),
            "topic_summary": str(screening["topic_summary"]),
            "selected_material_reasons": [
                str(item["selection_reason"])
                for item in screening["selected_materials"]
            ],
        })
    input_sha = _sha256_json({
        "global_input_sha256": global_input_sha,
        "requests": global_output["look_back_requests"],
        "candidates": candidates,
    })
    return LookbackSnapshot(
        topic=str(global_output["topic"]), global_run_id=global_run_id,
        global_input_sha256=global_input_sha,
        requests=tuple(global_output["look_back_requests"]),
        candidates=tuple(candidates), input_sha256=input_sha,
    )


def _load_global(workspace: Path, run_id: str) -> tuple[dict[str, Any], str]:
    _safe_segment(run_id)
    run_dir = workspace / HIERARCHICAL_GLOBAL_ROOT / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json")
    raw = _read_json(run_dir / "output" / "hierarchical_global_landscape.json")
    if manifest.get("schema_version") != HIERARCHICAL_GLOBAL_RUN_SCHEMA_VERSION or manifest.get("status") != "completed":
        raise AnalysisInputError("全局Landscape运行不可用。")
    snapshot = create_hierarchical_global_snapshot(
        workspace, local_batch_run_id=str(manifest["local_batch_run_id"])
    )
    config = HierarchicalGlobalConfig(**manifest["global_config"])
    schema = build_hierarchical_global_schema(
        topic=snapshot.topic, review_goal=snapshot.review_goal,
        clusters=list(snapshot.clusters), config=config,
    )
    replay_input = _strip_global_ids(raw)
    replay_input["schema_version"] = GLOBAL_LANDSCAPE_SCHEMA_VERSION
    replay = validate_hierarchical_global_landscape(
        replay_input, schema=schema, clusters=list(snapshot.clusters)
    )
    if _strip_global_ids(replay) != replay_input or snapshot.input_sha256 != manifest.get("input_sha256"):
        raise AnalysisInputError("全局Landscape无法严格重放。")
    return replay, snapshot.input_sha256


def load_lookback_config(path: Path) -> LookbackConfig:
    value = _read_json(path)
    if set(value) != set(LookbackConfig.__dataclass_fields__):
        raise AnalysisInputError("回看配置字段不匹配。")
    config = LookbackConfig(**value)
    if config.schema_version != LOOKBACK_CONFIG_SCHEMA_VERSION:
        raise AnalysisInputError("回看配置版本无效。")
    if any(type(getattr(config, name)) is not int or getattr(config, name) < 1 for name in config.__dataclass_fields__ if name != "schema_version"):
        raise AnalysisInputError("回看配置数值必须是正整数。")
    return config


def build_lookback_schema(snapshot: LookbackSnapshot, config: LookbackConfig) -> dict[str, Any]:
    request_schemas = []
    for request in snapshot.requests:
        desired_levels = set(request["desired_source_levels"])
        record_ids = [
            str(candidate["record_id"])
            for candidate in snapshot.candidates
            if candidate["paper_relevance"] in desired_levels
        ]
        request_schemas.append({
            "type": "object", "additionalProperties": False,
            "required": ["look_back_request_id", "candidates", "no_match_reason"],
            "allOf": [
                {
                    "if": {"properties": {"candidates": {"minItems": 1}}},
                    "then": {"properties": {"no_match_reason": {"type": "null"}}},
                    "else": {"properties": {"no_match_reason": {"type": "string", "minLength": 1, "maxLength": 800}}},
                }
            ],
            "properties": {
                "look_back_request_id": {"const": str(request["look_back_request_id"])},
                "candidates": {
                    "type": "array", "maxItems": config.max_candidates_per_request,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["record_id", "matched_reason", "expected_use", "confidence"],
                        "properties": {
                            "record_id": {"type": "string", "enum": record_ids},
                            "matched_reason": {"type": "string", "minLength": 1, "maxLength": 800},
                            "expected_use": {"type": "string", "minLength": 1, "maxLength": 800},
                            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        },
                    },
                },
                "no_match_reason": {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 800}, {"type": "null"}]},
            },
        })
    request_count = len(request_schemas)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "topic", "request_results"],
        "properties": {
            "schema_version": {"const": LOOKBACK_OUTPUT_SCHEMA_VERSION},
            "topic": {"const": snapshot.topic},
            "request_results": {
                "type": "array", "minItems": request_count, "maxItems": request_count,
                "prefixItems": request_schemas,
                "items": False,
            },
        },
    }


def validate_lookback(payload: object, *, schema: dict[str, Any], snapshot: LookbackSnapshot, config: LookbackConfig) -> dict[str, Any]:
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        raise AnalysisInputError(f"回看输出Schema无效：{errors[0].message}")
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    request_by_id = {str(row["look_back_request_id"]): row for row in snapshot.requests}
    candidate_by_id = {str(row["record_id"]): row for row in snapshot.candidates}
    result_ids = [str(row["look_back_request_id"]) for row in value["request_results"]]
    if len(result_ids) != len(set(result_ids)) or set(result_ids) != set(request_by_id):
        raise AnalysisInputError("回看请求结果必须逐项且仅一次覆盖全部请求。")
    memberships: dict[str, int] = {}
    for result in value["request_results"]:
        candidates = result["candidates"]
        if bool(candidates) == bool(result["no_match_reason"]):
            raise AnalysisInputError("有候选时no_match_reason必须为空，无候选时必须说明原因。")
        desired = set(request_by_id[result["look_back_request_id"]]["desired_source_levels"])
        record_ids = [str(row["record_id"]) for row in candidates]
        if len(record_ids) != len(set(record_ids)):
            raise AnalysisInputError("同一回看请求不得重复候选论文。")
        for candidate in candidates:
            record_id = str(candidate["record_id"])
            if candidate_by_id[record_id]["paper_relevance"] not in desired:
                raise AnalysisInputError("候选论文相关性等级不符合回看请求。")
            memberships[record_id] = memberships.get(record_id, 0) + 1
    overflow = {key: count for key, count in memberships.items() if count > config.max_requests_per_candidate}
    if overflow:
        raise AnalysisInputError(f"候选论文覆盖请求数超过上限：{overflow}")
    for result in value["request_results"]:
        result["request_result_id"] = _stable_id("lookback_result", result)
        for candidate in result["candidates"]:
            source = candidate_by_id[str(candidate["record_id"])]
            candidate.update({
                "paper_id": source["paper_id"], "paper_title": source["paper_title"],
                "screening_run_id": source["screening_run_id"],
                "paper_relevance": source["paper_relevance"],
            })
            candidate["candidate_assignment_id"] = _stable_id("candidate_assignment", {
                "request_id": result["look_back_request_id"], "record_id": candidate["record_id"],
                "reason": candidate["matched_reason"],
            })
    value["lookback_id"] = _stable_id("lookback", value)
    return value


def build_lookback_prompt(snapshot: LookbackSnapshot, *, schema: dict[str, Any], config: LookbackConfig) -> str:
    return json.dumps({
        "任务": "为全局Landscape回看请求匹配候选论文",
        "综述主题": snapshot.topic,
        "回看请求": list(snapshot.requests),
        "候选论文筛选投影": list(snapshot.candidates),
        "限制": {
            "每请求最多候选": config.max_candidates_per_request,
            "每论文最多覆盖请求": config.max_requests_per_candidate,
            "无合适候选允许空数组": True,
        },
        "输出JSONSchema": schema,
    }, ensure_ascii=False, separators=(",", ":"))


def _strip_global_ids(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value, ensure_ascii=False))
    result.pop("global_landscape_id", None)
    for row in result.get("global_dimensions", []):
        row.pop("global_dimension_id", None)
        row.pop("cluster_ids", None)
        row.pop("paper_ids", None)
    for row in result.get("cross_cluster_relations", []): row.pop("global_relation_id", None)
    for row in result.get("cross_cluster_relations", []):
        row["relation_pair_id"] = f"{row['from_cluster_id']}::{row['to_cluster_id']}"
        row.pop("from_cluster_id", None)
        row.pop("to_cluster_id", None)
    for row in result.get("look_back_requests", []): row.pop("look_back_request_id", None)
    return result


def _summary(output: dict[str, Any]) -> dict[str, int]:
    assignments = [candidate for result in output["request_results"] for candidate in result["candidates"]]
    return {
        "request_count": len(output["request_results"]),
        "matched_request_count": sum(bool(row["candidates"]) for row in output["request_results"]),
        "unmatched_request_count": sum(not row["candidates"] for row in output["request_results"]),
        "candidate_assignment_count": len(assignments),
        "unique_candidate_count": len({row["record_id"] for row in assignments}),
    }


def _render(output: dict[str, Any], summary: dict[str, int]) -> str:
    lines = ["# 层级综合自动回看候选", "", f"- 请求：{summary['request_count']}", f"- 已匹配：{summary['matched_request_count']}", f"- 候选论文：{summary['unique_candidate_count']}", ""]
    for result in output["request_results"]:
        lines.extend([f"## {result['look_back_request_id']}", ""])
        if not result["candidates"]:
            lines.extend([f"无匹配：{result['no_match_reason']}", ""])
        for row in result["candidates"]:
            lines.extend([f"- **{row['paper_title']}**（{row['paper_relevance']}，{row['confidence']}）", f"  - 匹配：{row['matched_reason']}", f"  - 用途：{row['expected_use']}"])
        lines.append("")
    return "\n".join(lines)


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
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _sha256_json(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()


def _stable_id(prefix: str, value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + "_" + hashlib.sha256(serialized.encode()).hexdigest()[:20]


def _now() -> str:
    return datetime.now(UTC).isoformat()
