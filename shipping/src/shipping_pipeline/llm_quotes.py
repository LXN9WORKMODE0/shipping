from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Iterable


MAX_QUOTE_CANDIDATE_CHARS = 240
MODEL_QUOTE_CANDIDATE_FIELDS = ("quote_id", "material_id", "text")
_LINE_RE = re.compile(r"[^\r\n]+")
_STRONG_BREAKS = "。！？.!?；;"
_WEAK_BREAKS = "，,、 "


class QuoteCandidateError(ValueError):
    pass


def build_quote_candidates(projected_cards: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    cards = list(projected_cards)
    if not cards:
        raise QuoteCandidateError("引文候选不能基于空 Card 集合生成。")
    candidates: list[dict[str, Any]] = []
    for card in cards:
        material_id = str(card.get("material_id", ""))
        extract = str(card.get("extract", ""))
        if not material_id or not extract:
            raise QuoteCandidateError("Card 缺少 material_id 或 extract。")
        if card.get("content_kind") == "image":
            continue
        for line_match in _LINE_RE.finditer(extract):
            line_start, line_end = _trim_span(extract, line_match.start(), line_match.end())
            cursor = line_start
            while cursor < line_end:
                cursor = _skip_space(extract, cursor, line_end)
                if cursor >= line_end:
                    break
                end = _choose_end(extract, cursor, line_end)
                candidate_start, candidate_end = _trim_span(extract, cursor, end)
                if candidate_start < candidate_end:
                    text = extract[candidate_start:candidate_end]
                    identity = f"{material_id}\0{candidate_start}\0{candidate_end}\0{text}"
                    candidates.append(
                        {
                            "quote_id": "quote_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                            "material_id": material_id,
                            "text": text,
                            "start_char": candidate_start,
                            "end_char": candidate_end,
                        }
                    )
                cursor = end
    validate_quote_candidates(cards, candidates)
    return candidates


def project_quote_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    projected: list[dict[str, str]] = []
    for candidate in candidates:
        row = {
            "quote_id": str(candidate["quote_id"]),
            "material_id": str(candidate["material_id"]),
            "text": str(candidate["text"]),
        }
        if tuple(row) != MODEL_QUOTE_CANDIDATE_FIELDS:
            raise AssertionError("模型引文候选字段顺序发生内部漂移。")
        projected.append(row)
    return projected


def validate_quote_candidates(
    projected_cards: Iterable[dict[str, Any]],
    candidates: Iterable[dict[str, Any]],
) -> None:
    cards = list(projected_cards)
    rows = list(candidates)
    cards_by_id = {str(card.get("material_id", "")): card for card in cards}
    if len(cards_by_id) != len(cards) or "" in cards_by_id:
        raise QuoteCandidateError("Card material_id 为空或重复。")
    quote_ids = [str(row.get("quote_id", "")) for row in rows]
    duplicate_ids = sorted(quote_id for quote_id, count in Counter(quote_ids).items() if count != 1)
    if "" in quote_ids or duplicate_ids:
        raise QuoteCandidateError(f"引文候选 quote_id 为空或重复：{duplicate_ids}")

    covered: dict[str, set[int]] = {material_id: set() for material_id in cards_by_id}
    for row in rows:
        material_id = str(row.get("material_id", ""))
        if material_id not in cards_by_id:
            raise QuoteCandidateError(f"引文候选指向未知 Card：{material_id!r}")
        start = row.get("start_char")
        end = row.get("end_char")
        text = row.get("text")
        if type(start) is not int or type(end) is not int or not isinstance(text, str):
            raise QuoteCandidateError("引文候选坐标或文本类型无效。")
        extract = str(cards_by_id[material_id]["extract"])
        if not (0 <= start < end <= len(extract)) or extract[start:end] != text:
            raise QuoteCandidateError(f"引文候选不是 Card extract 的精确坐标切片：{row.get('quote_id')!r}")
        if len(text) > MAX_QUOTE_CANDIDATE_CHARS or "\r" in text or "\n" in text:
            raise QuoteCandidateError(f"引文候选超过长度或跨行：{row.get('quote_id')!r}")
        covered[material_id].update(range(start, end))

    for material_id, card in cards_by_id.items():
        if card.get("content_kind") == "image":
            if covered[material_id]:
                raise QuoteCandidateError(f"图片 Card 不应生成文本引文候选：{material_id!r}")
            continue
        extract = str(card["extract"])
        missing = [index for index, char in enumerate(extract) if not char.isspace() and index not in covered[material_id]]
        if missing:
            raise QuoteCandidateError(
                f"引文候选没有覆盖 Card 的全部非空字符：material_id={material_id!r}, first_char={missing[0]}"
            )


def _choose_end(text: str, start: int, line_end: int) -> int:
    hard_end = min(start + MAX_QUOTE_CANDIDATE_CHARS, line_end)
    if hard_end == line_end:
        return hard_end
    minimum = start + MAX_QUOTE_CANDIDATE_CHARS // 2
    for marks in (_STRONG_BREAKS, _WEAK_BREAKS):
        for index in range(hard_end - 1, minimum - 1, -1):
            if text[index] in marks and not _is_numeric_separator(text, index, line_end):
                return index + 1
    return hard_end


def _is_numeric_separator(text: str, index: int, line_end: int) -> bool:
    return (
        text[index] in ".,"
        and index > 0
        and index + 1 < line_end
        and text[index - 1].isdigit()
        and text[index + 1].isdigit()
    )


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _skip_space(text: str, start: int, end: int) -> int:
    while start < end and text[start].isspace():
        start += 1
    return start
