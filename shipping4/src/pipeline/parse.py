"""Parse pipeline."""

from __future__ import annotations

import logging
from pathlib import Path

from src.clients.llm import LLMClient
from src.core.models import RunRecord
from src.parsing import DocumentStructureBuilder, normalize_markdown, parse_markdown_headings
from src.parsing.refinement import RefinementEngine
from src.workspace import WorkspaceRepository

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when parsing fails validation."""


class ParseStructureUseCase:
    """Build structure.json and derived section markdown files."""

    def __init__(
        self,
        repository: WorkspaceRepository,
        builder: DocumentStructureBuilder | None = None,
        llm_client: LLMClient | None = None,
        refinement_enabled: bool = True,
    ) -> None:
        self.repository = repository
        self.builder = builder or DocumentStructureBuilder()
        self.llm_client = llm_client
        self.refinement_enabled = refinement_enabled

    def execute(self, paper_id: str, markdown_content: str | None = None, source_type: str = "workspace") -> tuple[Path, list[Path]]:
        content = normalize_markdown(markdown_content) if markdown_content is not None else self.repository.load_normalized_markdown(paper_id)

        candidates = parse_markdown_headings(content)
        if not candidates:
            raise ParseError(f"No headings found in document for paper '{paper_id}'")

        structure = self.builder.build(paper_id=paper_id, markdown_content=content, source_type=source_type)

        # Run LLM refinement if enabled and low-confidence nodes exist
        warnings: list[str] = []
        refinement_record = None
        refinement_attempted = False
        refinement_ran = False
        refinement_warning: str | None = None
        if self.refinement_enabled and self.llm_client is not None and structure.low_confidence_node_ids:
            refinement_attempted = True
            engine = RefinementEngine(self.llm_client)
            try:
                structure, refinement_record = engine.refine(structure, enabled=True)
                self.repository.save_refinement_record(paper_id, refinement_record)
                refinement_ran = True
            except Exception as exc:
                logger.warning("LLM refinement failed for paper %s, continuing with rule-based structure: %s", paper_id, exc)
                refinement_warning = f"LLM refinement failed; used rule-based structure: {exc}"
                warnings.append("refinement_failed_fallback")
                warnings.append(refinement_warning)
                structure.stats["_refinement_warning"] = refinement_warning

        body_nodes = [n for n in self._flatten_nodes(structure.nodes) if n.role == "body"]
        if not body_nodes:
            warnings.append("body_start_missing")
            structure.stats["_parse_warning"] = "body_start_missing"

        # Update low_confidence_node_ids after refinement
        structure.low_confidence_node_ids = [
            n.id for n in self._flatten_nodes(structure.nodes) if n.is_low_confidence
        ]

        schema_evidence = self._build_schema_evidence(structure)
        diagnostics = self.builder.build_diagnostics(structure, schema_evidence, warnings)
        outline = self.builder.build_outline(structure)

        structure_path = self.repository.save_structure(paper_id, structure)
        diagnostics_path = self.repository.save_diagnostics(paper_id, diagnostics)
        outline_path = self.repository.save_outline(paper_id, outline)
        sections = self.builder.export_sections(structure, content, self.repository)
        section_paths = self.repository.save_sections(paper_id, sections)

        run_record = self.repository.load_run_record(paper_id) or RunRecord(
            paper_id=paper_id,
            source_type=source_type,
            source=paper_id,
            status="parsed",
        )
        run_record.status = "parsed"
        run_record.artifacts["structure"] = str(structure_path)
        run_record.artifacts["diagnostics"] = str(diagnostics_path)
        run_record.artifacts["outline"] = str(outline_path)
        run_record.artifacts["sections_dir"] = str(self.repository.get_workspace(paper_id).sections_dir)
        run_record.artifacts.pop("refinement", None)
        run_record.artifacts.pop("refinement_status", None)
        run_record.artifacts.pop("refinement_warning", None)
        if refinement_ran:
            run_record.artifacts["parse_stage"] = "phase3_complete"
            run_record.artifacts["refinement_status"] = "completed"
        elif refinement_attempted:
            run_record.artifacts["parse_stage"] = "phase3_fallback"
            run_record.artifacts["refinement_status"] = "failed_fallback"
        else:
            run_record.artifacts["parse_stage"] = "phase2_complete"
        if refinement_warning:
            run_record.artifacts["refinement_warning"] = refinement_warning
        if refinement_record:
            run_record.artifacts["refinement"] = str(self.repository.get_workspace(paper_id).structure_dir / "refinement.json")
        run_record.touch("parsed")
        self.repository.save_run_record(paper_id, run_record)
        return structure_path, section_paths

    def _flatten_nodes(self, nodes: list) -> list:
        flat = []
        for node in nodes:
            flat.append(node)
            flat.extend(self._flatten_nodes(node.children))
        return flat

    def _build_schema_evidence(self, structure) -> list[str]:
        """Build evidence list describing the detected schema."""
        evidence = [f"detected_schema={structure.detected_schema}"]
        stats = structure.stats
        if "role_counts" in stats:
            for role, count in stats["role_counts"].items():
                evidence.append(f"{role}={count}")
        evidence.append(f"total_nodes={stats.get('total_nodes', 0)}")
        return evidence
