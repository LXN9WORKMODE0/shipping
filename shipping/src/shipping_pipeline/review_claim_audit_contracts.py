from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


AUDIT_CONFIG_SCHEMA_VERSION = "llm.review_claim_audit_config.v3"
CHAPTER_AUDIT_SCHEMA_VERSION = "llm.review_chapter_claim_audit.v3"


class ReviewClaimAuditError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ReviewClaimAuditConfig:
    schema_version: str
    max_reason_chars: int
    audit_max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


def load_review_claim_audit_config(
    path: Path,
) -> ReviewClaimAuditConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewClaimAuditError(
            "audit.config_read_failed",
            f"无法读取Claim审计配置：{path}",
        ) from exc
    expected = set(ReviewClaimAuditConfig.__dataclass_fields__)
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ReviewClaimAuditError(
            "audit.config_invalid",
            "Claim审计配置字段不匹配。",
        )
    config = ReviewClaimAuditConfig(**payload)
    if config.schema_version != AUDIT_CONFIG_SCHEMA_VERSION:
        raise ReviewClaimAuditError(
            "audit.config_schema_invalid",
            "Claim审计配置版本不受支持。",
        )
    for field in expected - {"schema_version"}:
        if type(getattr(config, field)) is not int or getattr(config, field) < 1:
            raise ReviewClaimAuditError(
                "audit.config_value_invalid",
                f"{field}必须是正整数。",
            )
    return config


def build_claim_catalogue(chapter: dict[str, Any]) -> list[dict[str, Any]]:
    catalogue: list[dict[str, Any]] = []
    for paragraph in chapter["paragraphs"]:
        sentences = _split_sentences(str(paragraph["text"]))
        for claim_index, text in enumerate(sentences, start=1):
            identity = {
                "section_index": chapter["section_index"],
                "paragraph_index": paragraph["paragraph_index"],
                "claim_index": claim_index,
                "claim_text": text,
                "paragraph_citation_keys": paragraph["citation_keys"],
            }
            catalogue.append(
                {
                    **identity,
                    "claim_id": _stable_id("claim", identity),
                }
            )
    if not catalogue:
        raise ReviewClaimAuditError(
            "audit.claim_catalogue_empty",
            "章节没有可审计句子。",
        )
    return catalogue


def build_chapter_claim_audit_schema(
    *,
    section_index: int,
    claim_count: int,
    citation_keys: list[str],
    config: ReviewClaimAuditConfig,
) -> dict[str, Any]:
    source_assessment = {
        "type": "object",
        "additionalProperties": False,
        "required": ["citation_key", "source_tier", "reason"],
        "properties": {
            "citation_key": {"type": "string", "enum": citation_keys},
            "source_tier": {
                "type": "string",
                "enum": ["evidence", "card", "markdown", "none"],
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": config.max_reason_chars,
            },
        },
    }
    claim = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "paragraph_index",
            "claim_index",
            "claim_type",
            "importance",
            "status",
            "reason",
            "citation_keys",
            "source_assessments",
            "result_type_check",
            "validation_level_check",
        ],
        "properties": {
            "paragraph_index": {"type": "integer", "minimum": 1},
            "claim_index": {"type": "integer", "minimum": 1},
            "claim_type": {
                "type": "string",
                "enum": [
                    "factual",
                    "review_synthesis",
                    "navigation_synthesis",
                ],
            },
            "importance": {
                "type": "string",
                "enum": ["core", "supporting", "navigation"],
            },
            "status": {
                "type": "string",
                "enum": [
                    "supported",
                    "qualified",
                    "unsupported",
                    "not_applicable",
                ],
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": config.max_reason_chars,
            },
            "citation_keys": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "enum": citation_keys},
            },
            "source_assessments": {
                "type": "array",
                "items": source_assessment,
            },
            "result_type_check": {
                "type": "string",
                "enum": ["accurate", "overstated", "not_applicable"],
            },
            "validation_level_check": {
                "type": "string",
                "enum": ["accurate", "overstated", "not_applicable"],
            },
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "section_index",
            "claim_audits",
            "chapter_assessment",
        ],
        "properties": {
            "schema_version": {"const": CHAPTER_AUDIT_SCHEMA_VERSION},
            "section_index": {"const": section_index},
            "claim_audits": {
                "type": "array",
                "minItems": claim_count,
                "maxItems": claim_count,
                "items": claim,
            },
            "chapter_assessment": {
                "type": "string",
                "minLength": 1,
                "maxLength": config.max_reason_chars,
            },
        },
    }


def validate_chapter_claim_audit(
    payload: object,
    *,
    schema: dict[str, Any],
    catalogue: list[dict[str, Any]],
    package: dict[str, Any],
) -> dict[str, Any]:
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
        raise ReviewClaimAuditError(
            "audit.schema_invalid",
            error.message,
            path=path,
        )
    result = copy.deepcopy(payload)
    paper_by_citation = {
        paper["citation_key"]: paper for paper in package["papers"]
    }
    for offset, (audit, source) in enumerate(
        zip(result["claim_audits"], catalogue, strict=True)
    ):
        path = f"$.claim_audits[{offset}]"
        if (
            audit["paragraph_index"] != source["paragraph_index"]
            or audit["claim_index"] != source["claim_index"]
        ):
            raise ReviewClaimAuditError(
                "audit.claim_order_invalid",
                "Claim审计必须与程序生成的Claim目录逐项对应。",
                path=path,
            )
        citations = audit["citation_keys"]
        paragraph_citations = source["paragraph_citation_keys"]
        flags: list[str] = []
        outside = [
            key for key in citations if key not in paragraph_citations
        ]
        if outside:
            citations = [
                key for key in citations if key in paragraph_citations
            ]
            audit["citation_keys"] = citations
            flags.append(
                "citation_outside_paragraph_removed:" + ",".join(outside)
            )
        assessments = audit["source_assessments"]
        assessment_by_key = {
            row["citation_key"]: row
            for row in assessments
            if row["citation_key"] in citations
        }
        if [row["citation_key"] for row in assessments] != citations:
            flags.append("source_assessments_rebuilt")
        assessments = [
            assessment_by_key.get(
                key,
                {
                    "citation_key": key,
                    "source_tier": "none",
                    "reason": "模型未给出该段落引用的有效来源层级。",
                },
            )
            for key in citations
        ]
        audit["source_assessments"] = assessments
        if audit["claim_type"] == "navigation_synthesis":
            if (
                audit["importance"] != "navigation"
                or audit["status"] != "not_applicable"
                or citations
                or assessments
                or audit["result_type_check"] != "not_applicable"
                or audit["validation_level_check"] != "not_applicable"
            ):
                raise ReviewClaimAuditError(
                    "audit.navigation_contract_invalid",
                    "导航Claim字段组合无效。",
                    path=path,
                )
        elif audit["claim_type"] == "review_synthesis":
            if (
                audit["importance"] == "navigation"
                or audit["status"] != "not_applicable"
                or citations
                or assessments
                or audit["result_type_check"] != "not_applicable"
                or audit["validation_level_check"] != "not_applicable"
            ):
                raise ReviewClaimAuditError(
                    "audit.review_synthesis_contract_invalid",
                    "综述内部综合Claim字段组合无效。",
                    path=path,
                )
        else:
            if audit["importance"] == "navigation":
                audit["importance"] = "supporting"
                flags.append(
                    "factual_navigation_importance_changed_to_supporting"
                )
            if audit["status"] == "not_applicable":
                audit["status"] = "unsupported"
                flags.append("factual_not_applicable_changed_to_unsupported")
            if not citations:
                citations = list(paragraph_citations)
                audit["citation_keys"] = citations
                assessments = [
                    {
                        "citation_key": key,
                        "source_tier": "none",
                        "reason": "模型未给出段内引用，程序按原段落引用记录为无支持。",
                    }
                    for key in citations
                ]
                audit["source_assessments"] = assessments
                audit["status"] = "unsupported"
                flags.append("missing_citations_changed_to_unsupported")
            tiers = [row["source_tier"] for row in assessments]
            if audit["status"] in {"supported", "qualified"} and not any(
                tier != "none" for tier in tiers
            ):
                audit["status"] = "unsupported"
                flags.append("missing_source_tier_changed_to_unsupported")
            if audit["status"] == "unsupported" and any(
                tier != "none" for tier in tiers
            ):
                for assessment in assessments:
                    assessment["source_tier"] = "none"
                flags.append("unsupported_source_tiers_cleared")
        audit["claim_id"] = source["claim_id"]
        audit["claim_text"] = source["claim_text"]
        audit["normalization_flags"] = flags
        for assessment in assessments:
            assessment["resolved_sources"] = _resolve_sources(
                paper_by_citation[assessment["citation_key"]],
                assessment["source_tier"],
            )
    result["audit_id"] = _stable_id(
        "chapter_audit",
        {key: value for key, value in result.items() if key != "audit_id"},
    )
    return result


def _resolve_sources(
    paper: dict[str, Any],
    tier: str,
) -> list[dict[str, Any]]:
    if tier == "evidence":
        return copy.deepcopy(paper["evidence_units"])
    if tier == "card":
        return [
            {
                "material_id": row["material_id"],
                "title": row.get("title", ""),
                "extract": row.get("extract", ""),
                "source": row.get("source"),
            }
            for row in paper["cards"]
        ]
    if tier == "markdown":
        return [
            {
                "paper_id": paper["paper_id"],
                "document_sha256": hashlib.sha256(
                    paper["full_markdown"].encode("utf-8")
                ).hexdigest(),
                "full_markdown_available": True,
            }
        ]
    return []


def _split_sentences(text: str) -> list[str]:
    parts = [
        value.strip()
        for value in re.findall(r"[^。！？；!?;]+[。！？；!?;]?", text)
        if value.strip()
    ]
    return parts or ([text.strip()] if text.strip() else [])


def _stable_id(prefix: str, payload: object) -> str:
    compact = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        f"{prefix}_"
        + hashlib.sha256(compact.encode("utf-8")).hexdigest()[:20]
    )
