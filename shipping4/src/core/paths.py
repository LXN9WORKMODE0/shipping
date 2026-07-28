"""Project path helpers."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAPERS_DIR = PROJECT_ROOT / "papers"
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
LOG_DIR = PROJECT_ROOT / "logs"
DOCS_DIR = PROJECT_ROOT / "docs"
TESTS_DIR = PROJECT_ROOT / "tests"


def ensure_runtime_directories() -> None:
    """Create runtime directories required by the application."""
    for path in (PAPERS_DIR, WORKSPACE_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
