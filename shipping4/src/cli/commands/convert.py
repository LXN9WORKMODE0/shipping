"""Conversion-oriented CLI commands."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import click

from src.cli.context import AppContext

logger = logging.getLogger(__name__)


def register_convert_commands(cli: click.Group) -> None:
    @cli.command("convert")
    @click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
    @click.option("--skip-parse", "--skip-split", is_flag=True, help="只转换，不生成结构")
    @click.option("-v", "--verbose", is_flag=True, help="显示详细日志")
    @click.pass_obj
    def convert(app: AppContext, pdf_path: Path, skip_parse: bool, verbose: bool) -> None:
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        click.echo(app.formatter.info(f"开始转换: {pdf_path.name}"))
        run_record = app.convert_use_case.execute_local(pdf_path, parse_after_convert=not skip_parse)
        click.echo(app.formatter.success(f"完成: {run_record.status}"))
        for name, value in run_record.artifacts.items():
            click.echo(f"  {name}: {value}")

    @cli.command("convert-url")
    @click.argument("url")
    @click.option("--paper-id", default=None, help="自定义论文 ID")
    @click.option("--skip-parse", "--skip-split", is_flag=True, help="只转换，不生成结构")
    @click.option("-v", "--verbose", is_flag=True, help="显示详细日志")
    @click.pass_obj
    def convert_url(app: AppContext, url: str, paper_id: str | None, skip_parse: bool, verbose: bool) -> None:
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        click.echo(app.formatter.info(f"开始转换: {url}"))
        run_record = app.convert_use_case.execute_url(url, paper_id=paper_id, parse_after_convert=not skip_parse)
        click.echo(app.formatter.success(f"完成: {run_record.status}"))
        for name, value in run_record.artifacts.items():
            click.echo(f"  {name}: {value}")

    @cli.command("batch")
    @click.option("--directory", "-d", type=click.Path(exists=True, path_type=Path), default=None, help="PDF 搜索目录")
    @click.option("--skip-existing", is_flag=True, default=True, help="跳过已处理论文")
    @click.option("--skip-parse", is_flag=True, default=False, help="只转换，不生成结构")
    @click.option("-v", "--verbose", is_flag=True, help="显示详细日志")
    @click.pass_obj
    def batch(app: AppContext, directory: Path | None, skip_existing: bool, skip_parse: bool, verbose: bool) -> None:
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)

        pdf_files = app.repository.find_pdfs(directory)
        if skip_existing:
            pdf_files = [path for path in pdf_files if not _already_processed(app, path.stem)]
        if not pdf_files:
            click.echo(app.formatter.warning("没有需要处理的 PDF"))
            return

        success_count = 0
        failed_count = 0
        with click.progressbar(pdf_files, label="转换进度", item_show_func=lambda p: p.name if p else "") as bar:
            for pdf_path in bar:
                try:
                    app.convert_use_case.execute_local(pdf_path, parse_after_convert=not skip_parse)
                    success_count += 1
                except Exception as exc:  # pragma: no cover - integration path
                    failed_count += 1
                    logger.exception("批量处理失败: %s", exc)
        click.echo(app.formatter.info(f"批量完成: 成功 {success_count}, 失败 {failed_count}"))

    @cli.command("batch-url")
    @click.argument("url_file", type=click.Path(exists=True, path_type=Path))
    @click.option("--skip-existing", is_flag=True, default=True, help="跳过已处理论文")
    @click.option("--skip-parse", is_flag=True, default=False, help="只转换，不生成结构")
    @click.pass_obj
    def batch_url(app: AppContext, url_file: Path, skip_existing: bool, skip_parse: bool) -> None:
        entries = _load_urls(url_file)
        if skip_existing:
            entries = [(url, paper_id) for url, paper_id in entries if not _already_processed(app, paper_id)]
        if not entries:
            click.echo(app.formatter.warning("没有需要处理的 URL"))
            return

        success_count = 0
        failed_count = 0
        with click.progressbar(entries, label="转换进度", item_show_func=lambda item: item[1] if item else "") as bar:
            for url, paper_id in bar:
                try:
                    app.convert_use_case.execute_url(url, paper_id=paper_id, parse_after_convert=not skip_parse)
                    success_count += 1
                except Exception as exc:  # pragma: no cover - integration path
                    failed_count += 1
                    logger.exception("批量 URL 处理失败: %s", exc)
        click.echo(app.formatter.info(f"批量完成: 成功 {success_count}, 失败 {failed_count}"))

    @cli.command("batch-urls", hidden=True)
    @click.argument("url_file", type=click.Path(exists=True, path_type=Path))
    @click.option("--skip-existing", is_flag=True, default=True)
    @click.option("--skip-parse", is_flag=True, default=False)
    @click.pass_context
    def batch_urls_alias(ctx: click.Context, url_file: Path, skip_existing: bool, skip_parse: bool) -> None:
        ctx.invoke(batch_url, url_file=url_file, skip_existing=skip_existing, skip_parse=skip_parse)

    @cli.command("process")
    @click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
    @click.pass_context
    def process_alias(ctx: click.Context, pdf_path: Path) -> None:
        ctx.invoke(convert, pdf_path=pdf_path, skip_parse=False, verbose=False)


def _already_processed(app: AppContext, paper_id: str) -> bool:
    record = app.repository.load_run_record(paper_id)
    return record is not None and record.status in {"parsed", "summarized"}


def _load_urls(url_file: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    with open(url_file, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            paper_id = Path(urlparse(line).path).stem or "paper"
            entries.append((line, paper_id))
    return entries
