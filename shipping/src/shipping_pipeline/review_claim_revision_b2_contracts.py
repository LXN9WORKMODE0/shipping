from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator

from .source_window_contracts import stable_id


REVISION_INPUT_SCHEMA_VERSION = "llm.review_claim_revision_b2_input.v1"
REVISION_DRAFT_SCHEMA_VERSION = "llm.review_claim_revision_b2_draft.v1"
REVISION_SCHEMA_VERSION = "llm.review_claim_revision_b2.v1"
OPTION_TYPES = ["minimal_edit", "conservative_rewrite", "delete_or_split"]


class ReviewClaimRevisionB2ContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


def derive_revision_input(
    adjudication_input: dict[str, Any],
    adjudication: dict[str, Any],
    source_audit_input: dict[str, Any],
    *,
    adjudication_run_id: str,
    adjudication_manifest_sha256: str,
) -> dict[str, Any]:
    final_by_id = {
        row["sentence_id"]: row
        for row in adjudication["adjudications"]
        if row["requires_revision"]
    }
    if not final_by_id:
        raise ReviewClaimRevisionB2ContractError(
            "revision_b2.no_risk_sentences",
            "裁决后没有需要生成修订建议的风险句。",
        )
    audit_sentence_by_id = {
        row["sentence_id"]: row for row in source_audit_input["sentences"]
    }
    sentences_by_paragraph: dict[str, list[dict[str, Any]]] = {}
    for row in source_audit_input["sentences"]:
        sentences_by_paragraph.setdefault(row["paragraph_id"], []).append(row)

    items = []
    for source in adjudication_input["risk_sentences"]:
        final = final_by_id.get(source["sentence_id"])
        if final is None:
            continue
        audit_sentence = audit_sentence_by_id[source["sentence_id"]]
        paragraph_sentences = sorted(
            sentences_by_paragraph[audit_sentence["paragraph_id"]],
            key=lambda row: row["sentence_index"],
        )
        items.append(
            {
                "sentence_id": source["sentence_id"],
                "paragraph_id": source["paragraph_id"],
                "paragraph_index": source["paragraph_index"],
                "sentence_index": source["sentence_index"],
                "sentence_text": source["sentence_text"],
                "paragraph_text": "".join(
                    row["text"] for row in paragraph_sentences
                ),
                "paragraph_claim_ids": copy.deepcopy(
                    source["paragraph_claim_ids"]
                ),
                "paragraph_citation_keys": copy.deepcopy(
                    source["paragraph_citation_keys"]
                ),
                "final_planned_claim_ids": copy.deepcopy(
                    final["final_planned_claim_ids"]
                ),
                "final_risk_categories": copy.deepcopy(
                    final["final_risk_categories"]
                ),
                "uncovered_texts": copy.deepcopy(
                    final.get("uncovered_texts", [])
                ),
                "adjudication_reason": final["reason"],
                "recommended_actions": copy.deepcopy(
                    final["recommended_actions"]
                ),
                "blocking": final["blocking"],
            }
        )
    value_without_id = {
        "schema_version": REVISION_INPUT_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3d",
        "source": {
            "adjudication_run_id": adjudication_run_id,
            "adjudication_manifest_sha256": adjudication_manifest_sha256,
            "adjudication_id": adjudication["adjudication_id"],
            "audit_run_id": adjudication_input["source"]["audit_run_id"],
            "writing_run_id": adjudication_input["source"][
                "writing_run_id"
            ],
            "chapter_id": adjudication_input["source"]["chapter_id"],
        },
        "section": copy.deepcopy(adjudication_input["section"]),
        "risk_sentences": items,
        "candidate_claims": copy.deepcopy(
            adjudication_input["candidate_claims"]
        ),
        "source_windows": copy.deepcopy(adjudication_input["source_windows"]),
        "citation_metadata": copy.deepcopy(
            adjudication_input["citation_metadata"]
        ),
    }
    return {
        **value_without_id,
        "revision_input_id": stable_id(
            "review_claim_revision_input_b2", value_without_id
        ),
    }


def build_revision_draft_schema(
    revision_input: dict[str, Any],
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
            "suggestions",
            "chapter_assessment",
        ],
        "properties": {
            "schema_version": {"const": REVISION_DRAFT_SCHEMA_VERSION},
            "section_id": {"const": revision_input["section"]["section_id"]},
            "suggestions": {
                "type": "array",
                "minItems": len(revision_input["risk_sentences"]),
                "maxItems": len(revision_input["risk_sentences"]),
                "prefixItems": [
                    _suggestion_schema(row, max_reason_chars)
                    for row in revision_input["risk_sentences"]
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


def validate_revision_draft(
    payload: object,
    *,
    revision_input: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    draft = _validate_schema(payload, schema)
    claim_by_id = {
        row["claim_id"]: row for row in revision_input["candidate_claims"]
    }
    normalized = []
    for item_index, (row, source) in enumerate(
        zip(
            draft["suggestions"],
            revision_input["risk_sentences"],
            strict=True,
        )
    ):
        path = f"$.suggestions[{item_index}]"
        options = []
        seen_replacements: set[tuple[str, ...]] = set()
        for option_index, option in enumerate(row["revision_options"]):
            option_path = f"{path}.revision_options[{option_index}]"
            retained = option["retained_claim_ids"]
            if len(retained) != len(set(retained)) or any(
                claim_id not in source["paragraph_claim_ids"]
                for claim_id in retained
            ):
                raise ReviewClaimRevisionB2ContractError(
                    "revision_b2.claim_binding_invalid",
                    "修订建议只能绑定本段已有且不重复的Claim。",
                    path=f"{option_path}.retained_claim_ids",
                )
            allowed_citations = {
                citation
                for claim_id in retained
                for citation in claim_by_id[claim_id]["citation_keys"]
            }
            citations = option["citation_keys"]
            if len(citations) != len(set(citations)) or any(
                citation not in allowed_citations for citation in citations
            ):
                raise ReviewClaimRevisionB2ContractError(
                    "revision_b2.citation_binding_invalid",
                    "修订建议只能使用保留Claim绑定的引用。",
                    path=f"{option_path}.citation_keys",
                )
            replacements = tuple(
                value.strip() for value in option["replacement_sentences"]
            )
            if option["operation"] == "delete":
                if replacements or retained or citations:
                    raise ReviewClaimRevisionB2ContractError(
                        "revision_b2.delete_payload_invalid",
                        "删除建议不得携带替换句、Claim或引用。",
                        path=option_path,
                    )
            else:
                if not retained or not citations:
                    raise ReviewClaimRevisionB2ContractError(
                        "revision_b2.rewrite_binding_missing",
                        "改写或拆句必须绑定至少一个Claim及其引用。",
                        path=option_path,
                    )
                if source["sentence_text"].strip() in replacements:
                    raise ReviewClaimRevisionB2ContractError(
                        "revision_b2.original_unchanged",
                        "修订建议不得原样返回风险句。",
                        path=f"{option_path}.replacement_sentences",
                    )
                if replacements in seen_replacements:
                    raise ReviewClaimRevisionB2ContractError(
                        "revision_b2.options_duplicate",
                        "不同修订类型不得给出相同替换文本。",
                        path=f"{option_path}.replacement_sentences",
                    )
                seen_replacements.add(replacements)
            value = copy.deepcopy(option)
            value["replacement_sentences"] = list(replacements)
            value["option_id"] = stable_id(
                "revision_option_b2",
                {
                    "sentence_id": source["sentence_id"],
                    "option": value,
                },
            )
            options.append(value)
        normalized.append(
            {
                "sentence_id": source["sentence_id"],
                "sentence_text": source["sentence_text"],
                "final_risk_categories": copy.deepcopy(
                    source["final_risk_categories"]
                ),
                "blocking": source["blocking"],
                "revision_options": options,
            }
        )
    value_without_id = {
        "schema_version": REVISION_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3d",
        "revision_input_id": revision_input["revision_input_id"],
        "source_adjudication_id": revision_input["source"][
            "adjudication_id"
        ],
        "section_id": revision_input["section"]["section_id"],
        "chapter_id": revision_input["source"]["chapter_id"],
        "suggestions": normalized,
        "chapter_assessment": draft["chapter_assessment"].strip(),
        "decision_status": "pending",
        "auto_applied": False,
        "summary": {
            "risk_sentence_count": len(normalized),
            "option_count": sum(
                len(row["revision_options"]) for row in normalized
            ),
            "blocking_sentence_count": sum(
                bool(row["blocking"]) for row in normalized
            ),
            "pending_decision_count": len(normalized),
        },
    }
    return {
        **value_without_id,
        "revision_suggestion_id": stable_id(
            "review_claim_revision_b2", value_without_id
        ),
    }


def _suggestion_schema(
    source: dict[str, Any], max_reason_chars: int
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["sentence_id", "revision_options"],
        "properties": {
            "sentence_id": {"const": source["sentence_id"]},
            "revision_options": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "prefixItems": [
                    _option_schema(
                        "minimal_edit", "replace", source, max_reason_chars
                    ),
                    _option_schema(
                        "conservative_rewrite",
                        "replace",
                        source,
                        max_reason_chars,
                    ),
                    _delete_or_split_schema(source, max_reason_chars),
                ],
                "items": False,
            },
        },
    }


def _option_schema(
    option_type: str,
    operation: str,
    source: dict[str, Any],
    max_reason_chars: int,
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "option_type",
            "operation",
            "replacement_sentences",
            "retained_claim_ids",
            "citation_keys",
            "rationale",
        ],
        "properties": {
            "option_type": {"const": option_type},
            "operation": {"const": operation},
            "replacement_sentences": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "items": {"type": "string", "minLength": 1, "maxLength": 800},
            },
            "retained_claim_ids": _id_array(source["paragraph_claim_ids"]),
            "citation_keys": _id_array(source["paragraph_citation_keys"]),
            "rationale": {
                "type": "string",
                "minLength": 1,
                "maxLength": max_reason_chars,
            },
        },
    }


def _delete_or_split_schema(
    source: dict[str, Any], max_reason_chars: int
) -> dict[str, Any]:
    base = _option_schema(
        "delete_or_split", "split", source, max_reason_chars
    )
    base["properties"]["operation"] = {"enum": ["delete", "split"]}
    base["properties"]["replacement_sentences"] = {
        "type": "array",
        "maxItems": 2,
        "items": {"type": "string", "minLength": 1, "maxLength": 800},
    }
    base["allOf"] = [
        {
            "if": {"properties": {"operation": {"const": "delete"}}},
            "then": {
                "properties": {
                    "replacement_sentences": {"maxItems": 0},
                    "retained_claim_ids": {"maxItems": 0},
                    "citation_keys": {"maxItems": 0},
                }
            },
            "else": {
                "properties": {
                    "replacement_sentences": {"minItems": 2, "maxItems": 2},
                    "retained_claim_ids": {"minItems": 1},
                    "citation_keys": {"minItems": 1},
                }
            },
        }
    ]
    return base


def _id_array(values: list[str]) -> dict[str, Any]:
    return {
        "type": "array",
        "uniqueItems": True,
        "maxItems": len(values),
        "items": {"enum": values},
    }


def _validate_schema(payload: object, schema: dict[str, Any]) -> dict[str, Any]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{value}]" if isinstance(value, int) else f".{value}"
            for value in error.absolute_path
        )
        raise ReviewClaimRevisionB2ContractError(
            "revision_b2.schema_invalid", error.message, path=path
        )
    if not isinstance(payload, dict):
        raise ReviewClaimRevisionB2ContractError(
            "revision_b2.object_required", "修订建议输出必须是对象。"
        )
    return copy.deepcopy(payload)
