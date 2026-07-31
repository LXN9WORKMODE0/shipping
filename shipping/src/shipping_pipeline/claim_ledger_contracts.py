from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from jsonschema import Draft202012Validator

from .research_understanding_contracts import RESULT_TYPES, VALIDATION_LEVELS
from .source_window_contracts import stable_id


CLAIM_LEDGER_DRAFT_SCHEMA_VERSION = "llm.claim_ledger_draft.v1"
CLAIM_LEDGER_CLAIM_SCHEMA_VERSION = "llm.claim_ledger_claim.v1"
CLAIM_LEDGER_SCHEMA_VERSION = "llm.claim_ledger.v1"
CLAIM_LEDGER_RUN_SCHEMA_VERSION = "llm.claim_ledger_run.v1"

CLAIM_TYPES = (
    "direct_fact",
    "comparative_fact",
    "cross_paper_synthesis",
    "author_view_summary",
    "corpus_gap",
    "normative_recommendation",
    "navigation_synthesis",
)
SECTION_ROLES = (
    "topic_sentence",
    "core_argument",
    "comparison",
    "supporting",
    "qualification",
    "transition",
    "conclusion",
)
IMPORTANCE_LEVELS = ("core", "supporting")
SUPPORT_MODES = (
    "source_reported",
    "direct_comparison",
    "corpus_level_inference",
    "author_view",
    "corpus_scope_observation",
    "reviewer_recommendation",
    "navigation_only",
)
ALLOWED_STRENGTHS = (
    "source_reported_only",
    "qualified_comparison",
    "targeted_corpus_only",
)
CLAIM_RESULT_TYPES = (*RESULT_TYPES, "mixed", "not_applicable")
CLAIM_VALIDATION_LEVELS = (*VALIDATION_LEVELS, "mixed", "not_applicable")

TYPE_POLICY = {
    "direct_fact": {
        "support_modes": {"source_reported"},
        "strengths": {"source_reported_only"},
        "min_papers": 1,
        "requires_windows": True,
        "qualifiers": (),
        "prohibited": (),
    },
    "comparative_fact": {
        "support_modes": {"direct_comparison"},
        "strengths": {"qualified_comparison"},
        "min_papers": 2,
        "requires_windows": True,
        "qualifiers": (),
        "prohibited": ("完全优于", "必然优于"),
    },
    "cross_paper_synthesis": {
        "support_modes": {"corpus_level_inference"},
        "strengths": {"targeted_corpus_only"},
        "min_papers": 2,
        "requires_windows": True,
        "qualifiers": (
            "本次纳入的文献",
            "当前样本文献",
            "所分析研究",
            "样本文献",
            "本次综述",
        ),
        "prohibited": (
            "学界普遍认为",
            "现有研究已经证明",
            "已形成共识",
        ),
    },
    "author_view_summary": {
        "support_modes": {"author_view"},
        "strengths": {"source_reported_only"},
        "min_papers": 1,
        "requires_windows": True,
        "qualifiers": (
            "作者",
            "研究建议",
            "文献认为",
            "论文讨论",
            "主张",
            "认为",
            "提出",
        ),
        "prohibited": ("实践证明", "已经实现", "实测表明"),
    },
    "corpus_gap": {
        "support_modes": {"corpus_scope_observation"},
        "strengths": {"targeted_corpus_only"},
        "min_papers": 0,
        "requires_windows": False,
        "qualifiers": (
            "本次语料",
            "所纳入文献",
            "当前样本文献",
            "样本文献",
            "本次综述",
        ),
        "prohibited": ("该领域尚无研究", "现有研究均未"),
    },
    "normative_recommendation": {
        "support_modes": {"reviewer_recommendation"},
        "strengths": {"targeted_corpus_only"},
        "min_papers": 0,
        "requires_windows": False,
        "qualifiers": ("建议", "可考虑", "有必要", "应"),
        "prohibited": ("实践已经表明",),
    },
    "navigation_synthesis": {
        "support_modes": {"navigation_only"},
        "strengths": {"targeted_corpus_only"},
        "min_papers": 0,
        "requires_windows": False,
        "qualifiers": (),
        "prohibited": (),
    },
}


class ClaimLedgerContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


def build_claim_ledger_draft_schema(
    *,
    section: dict[str, Any],
    paper_ids: list[str],
    window_ids: list[str],
    citation_keys: list[str],
) -> dict[str, Any]:
    budget = section["claim_budget"]
    max_claims = budget["core_max"] + budget["supporting_max"]
    claim = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "claim_type",
            "planned_claim",
            "section_role",
            "importance",
            "supporting_papers",
            "supporting_source_windows",
            "citation_keys",
            "support_mode",
            "result_type",
            "validation_level",
            "allowed_strength",
            "audit_notes",
        ],
        "properties": {
            "claim_type": {"type": "string", "enum": list(CLAIM_TYPES)},
            "planned_claim": {
                "type": "string",
                "minLength": 1,
                "maxLength": 600,
            },
            "section_role": {
                "type": "string",
                "enum": list(SECTION_ROLES),
            },
            "importance": {
                "type": "string",
                "enum": list(IMPORTANCE_LEVELS),
            },
            "supporting_papers": {
                "type": "array",
                "uniqueItems": True,
                "items": _enum_or_never(paper_ids),
            },
            "supporting_source_windows": {
                "type": "array",
                "uniqueItems": True,
                "items": _enum_or_never(window_ids),
            },
            "citation_keys": {
                "type": "array",
                "uniqueItems": True,
                "items": _enum_or_never(citation_keys),
            },
            "support_mode": {
                "type": "string",
                "enum": list(SUPPORT_MODES),
            },
            "result_type": {
                "type": "string",
                "enum": list(CLAIM_RESULT_TYPES),
            },
            "validation_level": {
                "type": "string",
                "enum": list(CLAIM_VALIDATION_LEVELS),
            },
            "allowed_strength": {
                "type": "string",
                "enum": list(ALLOWED_STRENGTHS),
            },
            "audit_notes": {
                "type": "string",
                "maxLength": 240,
            },
        },
        "allOf": [
            {
                "if": {
                    "properties": {
                        "claim_type": {"const": claim_type}
                    },
                    "required": ["claim_type"],
                },
                "then": {
                    "properties": {
                        "support_mode": {
                            "enum": sorted(policy["support_modes"])
                        },
                        "allowed_strength": {
                            "enum": sorted(policy["strengths"])
                        },
                    }
                },
            }
            for claim_type, policy in TYPE_POLICY.items()
        ],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "section_id", "claims"],
        "properties": {
            "schema_version": {
                "const": CLAIM_LEDGER_DRAFT_SCHEMA_VERSION
            },
            "section_id": {"const": section["section_id"]},
            "claims": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_claims,
                "items": claim,
                "allOf": [
                    _array_count_constraint(
                        "importance",
                        "core",
                        minimum=1,
                        maximum=budget["core_max"],
                    ),
                    _array_count_constraint(
                        "importance",
                        "supporting",
                        minimum=0,
                        maximum=budget["supporting_max"],
                    ),
                    _array_count_constraint(
                        "claim_type",
                        "cross_paper_synthesis",
                        minimum=0,
                        maximum=budget["cross_paper_synthesis_max"],
                    ),
                ],
            },
        },
    }


def validate_claim_ledger_draft(
    payload: object,
    schema: dict[str, Any],
) -> dict[str, Any]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda row: list(row.path))
    if errors:
        first = errors[0]
        path = "$" + "".join(
            f"[{value}]" if isinstance(value, int) else f".{value}"
            for value in first.path
        )
        raise ClaimLedgerContractError(
            "claim_ledger.draft_schema_invalid",
            first.message,
            path=path,
        )
    return copy.deepcopy(payload)


def adjudicate_claim_ledger(
    draft: dict[str, Any],
    *,
    framework_b2: dict[str, Any],
    source_selection: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    section = _matching_section(framework_b2, draft["section_id"])
    windows = {
        row["window_id"]: row
        for row in source_selection["selected_source_windows"]
    }
    citation_by_paper = {
        row["paper_id"]: row["citation_key"]
        for row in source_selection["citation_metadata"]
    }
    section_papers = set(section["paper_ids"])
    excluded = set(
        section["material_selection"]["excluded_paper_ids"]
    )
    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for index, candidate in enumerate(draft["claims"], start=1):
        counted = False
        errors = _claim_errors(
            candidate,
            section=section,
            section_papers=section_papers,
            excluded_papers=excluded,
            windows=windows,
            citation_by_paper=citation_by_paper,
        )
        if not errors:
            counted = True
            counts[candidate["importance"]] += 1
            if candidate["claim_type"] == "cross_paper_synthesis":
                counts["cross_paper_synthesis"] += 1
            errors.extend(_budget_errors(counts, section["claim_budget"]))
        if errors:
            rejected.append(
                {
                    "candidate_index": index,
                    "importance": candidate["importance"],
                    "candidate": copy.deepcopy(candidate),
                    "errors": errors,
                }
            )
            if counted:
                counts[candidate["importance"]] -= 1
                if candidate["claim_type"] == "cross_paper_synthesis":
                    counts["cross_paper_synthesis"] -= 1
            continue
        approved.append(
            _approved_claim(
                candidate,
                framework_id=framework_b2["framework_id"],
                section_id=section["section_id"],
            )
        )
    approved.sort(key=lambda row: row["claim_id"])
    core_rejected = [
        row for row in rejected if row["importance"] == "core"
    ]
    if not any(row["importance"] == "core" for row in approved):
        core_rejected.append(
            {
                "candidate_index": None,
                "importance": "core",
                "candidate": None,
                "errors": [
                    {
                        "code": "claim_ledger.approved_core_missing",
                        "message": "章节没有任何通过校验的核心Claim。",
                    }
                ],
            }
        )
    ledger_without_id = {
        "schema_version": CLAIM_LEDGER_SCHEMA_VERSION,
        "framework_b2_id": framework_b2["framework_id"],
        "section_id": section["section_id"],
        "source_window_selection_id": source_selection["selection_id"],
        "approved_claims": approved,
        "rejected_claim_count": len(rejected),
        "core_rejected": bool(core_rejected),
        "claim_budget": copy.deepcopy(section["claim_budget"]),
    }
    ledger = {
        **ledger_without_id,
        "ledger_id": stable_id("claim_ledger", ledger_without_id),
    }
    return ledger, rejected


def _claim_errors(
    claim: dict[str, Any],
    *,
    section: dict[str, Any],
    section_papers: set[str],
    excluded_papers: set[str],
    windows: dict[str, dict[str, Any]],
    citation_by_paper: dict[str, str],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    claim_type = claim["claim_type"]
    policy = TYPE_POLICY[claim_type]
    papers = claim["supporting_papers"]
    window_ids = claim["supporting_source_windows"]
    if not set(papers).issubset(section_papers) or set(papers) & excluded_papers:
        errors.append(_error("claim_ledger.paper_outside_section", "Claim引用未入选或排除论文。"))
    unknown_windows = [value for value in window_ids if value not in windows]
    if unknown_windows:
        errors.append(_error("claim_ledger.window_unknown", f"未知Source Window：{unknown_windows}"))
    window_papers = {
        windows[value]["paper_id"]
        for value in window_ids
        if value in windows
    }
    if not window_papers.issubset(set(papers)):
        errors.append(_error("claim_ledger.window_paper_mismatch", "Source Window所属论文不在supporting_papers中。"))
    if policy["requires_windows"]:
        missing_window_papers = set(papers) - window_papers
        if missing_window_papers:
            errors.append(_error("claim_ledger.paper_window_missing", f"支持论文缺少窗口：{sorted(missing_window_papers)}"))
    if len(set(papers)) < policy["min_papers"]:
        errors.append(_error("claim_ledger.supporting_papers_insufficient", f"{claim_type}至少需要{policy['min_papers']}篇论文。"))
    expected_citations = [
        citation_by_paper[paper_id]
        for paper_id in papers
        if paper_id in citation_by_paper
    ]
    if claim["citation_keys"] != expected_citations:
        errors.append(_error("claim_ledger.citation_paper_mismatch", "citation_keys必须按supporting_papers顺序一一对应。"))
    if claim["support_mode"] not in policy["support_modes"]:
        errors.append(_error("claim_ledger.support_mode_invalid", f"{claim_type}不允许support_mode={claim['support_mode']}。"))
    if claim["allowed_strength"] not in policy["strengths"]:
        errors.append(_error("claim_ledger.strength_invalid", f"{claim_type}不允许allowed_strength={claim['allowed_strength']}。"))
    text = claim["planned_claim"]
    qualifiers = policy["qualifiers"]
    if qualifiers and not any(value in text for value in qualifiers):
        errors.append(_error("claim_ledger.required_qualifier_missing", f"Claim缺少范围或职责限定语：{list(qualifiers)}"))
    hits = [value for value in policy["prohibited"] if value in text]
    if hits:
        errors.append(_error("claim_ledger.prohibited_phrasing", f"Claim命中禁止措辞：{hits}"))
    mentioned_outside = [
        paper_id
        for paper_id in section_papers | excluded_papers
        if paper_id in text and paper_id not in set(papers)
    ]
    if mentioned_outside:
        errors.append(_error("claim_ledger.mentioned_paper_unbound", f"正文点名论文但未绑定支持：{sorted(mentioned_outside)}"))
    if claim_type in {"corpus_gap", "normative_recommendation", "navigation_synthesis"}:
        if claim["result_type"] != "not_applicable" or claim["validation_level"] != "not_applicable":
            errors.append(_error("claim_ledger.semantic_level_not_applicable", f"{claim_type}的result_type和validation_level必须为not_applicable。"))
    elif claim_type in {"comparative_fact", "cross_paper_synthesis"}:
        if claim["result_type"] != "mixed" or claim["validation_level"] != "mixed":
            errors.append(_error("claim_ledger.semantic_level_mixed_required", f"{claim_type}的result_type和validation_level必须为mixed。"))
    elif claim_type in {"direct_fact", "author_view_summary"}:
        source_result_types = {
            value
            for window_id in window_ids
            if window_id in windows
            for value in windows[window_id].get("result_types", [])
        }
        source_validation_levels = {
            value
            for window_id in window_ids
            if window_id in windows
            for value in windows[window_id].get("validation_levels", [])
        }
        if not source_result_types or not source_validation_levels:
            errors.append(
                _error(
                    "claim_ledger.typed_source_window_missing",
                    "直接事实或作者观点必须绑定至少一个带result type和validation level的窗口。",
                )
            )
        else:
            if claim["result_type"] not in source_result_types:
                errors.append(
                    _error(
                        "claim_ledger.result_type_source_mismatch",
                        "Claim result_type不在绑定窗口的result_types中。",
                    )
                )
            if claim["validation_level"] not in source_validation_levels:
                errors.append(
                    _error(
                        "claim_ledger.validation_level_source_mismatch",
                        "Claim validation_level不在绑定窗口的validation_levels中。",
                    )
                )
    return errors


def _budget_errors(
    counts: Counter[str],
    budget: dict[str, int],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if counts["core"] > budget["core_max"]:
        errors.append(_error("claim_ledger.core_budget_exceeded", "核心Claim数量超过预算。"))
    if counts["supporting"] > budget["supporting_max"]:
        errors.append(_error("claim_ledger.supporting_budget_exceeded", "支持Claim数量超过预算。"))
    if counts["cross_paper_synthesis"] > budget["cross_paper_synthesis_max"]:
        errors.append(_error("claim_ledger.cross_paper_budget_exceeded", "跨论文综合Claim数量超过预算。"))
    return errors


def _approved_claim(
    candidate: dict[str, Any],
    *,
    framework_id: str,
    section_id: str,
) -> dict[str, Any]:
    policy = TYPE_POLICY[candidate["claim_type"]]
    result = {
        "schema_version": CLAIM_LEDGER_CLAIM_SCHEMA_VERSION,
        "section_id": section_id,
        **copy.deepcopy(candidate),
        "required_qualifier": (
            policy["qualifiers"][0] if policy["qualifiers"] else ""
        ),
        "prohibited_phrasings": list(policy["prohibited"]),
    }
    result["claim_id"] = stable_id(
        "claim",
        {
            "schema_version": CLAIM_LEDGER_CLAIM_SCHEMA_VERSION,
            "framework_id": framework_id,
            "section_id": section_id,
            "claim_type": result["claim_type"],
            "planned_claim": result["planned_claim"],
            "supporting_papers": sorted(result["supporting_papers"]),
            "supporting_source_windows": sorted(
                result["supporting_source_windows"]
            ),
            "allowed_strength": result["allowed_strength"],
        },
    )
    return result


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
        raise ClaimLedgerContractError(
            "claim_ledger.section_mismatch",
            "Framework B2中无法唯一定位Ledger章节。",
        )
    return rows[0]


def _enum_or_never(values: list[str]) -> dict[str, Any]:
    if values:
        return {"type": "string", "enum": values}
    return {"not": {}}


def _array_count_constraint(
    field: str,
    value: str,
    *,
    minimum: int,
    maximum: int,
) -> dict[str, Any]:
    return {
        "contains": {
            "type": "object",
            "properties": {field: {"const": value}},
            "required": [field],
        },
        "minContains": minimum,
        "maxContains": maximum,
    }


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
