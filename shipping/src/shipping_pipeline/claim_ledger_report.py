from __future__ import annotations

from typing import Any


def render_claim_ledger_report(
    ledger: dict[str, Any],
    rejected: list[dict[str, Any]],
) -> str:
    approved = ledger["approved_claims"]
    core_count = sum(row["importance"] == "core" for row in approved)
    lines = [
        "# Claim Ledger人工审核",
        "",
        f"- 章节：`{ledger['section_id']}`",
        f"- Ledger：`{ledger['ledger_id']}`",
        f"- 批准Claim：{len(approved)}",
        f"- 核心Claim：{core_count}",
        f"- 拒绝Claim：{len(rejected)}",
        "",
        "## 批准的Claim",
        "",
    ]
    for index, claim in enumerate(approved, start=1):
        lines.extend(
            [
                f"### {index}. {claim['planned_claim']}",
                "",
                f"- 类型：`{claim['claim_type']}`",
                f"- 重要性：`{claim['importance']}`",
                f"- 章节角色：`{claim['section_role']}`",
                f"- 允许强度：`{claim['allowed_strength']}`",
                f"- 支持论文：{_display(claim['supporting_papers'])}",
                f"- 引用Key：{_display(claim['citation_keys'])}",
                f"- 来源窗口：{len(claim['supporting_source_windows'])} 个",
                (
                    f"- 必需限定语：{claim['required_qualifier']}"
                    if claim["required_qualifier"]
                    else "- 必需限定语：无"
                ),
                f"- 禁止措辞：{_display(claim['prohibited_phrasings'])}",
                f"- 审计备注：{claim['audit_notes'] or '无'}",
                "",
                "<details>",
                "<summary>来源窗口ID</summary>",
                "",
                *[
                    f"- `{window_id}`"
                    for window_id in claim["supporting_source_windows"]
                ],
                "",
                "</details>",
                "",
            ]
        )
    lines.extend(["## 拒绝项", ""])
    if not rejected:
        lines.append("- 无。")
    else:
        for row in rejected:
            candidate = row.get("candidate")
            text = (
                candidate.get("planned_claim", "无候选文本")
                if isinstance(candidate, dict)
                else "缺少核心Claim"
            )
            lines.append(f"### 候选 {row.get('candidate_index')}: {text}")
            lines.append("")
            for error in row["errors"]:
                lines.append(f"- `{error['code']}`：{error['message']}")
            lines.append("")
    return "\n".join(lines)


def _display(values: list[str]) -> str:
    return "、".join(values) if values else "无"
