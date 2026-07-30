from __future__ import annotations

from typing import Any, Iterable


_RELEVANCE_LABELS = {
    "core": "核心文献",
    "supporting": "支持性文献",
    "peripheral": "边缘相关文献",
    "exclude": "不纳入当前主题",
}
_VALIDATION_LABELS = {
    "none": "未验证",
    "conceptual": "概念论证",
    "simulation": "仿真验证",
    "benchmark": "基准测试",
    "field_observation": "现场观测",
    "engineering_application": "工程应用",
}
_STRENGTH_LABELS = {
    "strong": "强",
    "moderate": "中等",
    "limited": "有限",
    "uncertain": "不确定",
}
_ROLE_LABELS = {
    "background": "研究背景",
    "problem_definition": "问题界定",
    "method_comparison": "方法比较",
    "result_comparison": "结果比较",
    "mechanism_explanation": "机制解释",
    "historical_evolution": "历史演化",
    "debate": "争议讨论",
    "limitation": "局限说明",
}


def render_paper_understanding_report(
    understanding: dict[str, Any],
    *,
    materials: Iterable[dict[str, Any]],
    evidence_units: Iterable[dict[str, Any]],
) -> str:
    material_by_id = {
        str(row["material_id"]): row
        for row in materials
    }
    evidence_by_id = {
        str(row["evidence_unit_id"]): row
        for row in evidence_units
    }
    lines = [
        "# 论文认知报告",
        "",
        f"- 论文：{understanding['paper_title']}",
        f"- 综述主题：{understanding['topic']}",
        f"- 主题相关性：{_RELEVANCE_LABELS.get(str(understanding['paper_relevance']), understanding['paper_relevance'])}",
        f"- Card总数：{len(material_by_id)}",
        f"- 已有严格Evidence：{len(evidence_by_id)}",
        "",
        "> 本报告用于理解整篇论文。方法、贡献、结果和限制均展开原始Card位置与摘录；Evidence仅表示此前已经严格核验的事实锚点。",
        "",
        "## 研究问题",
        "",
    ]
    for index, row in enumerate(understanding["research_questions"], start=1):
        lines.extend(
            [
                f"### 问题 {index}",
                "",
                str(row["question"]),
                "",
                f"- 问题类别：{row['problem_category']}",
            ]
        )
        _append_sources(lines, row["material_ids"], material_by_id)

    context = understanding["study_context"]
    lines.extend(
        [
            "",
            "## 研究对象与材料",
            "",
            f"- 研究对象：{context['study_object']}",
            f"- 数据来源：{', '.join(context['data_sources'])}",
            f"- 时间范围：{context['time_scope'] or '原文未明确报告'}",
            f"- 地理范围：{context['geographic_scope'] or '原文未明确报告'}",
        ]
    )
    _append_sources(lines, context["material_ids"], material_by_id)

    lines.extend(["", "## 技术方法", ""])
    for index, row in enumerate(understanding["methods"], start=1):
        lines.extend(
            [
                f"### 方法 {index}：{row['method_name']}",
                "",
                f"- 方法类别：{row['method_category']}",
                f"- 作用：{row['description']}",
            ]
        )
        _append_sources(lines, row["material_ids"], material_by_id)

    lines.extend(["", "## 主要贡献与结果性质", ""])
    for index, row in enumerate(understanding["contributions"], start=1):
        lines.extend(
            [
                f"### 贡献 {index}",
                "",
                str(row["statement"]),
                "",
                f"- 结果类型：{row['result_type']}",
                f"- 验证水平：{_VALIDATION_LABELS.get(str(row['validation_level']), row['validation_level'])}",
                f"- 证据强度：{_STRENGTH_LABELS.get(str(row['evidence_strength']), row['evidence_strength'])}",
                f"- 判断依据：{row['strength_rationale']}",
            ]
        )
        _append_sources(lines, row["material_ids"], material_by_id)
        _append_evidence(lines, row["evidence_unit_ids"], evidence_by_id)

    lines.extend(["", "## 适用边界与限制", ""])
    if understanding["limitations"]:
        for index, row in enumerate(understanding["limitations"], start=1):
            lines.extend(
                [
                    f"### 限制 {index}",
                    "",
                    str(row["statement"]),
                    "",
                    f"- 判断来源：{'作者明确陈述' if row['basis'] == 'author_stated' else '基于原文的审阅推断'}",
                ]
            )
            _append_sources(lines, row["material_ids"], material_by_id)
    else:
        lines.append("本次分析未识别出有原文依据的明确限制。")

    lines.extend(["", "## 在综述中的用途", ""])
    if understanding["review_roles"]:
        for index, row in enumerate(understanding["review_roles"], start=1):
            contribution_numbers = "、".join(
                str(value) for value in row["contribution_indexes"]
            )
            lines.extend(
                [
                    f"### 用途 {index}：{_ROLE_LABELS.get(str(row['role']), row['role'])}",
                    "",
                    f"- 理由：{row['reason']}",
                    f"- 对应贡献：{contribution_numbers}",
                ]
            )
    else:
        lines.append("该论文不进入当前主题综述。")

    lines.extend(["", "## 尚未回答的问题", ""])
    if understanding["unresolved_questions"]:
        for index, row in enumerate(understanding["unresolved_questions"], start=1):
            lines.append(f"{index}. {row['question']}（{row['basis']}）")
    else:
        lines.append("本次分析未提出额外的论文范围内未决问题。")

    lines.extend(
        [
            "",
            "## 关键词",
            "",
            "、".join(understanding["keywords"]),
            "",
        ]
    )
    return "\n".join(lines)


def _append_sources(
    lines: list[str],
    material_ids: Iterable[str],
    material_by_id: dict[str, dict[str, Any]],
) -> None:
    lines.extend(["", "原文依据：", ""])
    for material_id in material_ids:
        material = material_by_id[str(material_id)]
        title = (
            str(material.get("clean_title") or material.get("raw_title") or "未命名Card")
        )
        span = material.get("source_span") or {}
        path = str(span.get("path") or material.get("source_path") or "")
        start_line = span.get("start_line")
        end_line = span.get("end_line")
        location = path
        if start_line is not None:
            location += f" L{start_line}"
            if end_line is not None and end_line != start_line:
                location += f"-L{end_line}"
        lines.extend(
            [
                f"- **{title}**（{location or '位置未报告'}）",
                "",
            ]
        )
        extract = str(material.get("extract", "")).strip()
        lines.extend(
            f"  > {line}" if line else "  >"
            for line in (extract.splitlines() or ["无可显示摘录"])
        )


def _append_evidence(
    lines: list[str],
    evidence_ids: Iterable[str],
    evidence_by_id: dict[str, dict[str, Any]],
) -> None:
    ids = list(evidence_ids)
    if not ids:
        lines.extend(["", "- 严格Evidence：无；当前贡献仅由Card和完整Markdown支持。"])
        return
    lines.extend(["", "已核验Evidence：", ""])
    for evidence_id in ids:
        evidence = evidence_by_id[str(evidence_id)]
        lines.append(f"- {evidence.get('claim', '无可显示陈述')}")
        for citation in evidence.get("citations", []):
            quote = str(citation.get("quote", "")).strip()
            source = citation.get("source_ref") or {}
            if quote:
                lines.append(
                    f"  - 原文引文（L{source.get('start_line', '?')}-L{source.get('end_line', '?')}）：{quote}"
                )
