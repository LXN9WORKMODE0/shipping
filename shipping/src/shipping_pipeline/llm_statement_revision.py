from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator

from .llm_contracts import (
    NON_USED_EVIDENCE_DISPOSITION_ITEM,
    ContractViolation,
    paper_analysis_referenced_evidence_ids,
    validate_non_used_evidence_dispositions,
    validate_paper_analysis,
)
from .llm_statement_support import build_statement_records


STATEMENT_REVISION_SCHEMA_VERSION = "llm.statement_revision.v1"

STATEMENT_REVISION_SYSTEM_PROMPT = """你是论文分析失败陈述的受约束修订器。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown、判断过程或额外解释。
只能处理输入中列出的失败 statement_id，每个 ID 恰好一次；不得修改未列出的陈述、study_type 或证据处置。
允许的动作只有 delete 和 replace。replace 可以把原陈述收窄、改写或拆成多条原子陈述；delete 表示删除该条。research_focus 不允许删除且只能替换为一条。
每条 replacement 只能使用该原陈述已经引用的 evidence_unit_id，可以减少或重新分配，禁止增加其他 evidence ID。
不得增加原陈述和原证据没有表达的新事实、因果、比较、程度、数值、解释或综述判断。优先删除不受支持片段并沿用逐字引文中的术语。
如果修订使原本 used 的 evidence 不再被任何陈述引用，必须在 released_evidence_dispositions 中逐条处置；只能使用 redundant/duplicate_support、peripheral/background_only、peripheral/supporting_detail、peripheral/low_confidence、excluded/low_confidence 或 excluded/off_topic。
released_evidence_dispositions 只能包含输入列出的可能释放证据，且必须与修订后的实际释放集合完全一致。禁止把释放证据标为 used。
这是单次修订，不得要求再次改写，也不得在输出中保留候选方案。"""


def build_statement_revision_schema(
    blocking_records: list[dict[str, Any]],
    potentially_released_evidence_ids: list[str],
) -> dict[str, Any]:
    if not blocking_records:
        raise ValueError("blocking_records 不能为空。")
    statement_ids = [str(row["statement_id"]) for row in blocking_records]
    if len(statement_ids) != len(set(statement_ids)):
        raise ValueError("blocking_records 的 statement_id 必须唯一。")
    revision_variants = [_revision_variant(record) for record in blocking_records]
    released_item = copy.deepcopy(NON_USED_EVIDENCE_DISPOSITION_ITEM)
    released_item["properties"]["evidence_unit_id"] = {
        "type": "string",
        "enum": list(potentially_released_evidence_ids),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.statement_revision.v1.json",
        "title": "失败陈述单次受约束修订",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "revisions", "released_evidence_dispositions"],
        "properties": {
            "schema_version": {"const": STATEMENT_REVISION_SCHEMA_VERSION},
            "revisions": {
                "type": "array",
                "minItems": len(blocking_records),
                "maxItems": len(blocking_records),
                "items": {"oneOf": revision_variants},
            },
            "released_evidence_dispositions": {
                "type": "array",
                "maxItems": len(potentially_released_evidence_ids),
                "items": released_item,
            },
        },
    }


def build_statement_revision_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    original_analysis: dict[str, Any],
    blocking_records: list[dict[str, Any]],
    evidence_units: list[dict[str, Any]],
) -> tuple[str, str]:
    all_records = build_statement_records(original_analysis)
    blocking_ids = {str(row["statement_id"]) for row in blocking_records}
    unchanged_evidence_ids = sorted(
        {
            str(evidence_id)
            for record in all_records
            if str(record["statement_id"]) not in blocking_ids
            for evidence_id in record["evidence_unit_ids"]
        }
    )
    potentially_released = _potentially_released_evidence_ids(
        original_analysis,
        blocking_records,
    )
    evidence_by_id = {str(row["evidence_unit_id"]): row for row in evidence_units}
    required_evidence_ids = sorted(
        {
            str(evidence_id)
            for record in blocking_records
            for evidence_id in record["evidence_unit_ids"]
        }
    )
    missing = sorted(set(required_evidence_ids) - set(evidence_by_id))
    if missing:
        raise ContractViolation(
            "input.statement_revision_evidence_missing",
            f"修订输入缺少证据：{missing}",
            path="$.evidence_units",
        )
    projected_evidence = [
        {
            "evidence_unit_id": evidence_id,
            "claim": evidence_by_id[evidence_id].get("claim"),
            "quotes": [
                str(citation.get("quote", ""))
                for citation in evidence_by_id[evidence_id].get("citations", [])
            ],
            "confidence": evidence_by_id[evidence_id].get("confidence"),
            "caveats": evidence_by_id[evidence_id].get("caveats", []),
        }
        for evidence_id in required_evidence_ids
    ]
    prompt_input = {
        "任务": "单次删除、拆分或收窄被证据支持核验拒绝的论文分析陈述",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "综述主题": topic,
        "输出JSONSchema": build_statement_revision_schema(
            blocking_records,
            potentially_released,
        ),
        "失败陈述及核验意见": blocking_records,
        "失败陈述已引用证据": projected_evidence,
        "未修改陈述仍引用的证据ID": unchanged_evidence_ids,
        "可能因修订释放的证据ID": potentially_released,
    }
    return STATEMENT_REVISION_SYSTEM_PROMPT, json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_and_apply_statement_revision(
    payload: object,
    *,
    original_analysis: dict[str, Any],
    blocking_records: list[dict[str, Any]],
    evidence_units: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    potentially_released = _potentially_released_evidence_ids(
        original_analysis,
        blocking_records,
    )
    validated = _validate_schema(
        payload,
        build_statement_revision_schema(blocking_records, potentially_released),
    )
    expected_ids = {str(row["statement_id"]) for row in blocking_records}
    observed = [str(row["statement_id"]) for row in validated["revisions"]]
    counts = Counter(observed)
    missing = sorted(expected_ids - set(observed))
    unknown = sorted(set(observed) - expected_ids)
    duplicate = sorted(statement_id for statement_id, count in counts.items() if count > 1)
    if missing or unknown or duplicate:
        raise ContractViolation(
            "coverage.statement_revision_invalid",
            f"missing={missing}, unknown={unknown}, duplicate={duplicate}",
            path="$.revisions",
        )

    revised = _apply_revisions(original_analysis, validated["revisions"])
    original_referenced = paper_analysis_referenced_evidence_ids(original_analysis)
    revised_referenced = paper_analysis_referenced_evidence_ids(revised)
    released_ids = sorted(original_referenced - revised_referenced)
    provided_release_rows = validated["released_evidence_dispositions"]
    provided_release_ids = [str(row["evidence_unit_id"]) for row in provided_release_rows]
    if len(provided_release_ids) != len(set(provided_release_ids)) or set(provided_release_ids) != set(
        released_ids
    ):
        raise ContractViolation(
            "coverage.released_evidence_disposition_invalid",
            f"expected={released_ids}, observed={sorted(provided_release_ids)}",
            path="$.released_evidence_dispositions",
        )
    if released_ids:
        validate_non_used_evidence_dispositions(
            {
                "schema_version": "llm.non_used_evidence_disposition.v1",
                "non_used_evidence_dispositions": provided_release_rows,
            },
            set(released_ids),
        )

    old_non_used = {
        str(row["evidence_unit_id"]): copy.deepcopy(row)
        for row in original_analysis["evidence_dispositions"]
        if row["disposition"] != "used"
    }
    released_by_id = {
        str(row["evidence_unit_id"]): copy.deepcopy(row) for row in provided_release_rows
    }
    evidence_ids = [str(row["evidence_unit_id"]) for row in evidence_units]
    revised["evidence_dispositions"] = [
        (
            {
                "evidence_unit_id": evidence_id,
                "disposition": "used",
                "reason_code": "supports_claim",
            }
            if evidence_id in revised_referenced
            else old_non_used.get(evidence_id) or released_by_id[evidence_id]
        )
        for evidence_id in evidence_ids
    ]
    validated_analysis = validate_paper_analysis(
        revised,
        set(evidence_ids),
        evidence_units=evidence_units,
    )
    return validated, validated_analysis, released_ids


def _revision_variant(record: dict[str, Any]) -> dict[str, Any]:
    original_evidence_ids = [str(value) for value in record["evidence_unit_ids"]]
    is_object_field = record["index"] is None
    actions = ["replace"] if is_object_field else ["delete", "replace"]
    replacement = {
        "type": "object",
        "additionalProperties": False,
        "required": ["statement", "evidence_unit_ids"],
        "properties": {
            "statement": {"type": "string", "minLength": 1, "maxLength": 320},
            "evidence_unit_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "enum": original_evidence_ids},
            },
        },
    }
    variant: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["statement_id", "action", "replacements"],
        "properties": {
            "statement_id": {"const": str(record["statement_id"])},
            "action": {"type": "string", "enum": actions},
            "replacements": {
                "type": "array",
                "uniqueItems": True,
                "maxItems": 1 if is_object_field else 4,
                "items": replacement,
            },
        },
    }
    if is_object_field:
        variant["properties"]["replacements"]["minItems"] = 1
    else:
        variant["allOf"] = [
            {
                "if": {"properties": {"action": {"const": "delete"}}, "required": ["action"]},
                "then": {"properties": {"replacements": {"maxItems": 0}}},
            },
            {
                "if": {"properties": {"action": {"const": "replace"}}, "required": ["action"]},
                "then": {"properties": {"replacements": {"minItems": 1}}},
            },
        ]
    return variant


def _potentially_released_evidence_ids(
    original_analysis: dict[str, Any],
    blocking_records: list[dict[str, Any]],
) -> list[str]:
    blocking_ids = {str(row["statement_id"]) for row in blocking_records}
    all_records = build_statement_records(original_analysis)
    unchanged_references = {
        str(evidence_id)
        for record in all_records
        if str(record["statement_id"]) not in blocking_ids
        for evidence_id in record["evidence_unit_ids"]
    }
    return sorted(
        {
            str(evidence_id)
            for record in blocking_records
            for evidence_id in record["evidence_unit_ids"]
            if str(evidence_id) not in unchanged_references
        }
    )


def _apply_revisions(
    original_analysis: dict[str, Any],
    revisions: list[dict[str, Any]],
) -> dict[str, Any]:
    revised = copy.deepcopy(original_analysis)
    records = build_statement_records(original_analysis)
    revisions_by_id = {str(row["statement_id"]): row for row in revisions}
    object_records = {str(row["field"]): row for row in records if row["index"] is None}
    for field, record in object_records.items():
        revision = revisions_by_id.get(str(record["statement_id"]))
        if revision is not None:
            replacement = revision["replacements"][0]
            if replacement == original_analysis[field]:
                raise ContractViolation(
                    "revision.statement_unchanged",
                    f"statement_id={record['statement_id']} 未发生变化",
                    path="$.revisions",
                )
            revised[field] = copy.deepcopy(replacement)

    for field in {str(row["field"]) for row in records if row["index"] is not None}:
        field_records = sorted(
            (row for row in records if row["field"] == field),
            key=lambda row: int(row["index"]),
        )
        output: list[dict[str, Any]] = []
        for record in field_records:
            original_item = original_analysis[field][int(record["index"])]
            revision = revisions_by_id.get(str(record["statement_id"]))
            if revision is None:
                output.append(copy.deepcopy(original_item))
                continue
            replacements = revision["replacements"]
            if revision["action"] == "replace" and replacements == [original_item]:
                raise ContractViolation(
                    "revision.statement_unchanged",
                    f"statement_id={record['statement_id']} 未发生变化",
                    path="$.revisions",
                )
            output.extend(copy.deepcopy(replacements))
        revised[field] = output
    return revised


def _validate_schema(payload: object, schema: dict[str, Any]) -> dict[str, Any]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path
        )
        raise ContractViolation("schema.statement_revision_invalid", error.message, path=path)
    assert isinstance(payload, dict)
    return copy.deepcopy(payload)
