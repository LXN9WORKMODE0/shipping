from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm_statement_support import build_statement_records


DECISION_VALUES = {"待审核", "接受", "拒绝", "修改"}
DECISION_HEADERS = ["evidence_unit_id", "evidence_anchor_id", "决策", "修改后观点", "备注"]
SUPPORT_VERDICT_LABELS = {
    "directly_supported": "直接支持",
    "grounded_inference": "有依据的综述推论",
    "partially_supported": "部分支持",
    "unsupported": "不支持",
}
CLAIM_SUPPORT_VERDICT_LABELS = {
    "directly_supported": "直接支持",
    "partially_supported": "部分支持",
    "unsupported": "不支持",
}
ROLE_VERDICT_LABELS = {
    "field_aligned": "字段职责一致",
    "misclassified": "字段职责错位",
}


def write_analysis_review(
    review_dir: Path,
    *,
    run_id: str,
    paper_id: str,
    paper_title: str,
    topic: str,
    evidence_units: list[dict[str, Any]],
    paper_result: dict[str, Any],
    coverage: dict[str, Any],
) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    run_metadata = _load_run_metadata(review_dir.parent)
    (review_dir / "review.md").write_text(
        render_analysis_review(
            run_id=run_id,
            paper_id=paper_id,
            paper_title=paper_title,
            topic=topic,
            evidence_units=evidence_units,
            paper_result=paper_result,
            coverage=coverage,
            run_metadata=run_metadata,
        ),
        encoding="utf-8",
    )
    _write_decisions(review_dir / "decisions.csv", evidence_units)


def write_failed_analysis_review(
    review_dir: Path,
    *,
    run_id: str,
    status: str,
    paper_id: str,
    paper_title: str,
    topic: str,
    evidence_units: list[dict[str, Any]],
    coverage: dict[str, Any],
    failures: list[dict[str, Any]],
) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 论文 LLM 分析运行失败审核",
        "",
        "## 运行概览",
        "",
        f"- 运行 ID：`{run_id}`",
        f"- 运行状态：`{status}`",
        f"- 论文 ID：`{paper_id}`",
        f"- 论文标题：{paper_title}",
        f"- 综述主题：{topic}",
        f"- 计划 Card：{coverage.get('planned_material_count', 0)}",
        f"- 已判定 Card：{coverage.get('assessed_material_count', 0)}",
        f"- 已验证证据单元：{len(evidence_units)}",
        "- 发布状态：未发布论文分析",
        "",
        "## 失败记录",
        "",
    ]
    if not failures:
        lines.extend(["未记录具体失败。", ""])
    for index, failure in enumerate(failures, start=1):
        lines.extend(
            [
                f"### 失败 {index}",
                "",
                f"- 阶段：`{failure.get('stage', '未记录')}`",
                f"- 请求/批次：`{failure.get('batch_id') or '未记录'}`",
                f"- 错误码：`{failure.get('error_code', '未记录')}`",
                f"- 错误位置：`{failure.get('error_path') or '未记录'}`",
                f"- 错误信息：{failure.get('error_message', '未记录')}",
                "",
            ]
        )
    support_failures = _load_blocking_statement_support_reviews(review_dir.parent, failures)
    if support_failures:
        lines.extend(["## 陈述支持核验阻断详情", ""])
        for row in support_failures:
            lines.extend(
                [
                    f"### {row['statement_id']}",
                    "",
                    f"- 分析字段：`{row['field']}`",
                    f"- 核验结论：{_support_verdict_label(str(row['verdict']))}",
                    f"- 陈述：{row['statement']}",
                    f"- 引用证据：{_format_list(row['evidence_unit_ids'])}",
                    f"- 核验理由：{row['reason']}",
                    f"- 不受支持片段：{_format_list(row['unsupported_fragments'])}",
                    "",
                ]
            )
    claim_failures = _load_blocking_evidence_claim_support_reviews(
        review_dir.parent,
        failures,
    )
    if claim_failures:
        lines.extend(["## Evidence claim 支持核验阻断详情", ""])
        for row in claim_failures:
            lines.extend(
                [
                    f"### {row['evidence_unit_id']}",
                    "",
                    f"- 核验结论：{_claim_support_verdict_label(str(row['verdict']))}",
                    f"- Evidence claim：{row['claim']}",
                    f"- 核验理由：{row['reason']}",
                    f"- 不受支持片段：{_format_list(row['unsupported_fragments'])}",
                    f"- Caveats：{_format_list(row.get('caveats', []))}",
                    "- 逐字引文：",
                    *[f"  - {quote}" for quote in row.get("quotes", [])],
                    "",
                ]
            )
    unstable_claims = [
        row
        for row in _read_jsonl(
            review_dir.parent / "output" / "evidence_claim_gate_decisions.jsonl"
        )
        if row.get("decision") == "gate_disagreement"
    ]
    if unstable_claims:
        lines.extend(["## Evidence claim 门禁不稳定项", ""])
        lines.extend(
            [
                "以下条目在首轮与窄上下文复核中得到相反结论。它们不是已确认的论文缺陷，也不会自动转成人工裁决；当前运行因门禁自身不稳定而停止。",
                "",
            ]
        )
        for row in unstable_claims:
            first = row.get("first_pass_review") or {}
            confirmation = row.get("confirmation_review") or {}
            lines.extend(
                [
                    f"### {row.get('evidence_unit_id', '未记录')}",
                    "",
                    f"- 首轮结论：{_claim_support_verdict_label(str(first.get('verdict', '')))}",
                    f"- 首轮理由：{first.get('reason', '未记录')}",
                    f"- 窄上下文结论：{_claim_support_verdict_label(str(confirmation.get('verdict', '')))}",
                    f"- 窄上下文理由：{confirmation.get('reason', '未记录')}",
                    "",
                ]
            )
    role_failures = _load_blocking_statement_role_reviews(review_dir.parent, failures)
    if role_failures:
        lines.extend(["## 字段职责核验阻断详情", ""])
        for row in role_failures:
            lines.extend(
                [
                    f"### {row['statement_id']}",
                    "",
                    f"- 分析字段：`{row['field']}`",
                    f"- 核验结论：{_role_verdict_label(str(row['verdict']))}",
                    f"- 陈述：{row['statement']}",
                    f"- 核验理由：{row['reason']}",
                    f"- 职责错位片段：{_format_list(row['misaligned_fragments'])}",
                    "",
                ]
            )
    missing = coverage.get("missing_material_ids", [])
    lines.extend(
        [
            "## 覆盖状态",
            "",
            f"- 未判定 Card：{_format_list(missing)}",
            f"- 失败证据批次：{_format_list(coverage.get('failed_batch_ids', []))}",
            f"- 已完成证据批次：{_format_list(coverage.get('completed_batch_ids', []))}",
            "",
            "## 已验证证据",
            "",
        ]
    )
    if not evidence_units:
        lines.extend(["当前运行没有可审核的已验证证据。", ""])
    for unit in evidence_units:
        lines.extend(
            [
                f"### {unit['evidence_unit_id']}",
                "",
                f"- 类型：{unit['evidence_type']}",
                f"- 置信度：{_confidence_label(str(unit['confidence']))}",
                f"- 来源请求：`{unit.get('request_id') or unit.get('batch_id') or '未记录'}`",
                f"- 观点：{unit['claim']}",
                "",
            ]
        )
    lines.extend(
        [
            "## 审核边界",
            "",
            "失败运行中的已验证证据可单独审核，但在失败解决并完成论文级综合前，不得作为正式论文分析发布。",
            "",
        ]
    )
    (review_dir / "review.md").write_text("\n".join(lines), encoding="utf-8")
    _write_decisions(review_dir / "decisions.csv", evidence_units)


def write_statement_revision_review(
    review_dir: Path,
    *,
    source_run_id: str,
    run_id: str,
    status: str,
    blocking_records: list[dict[str, Any]],
    revision: dict[str, Any],
    support_reviews: list[dict[str, Any]],
) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    revisions_by_id = {
        str(row["statement_id"]): row for row in revision.get("revisions", [])
    }
    verdict_counts = Counter(str(row["verdict"]) for row in support_reviews)
    lines = [
        "# 论文分析陈述修订对照",
        "",
        "## 运行概览",
        "",
        f"- 来源运行：`{source_run_id}`",
        f"- 修订运行：`{run_id}`",
        f"- 运行状态：`{status}`",
        f"- 来源阻断陈述：{len(blocking_records)}",
        f"- 已生成修订：{len(revisions_by_id)}",
        f"- 二次核验：{_format_support_counts(verdict_counts)}",
        "",
        "## 逐条对照",
        "",
    ]
    for record in blocking_records:
        statement_id = str(record["statement_id"])
        row = revisions_by_id.get(statement_id)
        lines.extend(
            [
                f"### {statement_id}",
                "",
                f"- 字段：`{record['field']}`",
                f"- 原核验：{_support_verdict_label(str(record['verdict']))}",
                f"- 原核验理由：{record['reason']}",
                f"- 原不受支持片段：{_format_list(record['unsupported_fragments'])}",
                f"- 原证据：{_format_list(record['evidence_unit_ids'])}",
                "",
                f"**原陈述：** {record['statement']}",
                "",
            ]
        )
        if row is None:
            lines.extend(["**修订状态：** 未形成通过合同的修订。", ""])
            continue
        lines.extend([f"**修订动作：** `{row['action']}`", ""])
        if row["action"] == "delete":
            lines.extend(["该陈述被删除。", ""])
        for index, replacement in enumerate(row["replacements"], start=1):
            lines.extend(
                [
                    f"**替换 {index}：** {replacement['statement']}",
                    "",
                    f"- 替换证据：{_format_list(replacement['evidence_unit_ids'])}",
                    "",
                ]
            )
    lines.extend(["## 释放证据处置", ""])
    released = revision.get("released_evidence_dispositions", [])
    if not released:
        lines.extend(["无证据因本次修订失去全部陈述引用。", ""])
    for row in released:
        lines.extend(
            [
                f"- `{row['evidence_unit_id']}`：`{row['disposition']}/{row['reason_code']}`",
            ]
        )
    lines.extend(
        [
            "",
            "## 发布边界",
            "",
            (
                "二次核验全部通过，正式论文分析已发布。"
                if status == "completed"
                else "二次核验或修订合同未通过，候选修订不得作为正式论文分析发布。"
            ),
            "",
        ]
    )
    (review_dir / "revision.md").write_text("\n".join(lines), encoding="utf-8")


def write_semantic_audit_review(
    review_dir: Path,
    *,
    source_run_id: str,
    run_id: str,
    paper_id: str,
    paper_title: str,
    evidence_units: list[dict[str, Any]],
    statement_records: list[dict[str, Any]],
    evidence_claim_reviews: list[dict[str, Any]],
    statement_role_reviews: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    evidence_by_id = {str(row["evidence_unit_id"]): row for row in evidence_units}
    statements_by_id = {str(row["statement_id"]): row for row in statement_records}
    claim_counts = Counter(str(row["verdict"]) for row in evidence_claim_reviews)
    role_counts = Counter(str(row["verdict"]) for row in statement_role_reviews)
    lines = [
        "# 论文分析语义门禁审计",
        "",
        "## 运行概览",
        "",
        f"- 来源运行：`{source_run_id}`",
        f"- 审计运行：`{run_id}`",
        f"- 论文 ID：`{paper_id}`",
        f"- 论文标题：{paper_title}",
        f"- Evidence claim：直接支持 {claim_counts['directly_supported']}；部分支持 {claim_counts['partially_supported']}；不支持 {claim_counts['unsupported']}",
        f"- 字段职责：一致 {role_counts['field_aligned']}；错位 {role_counts['misclassified']}",
        f"- 失败数：{len(failures)}",
        "- 发布状态：只读审计，不发布论文分析",
        "",
        "## 运行失败记录",
        "",
    ]
    if not failures:
        lines.extend(["无。", ""])
    for failure in failures:
        lines.extend(
            [
                f"- 阶段：`{failure.get('stage', 'unknown')}`",
                f"- 代码：`{failure.get('error_code', 'unknown')}`",
                f"- 请求：`{failure.get('batch_id') or '无'}`",
                f"- 说明：{failure.get('error_message') or '未记录'}",
                "",
            ]
        )
    lines.extend(
        [
        "## Evidence claim 阻断项",
        "",
        ]
    )
    blocked_claims = [row for row in evidence_claim_reviews if row["verdict"] != "directly_supported"]
    if not blocked_claims:
        lines.extend(["无。", ""])
    for review in blocked_claims:
        unit = evidence_by_id[str(review["evidence_unit_id"])]
        lines.extend(
            [
                f"### {review['evidence_unit_id']}",
                "",
                f"- 结论：{_claim_support_verdict_label(str(review['verdict']))}",
                f"- Claim：{unit['claim']}",
                f"- 理由：{review['reason']}",
                f"- 不受支持片段：{_format_list(review['unsupported_fragments'])}",
                f"- Caveats：{_format_list(unit.get('caveats', []))}",
                "- 逐字引文：",
                *[f"  - {row.get('quote', '')}" for row in unit.get("citations", [])],
                "",
            ]
        )
    lines.extend(["## 字段职责阻断项", ""])
    blocked_roles = [row for row in statement_role_reviews if row["verdict"] != "field_aligned"]
    if not blocked_roles:
        lines.extend(["无。", ""])
    for review in blocked_roles:
        record = statements_by_id[str(review["statement_id"])]
        lines.extend(
            [
                f"### {review['statement_id']}",
                "",
                f"- 字段：`{record['field']}`",
                f"- 结论：{_role_verdict_label(str(review['verdict']))}",
                f"- 陈述：{record['statement']}",
                f"- 理由：{review['reason']}",
                f"- 职责错位片段：{_format_list(review['misaligned_fragments'])}",
                "",
            ]
        )
    lines.extend(
        [
            "## 审计边界",
            "",
            "本运行只核验既有不可变产物，不修改 Evidence Unit、不改写陈述、不改变证据处置，也不生成正式 paper_analysis.json。",
            "",
        ]
    )
    (review_dir / "semantic_audit.md").write_text("\n".join(lines), encoding="utf-8")


def write_semantic_adjudication_review(
    review_dir: Path,
    *,
    source_audit_run_id: str,
    run_id: str,
    paper_id: str,
    status: str,
    decisions: list[dict[str, Any]],
    evidence_revision_mappings: list[dict[str, str]],
    evidence_claim_reviews: list[dict[str, Any]],
    statement_role_reviews: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    claim_counts = Counter(str(row.get("verdict")) for row in evidence_claim_reviews)
    role_counts = Counter(str(row.get("verdict")) for row in statement_role_reviews)
    mapping_by_source = {
        str(row["source_evidence_unit_id"]): row for row in evidence_revision_mappings
    }
    lines = [
        "# 论文分析人工语义裁决",
        "",
        "## 结论",
        "",
        (
            "人工裁决、受约束处置和三道语义门禁全部闭合，正式论文分析已经发布。"
            if status == "completed"
            else "人工裁决已被快照，但处置或重新核验未全部通过，候选不得进入下游。"
        ),
        "",
        "## 运行概览",
        "",
        f"- 来源语义审计：`{source_audit_run_id}`",
        f"- 裁决运行：`{run_id}`",
        f"- 论文 ID：`{paper_id}`",
        f"- 人工裁决项：{len(decisions)}",
        f"- Evidence claim 重核验：直接支持 {claim_counts['directly_supported']}；部分支持 {claim_counts['partially_supported']}；不支持 {claim_counts['unsupported']}",
        f"- 字段职责重核验：一致 {role_counts['field_aligned']}；错位 {role_counts['misclassified']}",
        f"- 运行失败：{len(failures)}",
        "",
        "## 人工裁决与处置",
        "",
    ]
    for decision in decisions:
        target_id = str(decision["target_id"])
        mapping = mapping_by_source.get(target_id)
        if decision["human_decision"] == "门禁误判":
            resolution = "保留原对象；仅在身份和内容未变化时允许显式覆盖门禁。"
        elif decision["issue_type"] == "evidence_claim" and mapping:
            resolution = (
                "删除该 Evidence Unit。"
                if mapping["action"] == "delete"
                else f"收窄并生成新 ID：`{mapping['result_evidence_unit_id']}`。"
            )
        else:
            resolution = "废弃旧论文候选，在重新综合时把该字段缺陷作为禁止复现约束。"
        lines.extend(
            [
                f"### {target_id}",
                "",
                f"- 类型：`{decision['issue_type']}`",
                f"- 原模型结论：`{decision['model_verdict']}`",
                f"- 人工裁决：{decision['human_decision']}",
                f"- 人工说明：{decision['human_note']}",
                f"- 实际处置：{resolution}",
                "",
            ]
        )
    lines.extend(["## 失败记录", ""])
    if not failures:
        lines.extend(["无。", ""])
    for failure in failures:
        lines.extend(
            [
                f"- 阶段：`{failure.get('stage', 'unknown')}`",
                f"- 代码：`{failure.get('error_code', 'unknown')}`",
                f"- 说明：{failure.get('error_message') or '未记录'}",
                "",
            ]
        )
    lines.extend(
        [
            "## 发布边界",
            "",
            (
                "正式结果位于 `output/paper_analysis.json`；原始门禁 verdict 和人工覆盖记录均保留。"
                if status == "completed"
                else "不存在可发布的 `output/paper_analysis.json`；不得读取中间候选代替正式结果。"
            ),
            "",
        ]
    )
    (review_dir / "adjudication.md").write_text("\n".join(lines), encoding="utf-8")


def _write_decisions(path: Path, evidence_units: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DECISION_HEADERS)
        writer.writeheader()
        for unit in evidence_units:
            writer.writerow(
                {
                    "evidence_unit_id": unit["evidence_unit_id"],
                    "evidence_anchor_id": unit["evidence_anchor_id"],
                    "决策": "待审核",
                    "修改后观点": "",
                    "备注": "",
                }
            )


def render_analysis_review(
    *,
    run_id: str,
    paper_id: str,
    paper_title: str,
    topic: str,
    evidence_units: list[dict[str, Any]],
    paper_result: dict[str, Any],
    coverage: dict[str, Any],
    run_metadata: dict[str, Any] | None = None,
) -> str:
    run_metadata = run_metadata or {}
    profile = run_metadata.get("model_profile", {})
    analysis_plan = run_metadata.get("analysis_plan", {})
    synthesis_plan = run_metadata.get("synthesis_plan", {})
    request_results = run_metadata.get("request_results", {})
    failures = run_metadata.get("failures", [])
    confidence_counts = Counter(str(unit["confidence"]) for unit in evidence_units)
    lines = [
        "# 论文 LLM 分析人工审核",
        "",
        "## 运行概览",
        "",
        f"- 运行 ID：`{run_id}`",
        f"- 论文 ID：`{paper_id}`",
        f"- 论文标题：{paper_title}",
        f"- 综述主题：{topic}",
        f"- 输入 Card：{coverage.get('planned_material_count', 0)}",
        f"- 已判定 Card：{coverage.get('assessed_material_count', 0)}",
        f"- 证据单元：{len(evidence_units)}",
        f"- 证据置信度：{_format_counts(confidence_counts)}",
        f"- 模型 profile：`{profile.get('profile_id', '未记录')}`",
        f"- Tokenizer revision：`{profile.get('tokenizer_revision', '未记录')}`",
        f"- 推理模式：{'Non-think（关闭）' if profile.get('thinking') == {'type': 'disabled'} else '未确认'}",
        f"- 证据抽取策略：`{analysis_plan.get('strategy', '未记录')}`",
        f"- 论文综合策略：`{synthesis_plan.get('strategy', '未生成')}`",
        f"- 最终采用证据：{len(coverage.get('used_in_final_analysis_ids', []))}",
        f"- 最终未采用证据：{len(coverage.get('not_used_in_final_analysis_ids', []))}",
        f"- 跨章节证据：{_format_list(synthesis_plan.get('cross_section_evidence_ids', []))}",
        f"- 失败记录：{len(failures)}",
        "",
        "## Token 计划与调用",
        "",
    ]
    claim_support_plan = run_metadata.get("evidence_claim_support_plan", {})
    planned_requests = [
        *analysis_plan.get("evidence_batches", []),
        *claim_support_plan.get("requests", []),
        *synthesis_plan.get("requests", []),
    ]
    if not planned_requests:
        lines.extend(["未记录请求计划。", ""])
    for request in planned_requests:
        request_id = str(request["request_id"])
        result = request_results.get(request_id, {})
        lines.extend(
            [
                f"- `{request_id}`：阶段 `{request.get('stage', '')}`；拆分原因 `{request.get('split_reason', '综合阶段')}`；"
                f"章节 `{_format_list(request.get('section_ids') or ([request.get('section_id')] if request.get('section_id') else []))}`；"
                f"计划输入 {request.get('input_tokens')}；实际输入 {result.get('actual_prompt_tokens', '未调用')}；"
                f"差值 {result.get('prompt_token_delta', '未调用')}；最大输出 {request.get('max_output_tokens')}；"
                f"实际输出 {result.get('actual_completion_tokens', '未调用')}。"
            ]
        )
    lines.extend(["", "## 论文分析", ""])
    if paper_result["paper_disposition"] == "no_usable_evidence":
        lines.extend(["该论文在当前主题下没有形成可验证的证据单元。", ""])
    else:
        analysis = paper_result["analysis"]
        support_by_location = _statement_support_by_location(paper_result)
        role_by_location = _statement_role_by_location(paper_result)
        lines.extend(
            [
                f"**研究焦点：** {_format_referenced_item(analysis['research_focus'], support_by_location.get(('research_focus', None)), role_by_location.get(('research_focus', None)))}",
                "",
                f"**研究类型：** {analysis['study_type']}",
                "",
                "### 方法",
                "",
            ]
        )
        lines.extend(_render_referenced_items(analysis["methods"], "methods", support_by_location, role_by_location))
        lines.extend(["", "### 核心发现", ""])
        lines.extend(
            _render_referenced_items(analysis["core_findings"], "core_findings", support_by_location, role_by_location)
        )
        lines.extend(["", "### 局限", ""])
        lines.extend(_render_referenced_items(analysis["limitations"], "limitations", support_by_location, role_by_location))
        lines.extend(["", "### 综述用途", ""])
        lines.extend(_render_referenced_items(analysis["review_uses"], "review_uses", support_by_location, role_by_location))
        lines.extend(["", "### 未解决问题", ""])
        lines.extend(
            _render_referenced_items(
                analysis["unresolved_questions"],
                "unresolved_questions",
                support_by_location,
                role_by_location,
            )
        )
        lines.append("")

    lines.extend(["## 逐条证据", ""])
    if not evidence_units:
        lines.extend(["无可审核证据。", ""])
    claim_reviews_by_id = {
        str(row["evidence_unit_id"]): row
        for row in paper_result.get("evidence_claim_support_reviews", [])
    }
    for unit in evidence_units:
        request_id = str(unit.get("request_id") or unit.get("batch_id") or "")
        request_plan = next(
            (row for row in analysis_plan.get("evidence_batches", []) if row.get("request_id") == request_id),
            {},
        )
        final_disposition = next(
            (
                row
                for row in (paper_result.get("analysis") or {}).get("evidence_dispositions", [])
                if row.get("evidence_unit_id") == unit["evidence_unit_id"]
            ),
            None,
        )
        claim_review = claim_reviews_by_id.get(str(unit["evidence_unit_id"]))
        lines.extend(
            [
                f"### {unit['evidence_unit_id']}",
                "",
                f"- 证据类型：{unit['evidence_type']}",
                f"- 证据 Anchor：`{unit['evidence_anchor_id']}`",
                f"- 来源请求：`{request_id}`",
                f"- 章节路径：{_format_list(request_plan.get('section_ids', []))}",
                f"- 置信度：{_confidence_label(str(unit['confidence']))}",
                f"- 主题关系：{unit['relevance']}",
                f"- 注意事项：{_format_list(unit['caveats'])}",
                f"- Claim 支持核验：{_claim_support_verdict_label(str(claim_review['verdict'])) if claim_review else '未核验'}"
                + (f"；说明：{claim_review['reason']}" if claim_review else ""),
                f"- 最终采用状态：{final_disposition['disposition'] if final_disposition else '未处置'}",
                f"- 最终未采用原因：{final_disposition['reason_code'] if final_disposition and final_disposition['disposition'] != 'used' else '不适用'}",
                "",
                f"**观点：** {unit['claim']}",
                "",
                "**原文证据：**",
                "",
            ]
        )
        for citation in unit["citations"]:
            lines.extend(
                [
                    f"- Card：`{citation['material_id']}`",
                    f"- 引文候选：`{citation.get('quote_id', '未记录')}`",
                    f"- Card 标题：{citation['card_title']}",
                    f"- 原文位置：`{_format_ref(citation['source_ref'])}`",
                    f"- Card 质量标记：{_format_list([*citation['confidence_flags'], *citation['quality_flags']])}",
                    f"> {citation['quote']}",
                    "",
                ]
            )
    lines.extend(
        [
            "## 审核方式",
            "",
            "在同目录的 `decisions.csv` 中填写决策。允许值为 `待审核`、`接受`、`拒绝`、`修改`；选择 `修改` 时必须填写修改后观点。",
            "",
        ]
    )
    return "\n".join(lines)


def import_review_decisions(run_dir: Path, decisions_path: Path | None = None) -> dict[str, Any]:
    evidence_path = run_dir / "output" / "evidence_units.jsonl"
    units = _read_jsonl(evidence_path)
    anchors_by_id = {str(row["evidence_unit_id"]): str(row["evidence_anchor_id"]) for row in units}
    evidence_ids = set(anchors_by_id)
    source_path = decisions_path or run_dir / "review" / "decisions.csv"
    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != DECISION_HEADERS:
            raise ValueError(f"审核决策 CSV 列必须为：{DECISION_HEADERS}。")
        rows = list(reader)

    observed: list[str] = []
    for line_number, row in enumerate(rows, start=2):
        evidence_id = str(row["evidence_unit_id"]).strip()
        anchor_id = str(row["evidence_anchor_id"]).strip()
        decision = str(row["决策"]).strip()
        revised_claim = str(row["修改后观点"]).strip()
        if evidence_id not in evidence_ids:
            raise ValueError(f"审核决策第 {line_number} 行引用未知 evidence_unit_id：{evidence_id!r}。")
        if evidence_id in observed:
            raise ValueError(f"审核决策存在重复 evidence_unit_id：{evidence_id!r}。")
        if decision not in DECISION_VALUES:
            raise ValueError(f"审核决策第 {line_number} 行决策值无效：{decision!r}。")
        if anchors_by_id[evidence_id] != anchor_id:
            raise ValueError(
                f"审核决策第 {line_number} 行 evidence_anchor_id 与运行产物不匹配：{anchor_id!r}。"
            )
        if decision == "修改" and not revised_claim:
            raise ValueError(f"审核决策第 {line_number} 行选择修改时必须填写修改后观点。")
        observed.append(evidence_id)
    missing = sorted(evidence_ids - set(observed))
    if missing:
        raise ValueError(f"审核决策缺少 evidence_unit_id：{missing}。")

    result = {
        "schema_version": "llm.review_decisions.v2",
        "imported_at": datetime.now(UTC).isoformat(),
        "source": str(source_path),
        "evidence_count": len(evidence_ids),
        "decision_counts": dict(Counter(str(row["决策"]).strip() for row in rows)),
        "decisions": rows,
    }
    output_path = run_dir / "review" / "decision_state.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def migrate_review_decisions(
    previous_state: dict[str, Any],
    current_units: list[dict[str, Any]],
) -> dict[str, Any]:
    previous = previous_state.get("decisions")
    if not isinstance(previous, list):
        raise ValueError("previous_state.decisions 必须是数组。")
    previous_by_unit = {str(row["evidence_unit_id"]): row for row in previous}
    previous_anchor_ids = {str(row["evidence_anchor_id"]) for row in previous}
    migrated: list[dict[str, Any]] = []
    for unit in current_units:
        unit_id = str(unit["evidence_unit_id"])
        anchor_id = str(unit["evidence_anchor_id"])
        old = previous_by_unit.get(unit_id)
        if old is not None:
            if str(old["evidence_anchor_id"]) != anchor_id:
                raise ValueError(f"相同 evidence_unit_id 对应了不同 anchor：{unit_id!r}。")
            migrated.append({**old, "migration": "exact_unit"})
        elif anchor_id in previous_anchor_ids:
            migrated.append(
                {
                    "evidence_unit_id": unit_id,
                    "evidence_anchor_id": anchor_id,
                    "决策": "待审核",
                    "修改后观点": "",
                    "备注": "来源相同但观点变化",
                    "migration": "anchor_only",
                }
            )
    return {"schema_version": "llm.review_decisions.v2", "decisions": migrated}


def _render_referenced_items(
    items: list[dict[str, Any]],
    field: str,
    support_by_location: dict[tuple[str, int | None], dict[str, Any]],
    role_by_location: dict[tuple[str, int | None], dict[str, Any]],
) -> list[str]:
    if not items:
        return ["- 未提取到明确内容。"]
    return [
        f"- {_format_referenced_item(item, support_by_location.get((field, index)), role_by_location.get((field, index)))}"
        for index, item in enumerate(items)
    ]


def _format_referenced_item(
    item: dict[str, Any],
    support_review: dict[str, Any] | None,
    role_review: dict[str, Any] | None,
) -> str:
    support_text = "未核验"
    if support_review is not None:
        support_text = _support_verdict_label(str(support_review["verdict"]))
        support_text += f"；说明：{support_review['reason']}"
    role_text = "未核验"
    if role_review is not None:
        role_text = _role_verdict_label(str(role_review["verdict"]))
        role_text += f"；说明：{role_review['reason']}"
    return (
        f"{item['statement']}（证据：{', '.join(item['evidence_unit_ids'])}；"
        f"字段职责：{role_text}；支持核验：{support_text}）"
    )


def _statement_support_by_location(
    paper_result: dict[str, Any],
) -> dict[tuple[str, int | None], dict[str, Any]]:
    analysis = paper_result["analysis"]
    records = build_statement_records(analysis)
    reviews_by_id = {
        str(row["statement_id"]): row
        for row in paper_result.get("statement_support_reviews", [])
    }
    return {
        (str(record["field"]), record["index"]): reviews_by_id[str(record["statement_id"])]
        for record in records
        if str(record["statement_id"]) in reviews_by_id
    }


def _statement_role_by_location(
    paper_result: dict[str, Any],
) -> dict[tuple[str, int | None], dict[str, Any]]:
    analysis = paper_result["analysis"]
    records = build_statement_records(analysis)
    reviews_by_id = {
        str(row["statement_id"]): row
        for row in paper_result.get("statement_role_reviews", [])
    }
    return {
        (str(record["field"]), record["index"]): reviews_by_id[str(record["statement_id"])]
        for record in records
        if str(record["statement_id"]) in reviews_by_id
    }


def _load_blocking_evidence_claim_support_reviews(
    run_dir: Path,
    failures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocking: list[dict[str, Any]] = []
    for failure in failures:
        if failure.get("stage") != "evidence_claim_support" or not failure.get("batch_id"):
            continue
        request_dir = run_dir / "batches" / str(failure["batch_id"])
        units = _read_jsonl(request_dir / "evidence_units.jsonl")
        reviews = _read_optional_json(request_dir / "validated_output.json").get(
            "evidence_claim_reviews", []
        )
        units_by_id = {str(row["evidence_unit_id"]): row for row in units}
        for review in reviews:
            if review.get("verdict") == "directly_supported":
                continue
            unit = units_by_id.get(str(review.get("evidence_unit_id", "")))
            if unit is None:
                continue
            blocking.append(
                {
                    **review,
                    "claim": unit.get("claim", ""),
                    "quotes": [
                        str(citation.get("quote", ""))
                        for citation in unit.get("citations", [])
                    ],
                    "caveats": unit.get("caveats", []),
                }
            )
    return blocking


def _load_blocking_statement_role_reviews(
    run_dir: Path,
    failures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocking: list[dict[str, Any]] = []
    for failure in failures:
        if failure.get("stage") != "statement_role" or not failure.get("batch_id"):
            continue
        request_dir = run_dir / "batches" / str(failure["batch_id"])
        records = _read_optional_json(request_dir / "statement_records.json").get(
            "statements", []
        )
        reviews = _read_optional_json(request_dir / "validated_output.json").get(
            "statement_role_reviews", []
        )
        records_by_id = {str(row["statement_id"]): row for row in records}
        for review in reviews:
            if review.get("verdict") == "field_aligned":
                continue
            record = records_by_id.get(str(review.get("statement_id", "")))
            if record is not None:
                blocking.append({**record, **review})
    return blocking


def _load_blocking_statement_support_reviews(
    run_dir: Path,
    failures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocking: list[dict[str, Any]] = []
    for failure in failures:
        if failure.get("stage") != "statement_support" or not failure.get("batch_id"):
            continue
        request_dir = run_dir / "batches" / str(failure["batch_id"])
        records = _read_optional_json(request_dir / "statement_records.json").get("statements", [])
        reviews = _read_optional_json(request_dir / "validated_output.json").get(
            "statement_reviews", []
        )
        records_by_id = {str(row["statement_id"]): row for row in records}
        for review in reviews:
            statement_id = str(review.get("statement_id", ""))
            record = records_by_id.get(statement_id)
            if record is None or not _is_blocking_support_review(record, review):
                continue
            blocking.append({**record, **review})
    return blocking


def _is_blocking_support_review(record: dict[str, Any], review: dict[str, Any]) -> bool:
    verdict = str(review.get("verdict", ""))
    return verdict in {"partially_supported", "unsupported"} or (
        record.get("required_support") == "direct" and verdict != "directly_supported"
    )


def _support_verdict_label(value: str) -> str:
    return SUPPORT_VERDICT_LABELS.get(value, value)


def _claim_support_verdict_label(value: str) -> str:
    return CLAIM_SUPPORT_VERDICT_LABELS.get(value, value)


def _role_verdict_label(value: str) -> str:
    return ROLE_VERDICT_LABELS.get(value, value)


def _format_support_counts(counts: Counter[str]) -> str:
    if not counts:
        return "未执行或未形成有效核验结果"
    return "；".join(
        f"{_support_verdict_label(key)} {value}"
        for key, value in sorted(counts.items())
    )


def _format_counts(counts: Counter[str]) -> str:
    return ", ".join(f"{_confidence_label(key)} {value}" for key, value in sorted(counts.items())) or "无"


def _format_list(values: list[str]) -> str:
    return "；".join(str(value) for value in values) if values else "无"


def _confidence_label(value: str) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(value, value)


def _format_ref(ref: dict[str, Any]) -> str:
    return f"{ref.get('path', '')}:{ref.get('start_line', '')}-{ref.get('end_line', '')}"


def _load_run_metadata(run_dir: Path) -> dict[str, Any]:
    request_results: dict[str, dict[str, Any]] = {}
    batches_dir = run_dir / "batches"
    if batches_dir.exists():
        for result_path in batches_dir.glob("*/result.json"):
            result = _read_json(result_path)
            request_id = result.get("request_id")
            if isinstance(request_id, str) and request_id:
                request_results[request_id] = result
    return {
        "model_profile": _read_optional_json(run_dir / "plan" / "model_profile.json"),
        "analysis_plan": _read_optional_json(run_dir / "plan" / "analysis_plan.json"),
        "evidence_claim_support_plan": _read_optional_json(
            run_dir / "plan" / "evidence_claim_support_plan.json"
        ),
        "synthesis_plan": _read_optional_json(run_dir / "plan" / "synthesis_plan.json"),
        "request_results": request_results,
        "failures": _read_jsonl(run_dir / "audit" / "failures.jsonl"),
    }


def _read_optional_json(path: Path) -> dict[str, Any]:
    return _read_json(path) if path.exists() else {}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
