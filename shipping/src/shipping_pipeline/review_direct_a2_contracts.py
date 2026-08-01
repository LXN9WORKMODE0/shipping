from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .source_window_contracts import stable_id


A2_CONFIG_SCHEMA_VERSION = "llm.review_direct_a2_config.v1"
A2_INPUT_SCHEMA_VERSION = "llm.review_direct_a2_input.v1"
A2_DRAFT_SCHEMA_VERSION = "llm.review_direct_a2_draft.v1"
A2_REVIEW_SCHEMA_VERSION = "llm.review_direct_a2.v1"
MACHINE_ID_PATTERN = re.compile(
    r"(?i)(?:ref|evidence|material|contribution|package|section|chapter|"
    r"paragraph|card|claim|window)[_-][a-z0-9][a-z0-9_-]{5,}"
)


class ReviewDirectA2ContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ReviewDirectA2Config:
    schema_version: str
    min_body_chars: int
    max_body_chars: int
    max_paragraphs_per_section: int
    max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


def load_review_direct_a2_config(path: Path) -> ReviewDirectA2Config:
    import json

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewDirectA2ContractError(
            "direct_a2.config_read_failed", f"无法读取A2配置：{path}"
        ) from exc
    expected = set(ReviewDirectA2Config.__dataclass_fields__)
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ReviewDirectA2ContractError(
            "direct_a2.config_fields_invalid", "A2配置字段不匹配。"
        )
    config = ReviewDirectA2Config(**payload)
    if config.schema_version != A2_CONFIG_SCHEMA_VERSION:
        raise ReviewDirectA2ContractError(
            "direct_a2.config_schema_invalid", "A2配置版本不受支持。"
        )
    if (
        type(config.min_body_chars) is not int
        or type(config.max_body_chars) is not int
        or not 3000 <= config.min_body_chars < config.max_body_chars <= 20000
    ):
        raise ReviewDirectA2ContractError(
            "direct_a2.body_range_invalid", "A2正文字符区间无效。"
        )
    if (
        type(config.max_paragraphs_per_section) is not int
        or not 1 <= config.max_paragraphs_per_section <= 10
        or type(config.max_output_tokens) is not int
        or config.max_output_tokens < 4096
    ):
        raise ReviewDirectA2ContractError(
            "direct_a2.limit_invalid", "A2段落或输出Token上限无效。"
        )
    return config


def derive_direct_a2_input(
    *,
    source_package: Any,
    audited_body: Any,
    framework: dict[str, Any],
    config: ReviewDirectA2Config,
) -> dict[str, Any]:
    if audited_body.draft.get("body_release_ready") is not True:
        raise ReviewDirectA2ContractError(
            "direct_a2.body_framework_not_released", "统一Framework的正文来源未通过门禁。"
        )
    sections = copy.deepcopy(framework["sections"])
    if [row["section_index"] for row in sections] != list(range(1, 10)):
        raise ReviewDirectA2ContractError(
            "direct_a2.framework_sections_invalid", "A2统一Framework必须包含连续九章。"
        )
    papers = []
    for paper in source_package.package["papers"]:
        papers.append(
            {
                "paper_id": paper["paper_id"],
                "paper_title": paper["paper_title"],
                "citation_key": paper["citation_key"],
                "bibliography": _project_bibliography(paper["bibliography"]),
                "full_markdown": paper["full_markdown"],
            }
        )
    if len(papers) != 14:
        raise ReviewDirectA2ContractError(
            "direct_a2.paper_count_invalid", f"A2必须使用14篇论文，实际{len(papers)}篇。"
        )
    keys = [row["citation_key"] for row in papers]
    if len(keys) != len(set(keys)):
        raise ReviewDirectA2ContractError(
            "direct_a2.citation_duplicate", "A2论文citation key不得重复。"
        )
    body_paper_ids = {row["paper_id"] for row in audited_body.draft["citation_metadata"]}
    if {row["paper_id"] for row in papers} != body_paper_ids:
        raise ReviewDirectA2ContractError(
            "direct_a2.corpus_mismatch", "A2全文论文集合与B2冻结题录集合不一致。"
        )
    without_id = {
        "schema_version": A2_INPUT_SCHEMA_VERSION,
        "pipeline_generation": "A2.phase6a",
        "source": {
            "source_package_run_id": source_package.run_id,
            "source_package_sha256": source_package.output_sha256,
            "audited_assembly_run_id": audited_body.run_id,
            "audited_draft_id": audited_body.draft["draft_id"],
            "framework_b2_id": framework["framework_b2_id"],
        },
        "review_scope": {
            "working_title": framework["working_title"],
            "central_question": framework["central_question"],
            "corpus_scope": "targeted_sample",
            "paper_count": len(papers),
        },
        "framework": {
            "working_title": framework["working_title"],
            "central_question": framework["central_question"],
            "sections": sections,
        },
        "papers": papers,
        "writing_constraints": {
            "min_body_chars": config.min_body_chars,
            "max_body_chars": config.max_body_chars,
            "section_count": len(sections),
            "max_paragraphs_per_section": config.max_paragraphs_per_section,
            "citation_format": "paragraph_level_citation_keys",
        },
        "excluded_cognitive_inputs": [
            "paper_understanding",
            "research_landscape",
            "claim_ledger",
            "source_windows",
            "b2_body_text",
            "b2_conclusion_text",
        ],
    }
    return {
        **without_id,
        "a2_input_id": stable_id("review_direct_a2_input", without_id),
    }


def build_direct_a2_schema(a2_input: dict[str, Any]) -> dict[str, Any]:
    citation_keys = [row["citation_key"] for row in a2_input["papers"]]
    max_paragraphs = a2_input["writing_constraints"]["max_paragraphs_per_section"]
    section_schemas = []
    for section in a2_input["framework"]["sections"]:
        section_schemas.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["section_id", "section_index", "title", "paragraphs"],
                "properties": {
                    "section_id": {"const": section["section_id"]},
                    "section_index": {"const": section["section_index"]},
                    "title": {"const": section["title"]},
                    "paragraphs": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": max_paragraphs,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["paragraph_index", "text", "citation_keys"],
                            "properties": {
                                "paragraph_index": {"type": "integer", "minimum": 1},
                                "text": {
                                    "type": "string",
                                    "minLength": 100,
                                    "maxLength": 4000,
                                    "pattern": "[。！？]$",
                                    "not": {"pattern": "(?:\\[ref_|\\[@ref_|ref_[0-9a-f]{8})"},
                                    "description": "纯正文；严禁写ref_、[ref_...]、[@ref_...]或任何引用标记，引用只放citation_keys。",
                                },
                                "citation_keys": {
                                    "type": "array",
                                    "uniqueItems": True,
                                    "items": {"type": "string", "enum": citation_keys},
                                },
                            },
                        },
                    },
                },
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "title", "sections"],
        "properties": {
            "schema_version": {"const": A2_DRAFT_SCHEMA_VERSION},
            "title": {"const": a2_input["review_scope"]["working_title"]},
            "sections": {
                "type": "array",
                "minItems": len(section_schemas),
                "maxItems": len(section_schemas),
                "prefixItems": section_schemas,
                "items": False,
            },
        },
    }


def validate_direct_a2_draft(
    payload: object, *, a2_input: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=str)
    if errors:
        first = errors[0]
        path = "$" + "".join(f"[{part!r}]" for part in first.absolute_path)
        raise ReviewDirectA2ContractError("direct_a2.schema_invalid", first.message, path=path)
    assert isinstance(payload, dict)
    normalized_sections = []
    all_paragraphs = []
    for section in payload["sections"]:
        paragraphs = []
        for expected_index, paragraph in enumerate(section["paragraphs"], start=1):
            if paragraph["paragraph_index"] != expected_index:
                raise ReviewDirectA2ContractError(
                    "direct_a2.paragraph_order_invalid", "各章段落编号必须从1连续递增。"
                )
            text = paragraph["text"]
            if text != text.strip() or text[-1:] not in "。！？":
                raise ReviewDirectA2ContractError(
                    "direct_a2.text_format_invalid", "段落必须无首尾空白并以中文句末标点结束。"
                )
            if MACHINE_ID_PATTERN.search(text) or "[@" in text:
                raise ReviewDirectA2ContractError(
                    "direct_a2.machine_text_invalid", "A2正文不得包含机器ID或手写引用。"
                )
            row = copy.deepcopy(paragraph)
            row["paragraph_id"] = stable_id(
                "review_direct_a2_paragraph",
                {
                    "a2_input_id": a2_input["a2_input_id"],
                    "section_id": section["section_id"],
                    **paragraph,
                },
            )
            paragraphs.append(row)
            all_paragraphs.append(row)
        normalized_sections.append({**copy.deepcopy(section), "paragraphs": paragraphs})
    total_chars = sum(len(re.sub(r"\s+", "", row["text"])) for row in all_paragraphs)
    constraints = a2_input["writing_constraints"]
    if not constraints["min_body_chars"] <= total_chars <= constraints["max_body_chars"]:
        raise ReviewDirectA2ContractError(
            "direct_a2.body_length_invalid",
            f"A2正文字符数{total_chars}不在{constraints['min_body_chars']}至{constraints['max_body_chars']}内。",
        )
    if not any(len(row["citation_keys"]) >= 2 for row in all_paragraphs):
        raise ReviewDirectA2ContractError(
            "direct_a2.cross_paper_paragraph_missing", "A2没有任何跨论文引用段落。"
        )
    without_id = {
        "schema_version": A2_REVIEW_SCHEMA_VERSION,
        "pipeline_generation": "A2.phase6a",
        "a2_input_id": a2_input["a2_input_id"],
        "working_title": payload["title"],
        "chapters": normalized_sections,
        "body_char_count": total_chars,
        "release_status": "candidate_pending_unified_audit",
    }
    return {**without_id, "review_id": stable_id("review_direct_a2", without_id)}


def _project_bibliography(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value.get(key))
        for key in (
            "authors",
            "year",
            "journal",
            "institution",
            "degree",
            "doi",
            "reference_type",
            "bibliography_status",
        )
    }
