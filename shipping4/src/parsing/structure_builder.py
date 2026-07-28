"""Build semantic document structures from normalized markdown."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from src.core.models import (
    ContentRef,
    DiagnosticsReport,
    DocumentStructure,
    ExportedSection,
    LowConfidenceNode,
    OutlineNode,
    SectionNode,
    utcnow_iso,
)
from src.parsing.front_matter import (
    DocArea,
    detect_area,
    detect_document_mode,
    is_ack_title,
    is_appendix_title,
    is_author_bio_title,
    is_front_matter_title,
    is_pre_body_misc_title,
    is_reference_title,
    is_tail_special_title,
    is_toc_title,
    looks_like_toc_entry,
)
from src.parsing.headings import (
    HeadingInfo,
    BODY_START_KEYWORDS,
    combine_numbering_path,
    detect_heading_family_counts,
    detect_schema,
    family_rank,
    is_compatible_parent,
    is_level_jump,
    parse_heading_info,
)
from src.parsing.markdown import HeadingCandidate, extract_text_from_ref, normalize_markdown, parse_markdown_headings
from src.workspace.repository import WorkspaceRepository

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.7
DIAGNOSTICS_VERSION = "2.0"
LOW_CONFIDENCE_EVIDENCE = {"schema_conflict", "level_jump", "profile_fallback", "front_matter_after_body"}


@dataclass
class CandidateRecord:
    """Internal representation of a heading before tree construction."""

    id: str
    candidate: HeadingCandidate
    heading: HeadingInfo
    role: str
    area: DocArea
    confidence: float
    semantic_level: int
    numbering_path: list[int]
    evidence: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    parent_id: str | None = None
    accepted_as_body: bool = False
    state_before: DocArea = DocArea.START
    state_after: DocArea = DocArea.START

    @property
    def title_raw(self) -> str:
        return self.candidate.title

    @property
    def title_norm(self) -> str:
        return self.heading.normalized_title


@dataclass
class ClassificationResult:
    records: list[CandidateRecord]
    document_mode: str
    initial_profile: str
    final_profile: str
    heading_family_counts: dict[str, int]
    body_start_rejections: list[dict]
    body_locked: bool
    front_matter_after_body_count: int
    tail_special_after_body_count: int
    warnings: list[str]


class DocumentStructureBuilder:
    """Convert normalized markdown into a semantic structure tree."""

    def build(self, paper_id: str, markdown_content: str, source_type: str) -> DocumentStructure:
        normalized = normalize_markdown(markdown_content)
        candidates = parse_markdown_headings(normalized)
        document_mode = detect_document_mode(candidates)
        initial_profile = detect_schema([candidate.title for candidate in candidates])
        classified = self._classify_candidates(candidates, document_mode, initial_profile)
        final_profile = self._resolve_body_hierarchy(classified.records, classified.initial_profile)
        classified.final_profile = final_profile
        detailed_nodes = self._nest_nodes(classified.records)
        self._assign_parent_ids(detailed_nodes)
        self._assign_ordinals(detailed_nodes)
        self._assign_node_types(detailed_nodes)
        self._detect_low_confidence_nodes(detailed_nodes)
        nodes = self._simplify_to_chapter_nodes(detailed_nodes, final_profile)
        self._assign_parent_ids(nodes)
        self._assign_ordinals(nodes)
        self._assign_node_types(nodes)
        self._detect_low_confidence_nodes(nodes)
        stats = self._build_stats(nodes, classified)
        low_confidence_ids = [n.id for n in self._flatten(nodes) if n.is_low_confidence]
        language = "mixed" if any("\u4e00" <= char <= "\u9fff" for char in normalized) and any(char.isascii() and char.isalpha() for char in normalized) else "zh"
        return DocumentStructure(
            paper_id=paper_id,
            source_type=source_type,
            language=language,
            detected_schema=final_profile,
            nodes=nodes,
            stats=stats,
            generated_at=utcnow_iso(),
            diagnostics_version=DIAGNOSTICS_VERSION,
            low_confidence_node_ids=low_confidence_ids,
        )

    def build_diagnostics(self, structure: DocumentStructure, schema_evidence: list[str], warnings: list[str]) -> DiagnosticsReport:
        """Build a diagnostics report from the structure."""
        low_conf_nodes = []
        flat = self._flatten(structure.nodes)
        for node in flat:
            if node.is_low_confidence:
                low_conf_nodes.append(
                    LowConfidenceNode(
                        node_id=node.id,
                        title_raw=node.title_raw,
                        role=node.role,
                        semantic_level=node.semantic_level,
                        confidence=node.confidence,
                        evidence=node.evidence,
                    )
                )

        context = structure.stats.get("_diagnostics_context", {})
        merged_warnings = _dedupe_preserve_order([*context.get("warnings", []), *warnings])

        return DiagnosticsReport(
            paper_id=structure.paper_id,
            detected_schema=structure.detected_schema,
            generated_at=structure.generated_at,
            low_confidence_nodes=low_conf_nodes,
            schema_evidence=schema_evidence,
            warnings=merged_warnings,
            document_mode=context.get("document_mode", "unknown"),
            numbering_profile=context.get("numbering_profile", structure.detected_schema),
            heading_family_counts=context.get("heading_family_counts", {}),
            area_trace=context.get("area_trace", []),
            body_start_rejections=context.get("body_start_rejections", []),
            first_body_node_id=context.get("first_body_node_id"),
            first_body_title=context.get("first_body_title"),
            body_locked=context.get("body_locked", False),
            pre_body_node_count=context.get("pre_body_node_count", 0),
            front_matter_after_body_count=context.get("front_matter_after_body_count", 0),
            tail_special_after_body_count=context.get("tail_special_after_body_count", 0),
        )

    def build_outline(self, structure: DocumentStructure) -> list[OutlineNode]:
        """Build a lightweight outline from the structure tree."""
        outline: list[OutlineNode] = []
        for node in structure.nodes:
            self._add_outline_node(node, outline)
        return outline

    def _add_outline_node(self, node: SectionNode, outline: list[OutlineNode]) -> None:
        outline.append(
            OutlineNode(
                id=node.id,
                title=node.title_norm or node.title_raw,
                role=node.role,
                semantic_level=node.semantic_level,
                ordinal=node.ordinal,
                is_low_confidence=node.is_low_confidence,
                parent_id=node.parent_id,
            )
        )
        for child in node.children:
            self._add_outline_node(child, outline)

    def export_sections(self, structure: DocumentStructure, markdown_content: str, repository: WorkspaceRepository) -> list[ExportedSection]:
        normalized = normalize_markdown(markdown_content)
        sections: list[ExportedSection] = []
        for index, node in enumerate(structure.nodes):
            content = extract_text_from_ref(normalized, node.content_ref)
            if not content:
                continue
            sections.append(
                ExportedSection(
                    node_id=node.id,
                    title=node.title_norm or node.title_raw,
                    file_name=repository.safe_file_name(node.title_norm or node.title_raw, index),
                    content=content + "\n",
                )
            )
        return sections

    def _classify_candidates(self, candidates: list[HeadingCandidate], document_mode: str, initial_profile: str) -> ClassificationResult:
        records: list[CandidateRecord] = []
        body_start_rejections: list[dict] = []
        warnings: list[str] = []
        current_area = DocArea.START
        current_index = 0
        pending_indices: list[int] = []
        body_locked = False
        front_matter_after_body_count = 0
        tail_special_after_body_count = 0
        heading_family_counts = detect_heading_family_counts([candidate.title for candidate in candidates])
        toc_seen = False
        toc_consumed = 0

        while current_index < len(candidates):
            candidate = candidates[current_index]
            heading = parse_heading_info(candidate.title, initial_profile)
            state_before = current_area
            lookahead_infos = [
                parse_heading_info(item.title, initial_profile)
                for item in candidates[current_index + 1 : current_index + 3]
            ]

            if not body_locked and is_toc_title(candidate.title):
                self._finalize_pending_as_front_matter(records, pending_indices, body_start_rejections, "toc_entered")
                toc_candidate, consumed = self._merge_toc_candidates(candidates[current_index:], initial_profile)
                record = self._make_record(
                    len(records),
                    toc_candidate,
                    parse_heading_info(toc_candidate.title, initial_profile),
                    role="toc",
                    area=DocArea.TOC,
                    confidence=1.0,
                    semantic_level=1,
                    state_before=state_before,
                    state_after=DocArea.TOC,
                    reason_codes=["toc_entered"],
                )
                records.append(record)
                current_area = DocArea.TOC
                toc_seen = True
                toc_consumed += consumed
                current_index += consumed
                continue

            if not body_locked and current_area == DocArea.TOC and looks_like_toc_entry(candidate.title, candidate.content, initial_profile):
                record = self._make_record(
                    len(records),
                    candidate,
                    heading,
                    role="front_matter",
                    area=DocArea.TOC,
                    confidence=max(heading.confidence, 0.72),
                    semantic_level=1,
                    state_before=state_before,
                    state_after=DocArea.TOC,
                    reason_codes=["toc_item"],
                )
                records.append(record)
                current_index += 1
                continue

            if body_locked and is_tail_special_title(candidate.title):
                area = self._tail_area_for_title(candidate.title)
                record = self._make_record(
                    len(records),
                    candidate,
                    heading,
                    role=self._role_for_area(area),
                    area=area,
                    confidence=1.0,
                    semantic_level=1,
                    state_before=state_before,
                    state_after=area,
                    reason_codes=["tail_special_after_body"],
                )
                records.append(record)
                tail_special_after_body_count += 1
                current_area = area
                current_index += 1
                continue

            if body_locked:
                if is_front_matter_title(candidate.title) or is_pre_body_misc_title(candidate.title):
                    record = self._make_record(
                        len(records),
                        candidate,
                        heading,
                        role="front_matter",
                        area=current_area,
                        confidence=max(heading.confidence, 0.72),
                        semantic_level=1,
                        state_before=state_before,
                        state_after=current_area,
                        evidence=["front_matter_after_body"],
                        reason_codes=["front_matter_after_body"],
                    )
                    records.append(record)
                    front_matter_after_body_count += 1
                else:
                    evidence = ["plain_heading"] if heading.family == "plain" else []
                    record = self._make_record(
                        len(records),
                        candidate,
                        heading,
                        role="body",
                        area=DocArea.BODY_LOCKED,
                        confidence=heading.confidence,
                        semantic_level=1,
                        state_before=state_before,
                        state_after=DocArea.BODY_LOCKED,
                        evidence=evidence,
                        reason_codes=["body_locked_followup"],
                    )
                    record.accepted_as_body = True
                    records.append(record)
                current_area = DocArea.BODY_LOCKED
                current_index += 1
                continue

            next_area = detect_area(candidate.title, candidate.content, current_area, document_mode)
            if next_area != current_area and next_area != DocArea.TOC:
                self._finalize_pending_as_front_matter(records, pending_indices, body_start_rejections, "special_front_matter")
                record = self._make_record(
                    len(records),
                    candidate,
                    heading,
                    role="front_matter",
                    area=next_area,
                    confidence=max(heading.confidence, 0.95 if is_front_matter_title(candidate.title) else 0.72),
                    semantic_level=1,
                    state_before=state_before,
                    state_after=next_area,
                    evidence=["front_matter_boundary"] if current_area == DocArea.START else [],
                    reason_codes=[next_area.value],
                )
                records.append(record)
                current_area = next_area
                current_index += 1
                continue

            should_lock_body = self._should_lock_body(heading, lookahead_infos, document_mode, current_area)
            body_candidate = self._is_body_candidate(heading)

            if should_lock_body:
                self._promote_pending_to_body(records, pending_indices)
                evidence = ["plain_heading"] if heading.family == "plain" else []
                record = self._make_record(
                    len(records),
                    candidate,
                    heading,
                    role="body",
                    area=DocArea.BODY_LOCKED,
                    confidence=heading.confidence,
                    semantic_level=1,
                    state_before=state_before,
                    state_after=DocArea.BODY_LOCKED,
                    evidence=evidence,
                    reason_codes=["body_locked"],
                )
                record.accepted_as_body = True
                records.append(record)
                body_locked = True
                current_area = DocArea.BODY_LOCKED
                current_index += 1
                continue

            if body_candidate:
                record = self._make_record(
                    len(records),
                    candidate,
                    heading,
                    role="pending_body",
                    area=current_area,
                    confidence=heading.confidence,
                    semantic_level=1,
                    state_before=state_before,
                    state_after=current_area,
                    reason_codes=["pending_body_candidate"],
                )
                records.append(record)
                pending_indices.append(len(records) - 1)
                if len(pending_indices) > 3:
                    oldest = pending_indices.pop(0)
                    self._demote_record_to_front_matter(records[oldest], body_start_rejections, "pending_buffer_exhausted")
                current_index += 1
                continue

            self._finalize_pending_as_front_matter(records, pending_indices, body_start_rejections, "body_lock_not_confirmed")
            record = self._make_record(
                len(records),
                candidate,
                heading,
                role="front_matter",
                area=current_area,
                confidence=max(heading.confidence, 0.72 if is_front_matter_title(candidate.title) else 0.62),
                semantic_level=1,
                state_before=state_before,
                state_after=current_area,
                evidence=["front_matter_boundary"] if current_area == DocArea.START else [],
                reason_codes=["pre_body_non_match"],
            )
            records.append(record)
            current_index += 1

        self._finalize_pending_as_front_matter(records, pending_indices, body_start_rejections, "body_lock_not_confirmed")

        if toc_seen and not body_locked:
            warnings.append("toc_consumption_suspicious")
        if not any(record.role == "body" for record in records):
            warnings.append("body_start_missing")
        if body_start_rejections:
            warnings.append("numbered_heading_classified_as_front_matter")
        if front_matter_after_body_count > 0:
            warnings.append("front_matter_after_body_detected")
        if toc_seen and toc_consumed > 4 and any(record.role == "body" for record in records):
            first_body_idx = next((i for i, record in enumerate(records) if record.role == "body"), 0)
            if first_body_idx > 6:
                warnings.append("body_start_detected_late")

        return ClassificationResult(
            records=records,
            document_mode=document_mode,
            initial_profile=initial_profile,
            final_profile=initial_profile,
            heading_family_counts=heading_family_counts,
            body_start_rejections=body_start_rejections,
            body_locked=body_locked,
            front_matter_after_body_count=front_matter_after_body_count,
            tail_special_after_body_count=tail_special_after_body_count,
            warnings=_dedupe_preserve_order(warnings),
        )

    def _resolve_body_hierarchy(self, records: list[CandidateRecord], initial_profile: str) -> str:
        body_records = [record for record in records if record.role == "body"]
        if not body_records:
            return initial_profile

        profile = self._detect_body_profile(body_records, initial_profile)
        previous_body: list[CandidateRecord] = []

        for record in body_records:
            parent = self._find_compatible_parent(previous_body, record, profile)
            if parent is None:
                if self._is_root_body_record(record, profile, previous_body):
                    record.semantic_level = 1
                    record.parent_id = None
                    record.numbering_path = list(record.heading.numbering_path)
                elif previous_body:
                    fallback_parent = self._nearest_root(previous_body)
                    record.parent_id = fallback_parent.id
                    record.semantic_level = fallback_parent.semantic_level + 1
                    record.numbering_path = combine_numbering_path(fallback_parent.heading, record.heading)
                    record.evidence.append("profile_fallback")
                    if family_rank(record.heading, profile) > family_rank(fallback_parent.heading, profile) + 1:
                        record.evidence.append("level_jump")
                    record.reason_codes.append("profile_fallback")
                else:
                    record.semantic_level = 1
                    record.parent_id = None
                    record.numbering_path = list(record.heading.numbering_path)
                    record.evidence.append("profile_fallback")
                    record.reason_codes.append("profile_fallback")
            else:
                record.parent_id = parent.id
                record.semantic_level = parent.semantic_level + 1
                record.numbering_path = combine_numbering_path(parent.heading, record.heading)
                if is_level_jump(record.heading, parent.heading, profile):
                    record.evidence.append("level_jump")
            if not record.numbering_path:
                record.numbering_path = list(record.heading.numbering_path)
            previous_body.append(record)

        if any("profile_fallback" in record.evidence for record in body_records):
            for record in body_records:
                if "profile_fallback" in record.evidence and "profile_fallback_used" not in record.reason_codes:
                    record.reason_codes.append("profile_fallback_used")
        return profile

    def _detect_body_profile(self, body_records: list[CandidateRecord], initial_profile: str) -> str:
        sample_titles = [record.candidate.title for record in body_records[:5]]
        detected = detect_schema(sample_titles)
        if detected != "mixed_unknown":
            return detected
        return initial_profile

    def _find_compatible_parent(self, previous_body: list[CandidateRecord], record: CandidateRecord, profile: str) -> CandidateRecord | None:
        if record.heading.family == "plain":
            return self._find_plain_markdown_parent(previous_body, record)
        for parent in reversed(previous_body):
            if is_compatible_parent(profile, record.heading, parent.heading):
                return parent
        return None

    def _find_plain_markdown_parent(self, previous_body: list[CandidateRecord], record: CandidateRecord) -> CandidateRecord | None:
        current_level = record.candidate.markdown_level
        for parent in reversed(previous_body):
            if parent.candidate.markdown_level < current_level:
                return parent
        return None

    def _is_root_body_record(self, record: CandidateRecord, profile: str, previous_body: list[CandidateRecord]) -> bool:
        if not previous_body:
            return True
        if record.heading.family == "plain":
            return True
        if record.heading.family in {"chapter_cn", "cn_main", "arabic_main"}:
            return True
        if profile == "decimal_only" and record.heading.family == "decimal_path" and len(record.heading.numbering_path) == 1:
            return True
        if record.heading.family == "plain" and record.title_norm in BODY_START_KEYWORDS:
            return True
        return False

    def _nearest_root(self, previous_body: list[CandidateRecord]) -> CandidateRecord:
        for record in reversed(previous_body):
            if record.semantic_level == 1:
                return record
        return previous_body[-1]

    def _nest_nodes(self, records: list[CandidateRecord]) -> list[SectionNode]:
        roots: list[SectionNode] = []
        node_map: dict[str, SectionNode] = {}

        for record in records:
            node = self._record_to_node(record)
            node_map[node.id] = node

        for record in records:
            node = node_map[record.id]
            if record.role == "body" and record.parent_id and record.parent_id in node_map:
                node_map[record.parent_id].children.append(node)
            else:
                roots.append(node)
        return roots

    def _record_to_node(self, record: CandidateRecord) -> SectionNode:
        return SectionNode(
            id=record.id,
            title_raw=record.title_raw,
            title_norm=record.title_norm,
            role="front_matter" if record.role == "pending_body" else record.role,
            semantic_level=1 if record.role != "body" else max(record.semantic_level, 1),
            numbering_path=record.numbering_path,
            confidence=record.confidence,
            content_ref=ContentRef(
                path="normalized/document.md",
                start_line=record.candidate.start_line,
                end_line=record.candidate.end_line,
            ).to_dict(),
            children=[],
            parent_id=record.parent_id,
            evidence=list(record.evidence),
        )

    def _simplify_to_chapter_nodes(self, nodes: list[SectionNode], profile: str) -> list[SectionNode]:
        """Collapse the body tree to chapter-level roots for downstream use."""
        body_nodes = [node for node in self._flatten(nodes) if node.role == "body"]
        chapter_nodes = self._select_chapter_nodes(body_nodes, profile)
        return [self._collapse_body_root(node) for node in chapter_nodes]

    def _collapse_body_root(self, node: SectionNode) -> SectionNode:
        descendants = self._flatten([node])
        start_line, end_line = self._aggregate_content_span(descendants)
        evidence = _dedupe_preserve_order(
            [marker for item in descendants for marker in item.evidence]
        )
        confidence = min((item.confidence for item in descendants), default=node.confidence)
        llm_reviewed = any(item.llm_reviewed for item in descendants)

        return SectionNode(
            id=node.id,
            title_raw=node.title_raw,
            title_norm=node.title_norm,
            role="body",
            semantic_level=1,
            numbering_path=list(node.numbering_path),
            confidence=confidence,
            content_ref=ContentRef(
                path="normalized/document.md",
                start_line=start_line,
                end_line=end_line,
            ).to_dict(),
            children=[],
            parent_id=None,
            evidence=evidence,
            llm_reviewed=llm_reviewed,
        )

    def _aggregate_content_span(self, nodes: list[SectionNode]) -> tuple[int, int]:
        refs = [node.content_ref for node in nodes if node.content_ref]
        if not refs:
            return 1, 1
        start_line = min(ref["start_line"] for ref in refs)
        end_line = max(ref["end_line"] for ref in refs)
        return start_line, end_line

    def _all_body_nodes_are_plain(self, node: SectionNode) -> bool:
        descendants = [item for item in self._flatten([node]) if item.role == "body"]
        if len(descendants) <= 1:
            return False
        return all(not item.numbering_path for item in descendants)

    def _select_chapter_nodes(self, body_nodes: list[SectionNode], profile: str) -> list[SectionNode]:
        if not body_nodes:
            return []

        chapter_family_nodes = [
            node for node in body_nodes if parse_heading_info(node.title_raw, profile).family == "chapter_cn"
        ]
        if chapter_family_nodes:
            return self._dedupe_chapter_candidates(chapter_family_nodes, profile)

        top_level_body_nodes = [node for node in body_nodes if node.semantic_level == 1]
        numbered_main_nodes = [
            node
            for node in body_nodes
            if node.semantic_level == 1
            and parse_heading_info(node.title_raw, profile).family in {"cn_main", "arabic_main"}
        ]
        if numbered_main_nodes:
            if self._numbered_main_nodes_are_late_sparse_roots(top_level_body_nodes, numbered_main_nodes, profile):
                return self._dedupe_chapter_candidates(top_level_body_nodes, profile)
            return self._dedupe_chapter_candidates(numbered_main_nodes, profile)

        if top_level_body_nodes:
            return self._dedupe_chapter_candidates(top_level_body_nodes, profile)

        if len(body_nodes) >= 2 and all(not node.numbering_path for node in body_nodes):
            return body_nodes

        return body_nodes[:1]

    def _numbered_main_nodes_are_late_sparse_roots(
        self,
        top_level_body_nodes: list[SectionNode],
        numbered_main_nodes: list[SectionNode],
        profile: str,
    ) -> bool:
        if not top_level_body_nodes or not numbered_main_nodes:
            return False
        if len(numbered_main_nodes) >= len(top_level_body_nodes):
            return False
        first_top_level = top_level_body_nodes[0]
        if first_top_level.id == numbered_main_nodes[0].id:
            return False
        return parse_heading_info(first_top_level.title_raw, profile).family == "plain"

    def _dedupe_chapter_candidates(self, nodes: list[SectionNode], profile: str) -> list[SectionNode]:
        grouped: dict[str, SectionNode] = {}
        for node in nodes:
            heading = parse_heading_info(node.title_raw, profile)
            key = heading.title_norm or node.title_norm or node.title_raw
            existing = grouped.get(key)
            if existing is None or self._chapter_sort_key(node) > self._chapter_sort_key(existing):
                grouped[key] = node
        return sorted(grouped.values(), key=lambda node: self._chapter_sort_key(node))

    def _chapter_sort_key(self, node: SectionNode) -> tuple[int, int, int]:
        ref = node.content_ref or {}
        start_line = int(ref.get("start_line", 0))
        end_line = int(ref.get("end_line", start_line))
        span = end_line - start_line
        has_page_tail = 1 if node.title_raw.rstrip().split(" ")[-1].rstrip("-").isdigit() else 0
        return (start_line, -has_page_tail, span)

    def _make_record(
        self,
        index: int,
        candidate: HeadingCandidate,
        heading: HeadingInfo,
        role: str,
        area: DocArea,
        confidence: float,
        semantic_level: int,
        state_before: DocArea,
        state_after: DocArea,
        evidence: list[str] | None = None,
        reason_codes: list[str] | None = None,
    ) -> CandidateRecord:
        return CandidateRecord(
            id=f"node_{index:03d}",
            candidate=candidate,
            heading=heading,
            role=role,
            area=area,
            confidence=confidence,
            semantic_level=semantic_level,
            numbering_path=list(heading.numbering_path),
            evidence=list(evidence or []),
            reason_codes=list(reason_codes or []),
            state_before=state_before,
            state_after=state_after,
        )

    def _merge_toc_candidates(self, candidates: list[HeadingCandidate], schema: str) -> tuple[HeadingCandidate, int]:
        toc_candidates = [candidates[0]]
        consumed = 1
        for candidate in candidates[1:]:
            if not looks_like_toc_entry(candidate.title, candidate.content, schema):
                break
            toc_candidates.append(candidate)
            consumed += 1
        merged = HeadingCandidate(
            markdown_level=1,
            title=toc_candidates[0].title,
            content="\n\n".join(candidate.content for candidate in toc_candidates if candidate.content),
            start_line=toc_candidates[0].start_line,
            end_line=toc_candidates[-1].end_line,
        )
        return merged, consumed

    def _should_lock_body(
        self,
        heading: HeadingInfo,
        lookahead_infos: list[HeadingInfo],
        document_mode: str,
        current_area: DocArea,
    ) -> bool:
        if heading.family == "chapter_cn":
            return True
        if heading.family == "arabic_main" and heading.title_norm in BODY_START_KEYWORDS:
            return True
        if heading.family == "cn_main":
            return any(info.family in {"cn_paren", "arabic_main", "decimal_path"} for info in lookahead_infos)
        if heading.family in {"decimal_path", "arabic_main"}:
            if any(self._is_compatible_followup(heading, info) for info in lookahead_infos):
                return True
        if heading.family == "plain" and heading.title_norm in BODY_START_KEYWORDS:
            if any(info.family in {"plain", "arabic_main", "decimal_path", "cn_main", "chapter_cn"} for info in lookahead_infos):
                return True
            return current_area in {
                DocArea.START,
                DocArea.TITLE_BLOCK,
                DocArea.DECLARATION,
                DocArea.AUTHORIZATION,
                DocArea.ABSTRACT_CN,
                DocArea.ABSTRACT_EN,
                DocArea.TOC,
                DocArea.PRE_BODY_MISC,
            }
        if document_mode == "journal_like" and heading.family == "arabic_main":
            return True
        return False

    def _is_body_candidate(self, heading: HeadingInfo) -> bool:
        if heading.family == "plain":
            return heading.title_norm in BODY_START_KEYWORDS
        return heading.family in {"chapter_cn", "arabic_main", "decimal_path", "cn_main"}

    def _is_compatible_followup(self, current: HeadingInfo, next_info: HeadingInfo) -> bool:
        if current.family == "arabic_main":
            return next_info.family in {"arabic_paren", "decimal_path"}
        if current.family == "decimal_path":
            if next_info.family == "decimal_path" and len(next_info.numbering_path) > len(current.numbering_path):
                return next_info.numbering_path[:-1] == current.numbering_path
            return next_info.family in {"arabic_paren", "cn_paren"}
        return False

    def _promote_pending_to_body(self, records: list[CandidateRecord], pending_indices: list[int]) -> None:
        for index in pending_indices:
            record = records[index]
            record.role = "body"
            record.area = DocArea.BODY_LOCKED
            record.accepted_as_body = True
            record.state_after = DocArea.BODY_LOCKED
            record.reason_codes.append("promoted_from_pending")
            if record.heading.family == "plain" and "plain_heading" not in record.evidence:
                record.evidence.append("plain_heading")
        pending_indices.clear()

    def _finalize_pending_as_front_matter(
        self,
        records: list[CandidateRecord],
        pending_indices: list[int],
        body_start_rejections: list[dict],
        reason: str,
    ) -> None:
        for index in pending_indices:
            self._demote_record_to_front_matter(records[index], body_start_rejections, reason)
        pending_indices.clear()

    def _demote_record_to_front_matter(self, record: CandidateRecord, body_start_rejections: list[dict], reason: str) -> None:
        if record.role != "pending_body":
            return
        record.role = "front_matter"
        record.semantic_level = 1
        record.area = record.state_after
        record.accepted_as_body = False
        record.reason_codes.append(reason)
        record.confidence = max(record.confidence, 0.62)
        if record.heading.is_numbered or record.title_norm in BODY_START_KEYWORDS:
            body_start_rejections.append(
                {
                    "node_id": record.id,
                    "title_raw": record.title_raw,
                    "title_norm": record.title_norm,
                    "family": record.heading.family,
                    "reason": reason,
                }
            )

    def _tail_area_for_title(self, title: str) -> DocArea:
        if is_reference_title(title):
            return DocArea.REFERENCES
        if is_appendix_title(title):
            return DocArea.APPENDIX
        if is_ack_title(title):
            return DocArea.ACK
        if is_author_bio_title(title):
            return DocArea.REFERENCES
        return DocArea.BODY_LOCKED

    def _role_for_area(self, area: DocArea) -> str:
        if area == DocArea.REFERENCES:
            return "references"
        if area == DocArea.APPENDIX:
            return "appendix"
        if area == DocArea.ACK:
            return "ack"
        if area == DocArea.TOC:
            return "toc"
        if area == DocArea.BODY_LOCKED:
            return "body"
        return "front_matter"

    def _assign_parent_ids(self, nodes: list[SectionNode]) -> None:
        """Assign parent_id to all children recursively."""
        for node in nodes:
            for child in node.children:
                child.parent_id = node.id
                self._assign_parent_ids([child])

    def _assign_ordinals(self, nodes: list[SectionNode]) -> None:
        """Assign ordinal numbers based on actual sibling order under the same parent."""
        for ordinal, node in enumerate(nodes, start=1):
            node.ordinal = ordinal
            if node.children:
                self._assign_ordinals(node.children)

    def _assign_node_types(self, nodes: list[SectionNode]) -> None:
        """Assign node_type based on role."""
        for node in nodes:
            if node.role in ("front_matter", "toc", "references", "appendix", "ack"):
                node.node_type = "special"
            elif node.children:
                node.node_type = "container"
            else:
                node.node_type = "section"
        for node in nodes:
            self._assign_node_types(node.children)

    def _detect_low_confidence_nodes(self, nodes: list[SectionNode]) -> None:
        """Mark low-confidence nodes from thresholds and rule-level risk evidence."""
        flat = self._flatten(nodes)
        for node in flat:
            threshold_triggered = node.confidence < LOW_CONFIDENCE_THRESHOLD
            risk_triggered = any(marker in LOW_CONFIDENCE_EVIDENCE for marker in node.evidence)
            node.is_low_confidence = threshold_triggered or risk_triggered
            if threshold_triggered and "low_confidence_threshold" not in node.evidence:
                node.evidence.append("low_confidence_threshold")
            if node.is_low_confidence and not node.evidence:
                node.evidence.append("low_confidence_threshold")

    def _build_stats(self, nodes: list[SectionNode], classified: ClassificationResult) -> dict:
        flat = self._flatten(nodes)
        role_counts = Counter(node.role for node in flat)
        max_depth = max((node.semantic_level for node in flat if node.role == "body"), default=1)
        body_nodes = [node for node in flat if node.role == "body"]
        toc_nodes = [node for node in flat if node.role == "toc"]
        first_body = next((node for node in flat if node.role == "body"), None)
        warnings = list(classified.warnings)
        if any("profile_fallback" in node.evidence for node in flat) and "profile_fallback_used" not in warnings:
            warnings.append("profile_fallback_used")

        area_trace = [
            {
                "index": index,
                "title_raw": record.title_raw,
                "title_norm": record.title_norm,
                "family": record.heading.family,
                "state_before": record.state_before.value,
                "state_after": record.state_after.value,
                "final_role": "front_matter" if record.role == "pending_body" else record.role,
                "accepted_as_body": record.accepted_as_body,
                "reason_codes": record.reason_codes,
            }
            for index, record in enumerate(classified.records)
        ]

        pre_body_node_count = 0
        for record in classified.records:
            if record.role != "body":
                pre_body_node_count += 1
                continue
            break

        return {
            "total_nodes": len(flat),
            "role_counts": dict(role_counts),
            "max_semantic_depth": max_depth,
            "body_node_count": len(body_nodes),
            "toc_node_count": len(toc_nodes),
            "_diagnostics_context": {
                "document_mode": classified.document_mode,
                "numbering_profile": classified.final_profile,
                "heading_family_counts": classified.heading_family_counts,
                "area_trace": area_trace,
                "body_start_rejections": classified.body_start_rejections,
                "first_body_node_id": first_body.id if first_body else None,
                "first_body_title": first_body.title_raw if first_body else None,
                "body_locked": classified.body_locked,
                "pre_body_node_count": pre_body_node_count,
                "front_matter_after_body_count": classified.front_matter_after_body_count,
                "tail_special_after_body_count": classified.tail_special_after_body_count,
                "warnings": warnings,
            },
        }

    def _flatten(self, nodes: list[SectionNode]) -> list[SectionNode]:
        flat: list[SectionNode] = []
        for node in nodes:
            flat.append(node)
            flat.extend(self._flatten(node.children))
        return flat


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
