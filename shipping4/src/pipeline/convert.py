"""Conversion pipeline."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from src.clients.base import BaseConversionClient
from src.core.models import RunRecord
from src.parsing import normalize_markdown
from src.pipeline.parse import ParseStructureUseCase
from src.workspace import WorkspaceRepository


class ConvertPaperUseCase:
    """Convert PDF sources, persist raw artifacts, and optionally parse structure."""

    def __init__(
        self,
        repository: WorkspaceRepository,
        conversion_client: BaseConversionClient,
        parse_use_case: ParseStructureUseCase,
    ) -> None:
        self.repository = repository
        self.conversion_client = conversion_client
        self.parse_use_case = parse_use_case

    def execute_local(self, pdf_path: Path, parse_after_convert: bool = True) -> RunRecord:
        paper_id = pdf_path.stem
        run_record = RunRecord(paper_id=paper_id, source_type="local_pdf", source=str(pdf_path), status="converting")
        self.repository.save_run_record(paper_id, run_record)

        result = self.conversion_client.convert_file(pdf_path)
        self.repository.save_raw_result(paper_id, result.raw_files or {"full.md": result.markdown.encode("utf-8")})
        markdown_path = self.repository.save_normalized_markdown(paper_id, normalize_markdown(result.markdown))

        run_record.status = "converted"
        run_record.artifacts["normalized_markdown"] = str(markdown_path)
        run_record.touch("converted")
        self.repository.save_run_record(paper_id, run_record)

        if parse_after_convert:
            structure_path, _ = self.parse_use_case.execute(paper_id, markdown_content=result.markdown, source_type="local_pdf")
            run_record = self.repository.load_run_record(paper_id) or run_record
            run_record.status = "parsed"
            run_record.artifacts["structure"] = str(structure_path)
            run_record.touch("parsed")
            self.repository.save_run_record(paper_id, run_record)

        return run_record

    def execute_url(self, url: str, paper_id: str | None = None, parse_after_convert: bool = True) -> RunRecord:
        if not paper_id:
            paper_id = Path(urlparse(url).path).stem or "paper"

        run_record = RunRecord(paper_id=paper_id, source_type="url_pdf", source=url, status="converting")
        self.repository.save_run_record(paper_id, run_record)

        result = self.conversion_client.convert_url(url)
        self.repository.save_raw_result(paper_id, result.raw_files or {"full.md": result.markdown.encode("utf-8")})
        markdown_path = self.repository.save_normalized_markdown(paper_id, normalize_markdown(result.markdown))

        run_record.status = "converted"
        run_record.artifacts["normalized_markdown"] = str(markdown_path)
        run_record.touch("converted")
        self.repository.save_run_record(paper_id, run_record)

        if parse_after_convert:
            structure_path, _ = self.parse_use_case.execute(paper_id, markdown_content=result.markdown, source_type="url_pdf")
            run_record = self.repository.load_run_record(paper_id) or run_record
            run_record.status = "parsed"
            run_record.artifacts["structure"] = str(structure_path)
            run_record.touch("parsed")
            self.repository.save_run_record(paper_id, run_record)

        return run_record
