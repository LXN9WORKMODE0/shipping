"""Heading normalization, family detection, and numbering profile heuristics."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

CHINESE_NUMERAL_MAP = {
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
    "十": 10,
}

CIRCLED_DIGITS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
BODY_START_KEYWORDS = {
    "绪论",
    "引言",
    "前言",
    "研究背景",
    "文献综述",
    "研究设计",
    "研究方法",
    "方法",
    "实验",
    "结果",
    "讨论",
    "结论",
    "概述",
}
COMMON_SPACED_TERMS = {
    "摘 要": "摘要",
    "参 考 文 献": "参考文献",
    "目 录": "目录",
    "致 谢": "致谢",
    "附 录": "附录",
    "独 创 性 声 明": "独创性声明",
    "授 权 书": "授权书",
    "作 者 简 介": "作者简介",
}
TRANSLATION_TABLE = str.maketrans(
    {
        "．": ".",
        "。": "。",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "：": ":",
        "　": " ",
        "－": "-",
        "—": "-",
        "–": "-",
        "﹒": ".",
    }
)

SCHEMA_MIN_DENSITY = 0.15


@dataclass(frozen=True)
class HeadingInfo:
    """Semantic information extracted from a heading title."""

    semantic_level: int
    numbering_path: list[int]
    normalized_title: str
    confidence: float
    family: str
    numbering_text: str = ""

    @property
    def title_norm(self) -> str:
        return self.normalized_title

    @property
    def match_kind(self) -> str:
        return self.family

    @property
    def is_numbered(self) -> bool:
        return self.family != "plain"


def normalize_heading_title(title: str) -> str:
    """Normalize punctuation, OCR spacing, and trailing page numbers."""
    normalized = title.translate(TRANSLATION_TABLE)
    normalized = re.sub(r"<sup\b[^>]*>.*?</sup>", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"</?[^>]+>", "", normalized)
    normalized = normalized.replace("\u3000", " ")
    normalized = _remove_page_number_suffix(normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = _collapse_cjk_spaces(normalized)
    normalized = _normalize_common_terms(normalized)
    if should_strip_trailing_page_number(normalized):
        normalized = strip_trailing_page_number(normalized)
    return normalized.strip("[]")


def parse_heading_info(title: str, schema: str = "mixed_unknown") -> HeadingInfo:
    """Parse a heading into numbering family and a provisional semantic level."""
    del schema
    raw = normalize_heading_title(title)

    chapter_match = re.match(r"^第([一二两三四五六七八九十百零\d]+)章(?:\s*[-:、.]?\s*)?", raw)
    if chapter_match:
        value = chinese_to_int(chapter_match.group(1))
        return HeadingInfo(1, [value], _strip_prefix(raw, chapter_match.group(0)), 0.98, "chapter_cn", chapter_match.group(0).strip())

    english_chapter_match = re.match(r"^Chapter\s+(\d+)(?:\s*[-:.]?\s*)?", raw, re.IGNORECASE)
    if english_chapter_match:
        value = int(english_chapter_match.group(1))
        return HeadingInfo(1, [value], _strip_prefix(raw, english_chapter_match.group(0)), 0.98, "chapter_cn", english_chapter_match.group(0).strip())

    decimal_path_match = re.match(r"^(\d+(?:\.\d+)+)(?:[.)、]?\s*|\s+)", raw)
    if decimal_path_match:
        path = [int(piece) for piece in decimal_path_match.group(1).split(".")]
        semantic_level = min(len(path), 5)
        return HeadingInfo(semantic_level, path, _strip_prefix(raw, decimal_path_match.group(0)), 0.96, "decimal_path", decimal_path_match.group(1))

    arabic_main_match = re.match(r"^(\d+)(?:[.、)]\s*|\s+)", raw)
    if arabic_main_match:
        path = [int(arabic_main_match.group(1))]
        return HeadingInfo(1, path, _strip_prefix(raw, arabic_main_match.group(0)), 0.94, "arabic_main", arabic_main_match.group(1))

    cn_main_match = re.match(r"^([一二两三四五六七八九十百零]+)[、.]\s*", raw)
    if cn_main_match:
        value = chinese_to_int(cn_main_match.group(1))
        return HeadingInfo(1, [value], _strip_prefix(raw, cn_main_match.group(0)), 0.95, "cn_main", cn_main_match.group(1))

    cn_paren_match = re.match(r"^\(([一二两三四五六七八九十百零]+)\)\s*", raw)
    if cn_paren_match:
        value = chinese_to_int(cn_paren_match.group(1))
        return HeadingInfo(2, [value], _strip_prefix(raw, cn_paren_match.group(0)), 0.94, "cn_paren", cn_paren_match.group(1))

    arabic_paren_match = re.match(r"^\((\d+)\)\s*", raw)
    if arabic_paren_match:
        value = int(arabic_paren_match.group(1))
        return HeadingInfo(3, [value], _strip_prefix(raw, arabic_paren_match.group(0)), 0.92, "arabic_paren", arabic_paren_match.group(1))

    circled_match = re.match(rf"^([{re.escape(CIRCLED_DIGITS)}])\s*", raw)
    if circled_match:
        value = circled_digit_to_int(circled_match.group(1))
        return HeadingInfo(4, [value], _strip_prefix(raw, circled_match.group(0)), 0.9, "circled", circled_match.group(1))

    confidence = 0.68 if raw in BODY_START_KEYWORDS else _compute_plain_confidence(raw)
    return HeadingInfo(1, [], raw, confidence, "plain", "")


def detect_schema(titles: list[str]) -> str:
    """Detect a coarse numbering profile from heading families."""
    if not titles:
        return "mixed_unknown"

    counts = detect_heading_family_counts(titles)
    total = max(sum(counts.values()), 1)
    has_chapter = counts.get("chapter_cn", 0) > 0
    has_decimal = counts.get("decimal_path", 0) > 0 or counts.get("arabic_main", 0) > 0
    has_cn = counts.get("cn_main", 0) > 0 or counts.get("cn_paren", 0) > 0

    if has_chapter and has_decimal:
        return "chapter_decimal"
    if has_cn and has_decimal:
        return "journal_mixed_cn"
    if has_decimal and (counts.get("decimal_path", 0) + counts.get("arabic_main", 0)) / total >= SCHEMA_MIN_DENSITY:
        return "decimal_only"
    return "mixed_unknown"


def detect_heading_family_counts(titles: list[str]) -> dict[str, int]:
    counter = Counter()
    for title in titles:
        counter[parse_heading_info(title).family] += 1
    return dict(counter)


def is_likely_body_start(title: str, schema: str = "mixed_unknown") -> bool:
    """Return whether a title looks like a body-start candidate."""
    info = parse_heading_info(title, schema)
    if info.family == "chapter_cn":
        return True
    if info.family == "arabic_main":
        return True
    if info.family == "cn_main":
        return True
    return info.normalized_title in BODY_START_KEYWORDS or info.normalized_title.startswith("Chapter ")


def is_level_jump(current: HeadingInfo, parent: HeadingInfo | None, schema: str = "mixed_unknown") -> bool:
    """Return True when a heading family is incompatible with the proposed parent."""
    if parent is None:
        return False
    if _is_compatible_parent(schema, current, parent):
        return False
    expected = family_rank(current, schema)
    parent_rank = family_rank(parent, schema)
    return expected > parent_rank + 1


def is_compatible_parent(profile: str, current: HeadingInfo, parent: HeadingInfo) -> bool:
    """Return whether a parent-child family pairing is valid for a profile."""
    return _is_compatible_parent(profile, current, parent)


def family_rank(info: HeadingInfo, profile: str) -> int:
    """Return a profile-aware rank for a heading family."""
    if info.family == "chapter_cn":
        return 1
    if profile == "journal_mixed_cn":
        if info.family == "cn_main":
            return 1
        if info.family == "cn_paren":
            return 2
        if info.family in {"arabic_main", "decimal_path"}:
            return 3
        if info.family == "arabic_paren":
            return 4
        if info.family == "circled":
            return 5
    if profile == "chapter_decimal":
        if info.family == "decimal_path":
            return min(len(info.numbering_path), 3)
        if info.family == "arabic_main":
            return 2
        if info.family in {"cn_paren", "arabic_paren"}:
            return 4
        if info.family == "circled":
            return 5
    if profile == "decimal_only":
        if info.family == "arabic_main":
            return 1
        if info.family == "decimal_path":
            return min(len(info.numbering_path), 4)
        if info.family == "arabic_paren":
            return 4
        if info.family == "circled":
            return 5
    if info.family == "cn_main":
        return 1
    if info.family == "cn_paren":
        return 2
    if info.family == "arabic_main":
        return 1
    if info.family == "decimal_path":
        return min(len(info.numbering_path), 4)
    if info.family == "arabic_paren":
        return 3
    if info.family == "circled":
        return 4
    return 1


def combine_numbering_path(parent: HeadingInfo | None, current: HeadingInfo) -> list[int]:
    """Combine local numbering with a parent path when the family is relative."""
    if not parent or not parent.numbering_path:
        return list(current.numbering_path)
    if current.family in {"decimal_path", "chapter_cn", "arabic_main", "cn_main"}:
        return list(current.numbering_path)
    if current.numbering_path:
        return list(parent.numbering_path) + list(current.numbering_path)
    return list(parent.numbering_path)


def is_body_start_keyword(title: str) -> bool:
    return normalize_heading_title(title) in BODY_START_KEYWORDS


def has_trailing_page_number(title: str) -> bool:
    return bool(re.search(r"\s+\d+\s*-?\s*$", title.strip()))


def strip_trailing_page_number(title: str) -> str:
    return re.sub(r"\s+\d+\s*-?\s*$", "", title).strip()


def should_strip_trailing_page_number(title: str) -> bool:
    stripped = title.strip()
    if not has_trailing_page_number(stripped):
        return False
    if re.match(r"^第[一二两三四五六七八九十百零\d]+章", stripped):
        return True
    if re.match(r"^[一二两三四五六七八九十百零]+[、.]", stripped):
        return True
    if re.match(r"^\d+(?:\.\d+)*[.、)]?\s*", stripped):
        return True
    if re.match(r"^(参考文献|致谢|摘要|ABSTRACT|目录|目次|附录)", stripped, re.IGNORECASE):
        return True
    return False


def chinese_to_int(text: str) -> int:
    """Convert a simple Chinese numeral into an integer."""
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" not in text:
        return sum(CHINESE_NUMERAL_MAP[char] for char in text if char in CHINESE_NUMERAL_MAP)
    parts = text.split("十", 1)
    tens = CHINESE_NUMERAL_MAP.get(parts[0], 1 if parts[0] == "" else 0)
    ones = CHINESE_NUMERAL_MAP.get(parts[1], 0) if parts[1] else 0
    return tens * 10 + ones


def circled_digit_to_int(char: str) -> int:
    index = CIRCLED_DIGITS.find(char)
    return index + 1 if index >= 0 else 0


def _compute_plain_confidence(title: str) -> float:
    if not title:
        return 0.2
    if any("\u4e00" <= c <= "\u9fff" for c in title):
        return 0.58
    return 0.45


def _collapse_cjk_spaces(title: str) -> str:
    collapsed = title
    while True:
        updated = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", collapsed)
        if updated == collapsed:
            return updated
        collapsed = updated


def _normalize_common_terms(title: str) -> str:
    normalized = title
    for spaced, combined in COMMON_SPACED_TERMS.items():
        normalized = normalized.replace(spaced, combined)
    return normalized


def _remove_page_number_suffix(title: str) -> str:
    return re.sub(r"\s*[.\-·•…]{2,}\s*\d+\s*$", "", title).rstrip()


def _strip_prefix(title: str, prefix: str) -> str:
    stripped = title[len(prefix) :].strip()
    return stripped or title.strip()


def _is_compatible_parent(profile: str, current: HeadingInfo, parent: HeadingInfo) -> bool:
    child = current.family
    parent_family = parent.family

    if child == "chapter_cn":
        return False
    if child == "cn_main":
        return False
    if child == "arabic_main":
        if profile == "journal_mixed_cn":
            return parent_family in {"cn_main", "cn_paren"}
        return False
    if child == "decimal_path":
        if not current.numbering_path:
            return True
        if len(current.numbering_path) == 2:
            if profile == "chapter_decimal":
                return parent_family in {"chapter_cn", "arabic_main"}
            if profile == "journal_mixed_cn":
                return parent_family in {"cn_main", "cn_paren", "arabic_main"}
            return parent_family in {"arabic_main", "chapter_cn"}
        if parent.family == "decimal_path" and parent.numbering_path:
            return current.numbering_path[:-1] == parent.numbering_path
        return False
    if child == "cn_paren":
        return parent_family in {"cn_main", "chapter_cn", "decimal_path"}
    if child == "arabic_paren":
        return parent_family in {"cn_main", "cn_paren", "arabic_main", "decimal_path"}
    if child == "circled":
        return parent_family in {"arabic_paren", "cn_paren", "decimal_path"}
    return True
