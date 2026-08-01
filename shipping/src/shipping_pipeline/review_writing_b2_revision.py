from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .review_claim_revision_b2 import (
    ReviewClaimRevisionB2RunSource,
    load_review_claim_revision_b2_run,
)
from .review_writing_b2 import ReviewWritingB2RunSource
from .review_writing_b2_revision_contracts import (
    apply_revision_decision,
    load_revision_decision_set,
    validate_revision_decision_set,
)
from .review_writing_b2_revision_report import (
    render_writing_revision_report,
)


WRITING_REVISION_ROOT = "_review_writing_revisions_b2"
WRITING_REVISION_RUN_SCHEMA_VERSION = "llm.review_writing_b2_revision_run.v1"


class ReviewWritingB2RevisionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewWritingB2RevisionRunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    writing_input: dict[str, Any]
    chapter: dict[str, Any]
    package_source: Any
    suggestion_source: ReviewClaimRevisionB2RunSource
    decision_set: dict[str, Any]


def load_review_writing_b2_revision_run(
    workspace: str | Path,
    run_id: str,
    *,
    suggestion_loader: Callable[
        [str | Path, str], ReviewClaimRevisionB2RunSource
    ]
    | None = None,
) -> ReviewWritingB2RevisionRunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / WRITING_REVISION_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(
        run_dir / "manifest.json", "B2章节revision manifest"
    )
    manifest = _json_object(manifest_bytes, "B2章节revision manifest")
    if (
        manifest.get("schema_version") != WRITING_REVISION_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewWritingB2RevisionError(
            "writing_revision_b2.run_unavailable",
            "B2章节revision运行Schema、身份或状态不可用。",
        )
    load_suggestion = suggestion_loader or load_review_claim_revision_b2_run
    source = load_suggestion(
        workspace_path, str(manifest["revision_suggestion_run_id"])
    )
    decision_set = validate_revision_decision_set(
        _read_json(run_dir / "input" / "decision_set.json", "B2修订决策")
    )
    replay = apply_revision_decision(
        decision_set=decision_set,
        suggestion_run_id=source.run_id,
        suggestion_manifest_sha256=source.manifest_sha256,
        revision_input=source.revision_input,
        suggestions=source.revision_suggestions,
        source_chapter=source.adjudication_source.audit_source.writing_source.chapter,
        source_audit_input=source.adjudication_source.audit_source.audit_input,
    )
    output_bytes = _read_bytes(
        run_dir / "output" / "review_chapter_revision_b2.json",
        "B2修订章节输出",
    )
    chapter = _json_object(output_bytes, "B2修订章节输出")
    if (
        replay != chapter
        or manifest.get("chapter_id") != chapter.get("chapter_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
        or manifest.get("decision_set_id")
        != decision_set.get("decision_set_id")
    ):
        raise ReviewWritingB2RevisionError(
            "writing_revision_b2.output_replay_mismatch",
            "B2修订章节身份、决策、哈希或确定性重放不一致。",
        )
    writing_source = source.adjudication_source.audit_source.writing_source
    return ReviewWritingB2RevisionRunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        writing_input=writing_source.writing_input,
        chapter=chapter,
        package_source=writing_source.package_source,
        suggestion_source=source,
        decision_set=decision_set,
    )


def load_review_writing_b2_source(
    workspace: str | Path,
    run_id: str,
) -> ReviewWritingB2RunSource | ReviewWritingB2RevisionRunSource:
    workspace_path = Path(workspace)
    generation_manifest = (
        workspace_path / "_review_writings_b2" / "runs" / run_id / "manifest.json"
    )
    revision_manifest = (
        workspace_path / WRITING_REVISION_ROOT / "runs" / run_id / "manifest.json"
    )
    matches = [path for path in (generation_manifest, revision_manifest) if path.is_file()]
    if len(matches) != 1:
        raise ReviewWritingB2RevisionError(
            "writing_revision_b2.source_ambiguous",
            "写作来源必须在generation或revision运行中唯一存在。",
        )
    if matches[0] == generation_manifest:
        from .review_writing_b2 import load_review_writing_b2_run

        return load_review_writing_b2_run(workspace_path, run_id)
    return load_review_writing_b2_revision_run(workspace_path, run_id)


class ReviewWritingB2RevisionRunner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        suggestion_loader: Callable[
            [str | Path, str], ReviewClaimRevisionB2RunSource
        ]
        | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.suggestion_loader = (
            suggestion_loader or load_review_claim_revision_b2_run
        )

    def run(
        self,
        *,
        revision_suggestion_run_id: str,
        decision_file: str | Path,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / WRITING_REVISION_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewWritingB2RevisionError(
                "writing_revision_b2.run_exists",
                f"run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        source: ReviewClaimRevisionB2RunSource | None = None
        decision_set: dict[str, Any] | None = None
        chapter: dict[str, Any] | None = None
        try:
            source = self.suggestion_loader(
                self.workspace, revision_suggestion_run_id
            )
            decision_set = load_revision_decision_set(Path(decision_file))
            _write_json(run_dir / "input" / "decision_set.json", decision_set)
            chapter = apply_revision_decision(
                decision_set=decision_set,
                suggestion_run_id=source.run_id,
                suggestion_manifest_sha256=source.manifest_sha256,
                revision_input=source.revision_input,
                suggestions=source.revision_suggestions,
                source_chapter=source.adjudication_source.audit_source.writing_source.chapter,
                source_audit_input=source.adjudication_source.audit_source.audit_input,
            )
            output_path = (
                run_dir / "output" / "review_chapter_revision_b2.json"
            )
            _write_json(output_path, chapter)
            manifest = _manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                source=source,
                decision_set=decision_set,
                chapter=chapter,
                output_path=output_path,
                failure=None,
            )
            _write_text(
                run_dir / "review" / "review_chapter_revision_b2.md",
                render_writing_revision_report(manifest, chapter),
            )
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "stage": "apply_revision",
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(
                run_id=resolved,
                status="failed",
                started_at=started_at,
                source=source,
                decision_set=decision_set,
                chapter=chapter,
                output_path=None,
                failure=failure,
            )
        _write_json(run_dir / "manifest.json", manifest)
        return _result(run_dir, manifest)


def _manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    source: ReviewClaimRevisionB2RunSource | None,
    decision_set: dict[str, Any] | None,
    chapter: dict[str, Any] | None,
    output_path: Path | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": WRITING_REVISION_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3e",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "revision_suggestion_run_id": source.run_id if source else None,
        "revision_suggestion_manifest_sha256": (
            source.manifest_sha256 if source else None
        ),
        "source_writing_run_id": (
            source.revision_input["source"]["writing_run_id"]
            if source
            else None
        ),
        "source_chapter_id": (
            source.adjudication_source.audit_source.writing_source.chapter[
                "chapter_id"
            ]
            if source
            else None
        ),
        "decision_set_id": (
            decision_set["decision_set_id"] if decision_set else None
        ),
        "chapter_id": chapter["chapter_id"] if chapter else None,
        "section_index": chapter["section_index"] if chapter else None,
        "summary": chapter["revision_audit"] if chapter else None,
        "output_sha256": (
            _sha256_bytes(output_path.read_bytes()) if output_path else None
        ),
        "failure": failure,
        "artifacts": {
            "decision_set": (
                "input/decision_set.json" if decision_set else None
            ),
            "chapter": (
                "output/review_chapter_revision_b2.json"
                if output_path
                else None
            ),
            "report": (
                "review/review_chapter_revision_b2.md"
                if output_path
                else None
            ),
            "failures": "audit/failures.jsonl" if failure else None,
        },
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "chapter_path": (
            str(run_dir / manifest["artifacts"]["chapter"])
            if manifest["artifacts"]["chapter"]
            else None
        ),
        "report_path": (
            str(run_dir / manifest["artifacts"]["report"])
            if manifest["artifacts"]["report"]
            else None
        ),
        "chapter_id": manifest["chapter_id"],
        "section_index": manifest["section_index"],
        "decision_set_id": manifest["decision_set_id"],
        "summary": manifest["summary"],
        "failure": manifest["failure"],
    }


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewWritingB2RevisionError(
            "writing_revision_b2.artifact_missing", f"{label}不存在：{path}"
        )
    return path.read_bytes()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewWritingB2RevisionError(
            "writing_revision_b2.json_invalid", f"{label}不是有效UTF-8 JSON。"
        ) from exc
    if not isinstance(value, dict):
        raise ReviewWritingB2RevisionError(
            "writing_revision_b2.json_object_required", f"{label}必须是JSON对象。"
        )
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ).encode("utf-8")
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((value.rstrip() + "\n").encode("utf-8"))


def _safe_segment(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in {".", ".."}
        or any(char in value for char in ("/", "\\", "\x00"))
    ):
        raise ReviewWritingB2RevisionError(
            "writing_revision_b2.run_id_invalid", "run_id不是安全路径段。"
        )
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat()
