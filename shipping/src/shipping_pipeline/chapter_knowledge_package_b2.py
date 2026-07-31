from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .chapter_knowledge_package import DEFAULT_REVIEW_WRITING_CONFIG
from .llm_analysis import DEFAULT_MODEL_PROFILE, DEFAULT_TOKENIZER_CACHE
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_writing_contracts import (
    ReviewWritingConfig,
    load_review_writing_config,
)
from .source_window_contracts import stable_id
from .source_window_selection import (
    SourceWindowRunSource,
    load_source_window_run,
)


B2_PREVIEW_PACKAGE_SCHEMA_VERSION = (
    "llm.chapter_knowledge_package_b2_preview.v1"
)
B2_PREVIEW_PACKAGE_RUN_SCHEMA_VERSION = (
    "llm.chapter_knowledge_package_b2_preview_run.v1"
)
B2_PREVIEW_PACKAGE_ROOT = "_chapter_knowledge_packages_b2"
B2_PREVIEW_SYSTEM_PROMPT = (
    "这是B2 Phase 1来源上下文预算预览，不执行正文写作。"
    "输入仅用于计算精简材料的真实Token，不得生成综述。"
)


class B2ChapterKnowledgePackageError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class B2PreviewPackageSnapshot:
    source: SourceWindowRunSource
    package: dict[str, Any]
    input_sha256: str


@dataclass(frozen=True)
class B2PreviewPackageRunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    package: dict[str, Any]
    token_budget: dict[str, Any]
    profile: ModelProfile
    config: ReviewWritingConfig


def load_b2_preview_package_run(
    workspace: str | Path,
    run_id: str,
    *,
    source_loader: Callable[[str | Path, str], SourceWindowRunSource]
    | None = None,
) -> B2PreviewPackageRunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id, "package_run_id")
    run_dir = workspace_path / B2_PREVIEW_PACKAGE_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(
        run_dir / "manifest.json",
        "B2 preview package manifest",
    )
    manifest = _json_object(
        manifest_bytes,
        "B2 preview package manifest",
    )
    if (
        manifest.get("schema_version")
        != B2_PREVIEW_PACKAGE_RUN_SCHEMA_VERSION
    ):
        raise B2ChapterKnowledgePackageError(
            "package_b2.run_schema_invalid",
            "B2 preview package运行Schema版本不受支持。",
        )
    if manifest.get("run_id") != run_id:
        raise B2ChapterKnowledgePackageError(
            "package_b2.run_identity_mismatch",
            "运行目录与manifest.run_id不一致。",
        )
    if manifest.get("status") != "completed":
        raise B2ChapterKnowledgePackageError(
            "package_b2.run_status_invalid",
            f"B2 preview package状态不可用：{manifest.get('status')!r}。",
        )
    output_bytes = _read_bytes(
        run_dir / "output" / "knowledge_package_b2_preview.json",
        "B2 preview package正式输出",
    )
    package = _validate_package(
        _json_object(output_bytes, "B2 preview package正式输出")
    )
    if (
        manifest.get("package_id") != package["package_id"]
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise B2ChapterKnowledgePackageError(
            "package_b2.output_identity_mismatch",
            "B2 preview package输出身份或文件哈希不一致。",
        )
    loader = source_loader or load_source_window_run
    source = loader(
        workspace_path,
        str(manifest["source_window_run_id"]),
    )
    replay = _build_package_snapshot(source)
    if replay.package != package:
        raise B2ChapterKnowledgePackageError(
            "package_b2.output_replay_mismatch",
            "B2 preview package无法由Source Window运行重放。",
        )
    profile = load_model_profile(run_dir / "input" / "model_profile.json")
    config = load_review_writing_config(
        run_dir / "input" / "writing_config.json"
    )
    budget = _read_json(
        run_dir / "audit" / "token_budget.json",
        "B2 preview package Token预算",
    )
    if (
        budget.get("model_profile_sha256") != profile.sha256
        or budget.get("planned_max_output_tokens")
        != config.chapter_max_output_tokens
        or budget.get("system_prompt_sha256")
        != _sha256_text(B2_PREVIEW_SYSTEM_PROMPT)
        or not budget.get("within_budget")
    ):
        raise B2ChapterKnowledgePackageError(
            "package_b2.token_budget_identity_mismatch",
            "Token预算与冻结模型、配置或Prompt不一致。",
        )
    _verify_file_hash_ledger(run_dir)
    return B2PreviewPackageRunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        package=package,
        token_budget=budget,
        profile=profile,
        config=config,
    )


class B2ChapterKnowledgePackageBuilder:
    def __init__(
        self,
        workspace: str | Path,
        token_counter: Any | None = None,
        source_loader: Callable[
            [str | Path, str], SourceWindowRunSource
        ]
        | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.token_counter = token_counter
        self.source_loader = source_loader or load_source_window_run

    def build(
        self,
        *,
        source_window_run_id: str,
        run_id: str | None = None,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        writing_config_path: str | Path = DEFAULT_REVIEW_WRITING_CONFIG,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved, "run_id")
        run_dir = (
            self.workspace / B2_PREVIEW_PACKAGE_ROOT / "runs" / resolved
        )
        if run_dir.exists():
            raise B2ChapterKnowledgePackageError(
                "package_b2.run_exists",
                f"B2 preview package run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot: B2PreviewPackageSnapshot | None = None
        profile: ModelProfile | None = None
        config: ReviewWritingConfig | None = None
        budget: dict[str, Any] | None = None
        try:
            source = self.source_loader(
                self.workspace,
                source_window_run_id,
            )
            snapshot = _build_package_snapshot(source)
            profile = load_model_profile(Path(model_profile_path))
            config = load_review_writing_config(Path(writing_config_path))
            _write_input_artifacts(
                run_dir,
                snapshot=snapshot,
                profile=profile,
                config=config,
            )
            token_counter = self.token_counter or DeepSeekV4TokenCounter(
                profile,
                DEFAULT_TOKENIZER_CACHE,
            )
            budget = _build_token_budget(
                snapshot.package,
                profile=profile,
                config=config,
                token_counter=token_counter,
            )
            _write_json(run_dir / "audit" / "token_budget.json", budget)
            if not budget["within_budget"]:
                raise B2ChapterKnowledgePackageError(
                    "package_b2.context_budget_exceeded",
                    "B2 preview来源上下文超过模型窗口："
                    f"input={budget['planned_input_tokens']}, "
                    f"output={budget['planned_max_output_tokens']}, "
                    f"margin={budget['safety_margin_tokens']}, "
                    f"window={budget['context_window_tokens']}。",
                )
            _write_json(
                run_dir / "output" / "knowledge_package_b2_preview.json",
                snapshot.package,
            )
            from .chapter_knowledge_package_b2_report import (
                render_b2_preview_package_report,
            )

            _write_text(
                run_dir / "review" / "knowledge_package_b2_preview.md",
                render_b2_preview_package_report(
                    snapshot.package,
                    budget,
                ),
            )
            manifest = _build_manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                source_window_run_id=source_window_run_id,
                snapshot=snapshot,
                profile=profile,
                config=config,
                budget=budget,
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
            manifest = _build_manifest(
                run_id=resolved,
                status="failed",
                started_at=started_at,
                source_window_run_id=source_window_run_id,
                snapshot=snapshot,
                profile=profile,
                config=config,
                budget=budget,
                failure=failure,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def build_b2_preview_prompt(package: dict[str, Any]) -> str:
    return json.dumps(
        {
            "阶段": "B2 Phase 1来源上下文预算预览",
            "writing_ready": False,
            "说明": (
                "Claim Ledger尚未生成；本输入不得用于综述写作，"
                "仅用于计算Source Window和认知投影的Token。"
            ),
            "preview_package": package,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _build_package_snapshot(
    source: SourceWindowRunSource,
) -> B2PreviewPackageSnapshot:
    selection = source.selection
    composition = _context_composition(selection)
    package_without_id = {
        "schema_version": B2_PREVIEW_PACKAGE_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase1",
        "writing_ready": False,
        "source": {
            "source_window_run_id": source.run_id,
            "selection_id": selection["selection_id"],
            "source_window_manifest_sha256": source.manifest_sha256,
            "source_window_output_sha256": source.output_sha256,
            **copy.deepcopy(selection["source"]),
        },
        "section_task": copy.deepcopy(selection["section"]),
        "material_tier": selection["material_tier"],
        "understanding_projections": copy.deepcopy(
            selection["understanding_projections"]
        ),
        "selected_source_windows": copy.deepcopy(
            selection["selected_source_windows"]
        ),
        "expanded_markdown_sources": copy.deepcopy(
            selection["expanded_markdown_sources"]
        ),
        "citation_metadata": copy.deepcopy(
            selection["citation_metadata"]
        ),
        "coverage": copy.deepcopy(selection["coverage"]),
        "context_composition": composition,
    }
    package = {
        **package_without_id,
        "package_id": stable_id("package_b2_preview", package_without_id),
    }
    return B2PreviewPackageSnapshot(
        source=source,
        package=_validate_package(package),
        input_sha256=_sha256_json(package_without_id),
    )


def _validate_package(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise B2ChapterKnowledgePackageError(
            "package_b2.invalid",
            "B2 preview package必须是JSON对象。",
        )
    required = {
        "schema_version",
        "pipeline_generation",
        "writing_ready",
        "source",
        "section_task",
        "material_tier",
        "understanding_projections",
        "selected_source_windows",
        "expanded_markdown_sources",
        "citation_metadata",
        "coverage",
        "context_composition",
        "package_id",
    }
    if set(payload) != required:
        raise B2ChapterKnowledgePackageError(
            "package_b2.fields_invalid",
            "B2 preview package字段不匹配。",
        )
    if (
        payload["schema_version"] != B2_PREVIEW_PACKAGE_SCHEMA_VERSION
        or payload["pipeline_generation"] != "B2.phase1"
        or payload["writing_ready"] is not False
    ):
        raise B2ChapterKnowledgePackageError(
            "package_b2.generation_invalid",
            "B2 preview package代际或writing_ready状态无效。",
        )
    if payload["material_tier"] == "tier_1":
        if payload["expanded_markdown_sources"]:
            raise B2ChapterKnowledgePackageError(
                "package_b2.tier_1_markdown_forbidden",
                "Tier 1不得包含完整Markdown。",
            )
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in ('"full_markdown"', '"cards"', '"evidence_units"'):
            if forbidden in serialized:
                raise B2ChapterKnowledgePackageError(
                    "package_b2.full_source_field_forbidden",
                    f"Tier 1不得包含B1全量字段：{forbidden}。",
                )
    expected = stable_id(
        "package_b2_preview",
        {key: value for key, value in payload.items() if key != "package_id"},
    )
    if payload["package_id"] != expected:
        raise B2ChapterKnowledgePackageError(
            "package_b2.package_id_mismatch",
            "B2 preview package ID无法重建。",
        )
    return copy.deepcopy(payload)


def _context_composition(selection: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "section_task": selection["section"],
        "understanding_projections": selection[
            "understanding_projections"
        ],
        "selected_source_windows": selection["selected_source_windows"],
        "expanded_markdown_sources": selection[
            "expanded_markdown_sources"
        ],
        "citation_metadata": selection["citation_metadata"],
    }
    return {
        "schema_version": "llm.b2_context_composition.v1",
        "paper_count": selection["coverage"]["paper_count"],
        "window_count": selection["coverage"]["window_count"],
        "expanded_markdown_count": len(
            selection["expanded_markdown_sources"]
        ),
        "component_serialized_chars": {
            key: len(_compact_json(value))
            for key, value in fields.items()
        },
        "total_serialized_chars": sum(
            len(_compact_json(value)) for value in fields.values()
        ),
    }


def _build_token_budget(
    package: dict[str, Any],
    *,
    profile: ModelProfile,
    config: ReviewWritingConfig,
    token_counter: Any,
) -> dict[str, Any]:
    if config.chapter_max_output_tokens > profile.model_max_output_tokens:
        raise B2ChapterKnowledgePackageError(
            "package_b2.output_budget_exceeded",
            "章节输出预算超过模型上限。",
        )
    prompt = build_b2_preview_prompt(package)
    messages = [
        {"role": "system", "content": B2_PREVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    count = token_counter.count_messages(messages)
    components = {
        "section_task_tokens": token_counter.count_text(
            _compact_json(package["section_task"])
        ),
        "understanding_tokens": token_counter.count_text(
            _compact_json(package["understanding_projections"])
        ),
        "source_window_tokens": token_counter.count_text(
            _compact_json(package["selected_source_windows"])
        ),
        "expanded_markdown_tokens": token_counter.count_text(
            _compact_json(package["expanded_markdown_sources"])
        ),
        "citation_metadata_tokens": token_counter.count_text(
            _compact_json(package["citation_metadata"])
        ),
    }
    reserved = (
        count.prompt_tokens
        + config.chapter_max_output_tokens
        + profile.safety_margin_tokens
    )
    return {
        "schema_version": "llm.chapter_token_budget_b2_preview.v1",
        "model_profile_id": profile.profile_id,
        "model_profile_sha256": profile.sha256,
        "planned_input_tokens": count.prompt_tokens,
        "planned_max_output_tokens": config.chapter_max_output_tokens,
        "safety_margin_tokens": profile.safety_margin_tokens,
        "context_window_tokens": profile.context_window_tokens,
        "total_reserved_tokens": reserved,
        "overflow_tokens": max(
            0,
            reserved - profile.context_window_tokens,
        ),
        "within_budget": reserved <= profile.context_window_tokens,
        "encoded_prompt_sha256": count.encoded_prompt_sha256,
        "system_prompt_sha256": _sha256_text(B2_PREVIEW_SYSTEM_PROMPT),
        "component_tokens": components,
        "component_token_sum": sum(components.values()),
        "claim_ledger_included": False,
        "writing_ready": False,
    }


def _write_input_artifacts(
    run_dir: Path,
    *,
    snapshot: B2PreviewPackageSnapshot,
    profile: ModelProfile,
    config: ReviewWritingConfig,
) -> None:
    ledger: list[dict[str, Any]] = []

    def write(relative_path: str, data: bytes) -> None:
        path = run_dir / "input" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        ledger.append(
            {
                "path": relative_path.replace("\\", "/"),
                "sha256": _sha256_bytes(data),
                "bytes": len(data),
            }
        )

    write("source_window_selection.json", _json_bytes(snapshot.source.selection))
    write("package_candidate.json", _json_bytes(snapshot.package))
    write("model_profile.json", _json_bytes(profile.to_dict()))
    write("writing_config.json", _json_bytes(config.to_dict()))
    _write_jsonl(run_dir / "input" / "file_hashes.jsonl", ledger)


def _build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    source_window_run_id: str,
    snapshot: B2PreviewPackageSnapshot | None,
    profile: ModelProfile | None,
    config: ReviewWritingConfig | None,
    budget: dict[str, Any] | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    package = snapshot.package if snapshot is not None else None
    section = package["section_task"] if package is not None else None
    return {
        "schema_version": B2_PREVIEW_PACKAGE_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase1",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "source_window_run_id": source_window_run_id,
        "selection_id": (
            snapshot.source.selection["selection_id"]
            if snapshot is not None
            else None
        ),
        "framework_run_id": (
            package["source"]["framework_run_id"]
            if package is not None
            else None
        ),
        "section_id": section["section_id"] if section is not None else None,
        "section_index": (
            section["section_index"] if section is not None else None
        ),
        "material_tier": (
            package["material_tier"] if package is not None else None
        ),
        "package_id": (
            package["package_id"] if package is not None else None
        ),
        "input_sha256": (
            snapshot.input_sha256 if snapshot is not None else None
        ),
        "output_sha256": (
            _sha256_bytes(_json_bytes(package))
            if status == "completed" and package is not None
            else None
        ),
        "model_profile_id": (
            profile.profile_id if profile is not None else None
        ),
        "model_profile_sha256": (
            profile.sha256 if profile is not None else None
        ),
        "writing_config": (
            config.to_dict() if config is not None else None
        ),
        "token_budget": copy.deepcopy(budget),
        "writing_ready": False,
        "failure": failure,
        "artifacts": {
            "package": (
                "output/knowledge_package_b2_preview.json"
                if status == "completed"
                else None
            ),
            "token_budget": (
                "audit/token_budget.json" if budget is not None else None
            ),
            "file_hashes": (
                "input/file_hashes.jsonl"
                if snapshot is not None
                else None
            ),
            "report": (
                "review/knowledge_package_b2_preview.md"
                if status == "completed"
                else None
            ),
            "failures": (
                "audit/failures.jsonl" if failure is not None else None
            ),
        },
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    budget = manifest.get("token_budget") or {}
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "output_path": (
            str(run_dir / "output" / "knowledge_package_b2_preview.json")
            if manifest["status"] == "completed"
            else None
        ),
        "report_path": (
            str(run_dir / "review" / "knowledge_package_b2_preview.md")
            if manifest["status"] == "completed"
            else None
        ),
        "package_id": manifest.get("package_id"),
        "planned_input_tokens": budget.get("planned_input_tokens"),
        "token_budget": budget or None,
        "writing_ready": False,
        "failure": manifest.get("failure"),
    }


def _verify_file_hash_ledger(run_dir: Path) -> None:
    rows = _read_jsonl(
        run_dir / "input" / "file_hashes.jsonl",
        "B2 preview package输入哈希账本",
    )
    for row in rows:
        relative = _required_text(row.get("path"), "file_hash.path")
        raw = _read_bytes(
            run_dir / "input" / Path(relative),
            f"冻结输入{relative}",
        )
        if (
            row.get("bytes") != len(raw)
            or row.get("sha256") != _sha256_bytes(raw)
        ):
            raise B2ChapterKnowledgePackageError(
                "package_b2.file_hash_mismatch",
                f"冻结输入哈希不一致：{relative}。",
            )


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "audit" / "token_budget.json").is_file():
        return "token_budget"
    if (run_dir / "input" / "file_hashes.jsonl").is_file():
        return "package_validation"
    return "input_validation"


def _safe_segment(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise B2ChapterKnowledgePackageError(
            "package_b2.run_id_invalid",
            f"{field}不是安全目录名。",
        )


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise B2ChapterKnowledgePackageError(
            "package_b2.text_required",
            f"{field}必须是非空字符串。",
        )
    return value.strip()


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise B2ChapterKnowledgePackageError(
            "package_b2.input_read_failed",
            f"无法读取{label}：{path}",
        ) from exc


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise B2ChapterKnowledgePackageError(
            "package_b2.json_invalid",
            f"{label}不是有效JSON。",
        ) from exc
    if not isinstance(value, dict):
        raise B2ChapterKnowledgePackageError(
            "package_b2.json_object_required",
            f"{label}必须是JSON对象。",
        )
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    raw = _read_bytes(path, label)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise B2ChapterKnowledgePackageError(
            "package_b2.jsonl_encoding_invalid",
            f"{label}不是UTF-8。",
        ) from exc
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise B2ChapterKnowledgePackageError(
                "package_b2.jsonl_invalid",
                f"{label}第{index + 1}行不是有效JSON。",
            ) from exc
        if not isinstance(row, dict):
            raise B2ChapterKnowledgePackageError(
                "package_b2.jsonl_row_invalid",
                f"{label}第{index + 1}行不是对象。",
            )
        rows.append(row)
    return rows


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(payload))


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows
        ).encode("utf-8")
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.encode("utf-8"))


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _compact_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_json(value: object) -> str:
    return _sha256_text(_compact_json(value))


def _now() -> str:
    return datetime.now(UTC).isoformat()
