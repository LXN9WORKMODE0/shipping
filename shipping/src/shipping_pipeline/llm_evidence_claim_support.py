from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator

from .llm_contracts import ContractViolation


EVIDENCE_CLAIM_SUPPORT_SCHEMA_VERSION = "llm.evidence_claim_support_review.v1"
EVIDENCE_CLAIM_SUPPORT_VERDICTS = [
    "directly_supported",
    "partially_supported",
    "unsupported",
]

EVIDENCE_CLAIM_SUPPORT_REVIEW_ITEM: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["evidence_unit_id", "verdict", "reason", "unsupported_fragments"],
    "properties": {
        "evidence_unit_id": {
            "type": "string",
            "pattern": "^evidence_[0-9a-f]{24}$",
        },
        "verdict": {"type": "string", "enum": EVIDENCE_CLAIM_SUPPORT_VERDICTS},
        "reason": {"type": "string", "minLength": 1, "maxLength": 512},
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
                "properties": {"verdict": {"const": "directly_supported"}},
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

EVIDENCE_CLAIM_SUPPORT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://shipping.local/schema/llm.evidence_claim_support_review.v1.json",
    "title": "证据单元观点逐字引文支持核验",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "evidence_claim_reviews"],
    "properties": {
        "schema_version": {"const": EVIDENCE_CLAIM_SUPPORT_SCHEMA_VERSION},
        "evidence_claim_reviews": {
            "type": "array",
            "items": EVIDENCE_CLAIM_SUPPORT_REVIEW_ITEM,
        },
    },
}

EVIDENCE_CLAIM_SUPPORT_SYSTEM_PROMPT = """你是 Evidence Unit 的独立语义支持核验器。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown、解释过程或额外字段。
必须核验输入中的全部 evidence_unit_id，每个 ID 恰好一次，不得改写、删除或合并 Evidence Unit。
只能使用该 Evidence Unit 的 claim、quotes 和 caveats；不得使用外部知识、relevance、同论文其他证据或未提供的上下文。
claim 是待核验命题，quotes 是证据；同一 Evidence Unit 的多条 quotes 必须合并阅读。confidence 不是支持证据。
核验标准是语义蕴含，不是逐字相同，也不要求引文证明论文陈述背后的理论正确性。只判断 claim 实际说出的内容，不得要求 claim 没有声称的数值、步骤细节、表格数据或证明过程。
directly_supported：quotes 合并后直接蕴含 claim 的全部事实、对象、范围、因果、比较、程度、数值和术语含义。
partially_supported：quotes 只支持 claim 的一部分，或 claim 增加了 quotes 没有表达的解释、范围、因果、比较、程度、数值或术语含义。
unsupported：quotes 不能支持 claim 的主要内容。
以下情况应判 directly_supported：
- quotes 使用更强表述而 claim 作保守概括，例如“几乎不可能”支持“很难”。
- 多条 quotes 分别列举若干领域、因素或方法，claim 只把这些已明确列举项合并概括，不增加趋势、主次或因果。
- quote 明确写“步骤如下”或“如表X所示”，claim 只说论文给出了步骤或数据见该表。这类“存在性陈述”必须判 directly_supported，即使当前 quotes 没有继续展示步骤正文或表格数值；只有 claim 复述了未提供的具体步骤或数值时才降低 verdict。
- quote 明确陈述采用某方法、模型、假设或结论，claim 只转述该陈述；不得因 quote 未提供证明、算法细节或外部文献定义而降低 verdict。
- 同义改述、语序变化、中文与英文的等值表达，或不扩大含义的上位概括。
以下情况不得判 directly_supported：quote 只写“拟研究、计划、尝试”而 claim 写成“已构建、已验证、已发现”；或 claim 增加 quotes 没有表达的完成状态、效果、趋势、主次、因果或术语解释。
公式只能直接支持公式的存在、形式和其中可见的符号关系。除非 quotes 明确给出定义或文字解释，否则不得根据公式、变量名或学科常识推断各项经济含义、作用或属性。
caveats 只是定位潜在边界的警示，不是反证，不能推翻 quotes 已明确表达的内容。只有当 caveats 指出的缺失内容确实是 claim 的必要组成部分，且 quotes 未表达该内容时，才据此降低 verdict。
输出前必须先确定最终 verdict，再一次性填写整行：如果最终判断没有任何可逐字摘录的不受支持 claim 片段，verdict 必须是 directly_supported 且 unsupported_fragments 必须为空；如果 verdict 是 partially_supported 或 unsupported，unsupported_fragments 必须非空且 reason 必须明确支持该最终 verdict。
reason 禁止保留初步判断、自我纠正或“经复核、复核后、实际应支持”等推理痕迹，禁止出现 reason 认定直接支持而 verdict 仍为 partially_supported/unsupported 的矛盾。
reason 只写一句最终中文依据，不得超过240个字符，不得整段复制中英文引文，不得写判断过程、自我纠正或候选 verdict。
partially_supported 和 unsupported 必须在 unsupported_fragments 中逐字摘录 claim 内不受支持的最小片段；directly_supported 必须输出空数组。"""

EVIDENCE_CLAIM_CONFIRMATION_SYSTEM_PROMPT = EVIDENCE_CLAIM_SUPPORT_SYSTEM_PROMPT + """

这是对首轮阻断项的独立窄上下文复核。首轮结论只用于说明争点，不是正确答案，也不得因为首轮 verdict 而维持或推翻判断。
你可以阅读被引 Card 与其前后相邻 Card 的完整 extract 来确认术语指代和上下文，但最终 claim 仍必须由 Evidence Unit 自带的逐字 quotes 直接支持；相邻 Card 不能替代 citation。
只核验当前一个 Evidence Unit，并给出一次独立最终结论。"""


def build_evidence_claim_support_schema(evidence_unit_ids: list[str]) -> dict[str, Any]:
    _validate_ids(evidence_unit_ids)
    schema = copy.deepcopy(EVIDENCE_CLAIM_SUPPORT_SCHEMA)
    rows = schema["properties"]["evidence_claim_reviews"]
    rows["minItems"] = len(evidence_unit_ids)
    rows["maxItems"] = len(evidence_unit_ids)
    evidence_id = rows["items"]["properties"]["evidence_unit_id"]
    rows["items"]["properties"]["evidence_unit_id"] = {
        **evidence_id,
        "enum": list(evidence_unit_ids),
    }
    return schema


def build_evidence_claim_support_prompts(
    *,
    paper_id: str,
    paper_title: str,
    evidence_units: list[dict[str, Any]],
) -> tuple[str, str]:
    evidence_ids = [str(row.get("evidence_unit_id", "")) for row in evidence_units]
    _validate_ids(evidence_ids)
    projected = [
        {
            "evidence_unit_id": str(row["evidence_unit_id"]),
            "claim": row.get("claim"),
            "quotes": [
                str(citation.get("quote", "")) for citation in row.get("citations", [])
            ],
            "confidence": row.get("confidence"),
            "caveats": row.get("caveats", []),
        }
        for row in evidence_units
    ]
    prompt_input = {
        "任务": "逐条核验 Evidence Unit 的 claim 是否被其逐字引文完整直接支持",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "输出JSONSchema": build_evidence_claim_support_schema(evidence_ids),
        "待核验证据单元": projected,
    }
    return EVIDENCE_CLAIM_SUPPORT_SYSTEM_PROMPT, json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_evidence_claim_confirmation_prompts(
    *,
    paper_id: str,
    paper_title: str,
    confirmation_context: dict[str, Any],
) -> tuple[str, str]:
    evidence_unit = confirmation_context["evidence_unit"]
    evidence_id = str(evidence_unit.get("evidence_unit_id") or "")
    _validate_ids([evidence_id])
    projected = {
        "evidence_unit_id": evidence_id,
        "claim": evidence_unit.get("claim"),
        "quotes": [
            str(citation.get("quote", ""))
            for citation in evidence_unit.get("citations", [])
        ],
        "confidence": evidence_unit.get("confidence"),
        "caveats": evidence_unit.get("caveats", []),
    }
    prompt_input = {
        "任务": "对首轮 Evidence claim 阻断项执行一次独立窄上下文复核",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "输出JSONSchema": build_evidence_claim_support_schema([evidence_id]),
        "待核验证据单元": projected,
        "首轮核验结论": confirmation_context["first_pass_review"],
        "被引及相邻Card上下文": confirmation_context["context_cards"],
    }
    return EVIDENCE_CLAIM_CONFIRMATION_SYSTEM_PROMPT, json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_evidence_claim_support_review(
    payload: object,
    evidence_units: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_ids = [str(row.get("evidence_unit_id", "")) for row in evidence_units]
    schema = build_evidence_claim_support_schema(evidence_ids)
    validated = _validate_schema(payload, schema)
    observed = [str(row["evidence_unit_id"]) for row in validated["evidence_claim_reviews"]]
    counts = Counter(observed)
    missing = sorted(set(evidence_ids) - set(observed))
    unknown = sorted(set(observed) - set(evidence_ids))
    duplicate = sorted(value for value, count in counts.items() if count > 1)
    if missing or unknown or duplicate:
        raise ContractViolation(
            "coverage.evidence_claim_support_invalid",
            f"missing={missing}, unknown={unknown}, duplicate={duplicate}",
            path="$.evidence_claim_reviews",
        )
    return validated


def enforce_evidence_claim_support_policy(validated_review: dict[str, Any]) -> None:
    for index, review in enumerate(validated_review["evidence_claim_reviews"]):
        verdict = str(review["verdict"])
        if verdict != "directly_supported":
            raise ContractViolation(
                "support.evidence_claim_not_fully_supported",
                f"evidence_unit_id={review['evidence_unit_id']!r}, verdict={verdict!r}, "
                f"unsupported_fragments={review['unsupported_fragments']}",
                path=f"$.evidence_claim_reviews[{index}]",
            )


def blocking_evidence_claim_support_reviews(
    validated_review: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(row)
        for row in validated_review["evidence_claim_reviews"]
        if row["verdict"] != "directly_supported"
    ]


def _validate_ids(evidence_unit_ids: list[str]) -> None:
    if not evidence_unit_ids or any(not isinstance(value, str) or not value for value in evidence_unit_ids):
        raise ValueError("evidence_unit_ids 必须是非空字符串列表。")
    if len(evidence_unit_ids) != len(set(evidence_unit_ids)):
        raise ValueError("evidence_unit_ids 必须唯一。")


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
        raise ContractViolation("schema.evidence_claim_support_invalid", error.message, path=path)
    assert isinstance(payload, dict)
    return copy.deepcopy(payload)
