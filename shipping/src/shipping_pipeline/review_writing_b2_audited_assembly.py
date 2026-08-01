from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .review_claim_audit_b2 import (
    ReviewClaimAuditB2RunSource,
    load_review_claim_audit_b2_run,
)
from .review_claim_audit_b2_adjudication import (
    ReviewClaimAuditB2AdjudicationRunSource,
    load_review_claim_audit_b2_adjudication_run,
)
from .review_writing_b2_revision import load_review_writing_b2_source
from .source_window_contracts import stable_id


AUDITED_ASSEMBLY_ROOT = "_review_drafts_b2_audited"
RELEASE_SET_SCHEMA_VERSION = "llm.review_writing_b2_audited_release_set.v1"
AUDITED_DRAFT_SCHEMA_VERSION = "llm.review_draft_b2_audited.v1"
AUDITED_RUN_SCHEMA_VERSION = "llm.review_draft_b2_audited_run.v1"
DISPOSITIONS = {
    "audit_passed",
    "adjudication_passed",
    "accepted_nonblocking",
}


class ReviewWritingB2AuditedAssemblyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewWritingB2AuditedAssemblySource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    release_set: dict[str, Any]
    draft: dict[str, Any]


def validate_audited_release_set(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) not in (
        {"schema_version", "allowed_nonblocking_risks", "chapters"},
        {
            "schema_version",
            "allowed_nonblocking_risks",
            "chapters",
            "release_set_id",
        },
    ):
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.release_set_invalid",
            "受审计发布集合字段不匹配。",
        )
    if payload["schema_version"] != RELEASE_SET_SCHEMA_VERSION:
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.release_schema_invalid",
            "受审计发布集合Schema版本不受支持。",
        )
    allowed = payload["allowed_nonblocking_risks"]
    if allowed != ["qualified"]:
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.allowed_risks_invalid",
            "当前正文门禁只允许记录qualified非阻断残余。",
        )
    chapters = payload["chapters"]
    if not isinstance(chapters, list) or len(chapters) != 8:
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.chapter_count_invalid",
            "受审计发布集合必须包含8个前文章节。",
        )
    normalized = []
    for index, row in enumerate(chapters, start=1):
        if not isinstance(row, dict) or set(row) != {
            "section_index",
            "chapter_run_id",
            "audit_run_id",
            "adjudication_run_id",
            "disposition",
        }:
            raise ReviewWritingB2AuditedAssemblyError(
                "audited_assembly_b2.chapter_entry_invalid",
                f"第{index}个章节条目字段不匹配。",
            )
        if row["section_index"] != index:
            raise ReviewWritingB2AuditedAssemblyError(
                "audited_assembly_b2.section_order_invalid",
                "章节必须按1至8连续排列。",
            )
        if row["disposition"] not in DISPOSITIONS:
            raise ReviewWritingB2AuditedAssemblyError(
                "audited_assembly_b2.disposition_invalid",
                f"第{index}节disposition无效。",
            )
        for field in ("chapter_run_id", "audit_run_id"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ReviewWritingB2AuditedAssemblyError(
                    "audited_assembly_b2.run_id_invalid",
                    f"第{index}节{field}必须非空。",
                )
        adjudication_run_id = row["adjudication_run_id"]
        if adjudication_run_id is not None and (
            not isinstance(adjudication_run_id, str)
            or not adjudication_run_id.strip()
        ):
            raise ReviewWritingB2AuditedAssemblyError(
                "audited_assembly_b2.adjudication_run_id_invalid",
                f"第{index}节adjudication_run_id无效。",
            )
        normalized.append(copy.deepcopy(row))
    without_id = {
        "schema_version": RELEASE_SET_SCHEMA_VERSION,
        "allowed_nonblocking_risks": copy.deepcopy(allowed),
        "chapters": normalized,
    }
    value = {
        **without_id,
        "release_set_id": stable_id(
            "review_writing_b2_audited_release_set", without_id
        ),
    }
    if payload.get("release_set_id") not in (None, value["release_set_id"]):
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.release_set_id_invalid",
            "release_set_id与发布集合内容不一致。",
        )
    return value


def load_audited_release_set(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.release_read_failed",
            f"无法读取受审计发布集合：{path}",
        ) from exc
    return validate_audited_release_set(payload)


def build_audited_draft(
    workspace: str | Path,
    release_set: dict[str, Any],
    *,
    chapter_loader: Callable[[str | Path, str], Any] = load_review_writing_b2_source,
    audit_loader: Callable[
        [str | Path, str], ReviewClaimAuditB2RunSource
    ] = load_review_claim_audit_b2_run,
    adjudication_loader: Callable[
        [str | Path, str], ReviewClaimAuditB2AdjudicationRunSource
    ] = load_review_claim_audit_b2_adjudication_run,
) -> dict[str, Any]:
    workspace_path = Path(workspace)
    chapter_sources = []
    outcomes = []
    for entry in release_set["chapters"]:
        chapter_source = chapter_loader(
            workspace_path, entry["chapter_run_id"]
        )
        audit_source = audit_loader(workspace_path, entry["audit_run_id"])
        if (
            chapter_source.run_id != audit_source.writing_source.run_id
            or chapter_source.chapter["chapter_id"]
            != audit_source.audit["chapter_id"]
            or chapter_source.chapter["section_index"]
            != entry["section_index"]
        ):
            raise ReviewWritingB2AuditedAssemblyError(
                "audited_assembly_b2.source_chain_mismatch",
                f"第{entry['section_index']}节章节与审计来源链不一致。",
            )
        adjudication_source = None
        if entry["adjudication_run_id"] is not None:
            adjudication_source = adjudication_loader(
                workspace_path, entry["adjudication_run_id"]
            )
            if adjudication_source.audit_source.run_id != audit_source.run_id:
                raise ReviewWritingB2AuditedAssemblyError(
                    "audited_assembly_b2.adjudication_chain_mismatch",
                    f"第{entry['section_index']}节裁决与审计来源链不一致。",
                )
        residual_risks = _validate_disposition(
            entry, audit_source, adjudication_source, release_set
        )
        chapter_sources.append(chapter_source)
        outcomes.append(
            {
                **copy.deepcopy(entry),
                "chapter_id": chapter_source.chapter["chapter_id"],
                "audit_id": audit_source.audit["audit_id"],
                "adjudication_id": (
                    adjudication_source.adjudication["adjudication_id"]
                    if adjudication_source
                    else None
                ),
                "blocking_sentence_count": 0,
                "residual_risks": residual_risks,
            }
        )
    framework_ids = {
        source.writing_input["framework_outline"]["framework_b2_id"]
        for source in chapter_sources
    }
    if len(framework_ids) != 1:
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.framework_mixed",
            "章节来自多个Framework B2。",
        )
    framework = chapter_sources[0].writing_input["framework_outline"]
    expected_ids = [
        row["section_id"]
        for row in framework["sections"]
        if row["section_type"] != "conclusion"
    ]
    actual_ids = [source.chapter["section_id"] for source in chapter_sources]
    if actual_ids != expected_ids:
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.section_set_invalid",
            "章节集合或顺序与Framework不一致。",
        )
    citations: dict[str, dict[str, Any]] = {}
    for source in chapter_sources:
        for citation in source.writing_input["citation_metadata"]:
            key = citation["citation_key"]
            if key in citations and citations[key] != citation:
                raise ReviewWritingB2AuditedAssemblyError(
                    "audited_assembly_b2.citation_conflict",
                    f"citation key题录冲突：{key}",
                )
            citations[key] = copy.deepcopy(citation)
    chapters = [copy.deepcopy(source.chapter) for source in chapter_sources]
    residual_count = sum(len(row["residual_risks"]) for row in outcomes)
    without_id = {
        "schema_version": AUDITED_DRAFT_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3f",
        "formal_review_published": False,
        "body_release_ready": True,
        "conclusion_input_ready": True,
        "release_set_id": release_set["release_set_id"],
        "framework_b2_id": next(iter(framework_ids)),
        "working_title": framework["working_title"],
        "central_question": framework["central_question"],
        "chapter_run_ids": [source.run_id for source in chapter_sources],
        "chapter_ids": [source.chapter["chapter_id"] for source in chapter_sources],
        "chapters": chapters,
        "audit_outcomes": outcomes,
        "audit_summary": {
            "chapter_count": len(chapters),
            "blocking_sentence_count": 0,
            "nonblocking_residual_count": residual_count,
            "status": (
                "passed_with_nonblocking_residuals"
                if residual_count
                else "passed"
            ),
        },
        "citation_metadata": [citations[key] for key in sorted(citations)],
        "conclusion": {
            "status": "ready_for_separate_conclusion_generation",
            "chapter_id": None,
        },
        "markdown": _assemble_markdown(framework, chapters, outcomes),
    }
    return {
        **without_id,
        "draft_id": stable_id("review_draft_b2_audited", without_id),
    }


def _validate_disposition(
    entry: dict[str, Any],
    audit_source: ReviewClaimAuditB2RunSource,
    adjudication_source: ReviewClaimAuditB2AdjudicationRunSource | None,
    release_set: dict[str, Any],
) -> list[str]:
    disposition = entry["disposition"]
    if disposition == "audit_passed":
        if adjudication_source is not None or not audit_source.audit["summary"][
            "publishable"
        ]:
            raise ReviewWritingB2AuditedAssemblyError(
                "audited_assembly_b2.audit_pass_invalid",
                f"第{entry['section_index']}节不满足audit_passed。",
            )
        return []
    if adjudication_source is None:
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.adjudication_required",
            f"第{entry['section_index']}节缺少裁决运行。",
        )
    summary = adjudication_source.adjudication["summary"]
    if disposition == "adjudication_passed":
        if not summary["publishable_after_adjudication"]:
            raise ReviewWritingB2AuditedAssemblyError(
                "audited_assembly_b2.adjudication_pass_invalid",
                f"第{entry['section_index']}节裁决后未通过。",
            )
        return []
    if summary["final_blocking_sentence_count"] != 0:
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.blocking_risk_present",
            f"第{entry['section_index']}节仍有阻断风险。",
        )
    residual = sorted(
        risk
        for risk, count in summary["final_risk_counts"].items()
        if risk != "supported" and count
    )
    if any(
        risk not in release_set["allowed_nonblocking_risks"]
        for risk in residual
    ):
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.residual_risk_not_allowed",
            f"第{entry['section_index']}节存在未允许的残余风险：{residual}。",
        )
    return residual


def _assemble_markdown(
    framework: dict[str, Any],
    chapters: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {framework['working_title']}",
        "",
        "## 资料范围",
        "",
        "本文仅基于本次显式纳入的样本文献展开。前文章节已完成逐句Claim审计及必要裁决，结论章尚未生成。",
        "",
    ]
    for chapter in chapters:
        lines.extend([f"## {chapter['section_index']}. {chapter['title']}", ""])
        for paragraph in chapter["paragraphs"]:
            text = paragraph["text"].strip()
            if paragraph["citation_keys"]:
                text += " [" + "; ".join(
                    f"@{key}" for key in paragraph["citation_keys"]
                ) + "]"
            lines.extend([text, ""])
    residuals = [
        f"第{row['section_index']}节：{', '.join(row['residual_risks'])}"
        for row in outcomes
        if row["residual_risks"]
    ]
    lines.extend(["## 审计说明", ""])
    if residuals:
        lines.append("正文无阻断风险；保留以下非阻断限定项：")
        lines.extend(f"- {value}" for value in residuals)
    else:
        lines.append("正文未保留阻断或非阻断风险。")
    lines.extend(["", "## 结论", "", "[尚未生成：进入独立结论章合同。]", ""])
    return "\n".join(lines).rstrip() + "\n"


class ReviewWritingB2AuditedAssembler:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def build(
        self, *, release_file: str | Path, run_id: str | None = None
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / AUDITED_ASSEMBLY_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewWritingB2AuditedAssemblyError(
                "audited_assembly_b2.run_exists",
                f"run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        release_set = None
        draft = None
        try:
            release_set = load_audited_release_set(Path(release_file))
            _write_json(run_dir / "input" / "release_set.json", release_set)
            draft = build_audited_draft(self.workspace, release_set)
            output_path = run_dir / "output" / "review_draft_b2_audited.json"
            _write_json(output_path, draft)
            _write_text(
                run_dir / "review" / "review_draft_b2_audited.md",
                draft["markdown"],
            )
            manifest = _manifest(
                resolved, "completed", started_at, release_set, draft, output_path, None
            )
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(
                resolved, "failed", started_at, release_set, None, None, failure
            )
        _write_json(run_dir / "manifest.json", manifest)
        return _result(run_dir, manifest)


def load_review_writing_b2_audited_assembly(
    workspace: str | Path, run_id: str
) -> ReviewWritingB2AuditedAssemblySource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / AUDITED_ASSEMBLY_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "受审计装配manifest")
    manifest = _json_object(manifest_bytes, "受审计装配manifest")
    if (
        manifest.get("schema_version") != AUDITED_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.run_unavailable", "受审计装配运行不可用。"
        )
    release_set = validate_audited_release_set(
        _read_json(run_dir / "input" / "release_set.json", "受审计发布集合")
    )
    replay = build_audited_draft(workspace_path, release_set)
    output_bytes = _read_bytes(
        run_dir / "output" / "review_draft_b2_audited.json", "受审计正文"
    )
    draft = _json_object(output_bytes, "受审计正文")
    if (
        replay != draft
        or manifest.get("draft_id") != draft.get("draft_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewWritingB2AuditedAssemblyError(
            "audited_assembly_b2.output_replay_mismatch",
            "受审计正文身份、哈希或确定性重放不一致。",
        )
    return ReviewWritingB2AuditedAssemblySource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        release_set=release_set,
        draft=draft,
    )


def _manifest(run_id, status, started_at, release_set, draft, output_path, failure):
    return {
        "schema_version": AUDITED_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3f",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "release_set_id": release_set["release_set_id"] if release_set else None,
        "draft_id": draft["draft_id"] if draft else None,
        "body_release_ready": draft["body_release_ready"] if draft else False,
        "conclusion_input_ready": draft["conclusion_input_ready"] if draft else False,
        "audit_summary": draft["audit_summary"] if draft else None,
        "output_sha256": _sha256_bytes(output_path.read_bytes()) if output_path else None,
        "failure": failure,
        "artifacts": {
            "release_set": "input/release_set.json" if release_set else None,
            "draft": "output/review_draft_b2_audited.json" if output_path else None,
            "markdown": "review/review_draft_b2_audited.md" if output_path else None,
            "failures": "audit/failures.jsonl" if failure else None,
        },
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "output_path": str(run_dir / manifest["artifacts"]["draft"]) if manifest["artifacts"]["draft"] else None,
        "review_path": str(run_dir / manifest["artifacts"]["markdown"]) if manifest["artifacts"]["markdown"] else None,
        "draft_id": manifest["draft_id"],
        "body_release_ready": manifest["body_release_ready"],
        "conclusion_input_ready": manifest["conclusion_input_ready"],
        "audit_summary": manifest["audit_summary"],
        "failure": manifest["failure"],
    }


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewWritingB2AuditedAssemblyError("audited_assembly_b2.artifact_missing", f"{label}不存在：{path}")
    return path.read_bytes()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewWritingB2AuditedAssemblyError("audited_assembly_b2.json_invalid", f"{label}不是有效JSON。") from exc
    if not isinstance(value, dict):
        raise ReviewWritingB2AuditedAssemblyError("audited_assembly_b2.json_object_required", f"{label}必须是JSON对象。")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8"))


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((value.rstrip() + "\n").encode("utf-8"))


def _safe_segment(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value in {".", ".."} or any(char in value for char in ("/", "\\", "\x00")):
        raise ReviewWritingB2AuditedAssemblyError("audited_assembly_b2.run_id_invalid", "run_id不是安全路径段。")
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat()
