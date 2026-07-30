from __future__ import annotations

from typing import Any, Iterable


_RELATION_LABELS = {
    "converges": "结论趋同",
    "complements": "相互补充",
    "contrasts": "结论对照",
    "scope_difference": "适用范围不同",
    "methodological_alternative": "方法替代",
}
_RESULT_LABELS = {
    "historical_observation": "历史观察",
    "empirical_measurement": "经验测量",
    "experimental_result": "实验结果",
    "simulation_result": "仿真结果",
    "algorithm_benchmark": "算法基准测试",
    "engineering_implementation": "工程实施",
    "system_design": "系统设计",
    "recommendation": "建议",
    "conceptual_argument": "概念论证",
}
_VALIDATION_LABELS = {
    "none": "未验证",
    "conceptual": "概念论证",
    "simulation": "仿真验证",
    "benchmark": "基准测试",
    "field_observation": "现场观察",
    "engineering_application": "工程应用",
}


def render_research_landscape_report(
    landscape: dict[str, Any],
    *,
    sources: Iterable[Any],
) -> str:
    source_by_paper = {source.paper_id: source for source in sources}
    contribution_by_id = {
        str(row["contribution_id"]): (source, row)
        for source in sources
        for row in source.understanding["contributions"]
    }
    lines = [
        "# Research Landscape",
        "",
        f"- 综述主题：{landscape['topic']}",
        f"- 综述目标：{landscape['review_goal']}",
        f"- 语料范围：{landscape['corpus_scope']}",
        f"- 来源论文：{len(source_by_paper)}篇",
        "",
        "> 维度、论文关系、研究演化、分歧和语料缺口均为模型基于本次显式论文集合形成的综合判断。下方贡献及原文用于核对该判断，不代表已经证明整个研究领域的结构或空白。",
        "",
        "## 中心问题",
        "",
        str(landscape["central_problem"]),
        "",
        "## 研究维度",
        "",
    ]
    for dimension in landscape["dimensions"]:
        lines.extend(
            [
                f"### {dimension['dimension_index']}. {dimension['title']}",
                "",
                f"- 回答问题：{dimension['question']}",
                f"- 涉及论文：{len(dimension['paper_ids'])}篇",
                "",
            ]
        )
        _append_contributions(
            lines,
            dimension["contribution_ids"],
            contribution_by_id,
        )

    lines.extend(["", "## 跨论文关系", ""])
    if landscape["relations"]:
        for index, relation in enumerate(landscape["relations"], start=1):
            source_title = source_by_paper[
                str(relation["from_paper_id"])
            ].paper_title
            target_title = source_by_paper[
                str(relation["to_paper_id"])
            ].paper_title
            lines.extend(
                [
                    f"### 关系 {index}：{_RELATION_LABELS.get(str(relation['relation_type']), relation['relation_type'])}",
                    "",
                    f"- 论文：{source_title} ↔ {target_title}",
                    f"- 综合判断：{relation['statement']}",
                    "",
                ]
            )
            _append_contributions(
                lines,
                relation["supporting_contribution_ids"],
                contribution_by_id,
            )
    else:
        lines.append("本次语料没有形成满足双论文贡献约束的显式关系。")

    lines.extend(["", "## 研究关注演化", ""])
    if landscape["research_evolution"]:
        for index, evolution in enumerate(
            landscape["research_evolution"],
            start=1,
        ):
            lines.extend(
                [
                    f"### 阶段 {index}：{evolution['period']}",
                    "",
                    str(evolution["statement"]),
                    "",
                ]
            )
            _append_contributions(
                lines,
                evolution["contribution_ids"],
                contribution_by_id,
            )
    else:
        lines.append("本次语料不足以形成有来源约束的年代演化判断。")

    lines.extend(["", "## 语料内分歧", ""])
    if landscape["disagreements"]:
        for index, disagreement in enumerate(
            landscape["disagreements"],
            start=1,
        ):
            lines.extend(
                [
                    f"### 分歧 {index}",
                    "",
                    f"**问题：** {disagreement['question']}",
                    "",
                ]
            )
            for position_index, position in enumerate(
                disagreement["positions"],
                start=1,
            ):
                titles = "、".join(
                    source_by_paper[str(paper_id)].paper_title
                    for paper_id in position["paper_ids"]
                )
                lines.extend(
                    [
                        f"#### 立场 {position_index}",
                        "",
                        f"- 论文：{titles}",
                        f"- 立场：{position['statement']}",
                        "",
                    ]
                )
                _append_contributions(
                    lines,
                    position["contribution_ids"],
                    contribution_by_id,
                )
    else:
        lines.append("本次语料没有形成满足来源约束的明确分歧。")

    lines.extend(["", "## 本次语料缺口", ""])
    if landscape["corpus_gaps"]:
        for index, gap in enumerate(landscape["corpus_gaps"], start=1):
            dimensions = "、".join(
                str(value) for value in gap["related_dimension_indexes"]
            )
            lines.extend(
                [
                    f"### 缺口 {index}",
                    "",
                    f"- 问题：{gap['question']}",
                    f"- 影响：{gap['why_it_matters']}",
                    f"- 相关维度：{dimensions}",
                    "- 边界：仅表示本次显式语料没有充分回答，不等同于领域空白。",
                    "",
                ]
            )
    else:
        lines.append("模型没有提出额外语料缺口。")

    lines.extend(["", "## 未归入维度的论文", ""])
    if landscape["unmapped_papers"]:
        for row in landscape["unmapped_papers"]:
            source = source_by_paper[str(row["paper_id"])]
            lines.append(f"- {source.paper_title}：{row['reason']}")
    else:
        lines.append("全部论文至少进入一个研究维度。")

    lines.extend(["", "## 来源论文概况", ""])
    lines.extend(
        [
            "| 论文 | 相关性 | 贡献 | 方法 | 限制 |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for source in sources:
        understanding = source.understanding
        lines.append(
            "| {title} | {relevance} | {contributions} | {methods} | {limitations} |".format(
                title=_escape_table(source.paper_title),
                relevance=understanding["paper_relevance"],
                contributions=len(understanding["contributions"]),
                methods=len(understanding["methods"]),
                limitations=len(understanding["limitations"]),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _append_contributions(
    lines: list[str],
    contribution_ids: Iterable[str],
    contribution_by_id: dict[str, tuple[Any, dict[str, Any]]],
) -> None:
    for contribution_id in contribution_ids:
        source, contribution = contribution_by_id[str(contribution_id)]
        lines.extend(
            [
                f"#### {source.paper_title}",
                "",
                f"- 贡献：{contribution['statement']}",
                f"- 结果类型：{_RESULT_LABELS.get(str(contribution['result_type']), contribution['result_type'])}",
                f"- 验证水平：{_VALIDATION_LABELS.get(str(contribution['validation_level']), contribution['validation_level'])}",
                f"- 证据强度：{contribution['evidence_strength']}",
                f"- 强度依据：{contribution['strength_rationale']}",
                "",
            ]
        )
        material_by_id = {
            str(row["material_id"]): row for row in source.materials
        }
        for material_id in contribution["material_ids"]:
            material = material_by_id[str(material_id)]
            title = str(
                material.get("clean_title")
                or material.get("raw_title")
                or material.get("paper_title")
                or "未命名Card"
            )
            span = material.get("source_span") or {}
            start = span.get("start_line", "?")
            end = span.get("end_line", "?")
            lines.append(f"- 原文：**{title}**（L{start}-L{end}）")
            extract = str(material.get("extract", "")).strip()
            lines.extend(
                f"  > {line}" if line else "  >"
                for line in (extract.splitlines() or ["无可显示摘录"])
            )
        lines.append("")


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
