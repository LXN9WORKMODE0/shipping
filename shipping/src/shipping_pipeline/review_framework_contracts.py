from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from jsonschema import Draft202012Validator


REVIEW_FRAMEWORK_DRAFT_SCHEMA_VERSION = "llm.review_framework_draft.v1"
REVIEW_FRAMEWORK_SCHEMA_VERSION = "llm.review_framework.v1"
SECTION_TYPES = ("introduction", "body", "conclusion")
BIBLIOGRAPHY_STATUSES = ("complete", "partial")


class ReviewFrameworkContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


def build_review_framework_draft_schema(
    *,
    topic: str,
    review_goal: str,
    dimension_indexes: list[int],
    controversy_ids: list[str],
    corpus_gap_indexes: list[int],
    max_sections: int = 12,
) -> dict[str, Any]:
    resolved_topic = _required_text(topic, "topic")
    resolved_review_goal = _required_text(review_goal, "review_goal")
    dimensions = _validated_positive_indexes(
        dimension_indexes,
        "dimension_indexes",
        allow_empty=False,
    )
    controversies = _validated_ids(
        controversy_ids,
        "controversy_ids",
        allow_empty=True,
    )
    corpus_gaps = _validated_positive_indexes(
        corpus_gap_indexes,
        "corpus_gap_indexes",
        allow_empty=True,
    )
    if type(max_sections) is not int or max_sections < 3:
        raise ValueError("max_sections必须是至少为3的整数。")

    short_text = {"type": "string", "minLength": 1, "maxLength": 240}
    text = {"type": "string", "minLength": 1, "maxLength": 1200}
    string_list = {
        "type": "array",
        "uniqueItems": True,
        "items": copy.deepcopy(text),
    }
    section = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "section_index",
            "title",
            "section_type",
            "question",
            "purpose",
            "dimension_indexes",
            "required_comparisons",
            "controversy_ids",
            "corpus_gap_indexes",
            "corpus_limitations",
        ],
        "properties": {
            "section_index": {
                "type": "integer",
                "minimum": 1,
                "maximum": max_sections,
            },
            "title": short_text,
            "section_type": {
                "type": "string",
                "enum": list(SECTION_TYPES),
            },
            "question": text,
            "purpose": text,
            "dimension_indexes": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {
                    "type": "integer",
                    "enum": dimensions,
                },
            },
            "required_comparisons": copy.deepcopy(string_list),
            "controversy_ids": {
                "type": "array",
                "uniqueItems": True,
                "items": _enum_or_never(controversies, "string"),
            },
            "corpus_gap_indexes": {
                "type": "array",
                "uniqueItems": True,
                "items": _enum_or_never(corpus_gaps, "integer"),
            },
            "corpus_limitations": copy.deepcopy(string_list),
        },
        "allOf": [
            {
                "if": {
                    "properties": {
                        "section_type": {"const": "body"},
                    },
                    "required": ["section_type"],
                },
                "then": {
                    "properties": {
                        "required_comparisons": {"minItems": 1},
                    }
                },
            }
        ],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": (
            "https://shipping.local/schema/"
            "llm.review_framework_draft.v1.json"
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "topic",
            "review_goal",
            "working_title",
            "central_question",
            "sections",
        ],
        "properties": {
            "schema_version": {
                "const": REVIEW_FRAMEWORK_DRAFT_SCHEMA_VERSION,
            },
            "topic": {"const": resolved_topic},
            "review_goal": {"const": resolved_review_goal},
            "working_title": {
                "type": "string",
                "minLength": 1,
                "maxLength": 400,
            },
            "central_question": text,
            "sections": {
                "type": "array",
                "minItems": 3,
                "maxItems": max_sections,
                "items": section,
            },
        },
    }


def validate_review_framework_draft(
    payload: object,
    schema: dict[str, Any],
    landscape: dict[str, Any],
) -> dict[str, Any]:
    validated = _validate_schema(
        payload,
        schema,
        "schema.review_framework_draft_invalid",
    )
    if not isinstance(landscape, dict):
        raise ReviewFrameworkContractError(
            "framework.landscape_invalid",
            "Research Landscape必须是JSON对象。",
            path="$.landscape",
        )
    expected_topic = _landscape_text(landscape, "topic")
    if validated["topic"] != expected_topic:
        raise ReviewFrameworkContractError(
            "framework.landscape_identity_mismatch",
            "topic与Research Landscape不一致。",
            path="$.topic",
        )

    dimensions = _landscape_dimensions(landscape)
    known_dimensions = set(dimensions)
    known_controversies = set(_landscape_controversies(landscape))
    known_corpus_gaps = set(_landscape_corpus_gap_indexes(landscape))
    sections = validated["sections"]
    actual_indexes = [int(section["section_index"]) for section in sections]
    expected_indexes = list(range(1, len(sections) + 1))
    if actual_indexes != expected_indexes:
        raise ReviewFrameworkContractError(
            "framework.section_indexes_invalid",
            "section_index必须按输出顺序从1连续编号："
            f"expected={expected_indexes}, actual={actual_indexes}",
            path="$.sections",
        )

    introduction_indexes = [
        index
        for index, section in enumerate(sections)
        if section["section_type"] == "introduction"
    ]
    body_indexes = [
        index
        for index, section in enumerate(sections)
        if section["section_type"] == "body"
    ]
    conclusion_indexes = [
        index
        for index, section in enumerate(sections)
        if section["section_type"] == "conclusion"
    ]
    if introduction_indexes != [0]:
        raise ReviewFrameworkContractError(
            "framework.introduction_order_invalid",
            "必须恰好有一个introduction，且位于第一节。",
            path="$.sections",
        )
    if not body_indexes:
        raise ReviewFrameworkContractError(
            "framework.body_missing",
            "至少需要一个body章节。",
            path="$.sections",
        )
    if conclusion_indexes != [len(sections) - 1]:
        raise ReviewFrameworkContractError(
            "framework.conclusion_order_invalid",
            "必须恰好有一个conclusion，且位于末节。",
            path="$.sections",
        )

    body_dimensions: set[int] = set()
    for section_index, section in enumerate(sections):
        dimensions_in_section = set(section["dimension_indexes"])
        unknown_dimensions = sorted(dimensions_in_section - known_dimensions)
        if unknown_dimensions:
            raise ReviewFrameworkContractError(
                "framework.dimension_unknown",
                f"章节引用未知Landscape维度：{unknown_dimensions}",
                path=f"$.sections[{section_index}].dimension_indexes",
            )
        unknown_controversies = sorted(
            set(section["controversy_ids"]) - known_controversies
        )
        if unknown_controversies:
            raise ReviewFrameworkContractError(
                "framework.controversy_unknown",
                f"章节引用未知争议ID：{unknown_controversies}",
                path=f"$.sections[{section_index}].controversy_ids",
            )
        unknown_gaps = sorted(
            set(section["corpus_gap_indexes"]) - known_corpus_gaps
        )
        if unknown_gaps:
            raise ReviewFrameworkContractError(
                "framework.corpus_gap_unknown",
                f"章节引用未知语料缺口序号：{unknown_gaps}",
                path=f"$.sections[{section_index}].corpus_gap_indexes",
            )
        if section["section_type"] == "body":
            body_dimensions.update(dimensions_in_section)
            if not section["required_comparisons"]:
                raise ReviewFrameworkContractError(
                    "framework.body_comparison_missing",
                    "body章节必须至少包含一个比较任务。",
                    path=(
                        f"$.sections[{section_index}]"
                        ".required_comparisons"
                    ),
                )

    missing_dimensions = sorted(known_dimensions - body_dimensions)
    if missing_dimensions:
        raise ReviewFrameworkContractError(
            "framework.body_dimension_coverage_incomplete",
            f"Landscape维度未进入任何body章节：{missing_dimensions}",
            path="$.sections",
        )
    return validated


def derive_review_framework(
    draft: dict[str, Any],
    *,
    landscape: dict[str, Any],
    reference_by_paper: dict[str, dict[str, Any]],
    source_landscape_id: str,
) -> dict[str, Any]:
    resolved_landscape_id = _required_contract_text(
        source_landscape_id,
        "framework.source_landscape_id_invalid",
        "$.source_landscape_id",
    )
    if not isinstance(landscape, dict):
        raise ReviewFrameworkContractError(
            "framework.landscape_invalid",
            "Research Landscape必须是JSON对象。",
            path="$.landscape",
        )
    landscape_id = landscape.get("landscape_id")
    if landscape_id is not None and landscape_id != resolved_landscape_id:
        raise ReviewFrameworkContractError(
            "framework.source_landscape_mismatch",
            "source_landscape_id与Landscape中的landscape_id不一致。",
            path="$.source_landscape_id",
        )

    dimensions = _landscape_dimensions(landscape)
    controversies = _landscape_controversies(landscape)
    corpus_gap_indexes = _landscape_corpus_gap_indexes(landscape)
    if not isinstance(draft, dict):
        raise ReviewFrameworkContractError(
            "framework.draft_invalid",
            "Review Framework草案必须是JSON对象。",
            path="$",
        )
    draft_review_goal = _required_contract_text(
        draft.get("review_goal"),
        "framework.review_goal_invalid",
        "$.review_goal",
    )
    schema = build_review_framework_draft_schema(
        topic=_landscape_text(landscape, "topic"),
        review_goal=draft_review_goal,
        dimension_indexes=list(dimensions),
        controversy_ids=list(controversies),
        corpus_gap_indexes=corpus_gap_indexes,
    )
    validated = validate_review_framework_draft(draft, schema, landscape)
    corpus_scope = _landscape_text(landscape, "corpus_scope")

    corpus_paper_ids = _landscape_paper_ids(
        landscape,
        dimensions=dimensions,
        controversies=controversies,
    )
    references = _validate_references(
        reference_by_paper,
        corpus_paper_ids,
    )
    section_rows: list[dict[str, Any]] = []
    body_paper_ids: list[str] = []
    for section in validated["sections"]:
        paper_ids: list[str] = []
        contribution_ids: list[str] = []
        for dimension_index in sorted(section["dimension_indexes"]):
            dimension = dimensions[dimension_index]
            _extend_unique(
                paper_ids,
                _required_string_list(
                    dimension.get("paper_ids"),
                    "framework.dimension_papers_missing",
                    (
                        "$.landscape.dimensions"
                        f"[{dimension_index - 1}].paper_ids"
                    ),
                ),
            )
            _extend_unique(
                contribution_ids,
                _required_string_list(
                    dimension.get("contribution_ids"),
                    "framework.dimension_contributions_missing",
                    (
                        "$.landscape.dimensions"
                        f"[{dimension_index - 1}].contribution_ids"
                    ),
                ),
            )
        for controversy_id in _ordered_selected_ids(
            section["controversy_ids"],
            controversies,
        ):
            disagreement = controversies[controversy_id]
            positions = disagreement.get("positions")
            if not isinstance(positions, list) or len(positions) < 2:
                raise ReviewFrameworkContractError(
                    "framework.controversy_positions_missing",
                    "争议必须至少包含两个立场。",
                    path=(
                        "$.landscape.disagreements"
                        f"[{controversies[controversy_id]['_source_index']}]"
                        ".positions"
                    ),
                )
            for position_index, position in enumerate(positions):
                if not isinstance(position, dict):
                    raise ReviewFrameworkContractError(
                        "framework.controversy_position_invalid",
                        "争议立场必须是JSON对象。",
                        path=(
                            "$.landscape.disagreements"
                            f"[{disagreement['_source_index']}]"
                            f".positions[{position_index}]"
                        ),
                    )
                _extend_unique(
                    paper_ids,
                    _required_string_list(
                        position.get("paper_ids"),
                        "framework.controversy_papers_missing",
                        (
                            "$.landscape.disagreements"
                            f"[{disagreement['_source_index']}]"
                            f".positions[{position_index}].paper_ids"
                        ),
                    ),
                )
                _extend_unique(
                    contribution_ids,
                    _required_string_list(
                        position.get("contribution_ids"),
                        "framework.controversy_contributions_missing",
                        (
                            "$.landscape.disagreements"
                            f"[{disagreement['_source_index']}]"
                            f".positions[{position_index}]"
                            ".contribution_ids"
                        ),
                    ),
                )

        citation_keys = [
            str(references[paper_id]["citation_key"])
            for paper_id in paper_ids
        ]
        for comparison_index, comparison in enumerate(
            section["required_comparisons"]
        ):
            outside_papers = [
                paper_id
                for paper_id in corpus_paper_ids
                if paper_id in comparison and paper_id not in paper_ids
            ]
            if outside_papers:
                raise ReviewFrameworkContractError(
                    "framework.comparison_source_outside_section",
                    "比较任务点名了未绑定到本节的论文；应加入该论文所属"
                    f"dimension，而不是让下游自行补资料：{outside_papers}",
                    path=(
                        f"$.sections[{section['section_index'] - 1}]"
                        f".required_comparisons[{comparison_index}]"
                    ),
                )
        bibliography_status = (
            "complete"
            if all(
                references[paper_id]["status"] == "complete"
                for paper_id in paper_ids
            )
            else "partial"
        )
        public_section = copy.deepcopy(section)
        public_section["dimension_indexes"] = sorted(
            public_section["dimension_indexes"]
        )
        public_section["controversy_ids"] = _ordered_selected_ids(
            public_section["controversy_ids"],
            controversies,
        )
        public_section["corpus_gap_indexes"] = sorted(
            public_section["corpus_gap_indexes"]
        )
        public_section["paper_ids"] = paper_ids
        public_section["contribution_ids"] = contribution_ids
        public_section["citation_keys"] = citation_keys
        public_section["bibliography_status"] = bibliography_status
        public_section["section_id"] = _stable_id(
            "section",
            {
                "source_landscape_id": resolved_landscape_id,
                "section": {
                    key: value
                    for key, value in public_section.items()
                    if key not in {
                        "citation_keys",
                        "bibliography_status",
                    }
                },
            },
        )
        section_rows.append(public_section)
        if section["section_type"] == "body":
            _extend_unique(body_paper_ids, paper_ids)

    bibliography_issues = [
        {
            "paper_id": paper_id,
            "citation_key": references[paper_id]["citation_key"],
            "status": references[paper_id]["status"],
            "missing_fields": copy.deepcopy(
                references[paper_id]["missing_fields"]
            ),
            "warnings": copy.deepcopy(references[paper_id]["warnings"]),
        }
        for paper_id in corpus_paper_ids
        if references[paper_id]["status"] != "complete"
    ]
    result = {
        "schema_version": REVIEW_FRAMEWORK_SCHEMA_VERSION,
        "topic": validated["topic"],
        "review_goal": validated["review_goal"],
        "corpus_scope": corpus_scope,
        "source_landscape_id": resolved_landscape_id,
        "working_title": validated["working_title"],
        "central_question": validated["central_question"],
        "sections": section_rows,
        "unused_papers": [
            paper_id
            for paper_id in corpus_paper_ids
            if paper_id not in set(body_paper_ids)
        ],
        "bibliography_issues": bibliography_issues,
    }
    result["framework_id"] = _stable_id(
        "framework",
        {
            "source_landscape_id": resolved_landscape_id,
            "topic": result["topic"],
            "review_goal": result["review_goal"],
            "working_title": result["working_title"],
            "central_question": result["central_question"],
            "sections": [
                {
                    key: value
                    for key, value in section.items()
                    if key not in {
                        "section_id",
                        "citation_keys",
                        "bibliography_status",
                    }
                }
                for section in section_rows
            ],
        },
    )
    return result


def _landscape_dimensions(
    landscape: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    rows = landscape.get("dimensions")
    if not isinstance(rows, list) or not rows:
        raise ReviewFrameworkContractError(
            "framework.landscape_dimensions_missing",
            "Research Landscape必须包含非空dimensions。",
            path="$.landscape.dimensions",
        )
    dimensions: dict[int, dict[str, Any]] = {}
    for source_index, row in enumerate(rows):
        path = f"$.landscape.dimensions[{source_index}]"
        if not isinstance(row, dict):
            raise ReviewFrameworkContractError(
                "framework.landscape_dimension_invalid",
                "Landscape维度必须是JSON对象。",
                path=path,
            )
        dimension_index = row.get("dimension_index")
        if type(dimension_index) is not int or dimension_index < 1:
            raise ReviewFrameworkContractError(
                "framework.landscape_dimension_index_invalid",
                "dimension_index必须是正整数。",
                path=f"{path}.dimension_index",
            )
        if dimension_index in dimensions:
            raise ReviewFrameworkContractError(
                "framework.landscape_dimension_duplicate",
                f"Landscape维度序号重复：{dimension_index}",
                path=f"{path}.dimension_index",
            )
        dimensions[dimension_index] = row
    expected = list(range(1, len(rows) + 1))
    if list(dimensions) != expected:
        raise ReviewFrameworkContractError(
            "framework.landscape_dimension_order_invalid",
            "Landscape维度必须按顺序从1连续编号。",
            path="$.landscape.dimensions",
        )
    return dimensions


def _landscape_controversies(
    landscape: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    rows = landscape.get("disagreements")
    if not isinstance(rows, list):
        raise ReviewFrameworkContractError(
            "framework.landscape_disagreements_invalid",
            "Research Landscape的disagreements必须是数组。",
            path="$.landscape.disagreements",
        )
    controversies: dict[str, dict[str, Any]] = {}
    for source_index, row in enumerate(rows):
        path = f"$.landscape.disagreements[{source_index}]"
        if not isinstance(row, dict):
            raise ReviewFrameworkContractError(
                "framework.landscape_disagreement_invalid",
                "Landscape争议必须是JSON对象。",
                path=path,
            )
        controversy_id = row.get("disagreement_id")
        if not isinstance(controversy_id, str) or not controversy_id.strip():
            raise ReviewFrameworkContractError(
                "framework.landscape_controversy_id_missing",
                "Landscape争议缺少disagreement_id。",
                path=f"{path}.disagreement_id",
            )
        if controversy_id in controversies:
            raise ReviewFrameworkContractError(
                "framework.landscape_controversy_id_duplicate",
                f"Landscape争议ID重复：{controversy_id}",
                path=f"{path}.disagreement_id",
            )
        resolved = copy.deepcopy(row)
        resolved["_source_index"] = source_index
        controversies[controversy_id] = resolved
    return controversies


def _landscape_corpus_gap_indexes(
    landscape: dict[str, Any],
) -> list[int]:
    rows = landscape.get("corpus_gaps")
    if not isinstance(rows, list):
        raise ReviewFrameworkContractError(
            "framework.landscape_corpus_gaps_invalid",
            "Research Landscape的corpus_gaps必须是数组。",
            path="$.landscape.corpus_gaps",
        )
    return list(range(1, len(rows) + 1))


def _landscape_paper_ids(
    landscape: dict[str, Any],
    *,
    dimensions: dict[int, dict[str, Any]],
    controversies: dict[str, dict[str, Any]],
) -> list[str]:
    paper_ids: list[str] = []
    for dimension_index, dimension in dimensions.items():
        _extend_unique(
            paper_ids,
            _required_string_list(
                dimension.get("paper_ids"),
                "framework.dimension_papers_missing",
                (
                    "$.landscape.dimensions"
                    f"[{dimension_index - 1}].paper_ids"
                ),
            ),
        )
        _required_string_list(
            dimension.get("contribution_ids"),
            "framework.dimension_contributions_missing",
            (
                "$.landscape.dimensions"
                f"[{dimension_index - 1}].contribution_ids"
            ),
        )
    for disagreement in controversies.values():
        positions = disagreement.get("positions")
        if not isinstance(positions, list):
            raise ReviewFrameworkContractError(
                "framework.controversy_positions_missing",
                "Landscape争议缺少positions数组。",
                path=(
                    "$.landscape.disagreements"
                    f"[{disagreement['_source_index']}].positions"
                ),
            )
        for position_index, position in enumerate(positions):
            path = (
                "$.landscape.disagreements"
                f"[{disagreement['_source_index']}].positions"
                f"[{position_index}]"
            )
            if not isinstance(position, dict):
                raise ReviewFrameworkContractError(
                    "framework.controversy_position_invalid",
                    "争议立场必须是JSON对象。",
                    path=path,
                )
            _extend_unique(
                paper_ids,
                _required_string_list(
                    position.get("paper_ids"),
                    "framework.controversy_papers_missing",
                    f"{path}.paper_ids",
                ),
            )
            _required_string_list(
                position.get("contribution_ids"),
                "framework.controversy_contributions_missing",
                f"{path}.contribution_ids",
            )
    unmapped = landscape.get("unmapped_papers", [])
    if not isinstance(unmapped, list):
        raise ReviewFrameworkContractError(
            "framework.landscape_unmapped_invalid",
            "Landscape的unmapped_papers必须是数组。",
            path="$.landscape.unmapped_papers",
        )
    for source_index, row in enumerate(unmapped):
        if not isinstance(row, dict):
            raise ReviewFrameworkContractError(
                "framework.landscape_unmapped_invalid",
                "unmapped_papers条目必须是JSON对象。",
                path=f"$.landscape.unmapped_papers[{source_index}]",
            )
        paper_id = row.get("paper_id")
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise ReviewFrameworkContractError(
                "framework.landscape_unmapped_paper_missing",
                "unmapped_papers条目缺少paper_id。",
                path=(
                    f"$.landscape.unmapped_papers[{source_index}].paper_id"
                ),
            )
        _extend_unique(paper_ids, [paper_id])
    return paper_ids


def _validate_references(
    reference_by_paper: dict[str, dict[str, Any]],
    paper_ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(reference_by_paper, dict):
        raise ReviewFrameworkContractError(
            "framework.reference_map_invalid",
            "reference_by_paper必须是JSON对象映射。",
            path="$.reference_by_paper",
        )
    unknown = sorted(set(reference_by_paper) - set(paper_ids))
    if unknown:
        raise ReviewFrameworkContractError(
            "framework.reference_paper_unknown",
            f"题录映射包含不在Landscape中的论文：{unknown}",
            path="$.reference_by_paper",
        )
    resolved: dict[str, dict[str, Any]] = {}
    citation_owner: dict[str, str] = {}
    for paper_id in paper_ids:
        path = f"$.reference_by_paper[{json.dumps(paper_id, ensure_ascii=False)}]"
        reference = reference_by_paper.get(paper_id)
        if not isinstance(reference, dict):
            raise ReviewFrameworkContractError(
                "framework.reference_missing",
                f"论文缺少题录映射：{paper_id}",
                path=path,
            )
        declared_paper_id = reference.get("paper_id")
        if declared_paper_id is not None and declared_paper_id != paper_id:
            raise ReviewFrameworkContractError(
                "framework.reference_identity_mismatch",
                "题录中的paper_id与映射键不一致。",
                path=f"{path}.paper_id",
            )
        citation_key = reference.get("citation_key")
        if citation_key is None:
            reference_id = reference.get("reference_id")
            if isinstance(reference_id, str) and reference_id.strip():
                citation_key = (
                    "ref_"
                    + reference_id.strip().removeprefix("ref-")
                )
        if not isinstance(citation_key, str) or not citation_key.strip():
            raise ReviewFrameworkContractError(
                "framework.citation_key_missing",
                "题录缺少citation_key或可转换的reference_id。",
                path=f"{path}.citation_key",
            )
        citation_key = citation_key.strip()
        owner = citation_owner.get(citation_key)
        if owner is not None and owner != paper_id:
            raise ReviewFrameworkContractError(
                "framework.citation_key_duplicate",
                f"多个论文共享同一citation_key：{owner}, {paper_id}",
                path=f"{path}.citation_key",
            )
        citation_owner[citation_key] = paper_id
        status = reference.get("status")
        if status not in BIBLIOGRAPHY_STATUSES:
            raise ReviewFrameworkContractError(
                "framework.bibliography_status_invalid",
                "题录status必须是complete或partial。",
                path=f"{path}.status",
            )
        missing_fields = reference.get("missing_fields", [])
        warnings = reference.get("warnings", [])
        if not _is_unique_string_list(missing_fields):
            raise ReviewFrameworkContractError(
                "framework.reference_missing_fields_invalid",
                "missing_fields必须是不重复字符串数组。",
                path=f"{path}.missing_fields",
            )
        if not _is_unique_string_list(warnings):
            raise ReviewFrameworkContractError(
                "framework.reference_warnings_invalid",
                "warnings必须是不重复字符串数组。",
                path=f"{path}.warnings",
            )
        resolved[paper_id] = {
            "citation_key": citation_key,
            "status": status,
            "missing_fields": list(missing_fields),
            "warnings": list(warnings),
        }
    return resolved


def _ordered_selected_ids(
    selected: list[str],
    known: dict[str, dict[str, Any]],
) -> list[str]:
    selected_set = set(selected)
    return [value for value in known if value in selected_set]


def _enum_or_never(values: list[Any], value_type: str) -> dict[str, Any]:
    if not values:
        return {"not": {}}
    return {"type": value_type, "enum": values}


def _landscape_text(landscape: dict[str, Any], field: str) -> str:
    value = landscape.get(field)
    return _required_contract_text(
        value,
        f"framework.landscape_{field}_missing",
        f"$.landscape.{field}",
    )


def _required_string_list(
    value: object,
    code: str,
    path: str,
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ReviewFrameworkContractError(
            code,
            "必须是非空字符串数组。",
            path=path,
        )
    if not _is_unique_string_list(value):
        raise ReviewFrameworkContractError(
            code,
            "必须是不重复的非空字符串数组。",
            path=path,
        )
    return list(value)


def _is_unique_string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(
            isinstance(item, str) and bool(item.strip())
            for item in value
        )
        and len(value) == len(set(value))
    )


def _extend_unique(target: list[str], values: list[str]) -> None:
    known = set(target)
    for value in values:
        if value not in known:
            target.append(value)
            known.add(value)


def _stable_id(prefix: str, payload: object) -> str:
    compact = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _validated_positive_indexes(
    values: list[int],
    field: str,
    *,
    allow_empty: bool,
) -> list[int]:
    if not isinstance(values, list) or (not values and not allow_empty):
        requirement = "数组" if allow_empty else "非空数组"
        raise ValueError(f"{field}必须是{requirement}。")
    if any(type(value) is not int or value < 1 for value in values):
        raise ValueError(f"{field}必须只包含正整数。")
    if len(values) != len(set(values)):
        raise ValueError(f"{field}不得重复。")
    return list(values)


def _validated_ids(
    values: list[str],
    field: str,
    *,
    allow_empty: bool,
) -> list[str]:
    if not isinstance(values, list) or (not values and not allow_empty):
        requirement = "数组" if allow_empty else "非空数组"
        raise ValueError(f"{field}必须是{requirement}。")
    result: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field}[{index}]必须是非空字符串。")
        result.append(value.strip())
    if len(result) != len(set(result)):
        raise ValueError(f"{field}不得重复。")
    return result


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}必须是非空字符串。")
    return value.strip()


def _required_contract_text(value: object, code: str, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewFrameworkContractError(
            code,
            "必须是非空字符串。",
            path=path,
        )
    return value.strip()


def _validate_schema(
    payload: object,
    schema: dict[str, Any],
    code: str,
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
        raise ReviewFrameworkContractError(
            code,
            error.message,
            path=path,
        )
    if not isinstance(payload, dict):
        raise ReviewFrameworkContractError(
            code,
            "Review Framework草案必须是JSON对象。",
        )
    return copy.deepcopy(payload)
