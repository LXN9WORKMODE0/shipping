from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .llm_sections import SectionMap
from .llm_synthesis import PAPER_ROOT_SECTION_ID, assign_evidence_to_sections


EVIDENCE_CLAIM_GATE_PLAN_SCHEMA_VERSION = "llm.evidence_claim_gate_plan.v2"
EVIDENCE_CLAIM_GATE_DECISION_SCHEMA_VERSION = "llm.evidence_claim_gate_decision.v3"
_CLAIM_CLAUSE_SPLIT_RE = re.compile(r"[，,；;。！？!?]+")
_UNSAFE_OMISSION_RE = re.compile(
    r"(?:但是|但|然而|不过|只是|仅|除非|如果|若|尚不能|不能据此|不代表|可能|或许|考虑)"
)


class EvidenceClaimGatePlanningError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceClaimReviewBatch:
    section_id: str
    batch_index: int
    evidence_units: tuple[dict[str, Any], ...]

    @property
    def evidence_unit_ids(self) -> tuple[str, ...]:
        return tuple(str(row["evidence_unit_id"]) for row in self.evidence_units)


def plan_evidence_claim_review_batches(
    evidence_units: list[dict[str, Any]],
    *,
    section_map: SectionMap | None,
    max_batch_size: int,
) -> list[EvidenceClaimReviewBatch]:
    if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int) or max_batch_size < 1:
        raise EvidenceClaimGatePlanningError("evidence_claim_batch_size 必须是正整数。")
    evidence_ids = [str(row.get("evidence_unit_id") or "") for row in evidence_units]
    if not evidence_ids or "" in evidence_ids:
        raise EvidenceClaimGatePlanningError("Evidence claim 门禁需要非空且具有 ID 的 Evidence Unit。")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise EvidenceClaimGatePlanningError("Evidence claim 门禁收到重复 evidence_unit_id。")

    if section_map is None:
        grouped = [(PAPER_ROOT_SECTION_ID, list(evidence_units))]
    else:
        assignment = assign_evidence_to_sections(evidence_units, section_map)
        sections = {section.section_id: section for section in section_map.sections}
        top_level_ids: list[str] = []
        for section in section_map.sections:
            section_id = _top_level_section_id(section.section_id, sections)
            if section_id not in top_level_ids:
                top_level_ids.append(section_id)
        section_order = top_level_ids
        section_order.append(PAPER_ROOT_SECTION_ID)
        by_section = {section_id: [] for section_id in section_order}
        for unit in evidence_units:
            evidence_id = str(unit["evidence_unit_id"])
            section_id = assignment.evidence_to_section[evidence_id]
            if section_id != PAPER_ROOT_SECTION_ID:
                section_id = _top_level_section_id(section_id, sections)
            by_section.setdefault(section_id, []).append(unit)
        grouped = [
            (section_id, units)
            for section_id, units in by_section.items()
            if units
        ]

    batches: list[EvidenceClaimReviewBatch] = []
    for section_id, units in grouped:
        for offset in range(0, len(units), max_batch_size):
            batches.append(
                EvidenceClaimReviewBatch(
                    section_id=section_id,
                    batch_index=(offset // max_batch_size) + 1,
                    evidence_units=tuple(copy.deepcopy(units[offset : offset + max_batch_size])),
                )
            )
    observed_ids = [
        evidence_id
        for batch in batches
        for evidence_id in batch.evidence_unit_ids
    ]
    if len(observed_ids) != len(evidence_ids) or set(observed_ids) != set(evidence_ids):
        raise EvidenceClaimGatePlanningError(
            "Evidence claim 小批计划没有完整且唯一覆盖 Evidence Unit。"
        )
    return batches


def build_evidence_claim_confirmation_context(
    *,
    target_evidence_unit: dict[str, Any],
    first_pass_review: dict[str, Any],
    materials: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_id = str(target_evidence_unit.get("evidence_unit_id") or "")
    if not evidence_id or str(first_pass_review.get("evidence_unit_id") or "") != evidence_id:
        raise EvidenceClaimGatePlanningError("窄上下文复核的 Evidence 与首轮结论身份不一致。")
    ordered = sorted(materials, key=lambda row: (int(row.get("order") or 0), str(row.get("material_id") or "")))
    positions = {str(row.get("material_id") or ""): index for index, row in enumerate(ordered)}
    cited_ids = {
        str(row.get("material_id") or "")
        for row in target_evidence_unit.get("citations", [])
    }
    if not cited_ids or "" in cited_ids:
        raise EvidenceClaimGatePlanningError(f"Evidence {evidence_id!r} 缺少可定位 citation。")
    unknown = sorted(cited_ids - set(positions))
    if unknown:
        raise EvidenceClaimGatePlanningError(
            f"Evidence {evidence_id!r} 引用未知 Card：{unknown}。"
        )
    context_indexes: set[int] = set()
    for material_id in cited_ids:
        position = positions[material_id]
        context_indexes.add(position)
        if position > 0:
            context_indexes.add(position - 1)
        if position + 1 < len(ordered):
            context_indexes.add(position + 1)
    context_cards = [
        {
            "material_id": str(ordered[index]["material_id"]),
            "order": ordered[index].get("order"),
            "title": ordered[index].get("clean_title"),
            "heading_path": ordered[index].get("heading_path", []),
            "extract": ordered[index].get("extract"),
            "is_cited": str(ordered[index]["material_id"]) in cited_ids,
        }
        for index in sorted(context_indexes)
    ]
    return {
        "evidence_unit": copy.deepcopy(target_evidence_unit),
        "first_pass_review": copy.deepcopy(first_pass_review),
        "context_cards": context_cards,
    }


def build_evidence_claim_gate_decisions(
    *,
    first_pass_reviews: list[dict[str, Any]],
    confirmation_reviews: dict[str, dict[str, Any]],
    first_request_ids: dict[str, str],
    confirmation_request_ids: dict[str, str],
    accepted_override_ids: set[str],
    accepted_override_reviews: dict[str, dict[str, Any]],
    deterministic_quote_support_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    deterministic_quote_support_ids = deterministic_quote_support_ids or set()
    decisions: list[dict[str, Any]] = []
    for first in first_pass_reviews:
        evidence_id = str(first["evidence_unit_id"])
        confirmation = confirmation_reviews.get(evidence_id)
        override_matches = (
            evidence_id in accepted_override_ids
            and _review_signature(first) == _review_signature(accepted_override_reviews.get(evidence_id))
        )
        if first["verdict"] == "directly_supported":
            decision = "passed_first_pass"
        elif evidence_id in deterministic_quote_support_ids:
            decision = "passed_quote_containment"
        elif override_matches:
            decision = "accepted_human_override"
        elif confirmation is None:
            decision = "confirmation_missing"
        elif confirmation["verdict"] == "directly_supported":
            decision = "gate_disagreement"
        else:
            decision = "confirmed_blocker"
        decisions.append(
            {
                "schema_version": EVIDENCE_CLAIM_GATE_DECISION_SCHEMA_VERSION,
                "evidence_unit_id": evidence_id,
                "decision": decision,
                "first_pass_request_id": first_request_ids.get(evidence_id),
                "confirmation_request_id": confirmation_request_ids.get(evidence_id),
                "first_pass_review": copy.deepcopy(first),
                "confirmation_review": copy.deepcopy(confirmation),
            }
        )
    return decisions


def effective_evidence_claim_reviews(
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for row in decisions:
        if row["decision"] == "confirmed_blocker":
            reviews.append(copy.deepcopy(row["confirmation_review"]))
        elif row["decision"] == "passed_quote_containment":
            review = copy.deepcopy(row["first_pass_review"])
            review["verdict"] = "directly_supported"
            review["reason"] = "代码已证明 claim 的全部分句在同一条逐字引文中按原顺序出现。"
            review["unsupported_fragments"] = []
            reviews.append(review)
        else:
            reviews.append(copy.deepcopy(row["first_pass_review"]))
    return reviews


def has_ordered_verbatim_quote_support(evidence_unit: dict[str, Any]) -> bool:
    claim = str(evidence_unit.get("claim") or "")
    clauses = [
        _compact_verbatim_text(value)
        for value in _CLAIM_CLAUSE_SPLIT_RE.split(claim)
        if _compact_verbatim_text(value)
    ]
    if not clauses or sum(len(value) for value in clauses) < 8:
        return False
    for citation in evidence_unit.get("citations", []):
        quote = _compact_verbatim_text(str(citation.get("quote") or ""))
        if not quote:
            continue
        cursor = 0
        matched = True
        for clause_index, clause in enumerate(clauses):
            position = quote.find(clause, cursor)
            if position < 0:
                matched = False
                break
            omitted = quote[cursor:position]
            if clause_index == 0:
                omitted = re.split(r"[。！？!?；;]", omitted)[-1]
            if _UNSAFE_OMISSION_RE.search(omitted):
                matched = False
                break
            cursor = position + len(clause)
        if matched:
            return True
    return False


def _compact_verbatim_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def _review_signature(review: dict[str, Any] | None) -> tuple[object, object] | None:
    if not review:
        return None
    return review.get("verdict"), review.get("unsupported_fragments")


def _top_level_section_id(
    section_id: str,
    sections: dict[str, Any],
) -> str:
    current = sections.get(section_id)
    if current is None:
        raise EvidenceClaimGatePlanningError(f"Evidence 引用未知章节：{section_id!r}。")
    while current.parent_section_id is not None:
        parent = sections.get(current.parent_section_id)
        if parent is None:
            raise EvidenceClaimGatePlanningError(
                f"章节父链引用未知章节：{current.parent_section_id!r}。"
            )
        current = parent
    return str(current.section_id)
