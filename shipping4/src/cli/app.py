"""Click application bootstrap."""

from __future__ import annotations

import click

from src.clients import LLMClient, MinerUClient
from src.cli.context import AppContext
from src.cli.output import OutputFormatter
from src.core import configure_logging, ensure_runtime_directories, get_settings
from src.parsing import DocumentStructureBuilder
from src.pipeline import ConvertPaperUseCase, GenerateSummaryUseCase, ParseStructureUseCase
from src.workspace import WorkspaceRepository


def build_app_context() -> AppContext:
    """Build runtime services for the CLI."""
    settings = get_settings()
    repository = WorkspaceRepository()
    conversion_client = MinerUClient()
    llm_client = LLMClient()
    parse_use_case = ParseStructureUseCase(repository=repository, builder=DocumentStructureBuilder())
    return AppContext(
        settings=settings,
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


@click.group()
@click.version_option(version="2.0.0")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """PDF 文献处理工具。"""
    ensure_runtime_directories()
    configure_logging()
    if ctx.obj is None:
        ctx.obj = build_app_context()


from src.cli.commands.checks import register_check_commands
from src.cli.commands.convert import register_convert_commands
from src.cli.commands.structure import register_structure_commands

register_convert_commands(cli)
register_structure_commands(cli)
register_check_commands(cli)
