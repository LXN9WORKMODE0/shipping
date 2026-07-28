"""Terminal output helpers."""

from __future__ import annotations

from typing import Iterable


class OutputFormatter:
    """Small formatter used by CLI commands."""

    def success(self, message: str) -> str:
        return f"[SUCCESS] {message}"

    def error(self, message: str) -> str:
        return f"[ERROR] {message}"

    def warning(self, message: str) -> str:
        return f"[WARNING] {message}"

    def info(self, message: str) -> str:
        return f"[INFO] {message}"

    def file_list(self, items: Iterable[str]) -> str:
        values = list(items)
        if not values:
            return "无"
        return "\n".join(f"  - {item}" for item in values)
