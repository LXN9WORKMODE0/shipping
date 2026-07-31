from __future__ import annotations

from typing import Any


def render_b2_preview_package_report(
    package: dict[str, Any],
    budget: dict[str, Any],
) -> str:
    section = package["section_task"]
    coverage = package["coverage"]
    components = budget["component_tokens"]
    lines = [
        "# B2 Phase 1 精简知识包报告",
        "",
        "> 本产物尚未包含 Claim Ledger，`writing_ready=false`，"
        "不得用于正式综述写作。",
        "",
        f"- 章节：第{section['section_index']}章 {section['title']}",
        f"- Material Tier：`{package['material_tier']}`",
        f"- 论文数：{coverage['paper_count']}",
        f"- Source Window：{coverage['window_count']}",
        f"- Prompt Token：{budget['planned_input_tokens']}",
        f"- 上下文窗口：{budget['context_window_tokens']}",
        f"- 预算状态：{'通过' if budget['within_budget'] else '超限'}",
        "",
        "## Token 组成",
        "",
        "| 组件 | Token |",
        "| --- | ---: |",
    ]
    for name, value in components.items():
        lines.append(f"| `{name}` | {value} |")
    lines.extend(
        [
            f"| 组件合计 | {budget['component_token_sum']} |",
            "",
            "## 来源覆盖",
            "",
            (
                "- Contribution："
                f"{len(coverage['covered_contribution_ids'])}/"
                f"{len(coverage['required_contribution_ids'])}"
            ),
            (
                "- Material："
                f"{len(coverage['covered_material_ids'])}/"
                f"{len(coverage['required_material_ids'])}"
            ),
            (
                "- 完整Markdown展开："
                f"{len(package['expanded_markdown_sources'])}篇"
            ),
            "",
            "## 状态边界",
            "",
            "- 已完成：章节任务、Understanding投影、Source Window、题录投影。",
            "- 未完成：Claim Ledger、受约束写作、结论章合同和B2审计。",
            "- 本运行只能用于Phase 1上下文与Token验收。",
            "",
        ]
    )
    return "\n".join(lines)
