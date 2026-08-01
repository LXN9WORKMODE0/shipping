from __future__ import annotations

from typing import Any


OPTION_LABELS = {
    "minimal_edit": "方案 A：最小修改",
    "conservative_rewrite": "方案 B：保守改写",
    "delete_or_split": "方案 C：删除或拆分",
}
OPERATION_LABELS = {
    "replace": "替换",
    "delete": "删除",
    "split": "拆句",
}


def render_revision_suggestion_report(
    manifest: dict[str, Any],
    result: dict[str, Any],
    revision_input: dict[str, Any],
) -> str:
    source_by_id = {
        row["sentence_id"]: row for row in revision_input["risk_sentences"]
    }
    claim_by_id = {
        row["claim_id"]: row for row in revision_input["candidate_claims"]
    }
    windows_by_claim = {
        claim_id: [
            window
            for window in revision_input["source_windows"]
            if window["window_id"]
            in set(claim_by_id[claim_id]["supporting_source_windows"])
        ]
        for claim_id in claim_by_id
    }
    lines = [
        f"# B2 风险句修订建议：{revision_input['section']['title']}",
        "",
        "## 使用说明",
        "",
        "本报告只提供候选方案，不会自动修改或发布正文。每个方案均保留原句、绑定Claim和来源窗口，选择后仍需创建新revision并重新审计。",
        "",
        "## 汇总",
        "",
        f"- 待处理风险句：{result['summary']['risk_sentence_count']}",
        f"- 候选方案：{result['summary']['option_count']}",
        f"- 阻断句：{result['summary']['blocking_sentence_count']}",
        f"- 决策状态：`{result['decision_status']}`",
        f"- 已自动应用：`{result['auto_applied']}`",
        "",
    ]
    for item_index, row in enumerate(result["suggestions"], start=1):
        source = source_by_id[row["sentence_id"]]
        lines.extend(
            [
                f"## {item_index}. 第 {source['paragraph_index']} 段第 {source['sentence_index']} 句",
                "",
                "**原句**",
                "",
                f"> {source['sentence_text']}",
                "",
                f"- 最终风险：`{', '.join(source['final_risk_categories'])}`",
                f"- 是否阻断：`{source['blocking']}`",
                f"- 裁决理由：{source['adjudication_reason']}",
                "",
                "**所在段落**",
                "",
                f"> {source['paragraph_text']}",
                "",
            ]
        )
        for option in row["revision_options"]:
            lines.extend(
                [
                    f"### {OPTION_LABELS[option['option_type']]}",
                    "",
                    f"- 动作：`{OPERATION_LABELS[option['operation']]}`",
                    f"- option_id：`{option['option_id']}`",
                    f"- 理由：{option['rationale']}",
                    "",
                    "```diff",
                    f"- {source['sentence_text']}",
                ]
            )
            if option["operation"] == "delete":
                lines.append("+ [删除该句]")
            else:
                lines.extend(
                    f"+ {sentence}"
                    for sentence in option["replacement_sentences"]
                )
            lines.extend(["```", "", "**绑定 Claim**", ""])
            if option["retained_claim_ids"]:
                for claim_id in option["retained_claim_ids"]:
                    claim = claim_by_id[claim_id]
                    lines.append(
                        f"- `{claim_id}`：{claim['planned_claim']}"
                    )
            else:
                lines.append("- 无")
            lines.extend(["", "**引用**", ""])
            if option["citation_keys"]:
                lines.extend(
                    f"- `{citation}`" for citation in option["citation_keys"]
                )
            else:
                lines.append("- 无")
            lines.append("")
        lines.extend(["**相关来源窗口**", ""])
        window_ids: set[str] = set()
        for claim_id in source["paragraph_claim_ids"]:
            for window in windows_by_claim.get(claim_id, []):
                if window["window_id"] in window_ids:
                    continue
                window_ids.add(window["window_id"])
                lines.extend(
                    [
                        f"- `{window['window_id']}` / `{window['citation_key']}` / `{window['window_type']}`",
                        f"  > {_compact(window['text'])}",
                    ]
                )
        if not window_ids:
            lines.append("- 无")
        lines.append("")
    lines.extend(
        [
            "## 章节总评",
            "",
            result["chapter_assessment"],
            "",
            "## 运行标识",
            "",
            f"- revision suggestion run：`{manifest['run_id']}`",
            f"- adjudication run：`{manifest['adjudication_run_id']}`",
            f"- suggestion ID：`{result['revision_suggestion_id']}`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _compact(value: str, limit: int = 1200) -> str:
    text = "\n> ".join(line.strip() for line in value.splitlines() if line.strip())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "……"
