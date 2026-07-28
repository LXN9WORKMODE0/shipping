from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


CONFIG_SCHEMA_VERSION = "llm.topic_synthesis_config.v2"
COLLECTION_SCHEMA_VERSION = "llm.topic_synthesis_collection.v1"
EVIDENCE_MAP_SCHEMA_VERSION = "llm.topic_evidence_map.v2"
OUTLINE_SCHEMA_VERSION = "llm.topic_review_outline.v2"

THEME_RELATION_TYPES = (
    "convergent",
    "complementary",
    "divergent",
    "mixed",
    "single_source",
)
EVIDENCE_ROLES = (
    "main_support",
    "context",
    "qualification",
    "contrast",
)
UNASSIGNED_REASON_CODES = (
    "redundant",
    "too_narrow",
    "weak_connection",
    "quality_concern",
)
GAP_TYPES = (
    "background",
    "mechanism",
    "method",
    "effect",
    "comparison",
    "limitation",
    "implementation",
)
SYNTHESIS_UNIT_TYPES = (
    "consensus",
    "complement",
    "contrast",
    "causal_chain",
    "single_source_context",
    "corpus_gap",
)
class TopicSynthesisContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class TopicSynthesisConfig:
    schema_version: str
    max_themes: int
    max_themes_per_evidence: int
    max_synthesis_units: int
    max_sections: int
    max_paragraphs_per_section: int
    max_evidence_per_synthesis_unit: int
    max_search_directions: int
    theme_map_output_base_tokens: int
    theme_map_output_tokens_per_evidence: int
    theme_map_max_output_tokens: int
    outline_output_base_tokens: int
    outline_output_tokens_per_theme: int
    outline_max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }

    def theme_map_output_tokens(self, evidence_count: int) -> int:
        if type(evidence_count) is not int or evidence_count < 1:
            raise ValueError("evidence_count 必须是正整数。")
        return min(
            self.theme_map_output_base_tokens
            + evidence_count * self.theme_map_output_tokens_per_evidence,
            self.theme_map_max_output_tokens,
        )

    def outline_output_tokens(self, theme_count: int) -> int:
        if type(theme_count) is not int or theme_count < 1:
            raise ValueError("theme_count 必须是正整数。")
        return min(
            self.outline_output_base_tokens
            + theme_count * self.outline_output_tokens_per_theme,
            self.outline_max_output_tokens,
        )


def load_topic_synthesis_config(path: Path) -> TopicSynthesisConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TopicSynthesisContractError(
            "config.read_failed",
            f"无法读取跨论文综合配置：{path}",
        ) from exc
    if not isinstance(payload, dict):
        raise TopicSynthesisContractError(
            "config.invalid",
            "跨论文综合配置必须是 JSON 对象。",
        )
    expected = set(TopicSynthesisConfig.__dataclass_fields__)
    if set(payload) != expected:
        raise TopicSynthesisContractError(
            "config.fields_invalid",
            f"配置字段不匹配：missing={sorted(expected - set(payload))}, "
            f"unknown={sorted(set(payload) - expected)}",
        )
    try:
        config = TopicSynthesisConfig(**payload)
    except TypeError as exc:
        raise TopicSynthesisContractError(
            "config.invalid",
            "跨论文综合配置字段类型错误。",
        ) from exc
    _validate_config(config)
    return config


def validate_collection(payload: object) -> dict[str, Any]:
    validated = _validate_schema(
        payload,
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://shipping.local/schema/llm.topic_synthesis_collection.v1.json",
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "topic", "source_run_ids"],
            "properties": {
                "schema_version": {"const": COLLECTION_SCHEMA_VERSION},
                "topic": {"type": "string", "minLength": 1, "maxLength": 1000},
                "source_run_ids": {
                    "type": "array",
                    "minItems": 2,
                    "items": {"type": "string", "minLength": 1, "maxLength": 240},
                },
            },
        },
        "schema.topic_synthesis_collection_invalid",
    )
    run_ids = [str(row) for row in validated["source_run_ids"]]
    if len(run_ids) != len(set(run_ids)):
        raise TopicSynthesisContractError(
            "collection.duplicate_source_run",
            "source_run_ids 不得重复。",
            path="$.source_run_ids",
        )
    invalid = [
        run_id
        for run_id in run_ids
        if run_id in {".", ".."} or "/" in run_id or "\\" in run_id
    ]
    if invalid:
        raise TopicSynthesisContractError(
            "collection.source_run_id_invalid",
            f"source_run_ids 必须是单个目录名，不能包含路径：{invalid}",
            path="$.source_run_ids",
        )
    return copy.deepcopy(validated)


def build_topic_evidence_map_schema(
    evidence_unit_ids: list[str],
    config: TopicSynthesisConfig,
) -> dict[str, Any]:
    ids = _validated_ids(evidence_unit_ids, "evidence_unit_ids")
    navigation = {
        "type": "string",
        "minLength": 1,
        "maxLength": 480,
    }
    assignment = {
        "type": "object",
        "additionalProperties": False,
        "required": ["evidence_unit_id", "role"],
        "properties": {
            "evidence_unit_id": {"type": "string", "enum": ids},
            "role": {"type": "string", "enum": list(EVIDENCE_ROLES)},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.topic_evidence_map.v2.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "themes",
            "unassigned_evidence",
            "corpus_gaps",
        ],
        "properties": {
            "schema_version": {"const": EVIDENCE_MAP_SCHEMA_VERSION},
            "themes": {
                "type": "array",
                "minItems": 1,
                "maxItems": config.max_themes,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "title",
                        "focus",
                        "synthesis_value",
                        "relation_type",
                        "assignments",
                    ],
                    "properties": {
                        "title": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 120,
                        },
                        "focus": navigation,
                        "synthesis_value": navigation,
                        "relation_type": {
                            "type": "string",
                            "enum": list(THEME_RELATION_TYPES),
                            "description": (
                                "仅当该主题全部 assignments 来自同一 paper_id 时"
                                "可用 single_source；来自两个或更多 paper_id 时必须"
                                "使用 convergent、complementary、divergent 或 mixed。"
                            ),
                        },
                        "assignments": {
                            "type": "array",
                            "minItems": 1,
                            "items": assignment,
                        },
                    },
                },
            },
            "unassigned_evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["evidence_unit_id", "reason_code", "reason"],
                    "properties": {
                        "evidence_unit_id": {"type": "string", "enum": ids},
                        "reason_code": {
                            "type": "string",
                            "enum": list(UNASSIGNED_REASON_CODES),
                        },
                        "reason": navigation,
                    },
                },
            },
            "corpus_gaps": {
                "type": "array",
                "maxItems": config.max_search_directions,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["gap_type", "question", "why_it_matters"],
                    "properties": {
                        "gap_type": {"type": "string", "enum": list(GAP_TYPES)},
                        "question": navigation,
                        "why_it_matters": navigation,
                    },
                },
            },
        },
    }


def validate_topic_evidence_map(
    payload: object,
    *,
    topic: str,
    evidence_to_paper: dict[str, str],
    config: TopicSynthesisConfig,
) -> dict[str, Any]:
    if not isinstance(topic, str) or not topic.strip():
        raise TopicSynthesisContractError("input.topic_invalid", "综述主题不能为空。")
    ids = _validated_ids(list(evidence_to_paper), "evidence_to_paper")
    if any(not isinstance(value, str) or not value.strip() for value in evidence_to_paper.values()):
        raise TopicSynthesisContractError(
            "input.paper_id_invalid",
            "Evidence 对应的 paper_id 必须是非空字符串。",
        )
    validated = _validate_schema(
        payload,
        build_topic_evidence_map_schema(ids, config),
        "schema.topic_evidence_map_invalid",
    )
    assignment_ids = [
        str(item["evidence_unit_id"])
        for theme in validated["themes"]
        for item in theme["assignments"]
    ]
    unassigned_ids = [
        str(item["evidence_unit_id"])
        for item in validated["unassigned_evidence"]
    ]
    assignment_counts = Counter(assignment_ids)
    over_limit = sorted(
        evidence_id
        for evidence_id, count in assignment_counts.items()
        if count > config.max_themes_per_evidence
    )
    if over_limit:
        raise TopicSynthesisContractError(
            "evidence_map.assignment_limit_exceeded",
            "Evidence 归属主题数超过上限 "
            f"{config.max_themes_per_evidence}：{over_limit}",
            path="$.themes",
        )
    for theme_index, theme in enumerate(validated["themes"]):
        theme_assignment_ids = [
            str(item["evidence_unit_id"])
            for item in theme["assignments"]
        ]
        if len(theme_assignment_ids) != len(set(theme_assignment_ids)):
            raise TopicSynthesisContractError(
                "evidence_map.duplicate_assignment_within_theme",
                "同一 Evidence 不能在同一主题内重复出现。",
                path=f"$.themes[{theme_index}].assignments",
            )
    if len(unassigned_ids) != len(set(unassigned_ids)):
        raise TopicSynthesisContractError(
            "evidence_map.duplicate_unassigned",
            "未归组 Evidence 不得重复。",
            path="$.unassigned_evidence",
        )
    overlap = sorted(set(assignment_ids) & set(unassigned_ids))
    if overlap:
        raise TopicSynthesisContractError(
            "evidence_map.assigned_and_unassigned",
            f"Evidence 不能同时归组和未归组：{overlap}",
        )
    covered = set(assignment_ids) | set(unassigned_ids)
    expected = set(ids)
    if covered != expected:
        raise TopicSynthesisContractError(
            "evidence_map.coverage_mismatch",
            f"Evidence 覆盖不完整：missing={sorted(expected - covered)}, "
            f"unknown={sorted(covered - expected)}",
        )
    titles: set[str] = set()
    normalized = copy.deepcopy(validated)
    theme_ids: set[str] = set()
    relation_derivations: list[dict[str, Any]] = []
    for index, theme in enumerate(normalized["themes"]):
        title_key = " ".join(str(theme["title"]).split()).casefold()
        if title_key in titles:
            raise TopicSynthesisContractError(
                "evidence_map.duplicate_theme",
                "主题标题不得重复。",
                path=f"$.themes[{index}].title",
            )
        titles.add(title_key)
        paper_ids = {
            evidence_to_paper[str(item["evidence_unit_id"])]
            for item in theme["assignments"]
        }
        input_relation = str(theme["relation_type"])
        if len(paper_ids) == 1:
            relation = "single_source"
            relation_rule = "derived_from_one_distinct_paper"
        elif input_relation == "single_source":
            raise TopicSynthesisContractError(
                "evidence_map.relation_paper_count_invalid",
                f"relation_type={input_relation!r} 与独立论文数 "
                f"{len(paper_ids)} 不一致。",
                path=f"$.themes[{index}].relation_type",
            )
        else:
            relation = input_relation
            relation_rule = "model_cross_paper_relation"
        theme["relation_type"] = relation
        theme_id = _stable_id(
            "theme",
            "\n".join(
                [
                    " ".join(topic.split()),
                    " ".join(str(theme["title"]).split()),
                    " ".join(str(theme["focus"]).split()),
                ]
            ),
        )
        if theme_id in theme_ids:
            raise TopicSynthesisContractError(
                "evidence_map.theme_id_collision",
                f"主题稳定 ID 冲突：{theme_id}",
            )
        theme_ids.add(theme_id)
        theme["theme_id"] = theme_id
        relation_derivations.append(
            {
                "theme_id": theme_id,
                "input_relation_type": input_relation,
                "relation_type": relation,
                "distinct_paper_count": len(paper_ids),
                "rule": relation_rule,
            }
        )
    gap_ids: set[str] = set()
    for index, gap in enumerate(normalized["corpus_gaps"]):
        gap_id = _stable_id(
            "gap",
            "\n".join(
                [
                    " ".join(topic.split()),
                    str(gap["gap_type"]),
                    " ".join(str(gap["question"]).split()),
                    " ".join(str(gap["why_it_matters"]).split()),
                ]
            ),
        )
        if gap_id in gap_ids:
            raise TopicSynthesisContractError(
                "evidence_map.duplicate_gap",
                "语料缺口不得重复。",
                path=f"$.corpus_gaps[{index}]",
            )
        gap_ids.add(gap_id)
        gap["gap_id"] = gap_id
    normalized["derivations"] = {
        "relation_types": relation_derivations,
    }
    return normalized


def build_topic_review_outline_schema(
    *,
    theme_ids: list[str],
    assigned_evidence_ids: list[str],
    gap_ids: list[str],
    config: TopicSynthesisConfig,
) -> dict[str, Any]:
    themes = _validated_ids(theme_ids, "theme_ids")
    evidence = _validated_ids(assigned_evidence_ids, "assigned_evidence_ids")
    gaps = _validated_optional_ids(gap_ids, "gap_ids")
    navigation = {
        "type": "string",
        "minLength": 1,
        "maxLength": 480,
    }
    evidence_refs = {
        "type": "array",
        "uniqueItems": True,
        "maxItems": config.max_evidence_per_synthesis_unit,
        "items": {"type": "string", "enum": evidence},
    }
    gap_refs = {
        "type": "array",
        "uniqueItems": True,
        "maxItems": len(gaps),
        "items": {"type": "string", "enum": gaps},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.topic_review_outline.v2.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "working_title",
            "central_question",
            "synthesis_units",
            "sections",
            "search_directions",
        ],
        "properties": {
            "schema_version": {"const": OUTLINE_SCHEMA_VERSION},
            "working_title": {
                "type": "string",
                "minLength": 1,
                "maxLength": 240,
            },
            "central_question": navigation,
            "synthesis_units": {
                "type": "array",
                "minItems": 1,
                "maxItems": config.max_synthesis_units,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "unit_index",
                        "unit_type",
                        "synthesis_statement",
                        "theme_ids",
                        "evidence_unit_ids",
                        "gap_ids",
                    ],
                    "properties": {
                        "unit_index": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": config.max_synthesis_units,
                        },
                        "unit_type": {
                            "type": "string",
                            "enum": list(SYNTHESIS_UNIT_TYPES),
                            "description": (
                                "仅当 evidence_unit_ids 全部来自同一 paper_id "
                                "时可用 single_source_context；来自两个或更多 "
                                "paper_id 时必须使用 consensus、complement、"
                                "contrast 或 causal_chain。"
                            ),
                        },
                        "synthesis_statement": navigation,
                        "theme_ids": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": themes},
                        },
                        "evidence_unit_ids": evidence_refs,
                        "gap_ids": gap_refs,
                    },
                    "allOf": [
                        {
                            "if": {
                                "properties": {
                                    "unit_type": {"const": "corpus_gap"}
                                },
                                "required": ["unit_type"],
                            },
                            "then": {
                                "properties": {
                                    "evidence_unit_ids": {"maxItems": 0},
                                    "gap_ids": {"minItems": 1},
                                }
                            },
                            "else": {
                                "properties": {
                                    "evidence_unit_ids": {"minItems": 1},
                                    "gap_ids": {"maxItems": 0},
                                }
                            },
                        }
                    ],
                },
            },
            "sections": {
                "type": "array",
                "minItems": 1,
                "maxItems": config.max_sections,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "purpose", "paragraphs"],
                    "properties": {
                        "title": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "purpose": navigation,
                        "paragraphs": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": config.max_paragraphs_per_section,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "synthesis_move",
                                    "synthesis_unit_indexes",
                                ],
                                "properties": {
                                    "synthesis_move": navigation,
                                    "synthesis_unit_indexes": {
                                        "type": "array",
                                        "minItems": 1,
                                        "uniqueItems": True,
                                        "items": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "maximum": config.max_synthesis_units,
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "search_directions": {
                "type": "array",
                "maxItems": config.max_search_directions if gaps else 0,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "priority",
                        "question",
                        "reason",
                        "related_theme_ids",
                        "source_gap_ids",
                    ],
                    "properties": {
                        "priority": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "question": navigation,
                        "reason": navigation,
                        "related_theme_ids": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": themes},
                        },
                        "source_gap_ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": len(gaps),
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": gaps},
                        },
                    },
                },
            },
        },
    }


def validate_topic_review_outline(
    payload: object,
    *,
    theme_map: dict[str, Any],
    evidence_to_paper: dict[str, str],
    config: TopicSynthesisConfig,
) -> dict[str, Any]:
    theme_ids = [str(row["theme_id"]) for row in theme_map["themes"]]
    assigned_ids = list(
        dict.fromkeys(
            str(item["evidence_unit_id"])
            for theme in theme_map["themes"]
            for item in theme["assignments"]
        )
    )
    gap_ids = [str(row["gap_id"]) for row in theme_map["corpus_gaps"]]
    validated = _validate_schema(
        payload,
        build_topic_review_outline_schema(
            theme_ids=theme_ids,
            assigned_evidence_ids=assigned_ids,
            gap_ids=gap_ids,
            config=config,
        ),
        "schema.topic_review_outline_invalid",
    )
    indexes = [int(row["unit_index"]) for row in validated["synthesis_units"]]
    expected_indexes = list(range(1, len(indexes) + 1))
    if sorted(indexes) != expected_indexes:
        raise TopicSynthesisContractError(
            "outline.unit_indexes_invalid",
            f"unit_index 必须连续且唯一：expected={expected_indexes}, actual={sorted(indexes)}",
            path="$.synthesis_units",
        )
    known_unit_indexes = set(indexes)
    normalized = copy.deepcopy(validated)
    stable_ids: dict[int, str] = {}
    normalized_unit_by_index: dict[int, dict[str, Any]] = {}
    seen_stable_ids: set[str] = set()
    unit_type_derivations: list[dict[str, Any]] = []
    section_theme_derivations: list[dict[str, Any]] = []
    paragraph_binding_derivations: list[dict[str, Any]] = []
    evidence_by_theme = {
        str(theme["theme_id"]): {
            str(item["evidence_unit_id"]) for item in theme["assignments"]
        }
        for theme in theme_map["themes"]
    }
    unit_theme_ids: set[str] = set()
    for index, unit in enumerate(normalized["synthesis_units"]):
        unit_index = int(unit["unit_index"])
        evidence_ids = [str(row) for row in unit["evidence_unit_ids"]]
        unit_gap_ids = [str(row) for row in unit["gap_ids"]]
        unit_theme_ids.update(str(row) for row in unit["theme_ids"])
        allowed_theme_evidence = {
            evidence_id
            for theme_id in unit["theme_ids"]
            for evidence_id in evidence_by_theme[str(theme_id)]
        }
        if not set(evidence_ids).issubset(allowed_theme_evidence):
            raise TopicSynthesisContractError(
                "outline.unit_evidence_outside_themes",
                "综合单元 Evidence 必须来自其声明主题。",
                path=f"$.synthesis_units[{index}].evidence_unit_ids",
            )
        paper_ids = {evidence_to_paper[row] for row in evidence_ids}
        input_unit_type = str(unit["unit_type"])
        if input_unit_type == "corpus_gap":
            unit_type = input_unit_type
            unit_type_rule = "model_corpus_gap"
            if evidence_ids:
                raise TopicSynthesisContractError(
                    "outline.gap_has_evidence",
                    "corpus_gap 不得伪造 Evidence。",
                    path=f"$.synthesis_units[{index}].evidence_unit_ids",
                )
            if not unit_gap_ids:
                raise TopicSynthesisContractError(
                    "outline.gap_identity_missing",
                    "corpus_gap 必须引用至少一个已识别的语料缺口。",
                    path=f"$.synthesis_units[{index}].gap_ids",
                )
        else:
            if not evidence_ids:
                raise TopicSynthesisContractError(
                    "outline.unit_evidence_missing",
                    "非 gap 综合单元必须引用 Evidence。",
                    path=f"$.synthesis_units[{index}].evidence_unit_ids",
                )
            if unit_gap_ids:
                raise TopicSynthesisContractError(
                    "outline.non_gap_has_gap",
                    "非 corpus_gap 综合单元不得引用语料缺口。",
                    path=f"$.synthesis_units[{index}].gap_ids",
                )
            if len(paper_ids) == 1:
                unit_type = "single_source_context"
                unit_type_rule = "derived_from_one_distinct_paper"
            elif input_unit_type == "single_source_context":
                raise TopicSynthesisContractError(
                    "outline.unit_paper_count_invalid",
                    "single_source_context 不能引用多篇独立论文。",
                    path=f"$.synthesis_units[{index}]",
                )
            else:
                unit_type = input_unit_type
                unit_type_rule = "model_cross_paper_unit"
        unit["unit_type"] = unit_type
        synthesis_id = _stable_id(
            "synthesis",
            json.dumps(
                {
                    "unit_type": unit_type,
                    "theme_ids": sorted(unit["theme_ids"]),
                    "evidence_unit_ids": sorted(evidence_ids),
                    "gap_ids": sorted(unit_gap_ids),
                    "statement": " ".join(str(unit["synthesis_statement"]).split()),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        if synthesis_id in seen_stable_ids:
            raise TopicSynthesisContractError(
                "outline.synthesis_id_collision",
                f"综合单元稳定 ID 冲突：{synthesis_id}",
            )
        seen_stable_ids.add(synthesis_id)
        stable_ids[unit_index] = synthesis_id
        unit["synthesis_unit_id"] = synthesis_id
        del unit["unit_index"]
        normalized_unit_by_index[unit_index] = unit
        unit_type_derivations.append(
            {
                "synthesis_unit_id": synthesis_id,
                "input_unit_type": input_unit_type,
                "unit_type": unit_type,
                "distinct_paper_count": len(paper_ids),
                "rule": unit_type_rule,
            }
        )

    if unit_theme_ids != set(theme_ids):
        raise TopicSynthesisContractError(
            "outline.theme_unit_coverage_invalid",
            "每个主题必须至少进入一个综合单元。",
            path="$.synthesis_units",
        )

    referenced_unit_indexes: set[int] = set()
    section_covered_themes: set[str] = set()
    for section_index, section in enumerate(normalized["sections"]):
        section_referenced_indexes: list[int] = []
        section_referenced_themes: set[str] = set()
        for paragraph_index, paragraph in enumerate(section["paragraphs"]):
            source_paragraph = validated["sections"][section_index]["paragraphs"][
                paragraph_index
            ]
            referenced_indexes = [
                int(row) for row in source_paragraph["synthesis_unit_indexes"]
            ]
            referenced_unit_indexes.update(referenced_indexes)
            section_referenced_indexes.extend(referenced_indexes)
            unknown = sorted(set(referenced_indexes) - known_unit_indexes)
            if unknown:
                raise TopicSynthesisContractError(
                    "outline.unknown_unit_index",
                    f"段落引用未知综合单元索引：{unknown}",
                    path=(
                        f"$.sections[{section_index}].paragraphs[{paragraph_index}]"
                        ".synthesis_unit_indexes"
                    ),
                )
            referenced_units = [
                normalized_unit_by_index[row] for row in referenced_indexes
            ]
            referenced_theme_ids = {
                str(theme_id)
                for unit in referenced_units
                for theme_id in unit["theme_ids"]
            }
            section_referenced_themes.update(referenced_theme_ids)
            unit_evidence = [
                str(evidence_id)
                for unit in referenced_units
                for evidence_id in unit["evidence_unit_ids"]
            ]
            derived_evidence = list(dict.fromkeys(unit_evidence))
            unit_gaps = [
                str(gap_id)
                for unit in referenced_units
                for gap_id in unit["gap_ids"]
            ]
            derived_gaps = list(dict.fromkeys(unit_gaps))
            unit_types = {str(row["unit_type"]) for row in referenced_units}
            if unit_types == {"corpus_gap"}:
                paragraph_role = "research_gap"
                derived_evidence = []
                binding_rule = "gap_units_only"
            elif "corpus_gap" in unit_types:
                paragraph_role = "evidence_gap_synthesis"
                binding_rule = "evidence_and_gap_units"
            else:
                paragraph_role = "evidence_synthesis"
                derived_gaps = []
                binding_rule = "evidence_units_only"
            paragraph["paragraph_role"] = paragraph_role
            paragraph["evidence_unit_ids"] = derived_evidence
            paragraph["gap_ids"] = derived_gaps
            paragraph["synthesis_unit_ids"] = [
                stable_ids[row] for row in referenced_indexes
            ]
            del paragraph["synthesis_unit_indexes"]
            paragraph_binding_derivations.append(
                {
                    "section_index": section_index,
                    "paragraph_index": paragraph_index,
                    "paragraph_role": paragraph_role,
                    "source_unit_indexes": referenced_indexes,
                    "evidence_unit_ids": derived_evidence,
                    "gap_ids": derived_gaps,
                    "rule": binding_rule,
                }
            )
        derived_section_themes = [
            theme_id
            for theme_id in theme_ids
            if theme_id in section_referenced_themes
        ]
        section["theme_ids"] = derived_section_themes
        section_covered_themes.update(derived_section_themes)
        section_theme_derivations.append(
            {
                "section_index": section_index,
                "source_unit_indexes": list(
                    dict.fromkeys(section_referenced_indexes)
                ),
                "theme_ids": derived_section_themes,
                "rule": "union_of_referenced_synthesis_units",
            }
        )
    if section_covered_themes != set(theme_ids):
        raise TopicSynthesisContractError(
            "outline.theme_coverage_invalid",
            "每个主题必须至少进入一个章节。",
            path="$.sections",
        )
    referenced_gap_ids = {
        str(gap_id)
        for row in normalized["search_directions"]
        for gap_id in row["source_gap_ids"]
    }
    unprioritized_gap_ids = sorted(set(gap_ids) - referenced_gap_ids)
    unplaced_unit_indexes = sorted(known_unit_indexes - referenced_unit_indexes)
    normalized["unplaced_synthesis_unit_ids"] = [
        stable_ids[row] for row in unplaced_unit_indexes
    ]
    normalized["unprioritized_gap_ids"] = unprioritized_gap_ids
    normalized["derivations"] = {
        "unit_types": unit_type_derivations,
        "section_theme_ids": section_theme_derivations,
        "paragraph_bindings": paragraph_binding_derivations,
        "unplaced_synthesis_units": [
            {
                "source_unit_index": row,
                "synthesis_unit_id": stable_ids[row],
                "rule": "preserved_unplaced_candidate",
            }
            for row in unplaced_unit_indexes
        ],
        "gap_prioritization": {
            "prioritized_gap_ids": sorted(referenced_gap_ids),
            "unprioritized_gap_ids": unprioritized_gap_ids,
            "rule": "preserve_all_prioritize_selected",
        },
    }
    return normalized


def _validate_config(config: TopicSynthesisConfig) -> None:
    if config.schema_version != CONFIG_SCHEMA_VERSION:
        raise TopicSynthesisContractError(
            "config.schema_version_invalid",
            f"配置 schema_version 必须是 {CONFIG_SCHEMA_VERSION!r}。",
        )
    integer_fields = [
        field
        for field in config.__dataclass_fields__
        if field != "schema_version"
    ]
    for field in integer_fields:
        value = getattr(config, field)
        if type(value) is not int or value < 1:
            raise TopicSynthesisContractError(
                "config.value_invalid",
                f"{field} 必须是正整数。",
                path=f"$.{field}",
            )
    if config.theme_map_max_output_tokens < config.theme_map_output_base_tokens:
        raise TopicSynthesisContractError(
            "config.output_budget_invalid",
            "theme_map_max_output_tokens 不得小于基础预算。",
        )
    if config.outline_max_output_tokens < config.outline_output_base_tokens:
        raise TopicSynthesisContractError(
            "config.output_budget_invalid",
            "outline_max_output_tokens 不得小于基础预算。",
        )


def _validated_ids(values: list[str], field: str) -> list[str]:
    if not values:
        raise ValueError(f"{field} 不能为空。")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{field} 必须只包含非空字符串。")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} 不得重复。")
    return list(values)


def _validated_optional_ids(values: list[str], field: str) -> list[str]:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{field} 必须只包含非空字符串。")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} 不得重复。")
    return list(values)


def _validate_schema(
    payload: object,
    schema: dict[str, Any],
    code: str,
) -> dict[str, Any]:
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
        raise TopicSynthesisContractError(code, error.message, path=path)
    if not isinstance(payload, dict):
        raise TopicSynthesisContractError(code, "模型输出必须是 JSON 对象。")
    return copy.deepcopy(payload)


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"
