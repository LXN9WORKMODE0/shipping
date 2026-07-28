from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator

from .llm_contracts import ContractViolation


STATEMENT_ROLE_SCHEMA_VERSION = "llm.statement_role_review.v1"
STATEMENT_ROLE_VERDICTS = ["field_aligned", "misclassified"]

STATEMENT_ROLE_REVIEW_ITEM: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["statement_id", "verdict", "reason", "misaligned_fragments"],
    "properties": {
        "statement_id": {"type": "string", "pattern": "^statement_[0-9a-f]{24}$"},
        "verdict": {"type": "string", "enum": STATEMENT_ROLE_VERDICTS},
        "reason": {"type": "string", "minLength": 1, "maxLength": 320},
        "misaligned_fragments": {
            "type": "array",
            "maxItems": 4,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
        },
    },
    "allOf": [
        {
            "if": {
                "properties": {"verdict": {"const": "field_aligned"}},
                "required": ["verdict"],
            },
            "then": {"properties": {"misaligned_fragments": {"maxItems": 0}}},
        },
        {
            "if": {
                "properties": {"verdict": {"const": "misclassified"}},
                "required": ["verdict"],
            },
            "then": {"properties": {"misaligned_fragments": {"minItems": 1}}},
        },
    ],
}

STATEMENT_ROLE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://shipping.local/schema/llm.statement_role_review.v1.json",
    "title": "论文分析陈述字段职责核验",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "statement_role_reviews"],
    "properties": {
        "schema_version": {"const": STATEMENT_ROLE_SCHEMA_VERSION},
        "statement_role_reviews": {
            "type": "array",
            "items": STATEMENT_ROLE_REVIEW_ITEM,
        },
    },
}

STATEMENT_ROLE_SYSTEM_PROMPT = """你是论文分析字段职责的独立核验器。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown、改写建议、解释过程或额外字段。
必须核验输入中的全部 statement_id，每个 ID 恰好一次；不得移动、删除或改写陈述。
本阶段只判断陈述是否履行当前 field 的职责，不判断证据是否支持陈述。
字段职责如下：
- research_focus：论文直接研究的对象、问题或目标。
- methods：论文实际采用的研究设计、数据、模型、算法、变量、步骤或实验设置。
- core_findings：论文报告的结果、发现、比较或结论，不是方法过程。对于政策分析、SWOT分析和案例研究，作者明确纳入分析并用于构成优势、劣势、机会、威胁、现状判断或对策依据的政策条件、市场预测和外部约束，也履行 core_findings 职责；与论文分析没有明确关系的一般背景不履行该职责。
- limitations：论文或证据明确表达的研究限制、适用边界或数据/方法约束，不是一般背景问题；带有“只、仅、限于、不考虑”等收窄范围的模型假设可以履行 limitations 职责，即使它同时也是方法设定。
- review_uses：明确说明该论文的事实、方法或结果怎样服务当前综述主题，例如作为现状证据、方法比较、机制解释、反例或政策讨论；只复述“论文构建了什么、发现了什么”而不说明综述用途，必须判为 misclassified。
- unresolved_questions：论文或证据明确提出的尚待回答问题；不能把模型未覆盖内容自行改写成开放问题。
字段职责不是互斥分类。同一陈述可能同时具有方法与限制、发现与综述用途等属性；只要它确实履行当前 field 的职责，就判 field_aligned，不得仅因它也适合其他字段而判错位。
field_aligned：整条陈述确实履行当前字段职责，即使同时具有其他字段属性。
misclassified：整条或部分陈述不履行当前字段职责，或在 review_uses 中只复述论文事实而没有综述用途。
校准示例：limitations 中“模型假设天气只影响水运和港口时间”明确收窄天气影响范围，应判 field_aligned；SWOT分析将国家扶持政策或需求预测作为发展机会时，即使陈述表现为政策事实或未来预测，也应判 core_findings 的 field_aligned；review_uses 中“论文构建了三个模型”没有说明如何服务综述，应判 misclassified。
reason 只写一句最终中文依据，建议不超过120个汉字，不得写判断过程或候选 verdict。
misclassified 必须在 misaligned_fragments 中逐字摘录陈述内职责错位的最小片段；field_aligned 必须输出空数组。"""


def build_statement_role_schema(statement_ids: list[str]) -> dict[str, Any]:
    _validate_ids(statement_ids)
    schema = copy.deepcopy(STATEMENT_ROLE_SCHEMA)
    rows = schema["properties"]["statement_role_reviews"]
    rows["minItems"] = len(statement_ids)
    rows["maxItems"] = len(statement_ids)
    statement_id = rows["items"]["properties"]["statement_id"]
    rows["items"]["properties"]["statement_id"] = {
        **statement_id,
        "enum": list(statement_ids),
    }
    return schema


def build_statement_role_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    study_type: str,
    research_focus: str,
    statement_records: list[dict[str, Any]],
) -> tuple[str, str]:
    statement_ids = [str(row.get("statement_id", "")) for row in statement_records]
    _validate_ids(statement_ids)
    projected = [
        {
            "statement_id": str(row["statement_id"]),
            "field": str(row["field"]),
            "statement": str(row["statement"]),
        }
        for row in statement_records
    ]
    prompt_input = {
        "任务": "逐条核验论文分析陈述是否履行其所在字段的职责",
        "论文": {
            "paper_id": paper_id,
            "paper_title": paper_title,
            "study_type": study_type,
            "research_focus": research_focus,
        },
        "综述主题": topic,
        "输出JSONSchema": build_statement_role_schema(statement_ids),
        "待核验陈述": projected,
    }
    return STATEMENT_ROLE_SYSTEM_PROMPT, json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_statement_role_review(
    payload: object,
    statement_records: list[dict[str, Any]],
) -> dict[str, Any]:
    statement_ids = [str(row.get("statement_id", "")) for row in statement_records]
    schema = build_statement_role_schema(statement_ids)
    validated = _validate_schema(payload, schema)
    observed = [str(row["statement_id"]) for row in validated["statement_role_reviews"]]
    counts = Counter(observed)
    missing = sorted(set(statement_ids) - set(observed))
    unknown = sorted(set(observed) - set(statement_ids))
    duplicate = sorted(value for value, count in counts.items() if count > 1)
    if missing or unknown or duplicate:
        raise ContractViolation(
            "coverage.statement_role_invalid",
            f"missing={missing}, unknown={unknown}, duplicate={duplicate}",
            path="$.statement_role_reviews",
        )
    return validated


def enforce_statement_role_policy(validated_review: dict[str, Any]) -> None:
    for index, review in enumerate(validated_review["statement_role_reviews"]):
        if review["verdict"] != "field_aligned":
            raise ContractViolation(
                "role.statement_misclassified",
                f"statement_id={review['statement_id']!r}, "
                f"misaligned_fragments={review['misaligned_fragments']}",
                path=f"$.statement_role_reviews[{index}]",
            )


def blocking_statement_role_reviews(validated_review: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(row)
        for row in validated_review["statement_role_reviews"]
        if row["verdict"] != "field_aligned"
    ]


def _validate_ids(statement_ids: list[str]) -> None:
    if not statement_ids or any(not isinstance(value, str) or not value for value in statement_ids):
        raise ValueError("statement_ids 必须是非空字符串列表。")
    if len(statement_ids) != len(set(statement_ids)):
        raise ValueError("statement_ids 必须唯一。")


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
        raise ContractViolation("schema.statement_role_invalid", error.message, path=path)
    assert isinstance(payload, dict)
    return copy.deepcopy(payload)
