from __future__ import annotations

import copy
from typing import Any

from .source_window_contracts import stable_id


REVIEW_FRAMEWORK_B2_SCHEMA_VERSION = "llm.review_framework.v2"
REVIEW_FRAMEWORK_B2_RUN_SCHEMA_VERSION = "llm.review_framework_b2_run.v1"
STRENGTH_LIMITS = (
    "source_reported_only",
    "qualified_comparison",
    "targeted_corpus_only",
)


class ReviewFrameworkB2ContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


def derive_review_framework_b2(
    framework: dict[str, Any],
    *,
    source_run_id: str,
    source_manifest_sha256: str,
    target_total_chars: int = 8000,
) -> dict[str, Any]:
    if framework.get("schema_version") != "llm.review_framework.v1":
        raise ReviewFrameworkB2ContractError(
            "framework_b2.source_schema_invalid",
            "只允许从Review Framework v1显式派生v2。",
        )
    sections = framework.get("sections")
    if not isinstance(sections, list) or len(sections) < 3:
        raise ReviewFrameworkB2ContractError(
            "framework_b2.sections_invalid",
            "Framework必须至少包含三个章节。",
        )
    if type(target_total_chars) is not int or target_total_chars < 3000:
        raise ReviewFrameworkB2ContractError(
            "framework_b2.target_total_chars_invalid",
            "目标总字数必须是至少3000的整数。",
        )
    body_count = sum(
        row.get("section_type") == "body"
        for row in sections
        if isinstance(row, dict)
    )
    introduction_count = sum(
        row.get("section_type") == "introduction"
        for row in sections
        if isinstance(row, dict)
    )
    conclusion_count = sum(
        row.get("section_type") == "conclusion"
        for row in sections
        if isinstance(row, dict)
    )
    if introduction_count != 1 or conclusion_count != 1:
        raise ReviewFrameworkB2ContractError(
            "framework_b2.boundary_sections_invalid",
            "Framework必须恰好包含一个introduction和一个conclusion章节。",
        )
    if body_count == 0:
        raise ReviewFrameworkB2ContractError(
            "framework_b2.body_missing",
            "Framework必须包含body章节。",
        )

    all_papers: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            raise ReviewFrameworkB2ContractError(
                "framework_b2.section_invalid",
                "章节必须是对象。",
            )
        _extend_unique(all_papers, _text_list(section.get("paper_ids")))
    _extend_unique(all_papers, _text_list(framework.get("unused_papers", [])))

    intro_chars = max(500, round(target_total_chars * 0.09))
    conclusion_chars = max(600, round(target_total_chars * 0.11))
    body_total = target_total_chars - intro_chars - conclusion_chars
    body_base, body_remainder = divmod(body_total, body_count)
    body_seen = 0
    projected_sections: list[dict[str, Any]] = []
    for section in sections:
        section_type = _required_text(section.get("section_type"))
        paper_ids = _text_list(section.get("paper_ids"))
        if section_type == "introduction":
            target_chars = intro_chars
            budget = {
                "core_max": 2,
                "supporting_max": 4,
                "cross_paper_synthesis_max": 1,
            }
            high_risk = ["corpus_gap", "cross_paper_synthesis"]
        elif section_type == "conclusion":
            target_chars = conclusion_chars
            budget = {
                "core_max": 3,
                "supporting_max": 4,
                "cross_paper_synthesis_max": 2,
            }
            high_risk = [
                "cross_paper_synthesis",
                "corpus_gap",
                "normative_recommendation",
            ]
        elif section_type == "body":
            target_chars = body_base + (
                1 if body_seen < body_remainder else 0
            )
            body_seen += 1
            budget = {
                "core_max": 4,
                "supporting_max": 8,
                "cross_paper_synthesis_max": 2,
            }
            high_risk = ["comparative_fact", "cross_paper_synthesis"]
        else:
            raise ReviewFrameworkB2ContractError(
                "framework_b2.section_type_invalid",
                f"未知section_type：{section_type!r}。",
            )
        projected = copy.deepcopy(section)
        projected.update(
            {
                "target_chars": target_chars,
                "max_paragraphs": max(2, (target_chars + 499) // 500),
                "claim_budget": budget,
                "material_selection": {
                    "required_paper_ids": paper_ids,
                    "optional_paper_ids": [],
                    "background_only_paper_ids": [],
                    "limitation_only_paper_ids": [],
                    "excluded_paper_ids": [
                        paper_id
                        for paper_id in all_papers
                        if paper_id not in set(paper_ids)
                    ],
                },
                "high_risk_claim_types": high_risk,
                "conclusion_strength_limit": "targeted_corpus_only",
            }
        )
        projected_sections.append(projected)

    result = {
        key: copy.deepcopy(value)
        for key, value in framework.items()
        if key not in {"schema_version", "framework_id", "sections"}
    }
    result.update(
        {
            "schema_version": REVIEW_FRAMEWORK_B2_SCHEMA_VERSION,
            "source": {
                "framework_run_id": _required_text(source_run_id),
                "framework_id": _required_text(framework.get("framework_id")),
                "framework_manifest_sha256": _required_text(
                    source_manifest_sha256
                ),
            },
            "target_total_chars": target_total_chars,
            "sections": projected_sections,
        }
    )
    result["framework_id"] = stable_id(
        "framework_b2",
        {
            key: value
            for key, value in result.items()
            if key != "framework_id"
        },
    )
    return validate_review_framework_b2(result)


def validate_review_framework_b2(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReviewFrameworkB2ContractError(
            "framework_b2.payload_invalid",
            "Framework v2必须是对象。",
        )
    if payload.get("schema_version") != REVIEW_FRAMEWORK_B2_SCHEMA_VERSION:
        raise ReviewFrameworkB2ContractError(
            "framework_b2.schema_invalid",
            "Framework v2 Schema版本不受支持。",
        )
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ReviewFrameworkB2ContractError(
            "framework_b2.sections_invalid",
            "Framework v2 sections必须是非空数组。",
        )
    target_total = payload.get("target_total_chars")
    if type(target_total) is not int or target_total < 3000:
        raise ReviewFrameworkB2ContractError(
            "framework_b2.target_total_chars_invalid",
            "target_total_chars无效。",
        )
    actual_total = 0
    for index, section in enumerate(sections):
        path = f"$.sections[{index}]"
        if not isinstance(section, dict):
            raise ReviewFrameworkB2ContractError(
                "framework_b2.section_invalid",
                "章节必须是对象。",
                path=path,
            )
        paper_ids = _text_list(section.get("paper_ids"))
        target_chars = section.get("target_chars")
        max_paragraphs = section.get("max_paragraphs")
        if type(target_chars) is not int or target_chars < 1:
            raise ReviewFrameworkB2ContractError(
                "framework_b2.section_target_invalid",
                "target_chars必须是正整数。",
                path=f"{path}.target_chars",
            )
        if type(max_paragraphs) is not int or max_paragraphs < 1:
            raise ReviewFrameworkB2ContractError(
                "framework_b2.max_paragraphs_invalid",
                "max_paragraphs必须是正整数。",
                path=f"{path}.max_paragraphs",
            )
        actual_total += target_chars
        budget = section.get("claim_budget")
        if not isinstance(budget, dict):
            raise ReviewFrameworkB2ContractError(
                "framework_b2.claim_budget_invalid",
                "claim_budget必须是对象。",
                path=f"{path}.claim_budget",
            )
        for field in (
            "core_max",
            "supporting_max",
            "cross_paper_synthesis_max",
        ):
            if type(budget.get(field)) is not int or budget[field] < 0:
                raise ReviewFrameworkB2ContractError(
                    "framework_b2.claim_budget_invalid",
                    f"{field}必须是非负整数。",
                    path=f"{path}.claim_budget.{field}",
                )
        if section.get("section_type") == "body" and budget["core_max"] < 1:
            raise ReviewFrameworkB2ContractError(
                "framework_b2.body_claim_budget_missing",
                "body章节必须有正数core Claim预算。",
                path=f"{path}.claim_budget.core_max",
            )
        selection = section.get("material_selection")
        if not isinstance(selection, dict):
            raise ReviewFrameworkB2ContractError(
                "framework_b2.material_selection_invalid",
                "material_selection必须是对象。",
                path=f"{path}.material_selection",
            )
        groups = [
            _text_list(selection.get(field))
            for field in (
                "required_paper_ids",
                "optional_paper_ids",
                "background_only_paper_ids",
                "limitation_only_paper_ids",
                "excluded_paper_ids",
            )
        ]
        flattened = [value for group in groups for value in group]
        if len(flattened) != len(set(flattened)):
            raise ReviewFrameworkB2ContractError(
                "framework_b2.material_groups_overlap",
                "五类论文集合必须互斥。",
                path=f"{path}.material_selection",
            )
        if not set(groups[0]).issubset(paper_ids):
            raise ReviewFrameworkB2ContractError(
                "framework_b2.required_papers_outside_section",
                "required_paper_ids必须属于章节paper_ids。",
                path=f"{path}.material_selection.required_paper_ids",
            )
        if section.get("conclusion_strength_limit") not in STRENGTH_LIMITS:
            raise ReviewFrameworkB2ContractError(
                "framework_b2.strength_limit_invalid",
                "conclusion_strength_limit无效。",
                path=f"{path}.conclusion_strength_limit",
            )
    if actual_total != target_total:
        raise ReviewFrameworkB2ContractError(
            "framework_b2.target_total_mismatch",
            f"章节目标字数合计{actual_total}，不等于{target_total}。",
        )
    expected_id = stable_id(
        "framework_b2",
        {
            key: value
            for key, value in payload.items()
            if key != "framework_id"
        },
    )
    if payload.get("framework_id") != expected_id:
        raise ReviewFrameworkB2ContractError(
            "framework_b2.identity_invalid",
            "framework_id与规范化内容不一致。",
        )
    return copy.deepcopy(payload)


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewFrameworkB2ContractError(
            "framework_b2.text_invalid",
            "必填文本不能为空。",
        )
    return value.strip()


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ReviewFrameworkB2ContractError(
            "framework_b2.list_invalid",
            "字段必须是字符串数组。",
        )
    result = [_required_text(item) for item in value]
    if len(result) != len(set(result)):
        raise ReviewFrameworkB2ContractError(
            "framework_b2.list_duplicate",
            "字符串数组不允许重复。",
        )
    return result


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)
