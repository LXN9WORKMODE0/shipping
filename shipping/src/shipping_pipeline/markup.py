from __future__ import annotations

import re


HTML_TAG_PATTERN = re.compile(
    r"</?(?:html|body|head|title|meta|link|style|script|table|thead|tbody|tfoot|tr|td|th|caption|"
    r"colgroup|col|sub|sup|div|span|p|br|img|figure|figcaption|a|em|strong|b|i|u|ol|ul|li|h[1-6])"
    r"\b[^>]*>",
    re.IGNORECASE,
)


def contains_html_markup(value: str) -> bool:
    return HTML_TAG_PATTERN.search(value) is not None


def strip_html_markup(value: str, replacement: str = "") -> str:
    return HTML_TAG_PATTERN.sub(replacement, value)
