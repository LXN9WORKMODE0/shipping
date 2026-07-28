from __future__ import annotations

from collections import Counter
from typing import Any


_RELEVANCE_LABELS = {
    "core": "核心文献",
    "supporting": "支持性文献",
    "peripheral": "边缘相关文献",
    "exclude": "不纳入当前主题",
}
_POINT_LABELS = {
    "background": "背景",
    "definition": "定义",
    "method": "方法",
    "finding": "发现",
    "result": "结果",
    "data": "数据",
    "mechanism": "机制",
    "argument": "论点",
    "recommendation": "建议",
    "limitation": "局限",
}
_USE_LABELS = {
    "background": "背景材料",
    "support": "支持论点",
    "comparison": "研究比较",
    "method": "方法参考",
    "counterpoint": "反向或限制性材料",
}
_REVISIT_LABELS = {
    "not_triggered": "未触发",
    "no_candidate": "已回查，未找到新增候选",
    "completed": "已完成并合并补充证据",
    "failed": "回查失败，基础简报仍保留",
    "running": "运行中",
}


def render_topic_review_report(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    scope: dict[str, Any],
    brief: dict[str, Any] | None,
    evidence_units: list[dict[str, Any]],
    materials: list[dict[str, Any]],
    selected_material_ids: list[str],
    omitted_material_ids: list[str],
    status: str,
    failure: dict[str, Any] | None = None,
    initial_selected_material_ids: list[str],
    revisit: dict[str, Any] | None,
    evidence_failures: list[dict[str, Any]],
) -> str:
    material_by_id = {str(row["material_id"]): row for row in materials}
    evidence_by_id = {str(row["evidence_unit_id"]): row for row in evidence_units}
    point_numbers_by_evidence: dict[str, list[int]] = {}
    if brief is not None:
        for point_number, item in enumerate(brief["key_points"], start=1):
            for evidence_id in item["evidence_unit_ids"]:
                point_numbers_by_evidence.setdefault(str(evidence_id), []).append(point_number)
    relevance = str(scope.get("paper_relevance", ""))
    lines = [
        "# 主题驱动论文材料简报",
        "",
        f"- 论文：{paper_title}",
        f"- 论文ID：`{paper_id}`",
        f"- 综述主题：{topic}",
        f"- 处理状态：{status}",
        f"- 主题相关性：{_RELEVANCE_LABELS.get(relevance, relevance or '未判定')}",
        f"- 入选 Card：{len(selected_material_ids)}",
        f"- 首次入选 Card：{len(initial_selected_material_ids)}",
        f"- 回查补选 Card：{len((revisit or {}).get('recovered_material_ids', []))}",
        f"- 未入选 Card：{len(omitted_material_ids)}",
        "",
        "## 定界结论",
        "",
        "> 本节是用于判断论文是否进入当前综述的模型归纳，未做逐句引证校验，不应直接作为综述引文。",
        "",
        str(scope.get("relevance_reason", "尚未形成定界结论。")),
        "",
        f"**主题概括：** {scope.get('topic_summary', '无')}",
    ]
    if revisit is not None:
        revisit_status = str(revisit.get("status", "not_triggered"))
        lines.extend(
            [
                "",
                "## 自动回查",
                "",
                f"- 状态：{_REVISIT_LABELS.get(revisit_status, revisit_status)}",
                f"- 扫描未入选 Card：{revisit.get('scanned_material_count', 0)} 张",
                f"- 回查候选 Card：{len(revisit.get('candidate_materials', []))} 张",
                f"- 成功补选 Card：{len(revisit.get('recovered_material_ids', []))} 张",
                f"- 说明：{revisit.get('review_summary', '无')}",
            ]
        )
        gaps = list(revisit.get("gaps", []))
        if gaps:
            lines.extend(["", "本轮回查问题：", ""])
            for index, gap in enumerate(gaps, start=1):
                lines.append(f"{index}. {gap.get('question', '无')}")
        candidates = list(revisit.get("candidate_materials", []))
        if candidates:
            lines.extend(["", "回查候选：", ""])
            for row in candidates:
                material = material_by_id.get(str(row.get("material_id")), {})
                lines.append(
                    f"- {_material_title(material)}：{row.get('selection_reason', '无')}"
                )
                lines.append(f"  - 新增性：{row.get('novelty_reason', '无')}")
        revisit_failure = revisit.get("failure")
        if isinstance(revisit_failure, dict):
            lines.extend(
                [
                    "",
                    f"- 回查错误代码：`{revisit_failure.get('error_code', 'unknown')}`",
                    f"- 回查错误说明：{revisit_failure.get('error_message', '无')}",
                    "- 处理：未将部分回查结果并入正式简报，基础 Evidence 和基础简报保持有效。",
                ]
            )
    if failure is not None:
        lines.extend(
            [
                "",
                "## 阻断问题",
                "",
                f"- 错误代码：`{failure.get('error_code', 'unknown')}`",
                f"- 阶段：{failure.get('stage', 'unknown')}",
                f"- 说明：{failure.get('error_message', '无')}",
            ]
        )
    if evidence_failures:
        lines.extend(
            [
                "",
                "## Evidence 失败账本",
                "",
                f"共有 {len(evidence_failures)} 张已入选 Card 未通过证据合同；其余 Card 继续处理。",
                "",
            ]
        )
        for index, row in enumerate(evidence_failures, start=1):
            material = material_by_id.get(str(row.get("material_id")), {})
            lines.extend(
                [
                    f"### 失败材料 {index}：{_material_title(material)}",
                    "",
                    f"- 原文位置：{_source_label(material)}",
                    f"- 错误代码：`{row.get('error_code', 'unknown')}`",
                    f"- 说明：{row.get('error_message', '无')}",
                    "- 处理：本 Card 未生成 Evidence，未进入简报；其他已验证 Evidence 不受影响。",
                    "",
                    "Card 原文：",
                    "",
                ]
            )
            extract = str(material.get("extract", "")).strip()
            if extract:
                lines.extend(
                    f"> {line}" if line else ">"
                    for line in extract.splitlines()
                )
            else:
                lines.append("> 无可显示内容")
            lines.append("")
    if brief is not None:
        lines.extend(
            [
                "",
                "## 模型归纳的综述贡献",
                "",
                "> 本节及后续“综述用途”“引用注意事项”用于导航；具体事实和可引用表述以“可用于综述的观点”及其原文为准。",
                "",
            ]
        )
        _append_compact_item(
            lines,
            brief["topic_contribution"],
            point_numbers_by_evidence,
            evidence_by_id,
        )

        lines.extend(["", "## 建议的综述用途", ""])
        for index, item in enumerate(brief["review_uses"], start=1):
            label = _USE_LABELS.get(str(item.get("use_type", "")), str(item.get("use_type", "")))
            lines.append(f"### 用途 {index}：{label}")
            lines.append("")
            _append_compact_item(lines, item, point_numbers_by_evidence, evidence_by_id)

        lines.extend(["", "## 可用于综述的观点", ""])
        for index, item in enumerate(brief["key_points"], start=1):
            label = _POINT_LABELS.get(str(item.get("point_type", "")), str(item.get("point_type", "")))
            lines.append(f"### 观点 {index}：{label}")
            lines.append("")
            _append_reference_item(lines, item, evidence_by_id)

        lines.extend(["", "## 引用注意事项", ""])
        if brief["cautions"]:
            for index, item in enumerate(brief["cautions"], start=1):
                lines.append(f"### 注意 {index}")
                lines.append("")
                _append_compact_item(lines, item, point_numbers_by_evidence, evidence_by_id)
        else:
            lines.append("当前入选证据没有形成额外的引用注意事项。")

    selected_scope = {
        str(row["material_id"]): row
        for row in scope.get("selected_materials", [])
        if isinstance(row, dict) and row.get("material_id")
    }
    if revisit is not None:
        selected_scope.update(
            {
                str(row["material_id"]): row
                for row in revisit.get("candidate_materials", [])
                if isinstance(row, dict) and row.get("material_id")
            }
        )
    initial_selected_set = set(initial_selected_material_ids)
    lines.extend(["", "## 入选材料", ""])
    if selected_material_ids:
        for index, material_id in enumerate(selected_material_ids, start=1):
            material = material_by_id[material_id]
            selection = selected_scope.get(material_id, {})
            lines.extend(
                [
                    f"### 材料 {index}：{_material_title(material)}",
                    "",
                    f"- 入选阶段：{'首次定界' if material_id in initial_selected_set else '自动回查'}",
                    f"- 原文位置：{_source_label(material)}",
                    f"- 选择原因：{selection.get('selection_reason', '无')}",
                    f"- 预期用途：{selection.get('intended_use', '无')}",
                ]
            )
    else:
        lines.append("没有材料进入严格证据抽取。")

    omitted = [material_by_id[material_id] for material_id in omitted_material_ids]
    heading_counts = Counter(_heading_label(row) for row in omitted)
    lines.extend(["", "## 未入选材料概况", ""])
    lines.append(f"共有 {len(omitted)} 张 Card 未进入严格证据抽取。")
    if heading_counts:
        lines.append("")
        lines.append("按章节或标题统计：")
        lines.append("")
        for label, count in heading_counts.most_common():
            lines.append(f"- {label}：{count} 张")

    selected_flag_count = sum(bool(_parse_flags(material_by_id[row])) for row in selected_material_ids)
    omitted_flag_count = sum(bool(_parse_flags(material_by_id[row])) for row in omitted_material_ids)
    lines.extend(
        [
            "",
            "## 解析状态",
            "",
            f"- 入选材料中带解析标记：{selected_flag_count} 张。",
            f"- 未入选材料中带解析标记：{omitted_flag_count} 张；这些标记没有阻断本次运行。",
            "",
            "## 机器追踪信息",
            "",
        ]
    )
    if evidence_units:
        for unit in evidence_units:
            lines.append(f"- `{unit['evidence_unit_id']}`：{unit['claim']}")
    else:
        lines.append("- 本次没有生成 Evidence Unit。")
    return "\n".join(lines).rstrip() + "\n"


def _append_reference_item(
    lines: list[str],
    item: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
) -> None:
    lines.append(str(item["statement"]))
    lines.append("")
    for evidence_id in item["evidence_unit_ids"]:
        unit = evidence_by_id[str(evidence_id)]
        if " ".join(str(unit["claim"]).split()) != " ".join(str(item["statement"]).split()):
            lines.append(f"- 证据观点：{unit['claim']}")
        for citation in unit.get("citations", []):
            title = str(citation.get("card_title") or citation.get("material_id") or "未命名材料")
            lines.append(f"  - 来源：{title}，{_citation_source_label(citation)}")
            lines.append(f"  - 原文：{citation.get('quote', '')}")


def _append_compact_item(
    lines: list[str],
    item: dict[str, Any],
    point_numbers_by_evidence: dict[str, list[int]],
    evidence_by_id: dict[str, dict[str, Any]],
) -> None:
    lines.append(str(item["statement"]))
    point_numbers = sorted(
        {
            number
            for evidence_id in item["evidence_unit_ids"]
            for number in point_numbers_by_evidence.get(str(evidence_id), [])
        }
    )
    if point_numbers:
        labels = "、".join(f"观点{number}" for number in point_numbers)
        lines.append("")
        lines.append(f"依据：{labels}。")
        return
    lines.append("")
    lines.append("依据原文：")
    for evidence_id in item["evidence_unit_ids"]:
        unit = evidence_by_id[str(evidence_id)]
        for citation in unit.get("citations", []):
            lines.append(
                f"- {citation.get('card_title') or citation.get('material_id')}，"
                f"{_citation_source_label(citation)}：{citation.get('quote', '')}"
            )


def _citation_source_label(citation: dict[str, Any]) -> str:
    source = citation.get("source_ref") or {}
    path = str(source.get("path") or "normalized/document.md")
    start = source.get("start_line")
    end = source.get("end_line")
    if type(start) is int and type(end) is int:
        return f"{path} L{start}-L{end}"
    return path


def _source_label(material: dict[str, Any]) -> str:
    source = material.get("source_span") or material.get("content_ref") or {}
    path = str(source.get("path") or material.get("source_path") or "normalized/document.md")
    start = source.get("start_line")
    end = source.get("end_line")
    if type(start) is int and type(end) is int:
        return f"{path} L{start}-L{end}"
    return path


def _material_title(material: dict[str, Any]) -> str:
    return str(
        material.get("clean_title")
        or material.get("raw_title")
        or material.get("paper_title")
        or material.get("material_id")
    )


def _heading_label(material: dict[str, Any]) -> str:
    headings = material.get("heading_path") or []
    if isinstance(headings, list) and headings:
        return " > ".join(str(row) for row in headings)
    return _material_title(material)


def _parse_flags(material: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *[str(row) for row in material.get("confidence_flags", [])],
                *[str(row) for row in material.get("quality_flags", [])],
            ]
        )
    )
