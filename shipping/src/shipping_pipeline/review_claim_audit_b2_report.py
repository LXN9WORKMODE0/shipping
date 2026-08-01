from __future__ import annotations

from typing import Any


def render_review_claim_audit_b2_report(
    manifest: dict[str, Any],
    audit: dict[str, Any],
    audit_input: dict[str, Any],
) -> str:
    summary = audit["summary"]
    claim_by_id = {
        row["claim_id"]: row for row in audit_input["implemented_claims"]
    }
    window_by_id = {
        row["window_id"]: row for row in audit_input["source_windows"]
    }
    lines = [
        f"# B2 Claim 审计：{audit_input['section']['title']}",
        "",
        "## 结论",
        "",
        f"- 运行状态：`{manifest['status']}`",
        f"- 审计通过：`{summary['audit_passed']}`",
        f"- 可发布：`{summary['publishable']}`",
        f"- 句子数：{summary['sentence_count']}",
        f"- 需修订句子：{summary['revision_sentence_count']}",
        f"- 阻断句子：{summary['blocking_sentence_count']}",
        f"- 人工确认候选：{summary['human_confirmation_count']}",
        "",
        "当前报告只表示本章写后语义审计结果。可发布状态由程序计算，"
        "不是模型自行给出的判断。",
        "",
        "## 风险统计",
        "",
        "| 分类 | 数量 |",
        "| --- | ---: |",
    ]
    for risk, count in summary["risk_counts"].items():
        lines.append(f"| `{risk}` | {count} |")
    lines.extend(["", "## 逐句审计", ""])
    for offset, sentence in enumerate(audit["sentence_audits"], start=1):
        risks = ", ".join(sentence["risk_categories"])
        lines.extend(
            [
                (
                    f"### {offset}. 第 {sentence['paragraph_index']} 段"
                    f"第 {sentence['sentence_index']} 句"
                ),
                "",
                f"> {sentence['sentence_text']}",
                "",
                f"- 角色：`{sentence['sentence_role']}`",
                f"- 重要性：`{sentence['importance']}`",
                f"- 风险：`{risks}`",
                f"- 阻断：`{sentence['blocking']}`",
                f"- 需要修订：`{sentence['requires_revision']}`",
                f"- 理由：{sentence['reason']}",
            ]
        )
        if sentence["recommended_actions"]:
            lines.append(
                "- 建议动作："
                + ", ".join(
                    f"`{value}`"
                    for value in sentence["recommended_actions"]
                )
            )
        if not sentence["planned_claim_ids"]:
            lines.extend(["- 映射 Claim：无", ""])
            continue
        lines.extend(["", "**映射 Claim**", ""])
        for claim_id in sentence["planned_claim_ids"]:
            claim = claim_by_id[claim_id]
            lines.extend(
                [
                    f"- `{claim_id}`：{claim['planned_claim']}",
                    (
                        f"  类型 `{claim['claim_type']}`，重要性 "
                        f"`{claim['importance']}`，允许强度 "
                        f"`{claim['allowed_strength']}`。"
                    ),
                ]
            )
        if sentence["requires_revision"]:
            lines.extend(["", "**相关来源窗口**", ""])
            emitted: set[str] = set()
            for claim_id in sentence["planned_claim_ids"]:
                claim = claim_by_id[claim_id]
                for window_id in claim["supporting_source_windows"]:
                    if window_id in emitted:
                        continue
                    emitted.add(window_id)
                    window = window_by_id[window_id]
                    lines.extend(
                        [
                            (
                                f"- `{window_id}` / "
                                f"`{window['citation_key']}` / "
                                f"`{window['window_type']}`"
                            ),
                            f"  > {window['text']}",
                        ]
                    )
        lines.append("")
    lines.extend(
        [
            "## 章节总评",
            "",
            audit["chapter_assessment"],
            "",
            "## 运行标识",
            "",
            f"- audit run：`{manifest['run_id']}`",
            f"- writing run：`{manifest['writing_run_id']}`",
            f"- audit ID：`{audit['audit_id']}`",
            f"- audit input ID：`{audit['audit_input_id']}`",
            "",
        ]
    )
    return "\n".join(lines)
