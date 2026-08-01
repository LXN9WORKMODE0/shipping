from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .review_conclusion_audit_b2 import (
    ReviewConclusionAuditB2RunSource,
    load_review_conclusion_audit_b2_run,
)
from .review_conclusion_audit_b2 import load_review_conclusion_b2_source
from .review_writing_b2_audited_assembly import (
    ReviewWritingB2AuditedAssemblySource,
    load_review_writing_b2_audited_assembly,
)
from .source_window_contracts import stable_id


FINAL_REVIEW_B2_ROOT = "_review_final_b2"
FINAL_RELEASE_SCHEMA_VERSION = "llm.review_b2_final_release.v1"
FINAL_REVIEW_SCHEMA_VERSION = "llm.review_b2_final.v1"
FINAL_RUN_SCHEMA_VERSION = "llm.review_b2_final_run.v1"


class ReviewB2FinalAssemblyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewB2FinalRunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    release: dict[str, Any]
    review: dict[str, Any]


def validate_final_release(payload: object) -> dict[str, Any]:
    expected = {
        "schema_version",
        "audited_assembly_run_id",
        "conclusion_run_id",
        "conclusion_audit_run_id",
    }
    if not isinstance(payload, dict) or set(payload) not in (
        expected, expected | {"release_id"}
    ):
        raise ReviewB2FinalAssemblyError(
            "final_b2.release_fields_invalid", "最终发布配置字段不匹配。"
        )
    if payload["schema_version"] != FINAL_RELEASE_SCHEMA_VERSION:
        raise ReviewB2FinalAssemblyError(
            "final_b2.release_schema_invalid", "最终发布配置版本不受支持。"
        )
    for field in expected - {"schema_version"}:
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ReviewB2FinalAssemblyError(
                "final_b2.release_run_id_invalid", f"{field}必须为非空字符串。"
            )
    without_id = {key: copy.deepcopy(payload[key]) for key in expected}
    value = {
        **without_id,
        "release_id": stable_id("review_b2_final_release", without_id),
    }
    if payload.get("release_id") not in (None, value["release_id"]):
        raise ReviewB2FinalAssemblyError(
            "final_b2.release_id_invalid", "release_id与内容不一致。"
        )
    return value


def load_final_release(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewB2FinalAssemblyError(
            "final_b2.release_read_failed", f"无法读取最终发布配置：{path}"
        ) from exc
    return validate_final_release(value)


def build_final_review(
    workspace: str | Path,
    release: dict[str, Any],
    *,
    body_loader: Callable[[str | Path, str], ReviewWritingB2AuditedAssemblySource]
    = load_review_writing_b2_audited_assembly,
    conclusion_loader: Callable[[str | Path, str], Any]
    = load_review_conclusion_b2_source,
    audit_loader: Callable[[str | Path, str], ReviewConclusionAuditB2RunSource]
    = load_review_conclusion_audit_b2_run,
) -> dict[str, Any]:
    body = body_loader(workspace, release["audited_assembly_run_id"])
    conclusion = conclusion_loader(workspace, release["conclusion_run_id"])
    audit = audit_loader(workspace, release["conclusion_audit_run_id"])
    if audit.conclusion_source.run_id != conclusion.run_id:
        raise ReviewB2FinalAssemblyError(
            "final_b2.conclusion_audit_chain_mismatch",
            "结论与结论审计来源链不一致。",
        )
    if (
        audit.audit["summary"]["publishable"] is not True
        or audit.audit["summary"]["blocking_sentence_count"] != 0
    ):
        raise ReviewB2FinalAssemblyError(
            "final_b2.conclusion_audit_not_passed", "结论审计尚未通过零阻断门禁。"
        )
    conclusion_input = conclusion.conclusion_input
    if (
        conclusion_input["source"]["audited_assembly_run_id"] != body.run_id
        or conclusion_input["source"]["audited_draft_id"] != body.draft["draft_id"]
        or conclusion_input["source"]["release_set_id"] != body.draft["release_set_id"]
    ):
        raise ReviewB2FinalAssemblyError(
            "final_b2.body_conclusion_chain_mismatch",
            "结论输入与受审计正文来源链不一致。",
        )
    chapters = copy.deepcopy(body.draft["chapters"])
    chapters.append(copy.deepcopy(conclusion.conclusion))
    if [row["section_index"] for row in chapters] != list(range(1, 10)):
        raise ReviewB2FinalAssemblyError(
            "final_b2.section_order_invalid", "最终综述必须包含连续的1至9节。"
        )
    citation_metadata = copy.deepcopy(body.draft["citation_metadata"])
    cited_keys = {
        key for chapter in chapters for paragraph in chapter["paragraphs"]
        for key in paragraph["citation_keys"]
    }
    metadata_keys = {row["citation_key"] for row in citation_metadata}
    if not cited_keys <= metadata_keys:
        raise ReviewB2FinalAssemblyError(
            "final_b2.citation_metadata_missing", "最终正文存在无法映射的引用。"
        )
    used_metadata = [
        row for row in citation_metadata if row["citation_key"] in cited_keys
    ]
    without_id = {
        "schema_version": FINAL_REVIEW_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3j",
        "formal_review_published": True,
        "release_id": release["release_id"],
        "working_title": body.draft["working_title"],
        "central_question": body.draft["central_question"],
        "source": {
            "audited_assembly_run_id": body.run_id,
            "audited_draft_id": body.draft["draft_id"],
            "conclusion_run_id": conclusion.run_id,
            "conclusion_chapter_id": conclusion.conclusion["chapter_id"],
            "conclusion_audit_run_id": audit.run_id,
            "conclusion_audit_id": audit.audit["audit_id"],
        },
        "chapters": chapters,
        "citation_metadata": used_metadata,
        "quality_gate": {
            "body_blocking_sentence_count": body.draft["audit_summary"][
                "blocking_sentence_count"
            ],
            "body_nonblocking_residual_count": body.draft["audit_summary"][
                "nonblocking_residual_count"
            ],
            "conclusion_blocking_sentence_count": audit.audit["summary"][
                "blocking_sentence_count"
            ],
            "status": "passed",
        },
    }
    markdown = _render_formal_review(without_id)
    return {
        **without_id,
        "markdown": markdown,
        "review_id": stable_id("review_b2_final", without_id),
    }


def _render_formal_review(review: dict[str, Any]) -> str:
    lines = [f"# {review['working_title']}", ""]
    for chapter in review["chapters"]:
        lines.extend([f"## {chapter['section_index']}. {chapter['title']}", ""])
        for paragraph in chapter["paragraphs"]:
            text = paragraph["text"].strip()
            if paragraph["citation_keys"]:
                text += " [" + "; ".join(
                    f"@{key}" for key in paragraph["citation_keys"]
                ) + "]"
            lines.extend([text, ""])
    lines.extend(["## 参考文献", ""])
    for row in review["citation_metadata"]:
        lines.append(f"- [@{row['citation_key']}] {_format_reference(row)}")
    return "\n".join(lines).rstrip() + "\n"


def _format_reference(row: dict[str, Any]) -> str:
    authors = "，".join(row.get("authors") or ["作者不详"])
    title = row["paper_title"]
    year = row.get("year") or "年份不详"
    reference_type = row.get("reference_type")
    if reference_type == "article":
        journal = row.get("journal") or "期刊不详"
        return f"{authors}. {title}[J]. {journal}, {year}."
    if reference_type in {"thesis", "dissertation"}:
        institution = row.get("institution") or "授予单位不详"
        return f"{authors}. {title}[D]. {institution}, {year}."
    return f"{authors}. {title}. {year}."


class ReviewB2FinalAssembler:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def build(self, *, release_file: str | Path, run_id: str | None = None):
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / FINAL_REVIEW_B2_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewB2FinalAssemblyError(
                "final_b2.run_exists", f"run_id已存在：{resolved}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        release = review = None
        try:
            release = load_final_release(Path(release_file))
            _write_json(run_dir / "input" / "release.json", release)
            review = build_final_review(self.workspace, release)
            output_path = run_dir / "output" / "review_b2_final.json"
            _write_json(output_path, review)
            _write_text(run_dir / "review" / "review_b2_final.md", review["markdown"])
            _write_text(run_dir / "review" / "release_audit.md", _render_audit(review))
            manifest = _manifest(resolved, "completed", started_at, release, review, output_path, None)
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(resolved, "failed", started_at, release, review, None, failure)
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": manifest["status"],
            "run_dir": str(run_dir),
            "output_path": str(run_dir / "output" / "review_b2_final.json") if review else None,
            "review_path": str(run_dir / "review" / "review_b2_final.md") if review else None,
            "audit_path": str(run_dir / "review" / "release_audit.md") if review else None,
            "review_id": review["review_id"] if review else None,
            "failure": manifest["failure"],
        }


def load_review_b2_final_run(
    workspace: str | Path, run_id: str
) -> ReviewB2FinalRunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / FINAL_REVIEW_B2_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "B2最终综述manifest")
    manifest = _json_object(manifest_bytes, "B2最终综述manifest")
    if (
        manifest.get("schema_version") != FINAL_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
        or manifest.get("formal_review_published") is not True
    ):
        raise ReviewB2FinalAssemblyError(
            "final_b2.run_unavailable", "B2最终综述运行不可用。"
        )
    release = validate_final_release(
        _read_json(run_dir / "input" / "release.json", "B2最终发布配置")
    )
    replay = build_final_review(workspace_path, release)
    output_bytes = _read_bytes(
        run_dir / "output" / "review_b2_final.json", "B2最终综述输出"
    )
    review = _json_object(output_bytes, "B2最终综述输出")
    if (
        replay != review
        or manifest.get("review_id") != review.get("review_id")
        or manifest.get("release_id") != release.get("release_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewB2FinalAssemblyError(
            "final_b2.output_replay_mismatch", "B2最终综述哈希、身份或重放不一致。"
        )
    return ReviewB2FinalRunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        release=release,
        review=review,
    )


def _render_audit(review):
    source = review["source"]
    gate = review["quality_gate"]
    return "\n".join([
        "# B2最终综述发布审计", "",
        f"- 综述ID：`{review['review_id']}`",
        f"- 正文章节来源：`{source['audited_assembly_run_id']}`",
        f"- 结论来源：`{source['conclusion_run_id']}`",
        f"- 结论审计：`{source['conclusion_audit_run_id']}`",
        f"- 正文阻断：{gate['body_blocking_sentence_count']}",
        f"- 正文非阻断残余：{gate['body_nonblocking_residual_count']}",
        f"- 结论阻断：{gate['conclusion_blocking_sentence_count']}",
        f"- 发布状态：`{gate['status']}`", "",
    ])


def _manifest(run_id, status, started_at, release, review, output_path, failure):
    return {
        "schema_version": FINAL_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3j",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "release_id": release["release_id"] if release else None,
        "review_id": review["review_id"] if review else None,
        "formal_review_published": review["formal_review_published"] if review else False,
        "quality_gate": review["quality_gate"] if review else None,
        "output_sha256": _sha256_bytes(output_path.read_bytes()) if output_path else None,
        "failure": failure,
    }


def _safe_segment(value):
    if not value or Path(value).name != value or value in {".", ".."}:
        raise ReviewB2FinalAssemblyError("final_b2.run_id_invalid", "run_id不是安全路径段。")


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewB2FinalAssemblyError(
            "final_b2.artifact_missing", f"{label}不存在：{path}"
        )
    return path.read_bytes()


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewB2FinalAssemblyError(
            "final_b2.json_invalid", f"{label}不是有效UTF-8 JSON。"
        ) from exc
    if not isinstance(value, dict):
        raise ReviewB2FinalAssemblyError(
            "final_b2.json_object_required", f"{label}必须是JSON对象。"
        )
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _sha256_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _now():
    return datetime.now(UTC).isoformat()
