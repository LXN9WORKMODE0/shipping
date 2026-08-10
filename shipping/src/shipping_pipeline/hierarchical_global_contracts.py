from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


GLOBAL_CONFIG_SCHEMA_VERSION = "llm.hierarchical_global_landscape_config.v1"
GLOBAL_LANDSCAPE_SCHEMA_VERSION = "llm.hierarchical_global_landscape.v7"
GLOBAL_RELATION_TYPES = (
    "converges",
    "complements",
    "scope_difference",
    "methodological_alternative",
)


class HierarchicalGlobalContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class HierarchicalGlobalConfig:
    schema_version: str
    max_global_dimensions: int
    max_cross_cluster_relations: int
    max_global_gaps: int
    max_look_back_requests: int
    max_local_dimensions_per_global_dimension: int
    global_max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def load_hierarchical_global_config(path: Path) -> HierarchicalGlobalConfig:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HierarchicalGlobalContractError(
            "hierarchical_global.config_invalid", f"无法读取配置：{path}"
        ) from exc
    if not isinstance(value, dict) or set(value) != set(HierarchicalGlobalConfig.__dataclass_fields__):
        raise HierarchicalGlobalContractError(
            "hierarchical_global.config_fields_invalid", "全局归并配置字段不匹配。"
        )
    config = HierarchicalGlobalConfig(**value)
    if config.schema_version != GLOBAL_CONFIG_SCHEMA_VERSION:
        raise HierarchicalGlobalContractError(
            "hierarchical_global.config_schema_invalid", "全局归并配置版本不受支持。"
        )
    for name in config.__dataclass_fields__:
        if name == "schema_version":
            continue
        if type(getattr(config, name)) is not int or getattr(config, name) < 1:
            raise HierarchicalGlobalContractError(
                "hierarchical_global.config_value_invalid", f"{name}必须是正整数。"
            )
    return config


def build_hierarchical_global_schema(
    *,
    topic: str,
    review_goal: str,
    clusters: list[dict[str, Any]],
    config: HierarchicalGlobalConfig,
) -> dict[str, Any]:
    cluster_ids = [str(row["cluster_id"]) for row in clusters]
    dimension_owner = {
        str(dimension["dimension_id"]): str(cluster["cluster_id"])
        for cluster in clusters
        for dimension in cluster["landscape"]["dimensions"]
    }
    local_dimension_ids = list(dimension_owner)
    text = {"type": "string", "minLength": 1, "maxLength": 1000}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "topic", "review_goal", "central_problem",
            "global_dimensions", "cross_cluster_relations", "global_gaps",
            "unmapped_local_dimensions", "local_dimension_accounting",
            "look_back_requests",
        ],
        "properties": {
            "schema_version": {"const": GLOBAL_LANDSCAPE_SCHEMA_VERSION},
            "topic": {"const": topic},
            "review_goal": {"const": review_goal},
            "central_problem": text,
            "global_dimensions": {
                "type": "array", "minItems": 1,
                "maxItems": config.max_global_dimensions,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["global_dimension_index", "title", "question"],
                    "properties": {
                        "global_dimension_index": {"type": "integer", "minimum": 1, "maximum": config.max_global_dimensions},
                        "title": {"type": "string", "minLength": 1, "maxLength": 160},
                        "question": text,
                    },
                },
            },
            "cross_cluster_relations": {
                "type": "array", "maxItems": config.max_cross_cluster_relations,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["relation_type", "statement", "supporting_local_dimension_ids"],
                    "properties": {
                        "relation_type": {"type": "string", "enum": list(GLOBAL_RELATION_TYPES)},
                        "statement": text,
                        "supporting_local_dimension_ids": {
                            "type": "array", "minItems": 2, "maxItems": 2,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": local_dimension_ids},
                        },
                    },
                },
            },
            "global_gaps": {
                "type": "array", "maxItems": config.max_global_gaps,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["question", "why_it_matters", "related_global_dimension_indexes"],
                    "properties": {
                        "question": text, "why_it_matters": text,
                        "related_global_dimension_indexes": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 1, "maximum": config.max_global_dimensions}},
                    },
                },
            },
            "unmapped_local_dimensions": {
                "type": "array", "maxItems": 0,
            },
            "local_dimension_accounting": {
                "type": "array",
                "minItems": len(local_dimension_ids),
                "maxItems": len(local_dimension_ids),
                "prefixItems": [
                    {
                        "type": "object", "additionalProperties": False,
                        "required": ["local_dimension_id", "disposition", "global_dimension_index", "reason"],
                        "properties": {
                            "local_dimension_id": {"const": dimension_id},
                            "disposition": {"type": "string", "enum": ["mapped", "unmapped"]},
                            "global_dimension_index": {
                                "anyOf": [
                                    {"type": "integer", "minimum": 1, "maximum": config.max_global_dimensions},
                                    {"type": "null"},
                                ]
                            },
                            "reason": {"anyOf": [text, {"type": "null"}]},
                        },
                        "allOf": [
                            {
                                "if": {"properties": {"disposition": {"const": "mapped"}}},
                                "then": {"properties": {"global_dimension_index": {"type": "integer"}}},
                            },
                            {
                                "if": {"properties": {"disposition": {"const": "unmapped"}}},
                                "then": {"properties": {
                                    "global_dimension_index": {"type": "null"},
                                    "reason": text,
                                }},
                            },
                        ],
                    }
                    for dimension_id in local_dimension_ids
                ],
            },
            "look_back_requests": {
                "type": "array", "maxItems": config.max_look_back_requests,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["question", "reason", "target_cluster_ids", "desired_source_levels", "priority"],
                    "properties": {
                        "question": text, "reason": text,
                        "target_cluster_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "enum": cluster_ids}},
                        "desired_source_levels": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "enum": ["core", "supporting", "peripheral"]}},
                        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                },
            },
        },
    }
    return schema


def validate_hierarchical_global_landscape(
    payload: object,
    *,
    schema: dict[str, Any],
    clusters: list[dict[str, Any]],
) -> dict[str, Any]:
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        path = "$" + "".join(f"[{value}]" if isinstance(value, int) else f".{value}" for value in error.absolute_path)
        raise HierarchicalGlobalContractError(
            "hierarchical_global.schema_invalid", error.message, path=path
        )
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    cluster_ids = [str(row["cluster_id"]) for row in clusters]
    dimension_owner = {
        str(dimension["dimension_id"]): str(cluster["cluster_id"])
        for cluster in clusters
        for dimension in cluster["landscape"]["dimensions"]
    }
    dimension_papers = {
        str(dimension["dimension_id"]): {str(item) for item in dimension["paper_ids"]}
        for cluster in clusters
        for dimension in cluster["landscape"]["dimensions"]
    }
    indexes = [row["global_dimension_index"] for row in value["global_dimensions"]]
    if indexes != list(range(1, len(indexes) + 1)):
        raise HierarchicalGlobalContractError(
            "hierarchical_global.dimension_index_invalid", "全局维度序号必须从1连续排列。"
        )
    accounting = value["local_dimension_accounting"]
    dimensions_by_index = {int(row["global_dimension_index"]): row for row in value["global_dimensions"]}
    assigned: dict[int, list[str]] = {index: [] for index in dimensions_by_index}
    unmapped_rows = []
    for row in accounting:
        dimension_id = str(row["local_dimension_id"])
        if row["disposition"] == "mapped":
            target = int(row["global_dimension_index"])
            if target not in dimensions_by_index:
                raise HierarchicalGlobalContractError(
                    "hierarchical_global.accounting_target_unknown", f"账本引用不存在的全局维度：{target}"
                )
            assigned[target].append(dimension_id)
        else:
            unmapped_rows.append({"local_dimension_id": dimension_id, "reason": row["reason"]})
    for index, row in dimensions_by_index.items():
        refs = assigned[index]
        if not refs:
            raise HierarchicalGlobalContractError(
                "hierarchical_global.dimension_empty", f"全局维度没有局部维度支持：{index}"
            )
        row["local_dimension_ids"] = refs
        row["cluster_ids"] = sorted({dimension_owner[item] for item in refs})
        row["paper_ids"] = sorted(set().union(*(dimension_papers[item] for item in refs)))
    value["unmapped_local_dimensions"] = unmapped_rows
    for row in value["cross_cluster_relations"]:
        owners = {dimension_owner[str(item)] for item in row["supporting_local_dimension_ids"]}
        if len(owners) != 2:
            raise HierarchicalGlobalContractError(
                "hierarchical_global.relation_support_invalid", "跨簇关系的局部维度必须且只能来自两个主题簇。"
            )
        row["from_cluster_id"], row["to_cluster_id"] = sorted(owners)
    result = value
    result["global_landscape_id"] = _stable_id("global_landscape", value)
    for row in result["global_dimensions"]:
        row["global_dimension_id"] = _stable_id("global_dimension", row)
    for row in result["cross_cluster_relations"]:
        row["global_relation_id"] = _stable_id("global_relation", row)
    for row in result["look_back_requests"]:
        row["look_back_request_id"] = _stable_id("look_back", row)
    return result


def _stable_id(prefix: str, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
