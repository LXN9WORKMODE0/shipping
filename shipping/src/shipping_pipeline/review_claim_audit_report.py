from __future__ import annotations

from collections import Counter
from typing import Any


_SOURCE_PREVIEW_LIMIT = 3
_SOURCE_TEXT_LIMIT = 280


def _preview_text(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= _SOURCE_TEXT_LIMIT:
        return normalized
    return normalized[: _SOURCE_TEXT_LIMIT - 1].rstrip() + "…"


def _requires_attention(claim: dict[str, Any]) -> bool:
    return (
        claim["status"] in {"qualified", "unsupported"}
        or claim["result_type_check"] == "overstated"
        or claim["validation_level_check"] == "overstated"
        or bool(claim["normalization_flags"])
    )


def render_claim_audit_report(
    manifest: dict[str, Any],
    *,
    audits: list[dict[str, Any]],
    package_sources: list[Any],
) -> str:
    summary = manifest["summary"]
    lines = [
        "# 综述正文Claim审计报告",
        "",
        f"- 运行ID：`{manifest['run_id']}`",
        f"- 来源写作运行：`{manifest['writing_run_id']}`",
        f"- 状态：{manifest['status']}",
        f"- 可发布：{'是' if manifest['publishable'] else '否'}",
        f"- 事实Claim：{summary['factual_claim_count']}",
        (
            "- 综述内部综合Claim："
            f"{summary.get('review_synthesis_claim_count', 0)}"
        ),
        (
            f"- 支持状态：supported {summary['supported_claim_count']}，"
            f"qualified {summary['qualified_claim_count']}，"
            f"unsupported {summary['unsupported_claim_count']}"
        ),
        f"- 核心unsupported：{summary['core_unsupported_claim_count']}",
        f"- result type写大：{summary['result_type_overstatement_count']}",
        (
            "- validation level写大："
            f"{summary['validation_level_overstatement_count']}"
        ),
        f"- 保守规范化Claim：{summary['normalized_claim_count']}",
        (
            "- Token："
            f"输入 {summary.get('usage', {}).get('prompt_tokens', 0)}，"
            f"输出 {summary.get('usage', {}).get('completion_tokens', 0)}，"
            f"合计 {summary.get('usage', {}).get('total_tokens', 0)}"
        ),
        (
            "- 完整来源记录："
            "`chapters/section-XXX/validated_audit.json`；"
            "本报告仅展开需关注Claim，每个来源最多展示3条预览"
        ),
        "",
    ]
    source_by_index = {
        source.package["section"]["section_index"]: source
        for source in package_sources
    }
    for audit in audits:
        source = source_by_index[audit["section_index"]]
        status_counts = Counter(
            claim["status"] for claim in audit["claim_audits"]
        )
        attention_claims = [
            claim
            for claim in audit["claim_audits"]
            if _requires_attention(claim)
        ]
        lines.extend(
            [
                f"## {audit['section_index']}. "
                f"{source.package['section']['title']}",
                "",
                audit["chapter_assessment"],
                "",
                (
                    f"- Claim总数：{len(audit['claim_audits'])}；"
                    f"supported {status_counts['supported']}，"
                    f"qualified {status_counts['qualified']}，"
                    f"unsupported {status_counts['unsupported']}，"
                    f"需关注 {len(attention_claims)}"
                ),
                "",
            ]
        )
        current_paragraph = None
        for claim in attention_claims:
            if claim["paragraph_index"] != current_paragraph:
                current_paragraph = claim["paragraph_index"]
                lines.extend([f"### 第{current_paragraph}段", ""])
            lines.extend(
                [
                    (
                        f"- **{claim['status']} / "
                        f"{claim['importance']}**：{claim['claim_text']}"
                    ),
                    f"  - 判断：{claim['reason']}",
                ]
            )
            if claim["normalization_flags"]:
                lines.append(
                    "  - 程序规范化："
                    + "；".join(claim["normalization_flags"])
                )
            for assessment in claim["source_assessments"]:
                lines.append(
                    f"  - 来源层级：`{assessment['citation_key']}` / "
                    f"{assessment['source_tier']}：{assessment['reason']}"
                )
                resolved = assessment["resolved_sources"]
                preview_rows = resolved[:_SOURCE_PREVIEW_LIMIT]
                if assessment["source_tier"] == "evidence":
                    for row in preview_rows:
                        quotes = [
                            citation.get("quote", "")
                            for citation in row.get("citations", [])
                            if citation.get("quote")
                        ]
                        lines.append(
                            f"    - `{row['evidence_unit_id']}`："
                            + _preview_text(
                                "；".join(
                                    quotes or [row.get("statement", "")]
                                )
                            )
                        )
                elif assessment["source_tier"] == "card":
                    for row in preview_rows:
                        lines.append(
                            f"    - `{row['material_id']}` "
                            f"{row['title']}："
                            f"{_preview_text(row['extract'])}"
                        )
                elif assessment["source_tier"] == "markdown":
                    for row in preview_rows:
                        lines.append(
                            f"    - 完整Markdown可用，SHA-256："
                            f"`{row['document_sha256']}`"
                        )
                if len(resolved) > _SOURCE_PREVIEW_LIMIT:
                    lines.append(
                        "    - 另有"
                        f"{len(resolved) - _SOURCE_PREVIEW_LIMIT}条完整记录，"
                        "见本章 `validated_audit.json`"
                    )
            lines.append("")
    if manifest["failures"]:
        lines.extend(["## 失败记录", ""])
        for failure in manifest["failures"]:
            lines.append(
                f"- 第{failure.get('section_index', '—')}章 "
                f"`{failure['error_code']}`：{failure['error_message']}"
            )
    return "\n".join(lines).rstrip() + "\n"
