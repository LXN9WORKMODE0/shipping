"""Structure and summary CLI commands."""

from __future__ import annotations

import logging

import click

from src.cli.context import AppContext

logger = logging.getLogger(__name__)


def register_structure_commands(cli: click.Group) -> None:
    @cli.command("parse")
    @click.argument("paper_id")
    @click.option("-v", "--verbose", is_flag=True, help="Show verbose logs")
    @click.pass_obj
    def parse(app: AppContext, paper_id: str, verbose: bool) -> None:
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        structure_path, section_paths = app.parse_use_case.execute(paper_id)
        click.echo(app.formatter.success(f"Structure generated: {structure_path}"))
        click.echo(app.formatter.info(f"Exported sections: {len(section_paths)}"))

    @cli.command("summarize")
    @click.argument("paper_id")
    @click.option("--force", is_flag=True, help="Regenerate summary artifacts even if cached")
    @click.option("-v", "--verbose", is_flag=True, help="Show verbose logs")
    @click.pass_obj
    def summarize(app: AppContext, paper_id: str, force: bool, verbose: bool) -> None:
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        result = app.summarize_use_case.execute(paper_id, force=force)
        click.echo(app.formatter.success(f"Summary artifacts generated: {paper_id}"))
        click.echo(app.formatter.info(f"Top-level nodes: {len(result.get('tree', []))}"))
        click.echo(app.formatter.info("Artifacts: summary_tree.json, overview.json, review_notes.json"))

    @cli.command("view-summary")
    @click.argument("paper_id")
    @click.option("--level", type=int, default=None, help="Only show nodes at a given semantic level")
    @click.pass_obj
    def view_summary(app: AppContext, paper_id: str, level: int | None) -> None:
        result = app.repository.load_summary_tree(paper_id)
        click.echo(app.formatter.info(f"Paper: {paper_id}"))
        click.echo(f"Generated at: {result.get('generated_at', 'unknown')}\n")
        for node in result.get("tree", []):
            _show_summary_node(node, level=level)

    @cli.command("view-overview")
    @click.argument("paper_id")
    @click.pass_obj
    def view_overview(app: AppContext, paper_id: str) -> None:
        result = app.repository.load_overview(paper_id)
        click.echo(app.formatter.info(f"Paper: {paper_id}"))
        click.echo(f"Generated at: {result.get('generated_at', 'unknown')}")
        click.echo(f"Quick take: {result.get('quick_take', '')}\n")
        for section in result.get("body_sections", []):
            click.echo(f"- {section.get('title', '')}")
            click.echo(f"  {section.get('summary', '')}")

    @cli.command("view-review-notes")
    @click.argument("paper_id")
    @click.pass_obj
    def view_review_notes(app: AppContext, paper_id: str) -> None:
        result = app.repository.load_review_notes(paper_id)
        click.echo(app.formatter.info(f"Paper: {paper_id}"))
        click.echo(f"Generated at: {result.get('generated_at', 'unknown')}\n")
        for note in result.get("body_section_notes", []):
            click.echo(f"- {note.get('title', '')}")
            click.echo(f"  {note.get('summary', '')}")
            for child in note.get("subsection_notes", []):
                click.echo(f"    * {child.get('title', '')}: {child.get('summary', '')}")

    @cli.command("generate-summary", hidden=True)
    @click.argument("paper_id")
    @click.option("--force", is_flag=True)
    @click.option("-v", "--verbose", is_flag=True)
    @click.pass_context
    def summarize_alias(ctx: click.Context, paper_id: str, force: bool, verbose: bool) -> None:
        ctx.invoke(summarize, paper_id=paper_id, force=force, verbose=verbose)


def _show_summary_node(node: dict, level: int | None, indent: int = 0) -> None:
    node_level = int(node.get("semantic_level", 1))
    if level is not None and node_level != level:
        for child in node.get("children", []):
            _show_summary_node(child, level, indent)
        return
    prefix = "  " * indent
    click.echo(f"{prefix}- [{node_level}] {node.get('title', '')}")
    click.echo(f"{prefix}  {node.get('summary', '')}")
    for child in node.get("children", []):
        _show_summary_node(child, level, indent + 1)
