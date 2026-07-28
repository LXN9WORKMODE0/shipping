"""Tests for LLM refinement of low-confidence structure nodes."""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from src.core.models import (
    DocumentStructure,
    RefinementRecord,
    RefinementSuggestion,
    SectionNode,
    utcnow_iso,
)
from src.parsing.refinement import RefinementEngine, _flatten_nodes, _merge_suggestions, _parse_suggestions
from src.parsing import DocumentStructureBuilder
from src.pipeline import ParseStructureUseCase


class StubLLMClient:
    """Stub LLM client for testing."""

    def __init__(self, suggestions: list[dict] | None = None, available: bool = True) -> None:
        self._suggestions = suggestions or []
        self._available = available
        self.refine_called_with: list | None = None

    @property
    def available(self) -> bool:
        return self._available

    def refine_low_confidence_nodes(self, paper_id, detected_schema, language, nodes):
        self.refine_called_with = {"paper_id": paper_id, "detected_schema": detected_schema, "language": language, "nodes": nodes}
        return self._suggestions


class BrokenLLMClient:
    """LLM client stub that fails during refinement."""

    def refine_low_confidence_nodes(self, paper_id, detected_schema, language, nodes):
        raise RuntimeError("boom")


def make_simple_structure(paper_id: str = "test-paper", low_conf_ids: list[str] | None = None) -> DocumentStructure:
    """Create a minimal document structure for testing."""
    child = SectionNode(
        id="node_001",
        title_raw="1. Introduction",
        title_norm="Introduction",
        role="body",
        semantic_level=1,
        numbering_path=[1],
        confidence=0.5,
        content_ref={"path": "normalized/document.md", "start_line": 1, "end_line": 3},
        children=[],
        parent_id=None,
        node_type="section",
        ordinal=1,
        is_low_confidence=True,
        evidence=["low_confidence_threshold"],
        llm_reviewed=False,
    )
    parent = SectionNode(
        id="node_000",
        title_raw="Chapter 1",
        title_norm="Chapter 1",
        role="body",
        semantic_level=0,
        numbering_path=[],
        confidence=0.95,
        content_ref={"path": "normalized/document.md", "start_line": 1, "end_line": 10},
        children=[child],
        parent_id=None,
        node_type="container",
        ordinal=1,
        is_low_confidence=False,
        evidence=[],
        llm_reviewed=False,
    )
    child.parent_id = parent.id
    low_conf = low_conf_ids if low_conf_ids is not None else ["node_001"]
    return DocumentStructure(
        paper_id=paper_id,
        source_type="test",
        language="en",
        detected_schema="standard_paper",
        nodes=[parent],
        stats={"total_nodes": 2, "role_counts": {"body": 2}},
        low_confidence_node_ids=low_conf,
    )


class TestRefinementModels:
    """Test RefinementSuggestion and RefinementRecord models."""

    def test_refinement_suggestion_to_dict(self):
        suggestion = RefinementSuggestion(
            node_id="node_001",
            role="body",
            semantic_level=2,
            confidence=0.9,
            evidence=["llm_suggested"],
            reason="Looks like a subsection",
        )
        d = suggestion.to_dict()
        assert d["node_id"] == "node_001"
        assert d["role"] == "body"
        assert d["semantic_level"] == 2
        assert d["confidence"] == 0.9
        assert d["evidence"] == ["llm_suggested"]
        assert d["reason"] == "Looks like a subsection"

    def test_refinement_suggestion_from_dict(self):
        data = {"node_id": "node_001", "role": "front_matter", "reason": "title page"}
        suggestion = RefinementSuggestion.from_dict(data)
        assert suggestion.node_id == "node_001"
        assert suggestion.role == "front_matter"
        assert suggestion.semantic_level is None
        assert suggestion.reason == "title page"

    def test_refinement_suggestion_applied_fields(self):
        suggestion = RefinementSuggestion(
            node_id="node_001",
            role="body",
            semantic_level=2,
            confidence=0.9,
        )
        fields = suggestion.applied_fields()
        assert fields == {"role": "body", "semantic_level": 2, "confidence": 0.9}

    def test_refinement_record_round_trip(self):
        record = RefinementRecord(
            paper_id="test-paper",
            generated_at="2026-04-13T00:00:00+00:00",
            input_node_ids=["node_001", "node_002"],
            applied_node_ids=["node_001"],
            skipped_node_ids=["node_002"],
            suggestions=[
                RefinementSuggestion(node_id="node_001", role="body", reason="ok"),
                RefinementSuggestion(node_id="node_002", reason="skipped"),
            ],
            skipped_details={"node_002": "unknown node_id"},
        )
        d = record.to_dict()
        loaded = RefinementRecord.from_dict(d)
        assert loaded.paper_id == "test-paper"
        assert loaded.input_node_ids == ["node_001", "node_002"]
        assert loaded.applied_node_ids == ["node_001"]
        assert loaded.skipped_node_ids == ["node_002"]
        assert loaded.skipped_details == {"node_002": "unknown node_id"}


class TestFlattenNodes:
    """Test _flatten_nodes helper."""

    def test_flatten_nodes(self):
        child = SectionNode(
            id="node_001", title_raw="A", title_norm="A", role="body", semantic_level=1,
            numbering_path=[1], confidence=0.9, content_ref=None, children=[], node_type="section", ordinal=1,
        )
        parent = SectionNode(
            id="node_000", title_raw="B", title_norm="B", role="body", semantic_level=0,
            numbering_path=[], confidence=0.9, content_ref=None, children=[child], node_type="container", ordinal=1,
        )
        flat = _flatten_nodes([parent])
        assert set(flat.keys()) == {"node_000", "node_001"}
        assert flat["node_001"].title_raw == "A"


class TestParseSuggestions:
    """Test _parse_suggestions helper."""

    def test_parses_valid_suggestions(self):
        record = RefinementRecord(paper_id="test")
        raw = [
            {"node_id": "node_001", "role": "body", "reason": "looks good"},
            {"node_id": "node_002", "semantic_level": 2, "reason": "level jump"},
        ]
        suggestions = _parse_suggestions(raw, record)
        assert len(suggestions) == 2
        assert suggestions[0].node_id == "node_001"
        assert suggestions[1].semantic_level == 2

    def test_skips_invalid_suggestions(self):
        record = RefinementRecord(paper_id="test")
        raw = [
            {"node_id": "node_001", "role": "body", "reason": "ok"},
            {"not_node_id": "node_002"},  # missing node_id
            "not a dict",
        ]
        suggestions = _parse_suggestions(raw, record)
        assert len(suggestions) == 1
        assert "not a dict" in record.skipped_details
        assert str({"not_node_id": "node_002"}) in record.skipped_details


class TestMergeSuggestions:
    """Test _merge_suggestions with various rules."""

    def _make_structure_with_nodes(self) -> tuple[DocumentStructure, dict[str, SectionNode]]:
        child = SectionNode(
            id="node_001", title_raw="Intro", title_norm="Intro", role="body",
            semantic_level=1, numbering_path=[1], confidence=0.5, content_ref=None,
            children=[], parent_id="node_000", node_type="section", ordinal=1,
            is_low_confidence=True, evidence=["low_confidence_threshold"], llm_reviewed=False,
        )
        parent = SectionNode(
            id="node_000", title_raw="Chapter 1", title_norm="Chapter 1", role="body",
            semantic_level=0, numbering_path=[], confidence=0.95, content_ref=None,
            children=[child], parent_id=None, node_type="container", ordinal=1,
            is_low_confidence=False, evidence=[], llm_reviewed=False,
        )
        child.parent_id = parent.id
        structure = DocumentStructure(
            paper_id="test",
            source_type="test",
            language="en",
            detected_schema="standard_paper",
            nodes=[parent],
            stats={"total_nodes": 2},
            low_confidence_node_ids=["node_001"],
        )
        return structure, {"node_000": parent, "node_001": child}

    def test_applies_valid_suggestions(self):
        structure, flat = self._make_structure_with_nodes()
        record = RefinementRecord(paper_id="test")
        suggestions = [RefinementSuggestion(node_id="node_001", role="body", semantic_level=2, confidence=0.9, reason="ok")]
        applied, skipped = _merge_suggestions(structure, suggestions, flat, record)

        assert applied == ["node_001"]
        assert skipped == []
        assert flat["node_001"].role == "body"
        assert flat["node_001"].semantic_level == 2
        assert flat["node_001"].confidence == 0.9
        assert flat["node_001"].llm_reviewed is True
        assert "llm_applied" in flat["node_001"].evidence

    def test_rejects_unknown_node_id(self):
        structure, flat = self._make_structure_with_nodes()
        record = RefinementRecord(paper_id="test")
        suggestions = [RefinementSuggestion(node_id="node_999", role="body", reason="unknown")]
        applied, skipped = _merge_suggestions(structure, suggestions, flat, record)

        assert applied == []
        assert skipped == ["node_999"]
        assert "unknown node_id" in record.skipped_details["node_999"]

    def test_rejects_forbidden_field_attempt(self):
        structure, flat = self._make_structure_with_nodes()
        record = RefinementRecord(paper_id="test")
        # Use from_dict to include forbidden fields that LLM should not have set
        suggestion = RefinementSuggestion.from_dict({
            "node_id": "node_001",
            "role": "body",
            "ordinal": 99,  # forbidden field
            "reason": "",
        })
        applied, skipped = _merge_suggestions(structure, [suggestion], flat, record)

        assert applied == []
        assert skipped == ["node_001"]
        assert "attempted forbidden fields" in record.skipped_details["node_001"]

    def test_rejects_semantic_level_inconsistency(self):
        structure, flat = self._make_structure_with_nodes()
        record = RefinementRecord(paper_id="test")
        # child has level 1, parent has level 0 - LLM suggests child at level 0 would break ordering
        suggestions = [RefinementSuggestion(node_id="node_001", semantic_level=0, reason="bad level")]
        applied, skipped = _merge_suggestions(structure, suggestions, flat, record)

        assert applied == []
        assert skipped == ["node_001"]
        assert "semantic level ordering" in record.skipped_details["node_001"]

    def test_rejects_role_change_that_requires_reparenting(self):
        structure, flat = self._make_structure_with_nodes()
        record = RefinementRecord(paper_id="test")
        suggestions = [RefinementSuggestion(node_id="node_001", role="front_matter", reason="title page")]
        applied, skipped = _merge_suggestions(structure, suggestions, flat, record)

        assert applied == []
        assert skipped == ["node_001"]
        assert "reparenting" in record.skipped_details["node_001"]
        assert flat["node_001"].role == "body"
        assert flat["node_001"].parent_id == "node_000"

    def test_refreshes_low_confidence_state_after_applied_suggestion(self):
        structure, flat = self._make_structure_with_nodes()
        record = RefinementRecord(paper_id="test")
        suggestions = [RefinementSuggestion(node_id="node_001", confidence=0.95, reason="fixed")]
        applied, skipped = _merge_suggestions(structure, suggestions, flat, record)

        assert applied == ["node_001"]
        assert skipped == []
        assert flat["node_001"].confidence == 0.95
        assert flat["node_001"].is_low_confidence is False
        assert "low_confidence_threshold" not in flat["node_001"].evidence
        assert structure.low_confidence_node_ids == []


class TestRefinementEngine:
    """Test RefinementEngine end-to-end."""

    def test_skips_when_no_low_confidence_nodes(self):
        client = StubLLMClient()
        engine = RefinementEngine(client)
        structure = make_simple_structure(low_conf_ids=[])
        structure, record = engine.refine(structure, enabled=True)

        assert client.refine_called_with is None
        assert record.input_node_ids == []
        assert record.applied_node_ids == []

    def test_skips_when_disabled(self):
        client = StubLLMClient(suggestions=[{"node_id": "node_001", "role": "body"}])
        engine = RefinementEngine(client)
        structure = make_simple_structure()
        structure, record = engine.refine(structure, enabled=False)

        assert client.refine_called_with is None
        assert record.applied_node_ids == []

    def test_calls_llm_with_only_low_confidence_nodes(self):
        suggestions = [{"node_id": "node_001", "role": "body", "confidence": 0.9, "reason": "ok"}]
        client = StubLLMClient(suggestions=suggestions)
        engine = RefinementEngine(client)
        structure = make_simple_structure()
        structure, record = engine.refine(structure, enabled=True)

        assert client.refine_called_with is not None
        assert client.refine_called_with["paper_id"] == "test-paper"
        # Only the low-confidence node should be sent
        assert len(client.refine_called_with["nodes"]) == 1
        assert client.refine_called_with["nodes"][0]["id"] == "node_001"

    def test_engine_does_not_crash_on_llm_empty_response(self):
        client = StubLLMClient(suggestions=[])
        engine = RefinementEngine(client)
        structure = make_simple_structure()
        structure, record = engine.refine(structure, enabled=True)

        assert record.input_node_ids == ["node_001"]
        assert record.applied_node_ids == []


class TestParseStructureUseCaseRefinement:
    """Test ParseStructureUseCase with refinement enabled."""

    def _make_thesis_markdown(self) -> str:
        return """---
title: Test Thesis
---

# 引言
Some content here.
# 研究设计
More content.
# 参考文献
"""

    def test_refinement_saves_refinement_json(self, repository):
        from src.core.models import RawConversionResult

        pdf_path = repository.papers_dir / "refinement-test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nstub")

        stub_conversion_client = type("Stub", (), {
            "convert_file": lambda self, p: RawConversionResult(markdown=self._md, raw_files={}),
            "health_check": lambda self: True,
            "_md": self._make_thesis_markdown(),
        })()

        llm_client = StubLLMClient(suggestions=[
            {"node_id": "node_000", "role": "body", "confidence": 0.9, "reason": "ok"}
        ])
        parse_use_case = ParseStructureUseCase(
            repository=repository,
            builder=DocumentStructureBuilder(),
            llm_client=llm_client,
            refinement_enabled=True,
        )

        from src.pipeline.convert import ConvertPaperUseCase
        convert_use_case = ConvertPaperUseCase(
            repository=repository,
            conversion_client=stub_conversion_client,
            parse_use_case=parse_use_case,
        )
        convert_use_case.execute_local(pdf_path, parse_after_convert=True)

        # Check refinement.json exists
        workspace = repository.get_workspace("refinement-test")
        refinement_path = workspace.structure_dir / "refinement.json"
        assert refinement_path.exists(), "refinement.json should be saved"

        record = repository.load_refinement_record("refinement-test")
        assert record is not None
        assert record.paper_id == "refinement-test"
        assert isinstance(record.suggestions, list)

    def test_refinement_disabled_does_not_call_llm(self, repository):
        from src.core.models import RawConversionResult

        pdf_path = repository.papers_dir / "no-refine-test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nstub")

        llm_client = StubLLMClient(suggestions=[{"node_id": "node_001", "role": "body"}])
        parse_use_case = ParseStructureUseCase(
            repository=repository,
            builder=DocumentStructureBuilder(),
            llm_client=llm_client,
            refinement_enabled=False,
        )

        stub_conversion_client = type("Stub", (), {
            "convert_file": lambda self, p: RawConversionResult(markdown=self._md, raw_files={}),
            "health_check": lambda self: True,
            "_md": self._make_thesis_markdown(),
        })()

        from src.pipeline.convert import ConvertPaperUseCase
        convert_use_case = ConvertPaperUseCase(
            repository=repository,
            conversion_client=stub_conversion_client,
            parse_use_case=parse_use_case,
        )
        convert_use_case.execute_local(pdf_path, parse_after_convert=True)

        assert llm_client.refine_called_with is None
        workspace = repository.get_workspace("no-refine-test")
        refinement_path = workspace.structure_dir / "refinement.json"
        assert not refinement_path.exists(), "refinement.json should NOT exist when disabled"

    def test_parse_stage_is_phase3_after_refinement(self, repository):
        from src.core.models import RawConversionResult

        pdf_path = repository.papers_dir / "phase3-test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nstub")

        llm_client = StubLLMClient(suggestions=[])
        parse_use_case = ParseStructureUseCase(
            repository=repository,
            builder=DocumentStructureBuilder(),
            llm_client=llm_client,
            refinement_enabled=True,
        )

        stub_conversion_client = type("Stub", (), {
            "convert_file": lambda self, p: RawConversionResult(markdown=self._md, raw_files={}),
            "health_check": lambda self: True,
            "_md": self._make_thesis_markdown(),
        })()

        from src.pipeline.convert import ConvertPaperUseCase
        convert_use_case = ConvertPaperUseCase(
            repository=repository,
            conversion_client=stub_conversion_client,
            parse_use_case=parse_use_case,
        )
        convert_use_case.execute_local(pdf_path, parse_after_convert=True)

        record = repository.load_run_record("phase3-test")
        assert record is not None
        assert record.artifacts["parse_stage"] == "phase3_complete"

    def test_refinement_failure_persists_fallback_warning(self, repository):
        from src.core.models import RawConversionResult

        pdf_path = repository.papers_dir / "phase3-fallback-test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nstub")

        parse_use_case = ParseStructureUseCase(
            repository=repository,
            builder=DocumentStructureBuilder(),
            llm_client=BrokenLLMClient(),
            refinement_enabled=True,
        )

        stub_conversion_client = type("Stub", (), {
            "convert_file": lambda self, p: RawConversionResult(markdown=self._md, raw_files={}),
            "health_check": lambda self: True,
            "_md": self._make_thesis_markdown(),
        })()

        from src.pipeline.convert import ConvertPaperUseCase
        convert_use_case = ConvertPaperUseCase(
            repository=repository,
            conversion_client=stub_conversion_client,
            parse_use_case=parse_use_case,
        )
        convert_use_case.execute_local(pdf_path, parse_after_convert=True)

        diagnostics = repository.load_diagnostics("phase3-fallback-test")
        assert diagnostics is not None
        assert any("LLM refinement failed; used rule-based structure: boom" in warning for warning in diagnostics.warnings)

        record = repository.load_run_record("phase3-fallback-test")
        assert record is not None
        assert record.artifacts["parse_stage"] == "phase3_fallback"
        assert record.artifacts["refinement_status"] == "failed_fallback"
        assert record.artifacts["refinement_warning"] == "LLM refinement failed; used rule-based structure: boom"
        assert "refinement" not in record.artifacts
        assert repository.load_refinement_record("phase3-fallback-test") is None

    def test_old_convert_parse_summarize_stub_flow_still_works(self, repository):
        from src.core.models import RawConversionResult
        from src.pipeline.summarize import GenerateSummaryUseCase

        pdf_path = repository.papers_dir / "stub-flow.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nstub")

        class StubSummarizeClient:
            def summarize(self, title, content, max_words=150):
                return title

        stub_conversion_client = type("Stub", (), {
            "convert_file": lambda self, p: RawConversionResult(markdown=self._md, raw_files={}),
            "health_check": lambda self: True,
            "_md": self._make_thesis_markdown(),
        })()

        parse_use_case = ParseStructureUseCase(
            repository=repository,
            builder=DocumentStructureBuilder(),
            llm_client=None,  # no LLM client
            refinement_enabled=False,
        )

        from src.pipeline.convert import ConvertPaperUseCase
        convert_use_case = ConvertPaperUseCase(
            repository=repository,
            conversion_client=stub_conversion_client,
            parse_use_case=parse_use_case,
        )
        summarize_use_case = GenerateSummaryUseCase(
            repository=repository,
            llm_client=StubSummarizeClient(),
        )

        convert_use_case.execute_local(pdf_path, parse_after_convert=True)
        summary = summarize_use_case.execute("stub-flow")

        workspace = repository.get_workspace("stub-flow")
        assert (workspace.raw_dir).exists()
        assert (workspace.normalized_dir / "document.md").exists()
        assert (workspace.structure_dir / "structure.json").exists()
        assert (workspace.summaries_dir / "summary_tree.json").exists()
        assert (workspace.summaries_dir / "overview.json").exists()
        assert (workspace.summaries_dir / "review_notes.json").exists()
        assert summary["paper_id"] == "stub-flow"
        assert summary["summary_version"] == "2.1"
