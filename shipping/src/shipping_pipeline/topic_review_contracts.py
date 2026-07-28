from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .llm_contracts import extract_numeric_facts


TOPIC_REVIEW_CONFIG_SCHEMA_VERSION = "llm.topic_review_config.v2"
TOPIC_SCOPE_SCHEMA_VERSION = "llm.topic_scope.v1"
TOPIC_BRIEF_SCHEMA_VERSION = "llm.topic_brief.v2"
TOPIC_REVISIT_SCHEMA_VERSION = "llm.topic_revisit.v1"
PAPER_RELEVANCE_LEVELS = ("core", "supporting", "peripheral", "exclude")

_SCOPE_PRIORITIES = ("primary", "secondary")
_SCOPE_INTENDED_USES = (
    "background",
    "method",
    "finding",
    "data",
    "debate",
    "limitation",
)
_BRIEF_POINT_TYPES = (
    "background",
    "definition",
    "method",
    "finding",
    "result",
    "data",
    "mechanism",
    "argument",
    "recommendation",
    "limitation",
)
_BRIEF_USE_TYPES = ("background", "support", "comparison", "method", "counterpoint")
_GAP_TYPES = (
    "background",
    "mechanism",
    "method",
    "effect",
    "comparison",
    "limitation",
    "implementation",
)


class TopicReviewContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class TopicReviewConfig:
    schema_version: str
    max_selected_materials: dict[str, int]
    max_evidence_units_per_material: int
    max_revisit_materials: dict[str, int]
    max_evidence_gaps: int
    max_brief_points: dict[str, int]
    max_review_uses: int
    max_cautions: int
    scope_max_output_tokens: int
    revisit_max_output_tokens: int
    brief_max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "max_selected_materials": dict(self.max_selected_materials),
            "max_evidence_units_per_material": self.max_evidence_units_per_material,
            "max_revisit_materials": dict(self.max_revisit_materials),
            "max_evidence_gaps": self.max_evidence_gaps,
            "max_brief_points": dict(self.max_brief_points),
            "max_review_uses": self.max_review_uses,
            "max_cautions": self.max_cautions,
            "scope_max_output_tokens": self.scope_max_output_tokens,
            "revisit_max_output_tokens": self.revisit_max_output_tokens,
            "brief_max_output_tokens": self.brief_max_output_tokens,
        }


def load_topic_review_config(path: Path) -> TopicReviewConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TopicReviewContractError(
            "config.read_failed",
            f"无法读取主题简报配置：{path}",
        ) from exc
    if not isinstance(payload, dict):
        raise TopicReviewContractError("config.invalid", "主题简报配置必须是 JSON 对象。")
    return parse_topic_review_config(payload)


def parse_topic_review_config(payload: dict[str, Any]) -> TopicReviewConfig:
    expected = {
        "schema_version",
        "max_selected_materials",
        "max_evidence_units_per_material",
        "max_revisit_materials",
        "max_evidence_gaps",
        "max_brief_points",
        "max_review_uses",
        "max_cautions",
        "scope_max_output_tokens",
        "revisit_max_output_tokens",
        "brief_max_output_tokens",
    }
    if set(payload) != expected:
        raise TopicReviewContractError(
            "config.fields_invalid",
            f"配置字段不匹配：missing={sorted(expected - set(payload))}, "
            f"unknown={sorted(set(payload) - expected)}",
        )
    try:
        config = TopicReviewConfig(**payload)
    except TypeError as exc:
        raise TopicReviewContractError("config.invalid", "主题简报配置字段类型错误。") from exc
    _validate_config(config)
    return config


def build_topic_scope_schema(
    material_ids: list[str],
    *,
    config: TopicReviewConfig | None = None,
) -> dict[str, Any]:
    ids = _validated_ids(material_ids, field="material_ids", allow_empty=False)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.topic_scope.v1.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "paper_relevance",
            "relevance_reason",
            "topic_summary",
            "selected_materials",
        ],
        "properties": {
            "schema_version": {"const": TOPIC_SCOPE_SCHEMA_VERSION},
            "paper_relevance": {"type": "string", "enum": list(PAPER_RELEVANCE_LEVELS)},
            "relevance_reason": {"type": "string", "minLength": 1, "maxLength": 480},
            "topic_summary": {"type": "string", "minLength": 1, "maxLength": 480},
            "selected_materials": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "material_id",
                        "priority",
                        "intended_use",
                        "selection_reason",
                    ],
                    "properties": {
                        "material_id": {"type": "string", "enum": ids},
                        "priority": {"type": "string", "enum": list(_SCOPE_PRIORITIES)},
                        "intended_use": {
                            "type": "string",
                            "enum": list(_SCOPE_INTENDED_USES),
                        },
                        "selection_reason": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 240,
                        },
                    },
                },
            },
        },
    }
    if config is not None:
        schema["allOf"] = [
            {
                "if": {
                    "properties": {
                        "paper_relevance": {"const": relevance},
                    },
                    "required": ["paper_relevance"],
                },
                "then": {
                    "properties": {
                        "selected_materials": {
                            "minItems": 0 if relevance == "exclude" else 1,
                            "maxItems": config.max_selected_materials[
                                relevance
                            ],
                        }
                    }
                },
            }
            for relevance in PAPER_RELEVANCE_LEVELS
        ]
    return schema


def validate_topic_scope(
    payload: object,
    material_ids: set[str],
    config: TopicReviewConfig,
) -> dict[str, Any]:
    ids = sorted(_validated_ids(list(material_ids), field="material_ids", allow_empty=False))
    validated = _validate_schema(
        payload,
        build_topic_scope_schema(ids),
        "schema.topic_scope_invalid",
    )
    relevance = str(validated["paper_relevance"])
    selected = list(validated["selected_materials"])
    selected_ids = [str(row["material_id"]) for row in selected]
    if len(selected_ids) != len(set(selected_ids)):
        raise TopicReviewContractError(
            "scope.duplicate_material",
            "主题定界不能重复选择同一张 Card。",
            path="$.selected_materials",
        )
    limit = config.max_selected_materials[relevance]
    if len(selected) > limit:
        raise TopicReviewContractError(
            "scope.selection_limit_exceeded",
            f"paper_relevance={relevance!r} 最多选择 {limit} 张 Card，实际为 {len(selected)}。",
            path="$.selected_materials",
        )
    if relevance == "exclude" and selected:
        raise TopicReviewContractError(
            "scope.exclude_has_selection",
            "exclude 论文不得继续选择 Card。",
            path="$.selected_materials",
        )
    if relevance != "exclude" and not selected:
        raise TopicReviewContractError(
            "scope.selection_missing",
            f"paper_relevance={relevance!r} 必须至少选择一张 Card。",
            path="$.selected_materials",
        )
    return copy.deepcopy(validated)


def build_topic_brief_schema(
    evidence_unit_ids: list[str],
    *,
    relevance: str,
    config: TopicReviewConfig,
    evidence_claims_by_id: dict[str, str] | None = None,
    required_key_point_evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    ids = _validated_ids(evidence_unit_ids, field="evidence_unit_ids", allow_empty=False)
    _validate_relevance(relevance)
    if relevance == "exclude":
        raise ValueError("exclude 论文不生成 Evidence 支撑的论文简报。")
    reference_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["statement", "evidence_unit_ids"],
        "properties": {
            "statement": {"type": "string", "minLength": 1, "maxLength": 480},
            "evidence_unit_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "enum": ids},
            },
        },
    }
    point_item = copy.deepcopy(reference_item)
    point_item["required"].insert(0, "point_type")
    point_item["properties"]["point_type"] = {
        "type": "string",
        "enum": list(_BRIEF_POINT_TYPES),
        "description": "只能使用枚举值；不得输出 fact 或其他近义标签。",
    }
    point_item["properties"]["evidence_unit_ids"]["maxItems"] = 1
    if evidence_claims_by_id is not None:
        if set(evidence_claims_by_id) != set(ids):
            raise ValueError("evidence_claims_by_id 必须完整覆盖 Evidence ID。")
        bound_points: list[dict[str, Any]] = []
        for evidence_id in ids:
            claim = evidence_claims_by_id[evidence_id]
            if not isinstance(claim, str) or not claim.strip():
                raise ValueError("Evidence claim 必须是非空字符串。")
            bound = copy.deepcopy(point_item)
            bound["properties"]["statement"] = {"const": claim}
            bound["properties"]["evidence_unit_ids"] = {
                "const": [evidence_id],
            }
            bound_points.append(bound)
        point_item = {"oneOf": bound_points}
    navigation_item = copy.deepcopy(reference_item)
    use_item = copy.deepcopy(navigation_item)
    use_item["required"].insert(0, "use_type")
    use_item["properties"]["use_type"] = {
        "type": "string",
        "enum": list(_BRIEF_USE_TYPES),
    }
    required_point_ids = _validated_ids(
        list(required_key_point_evidence_ids or []),
        field="required_key_point_evidence_ids",
        allow_empty=True,
    )
    unknown_required = sorted(set(required_point_ids) - set(ids))
    if unknown_required:
        raise ValueError(
            f"required_key_point_evidence_ids 包含未知 ID：{unknown_required}"
        )
    key_points_schema: dict[str, Any] = {
        "type": "array",
        "minItems": 1,
        "maxItems": config.max_brief_points[relevance],
        "items": point_item,
    }
    if required_point_ids:
        key_points_schema["allOf"] = [
            {
                "contains": {
                    "type": "object",
                    "required": ["evidence_unit_ids"],
                    "properties": {
                        "evidence_unit_ids": {"const": [evidence_id]},
                    },
                },
                "minContains": 1,
                "maxContains": 1,
            }
            for evidence_id in required_point_ids
        ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.topic_brief.v2.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "topic_contribution",
            "key_points",
            "review_uses",
            "cautions",
            "evidence_gaps",
        ],
        "properties": {
            "schema_version": {"const": TOPIC_BRIEF_SCHEMA_VERSION},
            "topic_contribution": navigation_item,
            "key_points": key_points_schema,
            "review_uses": {
                "type": "array",
                "minItems": 1,
                "maxItems": config.max_review_uses,
                "items": use_item,
            },
            "cautions": {
                "type": "array",
                "maxItems": config.max_cautions,
                "items": navigation_item,
            },
            "evidence_gaps": {
                "type": "array",
                "maxItems": config.max_evidence_gaps,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["gap_type", "question", "why_it_matters"],
                    "properties": {
                        "gap_type": {
                            "type": "string",
                            "enum": list(_GAP_TYPES),
                        },
                        "question": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 240,
                            "description": (
                                "不得创造 Evidence 中没有的示例数字；"
                                "需要泛化时使用无数字表述。"
                            ),
                        },
                        "why_it_matters": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 240,
                            "description": (
                                "不得创造 Evidence 中没有的具体数字。"
                            ),
                        },
                    },
                },
            },
        },
    }


def build_topic_revisit_schema(
    omitted_material_ids: list[str],
    gap_ids: list[str],
    *,
    max_materials: int,
) -> dict[str, Any]:
    material_ids = _validated_ids(
        omitted_material_ids,
        field="omitted_material_ids",
        allow_empty=False,
    )
    validated_gap_ids = _validated_ids(gap_ids, field="gap_ids", allow_empty=False)
    if type(max_materials) is not int or max_materials < 1:
        raise ValueError("max_materials 必须是正整数。")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.topic_revisit.v1.json",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "status", "review_summary", "selected_materials"],
        "properties": {
            "schema_version": {"const": TOPIC_REVISIT_SCHEMA_VERSION},
            "status": {"type": "string", "enum": ["no_candidate", "selected"]},
            "review_summary": {"type": "string", "minLength": 1, "maxLength": 360},
            "selected_materials": {
                "type": "array",
                "maxItems": max_materials,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "material_id",
                        "gap_ids",
                        "intended_use",
                        "selection_reason",
                        "novelty_reason",
                    ],
                    "properties": {
                        "material_id": {"type": "string", "enum": material_ids},
                        "gap_ids": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": validated_gap_ids},
                        },
                        "intended_use": {
                            "type": "string",
                            "enum": list(_SCOPE_INTENDED_USES),
                        },
                        "selection_reason": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 240,
                        },
                        "novelty_reason": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 240,
                        },
                    },
                },
            },
        },
    }


def validate_topic_revisit(
    payload: object,
    omitted_material_ids: set[str],
    gap_ids: set[str],
    *,
    max_materials: int,
) -> dict[str, Any]:
    validated = _validate_schema(
        payload,
        build_topic_revisit_schema(
            sorted(omitted_material_ids),
            sorted(gap_ids),
            max_materials=max_materials,
        ),
        "schema.topic_revisit_invalid",
    )
    selected = list(validated["selected_materials"])
    selected_ids = [str(row["material_id"]) for row in selected]
    if len(selected_ids) != len(set(selected_ids)):
        raise TopicReviewContractError(
            "revisit.duplicate_material",
            "自动回查不能重复选择同一张 Card。",
            path="$.selected_materials",
        )
    status = str(validated["status"])
    if status == "no_candidate" and selected:
        raise TopicReviewContractError(
            "revisit.no_candidate_has_selection",
            "no_candidate 状态不得选择 Card。",
            path="$.selected_materials",
        )
    if status == "selected" and not selected:
        raise TopicReviewContractError(
            "revisit.selection_missing",
            "selected 状态必须至少选择一张 Card。",
            path="$.selected_materials",
        )
    return copy.deepcopy(validated)


def validate_topic_brief(
    payload: object,
    evidence_units: list[dict[str, Any]],
    *,
    relevance: str,
    config: TopicReviewConfig,
    required_key_point_evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    evidence_by_id = {
        str(row.get("evidence_unit_id", "")): row
        for row in evidence_units
        if str(row.get("evidence_unit_id", ""))
    }
    if len(evidence_by_id) != len(evidence_units):
        raise TopicReviewContractError(
            "input.evidence_ids_invalid",
            "Evidence ID 为空或重复。",
        )
    validated = _validate_schema(
        payload,
        build_topic_brief_schema(
            list(evidence_by_id),
            relevance=relevance,
            config=config,
            evidence_claims_by_id={
                evidence_id: str(row.get("claim", ""))
                for evidence_id, row in evidence_by_id.items()
            },
            required_key_point_evidence_ids=required_key_point_evidence_ids,
        ),
        "schema.topic_brief_invalid",
    )
    used_point_evidence_ids: set[str] = set()
    for index, item in enumerate(validated["key_points"]):
        evidence_id = str(item["evidence_unit_ids"][0])
        if evidence_id in used_point_evidence_ids:
            raise TopicReviewContractError(
                "brief.duplicate_key_point_evidence",
                "同一 Evidence 不能重复生成多个主要观点。",
                path=f"$.key_points[{index}].evidence_unit_ids",
            )
        used_point_evidence_ids.add(evidence_id)
        claim = " ".join(str(evidence_by_id[evidence_id].get("claim", "")).split())
        statement = " ".join(str(item["statement"]).split())
        if statement != claim:
            raise TopicReviewContractError(
                "brief.key_point_not_evidence_claim",
                "主要观点必须原样采用其唯一 Evidence claim，不得自由改写或组合。",
                path=f"$.key_points[{index}].statement",
            )
    seen_gaps: set[tuple[str, str]] = set()
    all_evidence_support = "\n".join(
        [
            str(unit.get("claim", ""))
            + "\n"
            + "\n".join(
                str(citation.get("quote", ""))
                for citation in unit.get("citations", [])
            )
            for unit in evidence_units
        ]
    )
    all_evidence_numbers = extract_numeric_facts(all_evidence_support)
    for index, item in enumerate(validated["evidence_gaps"]):
        identity = (
            str(item["gap_type"]),
            " ".join(str(item["question"]).split()),
        )
        if identity in seen_gaps:
            raise TopicReviewContractError(
                "brief.duplicate_evidence_gap",
                "同一证据缺口不能重复出现。",
                path=f"$.evidence_gaps[{index}]",
            )
        seen_gaps.add(identity)
        for field in ("question", "why_it_matters"):
            unsupported = sorted(
                extract_numeric_facts(str(item[field])) - all_evidence_numbers
            )
            if unsupported:
                raise TopicReviewContractError(
                    "brief.gap_numeric_fact_unsupported",
                    "证据缺口中的数值没有出现在本篇已验证 Evidence 中："
                    f"{unsupported}",
                    path=f"$.evidence_gaps[{index}].{field}",
                )
    for path, item in _iter_brief_items(validated):
        support_parts: list[str] = []
        for evidence_id in item["evidence_unit_ids"]:
            unit = evidence_by_id[str(evidence_id)]
            support_parts.append(str(unit.get("claim", "")))
            support_parts.extend(
                str(citation.get("quote", ""))
                for citation in unit.get("citations", [])
            )
        unsupported = sorted(
            extract_numeric_facts(str(item["statement"]))
            - extract_numeric_facts("\n".join(support_parts))
        )
        if unsupported:
            raise TopicReviewContractError(
                "brief.numeric_fact_unsupported",
                f"简报陈述中的数值没有出现在所引 Evidence 中：{unsupported}",
                path=f"{path}.statement",
            )
    return copy.deepcopy(validated)


def _validate_config(config: TopicReviewConfig) -> None:
    if config.schema_version != TOPIC_REVIEW_CONFIG_SCHEMA_VERSION:
        raise TopicReviewContractError(
            "config.version_invalid",
            f"不支持的配置版本：{config.schema_version!r}",
        )
    if set(config.max_selected_materials) != set(PAPER_RELEVANCE_LEVELS):
        raise TopicReviewContractError(
            "config.depth_keys_invalid",
            "max_selected_materials 必须完整覆盖四种相关性。",
        )
    if set(config.max_revisit_materials) != set(PAPER_RELEVANCE_LEVELS):
        raise TopicReviewContractError(
            "config.depth_keys_invalid",
            "max_revisit_materials 必须完整覆盖四种相关性。",
        )
    if set(config.max_brief_points) != {"core", "supporting", "peripheral"}:
        raise TopicReviewContractError(
            "config.depth_keys_invalid",
            "max_brief_points 必须覆盖 core/supporting/peripheral。",
        )
    if config.max_selected_materials.get("exclude") != 0:
        raise TopicReviewContractError(
            "config.exclude_limit_invalid",
            "exclude 的 Card 上限必须为0。",
        )
    if config.max_revisit_materials.get("exclude") != 0:
        raise TopicReviewContractError(
            "config.exclude_limit_invalid",
            "exclude 的回查 Card 上限必须为0。",
        )
    revisit_values = list(config.max_revisit_materials.values())
    if any(type(value) is not int or value < 0 for value in revisit_values):
        raise TopicReviewContractError(
            "config.value_invalid",
            "回查 Card 上限必须是非负整数。",
        )
    for relevance in ("core", "supporting", "peripheral"):
        if config.max_revisit_materials[relevance] > config.max_brief_points[relevance]:
            raise TopicReviewContractError(
                "config.revisit_limit_invalid",
                f"{relevance} 的回查 Card 上限不能超过最终观点上限。",
            )
    positive_values = [
        *(config.max_selected_materials[level] for level in PAPER_RELEVANCE_LEVELS[:-1]),
        *config.max_brief_points.values(),
        config.max_evidence_units_per_material,
        config.max_evidence_gaps,
        config.max_review_uses,
        config.max_cautions,
        config.scope_max_output_tokens,
        config.revisit_max_output_tokens,
        config.brief_max_output_tokens,
    ]
    if any(type(value) is not int or value < 1 for value in positive_values):
        raise TopicReviewContractError(
            "config.value_invalid",
            "除 exclude 上限外，所有配置数值必须是正整数。",
        )


def _validate_relevance(relevance: str) -> None:
    if relevance not in PAPER_RELEVANCE_LEVELS:
        raise ValueError(f"不支持的 paper_relevance：{relevance!r}")


def _validated_ids(values: list[str], *, field: str, allow_empty: bool) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field} 必须是列表。")
    if not allow_empty and not values:
        raise ValueError(f"{field} 不能为空。")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{field} 必须只包含非空字符串。")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} 必须唯一。")
    return list(values)


def _validate_schema(payload: object, schema: dict[str, Any], code: str) -> dict[str, Any]:
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda row: list(row.path))
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.path
        )
        raise TopicReviewContractError(code, error.message, path=path)
    if not isinstance(payload, dict):
        raise TopicReviewContractError(code, "输出必须是 JSON 对象。")
    return payload


def _iter_brief_items(payload: dict[str, Any]):
    yield "$.topic_contribution", payload["topic_contribution"]
    for field in ("key_points", "review_uses", "cautions"):
        for index, item in enumerate(payload[field]):
            yield f"$.{field}[{index}]", item
