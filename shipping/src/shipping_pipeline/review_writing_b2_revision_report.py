from __future__ import annotations

from typing import Any


def render_writing_revision_report(
    manifest: dict[str, Any],
    chapter: dict[str, Any],
) -> str:
    audit = chapter["revision_audit"]
    lines = [
        f"# B2 章节修订：{chapter['title']}",
        "",
        "## 运行结论",
        "",
        f"- 来源写作运行：`{chapter['source_writing_run_id']}`",
        f"- 来源章节：`{chapter['source_chapter_id']}`",
        f"- 修订建议运行：`{chapter['revision_suggestion_run_id']}`",
        f"- 决策集合：`{chapter['decision_set_id']}`",
        f"- 新章节：`{chapter['chapter_id']}`",
        f"- 应用句子：{audit['selected_sentence_count']}",
        f"- 删除：{audit['deleted_sentence_count']}",
        f"- 替换：{audit['replaced_sentence_count']}",
        f"- 拆句：{audit['split_sentence_count']}",
        f"- 语义审计：`{audit['semantic_claim_audit']}`",
        "",
        "旧章节和旧运行均未被覆盖。当前revision必须重新通过逐句Claim审计后才能参与发布装配。",
        "",
        "## 逐句差异",
        "",
    ]
    for index, change in enumerate(audit["changes"], start=1):
        lines.extend(
            [
                f"### {index}. 第 {change['paragraph_index']} 段第 {change['sentence_index']} 句",
                "",
                f"- 动作：`{change['operation']}`",
                f"- 方案：`{change['option_type']}`",
                f"- option_id：`{change['option_id']}`",
                f"- 理由：{change['rationale']}",
                "",
                "```diff",
                f"- {change['original_text']}",
            ]
        )
        if change["replacement_sentences"]:
            lines.extend(
                f"+ {value}" for value in change["replacement_sentences"]
            )
        else:
            lines.append("+ [删除该句]")
        lines.extend(["```", ""])
    lines.extend(["## 修订后正文", ""])
    for paragraph in chapter["paragraphs"]:
        lines.extend(
            [
                f"### 段落 {paragraph['paragraph_index']}",
                "",
                paragraph["text"],
                "",
                "实现Claim：",
            ]
        )
        lines.extend(
            f"- `{claim_id}`" for claim_id in paragraph["implemented_claim_ids"]
        )
        lines.append(
            "- 引用：" + "、".join(paragraph["citation_keys"])
        )
        lines.append("")
    lines.extend(
        [
            "## 运行标识",
            "",
            f"- revision run：`{manifest['run_id']}`",
            f"- output sha256：`{manifest['output_sha256']}`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
