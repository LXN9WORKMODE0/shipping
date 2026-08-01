from __future__ import annotations

import copy
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .source_window_contracts import stable_id


AUDIT_B2_CONFIG_SCHEMA_VERSION = "llm.review_claim_audit_b2_config.v1"
AUDIT_B2_INPUT_SCHEMA_VERSION = "llm.review_claim_audit_b2_input.v1"
AUDIT_B2_DRAFT_SCHEMA_VERSION = "llm.review_claim_audit_b2_draft.v1"
AUDIT_B2_SCHEMA_VERSION = "llm.review_claim_audit_b2.v1"

RISK_CATEGORIES = (
    "supported",
    "qualified",
    "missing_citation_binding",
    "unsupported_inference",
    "overgeneralized_synthesis",
    "result_type_overstatement",
    "validation_level_overstatement",
    "source_mismatch",
    "contradicted",
    "unsupported_fact",
    "unplanned_claim",
)
CLAIM_ASSESSMENTS = (
    "supports_all_content",
    "supports_part_of_content",
    "contradicts_content",
    "not_relevant",
)
ACTION_BY_RISK = {
    "qualified": ("downgrade", "split"),
    "missing_citation_binding": ("add_citation",),
    "unsupported_inference": (
        "downgrade",
        "delete",
        "convert_to_corpus_synthesis",
    ),
    "overgeneralized_synthesis": (
        "downgrade",
        "convert_to_corpus_synthesis",
    ),
    "result_type_overstatement": ("downgrade", "split"),
    "validation_level_overstatement": ("downgrade", "split"),
    "source_mismatch": ("add_citation", "delete"),
    "contradicted": ("delete", "downgrade"),
    "unsupported_fact": ("delete", "split"),
    "unplanned_claim": (
        "delete",
        "split",
        "convert_to_corpus_synthesis",
        "convert_to_author_view",
        "convert_to_normative_recommendation",
    ),
}
ALWAYS_BLOCKING_RISKS = {
    "contradicted",
    "unsupported_fact",
    "result_type_overstatement",
    "validation_level_overstatement",
}
CORE_BLOCKING_RISKS = {"source_mismatch", "unplanned_claim"}
HIGH_RISK_CLAIM_TYPES = {
    "cross_paper_synthesis",
    "corpus_gap",
    "normative_recommendation",
}
SENTENCE_PATTERN = re.compile(r"[^。！？!?]+[。！？!?]?")


class ReviewClaimAuditB2ContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ReviewClaimAuditB2Config:
    schema_version: str
    max_reason_chars: int
    audit_max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


def load_review_claim_audit_b2_config(
    path: Path,
) -> ReviewClaimAuditB2Config:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.config_read_failed",
            f"无法读取B2 Claim审计配置：{path}",
        ) from exc
    expected = set(ReviewClaimAuditB2Config.__dataclass_fields__)
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.config_invalid",
            "B2 Claim审计配置字段不匹配。",
        )
    config = ReviewClaimAuditB2Config(**payload)
    if config.schema_version != AUDIT_B2_CONFIG_SCHEMA_VERSION:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.config_schema_invalid",
            "B2 Claim审计配置版本不受支持。",
        )
    for field in expected - {"schema_version"}:
        value = getattr(config, field)
        if type(value) is not int or value < 1:
            raise ReviewClaimAuditB2ContractError(
                "audit_b2.config_value_invalid",
                f"{field}必须是正整数。",
            )
    return config


def build_sentence_catalogue(chapter: dict[str, Any]) -> list[dict[str, Any]]:
    catalogue: list[dict[str, Any]] = []
    for paragraph in chapter["paragraphs"]:
        sentences = [
            value.strip()
            for value in SENTENCE_PATTERN.findall(paragraph["text"])
            if value.strip()
        ]
        if not sentences and paragraph["text"].strip():
            sentences = [paragraph["text"].strip()]
        for sentence_index, text in enumerate(sentences, start=1):
            identity = {
                "paragraph_id": paragraph["paragraph_id"],
                "paragraph_index": paragraph["paragraph_index"],
                "sentence_index": sentence_index,
                "text": text,
            }
            catalogue.append(
                {
                    **identity,
                    "sentence_id": stable_id("sentence_b2", identity),
                    "paragraph_claim_ids": copy.deepcopy(
                        paragraph["implemented_claim_ids"]
                    ),
                    "paragraph_citation_keys": copy.deepcopy(
                        paragraph["citation_keys"]
                    ),
                }
            )
    if not catalogue:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.sentence_catalogue_empty",
            "章节没有可审计句子。",
        )
    return catalogue


def derive_audit_input(
    writing_input: dict[str, Any],
    chapter: dict[str, Any],
    *,
    writing_run_id: str,
    writing_manifest_sha256: str,
) -> dict[str, Any]:
    implemented_ids = {
        claim_id
        for paragraph in chapter["paragraphs"]
        for claim_id in paragraph["implemented_claim_ids"]
    }
    claims = [
        copy.deepcopy(row)
        for row in writing_input["approved_claims"]
        if row["claim_id"] in implemented_ids
    ]
    if {row["claim_id"] for row in claims} != implemented_ids:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.implemented_claim_missing",
            "章节实现的Claim无法全部在写作输入中定位。",
        )
    required_window_ids = {
        window_id
        for claim in claims
        for window_id in claim["supporting_source_windows"]
    }
    windows = [
        copy.deepcopy(row)
        for row in writing_input["selected_source_windows"]
        if row["window_id"] in required_window_ids
    ]
    if {row["window_id"] for row in windows} != required_window_ids:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.source_window_missing",
            "已实现Claim绑定的Source Window不完整。",
        )
    citation_keys = {
        key
        for paragraph in chapter["paragraphs"]
        for key in paragraph["citation_keys"]
    }
    citations = [
        copy.deepcopy(row)
        for row in writing_input["citation_metadata"]
        if row["citation_key"] in citation_keys
    ]
    if {row["citation_key"] for row in citations} != citation_keys:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.citation_metadata_missing",
            "章节引用的题录信息不完整。",
        )
    value_without_id = {
        "schema_version": AUDIT_B2_INPUT_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3b",
        "source": {
            "writing_run_id": writing_run_id,
            "writing_manifest_sha256": writing_manifest_sha256,
            "writing_input_id": writing_input["writing_input_id"],
            "chapter_id": chapter["chapter_id"],
        },
        "section": {
            "section_id": chapter["section_id"],
            "section_index": chapter["section_index"],
            "title": chapter["title"],
        },
        "sentences": build_sentence_catalogue(chapter),
        "implemented_claims": claims,
        "source_windows": windows,
        "citation_metadata": citations,
    }
    value = {
        **value_without_id,
        "audit_input_id": stable_id("audit_input_b2", value_without_id),
    }
    return validate_audit_input(value)


def validate_audit_input(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.input_invalid",
            "B2审计输入必须是对象。",
        )
    required = {
        "schema_version",
        "pipeline_generation",
        "source",
        "section",
        "sentences",
        "implemented_claims",
        "source_windows",
        "citation_metadata",
        "audit_input_id",
    }
    if set(payload) != required:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.input_fields_invalid",
            "B2审计输入字段集合不匹配。",
        )
    if (
        payload.get("schema_version") != AUDIT_B2_INPUT_SCHEMA_VERSION
        or payload.get("pipeline_generation") != "B2.phase3b"
    ):
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.input_generation_invalid",
            "B2审计输入代际无效。",
        )
    claim_ids = [row.get("claim_id") for row in payload["implemented_claims"]]
    if not claim_ids or len(claim_ids) != len(set(claim_ids)):
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.claim_catalogue_invalid",
            "已实现Claim目录为空或ID重复。",
        )
    claim_id_set = set(claim_ids)
    sentence_ids = [row.get("sentence_id") for row in payload["sentences"]]
    if not sentence_ids or len(sentence_ids) != len(set(sentence_ids)):
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.sentence_ids_invalid",
            "句子目录为空或ID重复。",
        )
    for index, sentence in enumerate(payload["sentences"]):
        if not set(sentence["paragraph_claim_ids"]).issubset(claim_id_set):
            raise ReviewClaimAuditB2ContractError(
                "audit_b2.sentence_claim_unknown",
                "句子所在段落包含未知Claim。",
                path=f"$.sentences[{index}].paragraph_claim_ids",
            )
    expected_id = stable_id(
        "audit_input_b2",
        {key: value for key, value in payload.items() if key != "audit_input_id"},
    )
    if payload.get("audit_input_id") != expected_id:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.input_identity_invalid",
            "audit_input_id无法由内容重建。",
        )
    return copy.deepcopy(payload)


def build_audit_draft_schema(
    audit_input: dict[str, Any],
    config: ReviewClaimAuditB2Config,
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "section_id",
            "chapter_id",
            "sentence_audits",
            "chapter_assessment",
        ],
        "properties": {
            "schema_version": {"const": AUDIT_B2_DRAFT_SCHEMA_VERSION},
            "section_id": {"const": audit_input["section"]["section_id"]},
            "chapter_id": {"const": audit_input["source"]["chapter_id"]},
            "sentence_audits": {
                "type": "array",
                "minItems": len(audit_input["sentences"]),
                "maxItems": len(audit_input["sentences"]),
                "prefixItems": [
                    _sentence_audit_schema(sentence, config)
                    for sentence in audit_input["sentences"]
                ],
                "items": False,
            },
            "chapter_assessment": {
                "type": "string",
                "minLength": 1,
                "maxLength": config.max_reason_chars,
            },
        },
    }


def validate_audit_draft(
    payload: object,
    *,
    audit_input: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    draft = _validate_schema(payload, schema)
    claim_by_id = {
        row["claim_id"]: row for row in audit_input["implemented_claims"]
    }
    mapped_claim_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, (row, sentence) in enumerate(
        zip(draft["sentence_audits"], audit_input["sentences"], strict=True)
    ):
        path = f"$.sentence_audits[{index}]"
        assessments = row["claim_assessments"]
        expected_ids = sentence["paragraph_claim_ids"]
        actual_ids = [item["claim_id"] for item in assessments]
        if actual_ids != expected_ids:
            raise ReviewClaimAuditB2ContractError(
                "audit_b2.claim_assessment_order_invalid",
                "句子必须逐项评估所在段落全部计划Claim。",
                path=f"{path}.claim_assessments",
            )
        planned_ids = [
            item["claim_id"]
            for item in assessments
            if item["assessment"] != "not_relevant"
        ]
        mapped_claim_ids.update(planned_ids)
        risks = row["risk_categories"]
        if "supported" in risks and risks != ["supported"]:
            raise ReviewClaimAuditB2ContractError(
                "audit_b2.supported_mixed_with_risk",
                "supported不得与其他风险分类并存。",
                path=f"{path}.risk_categories",
            )
        sentence_role = (
            "navigation"
            if not planned_ids and risks == ["supported"]
            else "claim_bearing"
        )
        if (
            sentence_role == "claim_bearing"
            and not planned_ids
            and "unplanned_claim" not in risks
        ):
            raise ReviewClaimAuditB2ContractError(
                "audit_b2.unplanned_claim_not_reported",
                "承载实质内容但未映射计划Claim的句子必须标记unplanned_claim。",
                path=path,
            )
        if sentence_role == "claim_bearing":
            _validate_risk_assessment_consistency(row, path=path)
        importance = (
            "navigation"
            if sentence_role == "navigation"
            else _sentence_importance(planned_ids, claim_by_id)
        )
        blocking = _is_blocking(risks, importance)
        requires_revision = risks != ["supported"]
        human_confirmation_required = requires_revision and (
            any(
                claim_by_id[claim_id]["claim_type"]
                in HIGH_RISK_CLAIM_TYPES
                for claim_id in planned_ids
            )
            or bool(re.search(r"\d", sentence["text"]))
        )
        normalized.append(
            {
                **copy.deepcopy(row),
                "sentence_text": sentence["text"],
                "paragraph_index": sentence["paragraph_index"],
                "sentence_role": sentence_role,
                "planned_claim_ids": planned_ids,
                "recommended_actions": _recommended_actions(risks),
                "importance": importance,
                "blocking": blocking,
                "requires_revision": requires_revision,
                "human_confirmation_required": human_confirmation_required,
            }
        )
    missing_claims = set(claim_by_id) - mapped_claim_ids
    if missing_claims:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.implemented_claim_not_mapped",
            f"章节声明实现的Claim未映射到任何句子：{sorted(missing_claims)}。",
        )
    risk_counts = Counter(
        risk for row in normalized for risk in row["risk_categories"]
    )
    blocking_count = sum(row["blocking"] for row in normalized)
    revision_count = sum(row["requires_revision"] for row in normalized)
    human_count = sum(
        row["human_confirmation_required"] for row in normalized
    )
    without_id = {
        "schema_version": AUDIT_B2_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3b",
        "audit_input_id": audit_input["audit_input_id"],
        "section_id": draft["section_id"],
        "chapter_id": draft["chapter_id"],
        "sentence_audits": normalized,
        "chapter_assessment": draft["chapter_assessment"],
        "summary": {
            "sentence_count": len(normalized),
            "risk_counts": {
                risk: risk_counts.get(risk, 0) for risk in RISK_CATEGORIES
            },
            "blocking_sentence_count": blocking_count,
            "revision_sentence_count": revision_count,
            "human_confirmation_count": human_count,
            "implemented_claim_count": len(claim_by_id),
            "mapped_claim_count": len(mapped_claim_ids),
            "audit_passed": revision_count == 0,
            "publishable": revision_count == 0,
        },
    }
    return {
        **without_id,
        "audit_id": stable_id("review_claim_audit_b2", without_id),
    }


def _sentence_audit_schema(
    sentence: dict[str, Any],
    config: ReviewClaimAuditB2Config,
) -> dict[str, Any]:
    claim_assessment = {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim_id", "assessment", "reason"],
        "properties": {
            "claim_id": {"type": "string"},
            "assessment": {
                "type": "string",
                "enum": list(CLAIM_ASSESSMENTS),
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": config.max_reason_chars,
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "sentence_id",
            "paragraph_id",
            "sentence_index",
            "claim_assessments",
            "risk_categories",
            "reason",
        ],
        "properties": {
            "sentence_id": {"const": sentence["sentence_id"]},
            "paragraph_id": {"const": sentence["paragraph_id"]},
            "sentence_index": {"const": sentence["sentence_index"]},
            "claim_assessments": {
                "type": "array",
                "minItems": len(sentence["paragraph_claim_ids"]),
                "maxItems": len(sentence["paragraph_claim_ids"]),
                "prefixItems": [
                    {
                        **copy.deepcopy(claim_assessment),
                        "properties": {
                            **copy.deepcopy(claim_assessment["properties"]),
                            "claim_id": {"const": claim_id},
                        },
                    }
                    for claim_id in sentence["paragraph_claim_ids"]
                ],
                "items": False,
            },
            "risk_categories": {
                "oneOf": [
                    {"const": ["supported"]},
                    {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "enum": list(RISK_CATEGORIES[1:]),
                        },
                    },
                ],
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": config.max_reason_chars,
            },
        },
    }


def _validate_risk_assessment_consistency(
    row: dict[str, Any],
    *,
    path: str,
) -> None:
    risks = row["risk_categories"]
    if risks == ["supported"]:
        assessments = {
            item["assessment"] for item in row["claim_assessments"]
        }
        if "contradicts_content" in assessments or not assessments & {
            "supports_all_content",
            "supports_part_of_content",
        }:
            raise ReviewClaimAuditB2ContractError(
                "audit_b2.supported_assessment_invalid",
                "supported句必须至少有一个有效支持且不得包含来源冲突。",
                path=f"{path}.claim_assessments",
            )
        return
    assessment_values = {
        item["assessment"] for item in row["claim_assessments"]
    }
    if "contradicted" in risks and "contradicts_content" not in assessment_values:
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.contradiction_assessment_missing",
            "contradicted风险必须对应至少一个contradicts_content评估。",
            path=f"{path}.claim_assessments",
        )


def _sentence_importance(
    planned_ids: list[str],
    claim_by_id: dict[str, dict[str, Any]],
) -> str:
    if not planned_ids:
        return "unplanned"
    if any(claim_by_id[claim_id]["importance"] == "core" for claim_id in planned_ids):
        return "core"
    return "supporting"


def _recommended_actions(risks: list[str]) -> list[str]:
    actions: list[str] = []
    for risk in risks:
        if risk == "supported":
            continue
        for action in ACTION_BY_RISK[risk]:
            if action not in actions:
                actions.append(action)
            if len(actions) == 3:
                return actions
    return actions


def _is_blocking(risks: list[str], importance: str) -> bool:
    risk_set = set(risks)
    return bool(
        risk_set & ALWAYS_BLOCKING_RISKS
        or (
            importance in {"core", "unplanned"}
            and risk_set & CORE_BLOCKING_RISKS
        )
    )


def _validate_schema(payload: object, schema: dict[str, Any]) -> dict[str, Any]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda row: list(row.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.schema_invalid",
            error.message,
            path=path,
        )
    if not isinstance(payload, dict):
        raise ReviewClaimAuditB2ContractError(
            "audit_b2.schema_invalid",
            "审计输出必须是对象。",
        )
    return copy.deepcopy(payload)
