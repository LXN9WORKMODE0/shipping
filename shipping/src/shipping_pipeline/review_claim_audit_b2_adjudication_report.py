from __future__ import annotations

from typing import Any


def render_adjudication_report(
    manifest: dict[str, Any],
    result: dict[str, Any],
    adjudication_input: dict[str, Any],
) -> str:
    summary = result["summary"]
    claim_by_id = {
        row["claim_id"]: row for row in adjudication_input["candidate_claims"]
    }
    window_by_id = {
        row["window_id"]: row for row in adjudication_input["source_windows"]
    }
    lines = [
        f"# B2 风险句裁决：{adjudication_input['section']['title']}",
        "",
        "## 结论",
        "",
        f"- 初审风险句：{summary['source_risk_sentence_count']}",
        f"- 驳回风险：{summary['dismissed_count']}",
        f"- 确认风险：{summary['confirmed_count']}",
        f"- 重分类：{summary['reclassified_count']}",
        f"- 最终待修订：{summary['final_risk_sentence_count']}",
        f"- 最终阻断：{summary['final_blocking_sentence_count']}",
        (
            "- 裁决后可发布："
            f"`{summary['publishable_after_adjudication']}`"
        ),
        "",
        "裁决模型没有发布权限；最终风险、动作和门禁均由程序计算。",
        "",
        "## 逐句裁决",
        "",
    ]
    source_by_id = {
        row["sentence_id"]: row
        for row in adjudication_input["risk_sentences"]
    }
    for index, row in enumerate(result["adjudications"], start=1):
        source = source_by_id[row["sentence_id"]]
        lines.extend(
            [
                (
                    f"### {index}. 第 {source['paragraph_index']} 段"
                    f"第 {source['sentence_index']} 句"
                ),
                "",
                f"> {row['sentence_text']}",
                "",
                (
                    "- 初审风险：`"
                    + ", ".join(row["initial_risk_categories"])
                    + "`"
                ),
                f"- 裁决：`{row['verdict']}`",
                (
                    "- 最终风险：`"
                    + ", ".join(row["final_risk_categories"])
                    + "`"
                ),
                f"- 最终阻断：`{row['blocking']}`",
                f"- 理由：{row['reason']}",
            ]
        )
        if row["recommended_actions"]:
            lines.append(
                "- 程序建议动作："
                + ", ".join(
                    f"`{value}`" for value in row["recommended_actions"]
                )
            )
        lines.extend(["", "**最终映射 Claim**", ""])
        if not row["final_planned_claim_ids"]:
            lines.append("- 无")
        for claim_id in row["final_planned_claim_ids"]:
            claim = claim_by_id[claim_id]
            lines.append(f"- `{claim_id}`：{claim['planned_claim']}")
        if row["requires_revision"]:
            lines.extend(["", "**相关来源窗口**", ""])
            emitted = set()
            for claim_id in source["paragraph_claim_ids"]:
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
            result["chapter_assessment"],
            "",
            "## 运行标识",
            "",
            f"- adjudication run：`{manifest['run_id']}`",
            f"- audit run：`{manifest['audit_run_id']}`",
            f"- adjudication ID：`{result['adjudication_id']}`",
            "",
        ]
    )
    return "\n".join(lines)
