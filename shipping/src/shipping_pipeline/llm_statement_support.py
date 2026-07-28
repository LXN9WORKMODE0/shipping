from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator

from .llm_contracts import (
    PAPER_REFERENCE_ARRAY_FIELDS,
    PAPER_REFERENCE_OBJECT_FIELDS,
    ContractViolation,
)


STATEMENT_SUPPORT_SCHEMA_VERSION = "llm.statement_support_review.v1"
SUPPORT_VERDICTS = [
    "directly_supported",
    "grounded_inference",
    "partially_supported",
    "unsupported",
]

STATEMENT_SUPPORT_REVIEW_ITEM: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["statement_id", "verdict", "reason", "unsupported_fragments"],
    "properties": {
        "statement_id": {"type": "string", "pattern": "^statement_[0-9a-f]{24}$"},
        "verdict": {"type": "string", "enum": SUPPORT_VERDICTS},
        "reason": {"type": "string", "minLength": 1, "maxLength": 320},
        "unsupported_fragments": {
            "type": "array",
            "maxItems": 4,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
        },
    },
    "allOf": [
        {
            "if": {
                "properties": {
                    "verdict": {"enum": ["directly_supported", "grounded_inference"]}
                },
                "required": ["verdict"],
            },
            "then": {"properties": {"unsupported_fragments": {"maxItems": 0}}},
        },
        {
            "if": {
                "properties": {
                    "verdict": {"enum": ["partially_supported", "unsupported"]}
                },
                "required": ["verdict"],
            },
            "then": {"properties": {"unsupported_fragments": {"minItems": 1}}},
        },
    ],
}

STATEMENT_SUPPORT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://shipping.local/schema/llm.statement_support_review.v1.json",
    "title": "论文分析逐条陈述证据支持核验",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "statement_reviews"],
    "properties": {
        "schema_version": {"const": STATEMENT_SUPPORT_SCHEMA_VERSION},
        "statement_reviews": {
            "type": "array",
            "items": STATEMENT_SUPPORT_REVIEW_ITEM,
        },
    },
}

STATEMENT_SUPPORT_SYSTEM_PROMPT = """你是论文分析陈述的独立证据支持核验器。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。
只能依据输入中的 statement、其列出的 evidence_unit_id、证据观点和逐字引文判断，不得使用外部知识或同论文其他未列证据。
必须逐条核验所有 statement_id，每个 ID 恰好一次，不得改写陈述或证据身份。
先在内部完成判断，再一次性填写最终 JSON；reason 只能写最终依据的一句中文，建议不超过120个汉字，不得写判断过程、候选 verdict、规则复述、自我纠正或英文元话语。
verdict 决策表必须严格遵守：required_support=direct 时只能选择 directly_supported、partially_supported 或 unsupported，禁止选择 grounded_inference；required_support=grounded_inference_allowed 时才可选择 grounded_inference。
选定 verdict 后不得在 reason 中改判。directly_supported 或 grounded_inference 的 unsupported_fragments 必须为空；partially_supported 或 unsupported 必须列出不受支持片段。
证据观点 claim 是对逐字引文的待核验概括，逐字引文是主要依据，caveats 用于指出支持边界。claim 与引文语义一致、caveats 未质疑该命题时，可以共同形成直接支持；不得因为引文没有逐字重复 claim 的动词就降级，例如引文明示“模型使用 Frank-wolf 法”时，“使用 Frank-wolf 法求解”属于不扩大范围的直接改述。
如果 caveats 明确指出某项解释需要结合未提供的上下文，或逐字引文只有公式而没有解释各项含义，则不能仅凭 claim 把该解释判为直接支持。
directly_supported：陈述的全部事实、范围、因果、比较、程度和数值都被所列证据直接支持。
directly_supported 不要求逐字相同；不扩大对象、范围、程度或因果强度的等值改述和逻辑蕴含仍属于直接支持，例如证据明确给出“3种情景”时，陈述概括为“多种情景”可判为直接支持。
grounded_inference：全部事实前提被直接支持，但最终用途或解释属于明确可识别的综述推论；只有 required_support=grounded_inference_allowed 时才可使用。
partially_supported：陈述只有部分内容被支持，或增加了证据没有直接表达的范围、因果、程度、缺失判断或事实片段。
unsupported：所列证据不能支持陈述的主要内容。
不得把增加具体性或扩大术语范围当作等值改述，例如证据仅写“小船”时补充具体吨位，或把“运价”扩为一般“运输成本”，都应判为 partially_supported。
对 partially_supported 和 unsupported，unsupported_fragments 必须逐字摘录陈述中不受支持的最小片段；其他 verdict 必须输出空数组。"""


def build_statement_records(paper_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for field in PAPER_REFERENCE_OBJECT_FIELDS:
        records.append(_statement_record(field, None, paper_analysis[field]))
    for field in PAPER_REFERENCE_ARRAY_FIELDS:
        for index, item in enumerate(paper_analysis[field]):
            records.append(_statement_record(field, index, item))
    statement_ids = [row["statement_id"] for row in records]
    duplicates = sorted(
        statement_id for statement_id, count in Counter(statement_ids).items() if count > 1
    )
    if duplicates:
        raise ContractViolation(
            "identity.duplicate_statement_id",
            f"论文分析存在重复 statement 身份：{duplicates}",
            path="$.analysis",
        )
    return records


def build_statement_support_schema(statement_ids: list[str]) -> dict[str, Any]:
    if not statement_ids or any(not isinstance(value, str) or not value for value in statement_ids):
        raise ValueError("statement_ids 必须是非空字符串列表。")
    if len(statement_ids) != len(set(statement_ids)):
        raise ValueError("statement_ids 必须唯一。")
    schema = copy.deepcopy(STATEMENT_SUPPORT_SCHEMA)
    rows = schema["properties"]["statement_reviews"]
    rows["minItems"] = len(statement_ids)
    rows["maxItems"] = len(statement_ids)
    statement_id = rows["items"]["properties"]["statement_id"]
    rows["items"]["properties"]["statement_id"] = {
        **statement_id,
        "enum": list(statement_ids),
    }
    return schema


def build_statement_support_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    statement_records: list[dict[str, Any]],
    evidence_units: list[dict[str, Any]],
) -> tuple[str, str]:
    evidence_by_id = {str(unit["evidence_unit_id"]): unit for unit in evidence_units}
    referenced_ids = {
        str(evidence_id)
        for record in statement_records
        for evidence_id in record["evidence_unit_ids"]
    }
    missing = sorted(referenced_ids - set(evidence_by_id))
    if missing:
        raise ContractViolation(
            "input.statement_evidence_missing",
            f"statement 核验缺少证据：{missing}",
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
        for evidence_id in sorted(referenced_ids)
    ]
    statement_ids = [str(record["statement_id"]) for record in statement_records]
    prompt_input = {
        "任务": "逐条核验论文分析陈述是否被其所列证据完整支持",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "综述主题": topic,
        "输出JSONSchema": build_statement_support_schema(statement_ids),
        "待核验陈述": statement_records,
        "已引用证据": projected_evidence,
    }
    return STATEMENT_SUPPORT_SYSTEM_PROMPT, json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_statement_support_review(
    payload: object,
    statement_records: list[dict[str, Any]],
) -> dict[str, Any]:
    statement_ids = [str(record["statement_id"]) for record in statement_records]
    schema = build_statement_support_schema(statement_ids)
    validated = _validate_schema(payload, schema)
    observed = [str(row["statement_id"]) for row in validated["statement_reviews"]]
    counts = Counter(observed)
    missing = sorted(set(statement_ids) - set(observed))
    unknown = sorted(set(observed) - set(statement_ids))
    duplicate = sorted(statement_id for statement_id, count in counts.items() if count > 1)
    if missing or unknown or duplicate:
        raise ContractViolation(
            "coverage.statement_support_invalid",
            f"missing={missing}, unknown={unknown}, duplicate={duplicate}",
            path="$.statement_reviews",
        )
    return validated


def enforce_statement_support_policy(
    validated_review: dict[str, Any],
    statement_records: list[dict[str, Any]],
) -> None:
    records_by_id = {str(row["statement_id"]): row for row in statement_records}
    for index, review in enumerate(validated_review["statement_reviews"]):
        record = records_by_id[str(review["statement_id"])]
        verdict = str(review["verdict"])
        if verdict in {"partially_supported", "unsupported"}:
            raise ContractViolation(
                "support.statement_not_fully_supported",
                f"field={record['field']!r}, verdict={verdict!r}, "
                f"unsupported_fragments={review['unsupported_fragments']}",
                path=f"$.statement_reviews[{index}]",
            )
        if record["required_support"] == "direct" and verdict != "directly_supported":
            raise ContractViolation(
                "support.direct_statement_only_inferred",
                f"field={record['field']!r} 要求直接支持，实际 verdict={verdict!r}",
                path=f"$.statement_reviews[{index}]",
            )


def blocking_statement_support_reviews(
    validated_review: dict[str, Any],
    statement_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reviews_by_id = {
        str(row["statement_id"]): row for row in validated_review["statement_reviews"]
    }
    blocking: list[dict[str, Any]] = []
    for record in statement_records:
        review = reviews_by_id[str(record["statement_id"])]
        verdict = str(review["verdict"])
        if verdict in {"partially_supported", "unsupported"} or (
            record["required_support"] == "direct" and verdict != "directly_supported"
        ):
            blocking.append({**record, **review})
    return blocking


def _statement_record(field: str, index: int | None, item: dict[str, Any]) -> dict[str, Any]:
    statement = str(item["statement"])
    evidence_ids = [str(value) for value in item["evidence_unit_ids"]]
    identity = {
        "field": field,
        "statement": " ".join(statement.split()),
        "evidence_unit_ids": sorted(evidence_ids),
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return {
        "statement_id": f"statement_{digest}",
        "field": field,
        "index": index,
        "required_support": (
            "grounded_inference_allowed" if field == "review_uses" else "direct"
        ),
        "statement": statement,
        "evidence_unit_ids": evidence_ids,
    }


def _validate_schema(payload: object, schema: dict[str, Any]) -> dict[str, Any]:
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path
        )
        raise ContractViolation("schema.statement_support_invalid", error.message, path=path)
    assert isinstance(payload, dict)
    return copy.deepcopy(payload)
