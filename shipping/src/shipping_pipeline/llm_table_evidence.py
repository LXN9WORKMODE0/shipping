from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable


_LINE_RE = re.compile(r"[^\r\n]+")
_BARE_NUMBER_RE = re.compile(r"^(?:\d{1,3}(?:[, \u00a0]\d{3})+|\d+(?:\.\d+)?)$")
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_PERCENT_UNIT_RE = re.compile(r"[%％]")


@dataclass(frozen=True)
class TablePercentageComposite:
    fact: str
    material_id: str
    header_quote_id: str
    row_quote_id: str
    row_label: str
    column_label: str
    column_index: int
    row_value: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TableRankingScope:
    material_id: str
    header_quote_ids: tuple[str, ...]
    data_row_quote_ids: tuple[str, ...]

    @property
    def header_quote_id(self) -> str:
        return self.header_quote_ids[0]



def build_table_percentage_composites(
    material: dict[str, Any],
    quote_candidates: Iterable[dict[str, Any]],
) -> list[TablePercentageComposite]:
    """从完整表格行中构造可证明的“表头百分比单位 + 数据单元格”关系。"""
    if str(material.get("content_kind", "")) != "table":
        return []
    material_id = str(material.get("material_id", ""))
    extract = str(material.get("extract", ""))
    if not material_id or not extract:
        return []

    rows = _complete_table_rows(material, quote_candidates)

    headers: list[_TableHeader] = []
    for row in rows:
        percent_columns: list[tuple[int, str]] = []
        for index, cell in enumerate(row.cells):
            if index == 0 or not _PERCENT_UNIT_RE.search(cell):
                continue
            label = _column_label(cell)
            if label and _has_semantic_label(label):
                percent_columns.append((index, label))
        if percent_columns:
            headers.append(
                _TableHeader(
                    start=row.start,
                    group_index=row.group_index,
                    quote_id=row.quote_id,
                    column_count=len(row.cells),
                    percent_columns=tuple(percent_columns),
                )
            )

    composites: list[TablePercentageComposite] = []
    for row in rows:
        preceding = [
            header
            for header in headers
            if header.start < row.start
            and header.group_index == row.group_index
            and header.column_count == len(row.cells)
        ]
        if not preceding:
            continue
        header = max(preceding, key=lambda item: item.start)
        row_label = row.cells[0].strip()
        if not row_label or _BARE_NUMBER_RE.fullmatch(_normalize_space(row_label)):
            continue
        for column_index, column_label in header.percent_columns:
            value = _normalize_space(row.cells[column_index])
            if not _BARE_NUMBER_RE.fullmatch(value):
                continue
            composites.append(
                TablePercentageComposite(
                    fact=_normalize_number(value) + "%",
                    material_id=material_id,
                    header_quote_id=header.quote_id,
                    row_quote_id=row.quote_id,
                    row_label=row_label,
                    column_label=column_label,
                    column_index=column_index,
                    row_value=value,
                )
            )
    return composites


def build_table_ranking_scopes(
    material: dict[str, Any],
    quote_candidates: Iterable[dict[str, Any]],
) -> list[TableRankingScope]:
    return [
        scope
        for scope in build_table_numeric_scopes(material, quote_candidates)
        if len(scope.data_row_quote_ids) >= 2
    ]


def build_table_numeric_scopes(
    material: dict[str, Any],
    quote_candidates: Iterable[dict[str, Any]],
) -> list[TableRankingScope]:
    if str(material.get("content_kind", "")) != "table":
        return []
    material_id = str(material.get("material_id", ""))
    rows = _complete_table_rows(material, quote_candidates)
    scopes: list[TableRankingScope] = []
    for group_index in sorted({row.group_index for row in rows}):
        group = sorted(
            (row for row in rows if row.group_index == group_index),
            key=lambda row: row.start,
        )
        if len(group) < 2:
            continue
        headers: list[_TableRow] = []
        for row in group:
            if not _is_semantic_header(row.cells):
                break
            headers.append(row)
        if not headers:
            continue
        remaining = group[len(headers) :]
        data_widths = Counter(
            len(row.cells)
            for row in remaining
            if _is_numeric_data_row(row.cells)
        )
        if not data_widths:
            continue
        data_width = max(
            data_widths,
            key=lambda width: (data_widths[width], width),
        )
        if not _headers_cover_data_width(headers, data_width):
            continue
        data_rows = [
            row
            for row in remaining
            if len(row.cells) == data_width and _is_numeric_data_row(row.cells)
        ]
        if not data_rows:
            continue
        scopes.append(
            TableRankingScope(
                material_id=material_id,
                header_quote_ids=tuple(row.quote_id for row in headers),
                data_row_quote_ids=tuple(row.quote_id for row in data_rows),
            )
        )
    return scopes


def _headers_cover_data_width(
    headers: list[_TableRow],
    data_width: int,
) -> bool:
    if data_width < 2:
        return False
    detail_width = len(headers[-1].cells)
    if detail_width not in {data_width, data_width - 1}:
        return False
    value_columns = data_width - 1
    for header in headers[:-1]:
        width = len(header.cells)
        if width not in {data_width, data_width - 1} and (
            width < 2 or value_columns % width != 0
        ):
            return False
    return True


def table_percentage_candidates_for_claim(
    claim: str,
    material: dict[str, Any],
    quote_candidates: Iterable[dict[str, Any]],
    facts: Iterable[str],
    *,
    allow_unique_row_alias: bool = False,
) -> list[TablePercentageComposite]:
    wanted = {str(fact) for fact in facts if str(fact).endswith("%")}
    all_composites = build_table_percentage_composites(material, quote_candidates)
    selected: list[TablePercentageComposite] = []
    for fact in sorted(wanted):
        matching = [
            composite
            for composite in all_composites
            if composite.fact == fact
            and _claim_associates_column(claim, fact, composite.column_label, all_composites)
            and _row_label_matches_claim(
                composite.row_label,
                claim,
                all_composites,
                allow_unique_alias=allow_unique_row_alias,
            )
        ]
        if not matching:
            continue
        longest_row_label = max(len(_compact_label(row.row_label)) for row in matching)
        selected.extend(
            row
            for row in matching
            if len(_compact_label(row.row_label)) == longest_row_label
        )
    return selected


def resolve_selected_table_percentage_support(
    claim: str,
    material: dict[str, Any],
    quote_candidates: Iterable[dict[str, Any]],
    selected_quote_ids: Iterable[str],
    unsupported_facts: Iterable[str],
) -> set[str]:
    """仅为单一、标签明确且同时选择表头/数据行的百分比事实提供支持。"""
    return {
        row.fact
        for row in resolve_selected_table_percentage_composites(
            claim,
            material,
            quote_candidates,
            selected_quote_ids,
            unsupported_facts,
        )
    }


def resolve_selected_table_percentage_composites(
    claim: str,
    material: dict[str, Any],
    quote_candidates: Iterable[dict[str, Any]],
    selected_quote_ids: Iterable[str],
    unsupported_facts: Iterable[str],
) -> list[TablePercentageComposite]:
    unsupported = {str(fact) for fact in unsupported_facts}
    candidates = table_percentage_candidates_for_claim(
        claim,
        material,
        quote_candidates,
        unsupported,
    )
    candidate_facts = {row.fact for row in candidates}
    if len(candidate_facts) != 1:
        # 一个 Evidence 中有多个由表头补单位的百分比时，必须拆成原子 Evidence。
        return []
    fact = next(iter(candidate_facts))
    selected = {str(quote_id) for quote_id in selected_quote_ids}
    matches = [
        row
        for row in candidates
        if row.fact == fact
        and row.header_quote_id in selected
        and row.row_quote_id in selected
    ]
    identities = {
        (
            row.header_quote_id,
            row.row_quote_id,
            _compact_label(row.row_label),
            _compact_label(row.column_label),
            row.column_index,
        )
        for row in matches
    }
    if len(identities) != 1:
        return []
    identity = next(iter(identities))
    return [
        row
        for row in matches
        if (
            row.header_quote_id,
            row.row_quote_id,
            _compact_label(row.row_label),
            _compact_label(row.column_label),
            row.column_index,
        )
        == identity
    ][:1]


@dataclass(frozen=True)
class _TableRow:
    start: int
    group_index: int
    quote_id: str
    cells: tuple[str, ...]


@dataclass(frozen=True)
class _TableHeader:
    start: int
    group_index: int
    quote_id: str
    column_count: int
    percent_columns: tuple[tuple[int, str], ...]


def _complete_table_rows(
    material: dict[str, Any],
    quote_candidates: Iterable[dict[str, Any]],
) -> list[_TableRow]:
    material_id = str(material.get("material_id", ""))
    extract = str(material.get("extract", ""))
    candidates_by_span: dict[tuple[int, int], dict[str, Any]] = {}
    for candidate in quote_candidates:
        if str(candidate.get("material_id", "")) != material_id:
            continue
        start = candidate.get("start_char")
        end = candidate.get("end_char")
        if type(start) is not int or type(end) is not int:
            continue
        candidates_by_span[(start, end)] = candidate

    rows: list[_TableRow] = []
    group_index = 0
    previous_end = 0
    for match in _LINE_RE.finditer(extract):
        if re.search(r"\r?\n\s*\r?\n", extract[previous_end : match.start()]):
            group_index += 1
        previous_end = match.end()
        start, end = _trim_span(extract, match.start(), match.end())
        candidate = candidates_by_span.get((start, end))
        if candidate is None:
            # 超长表格行会被 quote 生成器拆分。不能恢复完整行时，不建立结构关系。
            continue
        cells = _split_markdown_row(str(candidate.get("text", "")))
        if cells is None or _is_separator_row(cells):
            continue
        rows.append(
            _TableRow(
                start=start,
                group_index=group_index,
                quote_id=str(candidate.get("quote_id", "")),
                cells=tuple(cells),
            )
        )
    return rows


def _is_semantic_header(cells: tuple[str, ...]) -> bool:
    if len(cells) < 2:
        return False
    return sum(_has_semantic_label(cell) for cell in cells) >= 2


def _is_numeric_data_row(cells: tuple[str, ...]) -> bool:
    if len(cells) < 2 or not cells[0].strip():
        return False
    return any(re.search(r"\d", cell) for cell in cells[1:])


def _split_markdown_row(value: str) -> list[str] | None:
    if "|" not in value or "`" in value:
        return None
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            current.append(character)
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    cells.append("".join(current).strip())
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    if len(cells) < 2 or any(not cell for cell in cells):
        return None
    return cells


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(_TABLE_SEPARATOR_CELL_RE.fullmatch(cell.strip()) for cell in cells)


def _column_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"[\(\[【]?\s*%\s*[\)\]】]?", "", normalized)
    return normalized.strip(" :：")


def _has_semantic_label(value: str) -> bool:
    return any(character.isalpha() or "\u4e00" <= character <= "\u9fff" for character in value)


def _claim_associates_column(
    claim: str,
    fact: str,
    column_label: str,
    composites: list[TablePercentageComposite],
) -> bool:
    searchable_claim = _compact_search_text(claim)
    searchable_fact = _compact_search_text(fact)
    fact_positions = [
        match.start() for match in re.finditer(re.escape(searchable_fact), searchable_claim)
    ]
    if len(fact_positions) != 1:
        return False
    fact_position = fact_positions[0]
    labels = {
        _compact_search_text(row.column_label)
        for row in composites
        if row.fact == fact and _compact_search_text(row.column_label)
    }
    preceding = {
        label: searchable_claim.rfind(label, 0, fact_position)
        for label in labels
    }
    nearest_position = max(preceding.values(), default=-1)
    target = _compact_search_text(column_label)
    return nearest_position >= 0 and preceding.get(target) == nearest_position


def _row_label_matches_claim(
    row_label: str,
    claim: str,
    composites: list[TablePercentageComposite],
    *,
    allow_unique_alias: bool,
) -> bool:
    compact_claim = _compact_label(claim)
    compact_row_label = _compact_label(row_label)
    if compact_row_label and compact_row_label in compact_claim:
        return True
    if not allow_unique_alias:
        return False
    aliases = {
        _compact_label(match.group(0))
        for match in re.finditer(
            r"(?<![A-Za-z0-9])[A-Za-z]{1,6}\s*[-_]?\s*\d{1,4}(?![A-Za-z0-9])",
            unicodedata.normalize("NFKC", row_label),
        )
    }
    for alias in aliases:
        if alias not in compact_claim:
            continue
        matching_labels = {
            _compact_label(row.row_label)
            for row in composites
            if alias in _compact_label(row.row_label)
        }
        if matching_labels == {compact_row_label}:
            return True
    return False


def _compact_label(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value)
        if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )


def _compact_search_text(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value)
        if character.isalnum()
        or "\u4e00" <= character <= "\u9fff"
        or character in ".%"
    )


def _normalize_number(value: str) -> str:
    return value.replace(",", "").replace(" ", "").replace("\u00a0", "")


def _normalize_space(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def _trim_span(value: str, start: int, end: int) -> tuple[int, int]:
    while start < end and value[start].isspace():
        start += 1
    while end > start and value[end - 1].isspace():
        end -= 1
    return start, end
