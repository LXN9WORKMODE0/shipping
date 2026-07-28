from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from jsonschema import Draft202012Validator

from .llm_contracts import (
    EVIDENCE_BATCH_SCHEMA_VERSION,
    ContractViolation,
    extract_numeric_facts,
    validate_evidence_batch,
)
from .llm_model_profile import ModelProfile
from .llm_planner import PlanningError
from .llm_table_evidence import table_percentage_candidates_for_claim


EVIDENCE_CORRECTION_SCHEMA_VERSION = "llm.evidence_batch_correction.v1"

EVIDENCE_CORRECTION_SYSTEM_PROMPT = """你负责修正一次已经失败的证据抽取响应，而不是重新抽取论文。
每个 material_id 都属于同一篇论文，但必须按 Card 隔离处理。
你必须对每个原始 evidence unit 恰好给出一个动作：
- keep：原单元保持不变；
- replace：仅修正原单元的 claim 与 citations，可把一个过宽观点拆成多个更窄观点；
- delete：原单元无法在当前 Card 内修正时删除。
不得新增原响应没有表达的事实，不得改变 evidence_type、relevance、confidence 或 caveats。
replace 只能使用当前 material_id 列出的 quote_id；数值、百分比、条件和模态词必须得到所选逐字引文支持。
通常，百分号必须与对应数值同时出现在同一条逐字引文中。
唯一例外是失败详情明确列出的 table_composite_candidates：此时每条 replacement 只能保留一个这类百分比事实，claim 必须逐字包含指定的 row_label 和 column_label，并同时引用对应 header_quote_id 与 row_quote_id。
不得自行组合失败详情没有列出的表头和数据行。一个原观点含有多个表格复合百分比时，必须拆成多个原子 replacement；无法唯一对应时删除百分号或删除该单元。
所有数值型表格观点都必须同时引用完整表头和对应数据行；即使百分号已经直接写在数据行中，也不能省略定义列语义的表头。
表格的最高、最低、增减或排序结论必须有覆盖比较范围的全部数据行支持；只有局部数据行时只能陈述对应单元格值。
跨 Card 的证据不得移动到另一张 Card；若原 claim 实际来自别的 Card，而当前 Card 没有支持该 claim 的引文，必须 delete，禁止改写成当前 Card 的另一条新事实。
除失败单元外应使用 keep；不得为了统一措辞重写原本有效的单元。
同一 material_correction 的所有 replacements 必须全局去重；对于表格事实，相同 header_quote_id、row_quote_id、列和数值即视为同一事实，即使 claim 措辞不同也只能保留一次。如果不同原单元表达了同一事实，只在语义更直接的原单元下保留一次，其他位置删除该重复 replacement。禁止依赖程序自动去重。
不得输出 schema 之外的字段。"""


class EvidenceCorrectionUnavailable(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class PlannedEvidenceCorrection:
    request_id: str
    source_request_id: str
    material_ids: tuple[str, ...]
    input_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int
    prompt_sha256: str


def collect_correctable_material_failures(
    payload: object,
    materials: list[dict[str, Any]],
    quote_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != EVIDENCE_BATCH_SCHEMA_VERSION:
        raise EvidenceCorrectionUnavailable(
            "correction.source_payload_unusable",
            "首次响应不是可识别的 evidence batch 对象。",
        )
    rows = payload.get("material_results")
    if not isinstance(rows, list):
        raise EvidenceCorrectionUnavailable(
            "correction.source_material_results_unusable",
            "首次响应没有可枚举的 material_results。",
        )
    expected_ids = [str(row.get("material_id", "")) for row in materials]
    actual_ids = [str(row.get("material_id", "")) for row in rows if isinstance(row, dict)]
    if len(actual_ids) != len(rows) or sorted(actual_ids) != sorted(expected_ids) or len(actual_ids) != len(set(actual_ids)):
        raise EvidenceCorrectionUnavailable(
            "correction.source_material_coverage_ambiguous",
            "首次响应的 material_id 覆盖不完整、重复或包含未知 ID，无法局部修正。",
        )

    material_by_id = {str(row["material_id"]): row for row in materials}
    failures: list[dict[str, Any]] = []
    for result_index, result in enumerate(rows):
        material_id = str(result["material_id"])
        scoped_quotes = [
            row for row in quote_candidates if str(row.get("material_id", "")) == material_id
        ]
        try:
            validate_evidence_batch(
                {
                    "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
                    "material_results": [copy.deepcopy(result)],
                },
                [material_by_id[material_id]],
                scoped_quotes,
            )
        except ContractViolation as material_exc:
            evidence_units = result.get("evidence_units") if isinstance(result, dict) else None
            if result.get("disposition") != "evidence" or not isinstance(evidence_units, list) or not evidence_units:
                continue
            violations: list[dict[str, Any]] = []
            for unit_index, unit in enumerate(evidence_units):
                isolated_result = copy.deepcopy(result)
                isolated_result["evidence_units"] = [copy.deepcopy(unit)]
                try:
                    validate_evidence_batch(
                        {
                            "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
                            "material_results": [isolated_result],
                        },
                        [material_by_id[material_id]],
                        scoped_quotes,
                    )
                except ContractViolation as unit_exc:
                    if not _is_correctable_violation(unit_exc):
                        continue
                    numeric_details = _numeric_failure_details(
                        unit_exc,
                        material_by_id[material_id],
                        scoped_quotes,
                        str(unit.get("claim", "")) if isinstance(unit, dict) else "",
                    )
                    violations.append(
                        {
                            "source_evidence_index": unit_index,
                            "error_code": unit_exc.code,
                            "error_path": _source_unit_error_path(
                                unit_exc.path,
                                result_index=result_index,
                                unit_index=unit_index,
                            ),
                            "error_message": str(unit_exc),
                            **numeric_details,
                        }
                    )
            if not violations and _is_correctable_violation(material_exc):
                numeric_details = _numeric_failure_details(
                    material_exc,
                    material_by_id[material_id],
                    scoped_quotes,
                    "",
                )
                violations.append(
                    {
                        "source_evidence_index": None,
                        "error_code": material_exc.code,
                        "error_path": material_exc.path,
                        "error_message": str(material_exc),
                        **numeric_details,
                    }
                )
            if violations:
                _assign_table_composite_ownership(violations, evidence_units)
                first = violations[0]
                failures.append(
                    {
                        "material_id": material_id,
                        "error_code": first["error_code"],
                        "error_path": first["error_path"],
                        "error_message": first["error_message"],
                        "violations": violations,
                    }
                )
    if not failures:
        raise EvidenceCorrectionUnavailable(
            "correction.no_local_correctable_failure",
            "失败无法精确定位到可修正的 evidence material_result。",
        )
    return failures


def build_evidence_correction_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    source_request_id: str,
    source_payload: dict[str, Any],
    projected_cards: list[dict[str, Any]],
    quote_candidates: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> tuple[str, str]:
    target_ids = [str(row["material_id"]) for row in failures]
    source_by_id = {
        str(row["material_id"]): row for row in source_payload["material_results"]
    }
    projected_by_id = {str(row["material_id"]): row for row in projected_cards}
    quote_rows_by_material = {
        material_id: [
            {"quote_id": str(row["quote_id"]), "text": str(row["text"])}
            for row in quote_candidates
            if str(row.get("material_id", "")) == material_id
        ]
        for material_id in target_ids
    }
    prompt_input = {
        "任务": "对首次失败响应中的指定 material_result 执行唯一一次受限修正",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "综述主题": topic,
        "首次请求ID": source_request_id,
        "失败详情": failures,
        "待修正材料": [
            {
                "material_id": material_id,
                "Card": projected_by_id[material_id],
                "引文候选": quote_rows_by_material[material_id],
                "首次输出": source_by_id[material_id],
            }
            for material_id in target_ids
        ],
        "输出JSONSchema": build_evidence_correction_schema(
            source_results=[source_by_id[material_id] for material_id in target_ids],
            quote_ids_by_material={
                material_id: [row["quote_id"] for row in quote_rows_by_material[material_id]]
                for material_id in target_ids
            },
            failures=failures,
        ),
    }
    return EVIDENCE_CORRECTION_SYSTEM_PROMPT, json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def plan_evidence_correction(
    *,
    generation_id: str,
    source_request_id: str,
    material_ids: list[str],
    system_prompt: str,
    user_prompt: str,
    profile: ModelProfile,
    token_counter: Any,
) -> PlannedEvidenceCorrection:
    count = token_counter.count_messages(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    max_output_tokens = min(
        profile.evidence_output_tokens(len(material_ids)),
        profile.evidence_max_output_tokens,
    )
    if count.prompt_tokens + max_output_tokens + profile.safety_margin_tokens > profile.context_window_tokens:
        raise PlanningError(
            "planning.evidence_correction_context_window_exceeded",
            f"source_request_id={source_request_id!r}, input_tokens={count.prompt_tokens}。",
        )
    prompt_sha256 = _sha256_text(system_prompt + "\n" + user_prompt)
    identity = {
        "paper_generation": generation_id,
        "stage": "evidence_batch_correction",
        "source_request_id": source_request_id,
        "material_ids": material_ids,
        "prompt_sha256": prompt_sha256,
    }
    return PlannedEvidenceCorrection(
        request_id="evidence_batch_correction_" + _sha256_json(identity)[:16],
        source_request_id=source_request_id,
        material_ids=tuple(material_ids),
        input_tokens=count.prompt_tokens,
        max_output_tokens=max_output_tokens,
        safety_margin_tokens=profile.safety_margin_tokens,
        prompt_sha256=prompt_sha256,
    )


def build_evidence_correction_schema(
    *,
    source_results: list[dict[str, Any]],
    quote_ids_by_material: dict[str, list[str]],
    failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    material_ids = [str(row["material_id"]) for row in source_results]
    if not material_ids or len(material_ids) != len(set(material_ids)):
        raise ValueError("source_results 必须包含唯一 material_id。")
    replacement = {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim", "citations"],
        "properties": {
            "claim": {"type": "string", "minLength": 1, "maxLength": 320},
            "citations": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "uniqueItems": True,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["quote_id"],
                    "properties": {"quote_id": {"type": "string"}},
                },
            },
        },
    }
    unit_correction = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_evidence_index", "action", "replacements", "reason"],
        "properties": {
            "source_evidence_index": {"type": "integer", "minimum": 0},
            "action": {"type": "string", "enum": ["keep", "replace", "delete"]},
            "replacements": {"type": "array", "maxItems": 12, "items": replacement},
            "reason": {"type": "string", "minLength": 1, "maxLength": 240},
        },
        "allOf": [
            {
                "if": {"properties": {"action": {"const": "replace"}}, "required": ["action"]},
                "then": {"properties": {"replacements": {"minItems": 1}}},
            },
            {
                "if": {"properties": {"action": {"enum": ["keep", "delete"]}}, "required": ["action"]},
                "then": {"properties": {"replacements": {"maxItems": 0}}},
            },
        ],
    }
    material_correction = {
        "type": "object",
        "additionalProperties": False,
        "required": ["material_id", "unit_corrections"],
        "properties": {
            "material_id": {"type": "string", "enum": material_ids},
            "unit_corrections": {"type": "array", "items": unit_correction},
        },
        "allOf": [],
    }
    for source in source_results:
        material_id = str(source["material_id"])
        unit_count = len(source["evidence_units"])
        scoped_unit = copy.deepcopy(unit_correction)
        scoped_unit["properties"]["source_evidence_index"]["enum"] = list(range(unit_count))
        scoped_replacement = scoped_unit["properties"]["replacements"]["items"]
        scoped_replacement["properties"]["citations"]["items"]["properties"]["quote_id"]["enum"] = list(
            quote_ids_by_material[material_id]
        )
        failure = next(
            (
                row
                for row in failures or []
                if str(row.get("material_id", "")) == material_id
            ),
            None,
        )
        if failure is not None:
            for violation in failure.get("violations", []):
                source_index = violation.get("source_evidence_index")
                if not isinstance(source_index, int):
                    continue
                forbidden = [
                    str(item)
                    for item in violation.get("forbidden_claim_fragments", [])
                    if str(item)
                ]
                claim_constraints = [
                    {"not": {"pattern": _numeric_fragment_pattern(item)}}
                    for item in forbidden
                ]
                replacement_item: dict[str, Any] = {
                    "properties": {
                        "claim": {"allOf": claim_constraints},
                    }
                }
                table_constraints = _table_composite_schema_constraints(
                    list(violation.get("table_composite_candidates", []))
                )
                if table_constraints:
                    replacement_item["allOf"] = table_constraints
                replacement_then: dict[str, Any] = {
                    "items": replacement_item
                }
                scoped_unit["allOf"].append(
                    {
                        "if": {
                            "properties": {"source_evidence_index": {"const": source_index}},
                            "required": ["source_evidence_index"],
                        },
                        "then": {
                            "properties": {
                                "action": {"enum": ["replace", "delete"]},
                                "replacements": replacement_then,
                            }
                        },
                    }
                )
        material_correction["allOf"].append(
            {
                "if": {
                    "properties": {"material_id": {"const": material_id}},
                    "required": ["material_id"],
                },
                "then": {
                    "properties": {
                        "unit_corrections": {
                            "minItems": unit_count,
                            "maxItems": unit_count,
                            "items": scoped_unit,
                        }
                    }
                },
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.evidence_batch_correction.v1.json",
        "title": "证据抽取失败材料的一次性修正",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "material_corrections"],
        "properties": {
            "schema_version": {"const": EVIDENCE_CORRECTION_SCHEMA_VERSION},
            "material_corrections": {
                "type": "array",
                "minItems": len(material_ids),
                "maxItems": len(material_ids),
                "items": material_correction,
            },
        },
    }


def validate_and_apply_evidence_correction(
    correction_payload: object,
    *,
    source_payload: dict[str, Any],
    target_material_ids: list[str],
    materials: list[dict[str, Any]],
    quote_candidates: list[dict[str, Any]],
    failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_by_id = {
        str(row["material_id"]): row for row in source_payload["material_results"]
    }
    source_results = [source_by_id[material_id] for material_id in target_material_ids]
    schema = build_evidence_correction_schema(
        source_results=source_results,
        quote_ids_by_material={
            material_id: [
                str(row["quote_id"])
                for row in quote_candidates
                if str(row.get("material_id", "")) == material_id
            ]
            for material_id in target_material_ids
        },
        failures=failures,
    )
    validated = _validate_schema(
        correction_payload,
        schema,
        "schema.evidence_batch_correction_invalid",
    )
    corrections = validated["material_corrections"]
    correction_ids = [str(row["material_id"]) for row in corrections]
    if sorted(correction_ids) != sorted(target_material_ids) or len(correction_ids) != len(set(correction_ids)):
        raise ContractViolation(
            "coverage.evidence_correction_invalid",
            "material_corrections 必须完整且唯一覆盖目标 material_id。",
            path="$.material_corrections",
        )
    correction_by_id = {str(row["material_id"]): row for row in corrections}
    merged_results: list[dict[str, Any]] = []
    change_records: list[dict[str, Any]] = []
    for source_result in source_payload["material_results"]:
        material_id = str(source_result["material_id"])
        if material_id not in correction_by_id:
            merged_results.append(copy.deepcopy(source_result))
            continue
        merged, changes = _apply_material_correction(
            source_result,
            correction_by_id[material_id],
            quote_candidates,
        )
        merged_results.append(merged)
        change_records.extend(changes)
    merged_payload = {
        "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
        "material_results": merged_results,
    }
    normalized = validate_evidence_batch(merged_payload, materials, quote_candidates)
    return {
        "schema_version": EVIDENCE_CORRECTION_SCHEMA_VERSION,
        "source_payload": copy.deepcopy(source_payload),
        "correction": copy.deepcopy(validated),
        "merged_payload": merged_payload,
        "normalized": normalized,
        "changes": change_records,
    }


def _apply_material_correction(
    source_result: dict[str, Any],
    correction: dict[str, Any],
    quote_candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    material_id = str(source_result["material_id"])
    source_units = list(source_result["evidence_units"])
    rows = correction["unit_corrections"]
    indices = [int(row["source_evidence_index"]) for row in rows]
    if sorted(indices) != list(range(len(source_units))) or len(indices) != len(set(indices)):
        raise ContractViolation(
            "coverage.evidence_unit_correction_invalid",
            "unit_corrections 必须对每个原始 evidence unit 恰好处理一次。",
            path=f"$.material_corrections[{material_id!r}].unit_corrections",
        )
    quote_text_by_id = {
        str(row["quote_id"]): str(row["text"])
        for row in quote_candidates
        if str(row.get("material_id", "")) == material_id
    }
    row_by_index = {int(row["source_evidence_index"]): row for row in rows}
    output_units: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for index, source_unit in enumerate(source_units):
        row = row_by_index[index]
        action = str(row["action"])
        if action == "keep":
            output_units.append(copy.deepcopy(source_unit))
        elif action == "delete":
            pass
        else:
            for replacement in row["replacements"]:
                _validate_replacement_scope(
                    material_id=material_id,
                    source_evidence_index=index,
                    source_claim=str(source_unit["claim"]),
                    replacement_claim=str(replacement["claim"]),
                    quote_texts=[
                        quote_text_by_id[str(citation["quote_id"])]
                        for citation in replacement["citations"]
                    ],
                )
                output_units.append(
                    {
                        **copy.deepcopy(source_unit),
                        "claim": str(replacement["claim"]),
                        "citations": copy.deepcopy(replacement["citations"]),
                    }
                )
        changes.append(
            {
                "material_id": material_id,
                "source_evidence_index": index,
                "action": action,
                "replacement_count": len(row["replacements"]),
                "reason": str(row["reason"]),
            }
        )
    merged = copy.deepcopy(source_result)
    merged["evidence_units"] = output_units
    if output_units:
        merged["disposition"] = "evidence"
        merged["reason_code"] = "direct_evidence"
    else:
        merged["disposition"] = "context"
        merged["reason_code"] = "context_only"
    return merged, changes


def _validate_replacement_scope(
    *,
    material_id: str,
    source_evidence_index: int,
    source_claim: str,
    replacement_claim: str,
    quote_texts: list[str],
) -> None:
    path = (
        f"$.material_corrections[{material_id!r}].unit_corrections"
        f"[{source_evidence_index}].replacements[].claim"
    )
    source_numbers = _numeric_values(source_claim)
    replacement_numbers = _numeric_values(replacement_claim)
    if not replacement_numbers.issubset(source_numbers):
        raise ContractViolation(
            "correction.new_numeric_fact",
            f"修正观点引入了首次观点没有的数值：{sorted(replacement_numbers - source_numbers)}",
            path=path,
        )
    source = _compact_claim(source_claim)
    replacement = _compact_claim(replacement_claim)
    if len(replacement) > len(source) + 16:
        raise ContractViolation(
            "correction.claim_expanded",
            "修正观点相较首次观点扩张过多。",
            path=path,
        )
    matching = sum(block.size for block in SequenceMatcher(None, source, replacement).get_matching_blocks())
    overlap = matching / len(replacement) if replacement else 0.0
    if overlap < 0.50:
        raise ContractViolation(
            "correction.claim_scope_changed",
            f"修正观点与首次观点的字符保留率过低：{overlap:.3f}",
            path=path,
        )
    combined_quotes = "\n".join(quote_texts)
    for qualifier in ("建议", "考虑", "可能", "预计", "计划", "约", "至少", "至多", "仅", "尚未"):
        if qualifier not in source_claim and qualifier in replacement_claim and qualifier not in combined_quotes:
            raise ContractViolation(
                "correction.qualifier_not_in_quote",
                f"修正观点新增的限定词没有出现在所选引文中：{qualifier!r}",
                path=path,
            )


def _is_correctable_violation(exc: ContractViolation) -> bool:
    if exc.code.startswith("citation."):
        return True
    if exc.code != "schema.evidence_batch_invalid":
        return False
    return (
        ".evidence_units" in exc.path
        and (exc.path.endswith(".evidence_units") or ".citations" in exc.path)
        and ("too long" in exc.detail or "is too long" in exc.detail)
    )


def _source_unit_error_path(path: str, *, result_index: int, unit_index: int) -> str:
    for prefix in (
        "$.material_results[0].evidence_units[0]",
        "$.evidence_units[0]",
    ):
        if path.startswith(prefix):
            return (
                f"$.material_results[{result_index}].evidence_units[{unit_index}]"
                + path[len(prefix) :]
            )
    return path


def _numeric_failure_details(
    exc: ContractViolation,
    material: dict[str, Any],
    quote_candidates: list[dict[str, Any]],
    claim: str,
) -> dict[str, Any]:
    if exc.code != "citation.evidence_claim_numeric_fact_unsupported":
        return {
            "forbidden_claim_fragments": [],
            "table_composite_candidates": [],
        }
    match = re.search(r"：(\[[^\]]*\])$", exc.detail)
    if not match:
        return {
            "forbidden_claim_fragments": [],
            "table_composite_candidates": [],
        }
    try:
        unsupported = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return {
            "forbidden_claim_fragments": [],
            "table_composite_candidates": [],
        }
    if not isinstance(unsupported, list):
        return {
            "forbidden_claim_fragments": [],
            "table_composite_candidates": [],
        }
    supported: set[str] = set()
    for candidate in quote_candidates:
        supported.update(extract_numeric_facts(str(candidate.get("text", ""))))
    for left, right in zip(quote_candidates, quote_candidates[1:]):
        if (
            left.get("material_id") == right.get("material_id")
            and left.get("end_char") == right.get("start_char")
        ):
            supported.update(
                extract_numeric_facts(
                    str(left.get("text", "")) + str(right.get("text", ""))
                )
            )
    unsupported_facts = {
        str(item) for item in unsupported if isinstance(item, str) and str(item)
    }
    composite_rows = table_percentage_candidates_for_claim(
        claim,
        material,
        quote_candidates,
        unsupported_facts - supported,
        allow_unique_row_alias=True,
    )
    composites_by_fact: dict[str, list[Any]] = {}
    for row in composite_rows:
        composites_by_fact.setdefault(row.fact, []).append(row)
    unique_composites = [
        rows[0]
        for _, rows in sorted(composites_by_fact.items())
        if len(
            {
                (
                    row.header_quote_id,
                    row.row_quote_id,
                    row.row_label,
                    row.column_label,
                    row.column_index,
                )
                for row in rows
            }
        )
        == 1
    ]
    composite_facts = {row.fact for row in unique_composites}
    forbidden = sorted(
        str(item)
        for item in unsupported_facts
        if item not in supported and item not in composite_facts
    )
    return {
        "forbidden_claim_fragments": forbidden,
        "table_composite_candidates": [row.to_dict() for row in unique_composites],
    }


def _table_composite_schema_constraints(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_fact: dict[str, list[tuple[str, str]]] = {}
    for candidate in candidates:
        fact = str(candidate.get("fact", ""))
        header_quote_id = str(candidate.get("header_quote_id", ""))
        row_quote_id = str(candidate.get("row_quote_id", ""))
        if fact and header_quote_id and row_quote_id:
            by_fact.setdefault(fact, []).append((header_quote_id, row_quote_id))

    constraints: list[dict[str, Any]] = []
    for fact, pairs in sorted(by_fact.items()):
        pair_schemas = []
        for header_quote_id, row_quote_id in sorted(set(pairs)):
            pair_schemas.append(
                {
                    "properties": {
                        "citations": {
                            "allOf": [
                                _citation_contains_schema(header_quote_id),
                                _citation_contains_schema(row_quote_id),
                            ]
                        }
                    }
                }
            )
        constraints.append(
            {
                "if": {
                    "properties": {
                        "claim": {"pattern": _numeric_fragment_pattern(fact)},
                    },
                    "required": ["claim"],
                },
                "then": {"anyOf": pair_schemas},
            }
        )
    return constraints


def _assign_table_composite_ownership(
    violations: list[dict[str, Any]],
    source_units: list[dict[str, Any]],
) -> None:
    original_candidate_counts = {
        int(violation["source_evidence_index"]): len(
            violation.get("table_composite_candidates", [])
        )
        for violation in violations
        if isinstance(violation.get("source_evidence_index"), int)
    }
    occurrences: dict[tuple[str, str, int, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for violation in violations:
        source_index = violation.get("source_evidence_index")
        if not isinstance(source_index, int):
            continue
        for candidate in violation.get("table_composite_candidates", []):
            identity = (
                str(candidate.get("header_quote_id", "")),
                str(candidate.get("row_quote_id", "")),
                int(candidate.get("column_index", -1)),
                str(candidate.get("fact", "")),
            )
            if all(identity[:2]) and identity[2] >= 0 and identity[3]:
                occurrences.setdefault(identity, []).append((violation, candidate))

    for rows in occurrences.values():
        source_indices = {
            int(violation["source_evidence_index"])
            for violation, _ in rows
        }
        if len(source_indices) < 2:
            continue
        owner_index = min(
            source_indices,
            key=lambda index: _table_composite_owner_score(
                index,
                source_units[index],
                rows,
                original_candidate_counts[index],
            ),
        )
        for violation, candidate in rows:
            source_index = int(violation["source_evidence_index"])
            if source_index == owner_index:
                candidate["owner_source_evidence_index"] = owner_index
                continue
            fact = str(candidate["fact"])
            violation["forbidden_claim_fragments"] = sorted(
                {
                    *[str(item) for item in violation.get("forbidden_claim_fragments", [])],
                    fact,
                }
            )
            violation["table_composite_candidates"] = [
                row
                for row in violation.get("table_composite_candidates", [])
                if row is not candidate
            ]
            violation.setdefault("delegated_table_composite_facts", []).append(
                {
                    "fact": fact,
                    "owner_source_evidence_index": owner_index,
                    "reason": "同一表格单元格事实只能由一个原证据单元输出。",
                }
            )


def _table_composite_owner_score(
    source_index: int,
    source_unit: dict[str, Any],
    occurrences: list[tuple[dict[str, Any], dict[str, Any]]],
    original_candidate_count: int,
) -> tuple[int, int, int, int]:
    claim = str(source_unit.get("claim", ""))
    candidate = next(
        candidate
        for violation, candidate in occurrences
        if int(violation["source_evidence_index"]) == source_index
    )
    exact_row_label = _compact_claim(str(candidate.get("row_label", ""))) in _compact_claim(claim)
    return (
        1 if re.search(r"最高|最低|最大|最小|居首|居末|排名第一|排名最后", claim) else 0,
        0 if exact_row_label else 1,
        original_candidate_count,
        source_index,
    )


def _citation_contains_schema(quote_id: str) -> dict[str, Any]:
    return {
        "contains": {
            "type": "object",
            "properties": {"quote_id": {"const": quote_id}},
            "required": ["quote_id"],
        },
        "minContains": 1,
    }


def _numeric_fragment_pattern(value: str) -> str:
    prefix = r"(^|[^0-9.])"
    suffix = r"([^0-9.]|$)"
    if value.endswith("%") or value.endswith("％"):
        escaped_number = re.escape(value[:-1])
        return prefix + escaped_number + r"\s*[%％]" + suffix
    return prefix + re.escape(value) + suffix


def _numeric_values(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).replace(",", "")
    return {match.group(0).lstrip("+") for match in re.finditer(r"[+-]?\d+(?:\.\d+)?", normalized)}


def _compact_claim(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value)
        if not character.isspace() and (character.isalnum() or "\u4e00" <= character <= "\u9fff")
    )


def _validate_schema(payload: object, schema: dict[str, Any], code: str) -> dict[str, Any]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}" for item in error.absolute_path
        )
        raise ContractViolation(code, error.message, path=path)
    if not isinstance(payload, dict):
        raise ContractViolation(code, "payload must be an object")
    return payload


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
