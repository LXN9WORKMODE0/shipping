from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Any

from shipping_pipeline.markup import strip_html_markup


SOURCE_PATH = "normalized/document.md"
H1_PATTERN = re.compile(r"^#\s+(.+?)\s*$")
MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
MIN_TITLE_MATCH_SCORE = 0.72
MIN_TITLE_MATCH_MARGIN = 0.15
SINGLE_TITLE_CONFIDENT_SCORE = 0.85
ABSTRACT_TITLES = {"摘要", "文章摘要", "abstract"}
KEYWORD_TITLES = {"关键词", "关键字", "keywords", "keyword", "keywordsindex"}
BACK_MATTER_TITLES = {
    "参考文献",
    "references",
    "bibliography",
    "致谢",
    "acknowledgements",
    "acknowledgments",
    "作者简介",
}
FRONT_MATTER_TITLES = {
    "目录",
    "contents",
    "学位论文版权使用授权书",
    "学位论文原创性声明和使用授权说明",
    "原创性声明",
}
DEGREE_H1_MARKERS = (
    "学位论文",
    "dissertation",
    "degreeofmaster",
    "degreeofdoctor",
    "原创性声明",
)
INLINE_ABSTRACT_PATTERN = re.compile(
    r"^\s*(?:摘\s*要|a\s*b\s*s\s*t\s*r\s*a\s*c\s*t)(?:\s*[:：]\s*|\s+)",
    re.IGNORECASE,
)
INLINE_KEYWORD_PATTERN = re.compile(
    r"^\s*(?:关\s*键\s*[词字]|k\s*e\s*y\s*w\s*o\s*r\s*d\s*s?)(?:\s*[:：]\s*|\s+)",
    re.IGNORECASE,
)
FRONT_METADATA_PATTERN = re.compile(
    r"^\s*(?:中\s*图(?:法)?分?类号|文\s*献标识码|文\s*章编号|doi\b|收稿日期|基金项目|分类号|udc\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceSpan:
    path: str
    start_line: int
    end_line: int
    start_char: int | None = None
    end_char: int | None = None

    def to_dict(self) -> dict[str, int | str]:
        payload: dict[str, int | str] = {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }
        if self.start_char is not None:
            payload["start_char"] = self.start_char
        if self.end_char is not None:
            payload["end_char"] = self.end_char
        return payload


@dataclass(frozen=True)
class Numbering:
    depth: int
    ordinal_path: tuple[int, ...]
    label: str


@dataclass(frozen=True)
class DocumentSegment:
    title: str
    start_line: int
    end_line: int
    h1_line: int | None
    match_score: float = 0.0
    title_level: int | None = 1
    companion_h1_lines: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "h1_line": self.h1_line,
            "match_score": self.match_score,
            "title_level": self.title_level,
            "companion_h1_lines": list(self.companion_h1_lines),
        }


@dataclass(frozen=True)
class HeadingNode:
    node_id: str
    raw_title: str
    clean_title: str
    depth: int
    source: str
    start_line: int
    end_line: int
    ordinal_path: tuple[int, ...] = ()
    confidence_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "title_raw": self.raw_title,
            "title_norm": self.clean_title,
            "depth": self.depth,
            "source": self.source,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "ordinal_path": list(self.ordinal_path),
            "confidence_flags": list(self.confidence_flags),
        }


@dataclass(frozen=True)
class Region:
    role: str
    span: SourceSpan
    heading_path: tuple[str, ...] = ()
    included_in_materials: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            **self.span.to_dict(),
            "heading_path": list(self.heading_path),
            "included_in_materials": self.included_in_materials,
        }


@dataclass(frozen=True)
class LineAssignment:
    line_number: int
    role: str | None
    included_in_materials: bool
    assignment_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "role": self.role,
            "included_in_materials": self.included_in_materials,
            "assignment_count": self.assignment_count,
        }


@dataclass(frozen=True)
class DocumentMap:
    paper_id: str
    paper_title: str
    selected_segment: DocumentSegment | None
    headings: tuple[HeadingNode, ...]
    regions: tuple[Region, ...]
    line_ledger: tuple[LineAssignment, ...]
    quality_label: str
    issues: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class _HeadingCandidate:
    raw_title: str
    level: int
    source: str
    start_line: int
    depth: int
    ordinal_path: tuple[int, ...]


def build_document_map(paper_id: str, lines: list[str]) -> DocumentMap:
    segments = split_h1_segments(lines)
    degree_document = _is_degree_document(segments, lines)
    selected, selection_issues = select_document_segment(
        paper_id,
        segments,
        allow_equivalent_ties=degree_document,
    )
    if any(issue["severity"] == "error" for issue in selection_issues):
        nested_selected, nested_issues = select_nested_document_segment(
            paper_id,
            lines,
        )
        if nested_selected is not None:
            selected = nested_selected
            selection_issues = nested_issues
    issues = list(selection_issues)

    if selected is None:
        return DocumentMap(
            paper_id=paper_id,
            paper_title=paper_id,
            selected_segment=None,
            headings=(),
            regions=(),
            line_ledger=(),
            quality_label="red",
            issues=tuple(issues),
        )

    selected, scope_issues = _expand_document_scope(selected, segments, lines)
    issues.extend(scope_issues)

    if not _segment_has_visible_content(selected, lines):
        issues.append(
            _issue(
                "parse.empty_selected_document",
                "选中的目标文档范围没有可用于解析的正文。",
                severity="error",
                details={"segment": selected.to_dict()},
            )
        )

    headings, heading_issues = _parse_headings(selected, lines)
    issues.extend(heading_issues)
    regions, region_issues = _build_regions(selected, headings, lines)
    issues.extend(region_issues)
    line_ledger, ledger_issues = _build_line_ledger(selected, headings, regions, lines)
    issues.extend(ledger_issues)
    quality_label = _quality_label(issues)
    fuzzy_title = any(issue["code"] == "parse.document_title_fuzzy_match" for issue in issues)
    return DocumentMap(
        paper_id=paper_id,
        paper_title=paper_id if fuzzy_title else selected.title or paper_id,
        selected_segment=selected,
        headings=tuple(headings),
        regions=tuple(regions),
        line_ledger=tuple(line_ledger),
        quality_label=quality_label,
        issues=tuple(issues),
    )


def split_h1_segments(lines: list[str]) -> list[DocumentSegment]:
    starts: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, start=1):
        match = H1_PATTERN.match(line)
        if match:
            title = _normalize_whitespace(match.group(1))
            if _is_valid_h1_title(title):
                starts.append((line_number, title))

    if not starts:
        return [
            DocumentSegment(
                title="",
                start_line=1,
                end_line=len(lines),
                h1_line=None,
                title_level=None,
            )
        ]

    segments: list[DocumentSegment] = []
    for index, (start_line, title) in enumerate(starts):
        end_line = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(lines)
        segments.append(
            DocumentSegment(
                title=title,
                start_line=start_line,
                end_line=end_line,
                h1_line=start_line,
                title_level=1,
            )
        )
    return segments


def select_nested_document_segment(
    paper_id: str,
    lines: list[str],
) -> tuple[DocumentSegment | None, list[dict[str, Any]]]:
    candidates: list[tuple[int, int, str, float]] = []
    for line_number, line in enumerate(lines, start=1):
        match = MARKDOWN_HEADING_PATTERN.match(line)
        if not match:
            continue
        title = _normalize_whitespace(match.group(2))
        if not _is_valid_h1_title(title):
            continue
        candidates.append(
            (
                line_number,
                len(match.group(1)),
                title,
                _title_match_score(paper_id, title),
            )
        )
    if not candidates:
        return None, []

    ranked = sorted(candidates, key=lambda item: item[3], reverse=True)
    best = ranked[0]
    second_score = ranked[1][3] if len(ranked) > 1 else 0.0
    tied = [candidate for candidate in ranked if candidate[3] == best[3]]
    if (
        best[3] < MIN_TITLE_MATCH_SCORE
        or len(tied) > 1
        or best[3] - second_score < MIN_TITLE_MATCH_MARGIN
    ):
        return None, []

    start_line, level, title, match_score = best
    end_line = len(lines)
    for line_number in range(start_line + 1, len(lines) + 1):
        match = MARKDOWN_HEADING_PATTERN.match(lines[line_number - 1])
        if match and len(match.group(1)) <= level:
            end_line = line_number - 1
            break
    selected = DocumentSegment(
        title=title,
        start_line=start_line,
        end_line=end_line,
        h1_line=start_line,
        match_score=match_score,
        title_level=level,
    )
    return selected, [
        _issue(
            "parse.document_title_non_h1_match",
            "目标论文标题位于非一级 Markdown 标题，已按同级或更高层级标题确定严格范围。",
            details={
                "paper_id": paper_id,
                "selected": selected.to_dict(),
            },
        )
    ]


def select_document_segment(
    paper_id: str,
    segments: list[DocumentSegment],
    allow_equivalent_ties: bool = False,
) -> tuple[DocumentSegment | None, list[dict[str, Any]]]:
    if not segments:
        return None, [
            _issue(
                "parse.empty_selected_document",
                "Markdown 中没有可供选择的文档范围。",
                severity="error",
            )
        ]

    if len(segments) == 1:
        segment = segments[0]
        issues: list[dict[str, Any]] = []
        if segment.h1_line is None:
            issues.append(
                _issue(
                    "parse.no_h1",
                    "Markdown 中没有一级标题，目标范围按整篇文档记录。",
                    details={"start_line": segment.start_line, "end_line": segment.end_line},
                )
            )
            return segment, issues
        segment = replace(segment, match_score=_title_match_score(paper_id, segment.title))
        if segment.match_score < MIN_TITLE_MATCH_SCORE:
            issues.append(
                _issue(
                    "parse.document_identity_mismatch",
                    "唯一一级标题与预期论文身份不匹配，拒绝发布材料卡。",
                    severity="error",
                    details={
                        "paper_id": paper_id,
                        "selected": segment.to_dict(),
                        "minimum_score": MIN_TITLE_MATCH_SCORE,
                    },
                )
            )
        elif segment.match_score < SINGLE_TITLE_CONFIDENT_SCORE:
            issues.append(
                _issue(
                    "parse.document_title_fuzzy_match",
                    "唯一一级标题与预期论文身份仅为模糊匹配，需要人工复核。",
                    details={"paper_id": paper_id, "selected": segment.to_dict()},
                )
            )
        return segment, issues

    scored = [replace(segment, match_score=_title_match_score(paper_id, segment.title)) for segment in segments]
    ranked = sorted(scored, key=lambda item: item.match_score, reverse=True)
    best = ranked[0]
    second_score = ranked[1].match_score
    tied = [segment for segment in ranked if segment.match_score == best.match_score]
    equivalent_tie = (
        allow_equivalent_ties
        and len(tied) > 1
        and len({_canonical_title(segment.title) for segment in tied}) == 1
    )
    if best.match_score < MIN_TITLE_MATCH_SCORE or (
        best.match_score - second_score < MIN_TITLE_MATCH_MARGIN and not equivalent_tie
    ):
        return None, [
            _issue(
                "parse.document_scope_ambiguous",
                "Markdown 包含多个一级标题，但无法唯一确定目标论文范围。",
                severity="error",
                details={
                    "paper_id": paper_id,
                    "candidates": [segment.to_dict() for segment in ranked],
                    "minimum_score": MIN_TITLE_MATCH_SCORE,
                    "minimum_margin": MIN_TITLE_MATCH_MARGIN,
                },
            )
        ]

    issues = []
    if best.match_score < 1.0:
        issues.append(
            _issue(
                "parse.document_title_fuzzy_match",
                "目标论文范围通过标题相似度唯一确定，需要人工复核。",
                details={"paper_id": paper_id, "selected": best.to_dict()},
            )
        )
    return best, issues


def _expand_document_scope(
    selected: DocumentSegment,
    segments: list[DocumentSegment],
    lines: list[str],
) -> tuple[DocumentSegment, list[dict[str, Any]]]:
    if selected.h1_line is None:
        return selected, []
    if selected.title_level != 1:
        return selected, []
    if len(segments) == 1:
        return replace(selected, start_line=1, end_line=len(lines)), []
    if _is_degree_document(segments, lines):
        return replace(selected, start_line=1, end_line=len(lines)), []
    companion = _bilingual_front_matter_companion(selected, segments, lines)
    if companion is not None:
        expanded = replace(
            selected,
            end_line=companion.end_line,
            companion_h1_lines=(companion.h1_line,) if companion.h1_line else (),
        )
        return expanded, [
            _issue(
                "parse.bilingual_title_scope_merged",
                "检测到双语题名与摘要结构，已将相邻外文题名段并入同一论文范围。",
                details={
                    "selected": selected.to_dict(),
                    "companion": companion.to_dict(),
                    "expanded": expanded.to_dict(),
                },
            )
        ]
    return selected, []


def _bilingual_front_matter_companion(
    selected: DocumentSegment,
    segments: list[DocumentSegment],
    lines: list[str],
) -> DocumentSegment | None:
    try:
        index = next(
            index
            for index, segment in enumerate(segments)
            if segment.start_line == selected.start_line
        )
    except StopIteration:
        return None
    if index + 1 >= len(segments):
        return None
    companion = segments[index + 1]
    if (
        _segment_has_body_heading(selected, lines)
        or not _segment_has_abstract(selected, lines)
        or not _segment_has_abstract(companion, lines)
        or not _segment_has_body_heading(companion, lines)
    ):
        return None
    return companion


def _segment_has_abstract(segment: DocumentSegment, lines: list[str]) -> bool:
    lower = max(segment.start_line, 1)
    upper = min(segment.end_line, len(lines))
    for line in lines[lower - 1:upper]:
        cleaned = strip_html_markup(line)
        if INLINE_ABSTRACT_PATTERN.match(cleaned):
            return True
        heading = MARKDOWN_HEADING_PATTERN.match(cleaned)
        if heading and canonical_heading_title(heading.group(2)) in ABSTRACT_TITLES:
            return True
    return False


def _segment_has_body_heading(segment: DocumentSegment, lines: list[str]) -> bool:
    special_titles = (
        ABSTRACT_TITLES
        | KEYWORD_TITLES
        | FRONT_MATTER_TITLES
        | BACK_MATTER_TITLES
    )
    lower = max(segment.start_line, 1)
    upper = min(segment.end_line, len(lines))
    for line in lines[lower - 1:upper]:
        heading = MARKDOWN_HEADING_PATTERN.match(line)
        if not heading or len(heading.group(1)) < 2:
            continue
        if canonical_heading_title(heading.group(2)) not in special_titles:
            return True
    return False


def _is_degree_document(segments: list[DocumentSegment], lines: list[str]) -> bool:
    canonical_titles = [canonical_heading_title(segment.title) for segment in segments]
    has_degree_marker = any(
        marker in title
        for title in canonical_titles
        for marker in DEGREE_H1_MARKERS
    )
    if not has_degree_marker:
        return False

    for segment in segments:
        numbering = parse_numbering(segment.title)
        if numbering is not None and numbering.depth == 1:
            return True

    last_h1_line = max((segment.h1_line or 0) for segment in segments)
    for line in lines[last_h1_line:]:
        match = MARKDOWN_HEADING_PATTERN.match(line)
        if not match or len(match.group(1)) == 1:
            continue
        numbering = parse_numbering(match.group(2))
        if numbering is not None and numbering.depth == 1:
            return True
    return False


def _is_valid_h1_title(title: str) -> bool:
    return len(_canonical_title(title)) >= 2


def parse_numbering(title: str) -> Numbering | None:
    normalized = _normalize_whitespace(strip_html_markup(title))
    chapter = re.match(r"^第\s*([一二两三四五六七八九十百零\d]+)\s*章", normalized)
    if chapter:
        value = _number_to_int(chapter.group(1))
        if value is not None and 0 <= value <= 99:
            return Numbering(depth=1, ordinal_path=(value,), label=chapter.group(0))

    chinese = re.match(r"^([一二两三四五六七八九十百零]+)[、.．]\s*\S", normalized)
    if chinese:
        value = _number_to_int(chinese.group(1))
        if value is not None and 0 <= value <= 99:
            return Numbering(depth=1, ordinal_path=(value,), label=chinese.group(1))

    decimal = re.match(r"^(\d{1,2}(?:[.．]\d{1,2})+)(?=\s|、|$)", normalized)
    if decimal:
        values = tuple(int(part) for part in re.split(r"[.．]", decimal.group(1)))
        if all(0 <= value <= 99 for value in values):
            return Numbering(depth=len(values), ordinal_path=values, label=decimal.group(1))

    root = re.match(r"^(\d{1,2})(?:\s+|[、.．]\s*)\S", normalized)
    if root:
        value = int(root.group(1))
        return Numbering(depth=1, ordinal_path=(value,), label=root.group(1))
    return None


def _parse_headings(
    segment: DocumentSegment,
    lines: list[str],
) -> tuple[list[HeadingNode], list[dict[str, Any]]]:
    markdown_rows: list[tuple[int, int, str, Numbering | None]] = []
    plain_rows: list[tuple[int, str, Numbering]] = []
    lower = max(segment.start_line, 1)
    upper = min(segment.end_line, len(lines))

    markdown_line_numbers: set[int] = set()
    for line_number in range(lower, upper + 1):
        line = lines[line_number - 1]
        markdown = MARKDOWN_HEADING_PATTERN.match(line)
        if markdown:
            markdown_line_numbers.add(line_number)
            level = len(markdown.group(1))
            if (
                line_number == segment.h1_line
                or line_number in segment.companion_h1_lines
            ):
                continue
            title = _normalize_whitespace(markdown.group(2))
            markdown_rows.append((line_number, level, title, parse_numbering(title)))

    excluded_plain_lines = _plain_heading_exclusion_lines(markdown_rows, upper)
    for line_number in range(lower, upper + 1):
        if line_number in markdown_line_numbers or line_number in excluded_plain_lines:
            continue
        line = lines[line_number - 1]
        plain = _plain_heading_numbering(line_number, lines, lower, upper)
        if plain is not None:
            plain_rows.append((line_number, _normalize_whitespace(line), plain))

    base_markdown_level = min((level for _, level, _, _ in markdown_rows), default=2)
    candidates: list[_HeadingCandidate] = []
    for line_number, level, title, numbering in markdown_rows:
        depth = numbering.depth if numbering is not None else max(level - base_markdown_level + 1, 1)
        candidates.append(
            _HeadingCandidate(
                raw_title=title,
                level=level,
                source="markdown",
                start_line=line_number,
                depth=depth,
                ordinal_path=numbering.ordinal_path if numbering is not None else (),
            )
        )

    root_ordinals = {
        numbering.ordinal_path[0]
        for _, _, _, numbering in markdown_rows
        if numbering is not None and numbering.depth == 1
    }
    root_ordinals.update(
        numbering.ordinal_path[0]
        for _, _, numbering in plain_rows
        if numbering.depth == 1
    )
    for line_number, title, numbering in plain_rows:
        root = numbering.ordinal_path[0]
        supported = numbering.depth == 1 and (root - 1 in root_ordinals or root + 1 in root_ordinals)
        if not supported:
            continue
        candidates.append(
            _HeadingCandidate(
                raw_title=title,
                level=2,
                source="plain",
                start_line=line_number,
                depth=numbering.depth,
                ordinal_path=numbering.ordinal_path,
            )
        )

    candidates.sort(key=lambda item: item.start_line)
    headings: list[HeadingNode] = []
    for index, candidate in enumerate(candidates):
        next_start = candidates[index + 1].start_line if index + 1 < len(candidates) else segment.end_line + 1
        headings.append(
            HeadingNode(
                node_id=f"section_{index + 1:03d}",
                raw_title=candidate.raw_title,
                clean_title=_clean_heading_title(candidate.raw_title),
                depth=candidate.depth,
                source=candidate.source,
                start_line=candidate.start_line,
                end_line=next_start - 1,
                ordinal_path=candidate.ordinal_path,
                confidence_flags=("heading.inferred",) if candidate.source == "plain" else (),
            )
        )

    inferred_count = sum(heading.source == "plain" for heading in headings)
    issues: list[dict[str, Any]] = []
    if inferred_count:
        issues.append(
            _issue(
                "parse.inferred_headings",
                "部分结构标题来自普通编号行推断，需要人工复核。",
                details={"inferred_heading_count": inferred_count},
            )
        )
    return headings, issues


def _plain_heading_exclusion_lines(
    markdown_rows: list[tuple[int, int, str, Numbering | None]],
    segment_end: int,
) -> set[int]:
    excluded: set[int] = set()
    special_titles = ABSTRACT_TITLES | KEYWORD_TITLES | FRONT_MATTER_TITLES | BACK_MATTER_TITLES
    for index, (line_number, _, title, _) in enumerate(markdown_rows):
        if canonical_heading_title(title) not in special_titles:
            continue
        next_markdown = markdown_rows[index + 1][0] if index + 1 < len(markdown_rows) else segment_end + 1
        excluded.update(range(line_number, next_markdown))
        if canonical_heading_title(title) in BACK_MATTER_TITLES:
            excluded.update(range(line_number, segment_end + 1))
            break
    return excluded


def _plain_heading_numbering(
    line_number: int,
    lines: list[str],
    lower: int,
    upper: int,
) -> Numbering | None:
    stripped = _normalize_whitespace(lines[line_number - 1])
    if not stripped or len(stripped) > 60:
        return None
    if stripped.endswith(("。", "！", "？", "!", "?", "；", ";", "，", ",")):
        return None
    previous_blank = line_number == lower or not lines[line_number - 2].strip()
    next_blank = line_number == upper or not lines[line_number].strip()
    if not (previous_blank or next_blank):
        return None
    return parse_numbering(stripped)


def _clean_heading_title(value: str) -> str:
    return _normalize_whitespace(strip_html_markup(value))


def canonical_heading_title(value: str) -> str:
    without_html = strip_html_markup(value)
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", without_html).lower()


def _build_regions(
    segment: DocumentSegment,
    headings: list[HeadingNode],
    lines: list[str],
) -> tuple[list[Region], list[dict[str, Any]]]:
    content_start = segment.start_line + (1 if segment.h1_line == segment.start_line else 0)
    content_end = min(segment.end_line, len(lines))
    issues: list[dict[str, Any]] = []
    regions: list[Region] = []

    back_marker = _find_back_matter_marker(segment, headings, lines)
    material_end = back_marker[0] - 1 if back_marker is not None else content_end
    material_headings = [
        heading
        for heading in headings
        if back_marker is None or heading.start_line < back_marker[0]
    ]

    first_heading_start = material_headings[0].start_line if material_headings else material_end + 1
    if content_start <= first_heading_start - 1:
        front_regions, front_issues = _classify_front_range(
            content_start,
            first_heading_start - 1,
            lines,
        )
        if material_headings or any(region.role in {"abstract", "keywords"} for region in front_regions):
            regions.extend(front_regions)
            issues.extend(front_issues)

    body_headings: list[HeadingNode] = []
    heading_paths: dict[str, tuple[str, ...]] = {}
    path_stack: list[str] = []
    degree_scope = _scope_has_degree_markers(segment, lines)
    first_numbered_body_line = next(
        (
            heading.start_line
            for heading in material_headings
            if heading.depth == 1
            and heading.ordinal_path
            and canonical_heading_title(heading.clean_title)
            not in ABSTRACT_TITLES | KEYWORD_TITLES | FRONT_MATTER_TITLES
        ),
        None,
    )
    for heading in material_headings:
        canonical = canonical_heading_title(heading.clean_title)
        end_line = min(heading.end_line, material_end)
        if end_line < heading.start_line:
            continue
        if (
            degree_scope
            and first_numbered_body_line is not None
            and heading.start_line < first_numbered_body_line
            and canonical not in ABSTRACT_TITLES | KEYWORD_TITLES
        ):
            regions.append(
                Region(
                    role="front_matter",
                    span=SourceSpan(SOURCE_PATH, heading.start_line, end_line),
                    heading_path=(heading.clean_title,),
                    included_in_materials=False,
                )
            )
            continue
        if canonical in ABSTRACT_TITLES:
            regions.append(
                Region(
                    role="abstract",
                    span=SourceSpan(SOURCE_PATH, heading.start_line, end_line),
                    heading_path=(heading.clean_title,),
                    included_in_materials=True,
                )
            )
            continue
        if canonical in KEYWORD_TITLES:
            regions.append(
                Region(
                    role="keywords",
                    span=SourceSpan(SOURCE_PATH, heading.start_line, end_line),
                    heading_path=(heading.clean_title,),
                    included_in_materials=False,
                )
            )
            continue
        if canonical in FRONT_MATTER_TITLES:
            regions.append(
                Region(
                    role="front_matter",
                    span=SourceSpan(SOURCE_PATH, heading.start_line, end_line),
                    heading_path=(heading.clean_title,),
                    included_in_materials=False,
                )
            )
            continue

        depth = max(heading.depth, 1)
        if depth > len(path_stack) + 1:
            path_stack = []
        else:
            path_stack = path_stack[: depth - 1]
        path_stack.append(heading.clean_title)
        heading_path = tuple(path_stack)
        heading_paths[heading.node_id] = heading_path
        body_headings.append(heading)
        regions.append(
            Region(
                role="body",
                span=SourceSpan(SOURCE_PATH, heading.start_line, end_line),
                heading_path=heading_path,
                included_in_materials=True,
            )
        )

    if not body_headings and material_end >= content_start:
        occupied = _covered_line_numbers(regions, included_only=False)
        unoccupied = [
            line_number
            for line_number in range(content_start, material_end + 1)
            if line_number not in occupied and lines[line_number - 1].strip()
        ]
        if unoccupied:
            regions.append(
                Region(
                    role="body",
                    span=SourceSpan(SOURCE_PATH, min(unoccupied), max(unoccupied)),
                    included_in_materials=True,
                )
            )
        body_regions = [region for region in regions if region.role == "body"]
        if body_regions:
            issues.append(
                _issue(
                    "parse.no_structured_body",
                    "目标论文正文没有可用标题结构，正文按未结构化区域记录。",
                    details={
                        "start_line": min(region.span.start_line for region in body_regions),
                        "end_line": max(region.span.end_line for region in body_regions),
                    },
                )
            )

    if back_marker is not None:
        regions.append(
            Region(
                role="back_matter",
                span=SourceSpan(SOURCE_PATH, back_marker[0], content_end),
                heading_path=(back_marker[1],),
                included_in_materials=False,
            )
        )

    regions.sort(key=lambda item: (item.span.start_line, item.span.end_line, item.role))
    return regions, issues


def _classify_front_range(
    start_line: int,
    end_line: int,
    lines: list[str],
) -> tuple[list[Region], list[dict[str, Any]]]:
    if end_line < start_line:
        return [], []
    regions: list[Region] = []
    issues: list[dict[str, Any]] = []
    seen_abstract = False
    seen_keywords = False
    front_spans: list[tuple[int, int]] = []
    for paragraph_start, paragraph_end in _paragraph_ranges(start_line, end_line, lines):
        first_line = lines[paragraph_start - 1]
        if INLINE_ABSTRACT_PATTERN.match(first_line):
            role = "abstract"
            seen_abstract = True
            seen_keywords = False
        elif INLINE_KEYWORD_PATTERN.match(first_line):
            role = "keywords"
            seen_keywords = True
        elif FRONT_METADATA_PATTERN.match(first_line):
            role = "front_matter"
        elif seen_abstract and not seen_keywords:
            role = "abstract"
        elif seen_keywords:
            role = "body"
        else:
            role = "front_matter"

        included = role in {"abstract", "body"}
        heading_path = ("摘要",) if role == "abstract" else ()
        regions.append(
            Region(
                role=role,
                span=SourceSpan(SOURCE_PATH, paragraph_start, paragraph_end),
                heading_path=heading_path,
                included_in_materials=included,
            )
        )
        if role == "front_matter":
            front_spans.append((paragraph_start, paragraph_end))

    if front_spans:
        issues.append(
            _issue(
                "parse.unclassified_front_matter",
                "正文标题前存在未分类或元数据内容，已逐行归入前置区域且不生成材料卡。",
                details={"spans": [{"start_line": start, "end_line": end} for start, end in front_spans]},
            )
        )
    return regions, issues


def _paragraph_ranges(start_line: int, end_line: int, lines: list[str]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    current_start: int | None = None
    for line_number in range(start_line, end_line + 1):
        if lines[line_number - 1].strip():
            if current_start is None:
                current_start = line_number
            continue
        if current_start is not None:
            ranges.append((current_start, line_number - 1))
            current_start = None
    if current_start is not None:
        ranges.append((current_start, end_line))
    return ranges


def _find_back_matter_marker(
    segment: DocumentSegment,
    headings: list[HeadingNode],
    lines: list[str],
) -> tuple[int, str] | None:
    candidates = [
        (heading.start_line, heading.clean_title)
        for heading in headings
        if canonical_heading_title(heading.clean_title) in BACK_MATTER_TITLES
    ]
    lower = max(segment.start_line, 1)
    upper = min(segment.end_line, len(lines))
    for line_number in range(lower, upper + 1):
        stripped = lines[line_number - 1].strip()
        markdown = MARKDOWN_HEADING_PATTERN.match(stripped)
        title = markdown.group(2) if markdown else stripped
        if len(title) <= 80 and canonical_heading_title(title) in BACK_MATTER_TITLES:
            candidates.append((line_number, _clean_heading_title(title)))
    return min(candidates, key=lambda item: item[0]) if candidates else None


def _build_line_ledger(
    segment: DocumentSegment,
    headings: list[HeadingNode],
    regions: list[Region],
    lines: list[str],
) -> tuple[list[LineAssignment], list[dict[str, Any]]]:
    marker_lines = {heading.start_line for heading in headings}
    if segment.h1_line is not None:
        marker_lines.add(segment.h1_line)
    marker_lines.update(segment.companion_h1_lines)
    ledger: list[LineAssignment] = []
    unassigned: list[int] = []
    overlapping: list[int] = []
    lower = max(segment.start_line, 1)
    upper = min(segment.end_line, len(lines))
    for line_number in range(lower, upper + 1):
        if not lines[line_number - 1].strip():
            continue
        if line_number in marker_lines:
            ledger.append(LineAssignment(line_number, "scope_marker", False, 1))
            continue
        matches = [
            region
            for region in regions
            if region.span.start_line <= line_number <= region.span.end_line
        ]
        assignment_count = len(matches)
        if assignment_count == 0:
            unassigned.append(line_number)
            ledger.append(LineAssignment(line_number, None, False, 0))
        elif assignment_count == 1:
            ledger.append(
                LineAssignment(
                    line_number,
                    matches[0].role,
                    matches[0].included_in_materials,
                    1,
                )
            )
        else:
            overlapping.append(line_number)
            ledger.append(LineAssignment(line_number, None, False, assignment_count))

    issues: list[dict[str, Any]] = []
    if unassigned:
        issues.append(
            _issue(
                "coverage.unassigned_source_line",
                "选定文档中存在未归入任何区域的非空原文行。",
                severity="error",
                details={"line_numbers": unassigned},
            )
        )
    if overlapping:
        issues.append(
            _issue(
                "coverage.overlapping_source_line",
                "选定文档中存在同时归入多个区域的非空原文行。",
                severity="error",
                details={"line_numbers": overlapping},
            )
        )
    return ledger, issues


def _range_has_visible_content(start_line: int, end_line: int, lines: list[str]) -> bool:
    return any(lines[index - 1].strip() for index in range(start_line, end_line + 1))


def _scope_has_degree_markers(segment: DocumentSegment, lines: list[str]) -> bool:
    lower = max(segment.start_line, 1)
    upper = min(segment.end_line, len(lines))
    for line in lines[lower - 1 : upper]:
        match = H1_PATTERN.match(line)
        if not match:
            continue
        canonical = canonical_heading_title(match.group(1))
        if any(marker in canonical for marker in DEGREE_H1_MARKERS):
            return True
    return False


def _covered_line_numbers(regions: list[Region], included_only: bool) -> set[int]:
    covered: set[int] = set()
    for region in regions:
        if included_only and not region.included_in_materials:
            continue
        covered.update(range(region.span.start_line, region.span.end_line + 1))
    return covered


def _number_to_int(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value == "十":
        return 10
    if "十" in value:
        tens_text, ones_text = value.split("十", 1)
        tens = digits.get(tens_text, 1 if not tens_text else -1)
        ones = digits.get(ones_text, 0 if not ones_text else -1)
        if tens < 0 or ones < 0:
            return None
        return tens * 10 + ones
    if value in digits:
        return digits[value]
    return None


def _title_match_score(target: str, candidate: str) -> float:
    normalized_target = _canonical_title(target)
    normalized_candidate = _canonical_title(candidate)
    if not normalized_target or not normalized_candidate:
        return 0.0
    if normalized_target == normalized_candidate:
        return 1.0
    shorter = min(len(normalized_target), len(normalized_candidate))
    if shorter >= 6 and (normalized_target in normalized_candidate or normalized_candidate in normalized_target):
        return 0.85
    ratio = SequenceMatcher(None, normalized_target, normalized_candidate).ratio()
    return ratio if ratio >= MIN_TITLE_MATCH_SCORE else 0.0


def _canonical_title(value: str) -> str:
    without_html = strip_html_markup(value)
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", without_html).lower()


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("　", " ")).strip()


def _segment_has_visible_content(segment: DocumentSegment, lines: list[str]) -> bool:
    start = segment.start_line + (1 if segment.h1_line == segment.start_line else 0)
    end = min(segment.end_line, len(lines))
    return any(line.strip() for line in lines[max(start - 1, 0) : end])


def _quality_label(issues: list[dict[str, Any]]) -> str:
    if any(issue.get("severity") == "error" for issue in issues):
        return "red"
    if issues:
        return "silver"
    return "gold"


def _issue(
    code: str,
    message: str,
    severity: str = "warning",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "stage": "parse",
        "code": code,
        "message": message,
        "severity": severity,
        "details": details or {},
    }
