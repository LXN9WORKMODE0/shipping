"""Core runtime primitives for the paper processing application."""

from src.core.logging import configure_logging
from src.core.models import (
    ContentRef,
    DocumentStructure,
    ExportedSection,
    RawConversionResult,
    RunRecord,
    SectionNode,
)
from src.core.paths import LOG_DIR, PAPERS_DIR, PROJECT_ROOT, WORKSPACE_DIR, ensure_runtime_directories
from src.core.settings import Settings, get_settings

__all__ = [
    "ContentRef",
    "DocumentStructure",
    "ExportedSection",
    "LOG_DIR",
    "PAPERS_DIR",
    "PROJECT_ROOT",
    "RawConversionResult",
    "RunRecord",
    "SectionNode",
    "Settings",
    "WORKSPACE_DIR",
    "configure_logging",
    "ensure_runtime_directories",
    "get_settings",
]
