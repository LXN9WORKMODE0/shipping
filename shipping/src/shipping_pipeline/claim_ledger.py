from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .claim_ledger_contracts import (
    CLAIM_LEDGER_RUN_SCHEMA_VERSION,
    adjudicate_claim_ledger,
    build_claim_ledger_draft_schema,
    validate_claim_ledger_draft,
)
from .llm_analysis import (
    DEFAULT_MODEL_PROFILE,
    DEFAULT_TOKENIZER_CACHE,
)
from .llm_json_stage import execute_json_stage
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_framework_b2 import (
    ReviewFrameworkB2RunSource,
    load_review_framework_b2_run,
)
from .source_window_selection import (
    SourceWindowRunSource,
    load_source_window_run,
)


CLAIM_LEDGER_ROOT = "_claim_ledgers"
CLAIM_LEDGER_MAX_OUTPUT_TOKENS = 32768
CLAIM_LEDGER_CONFIG = {
    "schema_version": "llm.claim_ledger_config.v1",
    "max_output_tokens": CLAIM_LEDGER_MAX_OUTPUT_TOKENS,
    "core_rejection_fails_run": True,
    "approval_policy": "program_validated.v1",
}
CLAIM_LEDGER_SYSTEM_PROMPT = """你是文献综述的写前Claim规划器。只输出符合给定JSON Schema的JSON对象，不输出Markdown、解释或Schema之外字段。
你的任务是提出本章节允许写入正文的有限Claim，不是撰写正文。
每条Claim只能引用输入中存在的paper_id、window_id和citation_key，不能补造来源。
direct_fact必须忠实复述来源；comparative_fact必须逐一绑定参与比较的论文和窗口；cross_paper_synthesis必须至少绑定两篇论文并明确限定在当前样本文献；corpus_gap只能描述本次语料；author_view_summary必须明确是作者观点；normative_recommendation必须明确是建议。
核心Claim优先回答章节问题和必做比较。不要为了用满预算而生成重复Claim。
result_type和validation_level不得写大。comparative_fact和cross_paper_synthesis使用mixed；corpus_gap、normative_recommendation和navigation_synthesis使用not_applicable。
direct_fact、comparative_fact、cross_paper_synthesis和author_view_summary的supporting_papers必须恰好等于supporting_source_windows所属paper_id的去重集合：每篇支持论文至少绑定一个窗口，禁止列入没有窗口的论文。corpus_gap、normative_recommendation和navigation_synthesis可不绑定原文窗口。citation_keys必须按supporting_papers顺序一一对应。
类型映射必须严格遵守：direct_fact使用source_reported和source_reported_only；comparative_fact使用direct_comparison和qualified_comparison；cross_paper_synthesis使用corpus_level_inference和targeted_corpus_only；author_view_summary使用author_view和source_reported_only；corpus_gap使用corpus_scope_observation和targeted_corpus_only；normative_recommendation使用reviewer_recommendation和targeted_corpus_only；navigation_synthesis使用navigation_only和targeted_corpus_only。
限定语必须逐字使用合同首选形式：cross_paper_synthesis以“本次纳入的文献”限定；corpus_gap以“本次语料”限定；author_view_summary明确写“作者认为”或“作者提出”；normative_recommendation明确写“建议”。不要用“本语料”“本综述所覆盖”“一文指出”等近义替换。
direct_fact和author_view_summary必须至少绑定一个result_types与validation_levels均非空的窗口；填写的result_type和validation_level必须逐字来自所绑定窗口的对应数组。只有方法或背景文本、但类型数组为空的窗口不能单独证明这两类Claim。
audit_notes只写一句不超过120个汉字的证据说明，禁止写思考过程、自我纠错、字段检查过程或备选方案。
每条Claim必须且只能包含claim_type、planned_claim、section_role、importance、supporting_papers、supporting_source_windows、citation_keys、support_mode、result_type、validation_level、allowed_strength、audit_notes这12个字段。planned_claim字段不可改名，禁止输出plain_claim或任何额外字段。"""


class ClaimLedgerError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ClaimLedgerRunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    ledger: dict[str, Any]
    framework_b2: dict[str, Any]
    source_selection: dict[str, Any]


def load_claim_ledger_run(
    workspace: str | Path,
    run_id: str,
    *,
    framework_loader: Callable[
        [str | Path, str], ReviewFrameworkB2RunSource
    ] | None = None,
    source_loader: Callable[
        [str | Path, str], SourceWindowRunSource
    ] | None = None,
) -> ClaimLedgerRunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id, "claim_ledger_run_id")
    run_dir = workspace_path / CLAIM_LEDGER_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "Claim Ledger manifest")
    manifest = _json_object(manifest_bytes, "Claim Ledger manifest")
    if manifest.get("schema_version") != CLAIM_LEDGER_RUN_SCHEMA_VERSION:
        raise ClaimLedgerError(
            "claim_ledger.run_schema_invalid",
            "Claim Ledger运行Schema版本不受支持。",
        )
    if manifest.get("run_id") != run_id or manifest.get("status") != "completed":
        raise ClaimLedgerError(
            "claim_ledger.run_unavailable",
            "Claim Ledger运行身份或状态不可用。",
        )
    output_bytes = _read_bytes(
        run_dir / "output" / "claim_ledger.json",
        "Claim Ledger正式输出",
    )
    ledger = _json_object(output_bytes, "Claim Ledger正式输出")
    if (
        manifest.get("ledger_id") != ledger.get("ledger_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ClaimLedgerError(
            "claim_ledger.output_identity_mismatch",
            "Claim Ledger输出身份或哈希不一致。",
        )
    load_framework = framework_loader or load_review_framework_b2_run
    load_source = source_loader or load_source_window_run
    framework_source = load_framework(
        workspace_path,
        str(manifest["framework_b2_run_id"]),
    )
    source = load_source(
        workspace_path,
        str(manifest["source_window_run_id"]),
    )
    draft = _read_json(
        run_dir / "claim_generation" / "validated_draft.json",
        "Claim Ledger已验证草案",
    )
    replay, rejected = adjudicate_claim_ledger(
        draft,
        framework_b2=framework_source.framework,
        source_selection=source.selection,
    )
    frozen_approved = _read_jsonl(
        run_dir / "audit" / "approved_claims.jsonl",
        "Claim Ledger批准账本",
    )
    frozen_rejected = _read_jsonl(
        run_dir / "audit" / "rejected_claims.jsonl",
        "Claim Ledger拒绝账本",
    )
    if (
        replay != ledger
        or frozen_approved != ledger["approved_claims"]
        or frozen_rejected != rejected
    ):
        raise ClaimLedgerError(
            "claim_ledger.output_replay_mismatch",
            "Claim Ledger无法由冻结候选和来源重放。",
        )
    return ClaimLedgerRunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        ledger=ledger,
        framework_b2=framework_source.framework,
        source_selection=source.selection,
    )


class ClaimLedgerRunner:
    def __init__(
        self,
        workspace: str | Path,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
        framework_loader: Callable[
            [str | Path, str], ReviewFrameworkB2RunSource
        ] | None = None,
        source_loader: Callable[
            [str | Path, str], SourceWindowRunSource
        ] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.framework_loader = framework_loader or load_review_framework_b2_run
        self.source_loader = source_loader or load_source_window_run

    def run(
        self,
        *,
        framework_b2_run_id: str,
        source_window_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved, "run_id")
        run_dir = self.workspace / CLAIM_LEDGER_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ClaimLedgerError(
                "claim_ledger.run_exists",
                f"Claim Ledger run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        framework_source: ReviewFrameworkB2RunSource | None = None
        source: SourceWindowRunSource | None = None
        profile: ModelProfile | None = None
        stage_result: dict[str, Any] | None = None
        ledger: dict[str, Any] | None = None
        rejected: list[dict[str, Any]] = []
        try:
            framework_source = self.framework_loader(
                self.workspace,
                framework_b2_run_id,
            )
            source = self.source_loader(
                self.workspace,
                source_window_run_id,
            )
            section = _validate_source_alignment(
                framework_source.framework,
                source.selection,
            )
            profile = load_model_profile(Path(model_profile_path))
            if provider != profile.provider:
                raise ClaimLedgerError(
                    "claim_ledger.provider_mismatch",
                    "provider与model profile不一致。",
                )
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=profile.request_model,
                timeout=timeout,
            )
            token_counter = self.token_counter or DeepSeekV4TokenCounter(
                profile,
                DEFAULT_TOKENIZER_CACHE,
            )
            windows = source.selection["selected_source_windows"]
            schema = build_claim_ledger_draft_schema(
                section=section,
                paper_ids=list(section["paper_ids"]),
                window_ids=[row["window_id"] for row in windows],
                citation_keys=list(section["citation_keys"]),
            )
            _write_inputs(
                run_dir,
                framework_source=framework_source,
                source=source,
                profile=profile,
                schema=schema,
            )
            prompt = build_claim_ledger_prompt(
                section=section,
                source_selection=source.selection,
                schema=schema,
            )
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="claim_generation",
                task_name="claim_ledger",
                directory_name="claim_generation",
                system_prompt=CLAIM_LEDGER_SYSTEM_PROMPT,
                user_prompt=prompt,
                max_output_tokens=CLAIM_LEDGER_MAX_OUTPUT_TOKENS,
                context={
                    "framework_b2_run_id": framework_b2_run_id,
                    "source_window_run_id": source_window_run_id,
                    "section_id": section["section_id"],
                },
                client=client,
                profile=profile,
                token_counter=token_counter,
            )
            draft = validate_claim_ledger_draft(parsed, schema)
            _write_json(
                run_dir / "claim_generation" / "validated_draft.json",
                draft,
            )
            ledger, rejected = adjudicate_claim_ledger(
                draft,
                framework_b2=framework_source.framework,
                source_selection=source.selection,
            )
            _write_jsonl(
                run_dir / "audit" / "approved_claims.jsonl",
                ledger["approved_claims"],
            )
            _write_jsonl(
                run_dir / "audit" / "rejected_claims.jsonl",
                rejected,
            )
            if ledger["core_rejected"]:
                raise ClaimLedgerError(
                    "claim_ledger.core_candidate_rejected",
                    "至少一个核心Claim被程序拒绝，Ledger不得发布。",
                )
            _write_json(run_dir / "output" / "claim_ledger.json", ledger)
            from .claim_ledger_report import render_claim_ledger_report

            _write_text(
                run_dir / "review" / "claim_ledger.md",
                render_claim_ledger_report(ledger, rejected),
            )
            manifest = _manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                framework_b2_run_id=framework_b2_run_id,
                source_window_run_id=source_window_run_id,
                framework_source=framework_source,
                source=source,
                profile=profile,
                stage_result=stage_result,
                ledger=ledger,
                rejected=rejected,
                output_path=run_dir / "output" / "claim_ledger.json",
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
            if stage_result is None:
                stage_result = _read_optional_json(
                    run_dir / "claim_generation" / "result.json"
                )
            manifest = _manifest(
                run_id=resolved,
                status="failed",
                started_at=started_at,
                framework_b2_run_id=framework_b2_run_id,
                source_window_run_id=source_window_run_id,
                framework_source=framework_source,
                source=source,
                profile=profile,
                stage_result=stage_result,
                ledger=ledger,
                rejected=rejected,
                output_path=None,
                failure=failure,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def build_claim_ledger_prompt(
    *,
    section: dict[str, Any],
    source_selection: dict[str, Any],
    schema: dict[str, Any],
) -> str:
    budget = section["claim_budget"]
    payload = {
        "任务": "为单个综述章节生成有限、可审计的候选Claim",
        "本章硬性数量上限": {
            "核心Claim": f"不得超过{budget['core_max']}条",
            "支持Claim": f"不得超过{budget['supporting_max']}条",
            "跨论文综合Claim": (
                f"不得超过{budget['cross_paper_synthesis_max']}条"
            ),
        },
        "章节任务": {
            key: section[key]
            for key in (
                "section_id",
                "title",
                "section_type",
                "question",
                "purpose",
                "required_comparisons",
                "corpus_limitations",
                "target_chars",
                "max_paragraphs",
                "claim_budget",
                "high_risk_claim_types",
                "conclusion_strength_limit",
            )
        },
        "相关论文认知": source_selection["understanding_projections"],
        "精选原文窗口": source_selection["selected_source_windows"],
        "题录映射": source_selection["citation_metadata"],
        "输出前核对": [
            (
                f"核心Claim不超过{budget['core_max']}条，"
                f"支持Claim不超过{budget['supporting_max']}条"
            ),
            (
                "cross_paper_synthesis不超过"
                f"{budget['cross_paper_synthesis_max']}条"
            ),
            "每个核心事实Claim都完整绑定来源窗口",
            "需要原文窗口的四类Claim中，supporting_papers恰好等于窗口所属论文的去重集合",
            "比较和跨论文综合至少绑定两篇论文",
            "样本综合和语料缺口必须带范围限定语",
            "作者观点不得写成已实施或实测事实",
            "audit_notes只写一句不超过120个汉字的证据说明，不写推理或纠错过程",
            "不要输出正文段落，不要补造ID",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _validate_source_alignment(
    framework_b2: dict[str, Any],
    source_selection: dict[str, Any],
) -> dict[str, Any]:
    if (
        framework_b2["source"]["framework_run_id"]
        != source_selection["source"]["framework_run_id"]
    ):
        raise ClaimLedgerError(
            "claim_ledger.framework_source_mismatch",
            "Framework B2与Source Window不来自同一Framework v1运行。",
        )
    section_id = source_selection["section"]["section_id"]
    rows = [
        row
        for row in framework_b2["sections"]
        if row["section_id"] == section_id
    ]
    if len(rows) != 1:
        raise ClaimLedgerError(
            "claim_ledger.section_mismatch",
            "Framework B2与Source Window章节不一致。",
        )
    section = rows[0]
    if section["paper_ids"] != source_selection["section"]["paper_ids"]:
        raise ClaimLedgerError(
            "claim_ledger.paper_set_mismatch",
            "Framework B2与Source Window论文集合不一致。",
        )
    return section


def _write_inputs(
    run_dir: Path,
    *,
    framework_source: ReviewFrameworkB2RunSource,
    source: SourceWindowRunSource,
    profile: ModelProfile,
    schema: dict[str, Any],
) -> None:
    _write_json(run_dir / "input" / "review_framework_b2.json", framework_source.framework)
    _write_json(run_dir / "input" / "source_window_selection.json", source.selection)
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(run_dir / "input" / "claim_ledger_config.json", CLAIM_LEDGER_CONFIG)
    _write_json(run_dir / "input" / "output_schema.json", schema)


def _manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    framework_b2_run_id: str,
    source_window_run_id: str,
    framework_source: ReviewFrameworkB2RunSource | None,
    source: SourceWindowRunSource | None,
    profile: ModelProfile | None,
    stage_result: dict[str, Any] | None,
    ledger: dict[str, Any] | None,
    rejected: list[dict[str, Any]],
    output_path: Path | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": CLAIM_LEDGER_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase2",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "framework_b2_run_id": framework_b2_run_id,
        "framework_b2_id": (
            framework_source.framework["framework_id"]
            if framework_source
            else None
        ),
        "source_window_run_id": source_window_run_id,
        "source_window_selection_id": (
            source.selection["selection_id"] if source else None
        ),
        "section_id": ledger["section_id"] if ledger else (
            source.selection["section"]["section_id"] if source else None
        ),
        "ledger_id": (
            ledger["ledger_id"]
            if ledger is not None and output_path is not None
            else None
        ),
        "candidate_ledger_id": (
            ledger["ledger_id"] if ledger is not None else None
        ),
        "claim_ledger_config_sha256": _sha256_json(CLAIM_LEDGER_CONFIG),
        "model_profile_id": profile.profile_id if profile else None,
        "model_profile_sha256": profile.sha256 if profile else None,
        "stage_result": stage_result,
        "approved_claim_count": (
            len(ledger["approved_claims"]) if ledger else 0
        ),
        "rejected_claim_count": len(rejected),
        "output_sha256": (
            _sha256_bytes(output_path.read_bytes())
            if output_path is not None
            else None
        ),
        "failure": failure,
        "artifacts": {
            "ledger": (
                "output/claim_ledger.json"
                if output_path is not None
                else None
            ),
            "approved_claims": (
                "audit/approved_claims.jsonl"
                if (ledger or rejected)
                else None
            ),
            "rejected_claims": (
                "audit/rejected_claims.jsonl"
                if (ledger or rejected)
                else None
            ),
            "raw_response": (
                "claim_generation/raw_response.json"
                if stage_result is not None
                else None
            ),
            "report": (
                "review/claim_ledger.md"
                if output_path is not None
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
        "output_path": (
            str(run_dir / manifest["artifacts"]["ledger"])
            if manifest["artifacts"]["ledger"]
            else None
        ),
        "report_path": (
            str(run_dir / manifest["artifacts"]["report"])
            if manifest["artifacts"]["report"]
            else None
        ),
        "ledger_id": manifest["ledger_id"],
        "approved_claim_count": manifest["approved_claim_count"],
        "rejected_claim_count": manifest["rejected_claim_count"],
        "failure": manifest["failure"],
    }


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "output" / "claim_ledger.json").exists():
        return "publish"
    if (run_dir / "audit" / "rejected_claims.jsonl").exists():
        return "adjudication"
    if (run_dir / "claim_generation" / "raw_response.json").exists():
        return "claim_generation"
    if (run_dir / "input" / "source_window_selection.json").exists():
        return "prepare"
    return "load_source"


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ClaimLedgerError("claim_ledger.artifact_missing", f"{label}不存在：{path}")
    return path.read_bytes()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return _read_json(path, str(path))


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    data = _read_bytes(path, label)
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(data.decode("utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ClaimLedgerError(
                "claim_ledger.jsonl_invalid",
                f"{label}第{index}行不是有效JSON。",
            ) from exc
        if not isinstance(value, dict):
            raise ClaimLedgerError(
                "claim_ledger.jsonl_object_required",
                f"{label}第{index}行必须是对象。",
            )
        rows.append(value)
    return rows


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaimLedgerError("claim_ledger.json_invalid", f"{label}不是有效JSON。") from exc
    if not isinstance(value, dict):
        raise ClaimLedgerError("claim_ledger.json_object_required", f"{label}必须是对象。")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")
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
        raise ClaimLedgerError("claim_ledger.run_id_invalid", f"{field}不是安全路径段。")
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
