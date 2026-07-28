from src.parsing.headings import detect_schema, normalize_heading_title, parse_heading_info


def test_detect_schema_prefers_chapter_decimal():
    schema = detect_schema(["第一章 绪论", "1.1 研究背景", "1.1.1 研究背景"])
    assert schema == "chapter_decimal"


def test_detect_schema_detects_journal_mixed_cn():
    schema = detect_schema(["一、研究背景", "（一）现实需求", "1．研究问题"])
    assert schema == "journal_mixed_cn"


def test_parse_heading_info_handles_arabic_main_variants():
    samples = ["0 引言", "1 引言", "1．标题", "1. 标题", "1）标题"]
    for title in samples:
        info = parse_heading_info(title)
        assert info.family == "arabic_main"
        assert info.semantic_level == 1
        assert info.numbering_path


def test_parse_heading_info_handles_parenthesized_numbering_families():
    cn_info = parse_heading_info("（一）现实需求")
    arabic_info = parse_heading_info("（1）研究问题")
    circled_info = parse_heading_info("①样本")

    assert cn_info.family == "cn_paren"
    assert cn_info.semantic_level == 2
    assert arabic_info.family == "arabic_paren"
    assert arabic_info.semantic_level == 3
    assert circled_info.family == "circled"
    assert circled_info.semantic_level == 4


def test_normalize_heading_title_repairs_spaced_front_matter_titles():
    assert normalize_heading_title("摘 要") == "摘要"
    assert normalize_heading_title("参 考 文 献") == "参考文献"
    assert normalize_heading_title("目 录") == "目录"


def test_normalize_heading_title_strips_inline_html_artifacts():
    assert normalize_heading_title("参 考 文 献<sup>．</sup>") == "参考文献"


def test_parse_heading_info_removes_page_number_suffix():
    info = parse_heading_info("第一章 绪论 ........ 1")
    assert info.family == "chapter_cn"
    assert info.title_norm == "绪论"
