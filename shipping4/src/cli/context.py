"""Shared CLI dependency container."""

from __future__ import annotations

from dataclasses import dataclass

from src.clients import LLMClient, MinerUClient
from src.cli.output import OutputFormatter
from src.pipeline import ConvertPaperUseCase, GenerateSummaryUseCase, ParseStructureUseCase
from src.workspace import WorkspaceRepository


@dataclass
class AppContext:
    """Dependency container for Click commands."""

    settings: object
    repository: WorkspaceRepository
    conversion_client: MinerUClient
    llm_client: LLMClient
    parse_use_case: ParseStructureUseCase
    convert_use_case: ConvertPaperUseCase
    summarize_use_case: GenerateSummaryUseCase
    formatter: OutputFormatter
