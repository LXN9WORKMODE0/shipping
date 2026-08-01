from __future__ import annotations

import copy
import math
import re
from typing import Any

from jsonschema import Draft202012Validator

from .source_window_contracts import stable_id


WRITING_INPUT_SCHEMA_VERSION = "llm.review_writing_input_b2.v1"
CHAPTER_DRAFT_SCHEMA_VERSION = "llm.review_chapter_b2_draft.v1"
CHAPTER_SCHEMA_VERSION = "llm.review_chapter_b2.v1"
WRITING_POLICY = {
    "schema_version": "llm.review_writing_b2_policy.v1",
    "min_target_ratio": 0.60,
    "max_target_ratio": 1.20,
    "min_paragraphs": 2,
    "max_core_claims_per_paragraph": 2,
    "max_claims_per_paragraph": 4,
    "claim_text_expansion_ratio": 1.20,
    "paragraph_min_share_ratio": 0.60,
    "introduction_min_chars_floor": 300,
    "body_min_chars_floor": 400,
}
MACHINE_ID_PATTERN = re.compile(
    r"(?i)(?:ref|evidence|material|contribution|package|section|chapter|"
    r"paragraph|card|claim|window)[_-][a-z0-9][a-z0-9_-]{5,}"
)


class ReviewWritingB2ContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


def derive_writing_input(
    package: dict[str, Any],
    framework_b2: dict[str, Any],
    *,
    package_run_id: str,
    package_manifest_sha256: str,
    max_paragraph_chars: int,
) -> dict[str, Any]:
    section = package["section_task"]
    framework_section = _matching_section(framework_b2, section["section_id"])
    if framework_section != section:
        raise ReviewWritingB2ContractError(
            "writing_b2.framework_section_mismatch",
            "知识包章节任务与Framework B2不一致。",
        )
    if (
        package.get("claim_planning_ready") is not True
        or package.get("writing_ready") is not False
    ):
        raise ReviewWritingB2ContractError(
            "writing_b2.package_state_invalid",
            "只接受Claim规划完成、尚未写作的Phase 2知识包。",
        )
    target_chars = int(section["target_chars"])
    section_type = section["section_type"]
    writing_ready = section_type in {"introduction", "body"}
    supporting_count = sum(
        row["importance"] == "supporting"
        for row in package["approved_claim_ledger"]["approved_claims"]
    )
    minimum_supporting = min(
        2 if section_type == "body" else 1,
        supporting_count,
    )
    selected_claims = _selected_claims_for_plan(
        package["approved_claim_ledger"]["approved_claims"],
        minimum_supporting,
    )
    planned_claim_chars = sum(
        _content_chars(row["planned_claim"])
        for row in selected_claims
    )
    target_ratio_limit = math.floor(
        target_chars * WRITING_POLICY["min_target_ratio"]
    )
    section_floor = WRITING_POLICY[
        "body_min_chars_floor"
        if section_type == "body"
        else "introduction_min_chars_floor"
    ]
    density_minimum = math.ceil(
        planned_claim_chars
        * WRITING_POLICY["claim_text_expansion_ratio"]
    )
    constraints = {
        "target_chars": target_chars,
        "min_chars": min(
            target_ratio_limit,
            max(section_floor, density_minimum),
        ),
        "max_chars": math.ceil(
            target_chars * WRITING_POLICY["max_target_ratio"]
        ),
        "min_paragraphs": WRITING_POLICY["min_paragraphs"],
        "max_paragraphs": int(section["max_paragraphs"]),
        "max_paragraph_chars": max_paragraph_chars,
        "max_core_claims_per_paragraph": WRITING_POLICY[
            "max_core_claims_per_paragraph"
        ],
        "max_claims_per_paragraph": WRITING_POLICY[
            "max_claims_per_paragraph"
        ],
        "min_supporting_claims": minimum_supporting,
        "planned_claim_chars": planned_claim_chars,
        "length_policy": "claim_density_capped_by_framework_target",
    }
    if constraints["max_paragraphs"] < constraints["min_paragraphs"]:
        raise ReviewWritingB2ContractError(
            "writing_b2.paragraph_budget_invalid",
            "章节最大段落数小于B2最小段落数。",
        )
    outline = {
        "framework_b2_id": framework_b2["framework_id"],
        "working_title": framework_b2["working_title"],
        "central_question": framework_b2["central_question"],
        "sections": [
            {
                key: copy.deepcopy(row[key])
                for key in (
                    "section_index",
                    "section_id",
                    "title",
                    "section_type",
                )
            }
            for row in framework_b2["sections"]
        ],
    }
    paragraph_plan = _build_paragraph_plan(
        package["approved_claim_ledger"]["approved_claims"],
        constraints,
    )
    value_without_id = {
        "schema_version": WRITING_INPUT_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3a",
        "writing_ready": writing_ready,
        "not_ready_reason": (
            None
            if writing_ready
            else "conclusion_requires_audited_core_claims"
        ),
        "source": {
            "package_run_id": package_run_id,
            "package_id": package["package_id"],
            "package_manifest_sha256": package_manifest_sha256,
            "claim_ledger_run_id": package["source"][
                "claim_ledger_run_id"
            ],
            "claim_ledger_id": package["source"]["claim_ledger_id"],
            "framework_b2_id": framework_b2["framework_id"],
        },
        "framework_outline": outline,
        "section_task": copy.deepcopy(section),
        "writing_constraints": constraints,
        "understanding_projections": copy.deepcopy(
            package["understanding_projections"]
        ),
        "selected_source_windows": copy.deepcopy(
            package["selected_source_windows"]
        ),
        "citation_metadata": copy.deepcopy(package["citation_metadata"]),
        "approved_claims": copy.deepcopy(
            package["approved_claim_ledger"]["approved_claims"]
        ),
        "required_core_claim_ids": sorted(
            row["claim_id"]
            for row in package["approved_claim_ledger"]["approved_claims"]
            if row["importance"] == "core"
        ),
        "optional_supporting_claim_ids": sorted(
            row["claim_id"]
            for row in package["approved_claim_ledger"]["approved_claims"]
            if row["importance"] == "supporting"
        ),
        "paragraph_plan": paragraph_plan,
    }
    value = {
        **value_without_id,
        "writing_input_id": stable_id("writing_input_b2", value_without_id),
    }
    return validate_writing_input(value)


def validate_writing_input(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReviewWritingB2ContractError(
            "writing_b2.input_invalid",
            "B2写作输入必须是对象。",
        )
    required = {
        "schema_version",
        "pipeline_generation",
        "writing_ready",
        "not_ready_reason",
        "source",
        "framework_outline",
        "section_task",
        "writing_constraints",
        "understanding_projections",
        "selected_source_windows",
        "citation_metadata",
        "approved_claims",
        "required_core_claim_ids",
        "optional_supporting_claim_ids",
        "paragraph_plan",
        "writing_input_id",
    }
    if set(payload) != required:
        raise ReviewWritingB2ContractError(
            "writing_b2.input_fields_invalid",
            "B2写作输入字段集合不匹配。",
        )
    if (
        payload.get("schema_version") != WRITING_INPUT_SCHEMA_VERSION
        or payload.get("pipeline_generation") != "B2.phase3a"
    ):
        raise ReviewWritingB2ContractError(
            "writing_b2.input_generation_invalid",
            "B2写作输入代际无效。",
        )
    section_type = payload["section_task"].get("section_type")
    expected_ready = section_type in {"introduction", "body"}
    if payload.get("writing_ready") is not expected_ready:
        raise ReviewWritingB2ContractError(
            "writing_b2.input_ready_invalid",
            "写作就绪状态与章节类型不一致。",
        )
    claim_ids = [row.get("claim_id") for row in payload["approved_claims"]]
    if not claim_ids or any(not isinstance(value, str) for value in claim_ids):
        raise ReviewWritingB2ContractError(
            "writing_b2.claims_invalid",
            "写作输入必须包含带有效ID的Approved Claim。",
        )
    if len(claim_ids) != len(set(claim_ids)):
        raise ReviewWritingB2ContractError(
            "writing_b2.claim_ids_duplicate",
            "Approved Claim ID不得重复。",
        )
    expected_core = sorted(
        row["claim_id"]
        for row in payload["approved_claims"]
        if row["importance"] == "core"
    )
    expected_supporting = sorted(set(claim_ids) - set(expected_core))
    if (
        payload["required_core_claim_ids"] != expected_core
        or payload["optional_supporting_claim_ids"] != expected_supporting
    ):
        raise ReviewWritingB2ContractError(
            "writing_b2.claim_priority_invalid",
            "核心与可选支持Claim目录无法由Approved Claim重建。",
        )
    expected_plan = _build_paragraph_plan(
        payload["approved_claims"],
        payload["writing_constraints"],
    )
    if payload["paragraph_plan"] != expected_plan:
        raise ReviewWritingB2ContractError(
            "writing_b2.paragraph_plan_invalid",
            "段落Claim计划无法由Approved Claim和写作约束重建。",
        )
    expected_id = stable_id(
        "writing_input_b2",
        {
            key: value
            for key, value in payload.items()
            if key != "writing_input_id"
        },
    )
    if payload.get("writing_input_id") != expected_id:
        raise ReviewWritingB2ContractError(
            "writing_b2.input_identity_invalid",
            "writing_input_id无法由内容重建。",
        )
    return copy.deepcopy(payload)


def build_chapter_draft_schema(
    writing_input: dict[str, Any],
) -> dict[str, Any]:
    section = writing_input["section_task"]
    constraints = writing_input["writing_constraints"]
    plans = writing_input["paragraph_plan"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "section_id",
            "section_index",
            "title",
            "paragraphs",
        ],
        "properties": {
            "schema_version": {"const": CHAPTER_DRAFT_SCHEMA_VERSION},
            "section_id": {"const": section["section_id"]},
            "section_index": {"const": section["section_index"]},
            "title": {"const": section["title"]},
            "paragraphs": {
                "type": "array",
                "minItems": len(plans),
                "maxItems": len(plans),
                "prefixItems": [
                    _paragraph_schema(plan, constraints)
                    for plan in plans
                ],
                "items": False,
            },
        },
    }


def validate_chapter_draft(
    payload: object,
    *,
    writing_input: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("paragraphs"), list):
        declared_list = [
            claim_id
            for paragraph in payload["paragraphs"]
            if isinstance(paragraph, dict)
            and isinstance(paragraph.get("implemented_claim_ids"), list)
            for claim_id in paragraph["implemented_claim_ids"]
            if isinstance(claim_id, str)
        ]
        duplicated_declared = sorted(
            {
                claim_id
                for claim_id in declared_list
                if declared_list.count(claim_id) > 1
            }
        )
        if duplicated_declared:
            raise ReviewWritingB2ContractError(
                "writing_b2.claim_implemented_twice",
                f"Claim不得跨段重复实现：{duplicated_declared}。",
            )
        declared = set(declared_list)
        missing_declared_core = (
            set(writing_input["required_core_claim_ids"]) - declared
        )
        if missing_declared_core:
            raise ReviewWritingB2ContractError(
                "writing_b2.core_claims_missing",
                f"核心Claim未全部实现：{sorted(missing_declared_core)}。",
            )
        plans = writing_input["paragraph_plan"]
        for index, paragraph in enumerate(payload["paragraphs"]):
            if index >= len(plans) or not isinstance(paragraph, dict):
                continue
            plan = plans[index]
            if paragraph.get("implemented_claim_ids") != plan[
                "required_claim_ids"
            ]:
                raise ReviewWritingB2ContractError(
                    "writing_b2.paragraph_claim_plan_mismatch",
                    f"第{index + 1}段Claim目录与程序计划不一致。",
                    path=f"$.paragraphs[{index}].implemented_claim_ids",
                )
            if paragraph.get("citation_keys") != plan[
                "required_citation_keys"
            ]:
                raise ReviewWritingB2ContractError(
                    "writing_b2.paragraph_citation_mismatch",
                    f"第{index + 1}段引用目录与程序计划不一致。",
                    path=f"$.paragraphs[{index}].citation_keys",
                )
            text = paragraph.get("text")
            if isinstance(text, str):
                content_chars = _content_chars(text)
                if not (
                    plan["required_min_chars"]
                    <= content_chars
                    <= plan["required_max_chars"]
                ):
                    raise ReviewWritingB2ContractError(
                        "writing_b2.paragraph_length_out_of_range",
                        f"第{index + 1}段正文字符数不在计划范围内："
                        f"actual={content_chars}, "
                        f"min={plan['required_min_chars']}, "
                        f"max={plan['required_max_chars']}。",
                        path=f"$.paragraphs[{index}].text",
                    )
                missing_qualifiers = [
                    value
                    for value in plan["required_qualifiers"]
                    if value not in text
                ]
                if missing_qualifiers:
                    raise ReviewWritingB2ContractError(
                        "writing_b2.required_qualifier_missing",
                        f"第{index + 1}段缺少限定语："
                        f"{missing_qualifiers}。",
                        path=f"$.paragraphs[{index}].text",
                    )
                prohibited_hits = [
                    value
                    for value in plan["prohibited_phrasings"]
                    if value in text
                ]
                if prohibited_hits:
                    raise ReviewWritingB2ContractError(
                        "writing_b2.prohibited_phrasing",
                        f"第{index + 1}段命中禁止措辞："
                        f"{prohibited_hits}。",
                        path=f"$.paragraphs[{index}].text",
                    )
    draft = _validate_schema(payload, schema)
    paragraphs = draft["paragraphs"]
    actual_indexes = [row["paragraph_index"] for row in paragraphs]
    expected_indexes = list(range(1, len(paragraphs) + 1))
    if actual_indexes != expected_indexes:
        raise ReviewWritingB2ContractError(
            "writing_b2.paragraph_indexes_invalid",
            "段落序号必须从1连续递增。",
            path="$.paragraphs",
        )
    claims = {
        row["claim_id"]: row for row in writing_input["approved_claims"]
    }
    seen: set[str] = set()
    for index, paragraph in enumerate(paragraphs):
        path = f"$.paragraphs[{index}]"
        text = paragraph["text"].strip()
        if not text:
            raise ReviewWritingB2ContractError(
                "writing_b2.paragraph_text_empty",
                "段落正文不得仅包含空白。",
                path=f"{path}.text",
            )
        machine_id = MACHINE_ID_PATTERN.search(text)
        if machine_id:
            raise ReviewWritingB2ContractError(
                "writing_b2.machine_id_in_text",
                f"正文不得包含机器ID：{machine_id.group(0)!r}。",
                path=f"{path}.text",
            )
        claim_ids = paragraph["implemented_claim_ids"]
        duplicated = seen.intersection(claim_ids)
        if duplicated:
            raise ReviewWritingB2ContractError(
                "writing_b2.claim_implemented_twice",
                f"Claim不得跨段重复实现：{sorted(duplicated)}。",
                path=f"{path}.implemented_claim_ids",
            )
        seen.update(claim_ids)
        core_count = sum(
            claims[claim_id]["importance"] == "core"
            for claim_id in claim_ids
        )
        if core_count > writing_input["writing_constraints"][
            "max_core_claims_per_paragraph"
        ]:
            raise ReviewWritingB2ContractError(
                "writing_b2.paragraph_core_claims_exceeded",
                "单段实现的核心Claim超过上限。",
                path=f"{path}.implemented_claim_ids",
            )
        expected_citations = {
            citation
            for claim_id in claim_ids
            for citation in claims[claim_id]["citation_keys"]
        }
        actual_citations = set(paragraph["citation_keys"])
        if actual_citations != expected_citations:
            raise ReviewWritingB2ContractError(
                "writing_b2.paragraph_citation_mismatch",
                "段落引用必须恰好等于本段实现Claim的引用并集。",
                path=f"{path}.citation_keys",
            )
        for claim_id in claim_ids:
            claim = claims[claim_id]
            qualifier = claim["required_qualifier"]
            if qualifier and qualifier not in text:
                raise ReviewWritingB2ContractError(
                    "writing_b2.required_qualifier_missing",
                    f"实现Claim时缺少限定语：{qualifier!r}。",
                    path=f"{path}.text",
                )
            hits = [
                phrase
                for phrase in claim["prohibited_phrasings"]
                if phrase in text
            ]
            if hits:
                raise ReviewWritingB2ContractError(
                    "writing_b2.prohibited_phrasing",
                    f"正文命中Claim禁止措辞：{hits}。",
                    path=f"{path}.text",
                )
    required_core = set(writing_input["required_core_claim_ids"])
    missing_core = required_core - seen
    if missing_core:
        raise ReviewWritingB2ContractError(
            "writing_b2.core_claims_missing",
            f"核心Claim未全部实现：{sorted(missing_core)}。",
        )
    implemented_supporting = {
        claim_id
        for claim_id in seen
        if claims[claim_id]["importance"] == "supporting"
    }
    minimum_supporting = writing_input["writing_constraints"][
        "min_supporting_claims"
    ]
    if len(implemented_supporting) < minimum_supporting:
        raise ReviewWritingB2ContractError(
            "writing_b2.supporting_claims_insufficient",
            "已实现supporting Claim少于本章最低数量："
            f"actual={len(implemented_supporting)}, "
            f"minimum={minimum_supporting}。",
        )
    total_chars = sum(_content_chars(row["text"]) for row in paragraphs)
    constraints = writing_input["writing_constraints"]
    if not constraints["min_chars"] <= total_chars <= constraints["max_chars"]:
        raise ReviewWritingB2ContractError(
            "writing_b2.chapter_length_out_of_range",
            "章节正文字符数不在允许范围内："
            f"actual={total_chars}, min={constraints['min_chars']}, "
            f"max={constraints['max_chars']}。",
        )
    omitted = sorted(set(claims) - seen)
    chapter_without_id = {
        "schema_version": CHAPTER_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3a",
        "writing_input_id": writing_input["writing_input_id"],
        "section_id": draft["section_id"],
        "section_index": draft["section_index"],
        "title": draft["title"],
        "paragraphs": copy.deepcopy(paragraphs),
        "implementation_audit": {
            "approved_claim_count": len(claims),
            "implemented_claim_count": len(seen),
            "implemented_core_claim_count": len(required_core),
            "implemented_supporting_claim_count": len(
                implemented_supporting
            ),
            "missing_core_claim_ids": [],
            "omitted_supporting_claim_ids": omitted,
            "total_chars": total_chars,
            "semantic_unplanned_claim_audit": "not_run",
        },
    }
    chapter_id = stable_id("chapter_b2", chapter_without_id)
    chapter = {**chapter_without_id, "chapter_id": chapter_id}
    for paragraph in chapter["paragraphs"]:
        paragraph["paragraph_id"] = stable_id(
            "paragraph_b2",
            {
                "chapter_id": chapter_id,
                "paragraph_index": paragraph["paragraph_index"],
                "text": paragraph["text"],
                "implemented_claim_ids": paragraph[
                    "implemented_claim_ids"
                ],
                "citation_keys": paragraph["citation_keys"],
            },
        )
    return chapter


def _matching_section(
    framework_b2: dict[str, Any],
    section_id: str,
) -> dict[str, Any]:
    rows = [
        row
        for row in framework_b2["sections"]
        if row["section_id"] == section_id
    ]
    if len(rows) != 1:
        raise ReviewWritingB2ContractError(
            "writing_b2.section_mismatch",
            "Framework B2中无法唯一定位章节。",
        )
    return rows[0]


def _build_paragraph_plan(
    claims: list[dict[str, Any]],
    constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    paragraph_count = constraints["max_paragraphs"]
    average_min_chars = math.ceil(
        constraints["min_chars"] / paragraph_count
    )
    paragraph_min_chars = max(
        1,
        math.ceil(
            average_min_chars
            * WRITING_POLICY["paragraph_min_share_ratio"]
        ),
    )
    generation_target_chars = math.ceil(
        constraints["target_chars"] / paragraph_count
    )
    plans = [
        {
            "paragraph_index": index,
            "required_claim_ids": [],
            "required_citation_keys": [],
            "required_min_chars": paragraph_min_chars,
            "required_max_chars": constraints["max_chars"],
            "generation_target_chars": generation_target_chars,
            "required_qualifiers": [],
            "prohibited_phrasings": [],
        }
        for index in range(1, paragraph_count + 1)
    ]
    selected_claims = _selected_claims_for_plan(
        claims,
        constraints["min_supporting_claims"],
    )
    core = [
        row for row in selected_claims if row["importance"] == "core"
    ]
    supporting = [
        row
        for row in selected_claims
        if row["importance"] == "supporting"
    ]
    if len(core) > paragraph_count * constraints[
        "max_core_claims_per_paragraph"
    ]:
        raise ReviewWritingB2ContractError(
            "writing_b2.core_claim_capacity_exceeded",
            "核心Claim数量超过段落容量。",
        )
    for index, claim in enumerate(core):
        plans[index % paragraph_count]["required_claim_ids"].append(
            claim["claim_id"]
        )
    claim_by_id = {row["claim_id"]: row for row in claims}
    for claim in supporting:
        candidates = [
            plan
            for plan in plans
            if len(plan["required_claim_ids"])
            < constraints["max_claims_per_paragraph"]
        ]
        if not candidates:
            raise ReviewWritingB2ContractError(
                "writing_b2.claim_capacity_exceeded",
                "核心与最低supporting Claim超过段落容量。",
            )
        citation_set = set(claim["citation_keys"])
        selected = min(
            candidates,
            key=lambda plan: (
                -len(
                    citation_set.intersection(
                        {
                            citation
                            for claim_id in plan["required_claim_ids"]
                            for citation in claim_by_id[claim_id][
                                "citation_keys"
                            ]
                        }
                    )
                ),
                len(plan["required_claim_ids"]),
                plan["paragraph_index"],
            ),
        )
        selected["required_claim_ids"].append(claim["claim_id"])
    for plan in plans:
        if not plan["required_claim_ids"]:
            raise ReviewWritingB2ContractError(
                "writing_b2.empty_paragraph_plan",
                "段落计划存在没有Claim的空段。",
            )
        plan["required_citation_keys"] = _ordered_unique(
            citation
            for claim_id in plan["required_claim_ids"]
            for citation in claim_by_id[claim_id]["citation_keys"]
        )
        plan["required_qualifiers"] = _ordered_unique(
            qualifier
            for claim_id in plan["required_claim_ids"]
            for qualifier in [claim_by_id[claim_id]["required_qualifier"]]
            if qualifier
        )
        plan["prohibited_phrasings"] = _ordered_unique(
            phrase
            for claim_id in plan["required_claim_ids"]
            for phrase in claim_by_id[claim_id]["prohibited_phrasings"]
        )
    return plans


def _paragraph_schema(
    plan: dict[str, Any],
    constraints: dict[str, Any],
) -> dict[str, Any]:
    text_schema: dict[str, Any] = {
        "type": "string",
        "minLength": plan["required_min_chars"],
        "maxLength": min(
            constraints["max_paragraph_chars"],
            plan["required_max_chars"],
        ),
    }
    text_rules = [
        {"pattern": re.escape(qualifier)}
        for qualifier in plan["required_qualifiers"]
    ]
    text_rules.extend(
        {"not": {"pattern": re.escape(phrase)}}
        for phrase in plan["prohibited_phrasings"]
    )
    if text_rules:
        text_schema["allOf"] = text_rules
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "paragraph_index",
            "text",
            "implemented_claim_ids",
            "citation_keys",
        ],
        "properties": {
            "paragraph_index": {"const": plan["paragraph_index"]},
            "text": text_schema,
            "implemented_claim_ids": {
                "const": plan["required_claim_ids"],
            },
            "citation_keys": {
                "const": plan["required_citation_keys"],
            },
        },
    }


def _supporting_priority(claim_type: str) -> int:
    order = {
        "direct_fact": 0,
        "author_view_summary": 1,
        "comparative_fact": 2,
        "cross_paper_synthesis": 3,
        "corpus_gap": 4,
        "normative_recommendation": 5,
        "navigation_synthesis": 6,
    }
    return order[claim_type]


def _selected_claims_for_plan(
    claims: list[dict[str, Any]],
    minimum_supporting: int,
) -> list[dict[str, Any]]:
    core = sorted(
        (row for row in claims if row["importance"] == "core"),
        key=lambda row: row["claim_id"],
    )
    supporting = sorted(
        (row for row in claims if row["importance"] == "supporting"),
        key=lambda row: (
            _supporting_priority(row["claim_type"]),
            row["claim_id"],
        ),
    )[:minimum_supporting]
    return [*core, *supporting]


def _ordered_unique(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _validate_schema(
    payload: object,
    schema: dict[str, Any],
) -> dict[str, Any]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        raise ReviewWritingB2ContractError(
            "writing_b2.chapter_schema_invalid",
            error.message,
            path=path,
        )
    return copy.deepcopy(payload)


def _content_chars(value: str) -> int:
    return len("".join(value.split()))
