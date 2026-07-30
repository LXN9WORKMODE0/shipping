from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


RESEARCH_UNDERSTANDING_CONFIG_SCHEMA_VERSION = (
    "llm.research_understanding_config.v1"
)
PAPER_UNDERSTANDING_SCHEMA_VERSION = "llm.paper_understanding.v1"

PAPER_RELEVANCE_LEVELS = ("core", "supporting", "peripheral", "exclude")
DATA_SOURCE_TYPES = (
    "operational_records",
    "field_observation",
    "experiment",
    "simulation_output",
    "survey",
    "interview",
    "document_analysis",
    "literature_only",
    "author_argument",
    "mixed",
    "not_reported",
)
METHOD_CATEGORIES = (
    "descriptive_analysis",
    "statistical_analysis",
    "optimization",
    "simulation",
    "algorithm_design",
    "system_design",
    "engineering_case",
    "policy_analysis",
    "conceptual_analysis",
    "mixed",
)
RESULT_TYPES = (
    "historical_observation",
    "empirical_measurement",
    "experimental_result",
    "simulation_result",
    "algorithm_benchmark",
    "engineering_implementation",
    "system_design",
    "recommendation",
    "conceptual_argument",
)
VALIDATION_LEVELS = (
    "none",
    "conceptual",
    "simulation",
    "benchmark",
    "field_observation",
    "engineering_application",
)
EVIDENCE_STRENGTH_LEVELS = ("strong", "moderate", "limited", "uncertain")
LIMITATION_BASES = ("author_stated", "reviewer_inferred")
REVIEW_ROLES = (
    "background",
    "problem_definition",
    "method_comparison",
    "result_comparison",
    "mechanism_explanation",
    "historical_evolution",
    "debate",
    "limitation",
)
UNRESOLVED_BASES = ("paper_scope_boundary", "missing_validation", "missing_data")


class ResearchUnderstandingContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ResearchUnderstandingConfig:
    schema_version: str
    max_research_questions: int
    max_methods: int
    max_contributions: int
    max_limitations: int
    max_review_roles: int
    max_unresolved_questions: int
    max_keywords: int
    understanding_max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


def load_research_understanding_config(
    path: Path,
) -> ResearchUnderstandingConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchUnderstandingContractError(
            "config.read_failed",
            f"无法读取论文认知配置：{path}",
        ) from exc
    if not isinstance(payload, dict):
        raise ResearchUnderstandingContractError(
            "config.invalid",
            "论文认知配置必须是JSON对象。",
        )
    expected = set(ResearchUnderstandingConfig.__dataclass_fields__)
    if set(payload) != expected:
        raise ResearchUnderstandingContractError(
            "config.fields_invalid",
            f"配置字段不匹配：missing={sorted(expected - set(payload))}, "
            f"unknown={sorted(set(payload) - expected)}",
        )
    try:
        config = ResearchUnderstandingConfig(**payload)
    except TypeError as exc:
        raise ResearchUnderstandingContractError(
            "config.invalid",
            "论文认知配置字段类型错误。",
        ) from exc
    _validate_config(config)
    return config


def build_paper_understanding_schema(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    material_ids: list[str],
    evidence_unit_ids: list[str],
    config: ResearchUnderstandingConfig,
) -> dict[str, Any]:
    materials = _validated_input_ids(material_ids, "material_ids", allow_empty=False)
    evidence = _validated_input_ids(
        evidence_unit_ids,
        "evidence_unit_ids",
        allow_empty=True,
    )
    _required_text(paper_id, "paper_id")
    _required_text(paper_title, "paper_title")
    _required_text(topic, "topic")

    navigation = {"type": "string", "minLength": 1, "maxLength": 600}
    optional_text = {"type": "string", "maxLength": 240}
    material_refs = {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"type": "string", "enum": materials},
    }
    evidence_refs = {
        "type": "array",
        "maxItems": len(evidence),
        "uniqueItems": True,
        "items": {"type": "string", "enum": evidence},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.paper_understanding.v1.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "paper_id",
            "paper_title",
            "topic",
            "paper_relevance",
            "research_questions",
            "study_context",
            "methods",
            "contributions",
            "limitations",
            "review_roles",
            "unresolved_questions",
            "keywords",
        ],
        "properties": {
            "schema_version": {"const": PAPER_UNDERSTANDING_SCHEMA_VERSION},
            "paper_id": {"const": paper_id},
            "paper_title": {"const": paper_title},
            "topic": {"const": topic},
            "paper_relevance": {
                "type": "string",
                "enum": list(PAPER_RELEVANCE_LEVELS),
            },
            "research_questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": config.max_research_questions,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "question",
                        "problem_category",
                        "material_ids",
                    ],
                    "properties": {
                        "question": navigation,
                        "problem_category": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 120,
                        },
                        "material_ids": material_refs,
                    },
                },
            },
            "study_context": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "study_object",
                    "data_sources",
                    "time_scope",
                    "geographic_scope",
                    "material_ids",
                ],
                "properties": {
                    "study_object": navigation,
                    "data_sources": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "enum": list(DATA_SOURCE_TYPES),
                        },
                    },
                    "time_scope": optional_text,
                    "geographic_scope": optional_text,
                    "material_ids": material_refs,
                },
            },
            "methods": {
                "type": "array",
                "minItems": 1,
                "maxItems": config.max_methods,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "method_category",
                        "method_name",
                        "description",
                        "material_ids",
                    ],
                    "properties": {
                        "method_category": {
                            "type": "string",
                            "enum": list(METHOD_CATEGORIES),
                        },
                        "method_name": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "description": navigation,
                        "material_ids": material_refs,
                    },
                },
            },
            "contributions": {
                "type": "array",
                "minItems": 1,
                "maxItems": config.max_contributions,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "statement",
                        "result_type",
                        "validation_level",
                        "evidence_strength",
                        "strength_rationale",
                        "material_ids",
                        "evidence_unit_ids",
                    ],
                    "properties": {
                        "statement": navigation,
                        "result_type": {
                            "type": "string",
                            "enum": list(RESULT_TYPES),
                        },
                        "validation_level": {
                            "type": "string",
                            "enum": list(VALIDATION_LEVELS),
                        },
                        "evidence_strength": {
                            "type": "string",
                            "enum": list(EVIDENCE_STRENGTH_LEVELS),
                        },
                        "strength_rationale": navigation,
                        "material_ids": material_refs,
                        "evidence_unit_ids": evidence_refs,
                    },
                },
            },
            "limitations": {
                "type": "array",
                "maxItems": config.max_limitations,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["statement", "basis", "material_ids"],
                    "properties": {
                        "statement": navigation,
                        "basis": {
                            "type": "string",
                            "enum": list(LIMITATION_BASES),
                        },
                        "material_ids": material_refs,
                    },
                },
            },
            "review_roles": {
                "type": "array",
                "minItems": 1,
                "maxItems": config.max_review_roles,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["role", "reason", "contribution_indexes"],
                    "properties": {
                        "role": {
                            "type": "string",
                            "enum": list(REVIEW_ROLES),
                        },
                        "reason": navigation,
                        "contribution_indexes": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": config.max_contributions,
                            },
                        },
                    },
                },
            },
            "unresolved_questions": {
                "type": "array",
                "maxItems": config.max_unresolved_questions,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["question", "basis"],
                    "properties": {
                        "question": navigation,
                        "basis": {
                            "type": "string",
                            "enum": list(UNRESOLVED_BASES),
                        },
                    },
                },
            },
            "keywords": {
                "type": "array",
                "minItems": 1,
                "maxItems": config.max_keywords,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": 80},
            },
        },
    }


def validate_paper_understanding(
    payload: object,
    *,
    schema: dict[str, Any],
    material_by_id: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    validated = _validate_schema(
        payload,
        schema,
        "schema.paper_understanding_invalid",
    )
    _validate_bound_ids(validated, material_by_id, evidence_by_id)
    _validate_duplicates(validated)

    contribution_count = len(validated["contributions"])
    for role_index, role in enumerate(validated["review_roles"]):
        unknown = [
            index
            for index in role["contribution_indexes"]
            if index < 1 or index > contribution_count
        ]
        if unknown:
            raise ResearchUnderstandingContractError(
                "understanding.review_role_contribution_unknown",
                f"综述用途引用不存在的贡献序号：{unknown}",
                path=f"$.review_roles[{role_index}].contribution_indexes",
            )

    result = copy.deepcopy(validated)
    result["understanding_id"] = _stable_id(
        "understanding",
        {
            "paper_id": result["paper_id"],
            "topic": result["topic"],
            "research_questions": result["research_questions"],
            "methods": result["methods"],
            "contributions": result["contributions"],
        },
    )
    _add_item_ids(
        result["research_questions"],
        prefix="question",
        field="research_question_id",
    )
    _add_item_ids(result["methods"], prefix="method", field="method_id")
    _add_item_ids(
        result["contributions"],
        prefix="contribution",
        field="contribution_id",
    )
    _add_item_ids(
        result["limitations"],
        prefix="limitation",
        field="limitation_id",
    )
    return result


def _validate_config(config: ResearchUnderstandingConfig) -> None:
    if (
        config.schema_version
        != RESEARCH_UNDERSTANDING_CONFIG_SCHEMA_VERSION
    ):
        raise ResearchUnderstandingContractError(
            "config.schema_version_invalid",
            "论文认知配置版本不受支持。",
        )
    for field in config.__dataclass_fields__:
        if field == "schema_version":
            continue
        value = getattr(config, field)
        if type(value) is not int or value < 1:
            raise ResearchUnderstandingContractError(
                "config.value_invalid",
                f"{field} 必须是正整数。",
                path=f"$.{field}",
            )


def _validate_bound_ids(
    payload: dict[str, Any],
    material_by_id: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
) -> None:
    material_ids: list[str] = list(payload["study_context"]["material_ids"])
    for field in ("research_questions", "methods", "contributions", "limitations"):
        for row in payload[field]:
            material_ids.extend(row["material_ids"])
    unknown_materials = sorted(set(material_ids) - set(material_by_id))
    if unknown_materials:
        raise ResearchUnderstandingContractError(
            "understanding.material_unknown",
            f"论文认知引用未知Card：{unknown_materials}",
        )
    used_evidence = {
        evidence_id
        for row in payload["contributions"]
        for evidence_id in row["evidence_unit_ids"]
    }
    unknown_evidence = sorted(used_evidence - set(evidence_by_id))
    if unknown_evidence:
        raise ResearchUnderstandingContractError(
            "understanding.evidence_unknown",
            f"论文认知引用未知Evidence：{unknown_evidence}",
        )


def _validate_duplicates(payload: dict[str, Any]) -> None:
    fields = (
        "research_questions",
        "methods",
        "contributions",
        "limitations",
        "review_roles",
        "unresolved_questions",
    )
    for field in fields:
        seen: set[str] = set()
        for index, row in enumerate(payload[field]):
            normalized = json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if normalized in seen:
                raise ResearchUnderstandingContractError(
                    "understanding.duplicate_item",
                    f"{field} 中存在重复对象。",
                    path=f"$.{field}[{index}]",
                )
            seen.add(normalized)


def _add_item_ids(
    rows: list[dict[str, Any]],
    *,
    prefix: str,
    field: str,
) -> None:
    for row in rows:
        row[field] = _stable_id(prefix, row)


def _stable_id(prefix: str, payload: object) -> str:
    compact = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _validated_input_ids(
    values: list[str],
    field: str,
    *,
    allow_empty: bool,
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field} 必须是字符串数组。")
    result: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field}[{index}] 必须是非空字符串。")
        result.append(value.strip())
    if not allow_empty and not result:
        raise ValueError(f"{field} 不能为空。")
    if len(result) != len(set(result)):
        raise ValueError(f"{field} 不得重复。")
    return result


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串。")
    return value.strip()


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
        raise ResearchUnderstandingContractError(
            code,
            error.message,
            path=path,
        )
    if not isinstance(payload, dict):
        raise ResearchUnderstandingContractError(
            code,
            "论文认知输出必须是JSON对象。",
        )
    return copy.deepcopy(payload)
