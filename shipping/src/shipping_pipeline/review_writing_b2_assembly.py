from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .review_writing_b2 import (
    ReviewWritingB2RunSource,
    load_review_writing_b2_run,
)
from .source_window_contracts import stable_id


ASSEMBLY_ROOT = "_review_drafts_b2_phase3a"
ASSEMBLY_SCHEMA_VERSION = "llm.review_draft_b2_phase3a.v1"
ASSEMBLY_RUN_SCHEMA_VERSION = "llm.review_draft_b2_phase3a_run.v1"


class ReviewWritingB2AssemblyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewWritingB2AssemblySource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    draft: dict[str, Any]


def load_review_writing_b2_assembly(
    workspace: str | Path,
    run_id: str,
    *,
    chapter_loader: Callable[
        [str | Path, str], ReviewWritingB2RunSource
    ]
    | None = None,
) -> ReviewWritingB2AssemblySource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / ASSEMBLY_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "Phase 3A manifest")
    manifest = _json_object(manifest_bytes, "Phase 3A manifest")
    if (
        manifest.get("schema_version") != ASSEMBLY_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewWritingB2AssemblyError(
            "writing_b2_assembly.run_unavailable",
            "Phase 3A装配运行Schema、身份或状态不可用。",
        )
    load_chapter = chapter_loader or load_review_writing_b2_run
    sources = [
        load_chapter(workspace_path, value)
        for value in manifest["chapter_run_ids"]
    ]
    replay = build_phase3a_draft(sources)
    output_bytes = _read_bytes(
        run_dir / "output" / "review_draft_phase3a.json",
        "Phase 3A正式输出",
    )
    draft = _json_object(output_bytes, "Phase 3A正式输出")
    if (
        replay != draft
        or manifest.get("draft_id") != draft.get("draft_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewWritingB2AssemblyError(
            "writing_b2_assembly.output_replay_mismatch",
            "Phase 3A草稿身份、哈希或确定性重放不一致。",
        )
    return ReviewWritingB2AssemblySource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        draft=draft,
    )


class ReviewWritingB2Assembler:
    def __init__(
        self,
        workspace: str | Path,
        *,
        chapter_loader: Callable[
            [str | Path, str], ReviewWritingB2RunSource
        ]
        | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.chapter_loader = chapter_loader or load_review_writing_b2_run

    def build(
        self,
        *,
        chapter_run_ids: list[str],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        if not chapter_run_ids or len(chapter_run_ids) != len(
            set(chapter_run_ids)
        ):
            raise ReviewWritingB2AssemblyError(
                "writing_b2_assembly.chapter_runs_invalid",
                "chapter_run_ids必须是非空且不重复的数组。",
            )
        run_dir = self.workspace / ASSEMBLY_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewWritingB2AssemblyError(
                "writing_b2_assembly.run_exists",
                f"run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        draft: dict[str, Any] | None = None
        try:
            sources = [
                self.chapter_loader(self.workspace, value)
                for value in chapter_run_ids
            ]
            draft = build_phase3a_draft(sources)
            _write_json(
                run_dir / "input" / "chapter_runs.json",
                {
                    "chapter_run_ids": chapter_run_ids,
                    "chapter_ids": [row.chapter["chapter_id"] for row in sources],
                },
            )
            _write_json(
                run_dir / "output" / "review_draft_phase3a.json",
                draft,
            )
            _write_text(
                run_dir / "review" / "review_draft_phase3a.md",
                draft["markdown"],
            )
            manifest = _manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                chapter_run_ids=chapter_run_ids,
                draft=draft,
                output_path=run_dir / "output" / "review_draft_phase3a.json",
                failure=None,
            )
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(
                run_id=resolved,
                status="failed",
                started_at=started_at,
                chapter_run_ids=chapter_run_ids,
                draft=None,
                output_path=None,
                failure=failure,
            )
        _write_json(run_dir / "manifest.json", manifest)
        return _result(run_dir, manifest)


def build_phase3a_draft(
    sources: list[ReviewWritingB2RunSource],
) -> dict[str, Any]:
    if not sources:
        raise ReviewWritingB2AssemblyError(
            "writing_b2_assembly.chapters_empty",
            "Phase 3A没有可装配章节。",
        )
    first = sources[0].writing_input
    framework = first["framework_outline"]
    framework_ids = {
        row.writing_input["framework_outline"]["framework_b2_id"]
        for row in sources
    }
    if framework_ids != {framework["framework_b2_id"]}:
        raise ReviewWritingB2AssemblyError(
            "writing_b2_assembly.framework_mixed",
            "章节来自多个Framework B2。",
        )
    expected_sections = [
        row
        for row in framework["sections"]
        if row["section_type"] != "conclusion"
    ]
    actual_ids = [row.chapter["section_id"] for row in sources]
    expected_ids = [row["section_id"] for row in expected_sections]
    if actual_ids != expected_ids:
        raise ReviewWritingB2AssemblyError(
            "writing_b2_assembly.section_set_invalid",
            "必须按Framework顺序提供全部引言和正文章节。",
        )
    if any(
        row.chapter["implementation_audit"][
            "semantic_unplanned_claim_audit"
        ]
        != "not_run"
        for row in sources
    ):
        raise ReviewWritingB2AssemblyError(
            "writing_b2_assembly.audit_state_invalid",
            "Phase 3A只接受尚待统一语义审计的章节。",
        )
    citation_by_key: dict[str, dict[str, Any]] = {}
    for source in sources:
        for citation in source.writing_input["citation_metadata"]:
            key = citation["citation_key"]
            if key in citation_by_key and citation_by_key[key] != citation:
                raise ReviewWritingB2AssemblyError(
                    "writing_b2_assembly.citation_conflict",
                    f"citation key题录冲突：{key}",
                )
            citation_by_key[key] = copy.deepcopy(citation)
    chapters = [copy.deepcopy(row.chapter) for row in sources]
    markdown = _assemble_markdown(framework, chapters)
    without_id = {
        "schema_version": ASSEMBLY_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3a",
        "formal_review_published": False,
        "framework_b2_id": framework["framework_b2_id"],
        "working_title": framework["working_title"],
        "central_question": framework["central_question"],
        "chapter_run_ids": [row.run_id for row in sources],
        "chapter_ids": [row.chapter["chapter_id"] for row in sources],
        "chapters": chapters,
        "citation_metadata": [
            citation_by_key[key] for key in sorted(citation_by_key)
        ],
        "conclusion": {
            "status": "not_ready_requires_audited_core_claims",
            "chapter_id": None,
        },
        "semantic_claim_audit": {
            "status": "not_run_required",
            "formal_release_blocked": True,
        },
        "markdown": markdown,
    }
    return {**without_id, "draft_id": stable_id("review_b2_phase3a", without_id)}


def _assemble_markdown(
    framework: dict[str, Any],
    chapters: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {framework['working_title']}",
        "",
        "## 资料范围",
        "",
        (
            "本文仅基于本次显式纳入的样本文献展开。"
            "当前文本为B2 Phase 3A草稿，尚未完成计划外Claim审计和结论生成。"
        ),
        "",
    ]
    for chapter in chapters:
        lines.extend(
            [
                f"## {chapter['section_index']}. {chapter['title']}",
                "",
            ]
        )
        for paragraph in chapter["paragraphs"]:
            text = paragraph["text"].strip()
            if paragraph["citation_keys"]:
                text += " [" + "; ".join(
                    f"@{key}" for key in paragraph["citation_keys"]
                ) + "]"
            lines.extend([text, ""])
    lines.extend(
        [
            "## 结论",
            "",
            "[尚未生成：需等待前文章节完成Claim审计。]",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    chapter_run_ids: list[str],
    draft: dict[str, Any] | None,
    output_path: Path | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": ASSEMBLY_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3a",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "chapter_run_ids": chapter_run_ids,
        "draft_id": draft["draft_id"] if draft else None,
        "formal_review_published": False,
        "conclusion_status": "not_ready_requires_audited_core_claims",
        "semantic_claim_audit": "not_run_required",
        "output_sha256": (
            _sha256_bytes(output_path.read_bytes()) if output_path else None
        ),
        "failure": failure,
        "artifacts": {
            "draft": (
                "output/review_draft_phase3a.json" if output_path else None
            ),
            "markdown": (
                "review/review_draft_phase3a.md" if output_path else None
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
        "output_path": (
            str(run_dir / manifest["artifacts"]["draft"])
            if manifest["artifacts"]["draft"]
            else None
        ),
        "review_path": (
            str(run_dir / manifest["artifacts"]["markdown"])
            if manifest["artifacts"]["markdown"]
            else None
        ),
        "draft_id": manifest["draft_id"],
        "formal_review_published": False,
        "failure": manifest["failure"],
    }


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewWritingB2AssemblyError(
            "writing_b2_assembly.artifact_missing",
            f"{label}不存在：{path}",
        )
    return path.read_bytes()


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewWritingB2AssemblyError(
            "writing_b2_assembly.json_invalid",
            f"{label}不是有效JSON。",
        ) from exc
    if not isinstance(value, dict):
        raise ReviewWritingB2AssemblyError(
            "writing_b2_assembly.json_object_required",
            f"{label}必须是JSON对象。",
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
        raise ReviewWritingB2AssemblyError(
            "writing_b2_assembly.run_id_invalid",
            "run_id不是安全路径段。",
        )
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat()
