"""Status and environment CLI commands."""

from __future__ import annotations

import click

from src.cli.context import AppContext
from src.core.paths import LOG_DIR, PAPERS_DIR, WORKSPACE_DIR


def register_check_commands(cli: click.Group) -> None:
    @cli.command("status")
    @click.pass_obj
    def status(app: AppContext) -> None:
        pdf_files = app.repository.find_pdfs()
        processed = []
        pending = []
        for pdf_path in pdf_files:
            record = app.repository.load_run_record(pdf_path.stem)
            if record and record.status in {"parsed", "summarized"}:
                processed.append(pdf_path.name)
            else:
                pending.append(pdf_path.name)

        click.echo(f"总 PDF 数量: {len(pdf_files)}")
        click.echo(f"已处理: {len(processed)}")
        click.echo(f"待处理: {len(pending)}")
        if pending:
            click.echo("\n待处理文件:")
            click.echo(app.formatter.file_list(pending[:10]))

    @cli.command("check")
    @click.pass_obj
    def check(app: AppContext) -> None:
        click.echo("目录检查:")
        for name, path in (("papers", PAPERS_DIR), ("workspace", WORKSPACE_DIR), ("logs", LOG_DIR)):
            click.echo(f"  - {name}: {'OK' if path.exists() else 'MISSING'} -> {path}")

        click.echo("\n服务检查:")
        click.echo(f"  - MinerU API Key: {'已配置' if app.settings.mineru_api_key else '未配置'}")
        click.echo(f"  - LLM API Key: {'已配置' if app.settings.llm_api_key else '未配置'}")
        click.echo(f"  - MinerU 连通性: {'OK' if app.conversion_client.health_check() else 'UNAVAILABLE'}")
        click.echo(f"  - LLM 连通性: {'OK' if app.llm_client.health_check() else 'UNAVAILABLE'}")
