"""Shared data models for workspace, parsing, and pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow_iso() -> str:
    """Return a timezone-neutral ISO timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ContentRef:
    """Reference to a slice of the normalized markdown document."""

    path: str
    start_line: int
    end_line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContentRef":
        return cls(
            path=data["path"],
            start_line=int(data["start_line"]),
            end_line=int(data["end_line"]),
        )


@dataclass
class SectionNode:
    """Structured representation of a paper section."""

    id: str
    title_raw: str
    title_norm: str
    role: str
    semantic_level: int
    numbering_path: list[int]
    confidence: float
    content_ref: dict[str, Any] | None
    children: list["SectionNode"] = field(default_factory=list)
    parent_id: str | None = None
    node_type: str = "section"
    ordinal: int = 0
    is_low_confidence: bool = False
    evidence: list[str] = field(default_factory=list)
    llm_reviewed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title_raw": self.title_raw,
            "title_norm": self.title_norm,
            "role": self.role,
            "semantic_level": self.semantic_level,
            "numbering_path": self.numbering_path,
            "confidence": self.confidence,
            "content_ref": self.content_ref,
            "children": [child.to_dict() for child in self.children],
            "parent_id": self.parent_id,
            "node_type": self.node_type,
            "ordinal": self.ordinal,
            "is_low_confidence": self.is_low_confidence,
            "evidence": self.evidence,
            "llm_reviewed": self.llm_reviewed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SectionNode":
        return cls(
            id=data["id"],
            title_raw=data["title_raw"],
            title_norm=data["title_norm"],
            role=data["role"],
            semantic_level=int(data["semantic_level"]),
            numbering_path=[int(value) for value in data.get("numbering_path", [])],
            confidence=float(data.get("confidence", 0.0)),
            content_ref=data.get("content_ref"),
            children=[cls.from_dict(child) for child in data.get("children", [])],
            parent_id=data.get("parent_id"),
            node_type=data.get("node_type", "section"),
            ordinal=data.get("ordinal", 0),
            is_low_confidence=data.get("is_low_confidence", False),
            evidence=data.get("evidence", []),
            llm_reviewed=data.get("llm_reviewed", False),
        )


@dataclass
class DocumentStructure:
    """Top-level semantic structure of a processed document."""

    paper_id: str
    source_type: str
    language: str
    detected_schema: str
    nodes: list[SectionNode]
    stats: dict[str, Any]
    generated_at: str = field(default_factory=utcnow_iso)
    diagnostics_version: str = "1.0"
    low_confidence_node_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "source_type": self.source_type,
            "language": self.language,
            "detected_schema": self.detected_schema,
            "nodes": [node.to_dict() for node in self.nodes],
            "stats": self.stats,
            "generated_at": self.generated_at,
            "diagnostics_version": self.diagnostics_version,
            "low_confidence_node_ids": self.low_confidence_node_ids,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentStructure":
        return cls(
            paper_id=data["paper_id"],
            source_type=data["source_type"],
            language=data["language"],
            detected_schema=data["detected_schema"],
            nodes=[SectionNode.from_dict(item) for item in data.get("nodes", [])],
            stats=data.get("stats", {}),
            generated_at=data.get("generated_at", utcnow_iso()),
            diagnostics_version=data.get("diagnostics_version", "1.0"),
            low_confidence_node_ids=data.get("low_confidence_node_ids", []),
        )


@dataclass
class RawConversionResult:
    """MinerU conversion output before normalization."""

    markdown: str
    raw_files: dict[str, bytes] = field(default_factory=dict)


@dataclass
class ExportedSection:
    """Markdown file exported for a top-level section."""

    node_id: str
    title: str
    file_name: str
    content: str


@dataclass
class RunRecord:
    """Per-paper processing status stored in the workspace."""

    paper_id: str
    source_type: str
    source: str
    status: str
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)
    artifacts: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def touch(self, status: str | None = None) -> None:
        if status:
            self.status = status
        self.updated_at = utcnow_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "source_type": self.source_type,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "artifacts": self.artifacts,
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        return cls(
            paper_id=data["paper_id"],
            source_type=data["source_type"],
            source=data["source"],
            status=data["status"],
            created_at=data.get("created_at", utcnow_iso()),
            updated_at=data.get("updated_at", utcnow_iso()),
            artifacts=data.get("artifacts", {}),
            errors=data.get("errors", []),
        )


@dataclass
class LowConfidenceNode:
    """Diagnostic record for a single low-confidence node."""

    node_id: str
    title_raw: str
    role: str
    semantic_level: int
    confidence: float
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title_raw": self.title_raw,
            "role": self.role,
            "semantic_level": self.semantic_level,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LowConfidenceNode":
        return cls(
            node_id=data["node_id"],
            title_raw=data["title_raw"],
            role=data["role"],
            semantic_level=int(data["semantic_level"]),
            confidence=float(data["confidence"]),
            evidence=data.get("evidence", []),
        )


@dataclass
class DiagnosticsReport:
    """Per-paper diagnostic report with low-confidence node evidence."""

    paper_id: str
    detected_schema: str
    generated_at: str
    low_confidence_nodes: list[LowConfidenceNode]
    schema_evidence: list[str]
    warnings: list[str]
    document_mode: str = "unknown"
    numbering_profile: str = "mixed_unknown"
    heading_family_counts: dict[str, int] = field(default_factory=dict)
    area_trace: list[dict[str, Any]] = field(default_factory=list)
    body_start_rejections: list[dict[str, Any]] = field(default_factory=list)
    first_body_node_id: str | None = None
    first_body_title: str | None = None
    body_locked: bool = False
    pre_body_node_count: int = 0
    front_matter_after_body_count: int = 0
    tail_special_after_body_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "detected_schema": self.detected_schema,
            "generated_at": self.generated_at,
            "low_confidence_nodes": [n.to_dict() for n in self.low_confidence_nodes],
            "schema_evidence": self.schema_evidence,
            "warnings": self.warnings,
            "document_mode": self.document_mode,
            "numbering_profile": self.numbering_profile,
            "heading_family_counts": self.heading_family_counts,
            "area_trace": self.area_trace,
            "body_start_rejections": self.body_start_rejections,
            "first_body_node_id": self.first_body_node_id,
            "first_body_title": self.first_body_title,
            "body_locked": self.body_locked,
            "pre_body_node_count": self.pre_body_node_count,
            "front_matter_after_body_count": self.front_matter_after_body_count,
            "tail_special_after_body_count": self.tail_special_after_body_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiagnosticsReport":
        return cls(
            paper_id=data["paper_id"],
            detected_schema=data["detected_schema"],
            generated_at=data["generated_at"],
            low_confidence_nodes=[LowConfidenceNode.from_dict(n) for n in data.get("low_confidence_nodes", [])],
            schema_evidence=data.get("schema_evidence", []),
            warnings=data.get("warnings", []),
            document_mode=data.get("document_mode", "unknown"),
            numbering_profile=data.get("numbering_profile", data.get("detected_schema", "mixed_unknown")),
            heading_family_counts=data.get("heading_family_counts", {}),
            area_trace=data.get("area_trace", []),
            body_start_rejections=data.get("body_start_rejections", []),
            first_body_node_id=data.get("first_body_node_id"),
            first_body_title=data.get("first_body_title"),
            body_locked=data.get("body_locked", False),
            pre_body_node_count=int(data.get("pre_body_node_count", 0)),
            front_matter_after_body_count=int(data.get("front_matter_after_body_count", 0)),
            tail_special_after_body_count=int(data.get("tail_special_after_body_count", 0)),
        )


@dataclass
class OutlineNode:
    """Lightweight outline node for structure overview and debugging."""

    id: str
    title: str
    role: str
    semantic_level: int
    ordinal: int
    is_low_confidence: bool
    parent_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "role": self.role,
            "semantic_level": self.semantic_level,
            "ordinal": self.ordinal,
            "is_low_confidence": self.is_low_confidence,
            "parent_id": self.parent_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OutlineNode":
        return cls(
            id=data["id"],
            title=data["title"],
            role=data["role"],
            semantic_level=int(data["semantic_level"]),
            ordinal=int(data["ordinal"]),
            is_low_confidence=data.get("is_low_confidence", False),
            parent_id=data.get("parent_id"),
        )


# ----------------------------------------------------------------------
# Refinement models
# ----------------------------------------------------------------------


@dataclass
class RefinementSuggestion:
    """Single LLM suggestion for a low-confidence node."""

    node_id: str
    role: str | None = None
    semantic_level: int | None = None
    confidence: float | None = None
    evidence: list[str] | None = None
    reason: str = ""
    # Track forbidden fields that LLM attempted to modify
    forbidden_fields: set[str] = field(default_factory=set)

    ALLOWED_FIELDS = {"role", "semantic_level", "confidence", "evidence"}
    FORBIDDEN_FIELDS = {"id", "parent_id", "ordinal", "content_ref", "title_raw", "title_norm"}

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"node_id": self.node_id, "reason": self.reason}
        if self.role is not None:
            result["role"] = self.role
        if self.semantic_level is not None:
            result["semantic_level"] = self.semantic_level
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.evidence is not None:
            result["evidence"] = self.evidence
        if self.forbidden_fields:
            result["_forbidden_attempted"] = list(self.forbidden_fields)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RefinementSuggestion":
        # Detect forbidden fields that LLM attempted to include
        all_keys = set(data.keys())
        allowed = cls.ALLOWED_FIELDS | {"node_id", "reason"}
        forbidden_attempted = all_keys - allowed
        return cls(
            node_id=data["node_id"],
            role=data.get("role"),
            semantic_level=data.get("semantic_level"),
            confidence=data.get("confidence"),
            evidence=data.get("evidence"),
            reason=data.get("reason", ""),
            forbidden_fields=forbidden_attempted,
        )

    def applied_fields(self) -> dict[str, Any]:
        """Return only the fields that are allowed to be modified."""
        result: dict[str, Any] = {}
        if self.role is not None:
            result["role"] = self.role
        if self.semantic_level is not None:
            result["semantic_level"] = self.semantic_level
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.evidence is not None:
            result["evidence"] = self.evidence
        return result

    def has_forbidden_attempt(self) -> bool:
        """Return True if LLM attempted to modify forbidden fields."""
        return bool(self.forbidden_fields)


@dataclass
class RefinementRecord:
    """Audit trail for a single LLM refinement pass."""

    paper_id: str
    generated_at: str = field(default_factory=utcnow_iso)
    input_node_ids: list[str] = field(default_factory=list)
    applied_node_ids: list[str] = field(default_factory=list)
    skipped_node_ids: list[str] = field(default_factory=list)
    suggestions: list[RefinementSuggestion] = field(default_factory=list)
    skipped_details: dict[str, str] = field(default_factory=dict)  # node_id -> reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "generated_at": self.generated_at,
            "input_node_ids": self.input_node_ids,
            "applied_node_ids": self.applied_node_ids,
            "skipped_node_ids": self.skipped_node_ids,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "skipped_details": self.skipped_details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RefinementRecord":
        return cls(
            paper_id=data["paper_id"],
            generated_at=data.get("generated_at", utcnow_iso()),
            input_node_ids=data.get("input_node_ids", []),
            applied_node_ids=data.get("applied_node_ids", []),
            skipped_node_ids=data.get("skipped_node_ids", []),
            suggestions=[RefinementSuggestion.from_dict(s) for s in data.get("suggestions", [])],
            skipped_details=data.get("skipped_details", {}),
        )
