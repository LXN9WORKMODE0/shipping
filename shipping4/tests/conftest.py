from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import shutil
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cli.context import AppContext
from src.cli.output import OutputFormatter
from src.core.models import RawConversionResult
from src.parsing import DocumentStructureBuilder
from src.pipeline import ConvertPaperUseCase, GenerateSummaryUseCase, ParseStructureUseCase
from src.workspace import WorkspaceRepository


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
        text = " ".join(content.split())
        return (text[:max_words] or title).strip()

    def health_check(self) -> bool:
        return True


@pytest.fixture
def journal_markdown() -> str:
    return Path("tests/fixtures/journal.md").read_text(encoding="utf-8")


@pytest.fixture
def thesis_markdown() -> str:
    return Path("tests/fixtures/thesis.md").read_text(encoding="utf-8")


@pytest.fixture
def thesis_complex_markdown() -> str:
    return Path("tests/fixtures/thesis_complex.md").read_text(encoding="utf-8")


@pytest.fixture
def journal_zero_intro_markdown() -> str:
    return Path("tests/fixtures/journal_zero_intro.md").read_text(encoding="utf-8")


@pytest.fixture
def journal_fullwidth_mix_markdown() -> str:
    return Path("tests/fixtures/journal_fullwidth_mix.md").read_text(encoding="utf-8")


@pytest.fixture
def plain_body_markdown() -> str:
    return Path("tests/fixtures/plain_body.md").read_text(encoding="utf-8")


@pytest.fixture
def repository() -> WorkspaceRepository:
    runtime_root = ROOT / "test_runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    case_root = runtime_root / f"case_{uuid.uuid4().hex}"
    papers_dir = case_root / "papers"
    workspace_dir = case_root / "workspace"
    papers_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    repo = WorkspaceRepository(papers_dir=papers_dir, workspace_dir=workspace_dir)
    yield repo
    shutil.rmtree(case_root, ignore_errors=True)


@pytest.fixture
def app_context(repository: WorkspaceRepository, thesis_markdown: str) -> AppContext:
    conversion_client = StubConversionClient(thesis_markdown)
    llm_client = StubLLMClient()
    parse_use_case = ParseStructureUseCase(repository=repository, builder=DocumentStructureBuilder())
    return AppContext(
        settings=SimpleNamespace(mineru_api_key="stub", llm_api_key="stub"),
        repository=repository,
        conversion_client=conversion_client,
        llm_client=llm_client,
        parse_use_case=parse_use_case,
        convert_use_case=ConvertPaperUseCase(
            repository=repository,
            conversion_client=conversion_client,
            parse_use_case=parse_use_case,
        ),
        summarize_use_case=GenerateSummaryUseCase(repository=repository, llm_client=llm_client),
        formatter=OutputFormatter(),
    )
