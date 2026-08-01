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
from .review_claim_audit_b2 import DEFAULT_REVIEW_CLAIM_AUDIT_B2_CONFIG
from .review_claim_audit_b2_adjudication import (
    ReviewClaimAuditB2AdjudicationRunSource,
    load_review_claim_audit_b2_adjudication_run,
)
from .review_claim_audit_b2_contracts import (
    ReviewClaimAuditB2Config,
    load_review_claim_audit_b2_config,
)
from .review_claim_revision_b2_contracts import (
    build_revision_draft_schema,
    derive_revision_input,
    validate_revision_draft,
)
from .review_claim_revision_b2_report import (
    render_revision_suggestion_report,
)


REVISION_ROOT = "_review_claim_revisions_b2"
REVISION_RUN_SCHEMA_VERSION = "llm.review_claim_revision_b2_run.v1"
SYSTEM_PROMPT = """你是B2文献综述风险句修订建议员。只输出符合JSON Schema的JSON对象，不输出Markdown、解释或额外字段。
你的任务不是重写整章，也不是提交最终修改。每个风险句必须依次给出三种候选：minimal_edit、conservative_rewrite、delete_or_split。
minimal_edit应尽量保留原句结构，只删除、限定或改写风险片段；conservative_rewrite只表达计划Claim和来源能够直接支持的内容；delete_or_split可以删除无必要句子，或拆成两句并只保留可支持内容。
不得引入新的数字、事实、趋势、共识、建议或因果关系。replacement_sentences中不得写引用占位符、Claim ID或解释。
改写和拆句必须绑定本段已有Claim，并只使用这些Claim自带的citation_keys；删除方案不得绑定Claim或引用。
retained_claim_ids和citation_keys都是所有替换句绑定关系的并集，不与替换句一一对应；每个ID最多出现一次，拆出的两句共用同一Claim或引用时也只列一次。
对于unplanned_claim，必须删除或限定裁决指出的计划外片段；对于unsupported_fact或unsupported_inference，不得用语气词掩盖无支持内容；对于overgeneralized_synthesis，必须明确限定到具名研究、本次样本或具体比较对象。
三个候选必须实质不同。不要输出选择结果、应用状态、发布判断或新revision ID，这些由程序处理。"""


class ReviewClaimRevisionB2Error(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewClaimRevisionB2RunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    revision_input: dict[str, Any]
    revision_suggestions: dict[str, Any]
    adjudication_source: ReviewClaimAuditB2AdjudicationRunSource


def load_review_claim_revision_b2_run(
    workspace: str | Path,
    run_id: str,
    *,
    adjudication_loader: Callable[
        [str | Path, str], ReviewClaimAuditB2AdjudicationRunSource
    ]
    | None = None,
) -> ReviewClaimRevisionB2RunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / REVISION_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "B2修订manifest")
    manifest = _json_object(manifest_bytes, "B2修订manifest")
    if (
        manifest.get("schema_version") != REVISION_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewClaimRevisionB2Error(
            "revision_b2.run_unavailable",
            "B2修订建议运行Schema、身份或状态不可用。",
        )
    load_adjudication = (
        adjudication_loader or load_review_claim_audit_b2_adjudication_run
    )
    source = load_adjudication(
        workspace_path, str(manifest["adjudication_run_id"])
    )
    expected_input = derive_revision_input(
        source.adjudication_input,
        source.adjudication,
        source.audit_source.audit_input,
        adjudication_run_id=source.run_id,
        adjudication_manifest_sha256=source.manifest_sha256,
    )
    frozen_input = _read_json(
        run_dir / "input" / "revision_input.json", "B2修订输入"
    )
    if frozen_input != expected_input:
        raise ReviewClaimRevisionB2Error(
            "revision_b2.input_replay_mismatch",
            "冻结修订输入无法由B2裁决运行重放。",
        )
    config = load_review_claim_audit_b2_config(
        run_dir / "input" / "audit_config.json"
    )
    schema = _read_json(
        run_dir / "input" / "output_schema.json", "B2修订输出Schema"
    )
    expected_schema = build_revision_draft_schema(
        frozen_input, max_reason_chars=config.max_reason_chars
    )
    if schema != expected_schema:
        raise ReviewClaimRevisionB2Error(
            "revision_b2.schema_replay_mismatch",
            "冻结修订Schema无法由修订输入重建。",
        )
    draft = _read_json(
        run_dir / "revision_suggestion" / "validated_draft.json",
        "B2已验证修订草案",
    )
    replay = validate_revision_draft(
        draft, revision_input=frozen_input, schema=schema
    )
    output_bytes = _read_bytes(
        run_dir / "output" / "review_claim_revision_b2.json",
        "B2正式修订建议输出",
    )
    result = _json_object(output_bytes, "B2正式修订建议输出")
    if (
        replay != result
        or manifest.get("revision_suggestion_id")
        != result.get("revision_suggestion_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewClaimRevisionB2Error(
            "revision_b2.output_replay_mismatch",
            "B2修订建议输出身份、哈希或确定性重放不一致。",
        )
    return ReviewClaimRevisionB2RunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        revision_input=frozen_input,
        revision_suggestions=result,
        adjudication_source=source,
    )


class ReviewClaimRevisionB2Runner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
        adjudication_loader: Callable[
            [str | Path, str], ReviewClaimAuditB2AdjudicationRunSource
        ]
        | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.adjudication_loader = (
            adjudication_loader or load_review_claim_audit_b2_adjudication_run
        )

    def run(
        self,
        *,
        adjudication_run_id: str,
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
        run_dir = self.workspace / REVISION_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewClaimRevisionB2Error(
                "revision_b2.run_exists",
                f"run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        source: ReviewClaimAuditB2AdjudicationRunSource | None = None
        revision_input: dict[str, Any] | None = None
        profile: ModelProfile | None = None
        config: ReviewClaimAuditB2Config | None = None
        stage_result: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        try:
            source = self.adjudication_loader(
                self.workspace, adjudication_run_id
            )
            profile = load_model_profile(Path(model_profile_path))
            config = load_review_claim_audit_b2_config(Path(audit_config_path))
            if provider != profile.provider:
                raise ReviewClaimRevisionB2Error(
                    "revision_b2.provider_mismatch",
                    "provider与model profile不一致。",
                )
            model_profile_id = source.audit_source.writing_source.package_source.token_budget[
                "model_profile_id"
            ]
            if model_profile_id != profile.profile_id:
                raise ReviewClaimRevisionB2Error(
                    "revision_b2.model_profile_mismatch",
                    "B2裁决来源与当前修订模型profile不一致。",
                )
            revision_input = derive_revision_input(
                source.adjudication_input,
                source.adjudication,
                source.audit_source.audit_input,
                adjudication_run_id=source.run_id,
                adjudication_manifest_sha256=source.manifest_sha256,
            )
            schema = build_revision_draft_schema(
                revision_input, max_reason_chars=config.max_reason_chars
            )
            _write_inputs(
                run_dir,
                revision_input=revision_input,
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
                profile, DEFAULT_TOKENIZER_CACHE
            )
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="revision_suggestion",
                task_name="review_claim_revision_b2",
                directory_name="revision_suggestion",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_revision_prompt(
                    revision_input, schema=schema
                ),
                max_output_tokens=config.audit_max_output_tokens,
                context={
                    "adjudication_run_id": adjudication_run_id,
                    "revision_input_id": revision_input["revision_input_id"],
                    "chapter_id": revision_input["source"]["chapter_id"],
                    "section_index": revision_input["section"][
                        "section_index"
                    ],
                },
                client=client,
                profile=profile,
                token_counter=counter,
            )
            result = validate_revision_draft(
                parsed, revision_input=revision_input, schema=schema
            )
            _write_json(
                run_dir / "revision_suggestion" / "validated_draft.json",
                parsed,
            )
            output_path = run_dir / "output" / "review_claim_revision_b2.json"
            _write_json(output_path, result)
            manifest = _manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                adjudication_run_id=adjudication_run_id,
                source=source,
                revision_input=revision_input,
                profile=profile,
                config=config,
                stage_result=stage_result,
                result=result,
                output_path=output_path,
                failure=None,
            )
            _write_text(
                run_dir / "review" / "revision_suggestions.md",
                render_revision_suggestion_report(
                    manifest, result, revision_input
                ),
            )
        except Exception as exc:
            if stage_result is None:
                stage_result = _read_optional_json(
                    run_dir / "revision_suggestion" / "result.json"
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
                adjudication_run_id=adjudication_run_id,
                source=source,
                revision_input=revision_input,
                profile=profile,
                config=config,
                stage_result=stage_result,
                result=result,
                output_path=None,
                failure=failure,
            )
        _write_json(run_dir / "manifest.json", manifest)
        return _result(run_dir, manifest)


def build_revision_prompt(
    revision_input: dict[str, Any], *, schema: dict[str, Any]
) -> str:
    payload = {
        "任务": "只为裁决后仍有风险的句子生成三类定向修订建议",
        "修订输入": revision_input,
        "输出前核对": [
            "按输入顺序逐句返回，不创造ID",
            "每句依次给出最小修改、保守改写、删除或拆分",
            "不重写整章，不替用户选择，不输出发布结论",
            "所有保留内容均绑定本段已有Claim和其citation_keys",
            "retained_claim_ids和citation_keys是替换句绑定关系的去重并集，共用ID只列一次",
            "不引入计划外事实、推断、数字、趋势或共识",
            "三个方案实质不同，且不得原样返回风险句",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _write_inputs(
    run_dir: Path,
    *,
    revision_input: dict[str, Any],
    profile: ModelProfile,
    config: ReviewClaimAuditB2Config,
    schema: dict[str, Any],
) -> None:
    _write_json(run_dir / "input" / "revision_input.json", revision_input)
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(run_dir / "input" / "audit_config.json", config.to_dict())
    _write_json(run_dir / "input" / "output_schema.json", schema)


def _manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    adjudication_run_id: str,
    source: ReviewClaimAuditB2AdjudicationRunSource | None,
    revision_input: dict[str, Any] | None,
    profile: ModelProfile | None,
    config: ReviewClaimAuditB2Config | None,
    stage_result: dict[str, Any] | None,
    result: dict[str, Any] | None,
    output_path: Path | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = result["summary"] if result else None
    return {
        "schema_version": REVISION_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3d",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "adjudication_run_id": adjudication_run_id,
        "adjudication_manifest_sha256": (
            source.manifest_sha256 if source else None
        ),
        "audit_run_id": (
            source.audit_source.run_id if source else None
        ),
        "writing_run_id": (
            source.audit_source.writing_source.run_id if source else None
        ),
        "chapter_id": (
            source.adjudication["chapter_id"] if source else None
        ),
        "section_index": (
            source.adjudication_input["section"]["section_index"]
            if source
            else None
        ),
        "revision_input_id": (
            revision_input["revision_input_id"] if revision_input else None
        ),
        "revision_suggestion_id": (
            result["revision_suggestion_id"] if result else None
        ),
        "model_profile_id": profile.profile_id if profile else None,
        "audit_config": config.to_dict() if config else None,
        "stage_result": stage_result,
        "summary": summary,
        "decision_status": result["decision_status"] if result else None,
        "auto_applied": False,
        "output_sha256": (
            _sha256_bytes(output_path.read_bytes()) if output_path else None
        ),
        "failure": failure,
        "artifacts": {
            "input": "input/revision_input.json" if revision_input else None,
            "raw_response": (
                "revision_suggestion/raw_response.json" if stage_result else None
            ),
            "suggestions": (
                "output/review_claim_revision_b2.json" if output_path else None
            ),
            "report": (
                "review/revision_suggestions.md" if output_path else None
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
            str(run_dir / manifest["artifacts"]["suggestions"])
            if manifest["artifacts"]["suggestions"]
            else None
        ),
        "report_path": (
            str(run_dir / manifest["artifacts"]["report"])
            if manifest["artifacts"]["report"]
            else None
        ),
        "revision_suggestion_id": manifest["revision_suggestion_id"],
        "section_index": manifest["section_index"],
        "decision_status": manifest["decision_status"],
        "auto_applied": manifest["auto_applied"],
        "summary": manifest["summary"],
        "failure": manifest["failure"],
    }


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "output" / "review_claim_revision_b2.json").exists():
        return "publish"
    if (run_dir / "revision_suggestion" / "raw_response.json").exists():
        return "revision_suggestion"
    if (run_dir / "input" / "revision_input.json").exists():
        return "prepare"
    return "load_source"


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewClaimRevisionB2Error(
            "revision_b2.artifact_missing", f"{label}不存在：{path}"
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
        raise ReviewClaimRevisionB2Error(
            "revision_b2.json_invalid", f"{label}不是有效UTF-8 JSON。"
        ) from exc
    if not isinstance(value, dict):
        raise ReviewClaimRevisionB2Error(
            "revision_b2.json_object_required", f"{label}必须是JSON对象。"
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
        raise ReviewClaimRevisionB2Error(
            "revision_b2.run_id_invalid", "run_id不是安全路径段。"
        )
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat()
