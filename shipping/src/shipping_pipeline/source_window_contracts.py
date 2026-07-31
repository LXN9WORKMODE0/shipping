from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


SOURCE_WINDOW_SCHEMA_VERSION = "llm.selected_source_window.v1"
SOURCE_WINDOW_SELECTION_SCHEMA_VERSION = "llm.source_window_selection.v1"
SOURCE_WINDOW_RUN_SCHEMA_VERSION = "llm.source_window_run.v1"
MATERIAL_TIERS = ("tier_1", "tier_2", "tier_3")
WINDOW_TYPES = (
    "problem_context",
    "method_context",
    "result_context",
    "limitation_context",
    "author_conclusion",
    "implementation_status",
)


class SourceWindowContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


def validate_source_window_selection(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SourceWindowContractError(
            "source_window.selection_invalid",
            "Source Window selection必须是JSON对象。",
        )
    required = {
        "schema_version",
        "source",
        "section",
        "material_tier",
        "understanding_projections",
        "selected_source_windows",
        "expanded_markdown_sources",
        "citation_metadata",
        "coverage",
        "selection_id",
    }
    if set(payload) != required:
        raise SourceWindowContractError(
            "source_window.selection_fields_invalid",
            "Source Window selection字段不匹配："
            f"missing={sorted(required - set(payload))}, "
            f"unknown={sorted(set(payload) - required)}。",
        )
    if payload["schema_version"] != SOURCE_WINDOW_SELECTION_SCHEMA_VERSION:
        raise SourceWindowContractError(
            "source_window.selection_schema_invalid",
            "Source Window selection Schema版本不受支持。",
            path="$.schema_version",
        )
    source = _object(payload["source"], "$.source")
    for field in (
        "framework_run_id",
        "framework_id",
        "framework_manifest_sha256",
        "framework_output_sha256",
        "landscape_run_id",
        "landscape_id",
    ):
        _required_text(source.get(field), f"$.source.{field}")
    section = _object(payload["section"], "$.section")
    section_id = _required_text(section.get("section_id"), "$.section.section_id")
    paper_ids = _unique_texts(section.get("paper_ids"), "$.section.paper_ids")
    contribution_ids = _unique_texts(
        section.get("contribution_ids"),
        "$.section.contribution_ids",
    )
    citation_keys = _unique_texts(
        section.get("citation_keys"),
        "$.section.citation_keys",
    )
    if len(paper_ids) != len(citation_keys):
        raise SourceWindowContractError(
            "source_window.section_citation_count_mismatch",
            "章节paper_ids与citation_keys数量不一致。",
            path="$.section",
        )
    tier = payload["material_tier"]
    if tier not in MATERIAL_TIERS:
        raise SourceWindowContractError(
            "source_window.material_tier_invalid",
            f"material_tier必须是以下值之一：{list(MATERIAL_TIERS)}。",
            path="$.material_tier",
        )

    projections = _object_list(
        payload["understanding_projections"],
        "$.understanding_projections",
    )
    projection_papers = [
        _required_text(
            row.get("paper_id"),
            f"$.understanding_projections[{index}].paper_id",
        )
        for index, row in enumerate(projections)
    ]
    if projection_papers != paper_ids:
        raise SourceWindowContractError(
            "source_window.projection_papers_mismatch",
            "Understanding投影必须按章节paper_ids顺序完整覆盖。",
            path="$.understanding_projections",
        )

    windows = _object_list(
        payload["selected_source_windows"],
        "$.selected_source_windows",
    )
    if not windows:
        raise SourceWindowContractError(
            "source_window.windows_empty",
            "Tier 1 Source Window不能为空。",
            path="$.selected_source_windows",
        )
    window_ids: list[str] = []
    material_ids: list[str] = []
    citation_by_paper = dict(zip(paper_ids, citation_keys, strict=True))
    for index, window in enumerate(windows):
        path = f"$.selected_source_windows[{index}]"
        _validate_window(
            window,
            path=path,
            section_id=section_id,
            citation_by_paper=citation_by_paper,
            contribution_ids=set(contribution_ids),
        )
        window_ids.append(str(window["window_id"]))
        material_ids.extend(str(value) for value in window["material_ids"])
    _reject_duplicates(
        window_ids,
        "source_window.window_id_duplicate",
        "$.selected_source_windows",
    )

    expanded = _object_list(
        payload["expanded_markdown_sources"],
        "$.expanded_markdown_sources",
    )
    if tier == "tier_1" and expanded:
        raise SourceWindowContractError(
            "source_window.tier_1_markdown_forbidden",
            "Tier 1不得展开完整Markdown。",
            path="$.expanded_markdown_sources",
        )
    expanded_papers: list[str] = []
    for index, row in enumerate(expanded):
        path = f"$.expanded_markdown_sources[{index}]"
        paper_id = _required_text(row.get("paper_id"), f"{path}.paper_id")
        citation_key = _required_text(
            row.get("citation_key"),
            f"{path}.citation_key",
        )
        _required_text(row.get("markdown"), f"{path}.markdown")
        if citation_by_paper.get(paper_id) != citation_key:
            raise SourceWindowContractError(
                "source_window.expanded_source_mismatch",
                "展开Markdown的论文与citation key不匹配。",
                path=path,
            )
        expanded_papers.append(paper_id)
    _reject_duplicates(
        expanded_papers,
        "source_window.expanded_paper_duplicate",
        "$.expanded_markdown_sources",
    )
    if tier == "tier_2" and expanded_papers != paper_ids:
        raise SourceWindowContractError(
            "source_window.tier_2_papers_incomplete",
            "Tier 2必须按章节顺序展开全部章节论文Markdown。",
            path="$.expanded_markdown_sources",
        )
    if tier == "tier_3" and expanded_papers != paper_ids:
        raise SourceWindowContractError(
            "source_window.tier_3_papers_incomplete",
            "Tier 3当前仅支持完整覆盖全部语料的章节。",
            path="$.expanded_markdown_sources",
        )

    metadata = _object_list(
        payload["citation_metadata"],
        "$.citation_metadata",
    )
    metadata_papers = [
        _required_text(
            row.get("paper_id"),
            f"$.citation_metadata[{index}].paper_id",
        )
        for index, row in enumerate(metadata)
    ]
    metadata_keys = [
        _required_text(
            row.get("citation_key"),
            f"$.citation_metadata[{index}].citation_key",
        )
        for index, row in enumerate(metadata)
    ]
    if metadata_papers != paper_ids or metadata_keys != citation_keys:
        raise SourceWindowContractError(
            "source_window.citation_metadata_mismatch",
            "citation metadata必须按章节论文和citation key顺序完整覆盖。",
            path="$.citation_metadata",
        )

    coverage = _object(payload["coverage"], "$.coverage")
    required_coverage_fields = {
        "required_contribution_ids",
        "covered_contribution_ids",
        "uncovered_contribution_ids",
        "required_material_ids",
        "covered_material_ids",
        "uncovered_material_ids",
        "window_count",
        "paper_count",
        "window_type_counts",
        "papers_without_method_context",
        "papers_without_limitation_context",
        "deduplicated_candidate_count",
    }
    if set(coverage) != required_coverage_fields:
        raise SourceWindowContractError(
            "source_window.coverage_fields_invalid",
            "coverage字段不匹配。",
            path="$.coverage",
        )
    required_contributions = _unique_texts(
        coverage["required_contribution_ids"],
        "$.coverage.required_contribution_ids",
    )
    covered_contributions = _unique_texts(
        coverage["covered_contribution_ids"],
        "$.coverage.covered_contribution_ids",
        allow_empty=True,
    )
    uncovered_contributions = _unique_texts(
        coverage["uncovered_contribution_ids"],
        "$.coverage.uncovered_contribution_ids",
        allow_empty=True,
    )
    if required_contributions != contribution_ids:
        raise SourceWindowContractError(
            "source_window.coverage_contributions_mismatch",
            "coverage中的required contribution与章节不一致。",
            path="$.coverage.required_contribution_ids",
        )
    if set(covered_contributions) | set(uncovered_contributions) != set(
        required_contributions
    ):
        raise SourceWindowContractError(
            "source_window.coverage_partition_invalid",
            "covered与uncovered contribution未构成完整分区。",
            path="$.coverage",
        )
    if uncovered_contributions:
        raise SourceWindowContractError(
            "source_window.contribution_uncovered",
            f"存在未覆盖contribution：{uncovered_contributions}。",
            path="$.coverage.uncovered_contribution_ids",
        )
    required_materials = _unique_texts(
        coverage["required_material_ids"],
        "$.coverage.required_material_ids",
    )
    covered_materials = _unique_texts(
        coverage["covered_material_ids"],
        "$.coverage.covered_material_ids",
        allow_empty=True,
    )
    uncovered_materials = _unique_texts(
        coverage["uncovered_material_ids"],
        "$.coverage.uncovered_material_ids",
        allow_empty=True,
    )
    if set(covered_materials) | set(uncovered_materials) != set(
        required_materials
    ):
        raise SourceWindowContractError(
            "source_window.coverage_material_partition_invalid",
            "covered与uncovered material未构成完整分区。",
            path="$.coverage",
        )
    if uncovered_materials:
        raise SourceWindowContractError(
            "source_window.material_uncovered",
            f"存在未覆盖material：{uncovered_materials}。",
            path="$.coverage.uncovered_material_ids",
        )
    if not set(required_materials).issubset(set(material_ids)):
        raise SourceWindowContractError(
            "source_window.required_material_missing",
            "窗口未包含全部required material。",
            path="$.selected_source_windows",
        )
    if coverage["window_count"] != len(windows):
        raise SourceWindowContractError(
            "source_window.window_count_mismatch",
            "coverage.window_count与窗口数量不一致。",
            path="$.coverage.window_count",
        )
    if coverage["paper_count"] != len(paper_ids):
        raise SourceWindowContractError(
            "source_window.paper_count_mismatch",
            "coverage.paper_count与章节论文数不一致。",
            path="$.coverage.paper_count",
        )

    expected_id = stable_id(
        "source_selection",
        {key: value for key, value in payload.items() if key != "selection_id"},
    )
    if payload["selection_id"] != expected_id:
        raise SourceWindowContractError(
            "source_window.selection_id_mismatch",
            "selection_id无法由正式内容重建。",
            path="$.selection_id",
        )
    return copy.deepcopy(payload)


def stable_id(prefix: str, payload: object) -> str:
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"{prefix}_{digest[:20]}"


def sha256_json(payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _validate_window(
    window: dict[str, Any],
    *,
    path: str,
    section_id: str,
    citation_by_paper: dict[str, str],
    contribution_ids: set[str],
) -> None:
    required = {
        "schema_version",
        "window_id",
        "section_id",
        "paper_id",
        "citation_key",
        "window_type",
        "text",
        "heading_path",
        "source_spans",
        "material_ids",
        "contribution_ids",
        "evidence_unit_ids",
        "result_types",
        "validation_levels",
        "contains_limitation",
        "parse_flags",
        "quality_flags",
        "selection_reasons",
        "source_fingerprints",
    }
    if set(window) != required:
        raise SourceWindowContractError(
            "source_window.window_fields_invalid",
            "Source Window字段不匹配。",
            path=path,
        )
    if window["schema_version"] != SOURCE_WINDOW_SCHEMA_VERSION:
        raise SourceWindowContractError(
            "source_window.window_schema_invalid",
            "Source Window Schema版本不受支持。",
            path=f"{path}.schema_version",
        )
    _required_text(window["window_id"], f"{path}.window_id")
    if window["section_id"] != section_id:
        raise SourceWindowContractError(
            "source_window.section_id_mismatch",
            "窗口section_id与selection章节不一致。",
            path=f"{path}.section_id",
        )
    paper_id = _required_text(window["paper_id"], f"{path}.paper_id")
    citation_key = _required_text(
        window["citation_key"],
        f"{path}.citation_key",
    )
    if citation_by_paper.get(paper_id) != citation_key:
        raise SourceWindowContractError(
            "source_window.citation_key_mismatch",
            "窗口paper_id与citation_key不匹配。",
            path=path,
        )
    if window["window_type"] not in WINDOW_TYPES:
        raise SourceWindowContractError(
            "source_window.window_type_invalid",
            f"window_type必须是以下值之一：{list(WINDOW_TYPES)}。",
            path=f"{path}.window_type",
        )
    _required_text(window["text"], f"{path}.text")
    _unique_texts(
        window["heading_path"],
        f"{path}.heading_path",
        allow_empty=True,
    )
    material_ids = _unique_texts(
        window["material_ids"],
        f"{path}.material_ids",
    )
    selected_contributions = _unique_texts(
        window["contribution_ids"],
        f"{path}.contribution_ids",
    )
    unknown = sorted(set(selected_contributions) - contribution_ids)
    if unknown:
        raise SourceWindowContractError(
            "source_window.contribution_unknown",
            f"窗口引用未知contribution：{unknown}。",
            path=f"{path}.contribution_ids",
        )
    _unique_texts(
        window["evidence_unit_ids"],
        f"{path}.evidence_unit_ids",
        allow_empty=True,
    )
    _unique_texts(
        window["result_types"],
        f"{path}.result_types",
        allow_empty=True,
    )
    _unique_texts(
        window["validation_levels"],
        f"{path}.validation_levels",
        allow_empty=True,
    )
    _unique_texts(
        window["parse_flags"],
        f"{path}.parse_flags",
        allow_empty=True,
    )
    _unique_texts(
        window["quality_flags"],
        f"{path}.quality_flags",
        allow_empty=True,
    )
    _unique_texts(
        window["selection_reasons"],
        f"{path}.selection_reasons",
    )
    _unique_texts(
        window["source_fingerprints"],
        f"{path}.source_fingerprints",
        allow_empty=True,
    )
    if type(window["contains_limitation"]) is not bool:
        raise SourceWindowContractError(
            "source_window.contains_limitation_invalid",
            "contains_limitation必须是布尔值。",
            path=f"{path}.contains_limitation",
        )
    spans = _object_list(window["source_spans"], f"{path}.source_spans")
    if not spans:
        raise SourceWindowContractError(
            "source_window.source_spans_empty",
            "Source Window必须绑定至少一个原文坐标。",
            path=f"{path}.source_spans",
        )
    for span_index, span in enumerate(spans):
        _validate_span(span, f"{path}.source_spans[{span_index}]")
    expected_window_id = stable_id(
        "window",
        {
            "section_id": section_id,
            "paper_id": paper_id,
            "text": window["text"],
            "source_spans": spans,
            "material_ids": material_ids,
        },
    )
    if window["window_id"] != expected_window_id:
        raise SourceWindowContractError(
            "source_window.window_id_mismatch",
            "window_id无法由来源和正文重建。",
            path=f"{path}.window_id",
        )


def _validate_span(span: dict[str, Any], path: str) -> None:
    required = {"path", "start_line", "end_line", "start_char", "end_char"}
    if set(span) != required:
        raise SourceWindowContractError(
            "source_window.span_fields_invalid",
            "source span字段不匹配。",
            path=path,
        )
    _required_text(span["path"], f"{path}.path")
    for field in ("start_line", "end_line"):
        value = span[field]
        if type(value) is not int or value < 1:
            raise SourceWindowContractError(
                "source_window.span_line_invalid",
                f"{field}必须是正整数。",
                path=f"{path}.{field}",
            )
    for field in ("start_char", "end_char"):
        value = span[field]
        if type(value) is not int or value < 0:
            raise SourceWindowContractError(
                "source_window.span_char_invalid",
                f"{field}必须是非负整数。",
                path=f"{path}.{field}",
            )
    if span["end_line"] < span["start_line"]:
        raise SourceWindowContractError(
            "source_window.span_line_order_invalid",
            "end_line不得小于start_line。",
            path=path,
        )
    if (
        span["start_line"] == span["end_line"]
        and span["end_char"] < span["start_char"]
    ):
        raise SourceWindowContractError(
            "source_window.span_char_order_invalid",
            "同一行内end_char不得小于start_char。",
            path=path,
        )


def _object(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourceWindowContractError(
            "source_window.object_expected",
            "字段必须是JSON对象。",
            path=path,
        )
    return value


def _object_list(value: object, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(row, dict) for row in value
    ):
        raise SourceWindowContractError(
            "source_window.object_list_expected",
            "字段必须是JSON对象数组。",
            path=path,
        )
    return value


def _required_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceWindowContractError(
            "source_window.text_required",
            "字段必须是非空字符串。",
            path=path,
        )
    return value.strip()


def _unique_texts(
    value: object,
    path: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise SourceWindowContractError(
            "source_window.text_list_expected",
            "字段必须是字符串数组。",
            path=path,
        )
    result = [
        _required_text(row, f"{path}[{index}]")
        for index, row in enumerate(value)
    ]
    if not allow_empty and not result:
        raise SourceWindowContractError(
            "source_window.text_list_empty",
            "字段不得为空数组。",
            path=path,
        )
    _reject_duplicates(
        result,
        "source_window.text_list_duplicate",
        path,
    )
    return result


def _reject_duplicates(values: list[str], code: str, path: str) -> None:
    duplicates = sorted(
        {value for value in values if values.count(value) > 1}
    )
    if duplicates:
        raise SourceWindowContractError(
            code,
            f"字段存在重复值：{duplicates}。",
            path=path,
        )
