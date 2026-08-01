from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .source_window_contracts import stable_id


CONCLUSION_INPUT_SCHEMA_VERSION = "llm.review_conclusion_input_b2.v1"
CONCLUSION_DRAFT_SCHEMA_VERSION = "llm.review_conclusion_b2_draft.v1"
CONCLUSION_SCHEMA_VERSION = "llm.review_conclusion_b2.v1"
CONCLUSION_CONFIG_SCHEMA_VERSION = "llm.review_conclusion_b2_config.v1"
ELIGIBLE_SPECIAL_TYPES = {
    "cross_paper_synthesis",
    "corpus_gap",
    "normative_recommendation",
}
CONCLUSION_GROUPS = (
    ("overall_findings", "direct_summary"),
    ("path_comparison", "multi_paper_synthesis"),
    ("corpus_limits", "corpus_gap"),
    ("future_direction", "normative_recommendation"),
)
BOUNDED_PHRASES = (
    "本次纳入的文献显示",
    "在当前样本文献中",
    "综合所分析研究可以观察到",
    "现有样本主要关注",
    "本次语料尚未充分回答",
)
PROHIBITED_PHRASES = (
    "学界一致认为",
    "已形成普遍共识",
    "现有研究已经证明",
    "该领域尚无研究",
    "实践已经表明",
)
MACHINE_ID_PATTERN = re.compile(
    r"(?i)(?:ref|evidence|material|contribution|package|section|chapter|"
    r"paragraph|card|claim|window)[_-][a-z0-9][a-z0-9_-]{5,}"
)


class ReviewConclusionB2ContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ReviewConclusionB2Config:
    schema_version: str
    max_output_tokens: int
    target_chars: int
    min_target_ratio: float
    max_target_ratio: float
    max_source_claims_per_paragraph: int

    def to_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


def load_review_conclusion_b2_config(
    path: Path,
) -> ReviewConclusionB2Config:
    import json

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.config_read_failed",
            f"无法读取B2结论配置：{path}",
        ) from exc
    expected = set(ReviewConclusionB2Config.__dataclass_fields__)
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.config_fields_invalid",
            "B2结论配置字段不匹配。",
        )
    try:
        config = ReviewConclusionB2Config(**payload)
    except TypeError as exc:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.config_invalid", "B2结论配置类型错误。"
        ) from exc
    _validate_config(config)
    return config


def derive_conclusion_input(
    audited_source: Any,
    chapter_sources: list[Any],
    *,
    config: ReviewConclusionB2Config,
) -> dict[str, Any]:
    _validate_config(config)
    draft = audited_source.draft
    if (
        draft.get("body_release_ready") is not True
        or draft.get("conclusion_input_ready") is not True
        or draft.get("audit_summary", {}).get("blocking_sentence_count") != 0
    ):
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.body_not_ready",
            "只有零阻断且明确允许进入结论阶段的受审计正文可生成结论输入。",
        )
    expected_runs = draft.get("chapter_run_ids")
    actual_runs = [source.run_id for source in chapter_sources]
    if expected_runs != actual_runs:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.chapter_sources_mismatch",
            "章节来源集合与受审计正文不一致。",
        )
    framework = chapter_sources[0].writing_input["framework_outline"]
    conclusion_sections = [
        row for row in framework["sections"] if row["section_type"] == "conclusion"
    ]
    if len(conclusion_sections) != 1:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.framework_conclusion_invalid",
            "Framework必须且只能包含一个结论章。",
        )
    section_task = copy.deepcopy(conclusion_sections[0])
    claims: list[dict[str, Any]] = []
    for source in chapter_sources:
        section_index = source.chapter["section_index"]
        section_title = source.chapter["title"]
        for claim in source.writing_input["approved_claims"]:
            if (
                claim["importance"] != "core"
                and claim["claim_type"] not in ELIGIBLE_SPECIAL_TYPES
            ):
                continue
            claims.append(
                {
                    "claim_id": claim["claim_id"],
                    "section_id": claim["section_id"],
                    "section_index": section_index,
                    "section_title": section_title,
                    "planned_claim": claim["planned_claim"],
                    "claim_type": claim["claim_type"],
                    "importance": claim["importance"],
                    "citation_keys": copy.deepcopy(claim["citation_keys"]),
                    "support_mode": claim["support_mode"],
                    "result_type": claim["result_type"],
                    "validation_level": claim["validation_level"],
                    "allowed_strength": claim["allowed_strength"],
                    "required_qualifier": claim["required_qualifier"],
                    "prohibited_phrasings": copy.deepcopy(
                        claim["prohibited_phrasings"]
                    ),
                }
            )
    claim_ids = [row["claim_id"] for row in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.claim_ids_duplicate", "结论输入Claim ID不得重复。"
        )
    section_indexes = {row["section_index"] for row in claims}
    if section_indexes != set(range(1, 9)):
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.section_coverage_unavailable",
            "八个前文章节必须各自提供至少一个可用于结论的Claim。",
        )
    available_types = {row["claim_type"] for row in claims}
    missing_special = ELIGIBLE_SPECIAL_TYPES - available_types
    if missing_special:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.special_claim_missing",
            f"结论所需已批准特殊Claim不足：{sorted(missing_special)}。",
        )
    citation_by_key = {
        row["citation_key"]: row for row in draft["citation_metadata"]
    }
    used_keys = sorted(
        {key for claim in claims for key in claim["citation_keys"]}
    )
    if any(key not in citation_by_key for key in used_keys):
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.citation_metadata_missing",
            "可用Claim存在无法映射的citation key。",
        )
    target_chars = config.target_chars
    constraints = {
        "target_chars": target_chars,
        "min_chars": math.floor(target_chars * config.min_target_ratio),
        "max_chars": math.ceil(target_chars * config.max_target_ratio),
        "paragraph_count": len(CONCLUSION_GROUPS),
        "max_source_claims_per_paragraph": config.max_source_claims_per_paragraph,
    }
    without_id = {
        "schema_version": CONCLUSION_INPUT_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3g",
        "source": {
            "audited_assembly_run_id": audited_source.run_id,
            "audited_assembly_manifest_sha256": audited_source.manifest_sha256,
            "audited_draft_id": draft["draft_id"],
            "release_set_id": draft["release_set_id"],
            "chapter_run_ids": actual_runs,
            "chapter_ids": [source.chapter["chapter_id"] for source in chapter_sources],
        },
        "review_scope": {
            "working_title": draft["working_title"],
            "central_question": draft["central_question"],
        },
        "section_task": section_task,
        "eligible_claims": claims,
        "citation_metadata": [copy.deepcopy(citation_by_key[key]) for key in used_keys],
        "audit_summary": copy.deepcopy(draft["audit_summary"]),
        "audit_outcomes": copy.deepcopy(draft["audit_outcomes"]),
        "writing_constraints": constraints,
        "language_policy": {
            "bounded_phrases": list(BOUNDED_PHRASES),
            "prohibited_phrases": list(PROHIBITED_PHRASES),
        },
    }
    return {
        **without_id,
        "conclusion_input_id": stable_id("review_conclusion_input_b2", without_id),
    }


def build_conclusion_draft_schema(
    conclusion_input: dict[str, Any],
) -> dict[str, Any]:
    claim_ids = [row["claim_id"] for row in conclusion_input["eligible_claims"]]
    citation_keys = [
        row["citation_key"] for row in conclusion_input["citation_metadata"]
    ]
    max_sources = conclusion_input["writing_constraints"][
        "max_source_claims_per_paragraph"
    ]
    items = []
    for index, (group, claim_type) in enumerate(CONCLUSION_GROUPS, start=1):
        items.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "paragraph_index",
                    "group",
                    "claim_type",
                    "text",
                    "source_claim_ids",
                    "citation_keys",
                ],
                "properties": {
                    "paragraph_index": {"const": index},
                    "group": {"const": group},
                    "claim_type": {"const": claim_type},
                    "text": {"type": "string", "minLength": 80, "maxLength": 2500},
                    "source_claim_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": max_sources,
                        "uniqueItems": True,
                        "items": {"type": "string", "enum": claim_ids},
                    },
                    "citation_keys": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string", "enum": citation_keys},
                    },
                },
            }
        )
    section = conclusion_input["section_task"]
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
            "schema_version": {"const": CONCLUSION_DRAFT_SCHEMA_VERSION},
            "section_id": {"const": section["section_id"]},
            "section_index": {"const": section["section_index"]},
            "title": {"const": section["title"]},
            "paragraphs": {
                "type": "array",
                "minItems": len(items),
                "maxItems": len(items),
                "prefixItems": items,
                "items": False,
            },
        },
    }


def validate_conclusion_draft(
    payload: object,
    *,
    conclusion_input: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=str)
    if errors:
        first = errors[0]
        path = "$" + "".join(f"[{part!r}]" for part in first.absolute_path)
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.schema_invalid", first.message, path=path
        )
    assert isinstance(payload, dict)
    claims = {row["claim_id"]: row for row in conclusion_input["eligible_claims"]}
    used_claim_ids: list[str] = []
    covered_sections: set[int] = set()
    normalized_paragraphs = []
    for index, paragraph in enumerate(payload["paragraphs"], start=1):
        path = f"$.paragraphs[{index - 1}]"
        text = paragraph["text"].strip()
        if text != paragraph["text"] or text[-1:] not in "。！？":
            raise ReviewConclusionB2ContractError(
                "conclusion_b2.text_format_invalid",
                "正文不得有首尾空白且必须以中文句末标点结束。",
                path=f"{path}.text",
            )
        if MACHINE_ID_PATTERN.search(text):
            raise ReviewConclusionB2ContractError(
                "conclusion_b2.machine_id_leaked",
                "结论正文不得出现机器ID。",
                path=f"{path}.text",
            )
        if any(value in text for value in PROHIBITED_PHRASES):
            raise ReviewConclusionB2ContractError(
                "conclusion_b2.prohibited_phrase",
                "结论正文包含禁止的领域级概括。",
                path=f"{path}.text",
            )
        if "[@" in text or "citation" in text.lower():
            raise ReviewConclusionB2ContractError(
                "conclusion_b2.manual_citation_invalid",
                "正文不得手写引用标记。",
                path=f"{path}.text",
            )
        source_claims = [claims[value] for value in paragraph["source_claim_ids"]]
        source_types = {row["claim_type"] for row in source_claims}
        section_count = len({row["section_index"] for row in source_claims})
        claim_type = paragraph["claim_type"]
        if claim_type == "multi_paper_synthesis" and (
            section_count < 2 and "cross_paper_synthesis" not in source_types
        ):
            raise ReviewConclusionB2ContractError(
                "conclusion_b2.multi_paper_source_invalid",
                "跨论文综合必须跨至少两个章节或引用已批准cross_paper_synthesis。",
                path=path,
            )
        if claim_type == "corpus_gap" and "corpus_gap" not in source_types:
            raise ReviewConclusionB2ContractError(
                "conclusion_b2.corpus_gap_source_missing",
                "语料缺口段必须引用已批准corpus_gap Claim。",
                path=path,
            )
        if (
            claim_type == "normative_recommendation"
            and "normative_recommendation" not in source_types
        ):
            raise ReviewConclusionB2ContractError(
                "conclusion_b2.recommendation_source_missing",
                "未来方向段必须引用已批准normative_recommendation Claim。",
                path=path,
            )
        if claim_type in {"multi_paper_synthesis", "corpus_gap"} and not any(
            value in text for value in BOUNDED_PHRASES
        ):
            raise ReviewConclusionB2ContractError(
                "conclusion_b2.bounded_phrase_missing",
                "跨论文综合和语料缺口必须使用样本文献限定语。",
                path=f"{path}.text",
            )
        expected_citations = sorted(
            {key for row in source_claims for key in row["citation_keys"]}
        )
        if paragraph["citation_keys"] != expected_citations:
            raise ReviewConclusionB2ContractError(
                "conclusion_b2.citation_union_mismatch",
                "citation_keys必须按字典序等于source_claim_ids允许引用的并集。",
                path=f"{path}.citation_keys",
            )
        used_claim_ids.extend(paragraph["source_claim_ids"])
        covered_sections.update(row["section_index"] for row in source_claims)
        normalized_paragraphs.append(
            {
                **copy.deepcopy(paragraph),
                "paragraph_id": stable_id(
                    "review_conclusion_paragraph_b2",
                    {
                        "conclusion_input_id": conclusion_input["conclusion_input_id"],
                        "paragraph_index": index,
                        "group": paragraph["group"],
                        "text": text,
                        "source_claim_ids": paragraph["source_claim_ids"],
                        "citation_keys": paragraph["citation_keys"],
                    },
                ),
            }
        )
    if len(used_claim_ids) != len(set(used_claim_ids)):
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.source_claim_reused",
            "同一来源Claim不得在多个结论段重复使用。",
        )
    if covered_sections != set(range(1, 9)):
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.section_coverage_incomplete",
            f"结论必须覆盖八章，当前覆盖：{sorted(covered_sections)}。",
        )
    total_chars = sum(_content_chars(row["text"]) for row in normalized_paragraphs)
    constraints = conclusion_input["writing_constraints"]
    if not constraints["min_chars"] <= total_chars <= constraints["max_chars"]:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.length_invalid",
            f"结论正文字符数{total_chars}不在允许范围"
            f"{constraints['min_chars']}至{constraints['max_chars']}内。",
        )
    without_id = {
        "schema_version": CONCLUSION_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3g",
        "conclusion_input_id": conclusion_input["conclusion_input_id"],
        "section_id": payload["section_id"],
        "section_index": payload["section_index"],
        "title": payload["title"],
        "paragraphs": normalized_paragraphs,
        "implemented_source_claim_ids": used_claim_ids,
        "covered_section_indexes": sorted(covered_sections),
        "citation_keys": sorted(
            {key for row in normalized_paragraphs for key in row["citation_keys"]}
        ),
        "total_chars": total_chars,
        "release_status": "candidate_pending_conclusion_audit",
    }
    return {
        **without_id,
        "chapter_id": stable_id("review_conclusion_b2", without_id),
    }


def _validate_config(config: ReviewConclusionB2Config) -> None:
    if config.schema_version != CONCLUSION_CONFIG_SCHEMA_VERSION:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.config_schema_invalid", "B2结论配置版本不受支持。"
        )
    if type(config.max_output_tokens) is not int or config.max_output_tokens < 1024:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.output_budget_invalid", "结论输出预算必须至少为1024 tokens。"
        )
    if type(config.target_chars) is not int or not 600 <= config.target_chars <= 4000:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.target_chars_invalid", "结论目标字符必须在600至4000之间。"
        )
    if not 0.3 <= config.min_target_ratio <= 1.0:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.min_ratio_invalid", "最小篇幅比例必须在0.3至1.0之间。"
        )
    if not 1.0 <= config.max_target_ratio <= 2.0:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.max_ratio_invalid", "最大篇幅比例必须在1.0至2.0之间。"
        )
    if config.min_target_ratio >= config.max_target_ratio:
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.ratio_order_invalid", "篇幅比例上下限顺序错误。"
        )
    if (
        type(config.max_source_claims_per_paragraph) is not int
        or not 2 <= config.max_source_claims_per_paragraph <= 20
    ):
        raise ReviewConclusionB2ContractError(
            "conclusion_b2.claim_limit_invalid", "每段来源Claim上限必须在2至20之间。"
        )


def _content_chars(value: str) -> int:
    return len(re.sub(r"\s+", "", value))
