from pathlib import Path

from src.core.models import RawConversionResult
from src.parsing.markdown import normalize_markdown
from src.pipeline import ConvertPaperUseCase, ParseStructureUseCase
from src.parsing import DocumentStructureBuilder


class StubConversionClient:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown

    def convert_file(self, pdf_path: Path) -> RawConversionResult:
        return RawConversionResult(markdown=self.markdown, raw_files={"full.md": self.markdown.encode("utf-8")})

    def convert_url(self, url: str) -> RawConversionResult:
        return RawConversionResult(markdown=self.markdown, raw_files={"full.md": self.markdown.encode("utf-8")})

    def health_check(self) -> bool:
        return True


def test_normalize_markdown_removes_noise_lines():
    content = (
        "# 摘要\r\n"
        "----- 1 -----\n"
        "![figure](image.png)\n"
        "学校名称\n"
        "学校名称\n"
        "\n"
        "\n"
        "# 第一章 绪论\n"
        "正文内容\n"
    )

    normalized = normalize_markdown(content)

    assert "----- 1 -----" not in normalized
    assert "![figure](image.png)" not in normalized
    assert normalized.count("学校名称") == 1
    assert "\r" not in normalized


def test_convert_pipeline_persists_cleaned_markdown(repository):
    noisy_markdown = (
        "# 摘要\n"
        "----- 1 -----\n"
        "![figure](image.png)\n"
        "# 第一章 绪论\n"
        "这里是正文。\n"
        "# 参考文献\n"
        "[1] 示例文献\n"
    )
    pdf_path = repository.papers_dir / "noise-sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nstub pdf")

    parse_use_case = ParseStructureUseCase(repository=repository, builder=DocumentStructureBuilder())
    convert_use_case = ConvertPaperUseCase(
        repository=repository,
        conversion_client=StubConversionClient(noisy_markdown),
        parse_use_case=parse_use_case,
    )

    convert_use_case.execute_local(pdf_path, parse_after_convert=True)

    workspace = repository.get_workspace("noise-sample")
    saved_markdown = (workspace.normalized_dir / "document.md").read_text(encoding="utf-8")

    assert "----- 1 -----" not in saved_markdown
    assert "![figure](image.png)" not in saved_markdown
