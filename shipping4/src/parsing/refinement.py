"""LLM-driven refinement of low-confidence structure nodes."""

from __future__ import annotations

from collections import Counter
import logging
from typing import Any

from src.clients.llm import LLMClient
from src.core.models import (
    DocumentStructure,
    RefinementRecord,
    RefinementSuggestion,
    SectionNode,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

# Fields that LLM is NOT allowed to modify
FORBIDDEN_FIELDS = {"id", "parent_id", "ordinal", "content_ref", "title_raw", "title_norm"}
BODY_ROLE = "body"
SPECIAL_ROLES = {"front_matter", "toc", "references", "appendix", "ack"}
VALID_ROLES = SPECIAL_ROLES | {BODY_ROLE}
LOW_CONFIDENCE_THRESHOLD = 0.7
LOW_CONFIDENCE_EVIDENCE = {"schema_conflict", "level_jump"}


class RefinementError(Exception):
    """Raised when refinement processing fails."""


def _flatten_nodes(nodes: list[SectionNode]) -> dict[str, SectionNode]:
    """Flatten tree into a dict mapping node id -> node."""
    result: dict[str, SectionNode] = {}

    def _walk(node_list: list[SectionNode]) -> None:
        for node in node_list:
            result[node.id] = node
            _walk(node.children)

    _walk(nodes)
    return result


class RefinementEngine:
    """Merge LLM suggestions into a DocumentStructure with strict rules."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    def refine(
        self,
        structure: DocumentStructure,
        enabled: bool = True,
    ) -> tuple[DocumentStructure, RefinementRecord]:
        """
        Run LLM refinement on low-confidence nodes.

        Args:
            structure: The rule-generated document structure.
            enabled: Whether to actually call LLM (False = skip, useful for testing).

        Returns:
            Tuple of (updated_structure, refinement_record).
        """
        record = RefinementRecord(paper_id=structure.paper_id, generated_at=utcnow_iso())
        low_conf_ids = set(structure.low_confidence_node_ids)

        if not low_conf_ids:
            logger.info("No low-confidence nodes to refine for paper %s", structure.paper_id)
            return structure, record

        record.input_node_ids = list(low_conf_ids)

        if not enabled:
            logger.info("Refinement disabled, skipping LLM for paper %s", structure.paper_id)
            return structure, record

        # Build node input for LLM
        flat = _flatten_nodes(structure.nodes)
        llm_input_nodes: list[dict[str, Any]] = []
        for node_id in low_conf_ids:
            if node_id in flat:
                node = flat[node_id]
                llm_input_nodes.append({
                    "id": node.id,
                    "title_raw": node.title_raw,
                    "title_norm": node.title_norm,
                    "role": node.role,
                    "semantic_level": node.semantic_level,
                    "confidence": node.confidence,
                    "evidence": list(node.evidence),
                    "parent_id": node.parent_id,
                    "ordinal": node.ordinal,
                })

        if not llm_input_nodes:
            logger.warning("Low-confidence IDs set but no matching nodes found for paper %s", structure.paper_id)
            return structure, record

        raw_suggestions = self.llm.refine_low_confidence_nodes(
            paper_id=structure.paper_id,
            detected_schema=structure.detected_schema,
            language=structure.language,
            nodes=llm_input_nodes,
        )

        suggestions = _parse_suggestions(raw_suggestions, record)

        # Merge suggestions
        applied, skipped = _merge_suggestions(structure, suggestions, flat, record)
        record.applied_node_ids = applied
        record.skipped_node_ids = skipped

        return structure, record


def _parse_suggestions(
    raw: list[dict[str, Any]],
    record: RefinementRecord,
) -> list[RefinementSuggestion]:
    """Parse raw LLM output into RefinementSuggestion objects, tracking invalid ones."""
    suggestions: list[RefinementSuggestion] = []
    for item in raw:
        if not isinstance(item, dict):
            record.skipped_details[str(item)] = "not a dict"
            continue
        node_id = item.get("node_id")
        if not node_id:
            record.skipped_details[str(item)] = "missing node_id"
            continue
        suggestions.append(RefinementSuggestion.from_dict(item))
    return suggestions


def _merge_suggestions(
    structure: DocumentStructure,
    suggestions: list[RefinementSuggestion],
    flat: dict[str, SectionNode],
    record: RefinementRecord,
) -> tuple[list[str], list[str]]:
    """
    Apply merge rules and update the structure in-place.

    Returns:
        Tuple of (applied_node_ids, skipped_node_ids).
    """
    applied: list[str] = []
    skipped: list[str] = []

    for suggestion in suggestions:
        node_id = suggestion.node_id

        # Rule 1: Unknown node_id -> ignore
        if node_id not in flat:
            skipped.append(node_id)
            record.skipped_details[node_id] = "unknown node_id"
            logger.warning("LLM suggested unknown node_id %s, skipping", node_id)
            continue

        node = flat[node_id]

        # Rule 2: Forbidden field change -> ignore
        if suggestion.has_forbidden_attempt():
            skipped.append(node_id)
            record.skipped_details[node_id] = f"attempted forbidden fields: {suggestion.forbidden_fields}"
            logger.warning("LLM attempted forbidden fields %s on node %s, skipping", suggestion.forbidden_fields, node_id)
            continue

        changes = suggestion.applied_fields()

        invalid_reason = _validate_role_change(node, changes, flat)
        if invalid_reason:
            skipped.append(node_id)
            record.skipped_details[node_id] = invalid_reason
            logger.warning("LLM suggestion on node %s rejected: %s", node_id, invalid_reason)
            continue

        invalid_reason = _validate_semantic_level_change(node, changes, flat)
        if invalid_reason:
            skipped.append(node_id)
            record.skipped_details[node_id] = invalid_reason
            logger.warning("LLM suggestion on node %s rejected: %s", node_id, invalid_reason)
            continue

        # All checks passed -> apply
        if "role" in changes:
            node.role = changes["role"]
        if "semantic_level" in changes:
            node.semantic_level = changes["semantic_level"]
        if node.role in SPECIAL_ROLES:
            node.semantic_level = 1
        if "confidence" in changes:
            node.confidence = changes["confidence"]
        if "evidence" in changes:
            node.evidence = _dedupe_evidence(list(changes["evidence"]) + ["llm_applied"])
        else:
            node.evidence = _dedupe_evidence(list(node.evidence) + ["llm_applied"])

        node.llm_reviewed = True
        applied.append(node_id)
        record.suggestions.append(suggestion)

    _refresh_structure_state(structure)
    return applied, skipped


def _validate_role_change(
    node: SectionNode,
    changes: dict[str, Any],
    flat: dict[str, SectionNode],
) -> str | None:
    if "role" not in changes:
        return None

    new_role = changes["role"]
    if new_role not in VALID_ROLES:
        return f"invalid role: {new_role}"

    if new_role == node.role:
        return None

    if new_role in SPECIAL_ROLES:
        if node.parent_id is not None:
            return "would require reparenting nested node to a non-body root"
        if node.children:
            return "would require rebuilding children under a non-body node"
        return None

    parent = flat[node.parent_id] if node.parent_id and node.parent_id in flat else None
    if parent and parent.role != BODY_ROLE:
        return "body node cannot be nested under a non-body parent"
    if any(child.role != BODY_ROLE for child in node.children):
        return "body node cannot keep non-body children"
    return None


def _validate_semantic_level_change(
    node: SectionNode,
    changes: dict[str, Any],
    flat: dict[str, SectionNode],
) -> str | None:
    if "semantic_level" not in changes:
        return None

    target_role = changes.get("role", node.role)
    if target_role != BODY_ROLE:
        return None

    new_level = changes["semantic_level"]
    parent = flat[node.parent_id] if node.parent_id and node.parent_id in flat else None
    if parent and parent.role == BODY_ROLE and new_level is not None and parent.semantic_level >= new_level:
        return "would break parent-child semantic level ordering"
    if any(child.role == BODY_ROLE and child.semantic_level <= new_level for child in node.children):
        return "would break child semantic level ordering"
    return None


def _dedupe_evidence(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _refresh_structure_state(structure: DocumentStructure) -> None:
    _refresh_tree_metadata(structure.nodes)
    flat_nodes = list(_flatten_nodes(structure.nodes).values())
    for node in flat_nodes:
        node.evidence = [item for item in node.evidence if item != "low_confidence_threshold"]
        threshold_triggered = node.confidence < LOW_CONFIDENCE_THRESHOLD
        risk_triggered = any(marker in LOW_CONFIDENCE_EVIDENCE for marker in node.evidence)
        node.is_low_confidence = threshold_triggered or risk_triggered
        if threshold_triggered:
            node.evidence.append("low_confidence_threshold")

    structure.low_confidence_node_ids = [node.id for node in flat_nodes if node.is_low_confidence]

    role_counts = Counter(node.role for node in flat_nodes)
    body_nodes = [node for node in flat_nodes if node.role == BODY_ROLE]
    toc_nodes = [node for node in flat_nodes if node.role == "toc"]
    structure.stats.update(
        {
            "total_nodes": len(flat_nodes),
            "role_counts": dict(role_counts),
            "max_semantic_depth": max((node.semantic_level for node in body_nodes), default=1),
            "body_node_count": len(body_nodes),
            "toc_node_count": len(toc_nodes),
        }
    )


def _refresh_tree_metadata(nodes: list[SectionNode], parent: SectionNode | None = None) -> None:
    for ordinal, node in enumerate(nodes, start=1):
        node.parent_id = parent.id if parent else None
        node.ordinal = ordinal
        if node.role in SPECIAL_ROLES:
            node.node_type = "special"
            node.semantic_level = 1
        elif node.children:
            node.node_type = "container"
        else:
            node.node_type = "section"
        _refresh_tree_metadata(node.children, parent=node)
