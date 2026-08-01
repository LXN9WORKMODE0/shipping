from __future__ import annotations

import copy
import re
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator

from .review_claim_audit_b2_contracts import ReviewClaimAuditB2Config
from .source_window_contracts import stable_id


CONCLUSION_AUDIT_INPUT_SCHEMA_VERSION = "llm.review_conclusion_audit_b2_input.v1"
CONCLUSION_AUDIT_DRAFT_SCHEMA_VERSION = "llm.review_conclusion_audit_b2_draft.v1"
CONCLUSION_AUDIT_SCHEMA_VERSION = "llm.review_conclusion_audit_b2.v1"
RISK_CATEGORIES = (
    "supported",
    "qualified",
    "unsupported_inference",
    "overgeneralized_synthesis",
    "result_type_overstatement",
    "validation_level_overstatement",
    "source_mismatch",
    "contradicted",
    "unsupported_fact",
    "unplanned_claim",
)
BLOCKING_RISKS = set(RISK_CATEGORIES) - {"supported", "qualified"}
SENTENCE_PATTERN = re.compile(r"[^。！？!?]+[。！？!?]?")


class ReviewConclusionAuditB2ContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


def derive_conclusion_audit_input(
    conclusion_source: Any,
) -> dict[str, Any]:
    conclusion = conclusion_source.conclusion
    conclusion_input = conclusion_source.conclusion_input
    used_ids = set(conclusion["implemented_source_claim_ids"])
    claims = [
        copy.deepcopy(row)
        for row in conclusion_input["eligible_claims"]
        if row["claim_id"] in used_ids
    ]
    if {row["claim_id"] for row in claims} != used_ids:
        raise ReviewConclusionAuditB2ContractError(
            "conclusion_audit_b2.claim_missing",
            "结论使用的来源Claim无法在冻结输入中完整定位。",
        )
    claim_by_id = {row["claim_id"]: row for row in claims}
    sentences = []
    source_claims_by_paragraph = []
    for paragraph in conclusion["paragraphs"]:
        paragraph_claims = [
            copy.deepcopy(claim_by_id[claim_id])
            for claim_id in paragraph["source_claim_ids"]
        ]
        source_claims_by_paragraph.append(
            {
                "paragraph_index": paragraph["paragraph_index"],
                "group": paragraph["group"],
                "source_claims": paragraph_claims,
            }
        )
        values = [
            value.strip()
            for value in SENTENCE_PATTERN.findall(paragraph["text"])
            if value.strip()
        ]
        for sentence_index, text in enumerate(values, start=1):
            identity = {
                "paragraph_id": paragraph["paragraph_id"],
                "paragraph_index": paragraph["paragraph_index"],
                "sentence_index": sentence_index,
                "text": text,
            }
            sentences.append(
                {
                    **identity,
                    "sentence_id": stable_id("conclusion_sentence_b2", identity),
                    "group": paragraph["group"],
                    "conclusion_claim_type": paragraph["claim_type"],
                    "paragraph_source_claim_ids": copy.deepcopy(
                        paragraph["source_claim_ids"]
                    ),
                    "paragraph_citation_keys": copy.deepcopy(
                        paragraph["citation_keys"]
                    ),
                }
            )
    if not sentences:
        raise ReviewConclusionAuditB2ContractError(
            "conclusion_audit_b2.sentences_empty", "结论没有可审计句子。"
        )
    without_id = {
        "schema_version": CONCLUSION_AUDIT_INPUT_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3h",
        "source": {
            "conclusion_run_id": conclusion_source.run_id,
            "conclusion_manifest_sha256": conclusion_source.manifest_sha256,
            "conclusion_input_id": conclusion_input["conclusion_input_id"],
            "chapter_id": conclusion["chapter_id"],
            "audited_assembly_run_id": conclusion_input["source"][
                "audited_assembly_run_id"
            ],
        },
        "section": {
            "section_id": conclusion["section_id"],
            "section_index": conclusion["section_index"],
            "title": conclusion["title"],
        },
        "sentences": sentences,
        "source_claims_by_paragraph": source_claims_by_paragraph,
        "citation_metadata": copy.deepcopy(conclusion_input["citation_metadata"]),
        "language_policy": copy.deepcopy(conclusion_input["language_policy"]),
    }
    return {
        **without_id,
        "audit_input_id": stable_id("review_conclusion_audit_input_b2", without_id),
    }


def build_conclusion_audit_draft_schema(
    audit_input: dict[str, Any],
    config: ReviewClaimAuditB2Config,
) -> dict[str, Any]:
    all_claim_ids = sorted(
        {
            claim["claim_id"]
            for paragraph in audit_input["source_claims_by_paragraph"]
            for claim in paragraph["source_claims"]
        }
    )
    items = []
    for sentence in audit_input["sentences"]:
        items.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "sentence_id",
                    "risk_labels",
                    "supporting_claim_ids",
                    "reason",
                ],
                "properties": {
                    "sentence_id": {"const": sentence["sentence_id"]},
                    "risk_labels": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": len(RISK_CATEGORIES) - 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "enum": list(RISK_CATEGORIES)},
                    },
                    "supporting_claim_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "enum": all_claim_ids,
                        },
                    },
                    "reason": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": config.max_reason_chars,
                    },
                },
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "sentence_audits"],
        "properties": {
            "schema_version": {"const": CONCLUSION_AUDIT_DRAFT_SCHEMA_VERSION},
            "sentence_audits": {
                "type": "array",
                "minItems": len(items),
                "maxItems": len(items),
                "prefixItems": items,
                "items": False,
            },
        },
    }


def validate_conclusion_audit_draft(
    payload: object,
    *,
    audit_input: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=str)
    if errors:
        first = errors[0]
        path = "$" + "".join(f"[{part!r}]" for part in first.absolute_path)
        raise ReviewConclusionAuditB2ContractError(
            "conclusion_audit_b2.schema_invalid", first.message, path=path
        )
    assert isinstance(payload, dict)
    sentence_by_id = {
        row["sentence_id"]: row for row in audit_input["sentences"]
    }
    audits = []
    counts: Counter[str] = Counter()
    blocking_count = 0
    for index, row in enumerate(payload["sentence_audits"]):
        path = f"$.sentence_audits[{index}]"
        model_labels = row["risk_labels"]
        if "supported" in model_labels and model_labels != ["supported"]:
            raise ReviewConclusionAuditB2ContractError(
                "conclusion_audit_b2.supported_mixed",
                "supported不得与其他风险并存。",
                path=f"{path}.risk_labels",
            )
        if model_labels != sorted(model_labels, key=RISK_CATEGORIES.index):
            raise ReviewConclusionAuditB2ContractError(
                "conclusion_audit_b2.risk_order_invalid",
                "risk_labels必须按合同顺序排列。",
                path=f"{path}.risk_labels",
            )
        if model_labels == ["supported"] and not row["supporting_claim_ids"]:
            raise ReviewConclusionAuditB2ContractError(
                "conclusion_audit_b2.support_missing",
                "supported句必须列出至少一个支持Claim。",
                path=path,
            )
        sentence = sentence_by_id[row["sentence_id"]]
        out_of_paragraph = sorted(
            set(row["supporting_claim_ids"])
            - set(sentence["paragraph_source_claim_ids"])
        )
        labels = list(model_labels)
        if out_of_paragraph:
            labels = [value for value in labels if value != "supported"]
            if "source_mismatch" not in labels:
                labels.append("source_mismatch")
            labels.sort(key=RISK_CATEGORIES.index)
        is_blocking = any(value in BLOCKING_RISKS for value in labels)
        blocking_count += int(is_blocking)
        counts.update(labels)
        audits.append(
            {
                **copy.deepcopy(row),
                "model_risk_labels": copy.deepcopy(model_labels),
                "risk_labels": labels,
                "out_of_paragraph_claim_ids": out_of_paragraph,
                "sentence_text": sentence["text"],
                "paragraph_index": sentence["paragraph_index"],
                "group": sentence["group"],
                "blocking": is_blocking,
            }
        )
    summary = {
        "sentence_count": len(audits),
        "blocking_sentence_count": blocking_count,
        "risk_counts": {name: counts.get(name, 0) for name in RISK_CATEGORIES},
        "publishable": blocking_count == 0,
        "status": "passed" if blocking_count == 0 else "revision_required",
    }
    without_id = {
        "schema_version": CONCLUSION_AUDIT_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3h",
        "audit_input_id": audit_input["audit_input_id"],
        "chapter_id": audit_input["source"]["chapter_id"],
        "sentence_audits": audits,
        "summary": summary,
    }
    return {
        **without_id,
        "audit_id": stable_id("review_conclusion_audit_b2", without_id),
    }
