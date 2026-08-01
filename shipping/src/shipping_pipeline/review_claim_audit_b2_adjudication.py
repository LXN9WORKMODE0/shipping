from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .llm_analysis import DEFAULT_MODEL_PROFILE, DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_claim_audit_b2 import (
    DEFAULT_REVIEW_CLAIM_AUDIT_B2_CONFIG,
    ReviewClaimAuditB2RunSource,
    load_review_claim_audit_b2_run,
)
from .review_claim_audit_b2_adjudication_contracts import (
    build_adjudication_draft_schema,
    derive_adjudication_input,
    validate_adjudication_draft,
)
from .review_claim_audit_b2_adjudication_report import (
    render_adjudication_report,
)
from .review_claim_audit_b2_contracts import (
    ReviewClaimAuditB2Config,
    load_review_claim_audit_b2_config,
)


ADJUDICATION_ROOT = "_review_claim_audit_adjudications_b2"
ADJUDICATION_RUN_SCHEMA_VERSION = (
    "llm.review_claim_audit_b2_adjudication_run.v1"
)
SYSTEM_PROMPT = """你是B2文献综述风险句独立裁决员。只输出符合JSON Schema的JSON对象，不输出Markdown、解释或额外字段。
输入只包含初审标记的风险句。初审结论不是权威答案；你必须重新阅读原句、所在段落候选Claim及其Source Window，独立判断。
评估方向始终是“Claim及其来源能否覆盖当前句内容”。较宽Claim可以支持较窄句子，多条Claim可以共同覆盖一个句子。
claim_reassessments必须逐项复核所在段落全部候选Claim，不得增删、调序或创造ID。
dismiss表示初审风险不成立，final_risk_categories必须仅为supported。confirm表示初审风险和分类均成立，风险数组必须原样保留。reclassify表示风险成立但初审分类不准确，必须给出不同的非supported分类。
安全、保守、可由计划Claim及来源逻辑推出的概括或比较可以dismiss；不得仅因句子没有复述全部细节或数字而保留风险。
明确归因于具名论文的事实或作者判断，不需要“本次纳入的文献”限定。只有把少量文献上升为领域趋势、共识或普遍事实时才是overgeneralized_synthesis。
句子即使有计划Claim支持，只要还增加了未批准的外部事实或综合判断，仍应确认或重分类为unplanned_claim。
只要最终风险包含unplanned_claim，必须在uncovered_texts中逐字摘出原句里的具体计划外片段；这些片段不得已存在于任何候选planned_claim中。其他裁决的uncovered_texts必须为空。
不要输出发布状态、重要性、修订动作或最终Claim ID数组，这些由程序计算。"""


class ReviewClaimAuditB2AdjudicationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewClaimAuditB2AdjudicationRunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    adjudication_input: dict[str, Any]
    adjudication: dict[str, Any]
    audit_source: ReviewClaimAuditB2RunSource


def load_review_claim_audit_b2_adjudication_run(
    workspace: str | Path,
    run_id: str,
    *,
    audit_loader: Callable[
        [str | Path, str], ReviewClaimAuditB2RunSource
    ]
    | None = None,
) -> ReviewClaimAuditB2AdjudicationRunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / ADJUDICATION_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "B2裁决manifest")
    manifest = _json_object(manifest_bytes, "B2裁决manifest")
    if (
        manifest.get("schema_version") != ADJUDICATION_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewClaimAuditB2AdjudicationError(
            "adjudication_b2.run_unavailable",
            "B2裁决运行Schema、身份或状态不可用。",
        )
    load_audit = audit_loader or load_review_claim_audit_b2_run
    audit_source = load_audit(workspace_path, str(manifest["audit_run_id"]))
    expected_input = derive_adjudication_input(
        audit_source.audit_input,
        audit_source.audit,
        audit_run_id=audit_source.run_id,
        audit_manifest_sha256=audit_source.manifest_sha256,
    )
    frozen_input = _read_json(
        run_dir / "input" / "adjudication_input.json",
        "B2裁决输入",
    )
    if frozen_input != expected_input:
        raise ReviewClaimAuditB2AdjudicationError(
            "adjudication_b2.input_replay_mismatch",
            "冻结裁决输入无法由B2审计运行重放。",
        )
    config = load_review_claim_audit_b2_config(
        run_dir / "input" / "audit_config.json"
    )
    schema = _read_json(
        run_dir / "input" / "output_schema.json",
        "B2裁决输出Schema",
    )
    expected_schema = build_adjudication_draft_schema(
        frozen_input,
        max_reason_chars=config.max_reason_chars,
    )
    if schema != expected_schema:
        raise ReviewClaimAuditB2AdjudicationError(
            "adjudication_b2.schema_replay_mismatch",
            "冻结裁决Schema无法由裁决输入重建。",
        )
    draft = _read_json(
        run_dir / "risk_adjudication" / "validated_draft.json",
        "B2已验证裁决草案",
    )
    replay = validate_adjudication_draft(
        draft,
        adjudication_input=frozen_input,
        source_audit=audit_source.audit,
        schema=schema,
    )
    output_bytes = _read_bytes(
        run_dir / "output" / "review_claim_audit_b2_adjudication.json",
        "B2正式裁决输出",
    )
    result = _json_object(output_bytes, "B2正式裁决输出")
    if (
        replay != result
        or manifest.get("adjudication_id") != result.get("adjudication_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewClaimAuditB2AdjudicationError(
            "adjudication_b2.output_replay_mismatch",
            "B2裁决输出身份、哈希或确定性重放不一致。",
        )
    return ReviewClaimAuditB2AdjudicationRunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        adjudication_input=frozen_input,
        adjudication=result,
        audit_source=audit_source,
    )


class ReviewClaimAuditB2AdjudicationRunner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
        audit_loader: Callable[
            [str | Path, str], ReviewClaimAuditB2RunSource
        ]
        | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.audit_loader = audit_loader or load_review_claim_audit_b2_run

    def run(
        self,
        *,
        audit_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        audit_config_path: str | Path = DEFAULT_REVIEW_CLAIM_AUDIT_B2_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / ADJUDICATION_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewClaimAuditB2AdjudicationError(
                "adjudication_b2.run_exists",
                f"run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        audit_source: ReviewClaimAuditB2RunSource | None = None
        adjudication_input: dict[str, Any] | None = None
        profile: ModelProfile | None = None
        config: ReviewClaimAuditB2Config | None = None
        stage_result: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        try:
            audit_source = self.audit_loader(self.workspace, audit_run_id)
            profile = load_model_profile(Path(model_profile_path))
            config = load_review_claim_audit_b2_config(Path(audit_config_path))
            if provider != profile.provider:
                raise ReviewClaimAuditB2AdjudicationError(
                    "adjudication_b2.provider_mismatch",
                    "provider与model profile不一致。",
                )
            if (
                audit_source.writing_source.package_source.token_budget[
                    "model_profile_id"
                ]
                != profile.profile_id
            ):
                raise ReviewClaimAuditB2AdjudicationError(
                    "adjudication_b2.model_profile_mismatch",
                    "B2审计来源与当前裁决模型profile不一致。",
                )
            adjudication_input = derive_adjudication_input(
                audit_source.audit_input,
                audit_source.audit,
                audit_run_id=audit_source.run_id,
                audit_manifest_sha256=audit_source.manifest_sha256,
            )
            schema = build_adjudication_draft_schema(
                adjudication_input,
                max_reason_chars=config.max_reason_chars,
            )
            _write_inputs(
                run_dir,
                adjudication_input=adjudication_input,
                profile=profile,
                config=config,
                schema=schema,
            )
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=profile.request_model,
                timeout=timeout,
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(
                profile,
                DEFAULT_TOKENIZER_CACHE,
            )
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="risk_adjudication",
                task_name="review_claim_audit_b2_adjudication",
                directory_name="risk_adjudication",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_adjudication_prompt(
                    adjudication_input,
                    schema=schema,
                ),
                max_output_tokens=config.audit_max_output_tokens,
                context={
                    "audit_run_id": audit_run_id,
                    "adjudication_input_id": adjudication_input[
                        "adjudication_input_id"
                    ],
                    "chapter_id": adjudication_input["source"]["chapter_id"],
                    "section_index": adjudication_input["section"][
                        "section_index"
                    ],
                },
                client=client,
                profile=profile,
                token_counter=counter,
            )
            result = validate_adjudication_draft(
                parsed,
                adjudication_input=adjudication_input,
                source_audit=audit_source.audit,
                schema=schema,
            )
            _write_json(
                run_dir / "risk_adjudication" / "validated_draft.json",
                parsed,
            )
            output_path = (
                run_dir
                / "output"
                / "review_claim_audit_b2_adjudication.json"
            )
            _write_json(output_path, result)
            manifest = _manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                audit_run_id=audit_run_id,
                audit_source=audit_source,
                adjudication_input=adjudication_input,
                profile=profile,
                config=config,
                stage_result=stage_result,
                result=result,
                output_path=output_path,
                failure=None,
            )
            _write_text(
                run_dir / "review" / "adjudication_report.md",
                render_adjudication_report(
                    manifest,
                    result,
                    adjudication_input,
                ),
            )
        except Exception as exc:
            if stage_result is None:
                stage_result = _read_optional_json(
                    run_dir / "risk_adjudication" / "result.json"
                )
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
                audit_run_id=audit_run_id,
                audit_source=audit_source,
                adjudication_input=adjudication_input,
                profile=profile,
                config=config,
                stage_result=stage_result,
                result=result,
                output_path=None,
                failure=failure,
            )
        _write_json(run_dir / "manifest.json", manifest)
        return _result(run_dir, manifest)


def build_adjudication_prompt(
    adjudication_input: dict[str, Any],
    *,
    schema: dict[str, Any],
) -> str:
    payload = {
        "任务": "独立复核B2初审风险句，区分真实越界与保守误报",
        "裁决输入": adjudication_input,
        "输出前核对": [
            "按输入顺序逐句返回，不创造ID",
            "逐项重评所在段落全部候选Claim",
            "dismiss只输出supported",
            "confirm原样保留初审风险分类",
            "reclassify输出不同的非supported分类",
            "不因安全概括、缺少细节或具名论文归因而保留风险",
            "句中确有额外事实或综合时不得为了通过而dismiss",
            "unplanned_claim必须给出原句中的具体uncovered_texts",
            "不输出程序计算字段",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _write_inputs(
    run_dir: Path,
    *,
    adjudication_input: dict[str, Any],
    profile: ModelProfile,
    config: ReviewClaimAuditB2Config,
    schema: dict[str, Any],
) -> None:
    _write_json(
        run_dir / "input" / "adjudication_input.json",
        adjudication_input,
    )
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(run_dir / "input" / "audit_config.json", config.to_dict())
    _write_json(run_dir / "input" / "output_schema.json", schema)


def _manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    audit_run_id: str,
    audit_source: ReviewClaimAuditB2RunSource | None,
    adjudication_input: dict[str, Any] | None,
    profile: ModelProfile | None,
    config: ReviewClaimAuditB2Config | None,
    stage_result: dict[str, Any] | None,
    result: dict[str, Any] | None,
    output_path: Path | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = result["summary"] if result else None
    return {
        "schema_version": ADJUDICATION_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3c",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "audit_run_id": audit_run_id,
        "audit_manifest_sha256": (
            audit_source.manifest_sha256 if audit_source else None
        ),
        "writing_run_id": (
            audit_source.writing_source.run_id if audit_source else None
        ),
        "chapter_id": (
            audit_source.audit["chapter_id"] if audit_source else None
        ),
        "section_index": (
            audit_source.audit_input["section"]["section_index"]
            if audit_source
            else None
        ),
        "adjudication_input_id": (
            adjudication_input["adjudication_input_id"]
            if adjudication_input
            else None
        ),
        "adjudication_id": (
            result["adjudication_id"] if result else None
        ),
        "model_profile_id": profile.profile_id if profile else None,
        "audit_config": config.to_dict() if config else None,
        "stage_result": stage_result,
        "summary": summary,
        "publishable_after_adjudication": (
            summary["publishable_after_adjudication"] if summary else False
        ),
        "output_sha256": (
            _sha256_bytes(output_path.read_bytes()) if output_path else None
        ),
        "failure": failure,
        "artifacts": {
            "input": (
                "input/adjudication_input.json" if adjudication_input else None
            ),
            "raw_response": (
                "risk_adjudication/raw_response.json" if stage_result else None
            ),
            "adjudication": (
                "output/review_claim_audit_b2_adjudication.json"
                if output_path
                else None
            ),
            "report": (
                "review/adjudication_report.md" if output_path else None
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
            str(run_dir / manifest["artifacts"]["adjudication"])
            if manifest["artifacts"]["adjudication"]
            else None
        ),
        "report_path": (
            str(run_dir / manifest["artifacts"]["report"])
            if manifest["artifacts"]["report"]
            else None
        ),
        "adjudication_id": manifest["adjudication_id"],
        "section_index": manifest["section_index"],
        "publishable_after_adjudication": manifest[
            "publishable_after_adjudication"
        ],
        "summary": manifest["summary"],
        "failure": manifest["failure"],
    }


def _failure_stage(run_dir: Path) -> str:
    if (
        run_dir / "output" / "review_claim_audit_b2_adjudication.json"
    ).exists():
        return "publish"
    if (run_dir / "risk_adjudication" / "raw_response.json").exists():
        return "risk_adjudication"
    if (run_dir / "input" / "adjudication_input.json").exists():
        return "prepare"
    return "load_source"


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewClaimAuditB2AdjudicationError(
            "adjudication_b2.artifact_missing",
            f"{label}不存在：{path}",
        )
    return path.read_bytes()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return _read_json(path, str(path))


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewClaimAuditB2AdjudicationError(
            "adjudication_b2.json_invalid",
            f"{label}不是有效UTF-8 JSON。",
        ) from exc
    if not isinstance(value, dict):
        raise ReviewClaimAuditB2AdjudicationError(
            "adjudication_b2.json_object_required",
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
        raise ReviewClaimAuditB2AdjudicationError(
            "adjudication_b2.run_id_invalid",
            "run_id不是安全路径段。",
        )
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat()
