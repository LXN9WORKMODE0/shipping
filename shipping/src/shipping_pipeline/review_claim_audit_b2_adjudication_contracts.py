from __future__ import annotations

import copy
import json
import re
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator

from .review_claim_audit_b2_contracts import (
    ACTION_BY_RISK,
    ALWAYS_BLOCKING_RISKS,
    CLAIM_ASSESSMENTS,
    CORE_BLOCKING_RISKS,
    HIGH_RISK_CLAIM_TYPES,
    RISK_CATEGORIES,
)
from .source_window_contracts import stable_id


ADJUDICATION_INPUT_SCHEMA_VERSION = (
    "llm.review_claim_audit_b2_adjudication_input.v1"
)
ADJUDICATION_DRAFT_SCHEMA_VERSION = (
    "llm.review_claim_audit_b2_adjudication_draft.v1"
)
ADJUDICATION_SCHEMA_VERSION = "llm.review_claim_audit_b2_adjudication.v1"


class ReviewClaimAuditB2AdjudicationContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


def derive_adjudication_input(
    audit_input: dict[str, Any],
    audit: dict[str, Any],
    *,
    audit_run_id: str,
    audit_manifest_sha256: str,
) -> dict[str, Any]:
    risky = [row for row in audit["sentence_audits"] if row["requires_revision"]]
    if not risky:
        raise ReviewClaimAuditB2AdjudicationContractError(
            "adjudication_b2.no_risk_sentences",
            "来源审计没有需要裁决的风险句。",
        )
    sentence_by_id = {
        row["sentence_id"]: row for row in audit_input["sentences"]
    }
    candidate_claim_ids = {
        claim_id
        for row in risky
        for claim_id in sentence_by_id[row["sentence_id"]][
            "paragraph_claim_ids"
        ]
    }
    claims = [
        copy.deepcopy(row)
        for row in audit_input["implemented_claims"]
        if row["claim_id"] in candidate_claim_ids
    ]
    window_ids = {
        window_id
        for claim in claims
        for window_id in claim["supporting_source_windows"]
    }
    windows = [
        copy.deepcopy(row)
        for row in audit_input["source_windows"]
        if row["window_id"] in window_ids
    ]
    items = []
    for row in risky:
        sentence = sentence_by_id[row["sentence_id"]]
        items.append(
            {
                "sentence_id": row["sentence_id"],
                "paragraph_id": row["paragraph_id"],
                "paragraph_index": row["paragraph_index"],
                "sentence_index": row["sentence_index"],
                "sentence_text": row["sentence_text"],
                "paragraph_claim_ids": copy.deepcopy(
                    sentence["paragraph_claim_ids"]
                ),
                "paragraph_citation_keys": copy.deepcopy(
                    sentence["paragraph_citation_keys"]
                ),
                "initial_planned_claim_ids": copy.deepcopy(
                    row["planned_claim_ids"]
                ),
                "initial_risk_categories": copy.deepcopy(
                    row["risk_categories"]
                ),
                "initial_reason": row["reason"],
                "initial_claim_assessments": copy.deepcopy(
                    row["claim_assessments"]
                ),
            }
        )
    value_without_id = {
        "schema_version": ADJUDICATION_INPUT_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3c",
        "source": {
            "audit_run_id": audit_run_id,
            "audit_manifest_sha256": audit_manifest_sha256,
            "audit_id": audit["audit_id"],
            "audit_input_id": audit["audit_input_id"],
            "writing_run_id": audit_input["source"]["writing_run_id"],
            "chapter_id": audit["chapter_id"],
        },
        "section": copy.deepcopy(audit_input["section"]),
        "risk_sentences": items,
        "candidate_claims": claims,
        "source_windows": windows,
        "citation_metadata": copy.deepcopy(audit_input["citation_metadata"]),
    }
    return {
        **value_without_id,
        "adjudication_input_id": stable_id(
            "audit_adjudication_input_b2", value_without_id
        ),
    }


def build_adjudication_draft_schema(
    adjudication_input: dict[str, Any],
    *,
    max_reason_chars: int,
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "section_id",
            "audit_id",
            "adjudications",
            "chapter_assessment",
        ],
        "properties": {
            "schema_version": {"const": ADJUDICATION_DRAFT_SCHEMA_VERSION},
            "section_id": {"const": adjudication_input["section"]["section_id"]},
            "audit_id": {"const": adjudication_input["source"]["audit_id"]},
            "adjudications": {
                "type": "array",
                "minItems": len(adjudication_input["risk_sentences"]),
                "maxItems": len(adjudication_input["risk_sentences"]),
                "prefixItems": [
                    _item_schema(row, max_reason_chars)
                    for row in adjudication_input["risk_sentences"]
                ],
                "items": False,
            },
            "chapter_assessment": {
                "type": "string",
                "minLength": 1,
                "maxLength": max_reason_chars,
            },
        },
    }


def validate_adjudication_draft(
    payload: object,
    *,
    adjudication_input: dict[str, Any],
    source_audit: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    draft = _validate_schema(payload, schema)
    claim_by_id = {
        row["claim_id"]: row for row in adjudication_input["candidate_claims"]
    }
    normalized = []
    final_by_sentence: dict[str, dict[str, Any]] = {}
    for index, (row, source) in enumerate(
        zip(
            draft["adjudications"],
            adjudication_input["risk_sentences"],
            strict=True,
        )
    ):
        path = f"$.adjudications[{index}]"
        actual_claim_ids = [
            item["claim_id"] for item in row["claim_reassessments"]
        ]
        if actual_claim_ids != source["paragraph_claim_ids"]:
            raise ReviewClaimAuditB2AdjudicationContractError(
                "adjudication_b2.claim_order_invalid",
                "裁决必须逐项复核所在段落全部候选Claim。",
                path=f"{path}.claim_reassessments",
            )
        final_planned_ids = [
            item["claim_id"]
            for item in row["claim_reassessments"]
            if item["assessment"] != "not_relevant"
        ]
        final_risks = row["final_risk_categories"]
        verdict = row["verdict"]
        if verdict == "dismiss" and final_risks != ["supported"]:
            raise ReviewClaimAuditB2AdjudicationContractError(
                "adjudication_b2.dismiss_risk_invalid",
                "dismiss裁决必须得到supported。",
                path=path,
            )
        if verdict == "confirm" and final_risks != source[
            "initial_risk_categories"
        ]:
            raise ReviewClaimAuditB2AdjudicationContractError(
                "adjudication_b2.confirm_risk_changed",
                "confirm裁决不得修改初审风险分类。",
                path=path,
            )
        if verdict == "reclassify" and (
            final_risks == ["supported"]
            or final_risks == source["initial_risk_categories"]
        ):
            raise ReviewClaimAuditB2AdjudicationContractError(
                "adjudication_b2.reclassify_unchanged",
                "reclassify必须得到不同的非supported风险分类。",
                path=path,
            )
        _validate_final_risks(
            final_risks,
            row["claim_reassessments"],
            final_planned_ids,
            uncovered_texts=row["uncovered_texts"],
            sentence_text=source["sentence_text"],
            candidate_claim_ids=source["paragraph_claim_ids"],
            claim_by_id=claim_by_id,
            path=path,
        )
        sentence_role = (
            "navigation"
            if not final_planned_ids and final_risks == ["supported"]
            else "claim_bearing"
        )
        importance = _importance(final_planned_ids, claim_by_id)
        blocking = _blocking(final_risks, importance)
        requires_revision = final_risks != ["supported"]
        human = requires_revision and (
            any(
                claim_by_id[claim_id]["claim_type"]
                in HIGH_RISK_CLAIM_TYPES
                for claim_id in final_planned_ids
            )
            or bool(re.search(r"\d", source["sentence_text"]))
        )
        value = {
            **copy.deepcopy(row),
            "sentence_text": source["sentence_text"],
            "initial_risk_categories": copy.deepcopy(
                source["initial_risk_categories"]
            ),
            "final_planned_claim_ids": final_planned_ids,
            "sentence_role": sentence_role,
            "importance": importance,
            "recommended_actions": _actions(final_risks),
            "blocking": blocking,
            "requires_revision": requires_revision,
            "human_confirmation_required": human,
        }
        normalized.append(value)
        final_by_sentence[row["sentence_id"]] = value
    combined = []
    for row in source_audit["sentence_audits"]:
        adjudicated = final_by_sentence.get(row["sentence_id"])
        if adjudicated is None:
            combined.append(
                {
                    "sentence_id": row["sentence_id"],
                    "risk_categories": copy.deepcopy(row["risk_categories"]),
                    "planned_claim_ids": copy.deepcopy(row["planned_claim_ids"]),
                    "blocking": row["blocking"],
                    "requires_revision": row["requires_revision"],
                }
            )
        else:
            combined.append(
                {
                    "sentence_id": adjudicated["sentence_id"],
                    "risk_categories": copy.deepcopy(
                        adjudicated["final_risk_categories"]
                    ),
                    "planned_claim_ids": copy.deepcopy(
                        adjudicated["final_planned_claim_ids"]
                    ),
                    "blocking": adjudicated["blocking"],
                    "requires_revision": adjudicated["requires_revision"],
                }
            )
    all_claim_ids = {
        claim_id
        for row in source_audit["sentence_audits"]
        for claim_id in row["planned_claim_ids"]
    }
    final_mapped = {
        claim_id for row in combined for claim_id in row["planned_claim_ids"]
    }
    if final_mapped != all_claim_ids:
        raise ReviewClaimAuditB2AdjudicationContractError(
            "adjudication_b2.claim_coverage_changed",
            "裁决后章节Claim覆盖与初审不一致。",
        )
    risk_counts = Counter(
        risk for row in combined for risk in row["risk_categories"]
    )
    final_revision_count = sum(row["requires_revision"] for row in combined)
    without_id = {
        "schema_version": ADJUDICATION_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3c",
        "adjudication_input_id": adjudication_input["adjudication_input_id"],
        "source_audit_id": source_audit["audit_id"],
        "section_id": draft["section_id"],
        "chapter_id": source_audit["chapter_id"],
        "adjudications": normalized,
        "chapter_assessment": draft["chapter_assessment"],
        "summary": {
            "source_risk_sentence_count": len(normalized),
            "dismissed_count": sum(
                row["verdict"] == "dismiss" for row in normalized
            ),
            "confirmed_count": sum(
                row["verdict"] == "confirm" for row in normalized
            ),
            "reclassified_count": sum(
                row["verdict"] == "reclassify" for row in normalized
            ),
            "final_risk_sentence_count": final_revision_count,
            "final_blocking_sentence_count": sum(
                row["blocking"] for row in combined
            ),
            "final_risk_counts": {
                risk: risk_counts.get(risk, 0) for risk in RISK_CATEGORIES
            },
            "publishable_after_adjudication": final_revision_count == 0,
        },
    }
    return {
        **without_id,
        "adjudication_id": stable_id(
            "review_claim_audit_adjudication_b2", without_id
        ),
    }


def _item_schema(row: dict[str, Any], max_reason_chars: int) -> dict[str, Any]:
    risk_schema = {
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
        ]
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "sentence_id",
            "verdict",
            "claim_reassessments",
            "final_risk_categories",
            "uncovered_texts",
            "reason",
        ],
        "properties": {
            "sentence_id": {"const": row["sentence_id"]},
            "verdict": {
                "type": "string",
                "enum": ["confirm", "dismiss", "reclassify"],
            },
            "claim_reassessments": {
                "type": "array",
                "minItems": len(row["paragraph_claim_ids"]),
                "maxItems": len(row["paragraph_claim_ids"]),
                "prefixItems": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["claim_id", "assessment", "reason"],
                        "properties": {
                            "claim_id": {"const": claim_id},
                            "assessment": {
                                "type": "string",
                                "enum": list(CLAIM_ASSESSMENTS),
                            },
                            "reason": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": max_reason_chars,
                            },
                        },
                    }
                    for claim_id in row["paragraph_claim_ids"]
                ],
                "items": False,
            },
            "final_risk_categories": risk_schema,
            "uncovered_texts": {
                "type": "array",
                "maxItems": 3,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 200,
                },
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": max_reason_chars,
            },
        },
        "allOf": [
            {
                "if": {"properties": {"verdict": {"const": "dismiss"}}},
                "then": {
                    "properties": {
                        "final_risk_categories": {"const": ["supported"]}
                    }
                },
            },
            {
                "if": {"properties": {"verdict": {"const": "confirm"}}},
                "then": {
                    "properties": {
                        "final_risk_categories": {
                            "const": row["initial_risk_categories"]
                        }
                    }
                },
            },
        ],
    }


def _validate_final_risks(
    risks: list[str],
    reassessments: list[dict[str, Any]],
    planned_ids: list[str],
    *,
    uncovered_texts: list[str],
    sentence_text: str,
    candidate_claim_ids: list[str],
    claim_by_id: dict[str, dict[str, Any]],
    path: str,
) -> None:
    values = {row["assessment"] for row in reassessments}
    if risks == ["supported"]:
        if planned_ids and (
            "contradicts_content" in values
            or not values
            & {"supports_all_content", "supports_part_of_content"}
        ):
            raise ReviewClaimAuditB2AdjudicationContractError(
                "adjudication_b2.supported_assessment_invalid",
                "驳回风险时必须存在有效支持且不得包含来源冲突。",
                path=path,
            )
        return
    if not planned_ids and "unplanned_claim" not in risks:
        raise ReviewClaimAuditB2AdjudicationContractError(
            "adjudication_b2.unplanned_missing",
            "无Claim映射的风险句必须包含unplanned_claim。",
            path=path,
        )
    if "contradicted" in risks and "contradicts_content" not in values:
        raise ReviewClaimAuditB2AdjudicationContractError(
            "adjudication_b2.contradiction_missing",
            "contradicted必须对应来源冲突评估。",
            path=path,
        )
    if "unplanned_claim" not in risks:
        if uncovered_texts:
            raise ReviewClaimAuditB2AdjudicationContractError(
                "adjudication_b2.uncovered_text_without_unplanned",
                "非unplanned裁决不得填写计划外片段。",
                path=f"{path}.uncovered_texts",
            )
        return
    if not uncovered_texts:
        raise ReviewClaimAuditB2AdjudicationContractError(
            "adjudication_b2.uncovered_text_missing",
            "确认unplanned_claim必须列出原句中的具体计划外片段。",
            path=f"{path}.uncovered_texts",
        )
    compact_sentence = _compact_text(sentence_text)
    compact_claims = [
        _compact_text(claim_by_id[claim_id]["planned_claim"])
        for claim_id in candidate_claim_ids
    ]
    for value in uncovered_texts:
        compact = _compact_text(value)
        if len(compact) < 4 or compact not in compact_sentence:
            raise ReviewClaimAuditB2AdjudicationContractError(
                "adjudication_b2.uncovered_text_not_in_sentence",
                f"计划外片段不是原句的有效子串：{value!r}。",
                path=f"{path}.uncovered_texts",
            )
        if any(compact in claim for claim in compact_claims):
            raise ReviewClaimAuditB2AdjudicationContractError(
                "adjudication_b2.uncovered_text_already_planned",
                f"所谓计划外片段已存在于候选计划Claim中：{value!r}。",
                path=f"{path}.uncovered_texts",
            )


def _importance(
    claim_ids: list[str],
    claim_by_id: dict[str, dict[str, Any]],
) -> str:
    if not claim_ids:
        return "unplanned"
    if any(claim_by_id[claim_id]["importance"] == "core" for claim_id in claim_ids):
        return "core"
    return "supporting"


def _blocking(risks: list[str], importance: str) -> bool:
    risk_set = set(risks)
    return bool(
        risk_set & ALWAYS_BLOCKING_RISKS
        or (
            importance in {"core", "unplanned"}
            and risk_set & CORE_BLOCKING_RISKS
        )
    )


def _actions(risks: list[str]) -> list[str]:
    values = []
    for risk in risks:
        if risk == "supported":
            continue
        for action in ACTION_BY_RISK[risk]:
            if action not in values:
                values.append(action)
            if len(values) == 3:
                return values
    return values


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
        raise ReviewClaimAuditB2AdjudicationContractError(
            "adjudication_b2.schema_invalid",
            error.message,
            path=path,
        )
    if not isinstance(payload, dict):
        raise ReviewClaimAuditB2AdjudicationContractError(
            "adjudication_b2.schema_invalid",
            "裁决输出必须是对象。",
        )
    return copy.deepcopy(payload)


def _compact_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()
