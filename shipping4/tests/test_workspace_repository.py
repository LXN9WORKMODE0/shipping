from __future__ import annotations

from src.core.models import DiagnosticsReport, DocumentStructure, ExportedSection, LowConfidenceNode, OutlineNode, RunRecord, SectionNode


def test_workspace_repository_prepares_expected_layout(repository):
    workspace = repository.prepare_workspace("sample-paper")
    assert workspace.root.exists()
    assert workspace.raw_dir.exists()
    assert workspace.normalized_dir.exists()
    assert workspace.structure_dir.exists()
    assert workspace.sections_dir.exists()
    assert workspace.summaries_dir.exists()


def test_workspace_repository_round_trips_run_record_and_structure(repository):
    record = RunRecord(paper_id="sample-paper", source_type="local_pdf", source="papers/a.pdf", status="parsed")
    repository.save_run_record("sample-paper", record)
    loaded_record = repository.load_run_record("sample-paper")
    assert loaded_record is not None
    assert loaded_record.status == "parsed"

    child_node = SectionNode(
        id="node_002",
        title_raw="1.1 研究背景",
        title_norm="研究背景",
        role="body",
        semantic_level=2,
        numbering_path=[1, 1],
        confidence=0.95,
        content_ref={"path": "normalized/document.md", "start_line": 3, "end_line": 5},
        parent_id="node_001",
        node_type="section",
        ordinal=2,
        is_low_confidence=False,
        evidence=["schema_conflict"],
        llm_reviewed=False,
    )
    parent_node = SectionNode(
        id="node_001",
        title_raw="第一章 绪论",
        title_norm="绪论",
        role="body",
        semantic_level=1,
        numbering_path=[1],
        confidence=0.98,
        content_ref={"path": "normalized/document.md", "start_line": 1, "end_line": 5},
        children=[child_node],
        parent_id=None,
        node_type="section",
        ordinal=1,
        is_low_confidence=False,
        evidence=[],
        llm_reviewed=False,
    )
    child_node.parent_id = parent_node.id

    structure = DocumentStructure(
        paper_id="sample-paper",
        source_type="local_pdf",
        language="zh",
        detected_schema="chapter_decimal",
        stats={"total_nodes": 2, "role_counts": {"body": 2}},
        nodes=[parent_node],
        diagnostics_version="1.0",
        low_confidence_node_ids=[],
    )
    repository.save_structure("sample-paper", structure)
    loaded_structure = repository.load_structure("sample-paper")
    assert loaded_structure.paper_id == "sample-paper"
    assert loaded_structure.nodes[0].title_norm == "绪论"
    assert loaded_structure.nodes[0].parent_id is None
    assert loaded_structure.nodes[0].node_type == "section"
    assert loaded_structure.nodes[0].ordinal == 1
    assert loaded_structure.diagnostics_version == "1.0"
    assert loaded_structure.low_confidence_node_ids == []
    assert loaded_structure.nodes[0].children[0].parent_id == "node_001"
    assert loaded_structure.nodes[0].children[0].evidence == ["schema_conflict"]


def test_workspace_repository_diagnostics_round_trip(repository):
    diagnostics = DiagnosticsReport(
        paper_id="sample-paper",
        detected_schema="chapter_decimal",
        generated_at="2026-04-13T00:00:00+00:00",
        low_confidence_nodes=[
            LowConfidenceNode(
                node_id="node_005",
                title_raw="研究",
                role="body",
                semantic_level=1,
                confidence=0.5,
                evidence=["plain_heading", "low_confidence_threshold"],
            )
        ],
        schema_evidence=["detected_schema=chapter_decimal", "body=5"],
        warnings=["level_jump detected"],
        document_mode="thesis_like",
        numbering_profile="chapter_decimal",
        heading_family_counts={"chapter_cn": 1, "decimal_path": 1},
        area_trace=[{"index": 0, "title_raw": "第一章 绪论", "final_role": "body"}],
        body_start_rejections=[],
        first_body_node_id="node_001",
        first_body_title="第一章 绪论",
        body_locked=True,
        pre_body_node_count=3,
        front_matter_after_body_count=0,
        tail_special_after_body_count=2,
    )
    repository.save_diagnostics("sample-paper", diagnostics)
    loaded = repository.load_diagnostics("sample-paper")
    assert loaded is not None
    assert loaded.paper_id == "sample-paper"
    assert loaded.detected_schema == "chapter_decimal"
    assert len(loaded.low_confidence_nodes) == 1
    assert loaded.low_confidence_nodes[0].node_id == "node_005"
    assert loaded.low_confidence_nodes[0].evidence == ["plain_heading", "low_confidence_threshold"]
    assert "level_jump detected" in loaded.warnings
    assert loaded.document_mode == "thesis_like"
    assert loaded.numbering_profile == "chapter_decimal"
    assert loaded.heading_family_counts["chapter_cn"] == 1
    assert loaded.first_body_title == "第一章 绪论"
    assert loaded.body_locked is True


def test_workspace_repository_outline_round_trip(repository):
    outline = [
        OutlineNode(id="node_001", title="绪论", role="body", semantic_level=1, ordinal=1, is_low_confidence=False, parent_id=None),
        OutlineNode(id="node_002", title="研究背景", role="body", semantic_level=2, ordinal=1, is_low_confidence=False, parent_id="node_001"),
        OutlineNode(id="node_003", title="参考文献", role="references", semantic_level=1, ordinal=2, is_low_confidence=False, parent_id=None),
    ]
    repository.save_outline("sample-paper", outline)
    loaded = repository.load_outline("sample-paper")
    assert loaded is not None
    assert len(loaded) == 3
    assert loaded[0].id == "node_001"
    assert loaded[0].parent_id is None
    assert loaded[1].parent_id == "node_001"
    assert loaded[2].role == "references"
    assert loaded[2].ordinal == 2


def test_workspace_repository_save_sections_removes_stale_markdown(repository):
    repository.save_sections(
        "sample-paper",
        [
            ExportedSection(node_id="old", title="旧章节", file_name="00_旧章节.md", content="old"),
            ExportedSection(node_id="keep", title="保留章节", file_name="01_保留章节.md", content="keep"),
        ],
    )

    repository.save_sections(
        "sample-paper",
        [
            ExportedSection(node_id="keep", title="保留章节", file_name="00_保留章节.md", content="keep"),
        ],
    )

    workspace = repository.get_workspace("sample-paper")
    assert sorted(path.name for path in workspace.sections_dir.glob("*.md")) == ["00_保留章节.md"]


def test_workspace_repository_summary_phase4_round_trip(repository):
    summary_tree = {
        "paper_id": "sample-paper",
        "summary_version": "2.0",
        "tree": [{"id": "node_001", "title": "Intro", "summary": "Summary"}],
    }
    overview = {
        "paper_id": "sample-paper",
        "summary_version": "2.0",
        "quick_take": "Quick take",
    }
    review_notes = {
        "paper_id": "sample-paper",
        "summary_version": "2.0",
        "body_section_notes": [{"section_id": "node_001", "title": "Intro"}],
    }

    repository.save_summary_tree("sample-paper", summary_tree)
    repository.save_overview("sample-paper", overview)
    repository.save_review_notes("sample-paper", review_notes)

    assert repository.load_summary_tree("sample-paper")["summary_version"] == "2.0"
    assert repository.load_overview("sample-paper")["quick_take"] == "Quick take"
    assert repository.load_review_notes("sample-paper")["body_section_notes"][0]["section_id"] == "node_001"
