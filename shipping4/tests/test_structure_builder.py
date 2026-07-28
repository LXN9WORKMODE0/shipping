from src.parsing.structure_builder import DocumentStructureBuilder


def _flatten(nodes):
    result = []
    for node in nodes:
        result.append(node)
        result.extend(_flatten(node.children))
    return result


def _assert_sibling_ordinals(nodes):
    expected = list(range(1, len(nodes) + 1))
    actual = [node.ordinal for node in nodes]
    assert actual == expected
    for node in nodes:
        _assert_sibling_ordinals(node.children)


def _assert_outline_ordinals(outline):
    by_parent = {}
    for node in outline:
        by_parent.setdefault(node.parent_id, []).append(node)

    for siblings in by_parent.values():
        expected = list(range(1, len(siblings) + 1))
        actual = [node.ordinal for node in siblings]
        assert actual == expected


def test_structure_builder_handles_thesis_front_matter(thesis_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("thesis", thesis_markdown, source_type="fixture")

    assert structure.detected_schema == "chapter_decimal"
    assert [node.role for node in structure.nodes] == ["body", "body"]

    body_nodes = [node for node in structure.nodes if node.role == "body"]
    assert [node.title_raw for node in body_nodes] == ["第一章 绪论", "第二章 方法设计"]
    assert all(node.children == [] for node in body_nodes)


def test_structure_builder_handles_journal_mixed_numbering(journal_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("journal", journal_markdown, source_type="fixture")

    assert structure.detected_schema == "journal_mixed_cn"
    body_nodes = [node for node in structure.nodes if node.role == "body"]
    assert [node.title_raw for node in body_nodes] == ["一、研究背景", "二、方法设计"]
    assert all(node.children == [] for node in body_nodes)


def test_structure_builder_locks_body_on_zero_intro(journal_zero_intro_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("zero-intro", journal_zero_intro_markdown, source_type="fixture")
    diagnostics = builder.build_diagnostics(structure, ["detected_schema=decimal_only"], [])

    body_nodes = [node for node in structure.nodes if node.role == "body"]
    assert [node.title_raw for node in body_nodes] == ["0 引言", "1 方法", "2 结果", "3 结论"]
    assert diagnostics.first_body_title == "0 引言"
    assert "body_start_missing" not in diagnostics.warnings


def test_structure_builder_handles_fullwidth_mixed_numbering_without_false_jump(journal_fullwidth_mix_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("fullwidth-mix", journal_fullwidth_mix_markdown, source_type="fixture")

    assert structure.detected_schema == "journal_mixed_cn"
    assert [node.title_raw for node in structure.nodes if node.role == "body"] == ["二、管控策略", "三、实施步骤"]
    assert all(node.children == [] for node in structure.nodes if node.role == "body")


def test_structure_builder_keeps_plain_body_headings_in_body(plain_body_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("plain-body", plain_body_markdown, source_type="fixture")

    body_titles = [node.title_raw for node in structure.nodes if node.role == "body"]
    assert body_titles == ["引言", "研究设计", "数据来源", "结论"]


def test_structure_builder_keeps_plain_roots_when_late_numbered_heading_appears():
    markdown = """# 题名

摘要 正文摘要。

## 引 言

引言内容。

## 交通管控线设置地点和启动条件

设置内容。

## 仿真模型构建

模型内容。

## 2．1 模型边界及构成

小节内容。

## 2．2 仿真模型构成

小节内容。

## 仿真结果与结果分析Record

结果内容。

## 4 结 1 论a

结论内容。

## 参 考 文 献<sup>．</sup>

[1] 参考文献。

# Research on the Grooming Strategy Effect

English abstract.
"""
    builder = DocumentStructureBuilder()
    structure = builder.build("plain-decimal-mix", markdown, source_type="fixture")
    diagnostics = builder.build_diagnostics(structure, [], [])

    body_titles = [node.title_raw for node in structure.nodes if node.role == "body"]
    assert body_titles == [
        "引 言",
        "交通管控线设置地点和启动条件",
        "仿真模型构建",
        "仿真结果与结果分析Record",
        "4 结 1 论a",
    ]
    assert "参 考 文 献<sup>．</sup>" not in body_titles
    assert diagnostics.tail_special_after_body_count == 1


def test_structure_builder_collapses_chapter_content_span(thesis_complex_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("thesis-complex", thesis_complex_markdown, source_type="fixture")

    first_chapter = next(node for node in structure.nodes if node.role == "body")
    assert first_chapter.children == []
    assert first_chapter.content_ref["start_line"] < first_chapter.content_ref["end_line"]
    assert first_chapter.content_ref["end_line"] > 40


def test_structure_builder_stats_include_role_counts(thesis_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("thesis", thesis_markdown, source_type="fixture")

    assert "role_counts" in structure.stats
    assert "body_node_count" in structure.stats
    assert "toc_node_count" in structure.stats
    assert structure.stats["body_node_count"] == 2
    assert structure.stats["toc_node_count"] == 0


def test_structure_builder_assigns_parent_ids_and_ordinals(thesis_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("thesis", thesis_markdown, source_type="fixture")

    flat = _flatten(structure.nodes)
    for node in flat:
        assert node.parent_id is None

    _assert_sibling_ordinals(structure.nodes)


def test_structure_builder_node_type_assignment(thesis_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("thesis", thesis_markdown, source_type="fixture")

    for node in _flatten(structure.nodes):
        assert node.node_type == "section"


def test_structure_builder_low_confidence_detection(plain_body_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("plain-body", plain_body_markdown, source_type="fixture")

    flat = _flatten(structure.nodes)
    low_conf_nodes = [node for node in flat if node.is_low_confidence]
    assert len(low_conf_nodes) == len(structure.low_confidence_node_ids)
    for node_id in structure.low_confidence_node_ids:
        assert any(node.id == node_id for node in flat)


def test_structure_builder_build_diagnostics_exposes_phase2_context(journal_zero_intro_markdown):
    builder = DocumentStructureBuilder()
    structure = builder.build("zero-intro", journal_zero_intro_markdown, source_type="fixture")
    diagnostics = builder.build_diagnostics(structure, ["detected_schema=decimal_only"], [])

    assert diagnostics.document_mode in {"journal_like", "unknown"}
    assert diagnostics.numbering_profile == "decimal_only"
    assert diagnostics.first_body_title == "0 引言"
    assert diagnostics.body_locked is True
    assert diagnostics.area_trace
    assert diagnostics.heading_family_counts["arabic_main"] >= 4


def test_structure_builder_build_outline():
    builder = DocumentStructureBuilder()
    structure = builder.build(
        "outline",
        "# 1 Intro\n\n## 1.1 Background\n\n### 1.1.1 Detail\n\n# References\n\n[1] ref\n",
        source_type="fixture",
    )

    outline = builder.build_outline(structure)
    assert outline
    for node in outline:
        assert node.parent_id is None
    _assert_outline_ordinals(outline)
