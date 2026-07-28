from __future__ import annotations

from collections import Counter
from typing import Any


def render_topic_synthesis_report(
    *,
    topic: str,
    sources: list[dict[str, Any]],
    evidence_map: dict[str, Any],
    outline: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    evidence_to_paper: dict[str, str],
    coverage: dict[str, Any],
) -> str:
    source_by_paper = {str(row["paper_id"]): row for row in sources}
    lines = [
        "# 跨论文主题综合",
        "",
        f"- 综述主题：{topic}",
        f"- 来源论文：{len(sources)} 篇",
        f"- 来源 Evidence：{coverage['source_evidence_count']} 条",
        f"- 进入提纲 Evidence：{coverage['outline_used_count']} 条",
        "",
        "> 主题名称、论文关系、综合判断和段落任务是模型生成的导航性分析。"
        "具体事实只能依据每项下方展示的 Evidence claim、逐字原文和来源位置。",
        "",
        "## 语料范围",
        "",
        "| 论文 | 单篇相关性 | 来源状态 | Evidence |",
        "|---|---|---|---:|",
    ]
    for source in sources:
        lines.append(
            "| {title} | {relevance} | {status} | {count} |".format(
                title=_escape_table(str(source["paper_title"])),
                relevance=source["paper_relevance"],
                status=source["status"],
                count=source["evidence_unit_count"],
            )
        )
    evidence_partial = [
        row for row in sources if str(row["status"]) == "completed_with_failures"
    ]
    if evidence_partial:
        lines.extend(
            [
                "",
                "> 以下论文有部分入选 Card 未通过 Evidence 合同；"
                "通过合同的 Evidence 已进入本次综合，失败 Card 保留在对应单篇运行的失败账本中："
                + "、".join(str(row["paper_title"]) for row in evidence_partial),
            ]
        )
    revisit_partial = [
        row for row in sources if str(row["status"]) == "completed_with_revisit_failure"
    ]
    if revisit_partial:
        lines.extend(
            [
                "",
                "> 以下论文的基础简报有效，但单篇自动回查未闭合："
                + "、".join(str(row["paper_title"]) for row in revisit_partial),
            ]
        )

    lines.extend(["", "## 主题矩阵", ""])
    for theme_index, theme in enumerate(evidence_map["themes"], start=1):
        assignment_ids = [
            str(row["evidence_unit_id"]) for row in theme["assignments"]
        ]
        paper_count = len({evidence_to_paper[row] for row in assignment_ids})
        lines.extend(
            [
                f"### {theme_index}. {theme['title']}",
                "",
                f"- 关系类型：{theme['relation_type']}",
                f"- 涉及论文：{paper_count} 篇",
                f"- 关注问题：{theme['focus']}",
                f"- 综合价值：{theme['synthesis_value']}",
                "",
            ]
        )
        for assignment in theme["assignments"]:
            evidence_id = str(assignment["evidence_unit_id"])
            lines.append(f"**证据角色：{assignment['role']}**")
            lines.append("")
            lines.extend(
                _render_evidence(
                    evidence_by_id[evidence_id],
                    source_by_paper[evidence_to_paper[evidence_id]],
                )
            )
            lines.append("")

    lines.extend(["## 跨论文综合", ""])
    for unit_index, unit in enumerate(outline["synthesis_units"], start=1):
        evidence_ids = [str(row) for row in unit["evidence_unit_ids"]]
        paper_count = len({evidence_to_paper[row] for row in evidence_ids})
        lines.extend(
            [
                f"### 综合单元 {unit_index}",
                "",
                f"- 类型：{unit['unit_type']}",
                f"- 独立来源论文：{paper_count} 篇",
                f"- 模型综合判断：{unit['synthesis_statement']}",
                "",
            ]
        )
        if not evidence_ids:
            lines.extend(["- 本项是语料缺口判断，没有被当作论文事实。", ""])
        for evidence_id in evidence_ids:
            lines.extend(
                _render_evidence(
                    evidence_by_id[evidence_id],
                    source_by_paper[evidence_to_paper[evidence_id]],
                )
            )
            lines.append("")

    lines.extend(["## 综述提纲", ""])
    unit_by_id = {
        str(row["synthesis_unit_id"]): row for row in outline["synthesis_units"]
    }
    for section_index, section in enumerate(outline["sections"], start=1):
        lines.extend(
            [
                f"### {section_index}. {section['title']}",
                "",
                f"- 本节任务：{section['purpose']}",
                "",
            ]
        )
        for paragraph_index, paragraph in enumerate(section["paragraphs"], start=1):
            lines.extend(
                [
                    f"#### 段落 {section_index}.{paragraph_index}",
                    "",
                    f"- 段落角色：{paragraph['paragraph_role']}",
                    f"- 论证动作：{paragraph['synthesis_move']}",
                    "",
                ]
            )
            for evidence_id in paragraph["evidence_unit_ids"]:
                evidence_id = str(evidence_id)
                lines.extend(
                    _render_evidence(
                        evidence_by_id[evidence_id],
                        source_by_paper[evidence_to_paper[evidence_id]],
                    )
                )
                lines.append("")
            if not paragraph["evidence_unit_ids"]:
                statements = [
                    str(unit_by_id[str(unit_id)]["synthesis_statement"])
                    for unit_id in paragraph["synthesis_unit_ids"]
                ]
                lines.append("- 本段讨论语料缺口：" + "；".join(statements))
                lines.append("")

    lines.extend(["## 证据缺口与检索方向", ""])
    if outline["search_directions"]:
        gap_by_id = {
            str(row["gap_id"]): row for row in evidence_map["corpus_gaps"]
        }
        for index, row in enumerate(outline["search_directions"], start=1):
            source_questions = "；".join(
                str(gap_by_id[str(gap_id)]["question"])
                for gap_id in row["source_gap_ids"]
            )
            lines.extend(
                [
                    f"{index}. **{row['priority']}**：{row['question']}",
                    f"   - 原因：{row['reason']}",
                    f"   - 对应语料缺口：{source_questions}",
                ]
            )
    else:
        lines.append("- 当前提纲没有生成新的检索方向。")

    lines.extend(["", "## 覆盖审计", ""])
    for label, key in [
        ("来源 Evidence", "source_evidence_count"),
        ("主题归属记录", "theme_assignment_count"),
        ("主题已归组", "theme_assigned_count"),
        ("仅归属一个主题", "single_theme_evidence_count"),
        ("归属多个主题", "multi_theme_evidence_count"),
        ("单条 Evidence 最大主题数", "max_observed_themes_per_evidence"),
        ("主题未归组", "theme_unassigned_count"),
        ("进入提纲", "outline_used_count"),
        ("已归组但未进入提纲", "assigned_but_unused_count"),
        ("综合单元总数", "synthesis_unit_count"),
        ("已入纲综合单元", "placed_synthesis_unit_count"),
        ("未入纲候选综合单元", "unplaced_synthesis_unit_count"),
        ("语料缺口总数", "corpus_gap_count"),
        ("已优先检索缺口", "prioritized_gap_count"),
        ("未优先检索缺口", "unprioritized_gap_count"),
        ("跨论文主题", "cross_paper_theme_count"),
        ("跨论文综合单元", "cross_paper_synthesis_unit_count"),
    ]:
        lines.append(f"- {label}：{coverage[key]}")
    lines.append(f"- 单来源主题：{len(coverage['single_source_theme_ids'])}")
    lines.append(f"- 部分状态论文：{len(coverage['partial_source_run_ids'])}")
    if coverage["quality_flags"]:
        lines.append(
            "- 质量标记：" + "、".join(coverage["quality_flags"])
        )

    if evidence_map["unassigned_evidence"]:
        lines.extend(["", "### 未归组 Evidence", ""])
        for row in evidence_map["unassigned_evidence"]:
            evidence_id = str(row["evidence_unit_id"])
            source = source_by_paper[evidence_to_paper[evidence_id]]
            lines.append(
                f"- {source['paper_title']}：{evidence_by_id[evidence_id]['claim']} "
                f"（{row['reason_code']}：{row['reason']}）"
            )

    unused_ids = [str(row) for row in coverage["assigned_but_unused_evidence_ids"]]
    if unused_ids:
        lines.extend(["", "### 已归组但未进入提纲", ""])
        for evidence_id in unused_ids:
            source = source_by_paper[evidence_to_paper[evidence_id]]
            lines.append(
                f"- {source['paper_title']}：{evidence_by_id[evidence_id]['claim']}"
            )

    unplaced_ids = {
        str(row) for row in coverage["unplaced_synthesis_unit_ids"]
    }
    if unplaced_ids:
        lines.extend(["", "### 未入纲候选综合单元", ""])
        for unit in outline["synthesis_units"]:
            if str(unit["synthesis_unit_id"]) in unplaced_ids:
                lines.append(f"- {unit['synthesis_statement']}")

    unprioritized_gap_ids = {
        str(row) for row in coverage["unprioritized_gap_ids"]
    }
    if unprioritized_gap_ids:
        lines.extend(["", "### 未优先检索的语料缺口", ""])
        for gap in evidence_map["corpus_gaps"]:
            if str(gap["gap_id"]) in unprioritized_gap_ids:
                lines.append(f"- {gap['question']}：{gap['why_it_matters']}")

    reuse = Counter(
        str(evidence_id)
        for section in outline["sections"]
        for paragraph in section["paragraphs"]
        for evidence_id in paragraph["evidence_unit_ids"]
    )
    repeated = sorted(
        evidence_id for evidence_id, count in reuse.items() if count > 1
    )
    if repeated:
        lines.extend(
            [
                "",
                "- 在多个段落复用的 Evidence："
                + "、".join(
                    f"{evidence_by_id[row]['claim']}（{reuse[row]}次）"
                    for row in repeated
                ),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_evidence(
    evidence: dict[str, Any],
    source: dict[str, Any],
) -> list[str]:
    evidence_id = str(evidence["evidence_unit_id"])
    lines = [
        f"- 来源论文：{source['paper_title']}",
        f"- 证据观点：{evidence['claim']}",
    ]
    for citation_index, citation in enumerate(evidence["citations"], start=1):
        quote = str(citation.get("quote", "")).strip()
        lines.append(f"- 原文 {citation_index}：")
        lines.extend(f"  > {row}" for row in quote.splitlines() or [""])
        source_ref = citation.get("source_ref") or {}
        location = str(source_ref.get("path", ""))
        start = source_ref.get("start_line")
        end = source_ref.get("end_line")
        if start is not None:
            location += f" L{start}"
            if end is not None and end != start:
                location += f"-L{end}"
        card_title = str(citation.get("card_title") or "")
        lines.append(f"- 位置：{location}；Card：{card_title}")
    caveats = [str(row) for row in evidence.get("caveats", []) if str(row).strip()]
    if caveats:
        lines.append("- Evidence 注意事项：" + "；".join(caveats))
    lines.append(f"- 机器锚点：`{evidence_id}`")
    return lines


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
