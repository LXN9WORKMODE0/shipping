"""Markdown parsing helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.models import ContentRef

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")

MAX_BLANK_LINES = 2


@dataclass
class HeadingCandidate:
    """Flat heading extracted from markdown."""

    markdown_level: int
    title: str
    content: str
    start_line: int
    end_line: int


def normalize_markdown(content: str) -> str:
    """Normalize markdown into the cleaned parsing baseline used across the pipeline."""
    lines = [line.rstrip() for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized = "\n".join(lines).strip()
    normalized = clean_noise_lines(normalized)
    normalized = _collapse_blank_lines(normalized)
    return normalized + ("\n" if normalized else "")


def _collapse_blank_lines(content: str) -> str:
    """Merge consecutive blank lines beyond MAX_BLANK_LINES into fixed上限."""
    lines = content.split("\n")
    result: list[str] = []
    blank_count = 0
    for line in lines:
        if not line.strip():
            blank_count += 1
            if blank_count <= MAX_BLANK_LINES:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)
    return "\n".join(result)


def clean_noise_lines(content: str) -> str:
    """Remove page numbers, headers/footers, image placeholders, and junk headings."""
    lines = content.split("\n")
    cleaned: list[str] = []
    prev_line = ""
    for line in lines:
        if _is_noise_line(line, prev_line):
            continue
        prev_line = line
        cleaned.append(line)
    return "\n".join(cleaned)


def _is_noise_line(line: str, prev_line: str) -> bool:
    """Detect if a line is noise (page numbers, headers, footers, etc.)."""
    stripped = line.strip()
    if not stripped:
        return False
    if _is_page_number_line(stripped):
        return True
    if _is_image_placeholder(stripped):
        return True
    if _is_empty_or_symbol_title(stripped):
        return True
    if _is_duplicate_header_footer(line, prev_line):
        return True
    return False


def _is_page_number_line(line: str) -> bool:
    """Detect standalone page number lines."""
    if re.match(r"^[-‑—]{5,}\s*\d+\s*[-‑—]{5,}$", line):
        return True
    if re.match(r"^\s*\d+\s*$", line) and len(line.strip()) <= 6:
        return True
    if re.match(r"^\[\d+\]$", line):
        return True
    return False


def _is_image_placeholder(line: str) -> bool:
    """Detect image placeholder lines."""
    if re.match(r"^!\[.*\]\(.*\)$", line):
        return True
    if re.match(r"^图\s*\d+.*$", line) or re.match(r"^Figure\s*\d+.*$", line):
        return True
    if re.match(r"^表\s*\d+.*$", line) or re.match(r"^Table\s*\d+.*$", line):
        return True
    return False


def _is_empty_or_symbol_title(line: str) -> bool:
    """Detect empty titles or titles with only symbols."""
    if not line.strip():
        return True
    if re.match(r"^[_\-*=~]{3,}$", line):
        return True
    if re.match(r"^\s*[_\-*=~]+\s*$", line):
        return True
    return False


def _is_duplicate_header_footer(line: str, prev_line: str) -> bool:
    """Detect duplicate header/footer lines."""
    if line == prev_line and len(line.strip()) > 0 and len(line.strip()) <= 80:
        return True
    return False


def parse_markdown_headings(content: str, allow_no_space_numbering: bool = True) -> list[HeadingCandidate]:
    """Parse markdown headings into flat candidates.

    Args:
        content: Markdown text (pre-normalized).
        allow_no_space_numbering: If True, recognize headings like "1.1.1研究背景" without space.
    """
    lines = content.split("\n")
    candidates: list[HeadingCandidate] = []
    current_index = 0
    while current_index < len(lines):
        match = HEADING_PATTERN.match(lines[current_index])
        if not match:
            if allow_no_space_numbering:
                no_space_match = _match_no_space_heading(lines[current_index])
                if no_space_match:
                    title, level = no_space_match
                    next_heading = _find_next_heading(lines, current_index + 1)
                    end_index = next_heading if next_heading is not None else len(lines)
                    candidates.append(
                        HeadingCandidate(
                            markdown_level=level,
                            title=title,
                            content="\n".join(lines[current_index + 1 : end_index]).strip(),
                            start_line=current_index + 1,
                            end_line=end_index,
                        )
                    )
                    current_index = end_index
                    continue
            current_index += 1
            continue

        next_heading = _find_next_heading(lines, current_index + 1)
        end_index = next_heading if next_heading is not None else len(lines)
        candidates.append(
            HeadingCandidate(
                markdown_level=len(match.group(1)),
                title=match.group(2).strip(),
                content="\n".join(lines[current_index + 1 : end_index]).strip(),
                start_line=current_index + 1,
                end_line=end_index,
            )
        )
        current_index = end_index
    return candidates


def _match_no_space_heading(line: str) -> tuple[str, int] | None:
    """Match headings with no space between numbering and title, e.g. '1.1.1研究背景'.

    Returns (title, level) if matched, None otherwise.
    """
    patterns = [
        (r"^(\d+(?:\.\d+)+)([^\s\d].*)$", 3),
        (r"^(\d+)\.([^\s\d].*)$", 2),
        (r"^([一二三四五六七八九十百千零]+)、([^ ].*)$", 1),
        (r"^（?([一二三四五六七八九十百千零]+)）?([^ ].*)$", 1),
    ]
    for pattern, level in patterns:
        m = re.match(pattern, line)
        if m:
            return m.group(1) + " " + m.group(2), level
    return None


def extract_text_from_ref(content: str, ref: dict | ContentRef | None) -> str:
    """Extract text from a content reference."""
    if ref is None:
        return ""
    if isinstance(ref, dict):
        ref = ContentRef.from_dict(ref)
    lines = content.split("\n")
    return "\n".join(lines[ref.start_line - 1 : ref.end_line]).strip()


def _find_next_heading(lines: list[str], start_index: int) -> int | None:
    for index in range(start_index, len(lines)):
        if HEADING_PATTERN.match(lines[index]):
            return index
    return None
