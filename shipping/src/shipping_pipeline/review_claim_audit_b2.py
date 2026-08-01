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
from .review_claim_audit_b2_contracts import (
    ReviewClaimAuditB2Config,
    build_audit_draft_schema,
    derive_audit_input,
    load_review_claim_audit_b2_config,
    validate_audit_draft,
    validate_audit_input,
)
from .review_claim_audit_b2_report import (
    render_review_claim_audit_b2_report,
)
from .review_writing_b2 import (
    ReviewWritingB2RunSource,
    load_review_writing_b2_run,
)


AUDIT_B2_ROOT = "_review_claim_audits_b2"
AUDIT_B2_RUN_SCHEMA_VERSION = "llm.review_claim_audit_b2_run.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW_CLAIM_AUDIT_B2_CONFIG = (
    PROJECT_ROOT / "config" / "review-claim-audit-b2-default.json"
)
SYSTEM_PROMPT = """你是B2文献综述写后语义审计员。只输出符合给定JSON Schema的JSON对象，不输出Markdown、解释或额外字段。
程序已经把当前章节切成稳定句子。sentence_audits必须严格按输入顺序返回，不得改写句子或创造ID。
每个句子的claim_assessments必须逐项评估该句所在段落的全部计划Claim。评估方向始终是“Claim及其来源能否覆盖当前句内容”，不是“当前句是否完整复述整个Claim”。supports_all_content表示该Claim和来源足以支持当前句的全部相关内容，即使当前句只表达了较宽Claim的一部分也使用此值；supports_part_of_content表示只能支持当前句的一部分或需要限定；contradicts_content表示来源与句子冲突；not_relevant表示该Claim与当前句无关。
实质句即使映射了计划Claim，只要还增加了Ledger未批准的外部事实或综合判断，仍必须包含unplanned_claim。
supported只能单独出现，表示句中全部实质内容均由映射Claim及其Source Window支持，引用、结果类型、验证层级和强度均准确。
严格区分：来源存在但缺引用、来源不足的推断、样本文献被写成领域共识、结果类型写大、验证层级写大、引用来源错配、与来源冲突、无来源事实和计划外Claim。
不得因为句子所在段落列有引用就默认得到支持；必须阅读Claim及其绑定Source Window。不得使用输入之外的知识补证。
安全、保守且可由计划Claim逻辑推出的概括句，可以判为supported；不得仅因它没有复述来源中的具体数字而降为qualified。
明确归因于某篇具名论文的事实或作者判断，不需要添加“本次纳入的文献”限定；不得仅因缺少该限定就标记overgeneralized_synthesis。只有把少量样本文献上升为领域趋势、共识或普遍事实时才使用该风险。
不要输出sentence_role、recommended_actions、publishable、blocking、importance、planned_claim_ids或人工确认判断，这些字段由程序计算。"""


class ReviewClaimAuditB2Error(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewClaimAuditB2RunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    audit_input: dict[str, Any]
    audit: dict[str, Any]
    writing_source: ReviewWritingB2RunSource


def load_review_claim_audit_b2_run(
    workspace: str | Path,
    run_id: str,
    *,
    writing_loader: Callable[
        [str | Path, str], ReviewWritingB2RunSource
    ]
    | None = None,
) -> ReviewClaimAuditB2RunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / AUDIT_B2_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "B2审计manifest")
    manifest = _json_object(manifest_bytes, "B2审计manifest")
    if (
        manifest.get("schema_version") != AUDIT_B2_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewClaimAuditB2Error(
            "audit_b2.run_unavailable",
            "B2审计运行Schema、身份或状态不可用。",
        )
    load_writing = writing_loader or load_review_writing_b2_run
    writing_source = load_writing(
        workspace_path,
        str(manifest["writing_run_id"]),
    )
    expected_input = derive_audit_input(
        writing_source.writing_input,
        writing_source.chapter,
        writing_run_id=writing_source.run_id,
        writing_manifest_sha256=writing_source.manifest_sha256,
    )
    frozen_input = validate_audit_input(
        _read_json(run_dir / "input" / "audit_input.json", "B2审计输入")
    )
    if frozen_input != expected_input:
        raise ReviewClaimAuditB2Error(
            "audit_b2.input_replay_mismatch",
            "冻结审计输入无法由B2写作运行重放。",
        )
    config = load_review_claim_audit_b2_config(
        run_dir / "input" / "audit_config.json"
    )
    schema = _read_json(
        run_dir / "input" / "output_schema.json",
        "B2审计输出Schema",
    )
    if schema != build_audit_draft_schema(frozen_input, config):
        raise ReviewClaimAuditB2Error(
            "audit_b2.schema_replay_mismatch",
            "冻结审计Schema无法由审计输入重建。",
        )
    draft = _read_json(
        run_dir / "semantic_audit" / "validated_draft.json",
        "B2已验证审计草案",
    )
    replay = validate_audit_draft(
        draft,
        audit_input=frozen_input,
        schema=schema,
    )
    output_bytes = _read_bytes(
        run_dir / "output" / "review_claim_audit_b2.json",
        "B2正式审计输出",
    )
    audit = _json_object(output_bytes, "B2正式审计输出")
    if (
        replay != audit
        or manifest.get("audit_id") != audit.get("audit_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewClaimAuditB2Error(
            "audit_b2.output_replay_mismatch",
            "B2审计输出身份、哈希或确定性重放不一致。",
        )
    return ReviewClaimAuditB2RunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        audit_input=frozen_input,
        audit=audit,
        writing_source=writing_source,
    )


class ReviewClaimAuditB2Runner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
        writing_loader: Callable[
            [str | Path, str], ReviewWritingB2RunSource
        ]
        | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.writing_loader = writing_loader or load_review_writing_b2_run

    def run(
        self,
        *,
        writing_run_id: str,
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
        run_dir = self.workspace / AUDIT_B2_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewClaimAuditB2Error(
                "audit_b2.run_exists",
                f"run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        writing_source: ReviewWritingB2RunSource | None = None
        audit_input: dict[str, Any] | None = None
        profile: ModelProfile | None = None
        config: ReviewClaimAuditB2Config | None = None
        stage_result: dict[str, Any] | None = None
        audit: dict[str, Any] | None = None
        try:
            writing_source = self.writing_loader(
                self.workspace,
                writing_run_id,
            )
            profile = load_model_profile(Path(model_profile_path))
            config = load_review_claim_audit_b2_config(Path(audit_config_path))
            if provider != profile.provider:
                raise ReviewClaimAuditB2Error(
                    "audit_b2.provider_mismatch",
                    "provider与model profile不一致。",
                )
            if (
                writing_source.package_source.token_budget["model_profile_id"]
                != profile.profile_id
            ):
                raise ReviewClaimAuditB2Error(
                    "audit_b2.model_profile_mismatch",
                    "B2写作来源与当前审计模型profile不一致。",
                )
            audit_input = derive_audit_input(
                writing_source.writing_input,
                writing_source.chapter,
                writing_run_id=writing_source.run_id,
                writing_manifest_sha256=writing_source.manifest_sha256,
            )
            schema = build_audit_draft_schema(audit_input, config)
            _write_inputs(
                run_dir,
                audit_input=audit_input,
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
                stage="semantic_audit",
                task_name="review_claim_audit_b2",
                directory_name="semantic_audit",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_audit_prompt(audit_input, schema=schema),
                max_output_tokens=config.audit_max_output_tokens,
                context={
                    "writing_run_id": writing_run_id,
                    "audit_input_id": audit_input["audit_input_id"],
                    "chapter_id": audit_input["source"]["chapter_id"],
                    "section_index": audit_input["section"]["section_index"],
                },
                client=client,
                profile=profile,
                token_counter=counter,
            )
            audit = validate_audit_draft(
                parsed,
                audit_input=audit_input,
                schema=schema,
            )
            _write_json(
                run_dir / "semantic_audit" / "validated_draft.json",
                parsed,
            )
            output_path = run_dir / "output" / "review_claim_audit_b2.json"
            _write_json(output_path, audit)
            manifest = _manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                writing_run_id=writing_run_id,
                writing_source=writing_source,
                audit_input=audit_input,
                profile=profile,
                config=config,
                stage_result=stage_result,
                audit=audit,
                output_path=output_path,
                failure=None,
            )
            _write_text(
                run_dir / "review" / "review_claim_audit_b2.md",
                render_review_claim_audit_b2_report(
                    manifest,
                    audit,
                    audit_input,
                ),
            )
        except Exception as exc:
            if stage_result is None:
                stage_result = _read_optional_json(
                    run_dir / "semantic_audit" / "result.json"
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
                writing_run_id=writing_run_id,
                writing_source=writing_source,
                audit_input=audit_input,
                profile=profile,
                config=config,
                stage_result=stage_result,
                audit=audit,
                output_path=None,
                failure=failure,
            )
        _write_json(run_dir / "manifest.json", manifest)
        return _result(run_dir, manifest)


def build_audit_prompt(
    audit_input: dict[str, Any],
    *,
    schema: dict[str, Any],
) -> str:
    payload = {
        "任务": "逐句检查B2正文是否严格落在已批准Claim及绑定来源范围内",
        "风险分类": {
            "supported": "表述、来源、引用、结果类型、验证层级和强度均匹配",
            "qualified": "基本有来源，但需要补充限定或降低强度",
            "missing_citation_binding": "来源存在，但正文引用绑定不足",
            "unsupported_inference": "可能是合理推断，但当前来源集合不足",
            "overgeneralized_synthesis": "少量样本文献被上升为领域共识",
            "result_type_overstatement": "算法、仿真、设计或建议被写成实际效果",
            "validation_level_overstatement": "有限验证被写成全面工程验证",
            "source_mismatch": "段落引用或归因来源与句子不匹配",
            "contradicted": "绑定来源与句子明确冲突",
            "unsupported_fact": "输入来源中没有支持的外部事实",
            "unplanned_claim": "正文增加Ledger没有批准的事实或综合判断",
        },
        "B2审计输入": audit_input,
        "输出前核对": [
            "sentence_audits数量、顺序和ID与输入完全一致",
            "逐句评估所在段落全部Claim，不增删或调序",
            "只在来源不能覆盖句子全部相关内容时使用supports_part_of_content",
            "较宽Claim支持较窄句子时使用supports_all_content",
            "句子同时含计划内与计划外内容时仍标unplanned_claim",
            "supported不与其他风险并存",
            "不输出程序计算字段",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _write_inputs(
    run_dir: Path,
    *,
    audit_input: dict[str, Any],
    profile: ModelProfile,
    config: ReviewClaimAuditB2Config,
    schema: dict[str, Any],
) -> None:
    _write_json(run_dir / "input" / "audit_input.json", audit_input)
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(run_dir / "input" / "audit_config.json", config.to_dict())
    _write_json(run_dir / "input" / "output_schema.json", schema)


def _manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    writing_run_id: str,
    writing_source: ReviewWritingB2RunSource | None,
    audit_input: dict[str, Any] | None,
    profile: ModelProfile | None,
    config: ReviewClaimAuditB2Config | None,
    stage_result: dict[str, Any] | None,
    audit: dict[str, Any] | None,
    output_path: Path | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = audit["summary"] if audit else None
    return {
        "schema_version": AUDIT_B2_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3b",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "writing_run_id": writing_run_id,
        "writing_manifest_sha256": (
            writing_source.manifest_sha256 if writing_source else None
        ),
        "chapter_id": (
            writing_source.chapter["chapter_id"] if writing_source else None
        ),
        "section_index": (
            writing_source.chapter["section_index"] if writing_source else None
        ),
        "audit_input_id": (
            audit_input["audit_input_id"] if audit_input else None
        ),
        "audit_id": audit["audit_id"] if audit else None,
        "model_profile_id": profile.profile_id if profile else None,
        "audit_config": config.to_dict() if config else None,
        "stage_result": stage_result,
        "summary": summary,
        "publishable": summary["publishable"] if summary else False,
        "output_sha256": (
            _sha256_bytes(output_path.read_bytes()) if output_path else None
        ),
        "failure": failure,
        "artifacts": {
            "audit_input": "input/audit_input.json" if audit_input else None,
            "raw_response": (
                "semantic_audit/raw_response.json" if stage_result else None
            ),
            "audit": (
                "output/review_claim_audit_b2.json" if output_path else None
            ),
            "report": (
                "review/review_claim_audit_b2.md" if output_path else None
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
            str(run_dir / manifest["artifacts"]["audit"])
            if manifest["artifacts"]["audit"]
            else None
        ),
        "report_path": (
            str(run_dir / manifest["artifacts"]["report"])
            if manifest["artifacts"]["report"]
            else None
        ),
        "audit_id": manifest["audit_id"],
        "section_index": manifest["section_index"],
        "publishable": manifest["publishable"],
        "summary": manifest["summary"],
        "failure": manifest["failure"],
    }


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "output" / "review_claim_audit_b2.json").exists():
        return "publish"
    if (run_dir / "semantic_audit" / "raw_response.json").exists():
        return "semantic_audit"
    if (run_dir / "input" / "audit_input.json").exists():
        return "prepare"
    return "load_source"


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewClaimAuditB2Error(
            "audit_b2.artifact_missing",
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
        raise ReviewClaimAuditB2Error(
            "audit_b2.json_invalid",
            f"{label}不是有效UTF-8 JSON。",
        ) from exc
    if not isinstance(value, dict):
        raise ReviewClaimAuditB2Error(
            "audit_b2.json_object_required",
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
        raise ReviewClaimAuditB2Error(
            "audit_b2.run_id_invalid",
            "run_id不是安全路径段。",
        )
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat()
