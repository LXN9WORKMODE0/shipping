"""Base client contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.core.models import RawConversionResult


class BaseConversionClient(ABC):
    """Base contract for PDF-to-markdown conversion clients."""

    @abstractmethod
    def convert_file(self, pdf_path: Path) -> RawConversionResult:
        """Convert a local PDF file into a markdown result."""

    @abstractmethod
    def convert_url(self, url: str) -> RawConversionResult:
        """Convert a remote PDF URL into a markdown result."""

    @abstractmethod
    def health_check(self) -> bool:
        """Return whether the remote service is reachable."""

    def validate_pdf(self, pdf_path: Path) -> None:
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"不是 PDF 文件: {pdf_path}")
        if pdf_path.stat().st_size == 0:
            raise ValueError(f"PDF 文件为空: {pdf_path}")
