from __future__ import annotations

from typing import Any


GROUP_LABELS = {
    "overall_findings": "总体发现",
    "path_comparison": "路径比较",
    "corpus_limits": "样本文献局限",
    "future_direction": "未来方向",
}


def render_review_conclusion_b2_report(
    manifest: dict[str, Any],
    conclusion: dict[str, Any],
    conclusion_input: dict[str, Any],
) -> str:
    claims = {
        row["claim_id"]: row for row in conclusion_input["eligible_claims"]
    }
    lines = [
        f"# {conclusion['section_index']}. {conclusion['title']}",
        "",
        f"- 结论章ID：`{conclusion['chapter_id']}`",
        f"- 来源：受审计正文 `{manifest['audited_assembly_run_id']}`",
        f"- 正文字符：{conclusion['total_chars']}",
        f"- 使用来源Claim：{len(conclusion['implemented_source_claim_ids'])}",
        f"- 覆盖前文章节：{conclusion['covered_section_indexes']}",
        "- 发布状态：候选结论，等待独立结论审计",
        "",
    ]
    for paragraph in conclusion["paragraphs"]:
        lines.extend(
            [
                f"## {paragraph['paragraph_index']}. "
                f"{GROUP_LABELS[paragraph['group']]}",
                "",
                paragraph["text"],
                "",
                f"结论类型：`{paragraph['claim_type']}`",
                "",
                "来源Claim：",
            ]
        )
        for claim_id in paragraph["source_claim_ids"]:
            claim = claims[claim_id]
            lines.append(
                f"- `{claim_id}`（第{claim['section_index']}节，"
                f"{claim['claim_type']}）：{claim['planned_claim']}"
            )
        citations = "、".join(paragraph["citation_keys"]) or "无"
        lines.extend([f"- 引用：{citations}", ""])
    return "\n".join(lines).rstrip() + "\n"
