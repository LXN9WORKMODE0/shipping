"""Logging configuration helpers."""

from __future__ import annotations

import logging

from src.core.paths import LOG_DIR, ensure_runtime_directories
from src.core.settings import get_settings


def configure_logging(level: str | None = None) -> None:
    """Configure root logging once for the CLI application."""
    ensure_runtime_directories()
    settings = get_settings()
    effective_level = getattr(logging, (level or settings.log_level).upper(), logging.INFO)

    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(effective_level)
        for handler in root_logger.handlers:
            handler.setLevel(effective_level)
        return

    log_path = LOG_DIR / "app.log"
    logging.basicConfig(
        level=effective_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
