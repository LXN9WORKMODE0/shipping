from __future__ import annotations

import hashlib
import copy
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from .hierarchical_global_contracts import (
    HierarchicalGlobalConfig,
    build_hierarchical_global_schema,
    load_hierarchical_global_config,
    validate_hierarchical_global_landscape,
)
from .hierarchical_landscape import (
    HIERARCHICAL_LANDSCAPE_ROOT,
    HIERARCHICAL_LOCAL_BATCH_RUN_SCHEMA_VERSION,
)
from .llm_analysis import DEFAULT_MODEL_PROFILE, DEFAULT_TOKENIZER_CACHE, AnalysisInputError
from .llm_json_stage import execute_json_stage
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .research_landscape import (
    RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION,
    create_research_landscape_snapshot,
)
from .research_landscape_contracts import (
    ResearchLandscapeConfig,
    build_research_landscape_schema,
    validate_research_landscape,
)


HIERARCHICAL_GLOBAL_ROOT = "_hierarchical_global_landscapes"
HIERARCHICAL_GLOBAL_RUN_SCHEMA_VERSION = "llm.hierarchical_global_landscape_run.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HIERARCHICAL_GLOBAL_CONFIG = PROJECT_ROOT / "config" / "hierarchical-global-landscape-default.json"

GLOBAL_SYSTEM_PROMPT = """你是层级文献综合的全局归并器。输入只包含多个已严格验证的局部Research Landscape投影，不包含完整论文、Card或Paper Understanding。
只输出符合JSON Schema的对象，不输出Markdown或额外字段。你的任务是把局部维度归并为全局维度，描述局部主题簇之间由真实局部维度直接支持的关系，并把证据不足的问题记录为回看请求。
每个局部dimension必须且只能在local_dimension_accounting中声明mapped及其global_dimension_index，或unmapped及原因。账本按输入完整性清单固定顺序输出，是唯一分配来源；global_dimensions只填写序号、标题和问题，不得输出local_dimension_ids、cluster_ids或paper_ids，这些字段以及unmapped_local_dimensions均由程序生成。
cross_cluster_relations的supporting_local_dimension_ids必须恰好引用两个不同局部维度，且两个维度必须来自不同主题簇；from_cluster_id和to_cluster_id由程序根据维度所有权生成。不得根据主题相邻自行补出因果、继承、整合成效或共同验证。局部gap只是当前语料缺口，不是已证实结论；需要补充论文时写入look_back_requests。
不得生成任何机器ID，ID由程序生成。"""

GLOBAL_RELATION_SYSTEM_PROMPT = """你是层级文献综合的跨主题关系审查器。输入是固定顺序的主题簇对及其局部维度短别名。
只输出符合JSON Schema的对象，不输出Markdown或额外字段。每个已选簇对必须返回一条关系。
关系只能从左侧选择一个维度、从右侧选择一个维度。statement必须直接写出两个研究主题或研究内容，不得使用“前者、后者、两者、二者”等指代词；只能保守描述两条局部维度已经明确表达的互补、趋同、范围差异、对照或方法替代，不得出现D0001等短别名，不得推演因果、继承、促进或整合效果。"""

GLOBAL_RELATION_SELECTION_SYSTEM_PROMPT = """你是层级文献综合的跨主题关系筛选器。只输出符合JSON Schema的对象。
从全部主题簇对中只选择对综述结构最有价值、且有直接局部维度支持的少数簇对；数量不得超过Schema的maxItems。主题相邻、都涉及效率或可以想象联合使用，不足以构成关系。没有足够强的簇对时允许输出空数组。"""


@dataclass(frozen=True)
class HierarchicalGlobalSnapshot:
    topic: str
    review_goal: str
    local_batch_run_id: str
    local_batch_sha256: str
    clusters: tuple[dict[str, Any], ...]
    input_sha256: str


class HierarchicalGlobalLandscapeRunner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        analysis_client=None,
        token_counter=None,
        snapshot_loader=None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.snapshot_loader = snapshot_loader or create_hierarchical_global_snapshot

    def run(
        self,
        *,
        local_batch_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        global_config_path: str | Path = DEFAULT_HIERARCHICAL_GLOBAL_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / HIERARCHICAL_GLOBAL_ROOT / "runs" / resolved
        if run_dir.exists():
            raise AnalysisInputError(f"全局Landscape run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot = profile = config = stage_result = relation_selection_stage_result = relation_stage_result = output = None
        try:
            snapshot = self.snapshot_loader(
                self.workspace, local_batch_run_id=local_batch_run_id
            )
            profile = load_model_profile(Path(model_profile_path))
            config = load_hierarchical_global_config(Path(global_config_path))
            if provider != profile.provider:
                raise AnalysisInputError("provider与model profile不一致。")
            validation_schema = build_hierarchical_global_schema(
                topic=snapshot.topic,
                review_goal=snapshot.review_goal,
                clusters=list(snapshot.clusters),
                config=config,
            )
            dimension_aliases = {
                str(dimension["dimension_id"]): f"D{index:04d}"
                for index, dimension in enumerate(
                    (
                        dimension
                        for cluster in snapshot.clusters
                        for dimension in cluster["landscape"]["dimensions"]
                    ),
                    start=1,
                )
            }
            alias_to_dimension = {
                alias: dimension_id for dimension_id, alias in dimension_aliases.items()
            }
            alias_snapshot = _alias_dimension_snapshot(snapshot, dimension_aliases)
            generation_schema = build_hierarchical_global_schema(
                topic=alias_snapshot.topic,
                review_goal=alias_snapshot.review_goal,
                clusters=list(alias_snapshot.clusters),
                config=config,
            )
            generation_schema["properties"]["cross_cluster_relations"] = {
                "type": "array", "maxItems": 0,
            }
            prompt = build_hierarchical_global_prompt(alias_snapshot, schema=generation_schema)
            _write_json(run_dir / "input" / "local_projection.json", _projection(alias_snapshot))
            _write_json(run_dir / "input" / "output_schema.json", generation_schema)
            _write_json(run_dir / "input" / "validation_schema.json", validation_schema)
            _write_json(run_dir / "input" / "local_dimension_aliases.json", {
                "alias_to_local_dimension_id": alias_to_dimension,
                "local_dimension_id_to_alias": dimension_aliases,
            })
            _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
            _write_json(run_dir / "input" / "global_config.json", config.to_dict())
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url, api_key_env=api_key_env,
                model=profile.request_model, timeout=timeout,
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="global_landscape",
                task_name="hierarchical_global_landscape",
                directory_name="global_landscape",
                system_prompt=GLOBAL_SYSTEM_PROMPT,
                user_prompt=prompt,
                max_output_tokens=config.global_max_output_tokens,
                context={
                    "local_batch_run_id": local_batch_run_id,
                    "cluster_ids": [row["cluster_id"] for row in snapshot.clusters],
                    "local_dimension_ids": [
                        dimension["dimension_id"]
                        for cluster in snapshot.clusters
                        for dimension in cluster["landscape"]["dimensions"]
                    ],
                    "input_sha256": snapshot.input_sha256,
                },
                client=client, profile=profile, token_counter=counter,
            )
            selection_schema = build_global_relation_selection_schema(
                alias_snapshot, max_relations=config.max_cross_cluster_relations
            )
            _write_json(run_dir / "input" / "relation_selection_output_schema.json", selection_schema)
            selection_parsed, relation_selection_stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="global_relation_selection",
                task_name="hierarchical_global_relation_selection",
                directory_name="global_relation_selection",
                system_prompt=GLOBAL_RELATION_SELECTION_SYSTEM_PROMPT,
                user_prompt=build_global_relation_selection_prompt(alias_snapshot, selection_schema),
                max_output_tokens=config.global_max_output_tokens,
                context={
                    "local_batch_run_id": local_batch_run_id,
                    "cluster_pair_count": len(_cluster_pairs(alias_snapshot)),
                    "max_selected_pairs": config.max_cross_cluster_relations,
                    "input_sha256": snapshot.input_sha256,
                },
                client=client, profile=profile, token_counter=counter,
            )
            selection_parsed = normalize_global_relation_selection(
                selection_parsed, selection_schema
            )
            validate_global_relation_review(selection_parsed, selection_schema, reject_aliases=False)
            selected_pair_ids = [str(value) for value in selection_parsed["selected_cluster_pair_ids"]]
            relation_stage_result = []
            relation_rows = []
            for relation_index, pair_id in enumerate(selected_pair_ids, start=1):
                relation_schema = build_global_relation_review_schema(
                    alias_snapshot, selected_pair_ids=[pair_id]
                )
                _write_json(
                    run_dir / "input" / "relation_schemas" / f"pair_{relation_index:02d}.json",
                    relation_schema,
                )
                relation_parsed, pair_stage_result = execute_json_stage(
                    run_dir=run_dir,
                    stage=f"global_relation_{relation_index:02d}",
                    task_name="hierarchical_global_relations",
                    directory_name=f"global_relations/pair_{relation_index:02d}",
                    system_prompt=GLOBAL_RELATION_SYSTEM_PROMPT,
                    user_prompt=build_global_relation_review_prompt(alias_snapshot, relation_schema),
                    max_output_tokens=config.global_max_output_tokens,
                    context={
                        "local_batch_run_id": local_batch_run_id,
                        "cluster_pair_id": pair_id,
                        "input_sha256": snapshot.input_sha256,
                    },
                    client=client, profile=profile, token_counter=counter,
                )
                relation_parsed = normalize_global_relation_statement(
                    relation_parsed, pair_id=pair_id, snapshot=alias_snapshot
                )
                validate_global_relation_review(relation_parsed, relation_schema)
                relation_rows.extend(_relation_review_to_global(relation_parsed))
                relation_stage_result.append(pair_stage_result)
            parsed["cross_cluster_relations"] = relation_rows
            restored = restore_hierarchical_global_dimension_ids(
                parsed, alias_to_dimension=alias_to_dimension
            )
            output = validate_hierarchical_global_landscape(
                restored, schema=validation_schema, clusters=list(snapshot.clusters)
            )
            coverage = _coverage(snapshot, output)
            _write_json(run_dir / "global_landscape" / "validated_global_landscape.json", output)
            _write_json(run_dir / "audit" / "coverage.json", coverage)
            _write_jsonl(run_dir / "audit" / "look_back_requests.jsonl", output["look_back_requests"])
            _write_json(run_dir / "output" / "hierarchical_global_landscape.json", output)
            _write_text(run_dir / "review" / "hierarchical_global_landscape.md", _render(output, coverage))
            status = "completed"
            failure = None
        except Exception as exc:
            status = "failed"
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "stage": "global_landscape",
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            coverage = None
        manifest = {
            "schema_version": HIERARCHICAL_GLOBAL_RUN_SCHEMA_VERSION,
            "run_id": resolved,
            "status": status,
            "started_at": started_at,
            "finished_at": _now(),
            "local_batch_run_id": local_batch_run_id,
            "input_sha256": snapshot.input_sha256 if snapshot else None,
            "provider": profile.provider if profile else None,
            "model": profile.request_model if profile else None,
            "global_config": config.to_dict() if config else None,
            "stage_result": stage_result,
            "relation_selection_stage_result": relation_selection_stage_result,
            "relation_stage_result": relation_stage_result,
            "global_landscape_id": output.get("global_landscape_id") if output else None,
            "coverage": coverage,
            "failure": failure,
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": status,
            "global_landscape_id": manifest["global_landscape_id"],
            "coverage": coverage,
            "failure": failure,
            "run_dir": str(run_dir),
            "output_path": str(run_dir / "output" / "hierarchical_global_landscape.json") if output else None,
            "review_path": str(run_dir / "review" / "hierarchical_global_landscape.md") if output else None,
        }


def create_hierarchical_global_snapshot(
    workspace: str | Path, *, local_batch_run_id: str
) -> HierarchicalGlobalSnapshot:
    _safe_segment(local_batch_run_id)
    workspace = Path(workspace)
    batch_dir = workspace / HIERARCHICAL_LANDSCAPE_ROOT / "local_runs" / local_batch_run_id
    manifest = _read_json(batch_dir / "manifest.json")
    batch = _read_json(batch_dir / "output" / "local_landscape_batch.json")
    if (
        manifest.get("schema_version") != HIERARCHICAL_LOCAL_BATCH_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != local_batch_run_id
        or manifest.get("status") != "completed"
        or batch.get("run_id") != local_batch_run_id
        or any(row.get("local_status") != "completed" for row in batch.get("records", []))
    ):
        raise AnalysisInputError("局部Landscape批次未完整完成或无法重放。")
    clusters = tuple(_load_local_cluster(workspace, row) for row in batch["records"])
    batch_sha256 = _sha256_json(batch)
    input_sha256 = _sha256_json({
        "batch_sha256": batch_sha256,
        "clusters": [
            {
                "cluster_id": row["cluster_id"],
                "landscape_run_id": row["landscape_run_id"],
                "landscape_sha256": row["landscape_sha256"],
            }
            for row in clusters
        ],
    })
    return HierarchicalGlobalSnapshot(
        topic=str(batch["topic"]), review_goal=str(batch["review_goal"]),
        local_batch_run_id=local_batch_run_id,
        local_batch_sha256=batch_sha256,
        clusters=clusters, input_sha256=input_sha256,
    )


def _load_local_cluster(workspace: Path, record: dict[str, Any]) -> dict[str, Any]:
    run_id = str(record["landscape_run_id"])
    run_dir = workspace / "_research_landscapes" / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json")
    raw = _read_json(run_dir / "output" / "research_landscape.json")
    if manifest.get("schema_version") != RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION or manifest.get("run_id") != run_id or manifest.get("status") != "completed":
        raise AnalysisInputError(f"局部Landscape运行不可用：{run_id}")
    snapshot = create_research_landscape_snapshot(
        workspace, collection_path=Path(str(manifest["collection_path"]))
    )
    config = ResearchLandscapeConfig(**manifest["landscape_config"])
    ownership = {
        contribution["contribution_id"]: source.paper_id
        for source in snapshot.sources
        for contribution in source.understanding["contributions"]
    }
    schema = build_research_landscape_schema(
        topic=snapshot.topic, review_goal=snapshot.review_goal,
        paper_ids=[source.paper_id for source in snapshot.sources],
        contribution_to_paper=ownership,
        corpus_scope=snapshot.corpus_scope, config=config,
    )
    replay_input = _strip_landscape_machine_ids(raw)
    landscape = validate_research_landscape(
        replay_input, schema=schema, contribution_to_paper=ownership
    )
    if landscape != raw:
        raise AnalysisInputError(f"局部Landscape无法由稳定ID规则重放：{run_id}")
    if landscape.get("landscape_id") != record.get("landscape_id"):
        raise AnalysisInputError(f"局部Landscape身份不匹配：{run_id}")
    return {
        "cluster_id": str(record["cluster_id"]),
        "cluster_title": str(record["cluster_title"]),
        "landscape_run_id": run_id,
        "landscape_sha256": _sha256_json(landscape),
        "landscape": landscape,
    }


def _strip_landscape_machine_ids(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value, ensure_ascii=False))
    result.pop("landscape_id", None)
    for collection, field in (
        ("dimensions", "dimension_id"),
        ("relations", "relation_id"),
        ("research_evolution", "evolution_id"),
        ("disagreements", "disagreement_id"),
        ("corpus_gaps", "corpus_gap_id"),
        ("field_gap_candidates", "field_gap_candidate_id"),
    ):
        for row in result.get(collection, []):
            row.pop(field, None)
    return result


def build_hierarchical_global_prompt(snapshot: HierarchicalGlobalSnapshot, *, schema: dict[str, Any]) -> str:
    payload = {
        "任务": "将局部Research Landscape归并为全局研究图谱并生成可审计回看请求",
        "综述主题": snapshot.topic,
        "综述目标": snapshot.review_goal,
        "局部主题图谱": _projection(snapshot)["clusters"],
        "局部维度完整性清单": [
            {
                "local_dimension_id": dimension["dimension_id"],
                "cluster_id": cluster["cluster_id"],
                "title": dimension["title"],
            }
            for cluster in snapshot.clusters
            for dimension in cluster["landscape"]["dimensions"]
        ],
        "输出前逐项核对": [
            "每个局部dimension只进入一个全局dimension或显式未映射",
            "local_dimension_accounting条目数与局部维度完整性清单完全相同且ID不重复",
            "global_dimensions不输出local_dimension_ids；所有分配只在local_dimension_accounting填写",
            "跨簇关系引用两侧真实局部dimension",
            "局部gap只形成全局gap或look_back_request，不写成已有事实",
            "global_gap中能够由补充论文回答的问题必须形成look_back_request，并指定目标主题簇",
            "本阶段cross_cluster_relations必须输出空数组，跨簇关系由独立阶段处理",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _cluster_pairs(snapshot: HierarchicalGlobalSnapshot) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    clusters = list(snapshot.clusters)
    return [
        (left, right)
        for left_index, left in enumerate(clusters)
        for right in clusters[left_index + 1:]
    ]


def build_global_relation_selection_schema(
    snapshot: HierarchicalGlobalSnapshot,
    *,
    max_relations: int,
) -> dict[str, Any]:
    pair_ids = [
        f"{left['cluster_id']}::{right['cluster_id']}"
        for left, right in _cluster_pairs(snapshot)
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "selected_cluster_pair_ids"],
        "properties": {
            "schema_version": {"const": "llm.hierarchical_global_relation_selection.v1"},
            "selected_cluster_pair_ids": {
                "type": "array", "maxItems": max_relations, "uniqueItems": True,
                "items": {"type": "string", "enum": pair_ids},
            },
        },
    }


def build_global_relation_review_schema(
    snapshot: HierarchicalGlobalSnapshot,
    *,
    selected_pair_ids: list[str],
) -> dict[str, Any]:
    items = []
    for left, right in _cluster_pairs(snapshot):
        pair_id = f"{left['cluster_id']}::{right['cluster_id']}"
        if pair_id not in selected_pair_ids:
            continue
        left_ids = [str(row["dimension_id"]) for row in left["landscape"]["dimensions"]]
        right_ids = [str(row["dimension_id"]) for row in right["landscape"]["dimensions"]]
        items.append({
                "type": "object", "additionalProperties": False,
                "required": ["cluster_pair_id", "relation"],
                "properties": {
                    "cluster_pair_id": {"const": pair_id},
                    "relation": {
                                "type": "object", "additionalProperties": False,
                                "required": ["relation_type", "statement", "left_local_dimension_id", "right_local_dimension_id"],
                                "properties": {
                                    "relation_type": {"type": "string", "enum": ["converges", "complements", "contrasts", "scope_difference", "methodological_alternative"]},
                                    "statement": {"type": "string", "minLength": 1, "maxLength": 1000},
                                    "left_local_dimension_id": {"type": "string", "enum": left_ids},
                                    "right_local_dimension_id": {"type": "string", "enum": right_ids},
                                },
                    },
                },
            })
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "relations"],
        "properties": {
            "schema_version": {"const": "llm.hierarchical_global_relations.v1"},
            "relations": {
                "type": "array", "minItems": len(items), "maxItems": len(items),
                "prefixItems": items,
            },
        },
    }


def build_global_relation_review_prompt(
    snapshot: HierarchicalGlobalSnapshot,
    schema: dict[str, Any],
) -> str:
    pair_id = schema["properties"]["relations"]["prefixItems"][0]["properties"]["cluster_pair_id"]["const"]
    selected_cluster_ids = set(pair_id.split("::"))
    selected_clusters = [
        cluster for cluster in _projection(snapshot)["clusters"]
        if cluster["cluster_id"] in selected_cluster_ids
    ]
    payload = {
        "任务": "为一个已选主题簇对生成一条由两条局部维度直接支持的跨簇关系",
        "综述主题": snapshot.topic,
        "cluster_pair_id": pair_id,
        "局部主题图谱": selected_clusters,
        "输出前逐项核对": [
            "relations顺序和数量与Schema的固定簇对完全相同",
            "每个关系只引用左侧和右侧各一个局部维度",
            "statement使用主题和研究内容的自然语言，不出现D0001等短别名",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_global_relation_selection_prompt(
    snapshot: HierarchicalGlobalSnapshot,
    schema: dict[str, Any],
) -> str:
    payload = {
        "任务": "从全部主题簇对中筛选少数具有直接证据和综述价值的跨簇关系候选",
        "综述主题": snapshot.topic,
        "局部主题图谱": _projection(snapshot)["clusters"],
        "候选簇对": schema["properties"]["selected_cluster_pair_ids"]["items"]["enum"],
        "输出JSONSchema": schema,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def normalize_global_relation_selection(
    payload: object,
    schema: dict[str, Any],
) -> object:
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    if not isinstance(normalized, dict) or not isinstance(normalized.get("selected_cluster_pair_ids"), list):
        return normalized
    valid = set(schema["properties"]["selected_cluster_pair_ids"]["items"]["enum"])
    values = []
    for raw in normalized["selected_cluster_pair_ids"]:
        value = str(raw)
        if value not in valid and "::" in value:
            left, right = value.split("::", 1)
            reversed_value = f"{right}::{left}"
            if reversed_value in valid:
                value = reversed_value
        values.append(value)
    normalized["selected_cluster_pair_ids"] = values
    return normalized


def normalize_global_relation_statement(
    payload: object,
    *,
    pair_id: str,
    snapshot: HierarchicalGlobalSnapshot,
) -> object:
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    if not isinstance(normalized, dict) or not isinstance(normalized.get("relations"), list):
        return normalized
    left_id, right_id = pair_id.split("::", 1)
    titles = {
        str(cluster["cluster_id"]): str(cluster["cluster_title"])
        for cluster in snapshot.clusters
    }
    dimensions = {
        str(cluster["cluster_id"]): {
            str(dimension["dimension_id"])
            for dimension in cluster["landscape"]["dimensions"]
        }
        for cluster in snapshot.clusters
    }
    replacements = (
        ("前者", titles[left_id]),
        ("后者", titles[right_id]),
        ("两者", "这两类研究"),
        ("二者", "这两类研究"),
    )
    for row in normalized["relations"]:
        if not isinstance(row, dict) or not isinstance(row.get("relation"), dict):
            continue
        relation = row["relation"]
        left_value = str(relation.get("left_local_dimension_id", ""))
        right_value = str(relation.get("right_local_dimension_id", ""))
        if left_value in dimensions[right_id] and right_value in dimensions[left_id]:
            relation["left_local_dimension_id"] = right_value
            relation["right_local_dimension_id"] = left_value
        statement = str(relation.get("statement", ""))
        for source, target in replacements:
            statement = statement.replace(source, target)
        relation["statement"] = statement
    return normalized


def _relation_review_to_global(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("relations"), list):
        return []
    result = []
    for row in payload["relations"]:
        if not isinstance(row, dict) or row.get("relation") is None:
            continue
        relation = row["relation"]
        result.append({
            "relation_type": relation["relation_type"],
            "statement": relation["statement"],
            "supporting_local_dimension_ids": [
                relation["left_local_dimension_id"],
                relation["right_local_dimension_id"],
            ],
        })
    return result


def validate_global_relation_review(
    payload: object,
    schema: dict[str, Any],
    *,
    reject_aliases: bool = True,
) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        raise AnalysisInputError(f"全局关系审查Schema无效 at {path}: {error.message}")
    if not reject_aliases:
        return
    for index, row in enumerate(payload["relations"]):
        relation = row.get("relation")
        if relation is None:
            continue
        statement = str(relation["statement"])
        if re.search(r"\bD\d{4}\b", statement):
            raise AnalysisInputError(f"全局关系审查statement不得泄露局部维度短别名：relations[{index}]")
        if any(term in statement for term in ("前者", "后者", "两者", "二者")):
            raise AnalysisInputError(f"全局关系审查statement不得使用指代词：relations[{index}]")


def _alias_dimension_snapshot(
    snapshot: HierarchicalGlobalSnapshot,
    aliases: dict[str, str],
) -> HierarchicalGlobalSnapshot:
    clusters = copy.deepcopy(list(snapshot.clusters))
    for cluster in clusters:
        for dimension in cluster["landscape"]["dimensions"]:
            dimension["dimension_id"] = aliases[str(dimension["dimension_id"])]
    return HierarchicalGlobalSnapshot(
        topic=snapshot.topic,
        review_goal=snapshot.review_goal,
        local_batch_run_id=snapshot.local_batch_run_id,
        local_batch_sha256=snapshot.local_batch_sha256,
        clusters=tuple(clusters),
        input_sha256=snapshot.input_sha256,
    )


def restore_hierarchical_global_dimension_ids(
    payload: object,
    *,
    alias_to_dimension: dict[str, str],
) -> object:
    restored = json.loads(json.dumps(payload, ensure_ascii=False))
    if not isinstance(restored, dict):
        return restored
    for row in restored.get("global_dimensions", []):
        if isinstance(row, dict) and isinstance(row.get("local_dimension_ids"), list):
            row["local_dimension_ids"] = [
                alias_to_dimension.get(str(value), str(value))
                for value in row["local_dimension_ids"]
            ]
    for row in restored.get("cross_cluster_relations", []):
        if isinstance(row, dict) and isinstance(row.get("supporting_local_dimension_ids"), list):
            row["supporting_local_dimension_ids"] = [
                alias_to_dimension.get(str(value), str(value))
                for value in row["supporting_local_dimension_ids"]
            ]
    for row in restored.get("unmapped_local_dimensions", []):
        if isinstance(row, dict) and "local_dimension_id" in row:
            value = str(row["local_dimension_id"])
            row["local_dimension_id"] = alias_to_dimension.get(value, value)
    for row in restored.get("local_dimension_accounting", []):
        if isinstance(row, dict) and "local_dimension_id" in row:
            value = str(row["local_dimension_id"])
            row["local_dimension_id"] = alias_to_dimension.get(value, value)
    return restored


def _projection(snapshot: HierarchicalGlobalSnapshot) -> dict[str, Any]:
    return {
        "topic": snapshot.topic,
        "review_goal": snapshot.review_goal,
        "clusters": [
            {
                "cluster_id": row["cluster_id"],
                "cluster_title": row["cluster_title"],
                "landscape_id": row["landscape"]["landscape_id"],
                "central_problem": row["landscape"]["central_problem"],
                "dimensions": row["landscape"]["dimensions"],
                "relations": row["landscape"]["relations"],
                "research_evolution": row["landscape"]["research_evolution"],
                "disagreements": row["landscape"]["disagreements"],
                "corpus_gaps": row["landscape"]["corpus_gaps"],
                "unmapped_papers": row["landscape"]["unmapped_papers"],
            }
            for row in snapshot.clusters
        ],
    }


def _coverage(snapshot: HierarchicalGlobalSnapshot, output: dict[str, Any]) -> dict[str, Any]:
    total = sum(len(row["landscape"]["dimensions"]) for row in snapshot.clusters)
    mapped = sum(len(row["local_dimension_ids"]) for row in output["global_dimensions"])
    return {
        "cluster_count": len(snapshot.clusters),
        "local_dimension_count": total,
        "mapped_local_dimension_count": mapped,
        "unmapped_local_dimension_count": len(output["unmapped_local_dimensions"]),
        "accounted_local_dimension_count": mapped + len(output["unmapped_local_dimensions"]),
        "global_dimension_count": len(output["global_dimensions"]),
        "cross_cluster_relation_count": len(output["cross_cluster_relations"]),
        "look_back_request_count": len(output["look_back_requests"]),
        "coverage_ratio": (mapped + len(output["unmapped_local_dimensions"])) / total if total else 0.0,
    }


def _render(output: dict[str, Any], coverage: dict[str, Any]) -> str:
    lines = [
        "# 层级论文综合全局图谱", "",
        f"- 主题：{output['topic']}",
        f"- 全局维度：{coverage['global_dimension_count']}",
        f"- 局部维度覆盖率：{coverage['coverage_ratio']:.3f}",
        f"- 跨簇关系：{coverage['cross_cluster_relation_count']}",
        f"- 回看请求：{coverage['look_back_request_count']}", "",
        f"## 中心问题\n\n{output['central_problem']}\n", "## 全局维度", "",
    ]
    for row in output["global_dimensions"]:
        lines.extend([f"### {row['global_dimension_index']}. {row['title']}", "", row["question"], ""])
    if output["look_back_requests"]:
        lines.extend(["## 回看请求", ""])
        for row in output["look_back_requests"]:
            lines.append(f"- [{row['priority']}] {row['question']}：{row['reason']}")
        lines.append("")
    return "\n".join(lines)


def _safe_segment(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise AnalysisInputError("运行ID不是安全路径段。")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"无法读取JSON：{path}") from exc
    if not isinstance(value, dict):
        raise AnalysisInputError(f"JSON必须是对象：{path}")
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


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
