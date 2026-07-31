from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .review_framework import load_review_framework_run
from .review_framework_b2_contracts import (
    REVIEW_FRAMEWORK_B2_RUN_SCHEMA_VERSION,
    ReviewFrameworkB2ContractError,
    derive_review_framework_b2,
    validate_review_framework_b2,
)


REVIEW_FRAMEWORK_B2_ROOT = "_review_frameworks_b2"


class ReviewFrameworkB2Error(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ReviewFrameworkB2RunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    framework: dict[str, Any]


def load_review_framework_b2_run(
    workspace: str | Path,
    run_id: str,
) -> ReviewFrameworkB2RunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id, "framework_b2_run_id")
    run_dir = workspace_path / REVIEW_FRAMEWORK_B2_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "Framework B2 manifest")
    manifest = _json_object(manifest_bytes, "Framework B2 manifest")
    if manifest.get("schema_version") != REVIEW_FRAMEWORK_B2_RUN_SCHEMA_VERSION:
        raise ReviewFrameworkB2Error(
            "framework_b2.run_schema_invalid",
            "Framework B2运行Schema版本不受支持。",
        )
    if manifest.get("run_id") != run_id or manifest.get("status") != "completed":
        raise ReviewFrameworkB2Error(
            "framework_b2.run_unavailable",
            "Framework B2运行身份或状态不可用。",
        )
    output_bytes = _read_bytes(
        run_dir / "output" / "review_framework_b2.json",
        "Framework B2正式输出",
    )
    framework = validate_review_framework_b2(
        _json_object(output_bytes, "Framework B2正式输出")
    )
    if (
        manifest.get("framework_id") != framework["framework_id"]
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewFrameworkB2Error(
            "framework_b2.output_identity_mismatch",
            "Framework B2输出身份或哈希不一致。",
        )
    source = load_review_framework_run(
        workspace_path,
        str(manifest["source_framework_run_id"]),
    )
    replay = derive_review_framework_b2(
        source.framework,
        source_run_id=source.run_id,
        source_manifest_sha256=source.manifest_sha256,
        target_total_chars=int(manifest["target_total_chars"]),
    )
    if replay != framework:
        raise ReviewFrameworkB2Error(
            "framework_b2.output_replay_mismatch",
            "Framework B2无法由冻结Framework v1重放。",
        )
    return ReviewFrameworkB2RunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        framework=framework,
    )


class ReviewFrameworkB2Builder:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def build(
        self,
        *,
        source_framework_run_id: str,
        target_total_chars: int = 8000,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved, "run_id")
        run_dir = self.workspace / REVIEW_FRAMEWORK_B2_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewFrameworkB2Error(
                "framework_b2.run_exists",
                f"Framework B2 run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        framework: dict[str, Any] | None = None
        try:
            source = load_review_framework_run(
                self.workspace,
                source_framework_run_id,
            )
            framework = derive_review_framework_b2(
                source.framework,
                source_run_id=source.run_id,
                source_manifest_sha256=source.manifest_sha256,
                target_total_chars=target_total_chars,
            )
            _write_json(run_dir / "input" / "review_framework_v1.json", source.framework)
            _write_json(
                run_dir / "input" / "projection_config.json",
                {
                    "target_total_chars": target_total_chars,
                    "projection_policy": "deterministic_defaults.v1",
                },
            )
            _write_json(
                run_dir / "output" / "review_framework_b2.json",
                framework,
            )
            _write_text(
                run_dir / "review" / "review_framework_b2.md",
                render_review_framework_b2(framework),
            )
            manifest = _manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                source_framework_run_id=source_framework_run_id,
                target_total_chars=target_total_chars,
                framework=framework,
                output_path=run_dir / "output" / "review_framework_b2.json",
                failure=None,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "stage": _failure_stage(run_dir),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(
                run_id=resolved,
                status="failed",
                started_at=started_at,
                source_framework_run_id=source_framework_run_id,
                target_total_chars=target_total_chars,
                framework=framework,
                output_path=None,
                failure=failure,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def render_review_framework_b2(framework: dict[str, Any]) -> str:
    lines = [
        "# B2综述框架审核",
        "",
        f"- 工作标题：{framework['working_title']}",
        f"- 目标总字数：{framework['target_total_chars']}",
        f"- 章节数：{len(framework['sections'])}",
        f"- 来源Framework：`{framework['source']['framework_run_id']}`",
        "",
        "## 章节预算",
        "",
    ]
    for section in framework["sections"]:
        budget = section["claim_budget"]
        selection = section["material_selection"]
        lines.extend(
            [
                f"### {section['section_index']}. {section['title']}",
                "",
                f"- 类型：`{section['section_type']}`",
                f"- 目标字数：{section['target_chars']}",
                f"- 最大段落数：{section['max_paragraphs']}",
                "- Claim预算："
                f"核心 {budget['core_max']}；"
                f"支持 {budget['supporting_max']}；"
                f"跨论文综合 {budget['cross_paper_synthesis_max']}",
                f"- 必须纳入论文：{len(selection['required_paper_ids'])}",
                f"- 排除论文：{len(selection['excluded_paper_ids'])}",
                f"- 强度上限：`{section['conclusion_strength_limit']}`",
                "",
            ]
        )
    return "\n".join(lines)


def _manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    source_framework_run_id: str,
    target_total_chars: int,
    framework: dict[str, Any] | None,
    output_path: Path | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": REVIEW_FRAMEWORK_B2_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase2",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "source_framework_run_id": source_framework_run_id,
        "source_framework_id": (
            framework["source"]["framework_id"] if framework else None
        ),
        "framework_id": framework["framework_id"] if framework else None,
        "target_total_chars": target_total_chars,
        "output_sha256": (
            _sha256_bytes(output_path.read_bytes())
            if output_path is not None
            else None
        ),
        "failure": failure,
        "artifacts": {
            "framework": (
                "output/review_framework_b2.json" if framework else None
            ),
            "report": (
                "review/review_framework_b2.md" if framework else None
            ),
            "failures": (
                "audit/failures.jsonl" if failure is not None else None
            ),
        },
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "output_path": (
            str(run_dir / manifest["artifacts"]["framework"])
            if manifest["artifacts"]["framework"]
            else None
        ),
        "report_path": (
            str(run_dir / manifest["artifacts"]["report"])
            if manifest["artifacts"]["report"]
            else None
        ),
        "framework_id": manifest["framework_id"],
        "failure": manifest["failure"],
    }


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "output" / "review_framework_b2.json").exists():
        return "publish"
    if (run_dir / "input" / "review_framework_v1.json").exists():
        return "derive"
    return "load_source"


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewFrameworkB2Error(
            "framework_b2.artifact_missing",
            f"{label}不存在：{path}",
        )
    return path.read_bytes()


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewFrameworkB2Error(
            "framework_b2.json_invalid",
            f"{label}不是有效UTF-8 JSON。",
        ) from exc
    if not isinstance(value, dict):
        raise ReviewFrameworkB2Error(
            "framework_b2.json_object_required",
            f"{label}必须是JSON对象。",
        )
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
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


def _safe_segment(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in {".", ".."}
        or any(char in value for char in ("/", "\\", "\x00"))
    ):
        raise ReviewFrameworkB2Error(
            "framework_b2.run_id_invalid",
            f"{field}不是安全路径段。",
        )
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
