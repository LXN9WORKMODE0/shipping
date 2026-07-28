from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator

from .llm_table_evidence import (
    build_table_numeric_scopes,
    build_table_ranking_scopes,
    resolve_selected_table_percentage_composites,
)


EVIDENCE_BATCH_SCHEMA_VERSION = "llm.evidence_batch.v4"
EVIDENCE_BATCH_NORMALIZED_SCHEMA_VERSION = "llm.evidence_batch.normalized.v1"
SECTION_SUMMARY_SCHEMA_VERSION = "llm.section_summary.v2"
PAPER_ANALYSIS_SCHEMA_VERSION = "llm.paper_analysis.v4"
PAPER_ANALYSIS_DRAFT_SCHEMA_VERSION = "llm.paper_analysis_draft.v2"
NON_USED_EVIDENCE_DISPOSITION_SCHEMA_VERSION = "llm.non_used_evidence_disposition.v1"

MATERIAL_DISPOSITIONS = ["evidence", "context", "not_relevant", "unusable"]
ASSESSMENT_REASON_CODES = [
    "direct_evidence",
    "context_only",
    "off_topic",
    "parse_damage",
    "empty_or_nontext",
    "duplicate_content",
]
EVIDENCE_TYPES = [
    "background",
    "method",
    "data",
    "result",
    "mechanism",
    "argument",
    "limitation",
    "recommendation",
    "definition",
]
CONFIDENCE_LEVELS = ["high", "medium", "low"]
MAX_EVIDENCE_CITATIONS = 8
MAX_EVIDENCE_UNITS_PER_MATERIAL = 12
_CRITICAL_CLAIM_QUALIFIERS = (
    "尚不能",
    "不超过",
    "不低于",
    "尚未",
    "未能",
    "约为",
    "大约",
    "预计",
    "可能",
    "或许",
    "有望",
    "考虑",
    "至少",
    "至多",
    "计划",
    "建议",
    "仅",
    "约",
)
_CLAIM_CLAUSE_RE = re.compile(r"[，,；;。！？!?]+")
STUDY_TYPES = [
    "quantitative",
    "qualitative",
    "simulation",
    "modeling",
    "policy_analysis",
    "review",
    "conceptual",
    "mixed",
    "unclear",
]
EVIDENCE_DISPOSITIONS = ["used", "redundant", "peripheral", "excluded"]
EVIDENCE_DISPOSITION_REASONS = [
    "supports_claim",
    "duplicate_support",
    "background_only",
    "supporting_detail",
    "low_confidence",
    "off_topic",
]

NONEMPTY_STRING = {"type": "string", "minLength": 1}

MODEL_EVIDENCE_UNIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claim", "evidence_type", "citations", "relevance", "confidence", "caveats"],
    "properties": {
        "claim": {"type": "string", "minLength": 1, "maxLength": 320},
        "evidence_type": {"type": "string", "enum": EVIDENCE_TYPES},
        "citations": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_EVIDENCE_CITATIONS,
            "uniqueItems": True,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["quote_id"],
                "properties": {
                    "quote_id": {
                        "type": "string",
                        "pattern": "^quote_[0-9a-f]{24}$",
                        "description": "从当前 Card 的引文候选中原样选择 quote_id。",
                    }
                },
            },
        },
        "relevance": {"type": "string", "minLength": 1, "maxLength": 240},
        "confidence": {"type": "string", "enum": CONFIDENCE_LEVELS},
        "caveats": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
        },
    },
}

MATERIAL_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["material_id", "disposition", "reason_code", "evidence_units"],
    "properties": {
        "material_id": NONEMPTY_STRING,
        "disposition": {"type": "string", "enum": MATERIAL_DISPOSITIONS},
        "reason_code": {"type": "string", "enum": ASSESSMENT_REASON_CODES},
        "evidence_units": {
            "type": "array",
            "maxItems": MAX_EVIDENCE_UNITS_PER_MATERIAL,
            "items": MODEL_EVIDENCE_UNIT_SCHEMA,
        },
    },
    "allOf": [
        {
            "if": {"properties": {"disposition": {"const": "evidence"}}, "required": ["disposition"]},
            "then": {
                "properties": {
                    "reason_code": {"const": "direct_evidence"},
                    "evidence_units": {"minItems": 1},
                }
            },
        },
        {
            "if": {"properties": {"disposition": {"const": "context"}}, "required": ["disposition"]},
            "then": {
                "properties": {
                    "reason_code": {"const": "context_only"},
                    "evidence_units": {"maxItems": 0},
                }
            },
        },
        {
            "if": {"properties": {"disposition": {"const": "not_relevant"}}, "required": ["disposition"]},
            "then": {
                "properties": {
                    "reason_code": {"const": "off_topic"},
                    "evidence_units": {"maxItems": 0},
                }
            },
        },
        {
            "if": {"properties": {"disposition": {"const": "unusable"}}, "required": ["disposition"]},
            "then": {
                "properties": {
                    "reason_code": {"enum": ["parse_damage", "empty_or_nontext", "duplicate_content"]},
                    "evidence_units": {"maxItems": 0},
                }
            },
        },
    ],
}

EVIDENCE_BATCH_SCHEMA_TEMPLATE: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://shipping.local/schema/llm.evidence_batch.v4.json",
    "title": "单批论文材料证据抽取",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "material_results"],
    "properties": {
        "schema_version": {"const": EVIDENCE_BATCH_SCHEMA_VERSION},
        "material_results": {
            "type": "array",
            "items": MATERIAL_RESULT_SCHEMA,
        },
    },
}


def build_evidence_batch_schema(
    batch_card_count: int,
    *,
    material_ids: list[str] | None = None,
    quote_candidate_ids: list[str] | None = None,
    quote_candidate_ids_by_material: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    if type(batch_card_count) is not int or batch_card_count < 1:
        raise ValueError("batch_card_count 必须 >= 1。")
    schema = copy.deepcopy(EVIDENCE_BATCH_SCHEMA_TEMPLATE)
    material_results = schema["properties"]["material_results"]
    material_results["minItems"] = batch_card_count
    material_results["maxItems"] = batch_card_count
    result_properties = material_results["items"]["properties"]
    citation_properties = result_properties["evidence_units"]["items"]["properties"]["citations"]["items"]["properties"]
    if material_ids is not None:
        if len(material_ids) != batch_card_count or len(material_ids) != len(set(material_ids)):
            raise ValueError("material_ids 必须与 batch_card_count 一致且唯一。")
        result_properties["material_id"]["enum"] = list(material_ids)
    if quote_candidate_ids is not None:
        if len(quote_candidate_ids) != len(set(quote_candidate_ids)):
            raise ValueError("quote_candidate_ids 必须唯一。")
        citation_properties["quote_id"]["enum"] = list(quote_candidate_ids)
    if quote_candidate_ids_by_material is not None:
        if material_ids is None:
            raise ValueError("按 Card 绑定 quote_id 时必须提供 material_ids。")
        if set(quote_candidate_ids_by_material) != set(material_ids):
            raise ValueError("quote_candidate_ids_by_material 必须完整覆盖 material_ids。")
        flattened = [
            quote_id
            for material_id in material_ids
            for quote_id in quote_candidate_ids_by_material[material_id]
        ]
        if len(flattened) != len(set(flattened)):
            raise ValueError("不同 Card 的 quote_id 必须全局唯一。")
        if quote_candidate_ids is not None and flattened != quote_candidate_ids:
            raise ValueError("全局 quote_candidate_ids 与按 Card 绑定结果不一致。")
        citation_properties["quote_id"]["enum"] = flattened
        for material_id in material_ids:
            material_results["items"]["allOf"].append(
                {
                    "if": {
                        "properties": {"material_id": {"const": material_id}},
                        "required": ["material_id"],
                    },
                    "then": {
                        "properties": {
                            "evidence_units": {
                                "items": {
                                    "properties": {
                                        "citations": {
                                            "items": {
                                                "properties": {
                                                    "quote_id": {
                                                        "enum": list(
                                                            quote_candidate_ids_by_material[material_id]
                                                        )
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    },
                }
            )
    return schema


# 通用存档 Schema；实际请求和验证必须使用 build_evidence_batch_schema。
EVIDENCE_BATCH_SCHEMA = copy.deepcopy(EVIDENCE_BATCH_SCHEMA_TEMPLATE)

EVIDENCE_REFERENCE_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["statement", "evidence_unit_ids"],
    "properties": {
        "statement": NONEMPTY_STRING,
        "evidence_unit_ids": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": NONEMPTY_STRING,
        },
    },
}

PAPER_REFERENCE_OBJECT_FIELDS = ("research_focus",)
PAPER_REFERENCE_ARRAY_FIELDS = (
    "methods",
    "core_findings",
    "limitations",
    "review_uses",
    "unresolved_questions",
)

EVIDENCE_DISPOSITION_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["evidence_unit_id", "disposition", "reason_code"],
    "properties": {
        "evidence_unit_id": NONEMPTY_STRING,
        "disposition": {"type": "string", "enum": EVIDENCE_DISPOSITIONS},
        "reason_code": {"type": "string", "enum": EVIDENCE_DISPOSITION_REASONS},
    },
    "allOf": [
        {
            "if": {"properties": {"disposition": {"const": "used"}}, "required": ["disposition"]},
            "then": {"properties": {"reason_code": {"const": "supports_claim"}}},
        },
        {
            "if": {"properties": {"disposition": {"const": "redundant"}}, "required": ["disposition"]},
            "then": {"properties": {"reason_code": {"const": "duplicate_support"}}},
        },
        {
            "if": {"properties": {"disposition": {"const": "peripheral"}}, "required": ["disposition"]},
            "then": {
                "properties": {
                    "reason_code": {
                        "enum": ["background_only", "supporting_detail", "low_confidence"]
                    }
                }
            },
        },
        {
            "if": {"properties": {"disposition": {"const": "excluded"}}, "required": ["disposition"]},
            "then": {"properties": {"reason_code": {"enum": ["low_confidence", "off_topic"]}}},
        },
    ],
}

SECTION_SUMMARY_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://shipping.local/schema/llm.section_summary.v2.json",
    "title": "论文内章节证据摘要",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "section_id",
        "section_focus",
        "claims",
        "evidence_dispositions",
    ],
    "properties": {
        "schema_version": {"const": SECTION_SUMMARY_SCHEMA_VERSION},
        "section_id": NONEMPTY_STRING,
        "section_focus": {"type": "string", "minLength": 1, "maxLength": 320},
        "claims": {"type": "array", "items": EVIDENCE_REFERENCE_ITEM},
        "evidence_dispositions": {"type": "array", "items": EVIDENCE_DISPOSITION_ITEM},
    },
}

PAPER_ANALYSIS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://shipping.local/schema/llm.paper_analysis.v4.json",
    "title": "单篇论文证据综合",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "research_focus",
        "study_type",
        "methods",
        "core_findings",
        "limitations",
        "review_uses",
        "unresolved_questions",
        "evidence_dispositions",
    ],
    "properties": {
        "schema_version": {"const": PAPER_ANALYSIS_SCHEMA_VERSION},
        "research_focus": EVIDENCE_REFERENCE_ITEM,
        "study_type": {"type": "string", "enum": STUDY_TYPES},
        "methods": {"type": "array", "items": EVIDENCE_REFERENCE_ITEM},
        "core_findings": {"type": "array", "items": EVIDENCE_REFERENCE_ITEM},
        "limitations": {"type": "array", "items": EVIDENCE_REFERENCE_ITEM},
        "review_uses": {"type": "array", "items": EVIDENCE_REFERENCE_ITEM},
        "unresolved_questions": {"type": "array", "items": EVIDENCE_REFERENCE_ITEM},
        "evidence_dispositions": {"type": "array", "items": EVIDENCE_DISPOSITION_ITEM},
    },
}

PAPER_ANALYSIS_DRAFT_SCHEMA: dict[str, Any] = copy.deepcopy(PAPER_ANALYSIS_SCHEMA)
PAPER_ANALYSIS_DRAFT_SCHEMA["$id"] = "https://shipping.local/schema/llm.paper_analysis_draft.v2.json"
PAPER_ANALYSIS_DRAFT_SCHEMA["title"] = "单篇论文证据综合草稿"
PAPER_ANALYSIS_DRAFT_SCHEMA["properties"]["schema_version"] = {
    "const": PAPER_ANALYSIS_DRAFT_SCHEMA_VERSION
}
PAPER_ANALYSIS_DRAFT_SCHEMA["required"].remove("evidence_dispositions")
del PAPER_ANALYSIS_DRAFT_SCHEMA["properties"]["evidence_dispositions"]

NON_USED_EVIDENCE_DISPOSITION_ITEM = copy.deepcopy(EVIDENCE_DISPOSITION_ITEM)
NON_USED_EVIDENCE_DISPOSITION_ITEM["properties"]["disposition"] = {
    "type": "string",
    "enum": ["redundant", "peripheral", "excluded"],
}
NON_USED_EVIDENCE_DISPOSITION_ITEM["properties"]["reason_code"] = {
    "type": "string",
    "enum": [
        "duplicate_support",
        "background_only",
        "supporting_detail",
        "low_confidence",
        "off_topic",
    ],
}
NON_USED_EVIDENCE_DISPOSITION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://shipping.local/schema/llm.non_used_evidence_disposition.v1.json",
    "title": "论文综合未采用证据分类",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "non_used_evidence_dispositions"],
    "properties": {
        "schema_version": {"const": NON_USED_EVIDENCE_DISPOSITION_SCHEMA_VERSION},
        "non_used_evidence_dispositions": {
            "type": "array",
            "items": NON_USED_EVIDENCE_DISPOSITION_ITEM,
        },
    },
}


def build_paper_analysis_schema(evidence_ids: list[str]) -> dict[str, Any]:
    ids = _validate_dynamic_evidence_ids(evidence_ids)
    schema = copy.deepcopy(PAPER_ANALYSIS_SCHEMA)
    properties = schema["properties"]
    _bind_paper_reference_ids(properties, ids)
    dispositions = properties["evidence_dispositions"]
    dispositions["minItems"] = len(ids)
    dispositions["maxItems"] = len(ids)
    disposition_id = dispositions["items"]["properties"]["evidence_unit_id"]
    dispositions["items"]["properties"]["evidence_unit_id"] = {
        **disposition_id,
        "enum": ids,
    }
    return schema


def build_paper_analysis_draft_schema(evidence_ids: list[str]) -> dict[str, Any]:
    ids = _validate_dynamic_evidence_ids(evidence_ids)
    schema = copy.deepcopy(PAPER_ANALYSIS_DRAFT_SCHEMA)
    properties = schema["properties"]
    _bind_paper_reference_ids(properties, ids)
    return schema


def _bind_paper_reference_ids(properties: dict[str, Any], evidence_ids: list[str]) -> None:
    for field in PAPER_REFERENCE_OBJECT_FIELDS:
        id_items = properties[field]["properties"]["evidence_unit_ids"]["items"]
        properties[field]["properties"]["evidence_unit_ids"]["items"] = {
            **id_items,
            "enum": evidence_ids,
        }
    for field in PAPER_REFERENCE_ARRAY_FIELDS:
        id_items = properties[field]["items"]["properties"]["evidence_unit_ids"]["items"]
        properties[field]["items"]["properties"]["evidence_unit_ids"]["items"] = {
            **id_items,
            "enum": evidence_ids,
        }


def build_non_used_evidence_disposition_schema(evidence_ids: list[str]) -> dict[str, Any]:
    ids = _validate_dynamic_evidence_ids(evidence_ids)
    schema = copy.deepcopy(NON_USED_EVIDENCE_DISPOSITION_SCHEMA)
    rows = schema["properties"]["non_used_evidence_dispositions"]
    rows["minItems"] = len(ids)
    rows["maxItems"] = len(ids)
    disposition_id = rows["items"]["properties"]["evidence_unit_id"]
    rows["items"]["properties"]["evidence_unit_id"] = {
        **disposition_id,
        "enum": ids,
    }
    return schema


def build_section_summary_schema(section_id: str, evidence_ids: list[str]) -> dict[str, Any]:
    if not section_id:
        raise ValueError("section_id 不能为空。")
    ids = _validate_dynamic_evidence_ids(evidence_ids)
    schema = copy.deepcopy(SECTION_SUMMARY_SCHEMA)
    properties = schema["properties"]
    properties["section_id"] = {"const": section_id}
    id_items = properties["claims"]["items"]["properties"]["evidence_unit_ids"]["items"]
    properties["claims"]["items"]["properties"]["evidence_unit_ids"]["items"] = {
        **id_items,
        "enum": ids,
    }
    dispositions = properties["evidence_dispositions"]
    dispositions["minItems"] = len(ids)
    dispositions["maxItems"] = len(ids)
    disposition_id = dispositions["items"]["properties"]["evidence_unit_id"]
    dispositions["items"]["properties"]["evidence_unit_id"] = {
        **disposition_id,
        "enum": ids,
    }
    return schema


def _validate_dynamic_evidence_ids(evidence_ids: list[str]) -> list[str]:
    if not evidence_ids or any(not isinstance(evidence_id, str) or not evidence_id for evidence_id in evidence_ids):
        raise ValueError("evidence_ids 必须是非空字符串列表。")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence_ids 必须唯一。")
    return list(evidence_ids)


class ContractViolation(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


def validate_evidence_batch(
    payload: object,
    materials: list[dict[str, Any]],
    quote_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    material_ids = [str(material.get("material_id", "")) for material in materials]
    validated = _validate_schema(
        payload,
        EVIDENCE_BATCH_SCHEMA_TEMPLATE,
        "schema.evidence_batch_invalid",
    )
    material_results = validated["material_results"]
    assessments = [
        {
            "material_id": str(row["material_id"]),
            "disposition": str(row["disposition"]),
            "reason_code": str(row["reason_code"]),
        }
        for row in material_results
    ]
    coverage = verify_material_coverage(material_ids, assessments)
    for key in ("missing_material_ids", "duplicate_material_ids", "unknown_material_ids"):
        if coverage[key]:
            raise ContractViolation(
                "coverage.material_result_invalid",
                f"{key}={coverage[key]}",
                path="$.material_results",
            )
    allowed_reasons = {
        "evidence": {"direct_evidence"},
        "context": {"context_only"},
        "not_relevant": {"off_topic"},
        "unusable": {"parse_damage", "empty_or_nontext", "duplicate_content"},
    }
    for index, assessment in enumerate(assessments):
        disposition = str(assessment["disposition"])
        reason_code = str(assessment["reason_code"])
        if reason_code not in allowed_reasons[disposition]:
            raise ContractViolation(
                "assessment.reason_code_mismatch",
                f"disposition={disposition!r} 不允许 reason_code={reason_code!r}",
                path=f"$.material_results[{index}].reason_code",
            )

    materials_by_id = {str(material["material_id"]): material for material in materials}
    candidates_by_id: dict[str, dict[str, Any]] = {}
    for candidate in quote_candidates:
        quote_id = str(candidate.get("quote_id", ""))
        material_id = str(candidate.get("material_id", ""))
        text = candidate.get("text")
        if not quote_id or quote_id in candidates_by_id:
            raise ContractViolation(
                "input.quote_candidate_id_invalid",
                f"quote_id 为空或重复：{quote_id!r}",
                path="$.quote_candidates",
            )
        if material_id not in materials_by_id or not isinstance(text, str) or not text:
            raise ContractViolation(
                "input.quote_candidate_invalid",
                f"引文候选字段无效：quote_id={quote_id!r}",
                path="$.quote_candidates",
            )
        extract = str(materials_by_id[material_id].get("extract", ""))
        if text not in extract:
            raise ContractViolation(
                "input.quote_candidate_not_exact",
                f"引文候选不是 material_id={material_id!r} 的逐字子串",
                path="$.quote_candidates",
            )
        candidates_by_id[quote_id] = candidate
    evidence_units: list[dict[str, Any]] = []
    for result_index, result in enumerate(material_results):
        material_id = str(result["material_id"])
        for unit_index, unit in enumerate(result["evidence_units"]):
            normalized_unit = copy.deepcopy(unit)
            normalized_citations: list[dict[str, str]] = []
            for citation_index, citation in enumerate(unit["citations"]):
                path = (
                    f"$.material_results[{result_index}].evidence_units[{unit_index}]"
                    f".citations[{citation_index}].quote_id"
                )
                quote_id = str(citation["quote_id"])
                candidate = candidates_by_id.get(quote_id)
                if candidate is None:
                    raise ContractViolation(
                        "citation.unknown_quote_candidate",
                        f"unknown quote_id={quote_id!r}",
                        path=path,
                    )
                if str(candidate["material_id"]) != material_id:
                    raise ContractViolation(
                        "citation.quote_material_mismatch",
                        f"quote_id={quote_id!r} 不属于当前 material_id={material_id!r}",
                        path=path,
                    )
                normalized_citations.append(
                    {
                        "material_id": material_id,
                        "quote_id": quote_id,
                        "quote": str(candidate["text"]),
                    }
                )
            normalized_unit["citations"] = normalized_citations
            evidence_units.append(normalized_unit)
    _validate_schema(
        validated,
        build_evidence_batch_schema(
            len(material_ids),
            material_ids=material_ids,
            quote_candidate_ids=[str(row.get("quote_id", "")) for row in quote_candidates],
            quote_candidate_ids_by_material={
                material_id: [
                    str(row.get("quote_id", ""))
                    for row in quote_candidates
                    if str(row.get("material_id", "")) == material_id
                ]
                for material_id in material_ids
            },
        ),
        "schema.evidence_batch_invalid",
    )
    _validate_evidence_claim_qualifier_preservation(evidence_units)
    _validate_numeric_evidence_claim_support(
        evidence_units,
        candidates_by_id,
        materials_by_id,
    )
    _validate_table_ranking_scope(
        evidence_units,
        candidates_by_id,
        materials_by_id,
    )
    _validate_table_numeric_header(
        evidence_units,
        candidates_by_id,
        materials_by_id,
    )
    return {
        "schema_version": EVIDENCE_BATCH_NORMALIZED_SCHEMA_VERSION,
        "material_assessments": assessments,
        "evidence_units": evidence_units,
    }


def validate_paper_analysis(
    payload: object,
    evidence_ids: set[str],
    *,
    evidence_units: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validated = _validate_schema(payload, PAPER_ANALYSIS_SCHEMA, "schema.paper_analysis_invalid")
    referenced_ids = _validate_paper_references(validated, evidence_ids)
    _validate_evidence_dispositions(
        validated["evidence_dispositions"],
        evidence_ids,
        referenced_ids,
        path="$.evidence_dispositions",
    )
    _validate_schema(
        validated,
        build_paper_analysis_schema(sorted(evidence_ids)),
        "schema.paper_analysis_invalid",
    )
    if evidence_units is not None:
        _validate_numeric_statement_support(validated, evidence_units)
    return validated


def validate_paper_analysis_draft(
    payload: object,
    evidence_ids: set[str],
    *,
    evidence_units: list[dict[str, Any]],
) -> dict[str, Any]:
    validated = _validate_schema(
        payload,
        PAPER_ANALYSIS_DRAFT_SCHEMA,
        "schema.paper_analysis_draft_invalid",
    )
    _validate_paper_references(validated, evidence_ids)
    _validate_schema(
        validated,
        build_paper_analysis_draft_schema(sorted(evidence_ids)),
        "schema.paper_analysis_draft_invalid",
    )
    _validate_numeric_statement_support(validated, evidence_units)
    return validated


def validate_non_used_evidence_dispositions(
    payload: object,
    evidence_ids: set[str],
) -> dict[str, Any]:
    validated = _validate_schema(
        payload,
        NON_USED_EVIDENCE_DISPOSITION_SCHEMA,
        "schema.non_used_evidence_disposition_invalid",
    )
    rows = validated["non_used_evidence_dispositions"]
    _validate_evidence_dispositions(rows, evidence_ids, set(), path="$.non_used_evidence_dispositions")
    _validate_schema(
        validated,
        build_non_used_evidence_disposition_schema(sorted(evidence_ids)),
        "schema.non_used_evidence_disposition_invalid",
    )
    return validated


def paper_analysis_referenced_evidence_ids(paper_analysis: dict[str, Any]) -> set[str]:
    return {
        str(evidence_id)
        for _, _, item in _iter_paper_reference_items(paper_analysis)
        for evidence_id in item["evidence_unit_ids"]
    }


def _validate_paper_references(
    paper_analysis: dict[str, Any],
    evidence_ids: set[str],
) -> set[str]:
    referenced_ids: set[str] = set()
    for field, item_index, item in _iter_paper_reference_items(paper_analysis):
        item_path = f"$.{field}" if item_index is None else f"$.{field}[{item_index}]"
        for id_index, evidence_id in enumerate(item["evidence_unit_ids"]):
            if evidence_id not in evidence_ids:
                raise ContractViolation(
                    "citation.unknown_evidence_unit",
                    f"unknown evidence_unit_id={evidence_id!r}",
                    path=f"{item_path}.evidence_unit_ids[{id_index}]",
                )
            referenced_ids.add(str(evidence_id))
    return referenced_ids


def _iter_paper_reference_items(
    paper_analysis: dict[str, Any],
):
    for field in PAPER_REFERENCE_OBJECT_FIELDS:
        yield field, None, paper_analysis[field]
    for field in PAPER_REFERENCE_ARRAY_FIELDS:
        for item_index, item in enumerate(paper_analysis[field]):
            yield field, item_index, item


def finalize_paper_analysis(
    draft: dict[str, Any],
    non_used_dispositions: list[dict[str, Any]],
    evidence_units: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_ids = [str(unit["evidence_unit_id"]) for unit in evidence_units]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ContractViolation(
            "input.duplicate_evidence_unit_id",
            "最终论文综合输入存在重复 evidence_unit_id",
            path="$.evidence_units",
        )
    referenced_ids = paper_analysis_referenced_evidence_ids(draft)
    expected_non_used = set(evidence_ids) - referenced_ids
    observed_non_used = {str(row["evidence_unit_id"]) for row in non_used_dispositions}
    if observed_non_used != expected_non_used or len(non_used_dispositions) != len(observed_non_used):
        raise ContractViolation(
            "coverage.non_used_evidence_disposition_invalid",
            f"missing={sorted(expected_non_used - observed_non_used)}, "
            f"unknown={sorted(observed_non_used - expected_non_used)}",
            path="$.non_used_evidence_dispositions",
        )
    non_used_by_id = {str(row["evidence_unit_id"]): copy.deepcopy(row) for row in non_used_dispositions}
    final = copy.deepcopy(draft)
    final["schema_version"] = PAPER_ANALYSIS_SCHEMA_VERSION
    final["evidence_dispositions"] = [
        (
            {
                "evidence_unit_id": evidence_id,
                "disposition": "used",
                "reason_code": "supports_claim",
            }
            if evidence_id in referenced_ids
            else non_used_by_id[evidence_id]
        )
        for evidence_id in evidence_ids
    ]
    return validate_paper_analysis(final, set(evidence_ids), evidence_units=evidence_units)


def validate_section_summary(
    payload: object,
    *,
    section_id: str,
    evidence_ids: set[str],
) -> dict[str, Any]:
    validated = _validate_schema(payload, SECTION_SUMMARY_SCHEMA, "schema.section_summary_invalid")
    if validated["section_id"] != section_id:
        raise ContractViolation(
            "section.id_mismatch",
            f"section_id={validated['section_id']!r} 与请求 {section_id!r} 不一致",
            path="$.section_id",
        )
    referenced_ids: set[str] = set()
    for item_index, item in enumerate(validated["claims"]):
        for id_index, evidence_id in enumerate(item["evidence_unit_ids"]):
            if evidence_id not in evidence_ids:
                raise ContractViolation(
                    "citation.unknown_evidence_unit",
                    f"unknown evidence_unit_id={evidence_id!r}",
                    path=f"$.claims[{item_index}].evidence_unit_ids[{id_index}]",
                )
            referenced_ids.add(evidence_id)
    _validate_evidence_dispositions(
        validated["evidence_dispositions"],
        evidence_ids,
        referenced_ids,
        path="$.evidence_dispositions",
    )
    _validate_schema(
        validated,
        build_section_summary_schema(section_id, sorted(evidence_ids)),
        "schema.section_summary_invalid",
    )
    return validated


def _validate_evidence_dispositions(
    rows: list[dict[str, Any]],
    expected_ids: set[str],
    referenced_ids: set[str],
    *,
    path: str,
) -> None:
    observed_ids = [str(row["evidence_unit_id"]) for row in rows]
    counts = Counter(observed_ids)
    missing = sorted(expected_ids - set(observed_ids))
    unknown = sorted(set(observed_ids) - expected_ids)
    duplicate = sorted(evidence_id for evidence_id, count in counts.items() if count > 1)
    if missing or unknown or duplicate:
        raise ContractViolation(
            "coverage.evidence_disposition_invalid",
            f"missing={missing}, unknown={unknown}, duplicate={duplicate}",
            path=path,
        )
    allowed_reasons = {
        "used": {"supports_claim"},
        "redundant": {"duplicate_support"},
        "peripheral": {"background_only", "supporting_detail", "low_confidence"},
        "excluded": {"low_confidence", "off_topic"},
    }
    for index, row in enumerate(rows):
        evidence_id = str(row["evidence_unit_id"])
        disposition = str(row["disposition"])
        reason_code = str(row["reason_code"])
        if reason_code not in allowed_reasons[disposition]:
            raise ContractViolation(
                "evidence_disposition.reason_mismatch",
                f"disposition={disposition!r} 不允许 reason_code={reason_code!r}",
                path=f"{path}[{index}].reason_code",
            )
        is_referenced = evidence_id in referenced_ids
        if disposition == "used" and not is_referenced:
            raise ContractViolation(
                "coverage.used_evidence_not_referenced",
                f"used evidence_unit_id={evidence_id!r} 未被 statement 引用",
                path=f"{path}[{index}]",
            )
        if disposition != "used" and is_referenced:
            raise ContractViolation(
                "coverage.non_used_evidence_referenced",
                f"{disposition} evidence_unit_id={evidence_id!r} 不得被 statement 引用",
                path=f"{path}[{index}]",
            )


_NUMERIC_FACT_RE = re.compile(
    r"(?<![A-Za-z0-9_.,])(?:\d{1,3}(?:[, \u00a0]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*[%％])?(?![0-9_.])"
)
_MATH_SEGMENT_RE = re.compile(r"\$[^$]+\$")
_OCR_SPACED_DIGITS_RE = re.compile(r"(?<!\d)\d(?:\s*(?:~\s*)?\d){1,}(?!\d)")
_OCR_SPACED_DECIMAL_RE = re.compile(
    r"(?<!\d)(\d+)\s*\.\s*((?:\d\s*)+)(?!\d)"
)
_TEX_CONTEXT_RE = re.compile(r"\\(?:tag|cdot|forall|frac|sum|alpha|text)\b|[_^]\s*\{")


def _numeric_facts(value: str) -> set[str]:
    value = value.replace(r"\%", "%")
    facts = {
        _normalize_numeric_fact(match.group(0))
        for match in _NUMERIC_FACT_RE.finditer(value)
    }
    for math_segment in _MATH_SEGMENT_RE.findall(value):
        for match in _OCR_SPACED_DECIMAL_RE.finditer(math_segment):
            fraction = re.sub(r"\s+", "", match.group(2))
            facts.add(_normalize_numeric_fact(f"{match.group(1)}.{fraction}"))
        for match in _OCR_SPACED_DIGITS_RE.finditer(math_segment):
            collapsed = re.sub(r"[^0-9]", "", match.group(0))
            if collapsed:
                facts.add(collapsed)
    for line in value.splitlines():
        if not _TEX_CONTEXT_RE.search(line):
            continue
        for match in _OCR_SPACED_DECIMAL_RE.finditer(line):
            fraction = re.sub(r"\s+", "", match.group(2))
            facts.add(_normalize_numeric_fact(f"{match.group(1)}.{fraction}"))
        for match in _OCR_SPACED_DIGITS_RE.finditer(line):
            collapsed = re.sub(r"[^0-9]", "", match.group(0))
            if collapsed:
                facts.add(collapsed)
    return facts


def extract_numeric_facts(value: str) -> set[str]:
    """返回与证据门禁一致的规范化数值事实集合。"""
    return set(_numeric_facts(value))


def _validate_numeric_evidence_claim_support(
    evidence_units: list[dict[str, Any]],
    candidates_by_id: dict[str, dict[str, Any]],
    materials_by_id: dict[str, dict[str, Any]],
) -> None:
    seen_table_composites: set[tuple[str, str, str, int, str]] = set()
    for index, unit in enumerate(evidence_units):
        citations = list(unit.get("citations", []))
        supported_numbers: set[str] = set()
        for citation in citations:
            supported_numbers.update(_numeric_facts(str(citation.get("quote", ""))))
        for left, right in zip(citations, citations[1:]):
            left_candidate = candidates_by_id.get(str(left.get("quote_id") or ""))
            right_candidate = candidates_by_id.get(str(right.get("quote_id") or ""))
            if not left_candidate or not right_candidate:
                continue
            if (
                left_candidate.get("material_id") == right_candidate.get("material_id")
                and left_candidate.get("end_char") == right_candidate.get("start_char")
            ):
                supported_numbers.update(
                    _numeric_facts(str(left.get("quote", "")) + str(right.get("quote", "")))
                )
        claim = str(unit.get("claim", ""))
        claim_numbers = _numeric_facts(claim)
        initially_unsupported = claim_numbers - supported_numbers
        material_id = str(citations[0].get("material_id", "")) if citations else ""
        material = materials_by_id.get(material_id)
        if material is not None and initially_unsupported:
            selected_quote_ids = [str(row.get("quote_id", "")) for row in citations]
            scoped_candidates = [
                row
                for row in candidates_by_id.values()
                if str(row.get("material_id", "")) == material_id
            ]
            composites = resolve_selected_table_percentage_composites(
                claim,
                material,
                scoped_candidates,
                selected_quote_ids,
                initially_unsupported,
            )
            supported_numbers.update(row.fact for row in composites)
            for row in composites:
                identity = (
                    row.material_id,
                    row.header_quote_id,
                    row.row_quote_id,
                    row.column_index,
                    row.fact,
                )
                if identity in seen_table_composites:
                    raise ContractViolation(
                        "identity.duplicate_table_composite_fact",
                        "同一个表格单元格事实被多个 Evidence 重复表达。",
                        path=f"$.evidence_units[{index}].claim",
                    )
                seen_table_composites.add(identity)
        unsupported = sorted(claim_numbers - supported_numbers)
        if unsupported:
            raise ContractViolation(
                "citation.evidence_claim_numeric_fact_unsupported",
                f"Evidence claim 中的数值没有出现在其逐字引文中：{unsupported}",
                path=f"$.evidence_units[{index}].claim",
            )


_TABLE_RANKING_RE = re.compile(r"最高|最低|最大|最小|居首|居末|排名第一|排名最后")


def _validate_table_ranking_scope(
    evidence_units: list[dict[str, Any]],
    candidates_by_id: dict[str, dict[str, Any]],
    materials_by_id: dict[str, dict[str, Any]],
) -> None:
    for index, unit in enumerate(evidence_units):
        claim = str(unit.get("claim", ""))
        if not _TABLE_RANKING_RE.search(claim):
            continue
        citations = list(unit.get("citations", []))
        material_id = str(citations[0].get("material_id", "")) if citations else ""
        material = materials_by_id.get(material_id)
        if material is None or str(material.get("content_kind", "")) != "table":
            continue
        scoped_candidates = [
            row
            for row in candidates_by_id.values()
            if str(row.get("material_id", "")) == material_id
        ]
        scopes = build_table_ranking_scopes(material, scoped_candidates)
        selected = {str(row.get("quote_id", "")) for row in citations}
        matching_scopes = [
            scope
            for scope in scopes
            if selected.intersection(
                {*scope.header_quote_ids, *scope.data_row_quote_ids}
            )
        ]
        if len(matching_scopes) != 1:
            raise ContractViolation(
                "citation.table_ranking_scope_unprovable",
                "表格排序观点无法唯一对应到具有完整表头和至少两条数据行的表格范围。",
                path=f"$.evidence_units[{index}].claim",
            )
        scope = matching_scopes[0]
        required = {*scope.header_quote_ids, *scope.data_row_quote_ids}
        missing = sorted(required - selected)
        if missing:
            raise ContractViolation(
                "citation.table_ranking_scope_incomplete",
                f"表格排序观点没有引用完整表头和全部数据行：missing_quote_ids={missing}",
                path=f"$.evidence_units[{index}].claim",
            )


def _validate_table_numeric_header(
    evidence_units: list[dict[str, Any]],
    candidates_by_id: dict[str, dict[str, Any]],
    materials_by_id: dict[str, dict[str, Any]],
) -> None:
    for index, unit in enumerate(evidence_units):
        claim = str(unit.get("claim", ""))
        if not _numeric_facts(claim):
            continue
        citations = list(unit.get("citations", []))
        material_id = str(citations[0].get("material_id", "")) if citations else ""
        material = materials_by_id.get(material_id)
        if material is None or str(material.get("content_kind", "")) != "table":
            continue
        scoped_candidates = [
            row
            for row in candidates_by_id.values()
            if str(row.get("material_id", "")) == material_id
        ]
        scopes = build_table_numeric_scopes(material, scoped_candidates)
        selected = {str(row.get("quote_id", "")) for row in citations}
        matching_scopes = [
            scope
            for scope in scopes
            if selected.intersection(
                {*scope.header_quote_ids, *scope.data_row_quote_ids}
            )
        ]
        if len(matching_scopes) != 1:
            raise ContractViolation(
                "citation.table_numeric_scope_unprovable",
                "数值型表格观点无法唯一对应到具有完整表头和数据行的表格范围。",
                path=f"$.evidence_units[{index}].claim",
            )
        missing_headers = sorted(
            set(matching_scopes[0].header_quote_ids) - selected
        )
        if missing_headers:
            raise ContractViolation(
                "citation.table_numeric_header_missing",
                "数值型表格观点没有引用全部表头："
                f"missing_quote_ids={missing_headers}",
                path=f"$.evidence_units[{index}].citations",
            )


def _validate_evidence_claim_qualifier_preservation(
    evidence_units: list[dict[str, Any]],
) -> None:
    for index, unit in enumerate(evidence_units):
        clauses = [
            _compact_claim_text(value)
            for value in _CLAIM_CLAUSE_RE.split(str(unit.get("claim") or ""))
            if _compact_claim_text(value)
        ]
        quotes = [
            _compact_claim_text(str(citation.get("quote") or ""))
            for citation in unit.get("citations", [])
        ]
        for clause in clauses:
            if any(clause in quote for quote in quotes):
                continue
            for quote in quotes:
                for qualifier in _CRITICAL_CLAIM_QUALIFIERS:
                    if qualifier in clause or qualifier not in quote:
                        continue
                    if clause in quote.replace(qualifier, ""):
                        raise ContractViolation(
                            "citation.evidence_claim_qualifier_dropped",
                            f"Evidence claim 删除了逐字引文中的关键限定词：{qualifier!r}",
                            path=f"$.evidence_units[{index}].claim",
                        )


def _compact_claim_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def _validate_numeric_statement_support(
    paper_analysis: dict[str, Any],
    evidence_units: list[dict[str, Any]],
) -> None:
    evidence_by_id = {
        str(unit.get("evidence_unit_id", "")): unit
        for unit in evidence_units
        if unit.get("evidence_unit_id")
    }
    for field, index, item in _iter_paper_reference_items(paper_analysis):
        item_path = f"$.{field}" if index is None else f"$.{field}[{index}]"
        support_parts: list[str] = []
        for evidence_id in item["evidence_unit_ids"]:
            unit = evidence_by_id.get(str(evidence_id))
            if unit is None:
                raise ContractViolation(
                    "input.evidence_unit_missing",
                    f"无法读取 evidence_unit_id={evidence_id!r} 的支持文本",
                    path=f"{item_path}.evidence_unit_ids",
                )
            support_parts.append(str(unit.get("claim", "")))
            support_parts.extend(str(citation.get("quote", "")) for citation in unit.get("citations", []))
        supported_numbers = _numeric_facts("\n".join(support_parts))
        statement_numbers = _numeric_facts(str(item["statement"]))
        unsupported = sorted(statement_numbers - supported_numbers)
        if unsupported:
            raise ContractViolation(
                "citation.numeric_fact_unsupported",
                f"statement 中的数值没有出现在所列证据中：{unsupported}",
                path=f"{item_path}.statement",
            )


def _normalize_numeric_fact(value: str) -> str:
    return value.replace(",", "").replace(" ", "").replace("％", "%")


def verify_material_coverage(
    material_ids: list[str], assessments: list[dict[str, Any]]
) -> dict[str, list[str]]:
    expected = set(material_ids)
    observed_ids = [str(row.get("material_id", "")) for row in assessments]
    observed = set(observed_ids)
    counts = Counter(observed_ids)
    return {
        "missing_material_ids": sorted(expected - observed),
        "duplicate_material_ids": sorted(material_id for material_id, count in counts.items() if count > 1),
        "unknown_material_ids": sorted(observed - expected),
    }


def verify_exact_quotes(
    evidence_units: list[dict[str, Any]], materials_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for unit in evidence_units:
        for citation in unit.get("citations", []):
            material_id = str(citation.get("material_id", ""))
            quote = str(citation.get("quote", ""))
            material = materials_by_id.get(material_id)
            if material is None:
                failures.append({"material_id": material_id, "quote": quote, "reason": "unknown_material"})
            elif quote not in str(material.get("extract", "")):
                failures.append({"material_id": material_id, "quote": quote, "reason": "quote_not_exact"})
    return failures


def assign_evidence_unit_ids(
    request_id: str,
    evidence_units: list[dict[str, Any]],
    materials: list[dict[str, Any]],
    *,
    generation_id: str,
) -> list[dict[str, Any]]:
    if not generation_id.strip():
        raise ContractViolation("identity.generation_missing", "generation_id 不能为空。")
    materials_by_id = {str(material["material_id"]): material for material in materials}
    assigned: list[dict[str, Any]] = []
    seen_unit_ids: set[str] = set()
    for index, unit in enumerate(evidence_units):
        normalized = copy.deepcopy(unit)
        anchor_id = build_evidence_anchor_id(
            generation_id,
            str(normalized["evidence_type"]),
            list(normalized["citations"]),
        )
        unit_id = build_evidence_unit_id(anchor_id, str(normalized["claim"]))
        if unit_id in seen_unit_ids:
            raise ContractViolation(
                "identity.duplicate_evidence_unit",
                f"同一响应产生重复 evidence_unit_id={unit_id!r}",
                path=f"$.evidence_units[{index}]",
            )
        seen_unit_ids.add(unit_id)
        normalized["evidence_anchor_id"] = anchor_id
        normalized["evidence_unit_id"] = unit_id
        normalized["request_id"] = request_id
        normalized["batch_id"] = request_id
        citations = []
        for citation in normalized["citations"]:
            material = materials_by_id[str(citation["material_id"])]
            citations.append(
                {
                    **citation,
                    "source_ref": copy.deepcopy(material.get("source_span") or material.get("content_ref") or {}),
                    "source_spans": copy.deepcopy(material.get("source_spans", [])),
                    "card_title": str(material.get("clean_title") or material.get("raw_title") or "未命名材料"),
                    "confidence_flags": list(material.get("confidence_flags", [])),
                    "quality_flags": list(material.get("quality_flags", [])),
                }
            )
        normalized["citations"] = citations
        assigned.append(normalized)
    return assigned


def build_evidence_anchor_id(
    generation_id: str,
    evidence_type: str,
    citations: list[dict[str, str]],
) -> str:
    canonical = {
        "generation_id": generation_id,
        "evidence_type": evidence_type,
        "citations": sorted(
            (
                {"material_id": str(row["material_id"]), "quote": str(row["quote"])}
                for row in citations
            ),
            key=lambda row: (row["material_id"], row["quote"]),
        ),
    }
    return "anchor_" + _sha256_json(canonical)[:24]


def build_evidence_unit_id(anchor_id: str, claim: str) -> str:
    normalized_claim = " ".join(claim.split())
    return "evidence_" + hashlib.sha256(
        (anchor_id + "\n" + normalized_claim).encode("utf-8")
    ).hexdigest()[:24]


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_schema(payload: object, schema: dict[str, Any], code: str) -> dict[str, Any]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        path = "$" + "".join(f"[{item}]" if isinstance(item, int) else f".{item}" for item in error.absolute_path)
        raise ContractViolation(code, error.message, path=path)
    if not isinstance(payload, dict):
        raise ContractViolation(code, "payload must be an object")
    return payload
