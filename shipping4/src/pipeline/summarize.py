"""Summary artifact pipeline."""

from __future__ import annotations

from typing import Any

from src.clients.llm import LLMClient
from src.core.models import DocumentStructure, RunRecord, SectionNode, utcnow_iso
from src.parsing.markdown import extract_text_from_ref
from src.workspace import WorkspaceRepository

SUMMARY_VERSION = "2.1"
STRUCTURE_SOURCE_PATH = "structure/structure.json"
SPECIAL_ROLE_SUMMARIES = {
    "toc": "Table of contents index for the document.",
    "references": "Reference list for the paper.",
    "ack": "Acknowledgements and credits.",
    "appendix": "Appendix or supplementary material.",
}
BACK_MATTER_ROLES = {"toc", "references", "appendix", "ack"}


class GenerateSummaryUseCase:
    """Generate progressive-disclosure summary artifacts from structure.json."""

    def __init__(self, repository: WorkspaceRepository, llm_client: LLMClient) -> None:
        self.repository = repository
        self.llm_client = llm_client

    def execute(self, paper_id: str, force: bool = False) -> dict[str, Any]:
        if not force:
            cached_bundle = self._load_cached_bundle(paper_id)
            if cached_bundle is not None:
                self._persist_run_record(paper_id, self.repository.load_structure(paper_id))
                return cached_bundle

        structure = self.repository.load_structure(paper_id)
        markdown_content = self.repository.load_normalized_markdown(paper_id)
        body_roots = [node for node in structure.nodes if node.role == "body"]
        supporting_roots = [node for node in structure.nodes if node.role != "body"]
        tree = [self._summarize_node(node, markdown_content) for node in body_roots]
        supporting_tree = [self._summarize_node(node, markdown_content) for node in supporting_roots]
        overview = self._build_overview(structure, tree, supporting_tree)
        review_notes = self._build_review_notes(structure, tree, supporting_tree)

        summary_tree = {
            "paper_id": structure.paper_id,
            "summary_version": SUMMARY_VERSION,
            "generated_at": utcnow_iso(),
            "source_structure": STRUCTURE_SOURCE_PATH,
            "detected_schema": structure.detected_schema,
            "language": structure.language,
            "node_count": structure.stats.get("total_nodes", 0),
            "tree": tree,
        }
        overview["summary_version"] = SUMMARY_VERSION
        review_notes["summary_version"] = SUMMARY_VERSION

        summary_path = self.repository.save_summary_tree(paper_id, summary_tree)
        overview_path = self.repository.save_overview(paper_id, overview)
        review_notes_path = self.repository.save_review_notes(paper_id, review_notes)

        self._persist_run_record(
            paper_id,
            structure,
            summary_path=str(summary_path),
            overview_path=str(overview_path),
            review_notes_path=str(review_notes_path),
        )

        return {
            **summary_tree,
            "overview": overview,
            "review_notes": review_notes,
        }

    def _persist_run_record(
        self,
        paper_id: str,
        structure: DocumentStructure,
        summary_path: str | None = None,
        overview_path: str | None = None,
        review_notes_path: str | None = None,
    ) -> None:
        workspace = self.repository.get_workspace(paper_id)
        run_record = self.repository.load_run_record(paper_id) or RunRecord(
            paper_id=paper_id,
            source_type=structure.source_type,
            source=paper_id,
            status="summarized",
        )
        run_record.status = "summarized"
        run_record.artifacts["summary_tree"] = summary_path or str(workspace.summaries_dir / "summary_tree.json")
        run_record.artifacts["overview"] = overview_path or str(workspace.summaries_dir / "overview.json")
        run_record.artifacts["review_notes"] = review_notes_path or str(workspace.summaries_dir / "review_notes.json")
        run_record.artifacts["summary_stage"] = "phase4_complete"
        run_record.touch("summarized")
        self.repository.save_run_record(paper_id, run_record)

    def _load_cached_bundle(self, paper_id: str) -> dict[str, Any] | None:
        try:
            summary_tree = self.repository.load_summary_tree(paper_id)
            overview = self.repository.load_overview(paper_id)
            review_notes = self.repository.load_review_notes(paper_id)
        except FileNotFoundError:
            return None

        payloads = (summary_tree, overview, review_notes)
        if any(payload.get("summary_version") != SUMMARY_VERSION for payload in payloads):
            return None

        return {
            **summary_tree,
            "overview": overview,
            "review_notes": review_notes,
        }

    def _summarize_node(self, node: SectionNode, markdown_content: str) -> dict[str, Any]:
        children = [self._summarize_node(child, markdown_content) for child in node.children]
        summary = self._summary_for_node(node, markdown_content)
        return {
            "id": node.id,
            "title": node.title_raw,
            "title_norm": node.title_norm,
            "role": node.role,
            "semantic_level": node.semantic_level,
            "ordinal": node.ordinal,
            "numbering_path": list(node.numbering_path),
            "parent_id": node.parent_id,
            "is_low_confidence": node.is_low_confidence,
            "evidence": list(node.evidence),
            "content_ref": node.content_ref,
            "child_count": len(children),
            "summary": summary,
            "children": children,
        }

    def _summary_for_node(self, node: SectionNode, markdown_content: str) -> str:
        if node.role in SPECIAL_ROLE_SUMMARIES:
            return SPECIAL_ROLE_SUMMARIES[node.role]

        content = extract_text_from_ref(markdown_content, node.content_ref)
        if node.role == "front_matter":
            return self.llm_client.summarize(node.title_raw, content, max_words=70)

        max_words = self._max_words_for_body_node(node)
        return self.llm_client.summarize(node.title_raw, content, max_words=max_words)

    def _max_words_for_body_node(self, node: SectionNode) -> int:
        if node.semantic_level <= 1 and node.children:
            return 140
        if node.semantic_level <= 1:
            return 120
        if node.semantic_level == 2:
            return 90
        return 70

    def _build_overview(
        self,
        structure: DocumentStructure,
        tree: list[dict[str, Any]],
        supporting_tree: list[dict[str, Any]],
    ) -> dict[str, Any]:
        body_roots = [node for node in tree if node["role"] == "body"]
        special_roots = supporting_tree
        flat_tree = self._flatten_tree(tree + supporting_tree)
        quick_take = self._build_quick_take(body_roots, special_roots)

        return {
            "paper_id": structure.paper_id,
            "generated_at": utcnow_iso(),
            "source_structure": STRUCTURE_SOURCE_PATH,
            "detected_schema": structure.detected_schema,
            "language": structure.language,
            "stats": structure.stats,
            "quick_take": quick_take,
            "body_sections": [self._overview_entry(node) for node in body_roots],
            "supporting_sections": [self._overview_entry(node) for node in special_roots],
            "low_confidence_watchlist": [self._watchlist_entry(node) for node in flat_tree if node["is_low_confidence"]],
        }

    def _build_quick_take(self, body_roots: list[dict[str, Any]], special_roots: list[dict[str, Any]]) -> str:
        quick_take_lines = [f"{node['title']}: {node['summary']}" for node in body_roots[:6]]
        if not quick_take_lines:
            quick_take_lines = [f"{node['title']}: {node['summary']}" for node in special_roots[:3]]
        if not quick_take_lines:
            return "No summary content available."
        return self.llm_client.summarize("Document overview", "\n".join(quick_take_lines), max_words=220)

    def _overview_entry(self, node: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": node["id"],
            "title": node["title"],
            "role": node["role"],
            "semantic_level": node["semantic_level"],
            "summary": node["summary"],
            "child_count": node["child_count"],
            "low_confidence_descendants": self._count_low_confidence_descendants(node),
        }

    def _build_review_notes(
        self,
        structure: DocumentStructure,
        tree: list[dict[str, Any]],
        supporting_tree: list[dict[str, Any]],
    ) -> dict[str, Any]:
        body_roots = [node for node in tree if node["role"] == "body"]
        front_matter_roots = [node for node in supporting_tree if node["role"] == "front_matter"]
        back_matter_roots = [node for node in supporting_tree if node["role"] in BACK_MATTER_ROLES]
        flat_tree = self._flatten_tree(tree + supporting_tree)

        return {
            "paper_id": structure.paper_id,
            "generated_at": utcnow_iso(),
            "source_structure": STRUCTURE_SOURCE_PATH,
            "front_matter_notes": [self._compact_note(node) for node in front_matter_roots],
            "body_section_notes": [self._body_section_note(node) for node in body_roots],
            "back_matter_notes": [self._compact_note(node) for node in back_matter_roots],
            "low_confidence_watchlist": [self._watchlist_entry(node) for node in flat_tree if node["is_low_confidence"]],
        }

    def _body_section_note(self, node: dict[str, Any]) -> dict[str, Any]:
        subsection_notes = [self._compact_note(child) for child in node["children"][:5]]
        return {
            "section_id": node["id"],
            "title": node["title"],
            "summary": node["summary"],
            "semantic_level": node["semantic_level"],
            "child_count": node["child_count"],
            "subsection_notes": subsection_notes,
            "is_low_confidence": node["is_low_confidence"],
            "evidence": list(node["evidence"]),
        }

    def _compact_note(self, node: dict[str, Any]) -> dict[str, Any]:
        return {
            "section_id": node["id"],
            "title": node["title"],
            "role": node["role"],
            "summary": node["summary"],
            "semantic_level": node["semantic_level"],
            "is_low_confidence": node["is_low_confidence"],
        }

    def _watchlist_entry(self, node: dict[str, Any]) -> dict[str, Any]:
        return {
            "section_id": node["id"],
            "title": node["title"],
            "role": node["role"],
            "semantic_level": node["semantic_level"],
            "summary": node["summary"],
            "evidence": list(node["evidence"]),
        }

    def _count_low_confidence_descendants(self, node: dict[str, Any]) -> int:
        total = 1 if node["is_low_confidence"] else 0
        for child in node["children"]:
            total += self._count_low_confidence_descendants(child)
        return total

    def _flatten_tree(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        flat: list[dict[str, Any]] = []
        for node in nodes:
            flat.append(node)
            flat.extend(self._flatten_tree(node["children"]))
        return flat
