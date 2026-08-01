from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .review_conclusion_audit_b2 import (
    ReviewConclusionAuditB2RunSource,
    load_review_conclusion_audit_b2_run,
)
from .review_conclusion_b2_contracts import (
    CONCLUSION_DRAFT_SCHEMA_VERSION,
    build_conclusion_draft_schema,
    validate_conclusion_draft,
)
from .source_window_contracts import stable_id


CONCLUSION_REVISION_B2_ROOT = "_review_conclusion_revisions_b2"
CONCLUSION_REVISION_B2_RUN_SCHEMA_VERSION = "llm.review_conclusion_revision_b2_run.v1"
CONCLUSION_REVISION_DECISION_SCHEMA_VERSION = (
    "llm.review_conclusion_revision_decisions_b2.v1"
)


class ReviewConclusionRevisionB2Error(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewConclusionRevisionB2RunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    conclusion_input: dict[str, Any]
    conclusion: dict[str, Any]
    audit_source: ReviewConclusionAuditB2RunSource
    decision_set: dict[str, Any]


def load_review_conclusion_revision_b2_run(
    workspace: str | Path, run_id: str
) -> ReviewConclusionRevisionB2RunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / CONCLUSION_REVISION_B2_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "B2结论修订manifest")
    manifest = _json_object(manifest_bytes, "B2结论修订manifest")
    if (
        manifest.get("schema_version") != CONCLUSION_REVISION_B2_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.run_unavailable", "B2结论修订运行不可用。"
        )
    audit_source = load_review_conclusion_audit_b2_run(
        workspace_path, str(manifest["source_audit_run_id"])
    )
    decision_set = validate_conclusion_revision_decisions(
        _read_json(run_dir / "input" / "decision_set.json", "B2结论修订决策")
    )
    replay = apply_conclusion_revision(audit_source, decision_set)
    output_bytes = _read_bytes(
        run_dir / "output" / "review_conclusion_revision_b2.json", "B2修订结论"
    )
    conclusion = _json_object(output_bytes, "B2修订结论")
    if (
        replay != conclusion
        or manifest.get("chapter_id") != conclusion.get("chapter_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.output_replay_mismatch", "B2修订结论哈希或重放不一致。"
        )
    source = audit_source.conclusion_source
    return ReviewConclusionRevisionB2RunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        conclusion_input=source.conclusion_input,
        conclusion=conclusion,
        audit_source=audit_source,
        decision_set=decision_set,
    )


def load_conclusion_revision_decisions(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.decision_read_failed",
            f"无法读取结论修订决策：{path}",
        ) from exc
    return validate_conclusion_revision_decisions(payload)


def validate_conclusion_revision_decisions(payload: object) -> dict[str, Any]:
    expected = {
        "schema_version",
        "source_audit_run_id",
        "source_audit_manifest_sha256",
        "decisions",
    }
    if not isinstance(payload, dict) or set(payload) not in (
        expected,
        expected | {"decision_set_id"},
    ):
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.decision_fields_invalid",
            "结论修订决策字段不匹配。",
        )
    if payload["schema_version"] != CONCLUSION_REVISION_DECISION_SCHEMA_VERSION:
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.decision_schema_invalid",
            "结论修订决策Schema版本不受支持。",
        )
    if not isinstance(payload["decisions"], list) or not payload["decisions"]:
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.decisions_empty", "结论修订决策不得为空。"
        )
    decisions = []
    for row in payload["decisions"]:
        if not isinstance(row, dict) or set(row) != {
            "sentence_id",
            "action",
            "replacement_text",
            "retained_source_claim_ids",
            "rationale",
        }:
            raise ReviewConclusionRevisionB2Error(
                "conclusion_revision_b2.decision_entry_invalid",
                "单条结论修订决策字段不匹配。",
            )
        if row["action"] not in {"delete", "replace"}:
            raise ReviewConclusionRevisionB2Error(
                "conclusion_revision_b2.action_invalid", "action只能为delete或replace。"
            )
        if row["action"] == "delete":
            if row["replacement_text"] is not None or row["retained_source_claim_ids"]:
                raise ReviewConclusionRevisionB2Error(
                    "conclusion_revision_b2.delete_payload_invalid",
                    "delete不得携带替换文本或保留Claim。",
                )
        elif (
            not isinstance(row["replacement_text"], str)
            or not row["replacement_text"].strip()
            or row["replacement_text"] != row["replacement_text"].strip()
            or row["replacement_text"][-1:] not in "。！？"
            or not isinstance(row["retained_source_claim_ids"], list)
            or not row["retained_source_claim_ids"]
        ):
            raise ReviewConclusionRevisionB2Error(
                "conclusion_revision_b2.replace_payload_invalid",
                "replace必须提供规范句子和非空保留Claim列表。",
            )
        if not isinstance(row["rationale"], str) or not row["rationale"].strip():
            raise ReviewConclusionRevisionB2Error(
                "conclusion_revision_b2.rationale_missing", "修订理由不得为空。"
            )
        decisions.append(copy.deepcopy(row))
    sentence_ids = [row["sentence_id"] for row in decisions]
    if len(sentence_ids) != len(set(sentence_ids)):
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.sentence_duplicate", "同一句不得重复裁决。"
        )
    without_id = {
        "schema_version": CONCLUSION_REVISION_DECISION_SCHEMA_VERSION,
        "source_audit_run_id": payload["source_audit_run_id"],
        "source_audit_manifest_sha256": payload["source_audit_manifest_sha256"],
        "decisions": decisions,
    }
    value = {
        **without_id,
        "decision_set_id": stable_id("review_conclusion_revision_decisions_b2", without_id),
    }
    if payload.get("decision_set_id") not in (None, value["decision_set_id"]):
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.decision_id_invalid", "decision_set_id与内容不一致。"
        )
    return value


def apply_conclusion_revision(
    audit_source: ReviewConclusionAuditB2RunSource,
    decision_set: dict[str, Any],
) -> dict[str, Any]:
    if (
        decision_set["source_audit_run_id"] != audit_source.run_id
        or decision_set["source_audit_manifest_sha256"]
        != audit_source.manifest_sha256
    ):
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.source_mismatch", "修订决策与审计来源不一致。"
        )
    audit_rows = {
        row["sentence_id"]: row for row in audit_source.audit["sentence_audits"]
    }
    decisions = {row["sentence_id"]: row for row in decision_set["decisions"]}
    if not set(decisions) <= set(audit_rows):
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.sentence_unknown", "修订决策包含未知句子。"
        )
    blockers = {
        sentence_id for sentence_id, row in audit_rows.items() if row["blocking"]
    }
    if not blockers <= set(decisions):
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.blocker_unresolved",
            f"仍有阻断句未决策：{sorted(blockers - set(decisions))}。",
        )
    source = audit_source.conclusion_source
    source_conclusion = source.conclusion
    conclusion_input = source.conclusion_input
    sentence_inputs = audit_source.audit_input["sentences"]
    audit_by_position = {
        (row["paragraph_index"], row["sentence_id"]): audit_rows[row["sentence_id"]]
        for row in sentence_inputs
    }
    source_paragraphs = {
        row["paragraph_index"]: row for row in source_conclusion["paragraphs"]
    }
    claim_by_id = {
        row["claim_id"]: row for row in conclusion_input["eligible_claims"]
    }
    draft_paragraphs = []
    for paragraph_index in sorted(source_paragraphs):
        source_paragraph = source_paragraphs[paragraph_index]
        texts = []
        used_claims = set()
        for sentence in [
            row for row in sentence_inputs if row["paragraph_index"] == paragraph_index
        ]:
            audit_row = audit_by_position[(paragraph_index, sentence["sentence_id"])]
            decision = decisions.get(sentence["sentence_id"])
            if decision and decision["action"] == "delete":
                continue
            if decision:
                allowed = set(source_paragraph["source_claim_ids"])
                retained = decision["retained_source_claim_ids"]
                if not set(retained) <= allowed:
                    raise ReviewConclusionRevisionB2Error(
                        "conclusion_revision_b2.retained_claim_invalid",
                        "替换句只能保留原段已绑定Claim。",
                    )
                texts.append(decision["replacement_text"])
                used_claims.update(retained)
            else:
                texts.append(sentence["text"])
                used_claims.update(
                    set(audit_row["supporting_claim_ids"])
                    & set(source_paragraph["source_claim_ids"])
                )
        if not texts or not used_claims:
            raise ReviewConclusionRevisionB2Error(
                "conclusion_revision_b2.paragraph_empty",
                f"修订导致第{paragraph_index}段为空或无来源Claim。",
            )
        ordered_claims = [
            value
            for value in source_paragraph["source_claim_ids"]
            if value in used_claims
        ]
        citations = sorted(
            {key for claim_id in ordered_claims for key in claim_by_id[claim_id]["citation_keys"]}
        )
        draft_paragraphs.append(
            {
                "paragraph_index": paragraph_index,
                "group": source_paragraph["group"],
                "claim_type": source_paragraph["claim_type"],
                "text": "".join(texts),
                "source_claim_ids": ordered_claims,
                "citation_keys": citations,
            }
        )
    payload = {
        "schema_version": CONCLUSION_DRAFT_SCHEMA_VERSION,
        "section_id": source_conclusion["section_id"],
        "section_index": source_conclusion["section_index"],
        "title": source_conclusion["title"],
        "paragraphs": draft_paragraphs,
    }
    schema = build_conclusion_draft_schema(conclusion_input)
    revised = validate_conclusion_draft(
        payload, conclusion_input=conclusion_input, schema=schema
    )
    revised["revision"] = {
        "source_conclusion_run_id": source.run_id,
        "source_chapter_id": source_conclusion["chapter_id"],
        "source_audit_run_id": audit_source.run_id,
        "source_audit_id": audit_source.audit["audit_id"],
        "decision_set_id": decision_set["decision_set_id"],
        "decision_count": len(decisions),
        "semantic_audit": "pending",
    }
    return revised


class ReviewConclusionRevisionB2Runner:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(self, *, audit_run_id: str, decision_file: str | Path, run_id: str | None = None):
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / CONCLUSION_REVISION_B2_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewConclusionRevisionB2Error(
                "conclusion_revision_b2.run_exists", f"run_id已存在：{resolved}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        source = decision_set = conclusion = None
        try:
            source = load_review_conclusion_audit_b2_run(self.workspace, audit_run_id)
            decision_set = load_conclusion_revision_decisions(Path(decision_file))
            _write_json(run_dir / "input" / "decision_set.json", decision_set)
            conclusion = apply_conclusion_revision(source, decision_set)
            output_path = run_dir / "output" / "review_conclusion_revision_b2.json"
            _write_json(output_path, conclusion)
            _write_report(run_dir / "review" / "review_conclusion_revision_b2.md", conclusion, decision_set)
            manifest = _manifest(resolved, "completed", started_at, audit_run_id, source, decision_set, conclusion, output_path, None)
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(resolved, "failed", started_at, audit_run_id, source, decision_set, conclusion, None, failure)
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": manifest["status"],
            "run_dir": str(run_dir),
            "output_path": str(run_dir / "output" / "review_conclusion_revision_b2.json") if conclusion else None,
            "report_path": str(run_dir / "review" / "review_conclusion_revision_b2.md") if conclusion else None,
            "chapter_id": conclusion["chapter_id"] if conclusion else None,
            "failure": manifest["failure"],
        }


def _write_report(path, conclusion, decisions):
    lines = [
        f"# {conclusion['section_index']}. {conclusion['title']}", "",
        f"- 修订决策：{len(decisions['decisions'])}",
        f"- 正文字符：{conclusion['total_chars']}",
        "- 状态：等待结论语义复审", "",
    ]
    for paragraph in conclusion["paragraphs"]:
        lines.extend([
            f"## 段落 {paragraph['paragraph_index']}", "",
            paragraph["text"], "",
            f"- 来源Claim：{', '.join(paragraph['source_claim_ids'])}", "",
        ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _manifest(run_id, status, started_at, audit_run_id, source, decisions, conclusion, output_path, failure):
    return {
        "schema_version": CONCLUSION_REVISION_B2_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3i",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "source_audit_run_id": audit_run_id,
        "source_audit_manifest_sha256": source.manifest_sha256 if source else None,
        "decision_set_id": decisions["decision_set_id"] if decisions else None,
        "chapter_id": conclusion["chapter_id"] if conclusion else None,
        "output_sha256": _sha256_bytes(output_path.read_bytes()) if output_path else None,
        "failure": failure,
    }


def _safe_segment(value):
    if not value or Path(value).name != value or value in {".", ".."}:
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.run_id_invalid", "run_id不是安全路径段。"
        )


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.artifact_missing", f"{label}不存在：{path}"
        )
    return path.read_bytes()


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.json_invalid", f"{label}不是有效UTF-8 JSON。"
        ) from exc
    if not isinstance(value, dict):
        raise ReviewConclusionRevisionB2Error(
            "conclusion_revision_b2.json_object_required", f"{label}必须是JSON对象。"
        )
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _sha256_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _now():
    return datetime.now(UTC).isoformat()
