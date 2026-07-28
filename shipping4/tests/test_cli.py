from pathlib import Path

from click.testing import CliRunner

from src.cli import cli


def test_check_command_runs(app_context):
    runner = CliRunner()
    result = runner.invoke(cli, ["check"], obj=app_context)
    assert result.exit_code == 0
    assert "目录检查" in result.output


def test_status_and_convert_commands_run(app_context):
    runner = CliRunner()
    pdf_path = app_context.repository.papers_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nstub pdf")
    result = runner.invoke(cli, ["convert", str(pdf_path)], obj=app_context)
    assert result.exit_code == 0
    assert "[SUCCESS] 完成: parsed" in result.output

    status_result = runner.invoke(cli, ["status"], obj=app_context)
    assert status_result.exit_code == 0
    assert "总 PDF 数量" in status_result.output


def test_batch_url_aliases_work(app_context):
    runner = CliRunner()
    url_file = app_context.repository.papers_dir / "urls.txt"
    url_file.write_text("https://example.com/paper.pdf\n", encoding="utf-8")

    standard = runner.invoke(cli, ["batch-url", str(url_file)], obj=app_context)
    alias = runner.invoke(cli, ["batch-urls", str(url_file)], obj=app_context)

    assert standard.exit_code == 0
    assert alias.exit_code == 0


def test_summary_view_commands_run(app_context):
    runner = CliRunner()
    pdf_path = app_context.repository.papers_dir / "summary-paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nstub pdf")

    convert_result = runner.invoke(cli, ["convert", str(pdf_path)], obj=app_context)
    assert convert_result.exit_code == 0

    summarize_result = runner.invoke(cli, ["summarize", "summary-paper"], obj=app_context)
    assert summarize_result.exit_code == 0
    assert "overview.json" in summarize_result.output

    overview_result = runner.invoke(cli, ["view-overview", "summary-paper"], obj=app_context)
    notes_result = runner.invoke(cli, ["view-review-notes", "summary-paper"], obj=app_context)

    assert overview_result.exit_code == 0
    assert notes_result.exit_code == 0
    assert "Paper: summary-paper" in overview_result.output
    assert "Paper: summary-paper" in notes_result.output
