from pathlib import Path

from src.core.models import RawConversionResult
from src.parsing import DocumentStructureBuilder
from src.pipeline import ConvertPaperUseCase, GenerateSummaryUseCase, ParseStructureUseCase


class StubConversionClient:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown

    def convert_file(self, pdf_path: Path) -> RawConversionResult:
        return RawConversionResult(markdown=self.markdown, raw_files={"full.md": self.markdown.encode("utf-8")})

    def convert_url(self, url: str) -> RawConversionResult:
        return RawConversionResult(markdown=self.markdown, raw_files={"full.md": self.markdown.encode("utf-8")})

    def health_check(self) -> bool:
        return True


class StubLLMClient:
    def summarize(self, title: str, content: str, max_words: int = 150) -> str:
        return title

    def health_check(self) -> bool:
        return True


def test_convert_parse_summarize_pipeline(repository, thesis_markdown):
    pdf_path = repository.papers_dir / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nstub pdf")

    parse_use_case = ParseStructureUseCase(repository=repository, builder=DocumentStructureBuilder())
    convert_use_case = ConvertPaperUseCase(
        repository=repository,
        conversion_client=StubConversionClient(thesis_markdown),
        parse_use_case=parse_use_case,
    )
    summarize_use_case = GenerateSummaryUseCase(repository=repository, llm_client=StubLLMClient())

    convert_use_case.execute_local(pdf_path, parse_after_convert=True)
    summary = summarize_use_case.execute("sample")

    workspace = repository.get_workspace("sample")
    assert (workspace.raw_dir / "full.md").exists()
    assert (workspace.normalized_dir / "document.md").exists()
    assert (workspace.structure_dir / "structure.json").exists()
    assert (workspace.summaries_dir / "summary_tree.json").exists()
    assert (workspace.summaries_dir / "overview.json").exists()
    assert (workspace.summaries_dir / "review_notes.json").exists()
    assert summary["paper_id"] == "sample"
    assert summary["summary_version"] == "2.1"
    assert "overview" in summary
    assert "review_notes" in summary
    assert len(summary["tree"]) == 2

    run_record = repository.load_run_record("sample")
    assert run_record is not None
    assert run_record.artifacts["summary_stage"] == "phase4_complete"


def test_parse_produces_phase2_diagnostics_fields(repository, thesis_markdown):
    pdf_path = repository.papers_dir / "phase2-sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nstub pdf")

    parse_use_case = ParseStructureUseCase(repository=repository, builder=DocumentStructureBuilder())
    convert_use_case = ConvertPaperUseCase(
        repository=repository,
        conversion_client=StubConversionClient(thesis_markdown),
        parse_use_case=parse_use_case,
    )
    convert_use_case.execute_local(pdf_path, parse_after_convert=True)

    workspace = repository.get_workspace("phase2-sample")
    assert (workspace.structure_dir / "diagnostics.json").exists()
    assert (workspace.structure_dir / "outline.json").exists()

    diagnostics = repository.load_diagnostics("phase2-sample")
    assert diagnostics is not None
    assert diagnostics.paper_id == "phase2-sample"
    assert diagnostics.detected_schema == "chapter_decimal"
    assert diagnostics.numbering_profile == "chapter_decimal"
    assert diagnostics.body_locked is True
    assert diagnostics.area_trace
    assert diagnostics.first_body_title == "第一章 绪论"

    outline = repository.load_outline("phase2-sample")
    assert outline is not None
    assert len(outline) == 2
    assert all(node.parent_id is None for node in outline)

    structure = repository.load_structure("phase2-sample")
    assert isinstance(structure.low_confidence_node_ids, list)
    assert len(structure.nodes) == 2

    run_record = repository.load_run_record("phase2-sample")
    assert run_record is not None
    assert run_record.artifacts["parse_stage"] == "phase2_complete"


def test_parse_zero_intro_regression_has_no_body_start_missing(repository, journal_zero_intro_markdown):
    pdf_path = repository.papers_dir / "zero-intro.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nstub pdf")

    parse_use_case = ParseStructureUseCase(repository=repository, builder=DocumentStructureBuilder())
    convert_use_case = ConvertPaperUseCase(
        repository=repository,
        conversion_client=StubConversionClient(journal_zero_intro_markdown),
        parse_use_case=parse_use_case,
    )
    convert_use_case.execute_local(pdf_path, parse_after_convert=True)

    diagnostics = repository.load_diagnostics("zero-intro")
    assert diagnostics is not None
    assert diagnostics.first_body_title == "0 引言"
    assert "body_start_missing" not in diagnostics.warnings


def test_summary_tree_uses_body_chapters_only(repository, thesis_markdown):
    pdf_path = repository.papers_dir / "summary-body-only.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nstub pdf")

    parse_use_case = ParseStructureUseCase(repository=repository, builder=DocumentStructureBuilder())
    convert_use_case = ConvertPaperUseCase(
        repository=repository,
        conversion_client=StubConversionClient(thesis_markdown),
        parse_use_case=parse_use_case,
    )
    summarize_use_case = GenerateSummaryUseCase(repository=repository, llm_client=StubLLMClient())

    convert_use_case.execute_local(pdf_path, parse_after_convert=True)
    summary = summarize_use_case.execute("summary-body-only", force=True)

    assert [node["title"] for node in summary["tree"]] == ["第一章 绪论", "第二章 方法设计"]
    assert summary["overview"]["supporting_sections"] == []


def test_summarize_does_not_depend_on_exported_sections(repository, thesis_markdown):
    pdf_path = repository.papers_dir / "summary-no-sections.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nstub pdf")

    parse_use_case = ParseStructureUseCase(repository=repository, builder=DocumentStructureBuilder())
    convert_use_case = ConvertPaperUseCase(
        repository=repository,
        conversion_client=StubConversionClient(thesis_markdown),
        parse_use_case=parse_use_case,
    )
    summarize_use_case = GenerateSummaryUseCase(repository=repository, llm_client=StubLLMClient())

    convert_use_case.execute_local(pdf_path, parse_after_convert=True)
    workspace = repository.get_workspace("summary-no-sections")
    for section_file in workspace.sections_dir.glob("*.md"):
        section_file.unlink()
    workspace.sections_dir.rmdir()

    summary = summarize_use_case.execute("summary-no-sections", force=True)

    assert summary["paper_id"] == "summary-no-sections"
    assert (workspace.summaries_dir / "summary_tree.json").exists()
    assert (workspace.summaries_dir / "overview.json").exists()
    assert (workspace.summaries_dir / "review_notes.json").exists()
    assert workspace.sections_dir.exists()
    assert list(workspace.sections_dir.iterdir()) == []
