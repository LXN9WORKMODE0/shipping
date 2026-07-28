from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from shipping_pipeline.markdown_structure import DocumentMap, SourceSpan
from shipping_pipeline.markup import contains_html_markup, strip_html_markup


MAX_BLOCK_CHARS = 1400
MARKDOWN_HEADING_PATTERN = re.compile(r"^#{1,6}\s+.+?\s*$")
MARKDOWN_IMAGE_PATTERN = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
TABLE_START_PATTERN = re.compile(r"<table\b", re.IGNORECASE)
TABLE_END_PATTERN = re.compile(r"</table>", re.IGNORECASE)
TABLE_STRUCTURE_PATTERN = re.compile(r"</?(?:table|thead|tbody|tfoot|tr|td|th|caption|colgroup|col)\b", re.IGNORECASE)
INLINE_ABSTRACT_LABEL_PATTERN = re.compile(
    r"^(?:摘\s*要|A\s*B\s*S\s*T\s*R\s*A\s*C\s*T)\s*(?:[:：]\s*)?",
    re.IGNORECASE,
)
SENTENCE_ENDINGS = "。！？!?；;"


@dataclass(frozen=True)
class ContentBlock:
    block_id: str
    group_id: str
    content_kind: str
    text: str
    source_spans: tuple[SourceSpan, ...]
    heading_path: tuple[str, ...]
    quality_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "group_id": self.group_id,
            "content_kind": self.content_kind,
            "text": self.text,
            "source_spans": [span.to_dict() for span in self.source_spans],
            "heading_path": list(self.heading_path),
            "quality_flags": list(self.quality_flags),
        }


class TableTextParser(HTMLParser):
    """提取 HTML 表格单元格，保留行边界，不推断表格语义。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized == "tr":
            self._finish_row()
            self._row = []
        elif normalized in {"td", "th"}:
            if self._row is None:
                self._row = []
            self._finish_cell()
            self._cell_parts = []
        elif normalized == "br" and self._cell_parts is not None:
            self._cell_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"td", "th"}:
            self._finish_cell()
        elif normalized == "tr":
            self._finish_cell()
            self._finish_row()

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def close(self) -> None:
        super().close()
        self._finish_cell()
        self._finish_row()

    def _finish_cell(self) -> None:
        if self._cell_parts is None:
            return
        value = re.sub(r"\s+", " ", "".join(self._cell_parts)).strip()
        if self._row is None:
            self._row = []
        self._row.append(value)
        self._cell_parts = None

    def _finish_row(self) -> None:
        if self._row is not None and any(cell for cell in self._row):
            self.rows.append(self._row)
        self._row = None


class TableBoundaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.table_depth = 0
        self.row_depth = 0
        self.cell_depth = 0
        self.saw_table = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized == "table":
            self.saw_table = True
            self.table_depth += 1
        elif normalized == "tr":
            self.row_depth += 1
        elif normalized in {"td", "th"}:
            self.cell_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized == "table":
            self.table_depth = max(self.table_depth - 1, 0)
        elif normalized == "tr":
            self.row_depth = max(self.row_depth - 1, 0)
        elif normalized in {"td", "th"}:
            self.cell_depth = max(self.cell_depth - 1, 0)

    @property
    def closed(self) -> bool:
        return self.saw_table and self.table_depth == 0

    @property
    def inside_row_or_cell(self) -> bool:
        return self.row_depth > 0 or self.cell_depth > 0


def build_blocks_for_document_map(lines: list[str], document_map: DocumentMap) -> list[ContentBlock]:
    if document_map.selected_segment is None:
        return []
    blocks: list[ContentBlock] = []
    for region in sorted(document_map.regions, key=lambda item: (item.span.start_line, item.span.end_line)):
        if not region.included_in_materials:
            continue
        default_kind = "abstract" if region.role == "abstract" else "prose"
        blocks.extend(
            build_content_blocks(
                lines,
                start_line=region.span.start_line,
                end_line=region.span.end_line,
                heading_path=region.heading_path,
                content_kind=default_kind,
            )
        )
    return blocks


def build_content_blocks(
    lines: list[str],
    start_line: int,
    end_line: int,
    heading_path: tuple[str, ...],
    content_kind: str = "prose",
) -> list[ContentBlock]:
    lower = max(start_line, 1)
    upper = min(end_line, len(lines))
    if upper < lower:
        return []

    blocks: list[ContentBlock] = []
    paragraph: list[tuple[int, str]] = []
    structural_start_pending = True

    def flush_paragraph() -> None:
        nonlocal paragraph
        if not paragraph:
            return
        blocks.extend(_paragraph_blocks(paragraph, heading_path, content_kind))
        paragraph = []

    line_number = lower
    while line_number <= upper:
        raw_line = lines[line_number - 1]
        stripped = raw_line.strip()
        if not stripped:
            flush_paragraph()
            line_number += 1
            continue
        if structural_start_pending:
            structural_start_pending = False
            if MARKDOWN_HEADING_PATTERN.match(stripped):
                flush_paragraph()
                line_number += 1
                continue
            if heading_path and _same_visible_text(stripped, heading_path[-1]):
                flush_paragraph()
                line_number += 1
                continue
            if content_kind == "abstract":
                raw_line = INLINE_ABSTRACT_LABEL_PATTERN.sub("", raw_line, count=1).strip()
                stripped = raw_line.strip()
                if not stripped:
                    line_number += 1
                    continue
        if MARKDOWN_HEADING_PATTERN.match(stripped):
            flush_paragraph()
            line_number += 1
            continue
        if TABLE_START_PATTERN.search(stripped):
            flush_paragraph()
            table_start = line_number
            table_lines, table_end, malformed = _collect_table_lines(lines, line_number, upper)
            blocks.extend(
                _table_blocks(
                    table_lines,
                    table_start,
                    table_end,
                    heading_path,
                    forced_parse_failure=malformed,
                )
            )
            line_number = table_end
            line_number += 1
            continue
        image_match = MARKDOWN_IMAGE_PATTERN.match(stripped)
        if image_match:
            flush_paragraph()
            alt_text = image_match.group(1).strip()
            target = image_match.group(2).strip()
            text = alt_text or target
            blocks.append(
                ContentBlock(
                    block_id=f"block_image_L{line_number:05d}",
                    group_id=f"image_L{line_number:05d}",
                    content_kind="image",
                    text=text,
                    source_spans=(SourceSpan("normalized/document.md", line_number, line_number),),
                    heading_path=heading_path,
                    quality_flags=("media.image_omitted",),
                )
            )
            line_number += 1
            continue
        paragraph.append((line_number, raw_line))
        line_number += 1

    flush_paragraph()
    return blocks


def _paragraph_blocks(
    paragraph: list[tuple[int, str]],
    heading_path: tuple[str, ...],
    content_kind: str,
) -> list[ContentBlock]:
    start_line = paragraph[0][0]
    end_line = paragraph[-1][0]
    raw_text = "\n".join(line.strip() for _, line in paragraph)
    text, clean_flags = _clean_visible_text(raw_text)
    if not text:
        return []
    parts = _split_text(text, MAX_BLOCK_CHARS)
    group_id = f"{content_kind}_L{start_line:05d}-{end_line:05d}"
    blocks = []
    offset = 0
    exact_line_offset: int | None = None
    if len(paragraph) == 1 and not clean_flags:
        exact_line_offset = paragraph[0][1].find(text)
        if exact_line_offset < 0:
            exact_line_offset = None
    for index, part in enumerate(parts, start=1):
        part_start = offset
        part_end = offset + len(part)
        span = SourceSpan(
            "normalized/document.md",
            start_line,
            end_line,
            start_char=exact_line_offset + part_start if exact_line_offset is not None else None,
            end_char=exact_line_offset + part_end if exact_line_offset is not None else None,
        )
        blocks.append(
            ContentBlock(
                block_id=f"block_{group_id}_p{index:03d}",
                group_id=group_id,
                content_kind=content_kind,
                text=part,
                source_spans=(span,),
                heading_path=heading_path,
                quality_flags=clean_flags,
            )
        )
        offset = part_end
    return blocks


def _table_blocks(
    table_lines: list[str],
    start_line: int,
    end_line: int,
    heading_path: tuple[str, ...],
    forced_parse_failure: bool = False,
) -> list[ContentBlock]:
    raw_table = "\n".join(table_lines)
    parser = TableTextParser()
    flags: list[str] = []
    try:
        parser.feed(raw_table)
        parser.close()
    except Exception:
        parser.rows = []

    rows = [" | ".join(cell for cell in row) for row in parser.rows if any(cell for cell in row)]
    if forced_parse_failure:
        rows = []
        flags.append("table.parse_failed")
    if not rows:
        visible = html.unescape(strip_html_markup(raw_table, " "))
        visible = re.sub(r"\s+", " ", visible).strip()
        rows = [visible or raw_table.strip()]
        if "table.parse_failed" not in flags:
            flags.append("table.parse_failed")

    packed_rows: list[tuple[str, tuple[str, ...]]] = []
    current: list[str] = []
    for row in rows:
        if len(row) > MAX_BLOCK_CHARS:
            if current:
                packed_rows.append(("\n".join(current), tuple(flags)))
                current = []
            packed_rows.append((row, tuple([*flags, "table.row_too_long"])))
            continue
        candidate = "\n".join([*current, row])
        if current and len(candidate) > MAX_BLOCK_CHARS:
            packed_rows.append(("\n".join(current), tuple(flags)))
            current = [row]
        else:
            current.append(row)
    if current:
        packed_rows.append(("\n".join(current), tuple(flags)))

    group_id = f"table_L{start_line:05d}-{end_line:05d}"
    source_span = SourceSpan("normalized/document.md", start_line, end_line)
    return [
        ContentBlock(
            block_id=f"block_{group_id}_p{index:03d}",
            group_id=group_id,
            content_kind="table",
            text=text,
            source_spans=(source_span,),
            heading_path=heading_path,
            quality_flags=_unique_flags(part_flags),
        )
        for index, (text, part_flags) in enumerate(packed_rows, start=1)
    ]


def _clean_visible_text(value: str) -> tuple[str, tuple[str, ...]]:
    flags: list[str] = []
    if contains_html_markup(value):
        flags.append("clean.removed_markup")
    visible = html.unescape(strip_html_markup(value))
    visible = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in visible.splitlines())
    return visible.strip(), tuple(flags)


def _same_visible_text(left: str, right: str) -> bool:
    def normalize(value: str) -> str:
        visible = html.unescape(strip_html_markup(value))
        return re.sub(r"\s+", " ", visible).strip()

    return normalize(left) == normalize(right)


def _collect_table_lines(lines: list[str], start_line: int, upper: int) -> tuple[list[str], int, bool]:
    parser = TableBoundaryParser()
    table_lines: list[str] = []
    line_number = start_line
    while line_number <= upper:
        raw_line = lines[line_number - 1]
        if (
            table_lines
            and raw_line.strip()
            and not parser.inside_row_or_cell
            and not TABLE_STRUCTURE_PATTERN.search(raw_line)
        ):
            break
        table_lines.append(raw_line)
        parser.feed(raw_line + "\n")
        if parser.closed or TABLE_END_PATTERN.search(raw_line):
            return table_lines, line_number, False
        line_number += 1
    return table_lines, start_line + len(table_lines) - 1, True


def _split_text(value: str, max_chars: int) -> list[str]:
    if len(value) <= max_chars:
        return [value]
    parts: list[str] = []
    start = 0
    while start < len(value):
        proposed_end = min(start + max_chars, len(value))
        if proposed_end < len(value):
            boundary = _sentence_boundary(value, start, proposed_end)
            if boundary is not None:
                proposed_end = boundary
        parts.append(value[start:proposed_end])
        start = proposed_end
    return parts


def _sentence_boundary(value: str, start: int, proposed_end: int) -> int | None:
    minimum = start + max((proposed_end - start) // 3, 1)
    for index in range(proposed_end - 1, minimum - 1, -1):
        if value[index] in SENTENCE_ENDINGS:
            return index + 1
    return None


def _unique_flags(flags: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(flag for flag in flags if flag))
