from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .llm_contracts import ContractViolation, build_evidence_unit_id


SEMANTIC_DECISIONS_SCHEMA_VERSION = "llm.semantic_adjudication_decisions.v2"
EVIDENCE_REVISION_SCHEMA_VERSION = "llm.evidence_revision.v1"
SEMANTIC_DECISION_HEADERS = (
    "裁决项",
    "论文标题",
    "问题类型",
    "程序环节",
    "所在字段",
    "原观点或陈述",
    "人工需回答的问题",
    "上游证据观点",
    "关联论文输出",
    "逐字引文",
    "原文位置",
    "Card标题",
    "Card完整文本",
    "相邻Card上下文",
    "质量标记",
    "模型结论",
    "冲突片段",
    "模型理由",
    "确认缺陷后的处理",
    "门禁误判后的处理",
    "人工裁决",
    "人工说明",
    "内部目标ID",
)
SEMANTIC_DECISION_EDITABLE_HEADERS = ("人工裁决", "人工说明")
HUMAN_DECISIONS = ("待裁决", "确认缺陷", "门禁误判")
ISSUE_TYPE_LABELS = {
    "evidence_claim": "证据观点",
    "statement_role": "字段职责",
}
ISSUE_TYPE_VALUES = {label: value for value, label in ISSUE_TYPE_LABELS.items()}
VERDICT_LABELS = {
    "partially_supported": "部分支持",
    "unsupported": "不支持",
    "misclassified": "字段错位",
}
FIELD_LABELS = {
    "research_focus": "研究重点",
    "methods": "研究方法",
    "core_findings": "核心发现",
    "limitations": "局限性",
    "review_uses": "综述用途",
    "unresolved_questions": "未解决问题",
}

EVIDENCE_REVISION_SYSTEM_PROMPT = """你是 Evidence Unit 的单次受约束修订器。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown、判断过程或额外解释。
只能处理输入列出的 evidence_unit_id，每个 ID 恰好一次，不得修改其他 Evidence Unit。
允许的动作只有 replace 和 delete。replace 只能收窄或忠实改写 claim，并可调整 caveats；逐字引文、evidence_type、relevance 和 confidence 均由代码保持不变。
replacement claim 必须被输入逐字引文完整直接支持，不得补充引文未表达的对象、范围、因果、程度、数值、公式含义或验证结论。
如果现有逐字引文无法形成任何有意义且直接支持的 claim，使用 delete；不得要求增加引文，也不得生成替代证据。
这是单次修订，不得保留多个候选方案，也不得要求再次改写。"""


class SemanticAdjudicationError(ValueError):
    pass


def build_semantic_issues(
    *,
    evidence_units: list[dict[str, Any]],
    statement_records: list[dict[str, Any]],
    evidence_claim_reviews: list[dict[str, Any]],
    statement_role_reviews: list[dict[str, Any]],
) -> list[dict[str, str]]:
    evidence_by_id = {str(row["evidence_unit_id"]): row for row in evidence_units}
    statements_by_id = {str(row["statement_id"]): row for row in statement_records}
    issues: list[dict[str, str]] = []
    for review in evidence_claim_reviews:
        if review.get("verdict") == "directly_supported":
            continue
        target_id = str(review.get("evidence_unit_id") or "")
        unit = evidence_by_id.get(target_id)
        if unit is None:
            raise SemanticAdjudicationError(f"Evidence claim 审计指向未知证据：{target_id!r}")
        issues.append(
            {
                "issue_type": "evidence_claim",
                "target_id": target_id,
                "model_verdict": str(review.get("verdict") or ""),
                "field": "",
                "content": str(unit.get("claim") or ""),
                "model_reason": str(review.get("reason") or ""),
                "conflict_fragments": "；".join(
                    str(value) for value in review.get("unsupported_fragments", [])
                ),
            }
        )
    for review in statement_role_reviews:
        if review.get("verdict") == "field_aligned":
            continue
        target_id = str(review.get("statement_id") or "")
        record = statements_by_id.get(target_id)
        if record is None:
            raise SemanticAdjudicationError(f"字段职责审计指向未知陈述：{target_id!r}")
        issues.append(
            {
                "issue_type": "statement_role",
                "target_id": target_id,
                "model_verdict": str(review.get("verdict") or ""),
                "field": str(record.get("field") or ""),
                "content": str(record.get("statement") or ""),
                "model_reason": str(review.get("reason") or ""),
                "conflict_fragments": "；".join(
                    str(value) for value in review.get("misaligned_fragments", [])
                ),
            }
        )
    identities = [(row["issue_type"], row["target_id"]) for row in issues]
    if len(identities) != len(set(identities)):
        raise SemanticAdjudicationError("语义阻断项身份重复。")
    return issues


def enrich_semantic_issues_for_review(
    *,
    issues: list[dict[str, str]],
    paper_title: str,
    evidence_units: list[dict[str, Any]],
    statement_records: list[dict[str, Any]],
    materials: list[dict[str, Any]],
) -> list[dict[str, str]]:
    evidence_by_id = {str(row["evidence_unit_id"]): row for row in evidence_units}
    statement_by_id = {str(row["statement_id"]): row for row in statement_records}
    material_by_id = {str(row["material_id"]): row for row in materials}
    ordered_materials = sorted(materials, key=lambda row: int(row.get("order") or 0))
    material_indexes = {
        str(material["material_id"]): index for index, material in enumerate(ordered_materials)
    }
    enriched: list[dict[str, str]] = []
    for issue in issues:
        target_id = issue["target_id"]
        if issue["issue_type"] == "evidence_claim":
            evidence = evidence_by_id[target_id]
            relevant_evidence = [evidence]
            related_statements = [
                row for row in statement_records if target_id in row.get("evidence_unit_ids", [])
            ]
            field = ""
            stage = "Card → Evidence Unit → 证据观点支持门禁"
            human_question = (
                "只依据当前 Evidence Unit 绑定的逐字引文，这个观点的全部对象、范围、程度、数值和因果是否都被直接支持？"
            )
            confirm_effect = "收窄或删除该 Evidence Unit；保持原逐字引文不变，再重做论文级综合和全部语义门禁。"
            reject_effect = "保留该 Evidence Unit；人工覆盖只对相同 ID、相同内容和相同门禁冲突签名有效。"
        else:
            statement = statement_by_id[target_id]
            relevant_evidence = [
                evidence_by_id[str(evidence_id)]
                for evidence_id in statement.get("evidence_unit_ids", [])
            ]
            related_statements = [statement]
            field = str(statement.get("field") or "")
            stage = "Evidence Unit → 论文级分析 → 字段职责门禁"
            human_question = (
                f"这条陈述是否真正履行“{FIELD_LABELS.get(field, field)}”字段职责，而不只是复述论文事实？"
            )
            confirm_effect = "废弃旧论文级候选，重新综合该字段，并禁止复现相同或同义的职责错位陈述。"
            reject_effect = "保留该论文级陈述；人工覆盖只对相同 ID、相同内容和相同门禁冲突签名有效。"
        citations = _unique_citations(relevant_evidence)
        cited_materials = _cited_materials(citations, material_by_id)
        enriched.append(
            {
                **issue,
                "paper_title": paper_title,
                "stage": stage,
                "field_label": _field_label(field),
                "human_question": human_question,
                "upstream_evidence": _format_evidence_units(relevant_evidence),
                "related_paper_output": _format_related_paper_output(
                    issue=issue,
                    related_statements=related_statements,
                    statement_records=statement_records,
                ),
                "quotes": _format_citations(citations),
                "source_locations": _format_source_locations(citations),
                "card_titles": "\n".join(
                    f"{index}. {material.get('clean_title') or material.get('raw_title') or '无标题'}"
                    for index, material in enumerate(cited_materials, start=1)
                ),
                "card_full_text": _format_materials(cited_materials),
                "neighbor_card_context": _format_neighbor_materials(
                    cited_materials=cited_materials,
                    ordered_materials=ordered_materials,
                    material_indexes=material_indexes,
                ),
                "quality_flags": _format_quality_flags(cited_materials, citations),
                "confirm_effect": confirm_effect,
                "reject_effect": reject_effect,
            }
        )
    return enriched


def write_semantic_decisions_template(
    path: Path,
    issues: list[dict[str, str]],
    *,
    overwrite: bool = False,
) -> None:
    if path.exists() and not overwrite:
        raise SemanticAdjudicationError(f"人工裁决文件已经存在，不能覆盖：{path}")
    if path.exists() and overwrite:
        _assert_existing_decisions_are_pending(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SEMANTIC_DECISION_HEADERS)
        writer.writeheader()
        for index, issue in enumerate(issues, start=1):
            writer.writerow(
                {
                    **_issue_csv_row(issue, index=index),
                    "人工裁决": "待裁决",
                    "人工说明": "",
                }
            )


def write_semantic_decision_workbook(path: Path, issues: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 人工语义裁决工作单",
        "",
        f"本次共有 {len(issues)} 项。人工只需按裁决项编号判断，不需要检索内部 Evidence ID。",
        "",
        "## 判断边界",
        "",
        "- 证据观点：只判断当前绑定的逐字引文能否完整支持观点。Card 全文和相邻 Card 用来解释问题来源，不能替代当前逐字引文。",
        "- 字段职责：判断论文级陈述是否履行所在字段职责；证据为真，不等于放在该字段就合适。",
        "- 本工作单中的“原文”是进入本流程的 MinerU Markdown 与 Card 不可变快照，并保留 Markdown 行号；当前上游没有保存 PDF 页码和版面坐标，因此这里不伪造 PDF 级定位。",
        "- `确认缺陷` 表示门禁指出的问题成立；`门禁误判` 表示当前内容本身正确，是门禁判错。",
        "- 最终仍在 `semantic_decisions.csv` 的 `人工裁决` 和 `人工说明` 两列填写。也可以直接按“第1项=……”的形式回复。",
        "",
        "## 流程说明",
        "",
        "`MinerU Markdown → Card → Evidence Unit → 论文级分析 → 独立语义门禁 → 人工裁决`",
        "",
    ]
    for index, issue in enumerate(issues, start=1):
        lines.extend(_semantic_issue_workbook_lines(index, issue))
    path.write_text("\n".join(lines), encoding="utf-8")


def load_semantic_decisions(
    path: Path,
    *,
    issues: list[dict[str, str]],
    audit_run_id: str,
    audit_manifest_sha256: str,
    candidate_sha256: str,
    evidence_units_sha256: str,
) -> dict[str, Any]:
    try:
        raw_bytes = path.read_bytes()
        text = raw_bytes.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SemanticAdjudicationError(f"无法读取人工裁决文件：{path}") from exc
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != SEMANTIC_DECISION_HEADERS:
        raise SemanticAdjudicationError(
            f"人工裁决文件列不匹配：expected={list(SEMANTIC_DECISION_HEADERS)}, "
            f"observed={reader.fieldnames}"
        )
    rows = list(reader)
    expected = {(row["issue_type"], row["target_id"]): row for row in issues}
    observed_ids = [
        (
            ISSUE_TYPE_VALUES.get(str(row["问题类型"]), str(row["问题类型"])),
            str(row["内部目标ID"]),
        )
        for row in rows
    ]
    counts = Counter(observed_ids)
    missing = sorted(set(expected) - set(observed_ids))
    unknown = sorted(set(observed_ids) - set(expected))
    duplicate = sorted(identity for identity, count in counts.items() if count > 1)
    if missing or unknown or duplicate:
        raise SemanticAdjudicationError(
            f"人工裁决覆盖不完整：missing={missing}, unknown={unknown}, duplicate={duplicate}"
        )
    decisions: list[dict[str, str]] = []
    expected_rows = {
        (issue["issue_type"], issue["target_id"]): _issue_csv_row(issue, index=index)
        for index, issue in enumerate(issues, start=1)
    }
    for row in rows:
        identity = (
            ISSUE_TYPE_VALUES.get(str(row["问题类型"]), str(row["问题类型"])),
            str(row["内部目标ID"]),
        )
        issue = expected[identity]
        expected_row = expected_rows[identity]
        for field in SEMANTIC_DECISION_HEADERS:
            if field in SEMANTIC_DECISION_EDITABLE_HEADERS:
                continue
            if str(row[field]) != expected_row[field]:
                raise SemanticAdjudicationError(
                    f"人工裁决文件的只读字段被修改：target_id={identity[1]!r}, field={field!r}"
                )
        decision = str(row["人工裁决"]).strip()
        note = str(row["人工说明"]).strip()
        if decision not in HUMAN_DECISIONS:
            raise SemanticAdjudicationError(
                f"未知人工裁决：target_id={identity[1]!r}, decision={decision!r}"
            )
        if decision == "待裁决":
            raise SemanticAdjudicationError(f"仍有待裁决项：target_id={identity[1]!r}")
        if not note:
            raise SemanticAdjudicationError(f"人工裁决必须填写说明：target_id={identity[1]!r}")
        decisions.append(
            {
                **issue,
                "human_decision": decision,
                "human_note": note,
            }
        )
    payload = {
        "schema_version": SEMANTIC_DECISIONS_SCHEMA_VERSION,
        "source_audit_run_id": audit_run_id,
        "source_audit_manifest_sha256": audit_manifest_sha256,
        "candidate_sha256": candidate_sha256,
        "evidence_units_sha256": evidence_units_sha256,
        "decisions_file_sha256": "sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
        "decisions": decisions,
    }
    payload["decisions_sha256"] = _sha256_json(payload)
    return payload


def build_evidence_revision_schema(evidence_unit_ids: list[str]) -> dict[str, Any]:
    if not evidence_unit_ids or len(evidence_unit_ids) != len(set(evidence_unit_ids)):
        raise ValueError("evidence_unit_ids 必须是非空且唯一的列表。")
    variants = [_evidence_revision_variant(evidence_id) for evidence_id in evidence_unit_ids]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.evidence_revision.v1.json",
        "title": "Evidence Unit 单次受约束修订",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "revisions"],
        "properties": {
            "schema_version": {"const": EVIDENCE_REVISION_SCHEMA_VERSION},
            "revisions": {
                "type": "array",
                "minItems": len(evidence_unit_ids),
                "maxItems": len(evidence_unit_ids),
                "items": {"oneOf": variants},
            },
        },
    }


def build_evidence_revision_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    evidence_units: list[dict[str, Any]],
    blocking_reviews: list[dict[str, Any]],
    human_decisions: list[dict[str, str]],
) -> tuple[str, str]:
    evidence_by_id = {str(row["evidence_unit_id"]): row for row in evidence_units}
    review_by_id = {str(row["evidence_unit_id"]): row for row in blocking_reviews}
    decision_by_id = {
        str(row["target_id"]): row
        for row in human_decisions
        if row["issue_type"] == "evidence_claim" and row["human_decision"] == "确认缺陷"
    }
    evidence_ids = list(decision_by_id)
    if not evidence_ids:
        raise ValueError("没有需要修订的 Evidence Unit。")
    if set(evidence_ids) - set(evidence_by_id) or set(evidence_ids) - set(review_by_id):
        raise SemanticAdjudicationError("Evidence Unit 修订输入与审计阻断项不一致。")
    items = []
    for evidence_id in evidence_ids:
        unit = evidence_by_id[evidence_id]
        review = review_by_id[evidence_id]
        decision = decision_by_id[evidence_id]
        items.append(
            {
                "evidence_unit_id": evidence_id,
                "claim": unit["claim"],
                "evidence_type": unit["evidence_type"],
                "quotes": [str(row.get("quote") or "") for row in unit.get("citations", [])],
                "caveats": list(unit.get("caveats", [])),
                "门禁结论": review["verdict"],
                "门禁理由": review["reason"],
                "不受支持片段": list(review.get("unsupported_fragments", [])),
                "人工说明": decision["human_note"],
            }
        )
    prompt_input = {
        "任务": "对人工确认存在缺陷的 Evidence Unit 执行一次收窄或删除",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "综述主题": topic,
        "输出JSONSchema": build_evidence_revision_schema(evidence_ids),
        "待修订证据": items,
    }
    return EVIDENCE_REVISION_SYSTEM_PROMPT, json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate_and_apply_evidence_revision(
    payload: object,
    *,
    evidence_units: list[dict[str, Any]],
    target_evidence_ids: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
    schema = build_evidence_revision_schema(target_evidence_ids)
    validated = _validate_schema(payload, schema)
    observed = [str(row["evidence_unit_id"]) for row in validated["revisions"]]
    counts = Counter(observed)
    missing = sorted(set(target_evidence_ids) - set(observed))
    unknown = sorted(set(observed) - set(target_evidence_ids))
    duplicate = sorted(evidence_id for evidence_id, count in counts.items() if count > 1)
    if missing or unknown or duplicate:
        raise ContractViolation(
            "coverage.evidence_revision_invalid",
            f"missing={missing}, unknown={unknown}, duplicate={duplicate}",
            path="$.revisions",
        )
    revisions_by_id = {str(row["evidence_unit_id"]): row for row in validated["revisions"]}
    revised_units: list[dict[str, Any]] = []
    mappings: list[dict[str, str]] = []
    for unit in evidence_units:
        old_id = str(unit["evidence_unit_id"])
        revision = revisions_by_id.get(old_id)
        if revision is None:
            revised_units.append(copy.deepcopy(unit))
            continue
        if revision["action"] == "delete":
            mappings.append(
                {
                    "source_evidence_unit_id": old_id,
                    "action": "delete",
                    "result_evidence_unit_id": "",
                }
            )
            continue
        replacement = revision["replacement"]
        revised = copy.deepcopy(unit)
        revised["claim"] = str(replacement["claim"]).strip()
        revised["caveats"] = list(replacement["caveats"])
        if revised["claim"] == str(unit["claim"]).strip() and revised["caveats"] == list(
            unit.get("caveats", [])
        ):
            raise ContractViolation(
                "revision.evidence_unchanged",
                f"evidence_unit_id={old_id!r} 未发生变化",
                path="$.revisions",
            )
        new_id = build_evidence_unit_id(str(unit["evidence_anchor_id"]), revised["claim"])
        revised["evidence_unit_id"] = new_id
        revised_units.append(revised)
        mappings.append(
            {
                "source_evidence_unit_id": old_id,
                "action": "replace",
                "result_evidence_unit_id": new_id,
            }
        )
    revised_ids = [str(row["evidence_unit_id"]) for row in revised_units]
    if len(revised_ids) != len(set(revised_ids)):
        raise ContractViolation(
            "identity.duplicate_revised_evidence_unit",
            "Evidence Unit 修订产生重复 ID。",
            path="$.revisions",
        )
    return validated, revised_units, mappings


def _evidence_revision_variant(evidence_id: str) -> dict[str, Any]:
    replacement = {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim", "caveats"],
        "properties": {
            "claim": {"type": "string", "minLength": 1, "maxLength": 320},
            "caveats": {
                "type": "array",
                "maxItems": 4,
                "items": {"type": "string", "minLength": 1, "maxLength": 240},
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["evidence_unit_id", "action", "replacement"],
        "properties": {
            "evidence_unit_id": {"const": evidence_id},
            "action": {"type": "string", "enum": ["replace", "delete"]},
            "replacement": {"oneOf": [replacement, {"type": "null"}]},
        },
        "allOf": [
            {
                "if": {"properties": {"action": {"const": "replace"}}, "required": ["action"]},
                "then": {"properties": {"replacement": replacement}},
            },
            {
                "if": {"properties": {"action": {"const": "delete"}}, "required": ["action"]},
                "then": {"properties": {"replacement": {"type": "null"}}},
            },
        ],
    }


def _unique_citations(evidence_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unit in evidence_units:
        for citation in unit.get("citations", []):
            identity = str(citation.get("quote_id") or "") or json.dumps(
                citation,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if identity in seen:
                continue
            seen.add(identity)
            citations.append(citation)
    return citations


def _assert_existing_decisions_are_pending(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SemanticAdjudicationError(f"无法读取待替换的人工裁决文件：{path}") from exc
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = set(reader.fieldnames or ())
    if not set(SEMANTIC_DECISION_EDITABLE_HEADERS).issubset(fieldnames):
        raise SemanticAdjudicationError("旧人工裁决文件缺少人工裁决或人工说明列，不能替换。")
    for row_number, row in enumerate(reader, start=2):
        if str(row.get("人工裁决") or "").strip() != "待裁决":
            raise SemanticAdjudicationError(
                f"旧人工裁决文件已包含实际裁决，不能替换：row={row_number}"
            )
        if str(row.get("人工说明") or "").strip():
            raise SemanticAdjudicationError(
                f"旧人工裁决文件已包含人工说明，不能替换：row={row_number}"
            )


def _cited_materials(
    citations: list[dict[str, Any]],
    material_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    seen: set[str] = set()
    for citation in citations:
        material_id = str(citation.get("material_id") or "")
        if material_id in seen:
            continue
        if material_id not in material_by_id:
            raise SemanticAdjudicationError(f"逐字引文指向未知 Card：{material_id!r}")
        seen.add(material_id)
        materials.append(material_by_id[material_id])
    return materials


def _format_evidence_units(evidence_units: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for index, unit in enumerate(evidence_units, start=1):
        caveats = "；".join(str(value) for value in unit.get("caveats", [])) or "无"
        parts.extend(
            [
                f"证据观点 {index}",
                f"观点：{unit.get('claim') or ''}",
                f"类型：{unit.get('evidence_type') or '未记录'}",
                f"与综述主题的关系：{unit.get('relevance') or '未记录'}",
                f"置信度：{unit.get('confidence') or '未记录'}",
                f"限定条件：{caveats}",
                f"证据抽取请求：{unit.get('request_id') or '未记录'}",
                f"论文内批次：{unit.get('batch_id') or '未记录'}",
                f"内部 Evidence ID：{unit.get('evidence_unit_id') or '未记录'}",
            ]
        )
    return "\n".join(parts)


def _format_related_paper_output(
    *,
    issue: dict[str, str],
    related_statements: list[dict[str, Any]],
    statement_records: list[dict[str, Any]],
) -> str:
    if issue["issue_type"] == "evidence_claim":
        if not related_statements:
            return "该 Evidence Unit 未被当前论文级分析引用。"
        return "\n".join(
            _statement_display(row, current_id="") for row in related_statements
        )
    current_id = issue["target_id"]
    field = issue["field"]
    same_field = [row for row in statement_records if str(row.get("field") or "") == field]
    header = f"所在字段共有 {len(same_field)} 条输出，以下展示完整字段上下文："
    return "\n".join(
        [header, *[_statement_display(row, current_id=current_id) for row in same_field]]
    )


def _statement_display(record: dict[str, Any], *, current_id: str) -> str:
    field = str(record.get("field") or "")
    index = record.get("index")
    position = "单项" if index is None else f"第{int(index) + 1}项"
    marker = "【当前待裁决】" if str(record.get("statement_id") or "") == current_id else ""
    return (
        f"{marker}[{FIELD_LABELS.get(field, field)} {position}] "
        f"{record.get('statement') or ''}"
    )


def _format_citations(citations: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"引文 {index}：{citation.get('quote') or ''}"
        for index, citation in enumerate(citations, start=1)
    )


def _format_source_locations(citations: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for index, citation in enumerate(citations, start=1):
        source_ref = citation.get("source_ref") or {}
        path = str(source_ref.get("path") or "未记录路径")
        start_line = source_ref.get("start_line")
        end_line = source_ref.get("end_line")
        if start_line is None or end_line is None:
            location = path
        elif start_line == end_line:
            location = f"{path}:L{int(start_line):05d}"
        else:
            location = f"{path}:L{int(start_line):05d}-L{int(end_line):05d}"
        rows.append(
            f"引文 {index}：{location}；Card={citation.get('material_id') or '未记录'}"
        )
    return "\n".join(rows)


def _format_materials(materials: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for index, material in enumerate(materials, start=1):
        rows.extend(
            [
                f"Card {index}",
                f"标题：{material.get('clean_title') or material.get('raw_title') or '无标题'}",
                f"位置：{_material_location(material)}",
                f"Card ID：{material.get('material_id') or '未记录'}",
                f"Card generation：{material.get('generation_id') or '未记录'}",
                "完整文本：",
                str(material.get("extract") or ""),
            ]
        )
    return "\n".join(rows)


def _format_neighbor_materials(
    *,
    cited_materials: list[dict[str, Any]],
    ordered_materials: list[dict[str, Any]],
    material_indexes: dict[str, int],
) -> str:
    cited_ids = {str(row["material_id"]) for row in cited_materials}
    neighbors: list[tuple[str, dict[str, Any], str]] = []
    seen: set[str] = set()
    for cited in cited_materials:
        cited_id = str(cited["material_id"])
        position = material_indexes[cited_id]
        for label, offset in (("前一张", -1), ("后一张", 1)):
            neighbor_index = position + offset
            if neighbor_index < 0 or neighbor_index >= len(ordered_materials):
                continue
            neighbor = ordered_materials[neighbor_index]
            neighbor_id = str(neighbor["material_id"])
            if neighbor_id in cited_ids or neighbor_id in seen:
                continue
            seen.add(neighbor_id)
            neighbors.append((label, neighbor, cited_id))
    if not neighbors:
        return "没有可展示的相邻 Card。"
    rows: list[str] = []
    for label, material, cited_id in neighbors:
        rows.extend(
            [
                f"{label} Card（相对于 {cited_id}）",
                f"标题：{material.get('clean_title') or material.get('raw_title') or '无标题'}",
                f"位置：{_material_location(material)}",
                f"Card ID：{material.get('material_id') or '未记录'}",
                f"Card generation：{material.get('generation_id') or '未记录'}",
                "完整文本：",
                str(material.get("extract") or ""),
            ]
        )
    return "\n".join(rows)


def _material_location(material: dict[str, Any]) -> str:
    span = material.get("source_span") or material.get("content_ref") or {}
    path = str(span.get("path") or material.get("source_path") or "未记录路径")
    start_line = span.get("start_line")
    end_line = span.get("end_line")
    if start_line is None or end_line is None:
        return path
    if start_line == end_line:
        return f"{path}:L{int(start_line):05d}"
    return f"{path}:L{int(start_line):05d}-L{int(end_line):05d}"


def _format_quality_flags(
    materials: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> str:
    flags = {
        str(flag)
        for row in [*materials, *citations]
        for key in ("confidence_flags", "quality_flags")
        for flag in row.get(key, [])
    }
    return "；".join(sorted(flags)) or "无"


def _field_label(field: str) -> str:
    if not field:
        return "不适用（Evidence Unit 级）"
    return f"{FIELD_LABELS.get(field, field)}（{field}）"


def _semantic_issue_workbook_lines(index: int, issue: dict[str, str]) -> list[str]:
    return [
        f"## 第{index}项：{ISSUE_TYPE_LABELS[issue['issue_type']]}",
        "",
        f"**你要判断：** {issue.get('human_question') or '未生成判断问题。'}",
        "",
        f"- 程序环节：{issue.get('stage') or '未记录'}",
        f"- 所在字段：{issue.get('field_label') or '未记录'}",
        f"- 门禁结论：{VERDICT_LABELS.get(issue['model_verdict'], issue['model_verdict'])}",
        f"- 门禁指出的冲突片段：{issue.get('conflict_fragments') or '未列出'}",
        f"- 门禁理由：{issue['model_reason']}",
        "",
        "### 1. 当前待判断内容",
        "",
        issue["content"],
        "",
        "### 2. 当前绑定的逐字引文",
        "",
        _workbook_text_block(issue.get("quotes") or "未记录逐字引文。"),
        "",
        f"原文位置：\n\n{issue.get('source_locations') or '未记录。'}",
        "",
        "### 3. Card 完整原文",
        "",
        "这是进入 Evidence 抽取器的完整 Card 快照，不等同于当前 Evidence Unit 已绑定的逐字引文。",
        "",
        _workbook_text_block(issue.get("card_full_text") or "未记录 Card。"),
        "",
        "### 4. 相邻 Card 上下文",
        "",
        _workbook_text_block(issue.get("neighbor_card_context") or "未记录相邻 Card。"),
        "",
        "### 5. Evidence 抽取器输出",
        "",
        _workbook_text_block(issue.get("upstream_evidence") or "未记录 Evidence Unit。"),
        "",
        "### 6. 论文级综合输出",
        "",
        _workbook_text_block(issue.get("related_paper_output") or "未记录论文级输出。"),
        "",
        "### 7. 两种裁决的实际后果",
        "",
        f"- 确认缺陷：{issue.get('confirm_effect') or '未记录'}",
        f"- 门禁误判：{issue.get('reject_effect') or '未记录'}",
        "",
        "### 8. 人工填写",
        "",
        "- 人工裁决：`待裁决`（改为 `确认缺陷` 或 `门禁误判`）",
        "- 人工说明：说明当前引文与观点，或陈述与字段职责之间为什么匹配/不匹配。",
        f"- 回复捷径：`第{index}项 = [确认缺陷/门禁误判]，因为……`",
        f"- 内部绑定 ID：`{issue['target_id']}`",
        f"- 质量标记：{issue.get('quality_flags') or '无'}",
        "",
    ]


def _workbook_text_block(value: str) -> str:
    return f"~~~~text\n{value}\n~~~~"


def _issue_csv_row(issue: dict[str, str], *, index: int) -> dict[str, str]:
    return {
        "裁决项": f"第{index}项",
        "论文标题": issue.get("paper_title", ""),
        "问题类型": ISSUE_TYPE_LABELS[issue["issue_type"]],
        "程序环节": issue.get("stage", ""),
        "所在字段": issue.get("field_label", issue["field"]),
        "原观点或陈述": issue["content"],
        "人工需回答的问题": issue.get("human_question", ""),
        "上游证据观点": issue.get("upstream_evidence", ""),
        "关联论文输出": issue.get("related_paper_output", ""),
        "逐字引文": issue.get("quotes", ""),
        "原文位置": issue.get("source_locations", ""),
        "Card标题": issue.get("card_titles", ""),
        "Card完整文本": issue.get("card_full_text", ""),
        "相邻Card上下文": issue.get("neighbor_card_context", ""),
        "质量标记": issue.get("quality_flags", ""),
        "模型结论": VERDICT_LABELS.get(issue["model_verdict"], issue["model_verdict"]),
        "冲突片段": issue.get("conflict_fragments", ""),
        "模型理由": issue["model_reason"],
        "确认缺陷后的处理": issue.get("confirm_effect", ""),
        "门禁误判后的处理": issue.get("reject_effect", ""),
        "内部目标ID": issue["target_id"],
    }


def _validate_schema(payload: object, schema: dict[str, Any]) -> dict[str, Any]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}" for item in error.absolute_path
        )
        raise ContractViolation("schema.evidence_revision_invalid", error.message, path=path)
    if not isinstance(payload, dict):
        raise ContractViolation("schema.evidence_revision_invalid", "payload must be an object")
    return copy.deepcopy(payload)


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
