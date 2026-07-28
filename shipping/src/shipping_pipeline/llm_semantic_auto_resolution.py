from __future__ import annotations

import copy
import json
import re
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator

from .llm_contracts import (
    PAPER_ANALYSIS_SCHEMA_VERSION,
    PAPER_REFERENCE_ARRAY_FIELDS,
    ContractViolation,
    assign_evidence_unit_ids,
    paper_analysis_referenced_evidence_ids,
    validate_paper_analysis,
)
from .llm_projection import project_cards
from .llm_quotes import build_quote_candidates, project_quote_candidates
from .llm_statement_support import build_statement_records


SEMANTIC_AUTO_RESOLUTION_SCHEMA_VERSION = "llm.semantic_auto_resolution.v1"
_NUMERIC_FACT_RE = re.compile(
    r"(?<![A-Za-z0-9_.,])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:\s*[%％])?(?![0-9_.,])"
)

SEMANTIC_AUTO_RESOLUTION_SYSTEM_PROMPT = """你是论文语义门禁问题的受约束自动处置规划器。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown、分析过程或额外字段。
每个阻断项必须恰好处置一次，只能从各项给出的动作和候选 ID 中选择，不得生成新的观点、陈述、引文或 ID。

Evidence claim 的动作边界：
- repair_citations：观点本身正确，且当前 Card 或相邻 Card 的候选逐字引文能够完整直接支持它。只能选择输入给出的 quote_id，1至4条。
- replace_with_supported_evidence：当前 Evidence 与同篇论文中已通过 claim 门禁的 Evidence 实质重复。只能选择输入给出的替代 Evidence ID；原 Evidence 将删除，所有引用将映射到替代 Evidence。
- keep_gate_override：只用于没有数字、公式、因果、范围或程度扩张的低风险等值改述，而且门禁明显把自然语言等价误判为缺少逐字量化。若输入标记 auto_override_eligible=false，禁止选择。
- human_required：无法在上述边界内可靠处置。
表格数值、公式或程序自行计算出的结果不能作为新的独立 Evidence。遇到这类项，应优先映射到作者直接写出的解释性 Evidence；没有则 human_required。
选择 repair_citations 或 replace_with_supported_evidence 前，必须逐条检查 affected_statements。新引文或替代 Evidence 必须保留这些陈述中的全部数字、公式编号、对象和范围；否则不能选择该动作。
repair_citations 必须至少选择一条 is_current_citation=false 的新引文。若现有引文已经完整支持、无需新增引文，必须在 available_actions 允许时选择 keep_gate_override，禁止把原引文原样包装成 repair_citations。
校准：原文“研究有很多”概括为“已有大量研究”属于可考虑 keep_gate_override 的自然语言等值表达，不能改映射到“内河研究相对较少”等相邻但不同的命题。

字段职责的动作边界：
- delete_duplicate_statement：错位陈述只是复述论文事实，且相同 Evidence 已在另一个职责正确的陈述中使用。只能选择输入给出的 canonical_statement_id；程序只删除错位副本，不改写正确陈述。措辞不必完全相同，但正确字段必须保留该 Evidence 所代表的核心信息。
- human_required：不存在完整的正确字段副本，或是否重复存在歧义。

宁可 human_required，也不得用近似、猜测或跨论文知识完成处置。reason 只写一句最终中文依据，建议不超过120个汉字。"""


class SemanticAutoResolutionError(ValueError):
    pass


def build_auto_resolution_context(
    *,
    materials: list[dict[str, Any]],
    evidence_units: list[dict[str, Any]],
    analysis: dict[str, Any],
    evidence_claim_reviews: list[dict[str, Any]],
    statement_role_reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    projected_cards = project_cards(materials)
    quote_candidates = build_quote_candidates(projected_cards)
    quote_candidates_by_material: dict[str, list[dict[str, str]]] = {}
    for row in project_quote_candidates(quote_candidates):
        quote_candidates_by_material.setdefault(str(row["material_id"]), []).append(row)

    ordered_materials = sorted(materials, key=lambda row: int(row.get("order") or 0))
    material_index = {
        str(row["material_id"]): index for index, row in enumerate(ordered_materials)
    }
    evidence_by_id = {str(row["evidence_unit_id"]): row for row in evidence_units}
    claim_review_by_id = {
        str(row["evidence_unit_id"]): row for row in evidence_claim_reviews
    }
    statement_records = build_statement_records(analysis)
    direct_evidence_ids = {
        evidence_id
        for evidence_id, review in claim_review_by_id.items()
        if review.get("verdict") == "directly_supported"
    }

    evidence_issues: list[dict[str, Any]] = []
    for review in evidence_claim_reviews:
        if review.get("verdict") == "directly_supported":
            continue
        target_id = str(review.get("evidence_unit_id") or "")
        unit = evidence_by_id.get(target_id)
        if unit is None:
            raise SemanticAutoResolutionError(
                f"Evidence claim 阻断项指向未知证据：{target_id!r}"
            )
        cited_material_ids = list(
            dict.fromkeys(str(row.get("material_id") or "") for row in unit.get("citations", []))
        )
        if not cited_material_ids or any(value not in material_index for value in cited_material_ids):
            raise SemanticAutoResolutionError(
                f"Evidence claim 缺少可定位的 Card：{target_id!r}"
            )
        allowed_material_ids: list[str] = []
        for material_id in cited_material_ids:
            index = material_index[material_id]
            for neighbor_index in range(max(0, index - 1), min(len(ordered_materials), index + 2)):
                candidate_id = str(ordered_materials[neighbor_index]["material_id"])
                if candidate_id not in allowed_material_ids:
                    allowed_material_ids.append(candidate_id)
        allowed_id_set = set(allowed_material_ids)
        current_quote_ids = {
            str(row.get("quote_id") or "") for row in unit.get("citations", [])
        }
        allowed_quotes = [
            {
                **quote,
                "is_current_citation": str(quote["quote_id"]) in current_quote_ids,
            }
            for material_id in allowed_material_ids
            for quote in quote_candidates_by_material.get(material_id, [])
        ]
        affected_statements = [
            _project_statement(record)
            for record in statement_records
            if target_id in record["evidence_unit_ids"]
        ]
        required_numeric_facts = {
            fact
            for record in affected_statements
            for fact in _numeric_facts(str(record["statement"]))
        }
        replacement_candidates = []
        for candidate_id in sorted(direct_evidence_ids):
            candidate = evidence_by_id[candidate_id]
            candidate_material_ids = {
                str(row.get("material_id") or "") for row in candidate.get("citations", [])
            }
            if not candidate_material_ids or not candidate_material_ids.issubset(allowed_id_set):
                continue
            similarity = _claim_bigram_similarity(
                str(unit.get("claim") or ""),
                str(candidate.get("claim") or ""),
            )
            if similarity < 0.35:
                continue
            candidate_support_text = "\n".join(
                [
                    str(candidate.get("claim") or ""),
                    *[
                        str(citation.get("quote") or "")
                        for citation in candidate.get("citations", [])
                    ],
                ]
            )
            if not required_numeric_facts.issubset(_numeric_facts(candidate_support_text)):
                continue
            replacement_candidates.append(
                {**_project_evidence(candidate), "claim_bigram_similarity": round(similarity, 4)}
            )
        auto_override_eligible = _auto_override_eligible(unit, review)
        available_actions = ["human_required"]
        if any(not row["is_current_citation"] for row in allowed_quotes):
            available_actions.insert(0, "repair_citations")
        if replacement_candidates:
            available_actions.insert(0, "replace_with_supported_evidence")
        if auto_override_eligible:
            available_actions.insert(0, "keep_gate_override")
        evidence_issues.append(
            {
                "target_evidence_unit_id": target_id,
                "claim": str(unit.get("claim") or ""),
                "evidence_type": str(unit.get("evidence_type") or ""),
                "current_citations": [
                    {
                        "quote_id": str(row.get("quote_id") or ""),
                        "material_id": str(row.get("material_id") or ""),
                        "quote": str(row.get("quote") or ""),
                    }
                    for row in unit.get("citations", [])
                ],
                "gate_review": copy.deepcopy(review),
                "allowed_material_ids": allowed_material_ids,
                "allowed_quote_candidates": allowed_quotes,
                "replacement_candidates": replacement_candidates,
                "auto_override_eligible": auto_override_eligible,
                "available_actions": available_actions,
                "affected_statements": affected_statements,
                "required_numeric_facts_for_replacement": sorted(required_numeric_facts),
            }
        )

    statement_by_id = {str(row["statement_id"]): row for row in statement_records}
    statement_issues: list[dict[str, Any]] = []
    for review in statement_role_reviews:
        if review.get("verdict") == "field_aligned":
            continue
        target_id = str(review.get("statement_id") or "")
        record = statement_by_id.get(target_id)
        if record is None:
            raise SemanticAutoResolutionError(
                f"字段职责阻断项指向未知陈述：{target_id!r}"
            )
        target_evidence_ids = set(str(value) for value in record["evidence_unit_ids"])
        canonical_candidates = [
            {
                **_project_statement(candidate),
                "shared_evidence_unit_ids": sorted(
                    target_evidence_ids.intersection(candidate["evidence_unit_ids"])
                ),
            }
            for candidate in statement_records
            if candidate["statement_id"] != target_id
            and candidate["field"] != record["field"]
            and target_evidence_ids.intersection(candidate["evidence_unit_ids"])
        ]
        statement_issues.append(
            {
                "target_statement_id": target_id,
                "field": str(record["field"]),
                "statement": str(record["statement"]),
                "gate_review": copy.deepcopy(review),
                "deletion_allowed": record["field"] in PAPER_REFERENCE_ARRAY_FIELDS,
                "canonical_candidates": canonical_candidates,
            }
        )

    context = {
        "evidence_issues": evidence_issues,
        "statement_issues": statement_issues,
    }
    if not evidence_issues and not statement_issues:
        raise SemanticAutoResolutionError("语义审计没有需要自动处置的阻断项。")
    return context


def build_auto_resolution_schema(context: dict[str, Any]) -> dict[str, Any]:
    evidence_variants = [
        _evidence_resolution_variant(row) for row in context["evidence_issues"]
    ]
    statement_variants = [
        _statement_resolution_variant(row) for row in context["statement_issues"]
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.semantic_auto_resolution.v1.json",
        "title": "论文语义门禁自动处置计划",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "evidence_resolutions", "statement_resolutions"],
        "properties": {
            "schema_version": {"const": SEMANTIC_AUTO_RESOLUTION_SCHEMA_VERSION},
            "evidence_resolutions": {
                "type": "array",
                "minItems": len(evidence_variants),
                "maxItems": len(evidence_variants),
                "items": {"oneOf": evidence_variants} if evidence_variants else False,
            },
            "statement_resolutions": {
                "type": "array",
                "minItems": len(statement_variants),
                "maxItems": len(statement_variants),
                "items": {"oneOf": statement_variants} if statement_variants else False,
            },
        },
    }


def build_auto_resolution_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    context: dict[str, Any],
) -> tuple[str, str]:
    prompt_input = {
        "任务": "在限定候选内规划语义门禁自动处置；无法可靠处置的项必须交给人工",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "综述主题": topic,
        "输出JSONSchema": build_auto_resolution_schema(context),
        "证据观点阻断项": context["evidence_issues"],
        "字段职责阻断项": context["statement_issues"],
    }
    return SEMANTIC_AUTO_RESOLUTION_SYSTEM_PROMPT, json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_auto_resolution_plan(
    payload: object,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    schema = build_auto_resolution_schema(context)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}"
            for item in error.absolute_path
        )
        raise ContractViolation("schema.semantic_auto_resolution_invalid", error.message, path=path)
    assert isinstance(payload, dict)
    validated = copy.deepcopy(payload)
    _verify_resolution_coverage(
        validated["evidence_resolutions"],
        [str(row["target_evidence_unit_id"]) for row in context["evidence_issues"]],
        id_field="target_evidence_unit_id",
        path="$.evidence_resolutions",
    )
    _verify_resolution_coverage(
        validated["statement_resolutions"],
        [str(row["target_statement_id"]) for row in context["statement_issues"]],
        id_field="target_statement_id",
        path="$.statement_resolutions",
    )
    return validated


def apply_auto_resolution_plan(
    plan: dict[str, Any],
    *,
    context: dict[str, Any],
    materials: list[dict[str, Any]],
    evidence_units: list[dict[str, Any]],
    analysis: dict[str, Any],
    generation_id: str,
    request_id: str,
) -> dict[str, Any]:
    validated = validate_auto_resolution_plan(plan, context=context)
    working_analysis = copy.deepcopy(analysis)
    original_statement_records = build_statement_records(working_analysis)
    statement_by_id = {
        str(row["statement_id"]): row for row in original_statement_records
    }
    statement_actions: list[dict[str, Any]] = []
    for resolution in validated["statement_resolutions"]:
        target_id = str(resolution["target_statement_id"])
        action = str(resolution["action"])
        if action == "human_required":
            statement_actions.append({**resolution, "applied": False})
            continue
        target = statement_by_id[target_id]
        canonical_id = str(resolution["canonical_statement_id"])
        canonical = statement_by_id.get(canonical_id)
        if canonical is None or canonical_id == target_id:
            raise ContractViolation(
                "identity.semantic_auto_canonical_statement_invalid",
                f"canonical_statement_id={canonical_id!r} 无效",
                path="$.statement_resolutions",
            )
        if target["field"] not in PAPER_REFERENCE_ARRAY_FIELDS:
            raise ContractViolation(
                "resolution.statement_delete_not_allowed",
                f"field={target['field']!r} 不允许删除单项对象",
                path="$.statement_resolutions",
            )
        items = working_analysis[str(target["field"])]
        matching_indexes = [
            index
            for index, item in enumerate(items)
            if str(item.get("statement") or "") == str(target["statement"])
            and [str(value) for value in item.get("evidence_unit_ids", [])]
            == [str(value) for value in target["evidence_unit_ids"]]
        ]
        if len(matching_indexes) != 1:
            raise ContractViolation(
                "identity.semantic_auto_statement_drift",
                f"statement_id={target_id!r} 无法在当前字段唯一定位",
                path="$.statement_resolutions",
            )
        del items[matching_indexes[0]]
        statement_actions.append(
            {
                **resolution,
                "applied": True,
                "target_field": target["field"],
                "canonical_field": canonical["field"],
            }
        )

    evidence_by_id = {str(row["evidence_unit_id"]): row for row in evidence_units}
    issue_by_id = {
        str(row["target_evidence_unit_id"]): row for row in context["evidence_issues"]
    }
    replacement_by_target: dict[str, dict[str, Any] | None] = {}
    evidence_id_mapping: dict[str, str] = {}
    automatic_override_ids: set[str] = set()
    evidence_actions: list[dict[str, Any]] = []
    for resolution in validated["evidence_resolutions"]:
        target_id = str(resolution["target_evidence_unit_id"])
        action = str(resolution["action"])
        issue = issue_by_id[target_id]
        unit = evidence_by_id[target_id]
        if action == "human_required":
            replacement_by_target[target_id] = copy.deepcopy(unit)
            evidence_actions.append({**resolution, "applied": False})
            continue
        if action == "keep_gate_override":
            if not issue["auto_override_eligible"]:
                raise ContractViolation(
                    "resolution.semantic_auto_override_not_eligible",
                    f"evidence_unit_id={target_id!r} 不满足低风险自动覆盖条件",
                    path="$.evidence_resolutions",
                )
            replacement_by_target[target_id] = copy.deepcopy(unit)
            automatic_override_ids.add(target_id)
            evidence_actions.append({**resolution, "applied": True})
            continue
        if action == "replace_with_supported_evidence":
            replacement_id = str(resolution["replacement_evidence_unit_id"])
            if replacement_id not in evidence_by_id:
                raise ContractViolation(
                    "identity.semantic_auto_replacement_missing",
                    f"replacement_evidence_unit_id={replacement_id!r} 不存在",
                    path="$.evidence_resolutions",
                )
            replacement_by_target[target_id] = None
            evidence_id_mapping[target_id] = replacement_id
            evidence_actions.append(
                {
                    **resolution,
                    "applied": True,
                    "result_evidence_unit_id": replacement_id,
                }
            )
            continue

        quote_by_id = {
            str(row["quote_id"]): row for row in issue["allowed_quote_candidates"]
        }
        selected_ids = [str(value) for value in resolution["selected_quote_ids"]]
        citations = [
            {
                "material_id": str(quote_by_id[quote_id]["material_id"]),
                "quote_id": quote_id,
                "quote": str(quote_by_id[quote_id]["text"]),
            }
            for quote_id in selected_ids
        ]
        normalized_unit = {
            key: copy.deepcopy(unit[key])
            for key in (
                "claim",
                "evidence_type",
                "relevance",
                "confidence",
                "caveats",
            )
        }
        normalized_unit["citations"] = citations
        assigned = assign_evidence_unit_ids(
            request_id,
            [normalized_unit],
            materials,
            generation_id=generation_id,
        )[0]
        new_id = str(assigned["evidence_unit_id"])
        if new_id == target_id:
            raise ContractViolation(
                "resolution.semantic_auto_citations_unchanged",
                f"evidence_unit_id={target_id!r} 的引文身份没有变化",
                path="$.evidence_resolutions",
            )
        replacement_by_target[target_id] = assigned
        evidence_id_mapping[target_id] = new_id
        evidence_actions.append(
            {
                **resolution,
                "applied": True,
                "result_evidence_unit_id": new_id,
            }
        )

    revised_evidence: list[dict[str, Any]] = []
    for unit in evidence_units:
        evidence_id = str(unit["evidence_unit_id"])
        if evidence_id in replacement_by_target:
            replacement = replacement_by_target[evidence_id]
            if replacement is not None:
                revised_evidence.append(copy.deepcopy(replacement))
            continue
        revised_evidence.append(copy.deepcopy(unit))
    revised_ids = [str(row["evidence_unit_id"]) for row in revised_evidence]
    if len(revised_ids) != len(set(revised_ids)):
        raise ContractViolation(
            "identity.semantic_auto_duplicate_evidence",
            "自动处置产生重复 Evidence ID",
            path="$.evidence_resolutions",
        )

    _remap_analysis_evidence_ids(working_analysis, evidence_id_mapping)
    _rebuild_evidence_dispositions(
        working_analysis,
        revised_evidence,
        source_analysis=analysis,
        reverse_mapping={value: key for key, value in evidence_id_mapping.items()},
    )
    working_analysis["schema_version"] = PAPER_ANALYSIS_SCHEMA_VERSION
    validated_analysis = validate_paper_analysis(
        working_analysis,
        set(revised_ids),
        evidence_units=revised_evidence,
    )
    return {
        "plan": validated,
        "analysis": validated_analysis,
        "evidence_units": revised_evidence,
        "evidence_id_mapping": evidence_id_mapping,
        "automatic_override_evidence_ids": sorted(automatic_override_ids),
        "evidence_actions": evidence_actions,
        "statement_actions": statement_actions,
        "human_required_count": sum(
            row["action"] == "human_required"
            for row in [*evidence_actions, *statement_actions]
        ),
    }


def _evidence_resolution_variant(issue: dict[str, Any]) -> dict[str, Any]:
    target_id = str(issue["target_evidence_unit_id"])
    quote_ids = [str(row["quote_id"]) for row in issue["allowed_quote_candidates"]]
    replacement_ids = [
        str(row["evidence_unit_id"]) for row in issue["replacement_candidates"]
    ]
    current_quote_ids = {
        str(row["quote_id"]) for row in issue["current_citations"]
    }
    has_new_quote = any(quote_id not in current_quote_ids for quote_id in quote_ids)
    actions = ["human_required"]
    if has_new_quote:
        actions.insert(0, "repair_citations")
    if replacement_ids:
        actions.append("replace_with_supported_evidence")
    if issue["auto_override_eligible"]:
        actions.append("keep_gate_override")
    properties: dict[str, Any] = {
        "target_evidence_unit_id": {"const": target_id},
        "action": {"type": "string", "enum": actions},
        "selected_quote_ids": {
            "type": "array",
            "maxItems": 4,
            "uniqueItems": True,
            "items": {"type": "string", "enum": quote_ids},
        },
        "replacement_evidence_unit_id": {
            "oneOf": [
                ({"type": "string", "enum": replacement_ids} if replacement_ids else {"type": "string", "maxLength": 0}),
                {"type": "null"},
            ]
        },
        "reason": {"type": "string", "minLength": 1, "maxLength": 240},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "target_evidence_unit_id",
            "action",
            "selected_quote_ids",
            "replacement_evidence_unit_id",
            "reason",
        ],
        "properties": properties,
        "allOf": [
            {
                "if": {"properties": {"action": {"const": "repair_citations"}}, "required": ["action"]},
                "then": {
                    "properties": {
                        "selected_quote_ids": {
                            "minItems": 1,
                            "contains": {"not": {"enum": sorted(current_quote_ids)}},
                            "minContains": 1,
                        },
                        "replacement_evidence_unit_id": {"type": "null"},
                    }
                },
            },
            {
                "if": {"properties": {"action": {"const": "replace_with_supported_evidence"}}, "required": ["action"]},
                "then": {
                    "properties": {
                        "selected_quote_ids": {"maxItems": 0},
                        "replacement_evidence_unit_id": {"type": "string"},
                    }
                },
            },
            {
                "if": {
                    "properties": {"action": {"enum": ["keep_gate_override", "human_required"]}},
                    "required": ["action"],
                },
                "then": {
                    "properties": {
                        "selected_quote_ids": {"maxItems": 0},
                        "replacement_evidence_unit_id": {"type": "null"},
                    }
                },
            },
        ],
    }


def _statement_resolution_variant(issue: dict[str, Any]) -> dict[str, Any]:
    target_id = str(issue["target_statement_id"])
    canonical_ids = [
        str(row["statement_id"]) for row in issue["canonical_candidates"]
    ]
    actions = ["human_required"]
    if issue["deletion_allowed"] and canonical_ids:
        actions.insert(0, "delete_duplicate_statement")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "target_statement_id",
            "action",
            "canonical_statement_id",
            "reason",
        ],
        "properties": {
            "target_statement_id": {"const": target_id},
            "action": {"type": "string", "enum": actions},
            "canonical_statement_id": {
                "oneOf": [
                    ({"type": "string", "enum": canonical_ids} if canonical_ids else {"type": "string", "maxLength": 0}),
                    {"type": "null"},
                ]
            },
            "reason": {"type": "string", "minLength": 1, "maxLength": 240},
        },
        "allOf": [
            {
                "if": {"properties": {"action": {"const": "delete_duplicate_statement"}}, "required": ["action"]},
                "then": {"properties": {"canonical_statement_id": {"type": "string"}}},
            },
            {
                "if": {"properties": {"action": {"const": "human_required"}}, "required": ["action"]},
                "then": {"properties": {"canonical_statement_id": {"type": "null"}}},
            },
        ],
    }


def _project_evidence(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_unit_id": str(unit["evidence_unit_id"]),
        "claim": str(unit.get("claim") or ""),
        "evidence_type": str(unit.get("evidence_type") or ""),
        "citations": [
            {
                "material_id": str(row.get("material_id") or ""),
                "quote": str(row.get("quote") or ""),
            }
            for row in unit.get("citations", [])
        ],
        "caveats": list(unit.get("caveats", [])),
    }


def _project_statement(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "statement_id": str(record["statement_id"]),
        "field": str(record["field"]),
        "statement": str(record["statement"]),
        "evidence_unit_ids": list(record["evidence_unit_ids"]),
    }


def _auto_override_eligible(unit: dict[str, Any], review: dict[str, Any]) -> bool:
    if review.get("verdict") != "partially_supported":
        return False
    texts = [
        str(value) for value in review.get("unsupported_fragments", [])
    ]
    risky_pattern = re.compile(
        r"[0-9０-９%=<>≤≥≈±+*/^]|因果|导致|提高|降低|增长|下降|倍|比例|"
        r"构建|建立|验证|证明|完成|发现|公式|最优|鲁棒|度量|含义|作用"
    )
    return all(not risky_pattern.search(text) for text in texts)


def _claim_bigram_similarity(left: str, right: str) -> float:
    def grams(value: str) -> set[str]:
        normalized = "".join(value.split())
        return {
            normalized[index : index + 2]
            for index in range(max(0, len(normalized) - 1))
        }

    left_grams = grams(left)
    right_grams = grams(right)
    union = left_grams | right_grams
    return len(left_grams & right_grams) / len(union) if union else 0.0


def _numeric_facts(value: str) -> set[str]:
    return {
        match.group(0).replace(",", "").replace(" ", "").replace("％", "%")
        for match in _NUMERIC_FACT_RE.finditer(value)
    }


def _verify_resolution_coverage(
    rows: list[dict[str, Any]],
    expected_ids: list[str],
    *,
    id_field: str,
    path: str,
) -> None:
    observed = [str(row[id_field]) for row in rows]
    counts = Counter(observed)
    missing = sorted(set(expected_ids) - set(observed))
    unknown = sorted(set(observed) - set(expected_ids))
    duplicate = sorted(value for value, count in counts.items() if count > 1)
    if missing or unknown or duplicate:
        raise ContractViolation(
            "coverage.semantic_auto_resolution_invalid",
            f"missing={missing}, unknown={unknown}, duplicate={duplicate}",
            path=path,
        )


def _remap_analysis_evidence_ids(
    analysis: dict[str, Any],
    mapping: dict[str, str],
) -> None:
    items = [analysis["research_focus"]]
    for field in PAPER_REFERENCE_ARRAY_FIELDS:
        items.extend(analysis[field])
    for item in items:
        remapped = [mapping.get(str(value), str(value)) for value in item["evidence_unit_ids"]]
        item["evidence_unit_ids"] = list(dict.fromkeys(remapped))


def _rebuild_evidence_dispositions(
    analysis: dict[str, Any],
    evidence_units: list[dict[str, Any]],
    *,
    source_analysis: dict[str, Any],
    reverse_mapping: dict[str, str],
) -> None:
    referenced_ids = paper_analysis_referenced_evidence_ids(analysis)
    source_by_id = {
        str(row["evidence_unit_id"]): row
        for row in source_analysis["evidence_dispositions"]
    }
    rows = []
    for unit in evidence_units:
        evidence_id = str(unit["evidence_unit_id"])
        if evidence_id in referenced_ids:
            rows.append(
                {
                    "evidence_unit_id": evidence_id,
                    "disposition": "used",
                    "reason_code": "supports_claim",
                }
            )
            continue
        source_id = reverse_mapping.get(evidence_id, evidence_id)
        source = source_by_id.get(source_id)
        if source is None or source.get("disposition") == "used":
            rows.append(
                {
                    "evidence_unit_id": evidence_id,
                    "disposition": "redundant",
                    "reason_code": "duplicate_support",
                }
            )
        else:
            rows.append({**copy.deepcopy(source), "evidence_unit_id": evidence_id})
    analysis["evidence_dispositions"] = rows
