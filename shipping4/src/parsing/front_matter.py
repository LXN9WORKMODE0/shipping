"""Document area heuristics for front matter, TOC, and tail sections."""

from __future__ import annotations

import re
from enum import Enum
from typing import Iterable

from src.parsing.headings import HeadingInfo, has_trailing_page_number, normalize_heading_title, parse_heading_info

TOC_TITLE_PATTERNS = [
    re.compile(r"^目录$"),
    re.compile(r"^目次$"),
    re.compile(r"^contents$", re.IGNORECASE),
    re.compile(r"^table of contents$", re.IGNORECASE),
]
REFERENCE_PATTERNS = [
    re.compile(r"^\[?参考文献\]?$"),
    re.compile(r"^references?$", re.IGNORECASE),
]
APPENDIX_PATTERNS = [
    re.compile(r"^\[?附录[A-Z]?\]?$"),
    re.compile(r"^appendix", re.IGNORECASE),
]
ACK_PATTERNS = [
    re.compile(r"^\[?致谢\]?$"),
    re.compile(r"^acknowledg", re.IGNORECASE),
]
AUTHOR_BIO_PATTERNS = [
    re.compile(r"^\[?作者简介\]?$"),
    re.compile(r"^author biography$", re.IGNORECASE),
]
FRONT_MATTER_PATTERNS = [
    re.compile(r"摘要"),
    re.compile(r"^abstract$", re.IGNORECASE),
    re.compile(r"独创性声明"),
    re.compile(r"原创性声明"),
    re.compile(r"版权使用授权书"),
    re.compile(r"授权书"),
    re.compile(r"硕士.*学位.*论文"),
    re.compile(r"博士.*学位.*论文"),
    re.compile(r"^research on", re.IGNORECASE),
    re.compile(r"^a dissertation", re.IGNORECASE),
]
SCHOOL_PATTERNS = [re.compile(r"大学|学院|研究所|school|university|institute", re.IGNORECASE)]
TITLE_PAGE_PATTERNS = [re.compile(r"硕士学位论文|博士学位论文|dissertation|thesis", re.IGNORECASE)]
DECLARATION_PATTERNS = [re.compile(r"独创性声明|原创性声明")]
AUTH_PATTERNS = [re.compile(r"授权书|版权.*授权|authorization", re.IGNORECASE)]
PRE_BODY_MISC_PATTERNS = [
    re.compile(r"^图目录$"),
    re.compile(r"^表目录$"),
    re.compile(r"^插图目录$"),
    re.compile(r"^符号说明$"),
    re.compile(r"^主要符号说明$"),
    re.compile(r"^缩略语(?:表|说明)?$"),
]
THESIS_STRONG_SIGNALS = [
    re.compile(r"硕士学位论文"),
    re.compile(r"博士学位论文"),
    re.compile(r"独创性声明"),
    re.compile(r"授权书"),
    re.compile(r"学号"),
    re.compile(r"导师"),
    re.compile(r"专业"),
]
JOURNAL_STRONG_SIGNALS = [
    re.compile(r"关键词"),
    re.compile(r"摘要"),
    re.compile(r"引言"),
]


class DocArea(Enum):
    """Document area states for the deterministic state machine."""

    START = "start"
    TITLE_BLOCK = "title_block"
    DECLARATION = "declaration"
    AUTHORIZATION = "authorization"
    ABSTRACT_CN = "abstract_cn"
    ABSTRACT_EN = "abstract_en"
    TOC = "toc"
    PRE_BODY_MISC = "pre_body_misc"
    BODY_LOCKED = "body_locked"
    REFERENCES = "references"
    APPENDIX = "appendix"
    ACK = "ack"


def detect_document_mode(items: Iterable[object]) -> str:
    """Detect whether the document looks like a thesis or journal."""
    titles: list[str] = []
    contents: list[str] = []
    for item in items:
        title = getattr(item, "title", "")
        content = getattr(item, "content", "")
        if title:
            titles.append(normalize_heading_title(title))
        if content:
            contents.append(content)
        if len(titles) >= 8:
            break

    thesis_score = 0
    journal_score = 0
    for text in titles + contents[:4]:
        thesis_score += sum(1 for pattern in THESIS_STRONG_SIGNALS if pattern.search(text))
        journal_score += sum(1 for pattern in JOURNAL_STRONG_SIGNALS if pattern.search(text))

    if thesis_score >= 2:
        return "thesis_like"
    if journal_score >= 2:
        return "journal_like"
    return "unknown"


def detect_area(title: str, content: str, current_area: DocArea, document_mode: str = "unknown") -> DocArea:
    """Advance the non-body state machine for a heading."""
    normalized = normalize_heading_title(title)

    if is_reference_title(normalized):
        return DocArea.REFERENCES
    if is_appendix_title(normalized):
        return DocArea.APPENDIX
    if is_ack_title(normalized):
        return DocArea.ACK

    if current_area == DocArea.BODY_LOCKED:
        return current_area

    if is_toc_title(normalized):
        return DocArea.TOC
    if _is_title_block(normalized, content, document_mode):
        return DocArea.TITLE_BLOCK
    if _is_declaration(normalized):
        return DocArea.DECLARATION
    if _is_authorization(normalized):
        return DocArea.AUTHORIZATION
    if _is_cn_abstract(normalized):
        return DocArea.ABSTRACT_CN
    if _is_en_abstract(normalized):
        return DocArea.ABSTRACT_EN
    if is_pre_body_misc_title(normalized):
        return DocArea.PRE_BODY_MISC
    return current_area


def is_toc_title(title: str) -> bool:
    normalized = normalize_heading_title(title)
    return any(pattern.match(normalized) for pattern in TOC_TITLE_PATTERNS)


def is_reference_title(title: str) -> bool:
    normalized = normalize_heading_title(title)
    return any(pattern.match(normalized) for pattern in REFERENCE_PATTERNS)


def is_appendix_title(title: str) -> bool:
    normalized = normalize_heading_title(title)
    return any(pattern.match(normalized) for pattern in APPENDIX_PATTERNS)


def is_ack_title(title: str) -> bool:
    normalized = normalize_heading_title(title)
    return any(pattern.match(normalized) for pattern in ACK_PATTERNS)


def is_author_bio_title(title: str) -> bool:
    normalized = normalize_heading_title(title)
    return any(pattern.match(normalized) for pattern in AUTHOR_BIO_PATTERNS)


def is_front_matter_title(title: str) -> bool:
    normalized = normalize_heading_title(title)
    return any(pattern.search(normalized) for pattern in FRONT_MATTER_PATTERNS)


def is_pre_body_misc_title(title: str) -> bool:
    normalized = normalize_heading_title(title)
    return any(pattern.match(normalized) for pattern in PRE_BODY_MISC_PATTERNS)


def is_tail_special_title(title: str) -> bool:
    normalized = normalize_heading_title(title)
    return is_reference_title(normalized) or is_appendix_title(normalized) or is_ack_title(normalized) or is_author_bio_title(normalized)


def looks_like_toc_entry(title: str, content: str, schema: str = "mixed_unknown") -> bool:
    normalized_title = normalize_heading_title(title)
    if is_toc_title(normalized_title):
        return True
    if re.search(r"[.\-·•…]{2,}\s*\d+\s*$", title):
        return True
    if has_trailing_page_number(title):
        info = parse_heading_info(normalized_title, schema)
        if info.family in {"chapter_cn", "arabic_main", "decimal_path", "cn_main", "plain"}:
            return True
    if looks_like_page_index(content):
        return True
    info = parse_heading_info(normalized_title, schema)
    return info.family in {"chapter_cn", "arabic_main", "decimal_path", "cn_main"} and bool(re.search(r"\d+\s*$", title))


def looks_like_page_index(content: str) -> bool:
    if not content:
        return False
    compact = re.sub(r"\s+", "", content)
    numeric_ratio = len(re.findall(r"[\d.\-]", compact)) / max(len(compact), 1)
    return len(compact) < 240 and numeric_ratio >= 0.18


def is_toc_exit_candidate(info: HeadingInfo, title: str) -> bool:
    normalized = normalize_heading_title(title)
    return info.family in {"chapter_cn", "arabic_main", "decimal_path", "cn_main"} or normalized in {
        "绪论",
        "引言",
        "前言",
    }


def _is_title_block(title: str, content: str, document_mode: str) -> bool:
    if any(pattern.search(title) for pattern in TITLE_PAGE_PATTERNS):
        return True
    if any(pattern.search(title) for pattern in SCHOOL_PATTERNS):
        return True
    if document_mode == "thesis_like" and re.search(r"作者|学生姓名|学号|导师|专业", content):
        return True
    return False


def _is_declaration(title: str) -> bool:
    return any(pattern.search(title) for pattern in DECLARATION_PATTERNS)


def _is_authorization(title: str) -> bool:
    return any(pattern.search(title) for pattern in AUTH_PATTERNS)


def _is_cn_abstract(title: str) -> bool:
    return "摘要" in title and not _is_en_abstract(title)


def _is_en_abstract(title: str) -> bool:
    return bool(re.match(r"^abstract$", title.strip(), re.IGNORECASE))
