from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm_analysis import DEFAULT_MODEL_PROFILE, DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_model_profile import load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_claim_audit_b2 import DEFAULT_REVIEW_CLAIM_AUDIT_B2_CONFIG
from .review_claim_audit_b2_contracts import load_review_claim_audit_b2_config
from .review_conclusion_audit_b2_contracts import (
    build_conclusion_audit_draft_schema,
    derive_conclusion_audit_input,
    validate_conclusion_audit_draft,
)
from .review_conclusion_b2 import (
    ReviewConclusionB2RunSource,
    load_review_conclusion_b2_run,
)


CONCLUSION_AUDIT_B2_ROOT = "_review_conclusion_audits_b2"
CONCLUSION_AUDIT_B2_RUN_SCHEMA_VERSION = "llm.review_conclusion_audit_b2_run.v1"
SYSTEM_PROMPT = """你是B2综述结论专用语义审计员。只输出符合JSON Schema的JSON对象，不输出解释或额外字段。
逐句判断结论是否完全落在该段绑定的已批准source_claims内。审计对象是“结论句相对来源Claim是否越界”，不得使用外部知识补证。
supported表示句子的全部实质内容、关系、因果、趋势、比较、数量、结果类型、验证层级和强度都能由所列Claim支持。句子对已批准Claim作等义压缩或近义复述时应判supported，不能因为措辞不同而判为计划外。qualified表示基本受支持但需要保留更明确限定，且没有新增事实。
unsupported_inference用于把若干Claim连接成来源未批准的新关系或因果；overgeneralized_synthesis用于把当前样本文献上升为领域趋势、共识或普遍事实；result_type_overstatement和validation_level_overstatement分别用于把仿真、设计、建议或有限验证写成实际效果或全面验证；source_mismatch表示所绑定Claim与句子内容不匹配；contradicted表示明确冲突；unsupported_fact表示没有任何绑定Claim支持的外部事实；unplanned_claim表示新增了来源Claim未批准的高层判断。
输入按段落列出source_claims。审计时优先使用相同paragraph_index的Claim；如果句子实际依赖其他段落的Claim，必须如实将该Claim列入supporting_claim_ids，程序会将其确定性标为source_mismatch。supported不得与其他风险并存。supporting_claim_ids只列真正支持该句的Claim。风险按Schema枚举顺序排列。
不得因为句子听起来合理就判supported；也不得因为它是压缩总结就自动判风险。重点检查“共识、趋势、根本、必然、显著、均不支持、上限缩”等连接性或强度表达。"""


class ReviewConclusionAuditB2Error(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewConclusionAuditB2RunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    audit_input: dict[str, Any]
    audit: dict[str, Any]
    conclusion_source: ReviewConclusionB2RunSource


def load_review_conclusion_audit_b2_run(
    workspace: str | Path, run_id: str
) -> ReviewConclusionAuditB2RunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / CONCLUSION_AUDIT_B2_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "B2结论审计manifest")
    manifest = _json_object(manifest_bytes, "B2结论审计manifest")
    if (
        manifest.get("schema_version") != CONCLUSION_AUDIT_B2_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewConclusionAuditB2Error(
            "conclusion_audit_b2.run_unavailable", "B2结论审计运行不可用。"
        )
    source = load_review_conclusion_b2_source(
        workspace_path, str(manifest["conclusion_run_id"])
    )
    expected_input = derive_conclusion_audit_input(source)
    frozen_input = _read_json(run_dir / "input" / "audit_input.json", "B2结论审计输入")
    if frozen_input != expected_input:
        raise ReviewConclusionAuditB2Error(
            "conclusion_audit_b2.input_replay_mismatch", "冻结审计输入无法由结论重放。"
        )
    config = load_review_claim_audit_b2_config(
        run_dir / "input" / "audit_config.json"
    )
    schema = _read_json(run_dir / "input" / "output_schema.json", "B2结论审计Schema")
    if schema != build_conclusion_audit_draft_schema(frozen_input, config):
        raise ReviewConclusionAuditB2Error(
            "conclusion_audit_b2.schema_replay_mismatch", "冻结审计Schema无法由输入重建。"
        )
    parsed = _read_json(
        run_dir / "conclusion_semantic_audit" / "validated_draft.json",
        "B2结论审计已验证草案",
    )
    replay = validate_conclusion_audit_draft(
        parsed, audit_input=frozen_input, schema=schema
    )
    output_bytes = _read_bytes(
        run_dir / "output" / "review_conclusion_audit_b2.json", "B2结论审计输出"
    )
    audit = _json_object(output_bytes, "B2结论审计输出")
    if (
        replay != audit
        or manifest.get("audit_id") != audit.get("audit_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewConclusionAuditB2Error(
            "conclusion_audit_b2.output_replay_mismatch", "B2结论审计哈希或重放不一致。"
        )
    return ReviewConclusionAuditB2RunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        audit_input=frozen_input,
        audit=audit,
        conclusion_source=source,
    )


class ReviewConclusionAuditB2Runner:
    def __init__(self, workspace, *, analysis_client=None, token_counter=None) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        conclusion_run_id: str,
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
        run_dir = self.workspace / CONCLUSION_AUDIT_B2_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewConclusionAuditB2Error(
                "conclusion_audit_b2.run_exists", f"run_id已存在：{resolved}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        source = audit_input = profile = config = stage_result = audit = None
        try:
            source = load_review_conclusion_b2_source(
                self.workspace, conclusion_run_id
            )
            profile = load_model_profile(Path(model_profile_path))
            config = load_review_claim_audit_b2_config(Path(audit_config_path))
            if provider != profile.provider:
                raise ReviewConclusionAuditB2Error(
                    "conclusion_audit_b2.provider_mismatch", "provider与模型配置不一致。"
                )
            audit_input = derive_conclusion_audit_input(source)
            schema = build_conclusion_audit_draft_schema(audit_input, config)
            _write_json(run_dir / "input" / "audit_input.json", audit_input)
            _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
            _write_json(run_dir / "input" / "audit_config.json", config.to_dict())
            _write_json(run_dir / "input" / "output_schema.json", schema)
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
                stage="conclusion_semantic_audit",
                task_name="review_conclusion_audit_b2",
                directory_name="conclusion_semantic_audit",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=_build_prompt(audit_input, schema),
                max_output_tokens=config.audit_max_output_tokens,
                context={
                    "conclusion_run_id": conclusion_run_id,
                    "audit_input_id": audit_input["audit_input_id"],
                    "chapter_id": source.conclusion["chapter_id"],
                },
                client=client,
                profile=profile,
                token_counter=counter,
            )
            audit = validate_conclusion_audit_draft(
                parsed, audit_input=audit_input, schema=schema
            )
            _write_json(
                run_dir / "conclusion_semantic_audit" / "validated_draft.json", parsed
            )
            output_path = run_dir / "output" / "review_conclusion_audit_b2.json"
            _write_json(output_path, audit)
            _write_report(run_dir / "review" / "review_conclusion_audit_b2.md", audit)
            manifest = _manifest(
                resolved, "completed", started_at, conclusion_run_id, source,
                audit_input, profile, config, stage_result, audit, output_path, None
            )
        except Exception as exc:
            if stage_result is None:
                stage_result = _read_optional_json(
                    run_dir / "conclusion_semantic_audit" / "result.json"
                )
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(
                resolved, "failed", started_at, conclusion_run_id, source,
                audit_input, profile, config, stage_result, audit, None, failure
            )
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": manifest["status"],
            "run_dir": str(run_dir),
            "output_path": str(run_dir / "output" / "review_conclusion_audit_b2.json") if audit else None,
            "report_path": str(run_dir / "review" / "review_conclusion_audit_b2.md") if audit else None,
            "summary": audit["summary"] if audit else None,
            "failure": manifest["failure"],
        }


def _build_prompt(audit_input, schema):
    return json.dumps(
        {
            "任务": "逐句审计候选结论是否越出已批准来源Claim",
            "结论审计输入": audit_input,
            "输出前核对": [
                "sentence_audits与输入句子数量、顺序和ID完全一致",
                "supporting_claim_ids只列当前段中实际支持该句的Claim",
                "supported不与其他风险混用",
                "不输出publishable、blocking等程序计算字段",
            ],
            "输出JSONSchema": schema,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _write_report(path: Path, audit: dict[str, Any]) -> None:
    lines = [
        "# B2结论章语义审计", "",
        f"- 句子：{audit['summary']['sentence_count']}",
        f"- 阻断句：{audit['summary']['blocking_sentence_count']}",
        f"- 状态：`{audit['summary']['status']}`", "",
    ]
    for row in audit["sentence_audits"]:
        flag = "阻断" if row["blocking"] else "通过"
        lines.extend([
            f"## 段落{row['paragraph_index']} · {flag}", "",
            row["sentence_text"], "",
            f"- 风险：{', '.join(row['risk_labels'])}",
            f"- 理由：{row['reason']}",
            f"- 支持Claim：{', '.join(row['supporting_claim_ids']) or '无'}", "",
        ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _manifest(run_id, status, started_at, conclusion_run_id, source, audit_input,
              profile, config, stage_result, audit, output_path, failure):
    return {
        "schema_version": CONCLUSION_AUDIT_B2_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3h",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "conclusion_run_id": conclusion_run_id,
        "conclusion_manifest_sha256": source.manifest_sha256 if source else None,
        "audit_input_id": audit_input["audit_input_id"] if audit_input else None,
        "audit_id": audit["audit_id"] if audit else None,
        "model_profile_id": profile.profile_id if profile else None,
        "audit_config": config.to_dict() if config else None,
        "stage_result": stage_result,
        "summary": audit["summary"] if audit else None,
        "publishable": audit["summary"]["publishable"] if audit else False,
        "output_sha256": _sha256_bytes(output_path.read_bytes()) if output_path else None,
        "failure": failure,
    }


def _safe_segment(value):
    if not value or Path(value).name != value or value in {".", ".."}:
        raise ReviewConclusionAuditB2Error(
            "conclusion_audit_b2.run_id_invalid", "run_id不是安全路径段。"
        )


def _read_optional_json(path):
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else None


def load_review_conclusion_b2_source(workspace: str | Path, run_id: str):
    workspace_path = Path(workspace)
    generation_manifest = (
        workspace_path / "_review_conclusions_b2" / "runs" / run_id / "manifest.json"
    )
    revision_manifest = (
        workspace_path
        / "_review_conclusion_revisions_b2"
        / "runs"
        / run_id
        / "manifest.json"
    )
    matches = [path for path in (generation_manifest, revision_manifest) if path.is_file()]
    if len(matches) != 1:
        raise ReviewConclusionAuditB2Error(
            "conclusion_audit_b2.source_ambiguous",
            "结论来源必须在generation或revision运行中唯一存在。",
        )
    if matches[0] == generation_manifest:
        return load_review_conclusion_b2_run(workspace_path, run_id)
    from .review_conclusion_b2_revision import (
        load_review_conclusion_revision_b2_run,
    )

    return load_review_conclusion_revision_b2_run(workspace_path, run_id)


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewConclusionAuditB2Error(
            "conclusion_audit_b2.artifact_missing", f"{label}不存在：{path}"
        )
    return path.read_bytes()


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewConclusionAuditB2Error(
            "conclusion_audit_b2.json_invalid", f"{label}不是有效UTF-8 JSON。"
        ) from exc
    if not isinstance(value, dict):
        raise ReviewConclusionAuditB2Error(
            "conclusion_audit_b2.json_object_required", f"{label}必须是JSON对象。"
        )
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _sha256_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _now():
    return datetime.now(UTC).isoformat()
