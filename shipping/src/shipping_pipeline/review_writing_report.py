from __future__ import annotations

from typing import Any


def render_review_writing_report(manifest: dict[str, Any]) -> str:
    lines = [
        "# 综述章节写作运行报告",
        "",
        f"- 运行ID：`{manifest['run_id']}`",
        f"- 状态：{_status_label(manifest['status'])}",
        f"- Framework运行：`{manifest.get('framework_run_id') or '未建立'}`",
        f"- 知识包：{manifest['package_count']}个",
        (
            f"- 章节：成功{manifest['completed_chapter_count']}章，"
            f"失败{manifest['failed_chapter_count']}章"
        ),
        f"- 正式综述：{'已发布' if manifest['formal_review_published'] else '未发布'}",
        (
            f"- Token：输入{manifest['usage']['prompt_tokens']}，"
            f"输出{manifest['usage']['completion_tokens']}，"
            f"合计{manifest['usage']['total_tokens']}"
        ),
        f"- 全文编辑：{manifest['final_edit']['status']}",
        "",
        "## 章节明细",
        "",
        "| 章 | 标题 | 状态 | 输入Token | 输出Token |",
        "|---:|---|---|---:|---:|",
    ]
    for row in manifest["chapter_statuses"]:
        stage = row.get("stage_result") or {}
        usage = stage.get("usage") or {}
        lines.append(
            f"| {row['section_index']} | {_escape(row['section_title'])} | "
            f"{_status_label(row['status'])} | "
            f"{usage.get('prompt_tokens', '—')} | "
            f"{usage.get('completion_tokens', '—')} |"
        )
    if manifest["failures"]:
        lines.extend(["", "## 失败记录", ""])
        for failure in manifest["failures"]:
            prefix = (
                f"第{failure['section_index']}章 "
                if "section_index" in failure
                else ""
            )
            lines.append(
                f"- {prefix}`{failure['error_code']}`："
                f"{failure['error_message']}"
            )
    lines.extend(
        [
            "",
            "## 发布判断",
            "",
            (
                "所有章节均完成，已由代码确定性装配正式综述。"
                if manifest["formal_review_published"]
                else "至少一个章节未完成，因此没有发布正式综述；已保留成功章节和失败账本。"
            ),
            (
                "最终全文编辑尚未运行。它必须等待Claim审计全部通过，"
                "编辑后还要重新审计。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _status_label(value: str) -> str:
    return {
        "completed": "完成",
        "failed": "失败",
    }.get(value, value)


def _escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
