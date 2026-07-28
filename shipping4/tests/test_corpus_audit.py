from __future__ import annotations

import json
from pathlib import Path

from src.parsing import DocumentStructureBuilder
from src.pipeline import ConvertPaperUseCase, GenerateSummaryUseCase, ParseStructureUseCase

from scripts.corpus_audit import (
    AuditConfig,
    AuditServices,
    CommandCheckResult,
    build_inventory,
    bucket_for_size,
    guess_category,
    run_audit,
    select_preflight_samples,
)


class StubConversionClient:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown

    def convert_file(self, pdf_path: Path):
        from src.core.models import RawConversionResult

        return RawConversionResult(markdown=self.markdown, raw_files={"full.md": self.markdown.encode("utf-8")})

    def convert_url(self, url: str):
        from src.core.models import RawConversionResult

        return RawConversionResult(markdown=self.markdown, raw_files={"full.md": self.markdown.encode("utf-8")})

    def health_check(self) -> bool:
        return True


class StubLLMClient:
    def summarize(self, title: str, content: str, max_words: int = 150) -> str:
        text = " ".join(content.split())
        if not text:
            return title
        return text[:max_words]

    def refine_low_confidence_nodes(self, paper_id, detected_schema, language, nodes):
        return []

    def health_check(self) -> bool:
        return True


class UnhealthyLLMClient(StubLLMClient):
    def health_check(self) -> bool:
        return False


def build_services(repository, markdown: str, llm_client) -> AuditServices:
    conversion_client = StubConversionClient(markdown)
    parse_use_case = ParseStructureUseCase(
        repository=repository,
        builder=DocumentStructureBuilder(),
        llm_client=llm_client,
        refinement_enabled=True,
    )
    return AuditServices(
        repository=repository,
        conversion_client=conversion_client,
        llm_client=llm_client,
        convert_use_case=ConvertPaperUseCase(
            repository=repository,
            conversion_client=conversion_client,
            parse_use_case=parse_use_case,
        ),
        parse_use_case=parse_use_case,
        summarize_use_case=GenerateSummaryUseCase(repository=repository, llm_client=llm_client),
    )


def write_stub_pdf(path: Path, size: int = 64) -> None:
    path.write_bytes(b"%PDF-1.4\n" + b"x" * size)


def ok_check_runner(project_root: Path) -> CommandCheckResult:
    return CommandCheckResult(exit_code=0, stdout="ok", stderr="")


def test_bucket_category_and_duplicate_inventory(repository):
    assert bucket_for_size(50 * 1024) == "xs(<200KB)"
    assert bucket_for_size(400 * 1024) == "s(200KB-1MB)"
    assert bucket_for_size(2 * 1024 * 1024) == "m(1MB-5MB)"
    assert bucket_for_size(8 * 1024 * 1024) == "l(5MB-15MB)"
    assert bucket_for_size(20 * 1024 * 1024) == "xl(>15MB)"

    assert guess_category("博士学位论文.pdf", 1024) == "thesis_like"
    assert guess_category("《人民日报》报道研究.pdf", 1024) == "news_like"
    assert guess_category("城市交通研究.pdf", 1024) == "journal_like"

    write_stub_pdf(repository.papers_dir / "城市交通研究.pdf")
    write_stub_pdf(repository.papers_dir / "城市交通研究_1.pdf")
    write_stub_pdf(repository.papers_dir / "博士学位论文.pdf")

    inventory = build_inventory(repository.papers_dir)
    duplicate_entries = {candidate.paper_id: candidate.is_duplicate for candidate in inventory}
    assert duplicate_entries["城市交通研究"] is True
    assert duplicate_entries["城市交通研究_1"] is True
    assert duplicate_entries["博士学位论文"] is False


def test_preflight_prefers_moderate_samples(repository):
    write_stub_pdf(repository.papers_dir / "大型博士学位论文.pdf", size=16 * 1024 * 1024)
    write_stub_pdf(repository.papers_dir / "中型博士学位论文.pdf", size=2 * 1024 * 1024)
    write_stub_pdf(repository.papers_dir / "城市交通研究.pdf", size=3 * 1024 * 1024)
    write_stub_pdf(repository.papers_dir / "《人民日报》报道研究.pdf", size=512 * 1024)

    inventory = build_inventory(repository.papers_dir)
    selected = select_preflight_samples(inventory, 3)
    selected_ids = {candidate.paper_id for candidate in selected}

    assert "中型博士学位论文" in selected_ids
    assert "大型博士学位论文" not in selected_ids


def test_run_audit_stops_when_environment_blocked(repository, thesis_markdown):
    project_root = repository.papers_dir.parent
    docs_dir = project_root / "docs"
    audit_root = repository.workspace_dir / "_audit"

    write_stub_pdf(repository.papers_dir / "博士学位论文.pdf")

    services = build_services(repository, thesis_markdown, UnhealthyLLMClient())
    config = AuditConfig(
        project_root=project_root,
        papers_dir=repository.papers_dir,
        workspace_dir=repository.workspace_dir,
        docs_dir=docs_dir,
        audit_root=audit_root,
        preflight_count=1,
        pilot_count=1,
        main_count=1,
        review_count=1,
    )

    results = run_audit(config, services=services, cli_check_runner=ok_check_runner)

    assert results["execution"]["stopped_reason"] == "environment_blocked"
    assert results["sample_stats"]["executed"] == 0
    assert any(finding["code"] == "llm_health_failed" for finding in results["findings"])

    report_path = project_root / results["report_path"]
    results_path = project_root / results["results_path"]
    assert report_path.exists()
    assert results_path.exists()


def test_run_audit_generates_results_and_report(repository, thesis_markdown):
    project_root = repository.papers_dir.parent
    docs_dir = project_root / "docs"
    audit_root = repository.workspace_dir / "_audit"

    for name in ("博士学位论文.pdf", "城市交通研究.pdf", "《人民日报》报道研究.pdf", "附录样本.pdf"):
        write_stub_pdf(repository.papers_dir / name)

    llm_client = StubLLMClient()
    services = build_services(repository, thesis_markdown, llm_client)
    config = AuditConfig(
        project_root=project_root,
        papers_dir=repository.papers_dir,
        workspace_dir=repository.workspace_dir,
        docs_dir=docs_dir,
        audit_root=audit_root,
        preflight_count=1,
        pilot_count=1,
        main_count=1,
        review_count=1,
    )

    results = run_audit(config, services=services, cli_check_runner=ok_check_runner)

    report_path = project_root / results["report_path"]
    results_path = project_root / results["results_path"]

    assert results["sample_stats"]["executed"] == 3
    assert results["sample_stats"]["summarized"] == 3
    assert len(results["manual_review_queue"]) == 1
    assert report_path.exists()
    assert results_path.exists()

    payload = json.loads(results_path.read_text(encoding="utf-8"))
    assert payload["sample_stats"]["executed"] == 3
    assert payload["phases"]["preflight"]["status"] == "completed"
    assert payload["phases"]["pilot"]["status"] == "completed"
    assert payload["phases"]["main"]["status"] == "completed"
    assert all(record["checks"]["summary_stage_recorded"] is True for record in payload["records"])
    assert all(record["checks"]["summary_artifacts_complete"] is True for record in payload["records"])
