from __future__ import annotations

from typing import Any


def render_source_window_report(selection: dict[str, Any]) -> str:
    section = selection["section"]
    coverage = selection["coverage"]
    lines = [
        "# Source Window 选择报告",
        "",
        f"- 章节：第{section['section_index']}章 {section['title']}",
        f"- 章节ID：`{section['section_id']}`",
        f"- Material Tier：`{selection['material_tier']}`",
        f"- 论文数：{coverage['paper_count']}",
        f"- 窗口数：{coverage['window_count']}",
        f"- 必需Contribution：{len(coverage['required_contribution_ids'])}",
        f"- 必需Material：{len(coverage['required_material_ids'])}",
        f"- 去重候选事件：{coverage['deduplicated_candidate_count']}",
        "",
        "## 覆盖结论",
        "",
        (
            "- Contribution覆盖："
            f"{len(coverage['covered_contribution_ids'])}/"
            f"{len(coverage['required_contribution_ids'])}"
        ),
        (
            "- Material覆盖："
            f"{len(coverage['covered_material_ids'])}/"
            f"{len(coverage['required_material_ids'])}"
        ),
        (
            "- 未覆盖Contribution："
            + (
                "无"
                if not coverage["uncovered_contribution_ids"]
                else "、".join(coverage["uncovered_contribution_ids"])
            )
        ),
        (
            "- 未覆盖Material："
            + (
                "无"
                if not coverage["uncovered_material_ids"]
                else "、".join(coverage["uncovered_material_ids"])
            )
        ),
        "",
        "## 上下文类型",
        "",
        "| 类型 | 窗口数 |",
        "| --- | ---: |",
    ]
    for name, count in coverage["window_type_counts"].items():
        lines.append(f"| `{name}` | {count} |")
    lines.extend(
        [
            "",
            "## 上下文缺口",
            "",
            (
                "- 未命中方法上下文的论文："
                + (
                    "无"
                    if not coverage["papers_without_method_context"]
                    else "、".join(
                        coverage["papers_without_method_context"]
                    )
                )
            ),
            (
                "- 未命中限制上下文的论文："
                + (
                    "无"
                    if not coverage["papers_without_limitation_context"]
                    else "、".join(
                        coverage["papers_without_limitation_context"]
                    )
                )
            ),
            "",
            "以上缺口只表示选中Contribution的来源Card没有同时绑定对应类型，",
            "不自动补充其他Card，也不自动升级Material Tier。",
            "",
            "## 窗口明细",
            "",
        ]
    )
    for window in selection["selected_source_windows"]:
        spans = "；".join(
            (
                f"{row['path']}:L{row['start_line']}-L{row['end_line']}"
                f":C{row['start_char']}-{row['end_char']}"
            )
            for row in window["source_spans"]
        )
        lines.extend(
            [
                f"### {window['paper_id']} / {window['window_type']}",
                "",
                f"- Window ID：`{window['window_id']}`",
                f"- Citation：`{window['citation_key']}`",
                f"- Contribution：{', '.join(window['contribution_ids'])}",
                f"- Material：{', '.join(window['material_ids'])}",
                f"- 原文坐标：{spans}",
                f"- 选择原因：{', '.join(window['selection_reasons'])}",
                "",
                window["text"],
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
