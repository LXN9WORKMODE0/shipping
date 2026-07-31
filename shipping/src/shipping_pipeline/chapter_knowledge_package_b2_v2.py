from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .chapter_knowledge_package import DEFAULT_REVIEW_WRITING_CONFIG
from .claim_ledger import ClaimLedgerRunSource, load_claim_ledger_run
from .llm_analysis import DEFAULT_MODEL_PROFILE, DEFAULT_TOKENIZER_CACHE
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_writing_contracts import load_review_writing_config
from .source_window_contracts import stable_id
from .source_window_selection import SourceWindowRunSource, load_source_window_run


PACKAGE_SCHEMA_VERSION = "llm.chapter_knowledge_package.v2"
PACKAGE_RUN_SCHEMA_VERSION = "llm.chapter_knowledge_package_b2_run.v1"
PACKAGE_ROOT = "_chapter_knowledge_packages_b2_v2"
SYSTEM_PROMPT = "这是B2 Phase 2受约束写作包预算检查，不执行正文写作。"


class B2PackageV2Error(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class B2PackageV2RunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    package: dict[str, Any]
    token_budget: dict[str, Any]


def load_b2_package_v2_run(
    workspace: str | Path,
    run_id: str,
) -> B2PackageV2RunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / PACKAGE_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "B2 v2 package manifest")
    manifest = _json_object(manifest_bytes, "B2 v2 package manifest")
    if (
        manifest.get("schema_version") != PACKAGE_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise B2PackageV2Error("package_b2_v2.run_unavailable", "运行Schema、身份或状态不可用。")
    output_bytes = _read_bytes(
        run_dir / "output" / "knowledge_package.json",
        "B2 v2 package正式输出",
    )
    package = _validate_package(_json_object(output_bytes, "B2 v2 package正式输出"))
    if manifest.get("output_sha256") != _sha256_bytes(output_bytes):
        raise B2PackageV2Error("package_b2_v2.output_hash_mismatch", "正式输出哈希不一致。")
    source = load_source_window_run(workspace_path, manifest["source_window_run_id"])
    ledger = load_claim_ledger_run(workspace_path, manifest["claim_ledger_run_id"])
    replay = _build_package(source, ledger)
    if replay != package:
        raise B2PackageV2Error("package_b2_v2.replay_mismatch", "正式包无法由冻结来源重放。")
    budget = _read_json(run_dir / "audit" / "token_budget.json", "B2 v2 Token预算")
    profile = load_model_profile(run_dir / "input" / "model_profile.json")
    config = load_review_writing_config(
        run_dir / "input" / "writing_config.json"
    )
    counter = DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
    replay_budget = _token_budget(
        package,
        profile=profile,
        config=config,
        counter=counter,
    )
    if replay_budget != budget:
        raise B2PackageV2Error(
            "package_b2_v2.token_budget_replay_mismatch",
            "Token预算无法由冻结模型配置和知识包重放。",
        )
    return B2PackageV2RunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        package=package,
        token_budget=budget,
    )


class B2PackageV2Builder:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def build(
        self,
        *,
        source_window_run_id: str,
        claim_ledger_run_id: str,
        run_id: str | None = None,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        writing_config_path: str | Path = DEFAULT_REVIEW_WRITING_CONFIG,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / PACKAGE_ROOT / "runs" / resolved
        if run_dir.exists():
            raise B2PackageV2Error("package_b2_v2.run_exists", f"run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        package: dict[str, Any] | None = None
        budget: dict[str, Any] | None = None
        try:
            source = load_source_window_run(self.workspace, source_window_run_id)
            ledger = load_claim_ledger_run(self.workspace, claim_ledger_run_id)
            package = _build_package(source, ledger)
            profile = load_model_profile(Path(model_profile_path))
            config = load_review_writing_config(Path(writing_config_path))
            counter = DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
            budget = _token_budget(package, profile=profile, config=config, counter=counter)
            if not budget["within_budget"]:
                raise B2PackageV2Error("package_b2_v2.token_overflow", "知识包超过模型上下文预算。")
            _write_json(run_dir / "input" / "source_window_selection.json", source.selection)
            _write_json(run_dir / "input" / "claim_ledger.json", ledger.ledger)
            _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
            _write_json(run_dir / "input" / "writing_config.json", config.to_dict())
            _write_json(run_dir / "output" / "knowledge_package.json", package)
            _write_json(run_dir / "audit" / "token_budget.json", budget)
            _write_text(run_dir / "review" / "knowledge_package.md", _report(package, budget))
            manifest = _manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                source_window_run_id=source_window_run_id,
                claim_ledger_run_id=claim_ledger_run_id,
                package=package,
                budget=budget,
                output_path=run_dir / "output" / "knowledge_package.json",
                failure=None,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)
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
                source_window_run_id=source_window_run_id,
                claim_ledger_run_id=claim_ledger_run_id,
                package=None,
                budget=budget,
                output_path=None,
                failure=failure,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def _build_package(
    source: SourceWindowRunSource,
    ledger: ClaimLedgerRunSource,
) -> dict[str, Any]:
    selection = source.selection
    if (
        ledger.ledger["source_window_selection_id"] != selection["selection_id"]
        or ledger.ledger["section_id"] != selection["section"]["section_id"]
    ):
        raise B2PackageV2Error("package_b2_v2.source_mismatch", "Ledger与Source Window来源不一致。")
    section = next(
        row
        for row in ledger.framework_b2["sections"]
        if row["section_id"] == ledger.ledger["section_id"]
    )
    package_without_id = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase2",
        "writing_ready": False,
        "claim_planning_ready": True,
        "source": {
            "source_window_run_id": source.run_id,
            "source_window_selection_id": selection["selection_id"],
            "claim_ledger_run_id": ledger.run_id,
            "claim_ledger_id": ledger.ledger["ledger_id"],
            "framework_b2_id": ledger.ledger["framework_b2_id"],
        },
        "section_task": copy.deepcopy(section),
        "material_tier": selection["material_tier"],
        "understanding_projections": copy.deepcopy(selection["understanding_projections"]),
        "selected_source_windows": copy.deepcopy(selection["selected_source_windows"]),
        "expanded_markdown_sources": copy.deepcopy(selection["expanded_markdown_sources"]),
        "citation_metadata": copy.deepcopy(selection["citation_metadata"]),
        "approved_claim_ledger": copy.deepcopy(ledger.ledger),
        "context_composition": {
            "paper_count": selection["coverage"]["paper_count"],
            "window_count": selection["coverage"]["window_count"],
            "approved_claim_count": len(ledger.ledger["approved_claims"]),
            "rejected_claims_included": 0,
        },
    }
    package = {
        **package_without_id,
        "package_id": stable_id("package_b2", package_without_id),
    }
    return _validate_package(package)


def _validate_package(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise B2PackageV2Error("package_b2_v2.invalid", "知识包必须是对象。")
    required = {
        "schema_version",
        "pipeline_generation",
        "writing_ready",
        "claim_planning_ready",
        "source",
        "section_task",
        "material_tier",
        "understanding_projections",
        "selected_source_windows",
        "expanded_markdown_sources",
        "citation_metadata",
        "approved_claim_ledger",
        "context_composition",
        "package_id",
    }
    if set(payload) != required:
        raise B2PackageV2Error(
            "package_b2_v2.fields_invalid",
            "知识包字段集合不匹配。",
        )
    if (
        payload.get("schema_version") != PACKAGE_SCHEMA_VERSION
        or payload.get("pipeline_generation") != "B2.phase2"
        or payload.get("writing_ready") is not False
        or payload.get("claim_planning_ready") is not True
    ):
        raise B2PackageV2Error("package_b2_v2.generation_invalid", "代际或就绪状态无效。")
    forbidden = {
        "full_markdown",
        "cards",
        "evidence_units",
        "rejected_claims",
    }
    forbidden_path = _find_forbidden_key(payload, forbidden)
    if forbidden_path is not None:
        raise B2PackageV2Error(
            "package_b2_v2.forbidden_field",
            f"正式包包含禁止字段：{forbidden_path}",
        )
    expected = stable_id(
        "package_b2",
        {key: value for key, value in payload.items() if key != "package_id"},
    )
    if payload.get("package_id") != expected:
        raise B2PackageV2Error("package_b2_v2.identity_invalid", "package_id无法重建。")
    return copy.deepcopy(payload)


def _find_forbidden_key(
    value: object,
    forbidden: set[str],
    *,
    path: str = "$",
) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in forbidden:
                return child_path
            found = _find_forbidden_key(child, forbidden, path=child_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_forbidden_key(
                child,
                forbidden,
                path=f"{path}[{index}]",
            )
            if found is not None:
                return found
    return None


def _token_budget(
    package: dict[str, Any],
    *,
    profile: ModelProfile,
    config: Any,
    counter: Any,
) -> dict[str, Any]:
    prompt = json.dumps({"阶段": "B2 Phase 2包预算", "package": package}, ensure_ascii=False, separators=(",", ":"))
    count = counter.count_messages(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    )
    reserved = count.prompt_tokens + config.chapter_max_output_tokens + profile.safety_margin_tokens
    return {
        "schema_version": "llm.chapter_token_budget_b2.v1",
        "planned_input_tokens": count.prompt_tokens,
        "planned_max_output_tokens": config.chapter_max_output_tokens,
        "safety_margin_tokens": profile.safety_margin_tokens,
        "context_window_tokens": profile.context_window_tokens,
        "total_reserved_tokens": reserved,
        "within_budget": reserved <= profile.context_window_tokens,
        "overflow_tokens": max(0, reserved - profile.context_window_tokens),
        "encoded_prompt_sha256": count.encoded_prompt_sha256,
        "model_profile_id": profile.profile_id,
        "model_profile_sha256": profile.sha256,
        "claim_ledger_included": True,
        "writing_ready": False,
    }


def _report(package: dict[str, Any], budget: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# B2 Phase 2章节知识包",
            "",
            f"- 章节：{package['section_task']['title']}",
            f"- Source Window：{len(package['selected_source_windows'])}",
            f"- 批准Claim：{len(package['approved_claim_ledger']['approved_claims'])}",
            "- 拒绝Claim进入正式包：0",
            f"- 输入Token：{budget['planned_input_tokens']}",
            f"- 预算内：{'是' if budget['within_budget'] else '否'}",
            "- 正文写作就绪：否（Phase 3尚未实现）",
            "",
        ]
    )


def _manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    source_window_run_id: str,
    claim_ledger_run_id: str,
    package: dict[str, Any] | None,
    budget: dict[str, Any] | None,
    output_path: Path | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": PACKAGE_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase2",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "source_window_run_id": source_window_run_id,
        "claim_ledger_run_id": claim_ledger_run_id,
        "package_id": package["package_id"] if package else None,
        "writing_ready": False,
        "token_budget": budget,
        "output_sha256": _sha256_bytes(output_path.read_bytes()) if output_path else None,
        "failure": failure,
        "artifacts": {
            "package": "output/knowledge_package.json" if output_path else None,
            "token_budget": "audit/token_budget.json" if budget else None,
            "report": "review/knowledge_package.md" if output_path else None,
            "failures": "audit/failures.jsonl" if failure else None,
        },
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "output_path": str(run_dir / manifest["artifacts"]["package"]) if manifest["artifacts"]["package"] else None,
        "package_id": manifest["package_id"],
        "token_budget": manifest["token_budget"],
        "failure": manifest["failure"],
    }


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise B2PackageV2Error("package_b2_v2.artifact_missing", f"{label}不存在。")
    return path.read_bytes()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise B2PackageV2Error("package_b2_v2.json_invalid", f"{label}不是有效JSON。") from exc
    if not isinstance(value, dict):
        raise B2PackageV2Error("package_b2_v2.object_required", f"{label}必须是对象。")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8"))


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((value.rstrip() + "\n").encode("utf-8"))


def _safe_segment(value: object) -> str:
    if not isinstance(value, str) or not value or any(char in value for char in ("/", "\\", "\x00")):
        raise B2PackageV2Error("package_b2_v2.run_id_invalid", "run_id不是安全路径段。")
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
