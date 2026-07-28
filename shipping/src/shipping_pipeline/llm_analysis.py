from __future__ import annotations

import copy
import hashlib
import json
import shutil
import uuid
import dataclasses
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm_contracts import (
    EVIDENCE_BATCH_SCHEMA,
    NON_USED_EVIDENCE_DISPOSITION_SCHEMA,
    PAPER_ANALYSIS_DRAFT_SCHEMA,
    PAPER_ANALYSIS_SCHEMA,
    SECTION_SUMMARY_SCHEMA,
    ContractViolation,
    assign_evidence_unit_ids,
    build_evidence_batch_schema,
    finalize_paper_analysis,
    paper_analysis_referenced_evidence_ids,
    validate_evidence_batch,
    validate_non_used_evidence_dispositions,
    validate_paper_analysis,
    validate_paper_analysis_draft,
    validate_section_summary,
    verify_material_coverage,
)
from .llm_provider import (
    OpenAICompatibleAnalysisClient,
    ProviderCallError,
    ProviderResult,
    build_chat_request,
    extract_chat_content,
    validate_completed_response,
)
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_evidence_claim_support import (
    EVIDENCE_CLAIM_SUPPORT_SCHEMA,
    EVIDENCE_CLAIM_SUPPORT_SYSTEM_PROMPT,
    build_evidence_claim_confirmation_prompts,
    build_evidence_claim_support_prompts,
    build_evidence_claim_support_schema,
    blocking_evidence_claim_support_reviews,
    enforce_evidence_claim_support_policy,
    validate_evidence_claim_support_review,
)
from .llm_evidence_gate_stability import (
    EVIDENCE_CLAIM_GATE_PLAN_SCHEMA_VERSION,
    build_evidence_claim_confirmation_context,
    build_evidence_claim_gate_decisions,
    effective_evidence_claim_reviews,
    has_ordered_verbatim_quote_support,
    plan_evidence_claim_review_batches,
)
from .llm_evidence_correction import (
    EVIDENCE_CORRECTION_SCHEMA_VERSION,
    EVIDENCE_CORRECTION_SYSTEM_PROMPT,
    EvidenceCorrectionUnavailable,
    PlannedEvidenceCorrection,
    build_evidence_correction_prompts,
    collect_correctable_material_failures,
    plan_evidence_correction,
    validate_and_apply_evidence_correction,
)
from .llm_planner import (
    EVIDENCE_SYSTEM_PROMPT,
    PaperAnalysisPlan,
    PlannedRequest,
    PlannedSynthesisRequest,
    PlanningError,
    build_evidence_prompts as build_projected_evidence_prompts,
    plan_paper_analysis,
    plan_synthesis_request,
)
from .llm_projection import project_cards
from .llm_quotes import build_quote_candidates
from .llm_review import (
    write_analysis_review,
    write_failed_analysis_review,
    write_semantic_adjudication_review,
    write_semantic_audit_review,
    write_statement_revision_review,
)
from .llm_sections import SectionMap, build_section_map
from .llm_synthesis import (
    NON_USED_EVIDENCE_DISPOSITION_SYSTEM_PROMPT,
    PAPER_ROOT_SECTION_ID,
    PAPER_SYNTHESIS_SYSTEM_PROMPT,
    SECTION_SUMMARY_SYSTEM_PROMPT,
    assign_evidence_to_sections,
    build_direct_paper_prompts,
    build_final_evidence_coverage,
    build_hierarchical_paper_prompts,
    build_non_used_evidence_disposition_prompts,
    build_section_summary_prompts,
    summary_evidence_ids,
)
from .llm_statement_support import (
    STATEMENT_SUPPORT_SCHEMA,
    STATEMENT_SUPPORT_SYSTEM_PROMPT,
    build_statement_records,
    build_statement_support_prompts,
    blocking_statement_support_reviews,
    enforce_statement_support_policy,
    validate_statement_support_review,
)
from .llm_statement_revision import (
    STATEMENT_REVISION_SCHEMA_VERSION,
    STATEMENT_REVISION_SYSTEM_PROMPT,
    build_statement_revision_prompts,
    build_statement_revision_schema,
    validate_and_apply_statement_revision,
)
from .llm_statement_role import (
    STATEMENT_ROLE_SCHEMA,
    STATEMENT_ROLE_SYSTEM_PROMPT,
    build_statement_role_prompts,
    build_statement_role_schema,
    blocking_statement_role_reviews,
    enforce_statement_role_policy,
    validate_statement_role_review,
)
from .llm_semantic_adjudication import (
    EVIDENCE_REVISION_SCHEMA_VERSION,
    SemanticAdjudicationError,
    build_evidence_revision_prompts,
    build_semantic_issues,
    enrich_semantic_issues_for_review,
    load_semantic_decisions,
    validate_and_apply_evidence_revision,
    write_semantic_decision_workbook,
    write_semantic_decisions_template,
)
from .llm_semantic_auto_resolution import (
    SEMANTIC_AUTO_RESOLUTION_SCHEMA_VERSION,
    apply_auto_resolution_plan,
    build_auto_resolution_context,
    build_auto_resolution_prompts,
    build_auto_resolution_schema,
    validate_auto_resolution_plan,
)
from .llm_tokenizer import DeepSeekV4TokenCounter


RUN_SCHEMA_VERSION = "llm.analysis_run.v4"
REVISION_RUN_SCHEMA_VERSION = "llm.statement_revision_run.v2"
SEMANTIC_AUDIT_RUN_SCHEMA_VERSION = "llm.semantic_audit_run.v1"
SEMANTIC_ADJUDICATION_RUN_SCHEMA_VERSION = "llm.semantic_adjudication_run.v1"
SEMANTIC_AUTO_RESOLUTION_RUN_SCHEMA_VERSION = "llm.semantic_auto_resolution_run.v1"
PAPER_RESULT_SCHEMA_VERSION = "llm.paper_result.v3"
ADJUDICATED_PAPER_RESULT_SCHEMA_VERSION = "llm.paper_result.v4"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "deepseek-v4-pro-official.json"
)
DEFAULT_TOKENIZER_CACHE = PROJECT_ROOT / ".model_cache"

SYNTHESIS_SYSTEM_PROMPT = PAPER_SYNTHESIS_SYSTEM_PROMPT


class AnalysisInputError(ValueError):
    pass


@dataclass(frozen=True)
class AnalysisInputSnapshot:
    paper_id: str
    paper_title: str
    topic: str
    generation_id: str
    materials: tuple[dict[str, Any], ...]
    input_sha256: str
    topic_sha256: str
    scope: str
    source_material_count: int


@dataclass(frozen=True)
class RetryPreparation:
    parent_dir: Path
    mode: str
    target_stage: str


@dataclass(frozen=True)
class StatementRevisionSource:
    run_dir: Path
    manifest: dict[str, Any]
    snapshot: AnalysisInputSnapshot
    evidence_units: tuple[dict[str, Any], ...]
    evidence_claim_support_reviews: tuple[dict[str, Any], ...]
    original_analysis: dict[str, Any]
    blocking_records: tuple[dict[str, Any], ...]
    source_support_request_id: str


@dataclass(frozen=True)
class SemanticAuditSource:
    run_dir: Path
    manifest: dict[str, Any]
    snapshot: AnalysisInputSnapshot
    evidence_units: tuple[dict[str, Any], ...]
    analysis: dict[str, Any]


@dataclass(frozen=True)
class SemanticAutoResolutionSource:
    audit_dir: Path
    audit_manifest: dict[str, Any]
    snapshot: AnalysisInputSnapshot
    evidence_units: tuple[dict[str, Any], ...]
    analysis: dict[str, Any]
    evidence_claim_reviews: tuple[dict[str, Any], ...]
    statement_role_reviews: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class SemanticAdjudicationSource:
    audit_dir: Path
    audit_manifest: dict[str, Any]
    source: SemanticAuditSource
    assessments: tuple[dict[str, Any], ...]
    evidence_claim_reviews: tuple[dict[str, Any], ...]
    statement_role_reviews: tuple[dict[str, Any], ...]
    statement_records: tuple[dict[str, Any], ...]
    issues: tuple[dict[str, str], ...]
    decisions: dict[str, Any]
    decisions_path: Path


class LLMAnalysisRunner:
    """以单篇论文为事务边界执行可审计的两阶段 LLM 分析。"""

    def __init__(
        self,
        workspace: str | Path,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        run_id: str | None = None,
        topic: str = "",
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 120,
        paper_id: str | None = None,
        limit: int | None = None,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        dry_run: bool = False,
        retry_from: str | None = None,
        retry_batch_id: str | None = None,
    ) -> dict[str, Any]:
        if not paper_id:
            raise AnalysisInputError("LLM 分析必须显式提供 paper_id。")
        if bool(retry_from) != bool(retry_batch_id):
            raise AnalysisInputError("retry_from 和 retry_batch_id 必须同时提供。")
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = self.workspace / "_llm_analysis" / "runs" / resolved_run_id
        if run_dir.exists():
            raise AnalysisInputError(f"LLM 分析 run_id 已存在，不能覆盖不可变运行：{resolved_run_id}")
        run_dir.mkdir(parents=True)
        started_at = _now()

        snapshot: AnalysisInputSnapshot | None = None
        profile: ModelProfile | None = None
        projected: list[dict[str, Any]] = []
        quote_candidates: list[dict[str, Any]] = []
        try:
            snapshot = create_input_snapshot(self.workspace, paper_id=paper_id, topic=topic, limit=limit)
            profile = load_model_profile(Path(model_profile_path))
            if provider != profile.provider:
                raise AnalysisInputError(
                    f"provider 与 model profile 不一致：provider={provider!r}, profile={profile.provider!r}。"
                )
            token_counter = self.token_counter or DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
            projected = project_cards(snapshot.materials)
            quote_candidates = build_quote_candidates(projected)
            structure_path = self.workspace / snapshot.paper_id / "structure" / "structure.json"
            plan = plan_paper_analysis(
                paper_id=snapshot.paper_id,
                paper_title=snapshot.paper_title,
                topic=snapshot.topic,
                generation_id=snapshot.generation_id,
                materials=list(snapshot.materials),
                profile=profile,
                token_counter=token_counter,
                structure_path=structure_path,
            )
            section_map = (
                build_section_map(
                    structure_path,
                    list(snapshot.materials),
                    paper_id=snapshot.paper_id,
                    generation_id=snapshot.generation_id,
                )
                if plan.strategy == "section_evidence_batches"
                else None
            )
            _write_input_artifacts_v3(
                run_dir,
                snapshot,
                projected,
                quote_candidates,
                profile,
                token_counter,
                plan,
                section_map,
            )
        except Exception as exc:
            _write_planning_failure_inputs(run_dir, snapshot, projected, quote_candidates, profile)
            failure = _failure("input", exc)
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = {
                "schema_version": RUN_SCHEMA_VERSION,
                "run_id": resolved_run_id,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "paper_id": paper_id,
                "topic": topic,
                "provider": provider,
                "model": profile.request_model if profile else None,
                "error_code": failure["error_code"],
                "error_message": failure["error_message"],
                "failure_count": 1,
                "failure_codes": [failure["error_code"]],
            }
            _write_json(run_dir / "manifest.json", manifest)
            planning_coverage = {
                "planned_material_count": len(snapshot.materials) if snapshot else 0,
                "assessed_material_count": 0,
                "missing_material_ids": [
                    str(material["material_id"])
                    for material in (snapshot.materials if snapshot else [])
                ],
                "failed_batch_ids": [],
                "completed_batch_ids": [],
            }
            _write_json(run_dir / "coverage.json", planning_coverage)
            _write_failed_run_review(run_dir, manifest, snapshot, [], planning_coverage, [failure])
            return _result(run_dir, manifest, batch_count=0, material_count=0)

        assert snapshot is not None and profile is not None
        prompt_sha256 = _sha256_json([batch.prompt_sha256 for batch in plan.evidence_batches])
        schema_sha256 = _sha256_json(
            {
                "evidence": EVIDENCE_BATCH_SCHEMA,
                "section_summary": SECTION_SUMMARY_SCHEMA,
                "paper_analysis_draft": PAPER_ANALYSIS_DRAFT_SCHEMA,
                "non_used_evidence_disposition": NON_USED_EVIDENCE_DISPOSITION_SCHEMA,
                "paper_analysis": PAPER_ANALYSIS_SCHEMA,
                "evidence_claim_support": EVIDENCE_CLAIM_SUPPORT_SCHEMA,
                "statement_role": STATEMENT_ROLE_SCHEMA,
                "statement_support": STATEMENT_SUPPORT_SCHEMA,
            }
        )

        base_manifest = {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": resolved_run_id,
            "status": "running",
            "started_at": started_at,
            "paper_id": snapshot.paper_id,
            "paper_title": snapshot.paper_title,
            "topic": snapshot.topic,
            "scope": snapshot.scope,
            "source_generation_id": snapshot.generation_id,
            "source_material_count": snapshot.source_material_count,
            "material_count": len(snapshot.materials),
            "batch_count": len(plan.evidence_batches),
            "requested_limit": limit,
            "input_sha256": snapshot.input_sha256,
            "topic_sha256": snapshot.topic_sha256,
            "prompt_sha256": prompt_sha256,
            "schema_sha256": schema_sha256,
            "plan_sha256": plan.plan_sha256,
            "model_profile_id": profile.profile_id,
            "model_profile_sha256": profile.sha256,
            "tokenizer_revision": profile.tokenizer_revision,
            "planning_strategy": plan.strategy,
            "provider": provider,
            "model": profile.request_model,
            "parent_run_id": retry_from,
            "retry_batch_id": retry_batch_id,
            "reused_batch_ids": [],
            "executed_batch_ids": [],
        }
        _write_json(run_dir / "manifest.json", base_manifest)

        if dry_run:
            coverage = {
                "scope": snapshot.scope,
                "planned_material_count": len(snapshot.materials),
                "planned_batch_count": len(plan.evidence_batches),
                "planned_material_ids": [material["material_id"] for material in snapshot.materials],
                "unplanned_material_ids": [],
                "multiply_planned_material_ids": [],
                "planning_coverage_ratio": 1.0,
            }
            _write_json(run_dir / "coverage.json", coverage)
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [])
            manifest = {
                **base_manifest,
                "status": "dry_run_completed",
                "finished_at": _now(),
                "provider": "not_called",
                "model": "not_called",
                "batches": [dataclasses.asdict(batch) for batch in plan.evidence_batches],
                "response_usage": _empty_usage(),
            }
            _write_json(run_dir / "manifest.json", manifest)
            return _result(
                run_dir,
                manifest,
                batch_count=len(plan.evidence_batches),
                material_count=len(snapshot.materials),
            )

        failures: list[dict[str, Any]] = []
        batch_results: list[dict[str, Any]] = []
        assessments: list[dict[str, Any]] = []
        evidence_units: list[dict[str, Any]] = []
        usage_rows: list[dict[str, int | None]] = []
        reused_batch_ids: list[str] = []
        executed_batch_ids: list[str] = []
        try:
            client = self._build_client(
                provider=provider,
                api_url=api_url,
                api_key_env=api_key_env,
                model=profile.request_model,
                timeout=timeout,
            )
            resolved_provider = str(getattr(client, "provider", provider))
            resolved_model = str(getattr(client, "model", ""))
            if resolved_provider != profile.provider:
                raise AnalysisInputError(
                    f"analysis client provider 与 model profile 不一致：client={resolved_provider!r}, "
                    f"profile={profile.provider!r}。"
                )
            if resolved_model != profile.request_model:
                raise AnalysisInputError(
                    f"analysis client model 与 model profile 不一致：client={resolved_model!r}, "
                    f"profile={profile.request_model!r}。"
                )
            base_manifest["provider"] = resolved_provider
            base_manifest["model"] = resolved_model
            retry_preparation = self._prepare_retry(
                run_dir=run_dir,
                snapshot=snapshot,
                plan=plan,
                profile=profile,
                retry_from=retry_from,
                retry_batch_id=retry_batch_id,
                provider=resolved_provider,
                model=resolved_model,
                schema_sha256=schema_sha256,
                prompt_sha256=prompt_sha256,
            )
            retry_context = retry_preparation.parent_dir if retry_preparation else None
            if retry_preparation is not None:
                base_manifest["retry_mode"] = retry_preparation.mode
                base_manifest["retry_target_stage"] = retry_preparation.target_stage
        except Exception as exc:
            failure = _failure("run_setup", exc)
            failures.append(failure)
            coverage = _build_coverage(snapshot, [], [], [])
            _write_json(run_dir / "coverage.json", coverage)
            _write_jsonl(run_dir / "audit" / "failures.jsonl", failures)
            manifest = _finish_manifest(
                base_manifest,
                status="failed",
                batch_results=[],
                usage_rows=[],
                failures=failures,
                reused_batch_ids=[],
                executed_batch_ids=[],
            )
            _write_json(run_dir / "manifest.json", manifest)
            _write_failed_run_review(run_dir, manifest, snapshot, [], coverage, failures)
            return _result(run_dir, manifest, len(plan.evidence_batches), len(snapshot.materials))

        materials_by_id = {str(material["material_id"]): material for material in snapshot.materials}
        projected_by_id = {str(card["material_id"]): card for card in projected}
        for planned_request in plan.evidence_batches:
            if retry_context and planned_request.request_id != retry_batch_id:
                reused = _reuse_batch(retry_context, run_dir, planned_request)
                batch_results.extend(reused["results"])
                assessments.extend(reused["assessments"])
                evidence_units.extend(reused["evidence_units"])
                reused_batch_ids.extend(
                    str(row["request_id"])
                    for row in reused["results"]
                    if str(row["request_id"]) not in reused_batch_ids
                )
                continue
            outcome = _execute_evidence_request(
                run_dir,
                client,
                planned_request,
                snapshot,
                materials_by_id,
                projected_by_id,
                projected,
                quote_candidates,
                profile,
                token_counter,
            )
            batch_results.extend(outcome["results"])
            for request_result in outcome["results"]:
                request_id = str(request_result["request_id"])
                if request_id not in executed_batch_ids:
                    executed_batch_ids.append(request_id)
                usage_rows.append(request_result["usage"])
            if outcome["failure"]:
                failures.append(outcome["failure"])
            else:
                assessments.extend(outcome["assessments"])
                evidence_units.extend(outcome["evidence_units"])

        _write_jsonl(
            run_dir / "audit" / "evidence_corrections.jsonl",
            _build_evidence_correction_audit(batch_results),
        )
        output_dir = run_dir / "output"
        _write_jsonl(output_dir / "material_assessments.jsonl", assessments)
        _write_jsonl(output_dir / "evidence_units.jsonl", evidence_units)
        coverage = _build_coverage(snapshot, assessments, evidence_units, batch_results)
        _write_json(run_dir / "coverage.json", coverage)

        successful_batch_count = sum(result["status"] == "completed" for result in batch_results)
        if failures:
            status = "partial" if successful_batch_count else "failed"
            _write_jsonl(run_dir / "audit" / "failures.jsonl", failures)
            manifest = _finish_manifest(
                base_manifest,
                status=status,
                batch_results=batch_results,
                usage_rows=usage_rows,
                failures=failures,
                reused_batch_ids=reused_batch_ids,
                executed_batch_ids=executed_batch_ids,
            )
            _write_json(run_dir / "manifest.json", manifest)
            _write_failed_run_review(run_dir, manifest, snapshot, evidence_units, coverage, failures)
            return _result(run_dir, manifest, len(plan.evidence_batches), len(snapshot.materials))

        if snapshot.scope == "partial_smoke":
            failure = _failure(
                "scope",
                AnalysisInputError("partial_smoke 运行只验证 API，不允许生成完整论文分析。"),
                error_code="scope.partial_smoke_not_publishable",
            )
            failures.append(failure)
            _write_jsonl(run_dir / "audit" / "failures.jsonl", failures)
            manifest = _finish_manifest(
                base_manifest,
                status="partial",
                batch_results=batch_results,
                usage_rows=usage_rows,
                failures=failures,
                reused_batch_ids=reused_batch_ids,
                executed_batch_ids=executed_batch_ids,
            )
            _write_json(run_dir / "manifest.json", manifest)
            _write_failed_run_review(run_dir, manifest, snapshot, evidence_units, coverage, failures)
            return _result(run_dir, manifest, len(plan.evidence_batches), len(snapshot.materials))

        evidence_claim_support_reviews: list[dict[str, Any]] = []
        if not evidence_units:
            synthesis_coverage = {
                "all_evidence_unit_ids": [],
                "used_in_final_analysis_ids": [],
                "not_used_in_final_analysis_ids": [],
                "unresolved_evidence_unit_ids": [],
                "evidence_claim_review_count": 0,
                "directly_supported_evidence_claim_count": 0,
                "partially_supported_evidence_claim_count": 0,
                "unsupported_evidence_claim_count": 0,
                "statement_count": 0,
                "field_aligned_statement_count": 0,
                "misclassified_statement_count": 0,
                "directly_supported_statement_count": 0,
                "grounded_inference_statement_count": 0,
            }
            paper_result = {
                "schema_version": PAPER_RESULT_SCHEMA_VERSION,
                "paper_disposition": "no_usable_evidence",
                "paper_id": snapshot.paper_id,
                "paper_title": snapshot.paper_title,
                "topic": snapshot.topic,
                "analysis": None,
                "evidence_claim_support_reviews": [],
                "statement_role_reviews": [],
                "statement_support_reviews": [],
            }
        else:
            evidence_request_ids = {row.request_id for row in plan.evidence_batches}
            claim_gate = _execute_evidence_claim_support_gate(
                run_dir=run_dir,
                client=client,
                snapshot=snapshot,
                evidence_units=evidence_units,
                profile=profile,
                token_counter=token_counter,
                section_map=section_map,
                reuse_from=(
                    retry_context
                    if retry_context is not None and retry_batch_id not in evidence_request_ids
                    else None
                ),
            )
            batch_results.extend(claim_gate["results"])
            coverage.update(claim_gate["coverage"])
            evidence_claim_support_reviews = claim_gate["reviews"]
            for request_result in claim_gate["results"]:
                request_id = str(request_result["request_id"])
                if request_result.get("reused_from_run_id"):
                    reused_batch_ids.append(request_id)
                else:
                    executed_batch_ids.append(request_id)
                    usage_rows.append(request_result["usage"])
            _write_json(run_dir / "coverage.json", coverage)
            if claim_gate["failure"]:
                failures.append(claim_gate["failure"])
                _write_jsonl(run_dir / "audit" / "failures.jsonl", failures)
                manifest = _finish_manifest(
                    base_manifest,
                    status="failed",
                    batch_results=batch_results,
                    usage_rows=usage_rows,
                    failures=failures,
                    reused_batch_ids=reused_batch_ids,
                    executed_batch_ids=executed_batch_ids,
                )
                _write_json(run_dir / "manifest.json", manifest)
                _write_failed_run_review(
                    run_dir,
                    manifest,
                    snapshot,
                    evidence_units,
                    coverage,
                    failures,
                )
                return _result(
                    run_dir,
                    manifest,
                    len(plan.evidence_batches),
                    len(snapshot.materials),
                )
            synthesis = _execute_synthesis_pipeline(
                run_dir,
                client,
                snapshot,
                evidence_units,
                assessments,
                profile=profile,
                token_counter=token_counter,
                section_map=section_map,
                retry_context=retry_context,
                retry_batch_id=retry_batch_id,
            )
            usage_rows.extend(
                result["usage"]
                for result in synthesis["results"]
                if not result.get("reused_from_run_id")
            )
            batch_results.extend(synthesis["results"])
            for request_result in synthesis["results"]:
                request_id = str(request_result["request_id"])
                destination = (
                    reused_batch_ids
                    if request_result.get("reused_from_run_id")
                    else executed_batch_ids
                )
                if request_id not in destination:
                    destination.append(request_id)
            if synthesis["failure"]:
                failures.append(synthesis["failure"])
                _write_jsonl(run_dir / "audit" / "failures.jsonl", failures)
                manifest = _finish_manifest(
                    base_manifest,
                    status="failed",
                    batch_results=batch_results,
                    usage_rows=usage_rows,
                    failures=failures,
                    reused_batch_ids=reused_batch_ids,
                    executed_batch_ids=executed_batch_ids,
                )
                _write_json(run_dir / "manifest.json", manifest)
                _write_failed_run_review(run_dir, manifest, snapshot, evidence_units, coverage, failures)
                return _result(run_dir, manifest, len(plan.evidence_batches), len(snapshot.materials))
            paper_result = {
                "schema_version": PAPER_RESULT_SCHEMA_VERSION,
                "paper_disposition": "analyzed",
                "paper_id": snapshot.paper_id,
                "paper_title": snapshot.paper_title,
                "topic": snapshot.topic,
                "analysis": synthesis["analysis"],
                "evidence_claim_support_reviews": evidence_claim_support_reviews,
                "statement_role_reviews": synthesis["statement_role_reviews"],
                "statement_support_reviews": synthesis["statement_support_reviews"],
            }
            synthesis_coverage = synthesis["coverage"]

        coverage.update(synthesis_coverage)
        _write_json(run_dir / "coverage.json", coverage)

        if not _generation_is_current(self.workspace, snapshot.paper_id, snapshot.generation_id):
            failure = _failure(
                "input",
                AnalysisInputError("分析期间论文 Card generation 已变化。"),
                error_code="input.source_generation_changed",
            )
            failures.append(failure)
            _write_jsonl(run_dir / "audit" / "failures.jsonl", failures)
            manifest = _finish_manifest(
                base_manifest,
                status="failed",
                batch_results=batch_results,
                usage_rows=usage_rows,
                failures=failures,
                reused_batch_ids=reused_batch_ids,
                executed_batch_ids=executed_batch_ids,
            )
            _write_json(run_dir / "manifest.json", manifest)
            _write_failed_run_review(run_dir, manifest, snapshot, evidence_units, coverage, failures)
            return _result(run_dir, manifest, len(plan.evidence_batches), len(snapshot.materials))

        _write_json(output_dir / "paper_analysis.json", paper_result)
        _write_jsonl(
            output_dir / "evidence_claim_support_reviews.jsonl",
            paper_result["evidence_claim_support_reviews"],
        )
        _write_jsonl(
            output_dir / "statement_role_reviews.jsonl",
            paper_result["statement_role_reviews"],
        )
        _write_jsonl(
            output_dir / "statement_support_reviews.jsonl",
            paper_result["statement_support_reviews"],
        )
        _write_jsonl(run_dir / "audit" / "failures.jsonl", [])
        write_analysis_review(
            run_dir / "review",
            run_id=resolved_run_id,
            paper_id=snapshot.paper_id,
            paper_title=snapshot.paper_title,
            topic=snapshot.topic,
            evidence_units=evidence_units,
            paper_result=paper_result,
            coverage=coverage,
        )
        manifest = _finish_manifest(
            base_manifest,
            status="completed",
            batch_results=batch_results,
            usage_rows=usage_rows,
            failures=[],
            reused_batch_ids=reused_batch_ids,
            executed_batch_ids=executed_batch_ids,
        )
        manifest["paper_disposition"] = paper_result["paper_disposition"]
        _write_json(run_dir / "manifest.json", manifest)
        return _result(run_dir, manifest, len(plan.evidence_batches), len(snapshot.materials))

    def _build_client(
        self,
        *,
        provider: str,
        api_url: str | None,
        api_key_env: str,
        model: str | None,
        timeout: int,
    ) -> Any:
        if self.analysis_client is not None:
            return self.analysis_client
        if provider == "openai-compatible":
            return OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=model,
                timeout=timeout,
            )
        raise AnalysisInputError(f"不支持的 LLM 分析 provider：{provider}")

    def _prepare_retry(
        self,
        *,
        run_dir: Path,
        snapshot: AnalysisInputSnapshot,
        plan: PaperAnalysisPlan,
        profile: ModelProfile,
        retry_from: str | None,
        retry_batch_id: str | None,
        provider: str,
        model: str,
        schema_sha256: str,
        prompt_sha256: str,
    ) -> RetryPreparation | None:
        del run_dir
        if not retry_from:
            return None
        parent_dir = self.workspace / "_llm_analysis" / "runs" / retry_from
        manifest_path = parent_dir / "manifest.json"
        if not manifest_path.exists():
            raise AnalysisInputError(f"找不到父运行：{retry_from}")
        parent = _read_json(manifest_path)
        known_batch_ids = {batch.request_id for batch in plan.evidence_batches}
        retry_is_evidence = str(retry_batch_id) in known_batch_ids
        failed_ids = {
            str(row.get("batch_id"))
            for row in _read_jsonl(parent_dir / "audit" / "failures.jsonl")
            if row.get("batch_id")
        }
        retry_mode = "failed_batch"
        target_stage = "evidence"
        if retry_is_evidence and retry_batch_id not in failed_ids:
            raise AnalysisInputError(f"只允许显式重跑父运行中的失败批次：{retry_batch_id}")
        if not retry_is_evidence:
            synthesis_plan_path = parent_dir / "plan" / "synthesis_plan.json"
            if not synthesis_plan_path.exists():
                raise AnalysisInputError(f"父运行不存在批次：{retry_batch_id}")
            synthesis_plan = _read_json(synthesis_plan_path)
            synthesis_requests = {
                str(row.get("request_id")): row
                for row in synthesis_plan.get("requests", [])
                if row.get("request_id")
            }
            target = synthesis_requests.get(str(retry_batch_id))
            if target is None:
                raise AnalysisInputError(f"父运行不存在批次：{retry_batch_id}")
            target_stage = str(target.get("stage", ""))
            if synthesis_plan.get("strategy") != "direct_paper_synthesis" or target.get("stage") not in {
                "paper_synthesis",
                "evidence_disposition",
                "statement_role",
                "statement_support",
            }:
                raise AnalysisInputError("当前只支持显式重跑直接论文综合事务；章节摘要链路必须重新规划运行。")
            if retry_batch_id not in failed_ids:
                ordered_ids = [
                    str(row["request_id"])
                    for row in synthesis_plan.get("requests", [])
                    if row.get("request_id")
                ]
                target_index = ordered_ids.index(str(retry_batch_id))
                later_failed_ids = set(ordered_ids[target_index + 1 :]) & failed_ids
                result_path = parent_dir / "batches" / str(retry_batch_id) / "result.json"
                target_result = _read_json(result_path) if result_path.exists() else {}
                if (
                    parent.get("status") != "failed"
                    or target_stage not in {"paper_synthesis", "evidence_disposition"}
                    or target_result.get("status") != "completed"
                    or not later_failed_ids
                ):
                    raise AnalysisInputError(
                        f"只允许显式重跑失败批次，或失败运行中位于失败阶段之前的已完成综合批次：{retry_batch_id}"
                    )
                retry_mode = "restart_from_completed_synthesis"
        comparisons = {
            "source_generation_id": snapshot.generation_id,
            "input_sha256": snapshot.input_sha256,
            "prompt_sha256": prompt_sha256,
            "plan_sha256": plan.plan_sha256,
            "model_profile_sha256": profile.sha256,
            "tokenizer_revision": profile.tokenizer_revision,
            "provider": provider,
            "model": model,
        }
        if retry_is_evidence:
            comparisons["schema_sha256"] = schema_sha256
        mismatched = [key for key, value in comparisons.items() if parent.get(key) != value]
        if mismatched:
            raise AnalysisInputError(f"失败批次重跑条件与父运行不一致：{mismatched}")
        requests_by_id = {batch.request_id: batch for batch in plan.evidence_batches}
        reusable_ids = known_batch_ids - ({str(retry_batch_id)} if retry_is_evidence else set())
        for batch_id in reusable_ids:
            result_path = parent_dir / "batches" / batch_id / "result.json"
            if not result_path.exists():
                raise AnalysisInputError(f"父运行批次不可复用：{batch_id}")
            result = _read_json(result_path)
            completed = result.get("status") == "completed"
            resolution_path = parent_dir / "batches" / batch_id / "resolution.json"
            corrected = False
            if not completed and resolution_path.exists():
                resolution = _read_json(resolution_path)
                correction_request_id = str(resolution.get("correction_request_id") or "")
                correction_result_path = (
                    parent_dir / "batches" / correction_request_id / "result.json"
                )
                corrected = (
                    resolution.get("status") == "corrected"
                    and correction_request_id.startswith("evidence_batch_correction_")
                    and correction_result_path.exists()
                    and _read_json(correction_result_path).get("status") == "completed"
                )
            if (
                not (completed or corrected)
                or result.get("prompt_sha256") != requests_by_id[batch_id].prompt_sha256
            ):
                raise AnalysisInputError(f"父运行批次不可复用：{batch_id}")
        return RetryPreparation(
            parent_dir=parent_dir,
            mode=retry_mode,
            target_stage=target_stage,
        )


class LLMStatementRevisionRunner:
    """对支持核验失败的论文分析执行一次显式、受约束的模型修订。"""

    def __init__(
        self,
        workspace: str | Path,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        source_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 120,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        retry_support_from: str | None = None,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = self.workspace / "_llm_analysis" / "runs" / resolved_run_id
        if run_dir.exists():
            raise AnalysisInputError(f"LLM 修订 run_id 已存在，不能覆盖不可变运行：{resolved_run_id}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        source: StatementRevisionSource | None = None
        profile: ModelProfile | None = None
        try:
            source = _load_statement_revision_source(self.workspace, source_run_id)
            profile = load_model_profile(Path(model_profile_path))
            if provider != profile.provider:
                raise AnalysisInputError(
                    f"provider 与 model profile 不一致：provider={provider!r}, profile={profile.provider!r}。"
                )
            if source.manifest.get("provider") != provider:
                raise AnalysisInputError("修订 provider 与来源运行不一致。")
            if source.manifest.get("model") != profile.request_model:
                raise AnalysisInputError("修订 model 与来源运行不一致。")
            if source.manifest.get("tokenizer_revision") != profile.tokenizer_revision:
                raise AnalysisInputError("修订 tokenizer revision 与来源运行不一致。")
            token_counter = self.token_counter or DeepSeekV4TokenCounter(
                profile,
                DEFAULT_TOKENIZER_CACHE,
            )
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=profile.request_model,
                timeout=timeout,
            )
            if str(getattr(client, "provider", provider)) != profile.provider:
                raise AnalysisInputError("analysis client provider 与 model profile 不一致。")
            if str(getattr(client, "model", "")) != profile.request_model:
                raise AnalysisInputError("analysis client model 与 model profile 不一致。")
            revision_system, revision_user = build_statement_revision_prompts(
                paper_id=source.snapshot.paper_id,
                paper_title=source.snapshot.paper_title,
                topic=source.snapshot.topic,
                original_analysis=source.original_analysis,
                blocking_records=list(source.blocking_records),
                evidence_units=list(source.evidence_units),
            )
            revision_statement_ids = tuple(
                str(row["statement_id"]) for row in source.blocking_records
            )
            revision_evidence_ids = tuple(
                sorted(
                    {
                        str(evidence_id)
                        for record in source.blocking_records
                        for evidence_id in record["evidence_unit_ids"]
                    }
                )
            )
            revision_request = plan_synthesis_request(
                generation_id=source.snapshot.generation_id,
                stage="statement_revision",
                section_id=None,
                evidence_unit_ids=revision_evidence_ids,
                statement_ids=revision_statement_ids,
                system_prompt=revision_system,
                user_prompt=revision_user,
                max_output_tokens=profile.statement_revision_max_output_tokens,
                profile=profile,
                token_counter=token_counter,
            )
            support_retry_parent = (
                _prepare_statement_revision_support_retry(
                    self.workspace,
                    retry_support_from,
                    source=source,
                    planned_revision=revision_request,
                )
                if retry_support_from
                else None
            )
            base_manifest = _statement_revision_base_manifest(
                run_id=resolved_run_id,
                started_at=started_at,
                source=source,
                profile=profile,
                provider=provider,
            )
            if support_retry_parent is not None:
                base_manifest["retry_mode"] = "statement_support_only"
                base_manifest["source_revision_run_id"] = support_retry_parent.name
            _write_statement_revision_inputs(
                run_dir,
                source=source,
                profile=profile,
                revision_system=revision_system,
                revision_user=revision_user,
            )
            _write_revision_plan(run_dir, [revision_request], status="planned")
            _write_json(run_dir / "manifest.json", base_manifest)
        except Exception as exc:
            failure = _failure("statement_revision_setup", exc)
            manifest = {
                "schema_version": REVISION_RUN_SCHEMA_VERSION,
                "run_id": resolved_run_id,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "source_run_id": source_run_id,
                "paper_id": source.snapshot.paper_id if source else None,
                "paper_title": source.snapshot.paper_title if source else None,
                "topic": source.snapshot.topic if source else None,
                "provider": provider,
                "model": profile.request_model if profile else None,
                "failure_count": 1,
                "failure_codes": [failure["error_code"]],
                "response_usage": _empty_usage(),
            }
            coverage = _empty_revision_coverage(source)
            _write_json(run_dir / "coverage.json", coverage)
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            _write_json(run_dir / "manifest.json", manifest)
            _write_failed_run_review(
                run_dir,
                manifest,
                source.snapshot if source else None,
                list(source.evidence_units) if source else [],
                coverage,
                [failure],
            )
            return _result(
                run_dir,
                manifest,
                batch_count=0,
                material_count=len(source.snapshot.materials) if source else 0,
            )

        assert source is not None and profile is not None
        batch_results: list[dict[str, Any]] = []
        revision_outcome = (
            _reuse_statement_revision_request(
                support_retry_parent,
                run_dir,
                source,
                revision_request,
            )
            if support_retry_parent is not None
            else _execute_statement_revision_request(
                run_dir,
                client,
                source,
                revision_request,
                revision_system,
                revision_user,
                profile=profile,
            )
        )
        batch_results.append(revision_outcome["result"])
        if revision_outcome["failure"]:
            return _finish_failed_statement_revision_run(
                run_dir,
                base_manifest,
                source,
                batch_results,
                revision_outcome["failure"],
            )

        validated_revision = revision_outcome["revision"]
        revised_analysis = revision_outcome["analysis"]
        _write_json(run_dir / "output" / "revision.json", validated_revision)
        _write_json(run_dir / "output" / "revised_analysis_candidate.json", revised_analysis)
        revised_records = build_statement_records(revised_analysis)
        role_system, role_user = build_statement_role_prompts(
            paper_id=source.snapshot.paper_id,
            paper_title=source.snapshot.paper_title,
            topic=source.snapshot.topic,
            study_type=str(revised_analysis["study_type"]),
            research_focus=str(revised_analysis["research_focus"]["statement"]),
            statement_records=revised_records,
        )
        support_system, support_user = build_statement_support_prompts(
            paper_id=source.snapshot.paper_id,
            paper_title=source.snapshot.paper_title,
            topic=source.snapshot.topic,
            statement_records=revised_records,
            evidence_units=list(source.evidence_units),
        )
        support_evidence_ids = tuple(
            sorted(
                {
                    str(evidence_id)
                    for record in revised_records
                    for evidence_id in record["evidence_unit_ids"]
                }
            )
        )
        revised_statement_ids = tuple(str(row["statement_id"]) for row in revised_records)
        try:
            role_request = plan_synthesis_request(
                generation_id=source.snapshot.generation_id,
                stage="statement_role",
                section_id=None,
                evidence_unit_ids=(),
                statement_ids=revised_statement_ids,
                system_prompt=role_system,
                user_prompt=role_user,
                max_output_tokens=profile.statement_role_max_output_tokens,
                profile=profile,
                token_counter=token_counter,
            )
            support_request = plan_synthesis_request(
                generation_id=source.snapshot.generation_id,
                stage="statement_support",
                section_id=None,
                evidence_unit_ids=support_evidence_ids,
                statement_ids=revised_statement_ids,
                system_prompt=support_system,
                user_prompt=support_user,
                max_output_tokens=profile.statement_support_max_output_tokens,
                profile=profile,
                token_counter=token_counter,
            )
            _write_text(
                run_dir / "prompts" / "statement_role_system.md",
                role_system + "\n",
            )
            _write_json(
                run_dir / "schemas" / "statement_role.schema.json",
                build_statement_role_schema(list(revised_statement_ids)),
            )
            _write_text(
                run_dir / "prompts" / "statement_support_system.md",
                support_system + "\n",
            )
            _write_revision_plan(
                run_dir,
                [revision_request, role_request, support_request],
                status="planned",
            )
        except Exception as exc:
            failure = _failure("statement_revalidation_planning", exc)
            return _finish_failed_statement_revision_run(
                run_dir,
                base_manifest,
                source,
                batch_results,
                failure,
                revised_analysis=revised_analysis,
            )

        role_outcome = (
            _reuse_statement_role_request(
                support_retry_parent,
                run_dir,
                role_request,
                statement_records=revised_records,
            )
            if support_retry_parent is not None
            else _execute_statement_role_request(
                run_dir,
                client,
                source.snapshot,
                role_request,
                role_system,
                role_user,
                statement_records=revised_records,
                profile=profile,
            )
        )
        batch_results.append(role_outcome["result"])
        _write_jsonl(
            run_dir / "output" / "statement_role_reviews.jsonl",
            role_outcome["reviews"],
        )
        if role_outcome["failure"]:
            _write_revision_plan(
                run_dir,
                [revision_request, role_request, support_request],
                status="failed",
            )
            return _finish_failed_statement_revision_run(
                run_dir,
                base_manifest,
                source,
                batch_results,
                role_outcome["failure"],
                revised_analysis=revised_analysis,
            )

        support_outcome = _execute_statement_support_request(
            run_dir,
            client,
            source.snapshot,
            support_request,
            support_system,
            support_user,
            statement_records=revised_records,
            profile=profile,
        )
        batch_results.append(support_outcome["result"])
        if support_outcome["failure"]:
            _write_revision_plan(
                run_dir,
                [revision_request, role_request, support_request],
                status="failed",
            )
            return _finish_failed_statement_revision_run(
                run_dir,
                base_manifest,
                source,
                batch_results,
                support_outcome["failure"],
                revised_analysis=revised_analysis,
            )

        if not _generation_is_current(
            self.workspace,
            source.snapshot.paper_id,
            source.snapshot.generation_id,
        ):
            failure = _failure(
                "statement_revision_publish",
                AnalysisInputError("修订期间论文 Card generation 已变化。"),
                error_code="input.source_generation_changed",
            )
            return _finish_failed_statement_revision_run(
                run_dir,
                base_manifest,
                source,
                batch_results,
                failure,
                revised_analysis=revised_analysis,
            )

        role_reviews = role_outcome["reviews"]
        reviews = support_outcome["reviews"]
        coverage = _build_statement_revision_coverage(
            source,
            revised_analysis,
            validated_revision,
            role_reviews,
            reviews,
            batch_results,
        )
        paper_result = {
            "schema_version": PAPER_RESULT_SCHEMA_VERSION,
            "paper_disposition": "analyzed",
            "paper_id": source.snapshot.paper_id,
            "paper_title": source.snapshot.paper_title,
            "topic": source.snapshot.topic,
            "analysis": revised_analysis,
            "evidence_claim_support_reviews": list(
                source.evidence_claim_support_reviews
            ),
            "statement_role_reviews": role_reviews,
            "statement_support_reviews": reviews,
        }
        _write_json(run_dir / "output" / "paper_analysis.json", paper_result)
        _write_jsonl(
            run_dir / "output" / "evidence_claim_support_reviews.jsonl",
            list(source.evidence_claim_support_reviews),
        )
        _write_jsonl(run_dir / "output" / "statement_support_reviews.jsonl", reviews)
        _write_json(run_dir / "coverage.json", coverage)
        _write_jsonl(run_dir / "audit" / "failures.jsonl", [])
        write_analysis_review(
            run_dir / "review",
            run_id=resolved_run_id,
            paper_id=source.snapshot.paper_id,
            paper_title=source.snapshot.paper_title,
            topic=source.snapshot.topic,
            evidence_units=list(source.evidence_units),
            paper_result=paper_result,
            coverage=coverage,
        )
        write_statement_revision_review(
            run_dir / "review",
            source_run_id=source.run_dir.name,
            run_id=resolved_run_id,
            status="completed",
            blocking_records=list(source.blocking_records),
            revision=validated_revision,
            support_reviews=reviews,
        )
        _write_revision_plan(
            run_dir,
            [revision_request, role_request, support_request],
            status="completed",
        )
        manifest = {
            **base_manifest,
            "status": "completed",
            "finished_at": _now(),
            "paper_disposition": "analyzed",
            "failure_count": 0,
            "failure_codes": [],
            "batches": batch_results,
            **_revision_request_audit(batch_results),
        }
        _write_json(run_dir / "manifest.json", manifest)
        return _result(
            run_dir,
            manifest,
            batch_count=len(batch_results),
            material_count=len(source.snapshot.materials),
        )


class LLMSemanticAuditRunner:
    """只读核验既有论文分析候选中的证据观点和字段职责。"""

    def __init__(
        self,
        workspace: str | Path,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        source_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 120,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = self.workspace / "_llm_analysis" / "semantic_audits" / resolved_run_id
        if run_dir.exists():
            raise AnalysisInputError(f"LLM 语义审计 run_id 已存在，不能覆盖不可变运行：{resolved_run_id}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        source: SemanticAuditSource | None = None
        profile: ModelProfile | None = None
        statement_records: list[dict[str, Any]] = []
        try:
            source = _load_semantic_audit_source(self.workspace, source_run_id)
            profile = load_model_profile(Path(model_profile_path))
            if provider != profile.provider:
                raise AnalysisInputError(
                    f"provider 与 model profile 不一致：provider={provider!r}, profile={profile.provider!r}。"
                )
            if source.manifest.get("provider") != provider:
                raise AnalysisInputError("语义审计 provider 与来源运行不一致。")
            if source.manifest.get("model") != profile.request_model:
                raise AnalysisInputError("语义审计 model 与来源运行不一致。")
            if source.manifest.get("tokenizer_revision") != profile.tokenizer_revision:
                raise AnalysisInputError("语义审计 tokenizer revision 与来源运行不一致。")
            token_counter = self.token_counter or DeepSeekV4TokenCounter(
                profile,
                DEFAULT_TOKENIZER_CACHE,
            )
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=profile.request_model,
                timeout=timeout,
            )
            if str(getattr(client, "provider", provider)) != profile.provider:
                raise AnalysisInputError("analysis client provider 与 model profile 不一致。")
            if str(getattr(client, "model", "")) != profile.request_model:
                raise AnalysisInputError("analysis client model 与 model profile 不一致。")

            statement_records = build_statement_records(source.analysis)
            role_system, role_user = build_statement_role_prompts(
                paper_id=source.snapshot.paper_id,
                paper_title=source.snapshot.paper_title,
                topic=source.snapshot.topic,
                study_type=str(source.analysis["study_type"]),
                research_focus=str(source.analysis["research_focus"]["statement"]),
                statement_records=statement_records,
            )
            statement_ids = tuple(str(row["statement_id"]) for row in statement_records)
            role_request = plan_synthesis_request(
                generation_id=source.snapshot.generation_id,
                stage="statement_role",
                section_id=None,
                evidence_unit_ids=(),
                statement_ids=statement_ids,
                system_prompt=role_system,
                user_prompt=role_user,
                max_output_tokens=profile.statement_role_max_output_tokens,
                profile=profile,
                token_counter=token_counter,
            )
            _write_semantic_audit_inputs(
                run_dir,
                source=source,
                profile=profile,
                token_counter=token_counter,
                role_system=role_system,
                role_request=role_request,
                statement_ids=list(statement_ids),
            )
            base_manifest = _semantic_audit_base_manifest(
                run_id=resolved_run_id,
                started_at=started_at,
                source=source,
                profile=profile,
                provider=provider,
            )
            _write_json(run_dir / "manifest.json", base_manifest)
        except Exception as exc:
            failure = _failure("semantic_audit_setup", exc)
            coverage = _empty_semantic_audit_coverage(source, statement_records)
            manifest = {
                "schema_version": SEMANTIC_AUDIT_RUN_SCHEMA_VERSION,
                "run_id": resolved_run_id,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "source_run_id": source_run_id,
                "paper_id": source.snapshot.paper_id if source else None,
                "paper_title": source.snapshot.paper_title if source else None,
                "topic": source.snapshot.topic if source else None,
                "scope": "semantic_audit_only",
                "paper_disposition": "not_published",
                "provider": provider,
                "model": profile.request_model if profile else None,
                "failure_count": 1,
                "failure_codes": [failure["error_code"]],
                "response_usage": _empty_usage(),
                "batches": [],
            }
            _write_json(run_dir / "coverage.json", coverage)
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            _write_json(run_dir / "manifest.json", manifest)
            write_semantic_audit_review(
                run_dir / "review",
                source_run_id=source_run_id,
                run_id=resolved_run_id,
                paper_id=source.snapshot.paper_id if source else "未记录",
                paper_title=source.snapshot.paper_title if source else "未记录",
                evidence_units=list(source.evidence_units) if source else [],
                statement_records=statement_records,
                evidence_claim_reviews=[],
                statement_role_reviews=[],
                failures=[failure],
            )
            return _semantic_audit_result(run_dir, manifest, material_count=0 if source is None else len(source.snapshot.materials))

        assert source is not None and profile is not None
        claim_outcome = _execute_evidence_claim_support_gate(
            run_dir=run_dir,
            client=client,
            snapshot=source.snapshot,
            evidence_units=list(source.evidence_units),
            profile=profile,
            token_counter=token_counter,
        )
        role_outcome = _execute_statement_role_request(
            run_dir,
            client,
            source.snapshot,
            role_request,
            role_system,
            role_user,
            statement_records=statement_records,
            profile=profile,
        )
        _write_jsonl(
            run_dir / "output" / "statement_role_reviews.jsonl",
            role_outcome["reviews"],
        )
        _write_statement_role_audit_plan(
            run_dir,
            role_request,
            status="failed" if role_outcome["failure"] else "completed",
        )
        batch_results = [*claim_outcome["results"], role_outcome["result"]]
        failures = [
            row
            for row in (claim_outcome["failure"], role_outcome["failure"])
            if row is not None
        ]
        coverage = _build_semantic_audit_coverage(
            source,
            statement_records,
            claim_outcome["reviews"],
            role_outcome["reviews"],
            batch_results,
        )
        status = "completed" if not failures else "failed"
        manifest = {
            **base_manifest,
            "status": status,
            "finished_at": _now(),
            "failure_count": len(failures),
            "failure_codes": [str(row["error_code"]) for row in failures],
            "response_usage": _sum_usage([row["usage"] for row in batch_results]),
            "batches": batch_results,
            "executed_batch_ids": [str(row["request_id"]) for row in batch_results],
        }
        _write_json(run_dir / "coverage.json", coverage)
        _write_jsonl(run_dir / "audit" / "failures.jsonl", failures)
        _write_json(run_dir / "manifest.json", manifest)
        write_semantic_audit_review(
            run_dir / "review",
            source_run_id=source.run_dir.name,
            run_id=resolved_run_id,
            paper_id=source.snapshot.paper_id,
            paper_title=source.snapshot.paper_title,
            evidence_units=list(source.evidence_units),
            statement_records=statement_records,
            evidence_claim_reviews=claim_outcome["reviews"],
            statement_role_reviews=role_outcome["reviews"],
            failures=failures,
        )
        adjudicable_failure_codes = {
            "support.evidence_claim_not_fully_supported",
            "role.statement_misclassified",
        }
        has_semantic_blockers = any(
            row.get("verdict") != "directly_supported"
            for row in claim_outcome["reviews"]
        ) or any(
            row.get("verdict") != "field_aligned"
            for row in role_outcome["reviews"]
        )
        if has_semantic_blockers and all(
            str(row["error_code"]) in adjudicable_failure_codes for row in failures
        ):
            initialize_semantic_decisions(
                self.workspace,
                resolved_run_id,
            )
        return _semantic_audit_result(
            run_dir,
            manifest,
            material_count=len(source.snapshot.materials),
        )


class LLMSemanticAutoResolutionRunner:
    """对语义审计阻断项执行受约束自动处置，并只升级残余问题。"""

    def __init__(
        self,
        workspace: str | Path,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        audit_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 120,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = (
            self.workspace
            / "_llm_analysis"
            / "semantic_auto_resolutions"
            / resolved_run_id
        )
        if run_dir.exists():
            raise AnalysisInputError(
                f"语义自动处置 run_id 已存在，不能覆盖不可变运行：{resolved_run_id}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        source: SemanticAutoResolutionSource | None = None
        profile: ModelProfile | None = None
        client: Any | None = None
        context: dict[str, Any] | None = None
        planned: PlannedSynthesisRequest | None = None
        system_prompt = ""
        user_prompt = ""
        try:
            source = _load_semantic_auto_resolution_source(
                self.workspace,
                audit_run_id,
            )
            profile = load_model_profile(Path(model_profile_path))
            if provider != profile.provider:
                raise AnalysisInputError("语义自动处置 provider 与 model profile 不一致。")
            if source.audit_manifest.get("provider") != provider:
                raise AnalysisInputError("语义自动处置 provider 与来源审计不一致。")
            if source.audit_manifest.get("model") != profile.request_model:
                raise AnalysisInputError("语义自动处置 model 与来源审计不一致。")
            if source.audit_manifest.get("tokenizer_revision") != profile.tokenizer_revision:
                raise AnalysisInputError("语义自动处置 tokenizer revision 与来源审计不一致。")
            token_counter = self.token_counter or DeepSeekV4TokenCounter(
                profile,
                DEFAULT_TOKENIZER_CACHE,
            )
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=profile.request_model,
                timeout=timeout,
            )
            if str(getattr(client, "provider", provider)) != profile.provider:
                raise AnalysisInputError("analysis client provider 与 model profile 不一致。")
            if str(getattr(client, "model", "")) != profile.request_model:
                raise AnalysisInputError("analysis client model 与 model profile 不一致。")

            context = build_auto_resolution_context(
                materials=list(source.snapshot.materials),
                evidence_units=list(source.evidence_units),
                analysis=source.analysis,
                evidence_claim_reviews=list(source.evidence_claim_reviews),
                statement_role_reviews=list(source.statement_role_reviews),
            )
            system_prompt, user_prompt = build_auto_resolution_prompts(
                paper_id=source.snapshot.paper_id,
                paper_title=source.snapshot.paper_title,
                topic=source.snapshot.topic,
                context=context,
            )
            target_evidence_ids = tuple(
                str(row["target_evidence_unit_id"])
                for row in context["evidence_issues"]
            )
            target_statement_ids = tuple(
                str(row["target_statement_id"])
                for row in context["statement_issues"]
            )
            planned = plan_synthesis_request(
                generation_id=source.snapshot.generation_id,
                stage="semantic_auto_resolution",
                section_id=None,
                evidence_unit_ids=target_evidence_ids,
                statement_ids=target_statement_ids,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=profile.evidence_revision_max_output_tokens,
                profile=profile,
                token_counter=token_counter,
            )
            _write_semantic_auto_resolution_inputs(
                run_dir,
                source=source,
                profile=profile,
                context=context,
                planned=planned,
                system_prompt=system_prompt,
            )
            base_manifest = _semantic_auto_resolution_base_manifest(
                run_id=resolved_run_id,
                started_at=started_at,
                source=source,
                profile=profile,
                provider=provider,
                context=context,
            )
            _write_json(run_dir / "manifest.json", base_manifest)
        except Exception as exc:
            failure = _failure("semantic_auto_resolution_setup", exc)
            manifest = {
                "schema_version": SEMANTIC_AUTO_RESOLUTION_RUN_SCHEMA_VERSION,
                "run_id": resolved_run_id,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "source_audit_run_id": audit_run_id,
                "paper_id": source.snapshot.paper_id if source else None,
                "paper_title": source.snapshot.paper_title if source else None,
                "scope": "semantic_auto_resolution_only",
                "paper_disposition": "not_published",
                "provider": provider,
                "model": profile.request_model if profile else None,
                "failure_count": 1,
                "failure_codes": [failure["error_code"]],
                "response_usage": _empty_usage(),
                "batches": [],
            }
            _write_json(run_dir / "coverage.json", _empty_semantic_auto_resolution_coverage(context))
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            _write_json(run_dir / "manifest.json", manifest)
            _write_semantic_auto_resolution_review(
                run_dir,
                manifest=manifest,
                evidence_actions=[],
                statement_actions=[],
                residual_issues=[],
                failures=[failure],
            )
            return _semantic_auto_resolution_result(run_dir, manifest, source)

        assert source is not None and profile is not None and client is not None
        assert context is not None and planned is not None
        batch_results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        plan_outcome = _execute_semantic_auto_resolution_request(
            run_dir,
            client,
            source.snapshot,
            planned,
            system_prompt,
            user_prompt,
            context=context,
            profile=profile,
        )
        batch_results.append(plan_outcome["result"])
        if plan_outcome["failure"]:
            failures.append(plan_outcome["failure"])
            return _finish_semantic_auto_resolution_run(
                run_dir,
                base_manifest,
                source,
                context,
                batch_results,
                failures,
                applied=None,
                evidence_claim_reviews=[],
                statement_role_reviews=[],
                statement_support_reviews=[],
                residual_issues=[],
            )

        try:
            applied = apply_auto_resolution_plan(
                plan_outcome["plan"],
                context=context,
                materials=list(source.snapshot.materials),
                evidence_units=list(source.evidence_units),
                analysis=source.analysis,
                generation_id=source.snapshot.generation_id,
                request_id=planned.request_id,
            )
            _write_json(run_dir / "output" / "auto_resolution_plan.json", applied["plan"])
            _write_jsonl(run_dir / "output" / "evidence_actions.jsonl", applied["evidence_actions"])
            _write_jsonl(run_dir / "output" / "statement_actions.jsonl", applied["statement_actions"])
            _write_json(run_dir / "output" / "auto_resolved_analysis_candidate.json", applied["analysis"])
            _write_jsonl(run_dir / "output" / "evidence_units.jsonl", applied["evidence_units"])
        except Exception as exc:
            failures.append(_failure("semantic_auto_resolution_apply", exc))
            return _finish_semantic_auto_resolution_run(
                run_dir,
                base_manifest,
                source,
                context,
                batch_results,
                failures,
                applied=None,
                evidence_claim_reviews=[],
                statement_role_reviews=[],
                statement_support_reviews=[],
                residual_issues=[],
            )

        source_claim_reviews_by_id = {
            str(row["evidence_unit_id"]): row
            for row in source.evidence_claim_reviews
        }
        automatic_override_ids = set(applied["automatic_override_evidence_ids"])
        automatic_override_reviews = {
            evidence_id: source_claim_reviews_by_id[evidence_id]
            for evidence_id in automatic_override_ids
        }
        claim_gate = _execute_evidence_claim_support_gate(
            run_dir=run_dir,
            client=client,
            snapshot=source.snapshot,
            evidence_units=applied["evidence_units"],
            profile=profile,
            token_counter=token_counter,
            accepted_override_ids=automatic_override_ids,
            accepted_override_reviews=automatic_override_reviews,
        )
        batch_results.extend(claim_gate["results"])
        if claim_gate["failure"]:
            failures.append(claim_gate["failure"])

        statement_records = build_statement_records(applied["analysis"])
        role_system, role_user = build_statement_role_prompts(
            paper_id=source.snapshot.paper_id,
            paper_title=source.snapshot.paper_title,
            topic=source.snapshot.topic,
            study_type=str(applied["analysis"]["study_type"]),
            research_focus=str(applied["analysis"]["research_focus"]["statement"]),
            statement_records=statement_records,
        )
        try:
            role_request = plan_synthesis_request(
                generation_id=source.snapshot.generation_id,
                stage="statement_role",
                section_id=None,
                evidence_unit_ids=(),
                statement_ids=tuple(str(row["statement_id"]) for row in statement_records),
                system_prompt=role_system,
                user_prompt=role_user,
                max_output_tokens=profile.statement_role_max_output_tokens,
                profile=profile,
                token_counter=token_counter,
            )
            role_outcome = _execute_statement_role_request(
                run_dir,
                client,
                source.snapshot,
                role_request,
                role_system,
                role_user,
                statement_records=statement_records,
                profile=profile,
            )
            batch_results.append(role_outcome["result"])
            if role_outcome["failure"]:
                failures.append(role_outcome["failure"])
        except Exception as exc:
            role_outcome = {
                "reviews": [],
                "failure": _failure("statement_role_planning", exc),
            }
            failures.append(role_outcome["failure"])
        _write_jsonl(
            run_dir / "output" / "statement_role_reviews.jsonl",
            role_outcome["reviews"],
        )

        statement_support_reviews: list[dict[str, Any]] = []
        if claim_gate["failure"] is None and role_outcome["failure"] is None:
            support_system, support_user = build_statement_support_prompts(
                paper_id=source.snapshot.paper_id,
                paper_title=source.snapshot.paper_title,
                topic=source.snapshot.topic,
                statement_records=statement_records,
                evidence_units=applied["evidence_units"],
            )
            try:
                support_request = plan_synthesis_request(
                    generation_id=source.snapshot.generation_id,
                    stage="statement_support",
                    section_id=None,
                    evidence_unit_ids=tuple(
                        sorted(
                            {
                                str(evidence_id)
                                for row in statement_records
                                for evidence_id in row["evidence_unit_ids"]
                            }
                        )
                    ),
                    statement_ids=tuple(str(row["statement_id"]) for row in statement_records),
                    system_prompt=support_system,
                    user_prompt=support_user,
                    max_output_tokens=profile.statement_support_max_output_tokens,
                    profile=profile,
                    token_counter=token_counter,
                )
                support_outcome = _execute_statement_support_request(
                    run_dir,
                    client,
                    source.snapshot,
                    support_request,
                    support_system,
                    support_user,
                    statement_records=statement_records,
                    profile=profile,
                )
                batch_results.append(support_outcome["result"])
                statement_support_reviews = support_outcome["reviews"]
                if support_outcome["failure"]:
                    failures.append(support_outcome["failure"])
            except Exception as exc:
                failures.append(_failure("statement_support_planning", exc))
        _write_jsonl(
            run_dir / "output" / "statement_support_reviews.jsonl",
            statement_support_reviews,
        )

        residual_claim_reviews = [
            row
            for row in claim_gate["reviews"]
            if row.get("verdict") != "directly_supported"
            and not (
                str(row.get("evidence_unit_id") or "") in automatic_override_ids
                and _evidence_claim_override_matches(
                    row,
                    automatic_override_reviews.get(str(row.get("evidence_unit_id") or "")),
                )
            )
        ]
        residual_claim_by_id = {
            str(row["evidence_unit_id"]): row for row in residual_claim_reviews
        }
        for action in applied["evidence_actions"]:
            if action["action"] != "human_required":
                continue
            target_id = str(action["target_evidence_unit_id"])
            residual_claim_by_id.setdefault(
                target_id,
                copy.deepcopy(source_claim_reviews_by_id[target_id]),
            )
        residual_claim_reviews = list(residual_claim_by_id.values())
        residual_role_reviews = [
            row
            for row in role_outcome["reviews"]
            if row.get("verdict") != "field_aligned"
        ]
        source_role_review_by_id = {
            str(row["statement_id"]): row for row in source.statement_role_reviews
        }
        source_statement_by_id = {
            str(row["statement_id"]): row for row in build_statement_records(source.analysis)
        }
        residual_role_by_id = {
            str(row["statement_id"]): row for row in residual_role_reviews
        }
        for action in applied["statement_actions"]:
            if action["action"] != "human_required":
                continue
            source_id = str(action["target_statement_id"])
            source_record = source_statement_by_id[source_id]
            matching = [
                row
                for row in statement_records
                if row["field"] == source_record["field"]
                and row["statement"] == source_record["statement"]
            ]
            if len(matching) != 1:
                failures.append(
                    _failure(
                        "semantic_auto_resolution_residual",
                        AnalysisInputError(
                            f"无法把人工字段职责项映射到处置后候选：{source_id}"
                        ),
                    )
                )
                continue
            current_id = str(matching[0]["statement_id"])
            residual_role_by_id.setdefault(
                current_id,
                {
                    **copy.deepcopy(source_role_review_by_id[source_id]),
                    "statement_id": current_id,
                },
            )
        residual_role_reviews = list(residual_role_by_id.values())
        residual_issues = build_semantic_issues(
            evidence_units=applied["evidence_units"],
            statement_records=statement_records,
            evidence_claim_reviews=residual_claim_reviews,
            statement_role_reviews=residual_role_reviews,
        )
        residual_issues = enrich_semantic_issues_for_review(
            issues=residual_issues,
            paper_title=source.snapshot.paper_title,
            evidence_units=applied["evidence_units"],
            statement_records=statement_records,
            materials=list(source.snapshot.materials),
        )
        if residual_issues:
            write_semantic_decisions_template(
                run_dir / "review" / "residual_semantic_decisions.csv",
                residual_issues,
            )
            write_semantic_decision_workbook(
                run_dir / "review" / "residual_semantic_workbook.md",
                residual_issues,
            )

        if not _generation_is_current(
            self.workspace,
            source.snapshot.paper_id,
            source.snapshot.generation_id,
        ):
            failures.append(
                _failure(
                    "semantic_auto_resolution_publish",
                    AnalysisInputError("Card generation 在自动处置期间发生变化。"),
                    error_code="input.source_generation_changed",
                )
            )
        return _finish_semantic_auto_resolution_run(
            run_dir,
            base_manifest,
            source,
            context,
            batch_results,
            failures,
            applied=applied,
            evidence_claim_reviews=claim_gate["reviews"],
            statement_role_reviews=role_outcome["reviews"],
            statement_support_reviews=statement_support_reviews,
            residual_issues=residual_issues,
        )


class LLMSemanticAdjudicationRunner:
    """执行一次人工语义裁决、受约束处置和全文重新核验。"""

    def __init__(
        self,
        workspace: str | Path,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        audit_run_id: str,
        run_id: str | None = None,
        decisions_path: str | Path | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 120,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = self.workspace / "_llm_analysis" / "runs" / resolved_run_id
        if run_dir.exists():
            raise AnalysisInputError(f"人工语义裁决 run_id 已存在，不能覆盖：{resolved_run_id}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        source: SemanticAdjudicationSource | None = None
        profile: ModelProfile | None = None
        client: Any | None = None
        revision_request: PlannedSynthesisRequest | None = None
        revision_system = ""
        revision_user = ""
        try:
            source = _load_semantic_adjudication_source(
                self.workspace,
                audit_run_id,
                Path(decisions_path) if decisions_path is not None else None,
            )
            profile = load_model_profile(Path(model_profile_path))
            if provider != profile.provider:
                raise AnalysisInputError("人工裁决 provider 与 model profile 不一致。")
            if source.audit_manifest.get("provider") != provider:
                raise AnalysisInputError("人工裁决 provider 与来源语义审计不一致。")
            if source.audit_manifest.get("model") != profile.request_model:
                raise AnalysisInputError("人工裁决 model 与来源语义审计不一致。")
            if source.audit_manifest.get("tokenizer_revision") != profile.tokenizer_revision:
                raise AnalysisInputError("人工裁决 tokenizer revision 与来源语义审计不一致。")
            token_counter = self.token_counter or DeepSeekV4TokenCounter(
                profile,
                DEFAULT_TOKENIZER_CACHE,
            )
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=profile.request_model,
                timeout=timeout,
            )
            if str(getattr(client, "provider", provider)) != profile.provider:
                raise AnalysisInputError("analysis client provider 与 model profile 不一致。")
            if str(getattr(client, "model", "")) != profile.request_model:
                raise AnalysisInputError("analysis client model 与 model profile 不一致。")

            decisions = list(source.decisions["decisions"])
            confirmed_claims = [
                row
                for row in decisions
                if row["issue_type"] == "evidence_claim"
                and row["human_decision"] == "确认缺陷"
            ]
            if confirmed_claims:
                blocking_claims = [
                    row
                    for row in source.evidence_claim_reviews
                    if str(row["evidence_unit_id"])
                    in {str(item["target_id"]) for item in confirmed_claims}
                ]
                revision_system, revision_user = build_evidence_revision_prompts(
                    paper_id=source.source.snapshot.paper_id,
                    paper_title=source.source.snapshot.paper_title,
                    topic=source.source.snapshot.topic,
                    evidence_units=list(source.source.evidence_units),
                    blocking_reviews=blocking_claims,
                    human_decisions=confirmed_claims,
                )
                revision_ids = tuple(str(row["target_id"]) for row in confirmed_claims)
                revision_request = plan_synthesis_request(
                    generation_id=source.source.snapshot.generation_id,
                    stage="evidence_revision",
                    section_id=None,
                    evidence_unit_ids=revision_ids,
                    system_prompt=revision_system,
                    user_prompt=revision_user,
                    max_output_tokens=profile.evidence_revision_max_output_tokens,
                    profile=profile,
                    token_counter=token_counter,
                )
            base_manifest = _semantic_adjudication_base_manifest(
                run_id=resolved_run_id,
                started_at=started_at,
                source=source,
                profile=profile,
                provider=provider,
            )
            _write_semantic_adjudication_inputs(
                run_dir,
                source=source,
                profile=profile,
                token_counter=token_counter,
                revision_request=revision_request,
                revision_system=revision_system,
                revision_user=revision_user,
            )
            _write_json(run_dir / "manifest.json", base_manifest)
        except Exception as exc:
            failure = _failure("semantic_adjudication_setup", exc)
            manifest = {
                "schema_version": SEMANTIC_ADJUDICATION_RUN_SCHEMA_VERSION,
                "run_id": resolved_run_id,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "source_audit_run_id": audit_run_id,
                "paper_id": source.source.snapshot.paper_id if source else None,
                "paper_disposition": "not_published",
                "provider": provider,
                "model": profile.request_model if profile else None,
                "failure_count": 1,
                "failure_codes": [failure["error_code"]],
                "response_usage": _empty_usage(),
                "batches": [],
            }
            coverage = _empty_semantic_adjudication_coverage(source)
            _write_json(run_dir / "coverage.json", coverage)
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            _write_json(run_dir / "manifest.json", manifest)
            write_semantic_adjudication_review(
                run_dir / "review",
                source_audit_run_id=audit_run_id,
                run_id=resolved_run_id,
                paper_id=source.source.snapshot.paper_id if source else "未记录",
                status="failed",
                decisions=list(source.decisions.get("decisions", [])) if source else [],
                evidence_revision_mappings=[],
                evidence_claim_reviews=[],
                statement_role_reviews=[],
                failures=[failure],
            )
            return _semantic_adjudication_result(run_dir, manifest, source)

        assert source is not None and profile is not None and client is not None
        batch_results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        evidence_units = list(source.source.evidence_units)
        revision_mappings: list[dict[str, str]] = []
        if revision_request is not None:
            revision_outcome = _execute_evidence_revision_request(
                run_dir,
                client,
                source,
                revision_request,
                revision_system,
                revision_user,
                profile=profile,
            )
            batch_results.append(revision_outcome["result"])
            if revision_outcome["failure"]:
                failures.append(revision_outcome["failure"])
                return _finish_failed_semantic_adjudication_run(
                    run_dir,
                    base_manifest,
                    source,
                    batch_results,
                    failures,
                    evidence_units=evidence_units,
                    revision_mappings=[],
                )
            evidence_units = revision_outcome["evidence_units"]
            revision_mappings = revision_outcome["mappings"]
            _write_json(run_dir / "output" / "evidence_revision.json", revision_outcome["revision"])
        _write_jsonl(run_dir / "output" / "evidence_units.jsonl", evidence_units)
        _write_jsonl(run_dir / "output" / "evidence_revision_mappings.jsonl", revision_mappings)

        decisions = list(source.decisions["decisions"])
        accepted_claim_overrides = {
            str(row["target_id"])
            for row in decisions
            if row["issue_type"] == "evidence_claim" and row["human_decision"] == "门禁误判"
        }
        source_claim_reviews_by_id = {
            str(row["evidence_unit_id"]): row for row in source.evidence_claim_reviews
        }
        accepted_claim_override_reviews = {
            evidence_id: source_claim_reviews_by_id[evidence_id]
            for evidence_id in accepted_claim_overrides
        }
        claim_gate = _execute_evidence_claim_support_gate(
            run_dir=run_dir,
            client=client,
            snapshot=source.source.snapshot,
            evidence_units=evidence_units,
            profile=profile,
            token_counter=token_counter,
            accepted_override_ids=accepted_claim_overrides,
            accepted_override_reviews=accepted_claim_override_reviews,
        )
        batch_results.extend(claim_gate["results"])
        if claim_gate["failure"]:
            failures.append(claim_gate["failure"])
            return _finish_failed_semantic_adjudication_run(
                run_dir,
                base_manifest,
                source,
                batch_results,
                failures,
                evidence_units=evidence_units,
                revision_mappings=revision_mappings,
                evidence_claim_reviews=claim_gate["reviews"],
                coverage_update=claim_gate["coverage"],
            )

        role_review_by_id = {
            str(row["statement_id"]): row for row in source.statement_role_reviews
        }
        statement_by_id = {
            str(row["statement_id"]): row for row in source.statement_records
        }
        confirmed_role_constraints = []
        for decision in decisions:
            if decision["issue_type"] != "statement_role" or decision["human_decision"] != "确认缺陷":
                continue
            statement_id = str(decision["target_id"])
            review = role_review_by_id[statement_id]
            record = statement_by_id[statement_id]
            confirmed_role_constraints.append(
                {
                    "source_statement_id": statement_id,
                    "field": record["field"],
                    "rejected_statement": record["statement"],
                    "gate_reason": review["reason"],
                    "misaligned_fragments": review["misaligned_fragments"],
                    "human_note": decision["human_note"],
                }
            )
        accepted_role_overrides = {
            str(row["target_id"])
            for row in decisions
            if row["issue_type"] == "statement_role" and row["human_decision"] == "门禁误判"
        }
        accepted_role_override_reviews = {
            statement_id: role_review_by_id[statement_id]
            for statement_id in accepted_role_overrides
        }

        synthesis: dict[str, Any] | None = None
        if evidence_units:
            synthesis = _execute_synthesis_pipeline(
                run_dir,
                client,
                source.source.snapshot,
                evidence_units,
                list(source.assessments),
                profile=profile,
                token_counter=token_counter,
                section_map=None,
                retry_context=None,
                retry_batch_id=None,
                adjudication_constraints=confirmed_role_constraints,
                accepted_role_override_ids=accepted_role_overrides,
                accepted_role_override_reviews=accepted_role_override_reviews,
            )
            batch_results.extend(synthesis["results"])
            if synthesis["failure"]:
                failures.append(synthesis["failure"])
                return _finish_failed_semantic_adjudication_run(
                    run_dir,
                    base_manifest,
                    source,
                    batch_results,
                    failures,
                    evidence_units=evidence_units,
                    revision_mappings=revision_mappings,
                    evidence_claim_reviews=claim_gate["reviews"],
                    statement_role_reviews=_read_jsonl(
                        run_dir / "output" / "statement_role_reviews.jsonl"
                    ),
                    coverage_update=claim_gate["coverage"],
                )
            analysis = synthesis["analysis"]
            paper_disposition = "analyzed"
            statement_role_reviews = synthesis["statement_role_reviews"]
            statement_support_reviews = synthesis["statement_support_reviews"]
            coverage = {
                **_empty_semantic_adjudication_coverage(source),
                **claim_gate["coverage"],
                **synthesis["coverage"],
            }
        else:
            analysis = None
            paper_disposition = "no_usable_evidence"
            statement_role_reviews = []
            statement_support_reviews = []
            coverage = {
                **_empty_semantic_adjudication_coverage(source),
                **claim_gate["coverage"],
                "all_evidence_unit_ids": [],
                "used_in_final_analysis_ids": [],
                "not_used_in_final_analysis_ids": [],
                "unresolved_evidence_unit_ids": [],
            }

        if not _generation_is_current(
            self.workspace,
            source.source.snapshot.paper_id,
            source.source.snapshot.generation_id,
        ):
            failures.append(
                _failure(
                    "semantic_adjudication_publish",
                    AnalysisInputError("人工裁决期间论文 Card generation 已变化。"),
                    error_code="input.source_generation_changed",
                )
            )
            return _finish_failed_semantic_adjudication_run(
                run_dir,
                base_manifest,
                source,
                batch_results,
                failures,
                evidence_units=evidence_units,
                revision_mappings=revision_mappings,
                evidence_claim_reviews=claim_gate["reviews"],
                statement_role_reviews=statement_role_reviews,
                coverage_update=coverage,
            )

        adjudication_records = _build_semantic_adjudication_records(
            source,
            revision_mappings,
            claim_gate["reviews"],
            statement_role_reviews,
        )
        paper_result = {
            "schema_version": ADJUDICATED_PAPER_RESULT_SCHEMA_VERSION,
            "paper_disposition": paper_disposition,
            "paper_id": source.source.snapshot.paper_id,
            "paper_title": source.source.snapshot.paper_title,
            "topic": source.source.snapshot.topic,
            "analysis": analysis,
            "evidence_claim_support_reviews": claim_gate["reviews"],
            "statement_role_reviews": statement_role_reviews,
            "statement_support_reviews": statement_support_reviews,
            "semantic_adjudications": adjudication_records,
        }
        coverage.update(
            {
                "source_evidence_unit_count": len(source.source.evidence_units),
                "final_evidence_unit_count": len(evidence_units),
                "replaced_evidence_unit_count": sum(
                    row["action"] == "replace" for row in revision_mappings
                ),
                "deleted_evidence_unit_count": sum(
                    row["action"] == "delete" for row in revision_mappings
                ),
                "human_decision_count": len(decisions),
            }
        )
        _write_json(run_dir / "output" / "paper_analysis.json", paper_result)
        _write_json(run_dir / "output" / "semantic_adjudications.json", {
            "schema_version": "llm.semantic_adjudications.v1",
            "items": adjudication_records,
        })
        _write_jsonl(
            run_dir / "output" / "evidence_claim_support_reviews.jsonl",
            claim_gate["reviews"],
        )
        _write_jsonl(
            run_dir / "output" / "statement_role_reviews.jsonl",
            statement_role_reviews,
        )
        _write_jsonl(
            run_dir / "output" / "statement_support_reviews.jsonl",
            statement_support_reviews,
        )
        _write_json(run_dir / "coverage.json", coverage)
        _write_jsonl(run_dir / "audit" / "failures.jsonl", [])
        write_analysis_review(
            run_dir / "review",
            run_id=resolved_run_id,
            paper_id=source.source.snapshot.paper_id,
            paper_title=source.source.snapshot.paper_title,
            topic=source.source.snapshot.topic,
            evidence_units=evidence_units,
            paper_result=paper_result,
            coverage=coverage,
        )
        write_semantic_adjudication_review(
            run_dir / "review",
            source_audit_run_id=source.audit_dir.name,
            run_id=resolved_run_id,
            paper_id=source.source.snapshot.paper_id,
            status="completed",
            decisions=decisions,
            evidence_revision_mappings=revision_mappings,
            evidence_claim_reviews=claim_gate["reviews"],
            statement_role_reviews=statement_role_reviews,
            failures=[],
        )
        manifest = {
            **base_manifest,
            "status": "completed",
            "finished_at": _now(),
            "paper_disposition": paper_disposition,
            "failure_count": 0,
            "failure_codes": [],
            "batches": batch_results,
            "executed_batch_ids": [str(row["request_id"]) for row in batch_results],
            "response_usage": _sum_usage([row["usage"] for row in batch_results]),
        }
        _write_json(run_dir / "manifest.json", manifest)
        return _semantic_adjudication_result(run_dir, manifest, source)


def initialize_semantic_decisions(
    workspace: str | Path,
    audit_run_id: str,
    *,
    replace_pending: bool = False,
) -> dict[str, Any]:
    workspace = Path(workspace)
    audit_dir = workspace / "_llm_analysis" / "semantic_audits" / audit_run_id
    manifest_path = audit_dir / "manifest.json"
    if not manifest_path.exists():
        raise AnalysisInputError(f"找不到语义审计运行：{audit_run_id}")
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != SEMANTIC_AUDIT_RUN_SCHEMA_VERSION:
        raise AnalysisInputError("人工裁决只能基于语义审计运行初始化。")
    if manifest.get("status") not in {"failed", "completed"}:
        raise AnalysisInputError("语义审计尚未结束，不能初始化人工裁决。")
    allowed_failure_codes = {
        "support.evidence_claim_not_fully_supported",
        "role.statement_misclassified",
    }
    unknown_failures = sorted(set(manifest.get("failure_codes", [])) - allowed_failure_codes)
    if unknown_failures:
        raise AnalysisInputError(f"语义审计包含技术失败，不能进入人工裁决：{unknown_failures}")

    evidence_units = _read_jsonl(audit_dir / "input" / "evidence_units.jsonl")
    materials = _read_jsonl(audit_dir / "input" / "materials.jsonl")
    candidate = _read_json(audit_dir / "input" / "candidate_analysis.json")
    evidence_ids = {str(row.get("evidence_unit_id") or "") for row in evidence_units}
    if not evidence_units or "" in evidence_ids or len(evidence_ids) != len(evidence_units):
        raise AnalysisInputError("语义审计 Evidence Unit 为空、缺少 ID 或重复。")
    if _sha256_json(candidate) != manifest.get("candidate_sha256"):
        raise AnalysisInputError("语义审计候选分析哈希不一致。")
    statement_records = build_statement_records(
        validate_paper_analysis(candidate, evidence_ids, evidence_units=evidence_units)
    )
    claim_payload = validate_evidence_claim_support_review(
        {
            "schema_version": "llm.evidence_claim_support_review.v1",
            "evidence_claim_reviews": _read_jsonl(
                audit_dir / "output" / "evidence_claim_support_reviews.jsonl"
            ),
        },
        evidence_units,
    )
    role_payload = validate_statement_role_review(
        {
            "schema_version": "llm.statement_role_review.v1",
            "statement_role_reviews": _read_jsonl(
                audit_dir / "output" / "statement_role_reviews.jsonl"
            ),
        },
        statement_records,
    )
    issues = build_semantic_issues(
        evidence_units=evidence_units,
        statement_records=statement_records,
        evidence_claim_reviews=claim_payload["evidence_claim_reviews"],
        statement_role_reviews=role_payload["statement_role_reviews"],
    )
    if not issues:
        raise AnalysisInputError("语义审计没有阻断项，不需要人工裁决。")
    issues = enrich_semantic_issues_for_review(
        issues=issues,
        paper_title=str(manifest.get("paper_title") or manifest.get("paper_id") or ""),
        evidence_units=evidence_units,
        statement_records=statement_records,
        materials=materials,
    )
    decisions_path = audit_dir / "review" / "semantic_decisions.csv"
    try:
        write_semantic_decisions_template(
            decisions_path,
            issues,
            overwrite=replace_pending,
        )
    except SemanticAdjudicationError as exc:
        raise AnalysisInputError(str(exc)) from exc
    workbook_path = audit_dir / "review" / "semantic_decision_workbook.md"
    write_semantic_decision_workbook(workbook_path, issues)
    guide_path = audit_dir / "review" / "semantic_decision_guide.md"
    _write_text(
        guide_path,
        "\n".join(
            [
                "# 人工语义裁决说明",
                "",
                f"本次共有 {len(issues)} 个语义阻断项。先阅读同目录的 `semantic_decision_workbook.md`，再填写 `semantic_decisions.csv`。",
                "",
                "## 填写规则",
                "",
                "- 裁决表已按第1项、第2项编号，直接包含逐字引文、Card全文、相邻Card、Evidence输出、论文级输出和门禁理由。",
                "- 只能修改 `人工裁决` 和 `人工说明` 两列；其他列是来源快照，修改后文件会被拒绝。",
                "- `人工裁决` 只能填写 `确认缺陷` 或 `门禁误判`，不得保留 `待裁决`。",
                "- 每一行都必须填写 `人工说明`，说明为什么接受或驳回模型门禁。",
                "",
                "## 两种裁决的后果",
                "",
                "- `确认缺陷`：Evidence claim 只允许基于原逐字引文收窄或删除；字段职责问题会废弃旧候选并重新综合对应字段。",
                "- `门禁误判`：不改内容，只保存显式人工覆盖；覆盖只对相同稳定 ID 和未变化内容有效。",
                "",
                "## 执行边界",
                "",
                "裁决执行后会重新运行 Evidence claim、字段职责和陈述支持门禁。任一新问题仍会失败，且不会生成正式 `paper_analysis.json`。",
                "",
            ]
        ),
    )
    manifest.setdefault("paths", {})["semantic_decisions"] = "review/semantic_decisions.csv"
    manifest["paths"]["semantic_decision_guide"] = "review/semantic_decision_guide.md"
    manifest["paths"]["semantic_decision_workbook"] = "review/semantic_decision_workbook.md"
    _write_json(manifest_path, manifest)
    return {
        "audit_run_id": audit_run_id,
        "issue_count": len(issues),
        "decisions_path": str(decisions_path),
        "guide_path": str(guide_path),
        "workbook_path": str(workbook_path),
    }


def _load_semantic_adjudication_source(
    workspace: Path,
    audit_run_id: str,
    decisions_path: Path | None,
) -> SemanticAdjudicationSource:
    audit_dir = workspace / "_llm_analysis" / "semantic_audits" / audit_run_id
    audit_manifest_path = audit_dir / "manifest.json"
    if not audit_manifest_path.exists():
        raise AnalysisInputError(f"找不到语义审计运行：{audit_run_id}")
    audit_manifest = _read_json(audit_manifest_path)
    if audit_manifest.get("schema_version") != SEMANTIC_AUDIT_RUN_SCHEMA_VERSION:
        raise AnalysisInputError("人工裁决来源必须是语义审计运行。")
    if audit_manifest.get("status") not in {"failed", "completed"}:
        raise AnalysisInputError("人工裁决来源语义审计尚未结束。")
    allowed_failure_codes = {
        "support.evidence_claim_not_fully_supported",
        "role.statement_misclassified",
    }
    unknown_failures = sorted(
        set(audit_manifest.get("failure_codes", [])) - allowed_failure_codes
    )
    if unknown_failures:
        raise AnalysisInputError(f"语义审计包含技术失败，不能裁决：{unknown_failures}")

    source_run_id = str(audit_manifest.get("source_run_id") or "")
    source = _load_semantic_audit_source(workspace, source_run_id)
    audit_evidence_units = _read_jsonl(audit_dir / "input" / "evidence_units.jsonl")
    audit_candidate = _read_json(audit_dir / "input" / "candidate_analysis.json")
    if audit_evidence_units != list(source.evidence_units):
        raise AnalysisInputError("语义审计 Evidence Unit 快照与来源运行不一致。")
    if audit_candidate != source.analysis:
        raise AnalysisInputError("语义审计候选分析与来源运行不一致。")
    if _sha256_json(audit_candidate) != audit_manifest.get("candidate_sha256"):
        raise AnalysisInputError("语义审计候选分析哈希不一致。")

    statement_records = build_statement_records(audit_candidate)
    claim_payload = validate_evidence_claim_support_review(
        {
            "schema_version": "llm.evidence_claim_support_review.v1",
            "evidence_claim_reviews": _read_jsonl(
                audit_dir / "output" / "evidence_claim_support_reviews.jsonl"
            ),
        },
        audit_evidence_units,
    )
    role_payload = validate_statement_role_review(
        {
            "schema_version": "llm.statement_role_review.v1",
            "statement_role_reviews": _read_jsonl(
                audit_dir / "output" / "statement_role_reviews.jsonl"
            ),
        },
        statement_records,
    )
    issues = build_semantic_issues(
        evidence_units=audit_evidence_units,
        statement_records=statement_records,
        evidence_claim_reviews=claim_payload["evidence_claim_reviews"],
        statement_role_reviews=role_payload["statement_role_reviews"],
    )
    if not issues:
        raise AnalysisInputError("语义审计没有阻断项，不需要人工裁决。")
    issues = enrich_semantic_issues_for_review(
        issues=issues,
        paper_title=source.snapshot.paper_title,
        evidence_units=audit_evidence_units,
        statement_records=statement_records,
        materials=list(source.snapshot.materials),
    )

    resolved_decisions_path = decisions_path or audit_dir / "review" / "semantic_decisions.csv"
    try:
        decisions = load_semantic_decisions(
            resolved_decisions_path,
            issues=issues,
            audit_run_id=audit_run_id,
            audit_manifest_sha256=_sha256_json(audit_manifest),
            candidate_sha256=_sha256_json(audit_candidate),
            evidence_units_sha256=_sha256_json(audit_evidence_units),
        )
    except SemanticAdjudicationError as exc:
        raise AnalysisInputError(str(exc)) from exc

    source_manifest_path = source.run_dir / "input" / "source_manifest.json"
    if not source_manifest_path.exists():
        raise AnalysisInputError("语义审计来源缺少原始分析 manifest 快照。")
    original_manifest = _read_json(source_manifest_path)
    original_run_id = str(original_manifest.get("run_id") or "")
    original_run_dir = workspace / "_llm_analysis" / "runs" / original_run_id
    assessments = _read_jsonl(original_run_dir / "output" / "material_assessments.jsonl")
    material_ids = [str(row["material_id"]) for row in source.snapshot.materials]
    assessment_ids = [str(row.get("material_id") or "") for row in assessments]
    if sorted(assessment_ids) != sorted(material_ids) or len(assessment_ids) != len(
        set(assessment_ids)
    ):
        raise AnalysisInputError("原始材料判定无法完整对应语义审计 Card 快照。")
    return SemanticAdjudicationSource(
        audit_dir=audit_dir,
        audit_manifest=audit_manifest,
        source=source,
        assessments=tuple(assessments),
        evidence_claim_reviews=tuple(claim_payload["evidence_claim_reviews"]),
        statement_role_reviews=tuple(role_payload["statement_role_reviews"]),
        statement_records=tuple(statement_records),
        issues=tuple(issues),
        decisions=decisions,
        decisions_path=resolved_decisions_path,
    )


def _semantic_adjudication_base_manifest(
    *,
    run_id: str,
    started_at: str,
    source: SemanticAdjudicationSource,
    profile: ModelProfile,
    provider: str,
) -> dict[str, Any]:
    decisions = list(source.decisions["decisions"])
    return {
        "schema_version": SEMANTIC_ADJUDICATION_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "running",
        "started_at": started_at,
        "source_run_id": source.audit_dir.name,
        "source_schema_version": source.audit_manifest.get("schema_version"),
        "source_audit_run_id": source.audit_dir.name,
        "source_run_id": source.source.run_dir.name,
        "adjudication_attempt": 1,
        "paper_id": source.source.snapshot.paper_id,
        "paper_title": source.source.snapshot.paper_title,
        "topic": source.source.snapshot.topic,
        "scope": "full_paper",
        "paper_disposition": "not_published",
        "source_generation_id": source.source.snapshot.generation_id,
        "source_material_count": source.source.snapshot.source_material_count,
        "material_count": len(source.source.snapshot.materials),
        "source_evidence_unit_count": len(source.source.evidence_units),
        "human_decision_count": len(decisions),
        "confirmed_defect_count": sum(row["human_decision"] == "确认缺陷" for row in decisions),
        "gate_rejection_count": sum(row["human_decision"] == "门禁误判" for row in decisions),
        "input_sha256": source.source.snapshot.input_sha256,
        "source_candidate_sha256": _sha256_json(source.source.analysis),
        "source_evidence_units_sha256": _sha256_json(list(source.source.evidence_units)),
        "decisions_sha256": source.decisions["decisions_sha256"],
        "model_profile_id": profile.profile_id,
        "model_profile_sha256": profile.sha256,
        "tokenizer_revision": profile.tokenizer_revision,
        "provider": provider,
        "model": profile.request_model,
        "executed_batch_ids": [],
        "paths": {
            "source_audit_manifest": "input/source_audit_manifest.json",
            "source_run_manifest": "input/source_run_manifest.json",
            "source_candidate": "input/source_candidate_analysis.json",
            "source_evidence_units": "input/source_evidence_units.jsonl",
            "human_decisions_csv": "input/semantic_decisions.csv",
            "human_decisions_snapshot": "input/semantic_decisions.json",
            "evidence_revision": "output/evidence_revision.json",
            "evidence_revision_mappings": "output/evidence_revision_mappings.jsonl",
            "evidence_units": "output/evidence_units.jsonl",
            "paper_analysis": "output/paper_analysis.json",
            "semantic_adjudications": "output/semantic_adjudications.json",
            "coverage": "coverage.json",
            "failures": "audit/failures.jsonl",
            "evidence_corrections": "audit/evidence_corrections.jsonl",
            "review": "review/adjudication.md",
        },
    }


def _write_semantic_adjudication_inputs(
    run_dir: Path,
    *,
    source: SemanticAdjudicationSource,
    profile: ModelProfile,
    token_counter: Any,
    revision_request: PlannedSynthesisRequest | None,
    revision_system: str,
    revision_user: str,
) -> None:
    snapshot = source.source.snapshot
    _write_json(run_dir / "input" / "source_audit_manifest.json", source.audit_manifest)
    _write_json(run_dir / "input" / "source_run_manifest.json", source.source.manifest)
    _write_json(
        run_dir / "input" / "paper.json",
        _read_json(source.source.run_dir / "input" / "paper.json"),
    )
    _write_jsonl(run_dir / "input" / "materials.jsonl", list(snapshot.materials))
    _write_jsonl(
        run_dir / "input" / "source_evidence_units.jsonl",
        list(source.source.evidence_units),
    )
    _write_json(run_dir / "input" / "source_candidate_analysis.json", source.source.analysis)
    _write_jsonl(run_dir / "input" / "material_assessments.jsonl", list(source.assessments))
    _write_jsonl(
        run_dir / "input" / "source_evidence_claim_reviews.jsonl",
        list(source.evidence_claim_reviews),
    )
    _write_jsonl(
        run_dir / "input" / "source_statement_role_reviews.jsonl",
        list(source.statement_role_reviews),
    )
    _write_jsonl(run_dir / "input" / "source_statement_records.jsonl", list(source.statement_records))
    _write_json(run_dir / "input" / "semantic_decisions.json", source.decisions)
    decisions_text = source.decisions_path.read_text(encoding="utf-8-sig")
    _write_text(run_dir / "input" / "semantic_decisions.csv", decisions_text)
    _write_json(run_dir / "plan" / "model_profile.json", profile.to_dict())
    tokenizer_manifest = Path(token_counter.asset_dir) / "manifest.json"
    _write_json(run_dir / "plan" / "tokenizer_manifest.json", _read_json(tokenizer_manifest))
    if revision_request is not None:
        prompt_payload = json.loads(revision_user)
        _write_json(
            run_dir / "schemas" / "evidence_revision.schema.json",
            prompt_payload["输出JSONSchema"],
        )
        _write_text(
            run_dir / "prompts" / "evidence_revision_system.md",
            revision_system + "\n",
        )
    _write_semantic_adjudication_plan(
        run_dir,
        revision_request,
        status="planned" if revision_request is not None else "not_required",
    )


def _write_semantic_adjudication_plan(
    run_dir: Path,
    revision_request: PlannedSynthesisRequest | None,
    *,
    status: str,
) -> None:
    _write_json(
        run_dir / "plan" / "adjudication_plan.json",
        {
            "strategy": "human_decision_then_single_constrained_revision",
            "status": status,
            "requests": [dataclasses.asdict(revision_request)] if revision_request else [],
        },
    )


def _execute_evidence_revision_request(
    run_dir: Path,
    client: Any,
    source: SemanticAdjudicationSource,
    planned: PlannedSynthesisRequest,
    system_prompt: str,
    user_prompt: str,
    *,
    profile: ModelProfile,
) -> dict[str, Any]:
    request_dir = run_dir / "batches" / planned.request_id
    request_dir.mkdir(parents=True)
    decisions = [
        row
        for row in source.decisions["decisions"]
        if row["issue_type"] == "evidence_claim" and row["human_decision"] == "确认缺陷"
    ]
    target_ids = [str(row["target_id"]) for row in decisions]
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(request_dir / "request.json", request_payload)
    started_at = _now()
    input_sha256 = _sha256_json(
        {
            "evidence_units": list(source.source.evidence_units),
            "decisions": decisions,
        }
    )
    provider_result: ProviderResult | None = None
    validated: dict[str, Any] | None = None
    try:
        provider_result = client.complete(
            "evidence_revision",
            request_payload,
            {
                "request_id": planned.request_id,
                "paper_id": source.source.snapshot.paper_id,
                "topic": source.source.snapshot.topic,
                "target_evidence_unit_ids": target_ids,
                "target_evidence_units": [
                    row
                    for row in source.source.evidence_units
                    if str(row["evidence_unit_id"]) in set(target_ids)
                ],
                "human_decisions": decisions,
                "planned_input_tokens": planned.input_tokens,
                "planned_max_output_tokens": planned.max_output_tokens,
            },
        )
        _write_provider_response(request_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        if provider_result.usage.get("prompt_tokens") != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={provider_result.usage.get('prompt_tokens')}"
            )
        parsed = json.loads(extract_chat_content(provider_result.parsed_response))
        _write_json(request_dir / "parsed_response.json", parsed)
        validated, evidence_units, mappings = validate_and_apply_evidence_revision(
            parsed,
            evidence_units=list(source.source.evidence_units),
            target_evidence_ids=target_ids,
        )
        _write_json(request_dir / "validated_output.json", validated)
        _write_jsonl(request_dir / "revised_evidence_units.jsonl", evidence_units)
        _write_jsonl(request_dir / "evidence_revision_mappings.jsonl", mappings)
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        _write_semantic_adjudication_plan(run_dir, planned, status="completed")
        return {
            "result": result,
            "revision": validated,
            "evidence_units": evidence_units,
            "mappings": mappings,
            "failure": None,
        }
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(request_dir, exc.raw_body)
        failure = _failure("evidence_revision", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        _write_semantic_adjudication_plan(run_dir, planned, status="failed")
        return {
            "result": result,
            "revision": validated,
            "evidence_units": [],
            "mappings": [],
            "failure": failure,
        }


def _finish_failed_semantic_adjudication_run(
    run_dir: Path,
    base_manifest: dict[str, Any],
    source: SemanticAdjudicationSource,
    batch_results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    *,
    evidence_units: list[dict[str, Any]],
    revision_mappings: list[dict[str, str]],
    evidence_claim_reviews: list[dict[str, Any]] | None = None,
    statement_role_reviews: list[dict[str, Any]] | None = None,
    coverage_update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_claim_reviews = evidence_claim_reviews or []
    statement_role_reviews = statement_role_reviews or []
    _write_jsonl(run_dir / "output" / "evidence_units.jsonl", evidence_units)
    _write_jsonl(run_dir / "output" / "evidence_revision_mappings.jsonl", revision_mappings)
    coverage = {
        **_empty_semantic_adjudication_coverage(source),
        **(coverage_update or {}),
        "source_evidence_unit_count": len(source.source.evidence_units),
        "final_evidence_unit_count": len(evidence_units),
        "replaced_evidence_unit_count": sum(
            row["action"] == "replace" for row in revision_mappings
        ),
        "deleted_evidence_unit_count": sum(
            row["action"] == "delete" for row in revision_mappings
        ),
    }
    _write_json(run_dir / "coverage.json", coverage)
    _write_jsonl(run_dir / "audit" / "failures.jsonl", failures)
    write_semantic_adjudication_review(
        run_dir / "review",
        source_audit_run_id=source.audit_dir.name,
        run_id=str(base_manifest["run_id"]),
        paper_id=source.source.snapshot.paper_id,
        status="failed",
        decisions=list(source.decisions["decisions"]),
        evidence_revision_mappings=revision_mappings,
        evidence_claim_reviews=evidence_claim_reviews,
        statement_role_reviews=statement_role_reviews,
        failures=failures,
    )
    manifest = {
        **base_manifest,
        "status": "failed",
        "finished_at": _now(),
        "paper_disposition": "not_published",
        "failure_count": len(failures),
        "failure_codes": [str(row["error_code"]) for row in failures],
        "batches": batch_results,
        "executed_batch_ids": [str(row["request_id"]) for row in batch_results],
        "response_usage": _sum_usage([row["usage"] for row in batch_results]),
    }
    _write_json(run_dir / "manifest.json", manifest)
    return _semantic_adjudication_result(run_dir, manifest, source)


def _empty_semantic_adjudication_coverage(
    source: SemanticAdjudicationSource | None,
) -> dict[str, Any]:
    decisions = list(source.decisions.get("decisions", [])) if source else []
    return {
        "scope": "semantic_adjudication",
        "human_decision_count": len(decisions),
        "confirmed_defect_count": sum(row["human_decision"] == "确认缺陷" for row in decisions),
        "gate_rejection_count": sum(row["human_decision"] == "门禁误判" for row in decisions),
        "source_evidence_unit_count": len(source.source.evidence_units) if source else 0,
        "final_evidence_unit_count": 0,
        "replaced_evidence_unit_count": 0,
        "deleted_evidence_unit_count": 0,
        "evidence_claim_review_count": 0,
        "statement_count": 0,
        "statement_role_review_count": 0,
        "human_override_evidence_claim_count": 0,
        "human_override_statement_role_count": 0,
    }


def _build_semantic_adjudication_records(
    source: SemanticAdjudicationSource,
    revision_mappings: list[dict[str, str]],
    evidence_claim_reviews: list[dict[str, Any]],
    statement_role_reviews: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mapping_by_source = {
        str(row["source_evidence_unit_id"]): row for row in revision_mappings
    }
    current_claims = {
        str(row["evidence_unit_id"]): row for row in evidence_claim_reviews
    }
    current_roles = {str(row["statement_id"]): row for row in statement_role_reviews}
    source_claims = {
        str(row["evidence_unit_id"]): row for row in source.evidence_claim_reviews
    }
    source_roles = {str(row["statement_id"]): row for row in source.statement_role_reviews}
    records: list[dict[str, Any]] = []
    for decision in source.decisions["decisions"]:
        target_id = str(decision["target_id"])
        mapping = mapping_by_source.get(target_id)
        current_target_id = target_id
        resolution = ""
        override_applied = False
        if decision["issue_type"] == "evidence_claim":
            source_review = source_claims[target_id]
            if mapping:
                current_target_id = str(mapping["result_evidence_unit_id"])
                resolution = "evidence_deleted" if mapping["action"] == "delete" else "evidence_replaced"
            else:
                current_review = current_claims.get(target_id)
                override_applied = bool(
                    decision["human_decision"] == "门禁误判"
                    and current_review
                    and current_review["verdict"] != "directly_supported"
                )
                resolution = "human_override_applied" if override_applied else "gate_passed_without_override"
        else:
            source_review = source_roles[target_id]
            current_review = current_roles.get(target_id)
            override_applied = bool(
                decision["human_decision"] == "门禁误判"
                and current_review
                and current_review["verdict"] != "field_aligned"
            )
            if decision["human_decision"] == "确认缺陷":
                resolution = "statement_regenerated"
            else:
                resolution = "human_override_applied" if override_applied else "gate_passed_without_override"
        records.append(
            {
                "source_audit_run_id": source.audit_dir.name,
                "issue_type": decision["issue_type"],
                "source_target_id": target_id,
                "current_target_id": current_target_id or None,
                "source_model_verdict": decision["model_verdict"],
                "source_review_sha256": _sha256_json(source_review),
                "human_decision": decision["human_decision"],
                "human_note": decision["human_note"],
                "resolution": resolution,
                "override_applied": override_applied,
            }
        )
    return records


def _semantic_adjudication_result(
    run_dir: Path,
    manifest: dict[str, Any],
    source: SemanticAdjudicationSource | None,
) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "provider": manifest.get("provider"),
        "model": manifest.get("model"),
        "paper_id": manifest.get("paper_id"),
        "paper_disposition": manifest.get("paper_disposition"),
        "batch_count": len(manifest.get("batches", [])),
        "material_count": len(source.source.snapshot.materials) if source else 0,
        "failure_count": manifest.get("failure_count", 0),
        "failure_codes": list(manifest.get("failure_codes", [])),
        "scope": manifest.get("scope"),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "review_path": str(run_dir / "review" / "adjudication.md"),
    }


def create_input_snapshot(
    workspace: str | Path,
    *,
    paper_id: str,
    topic: str,
    limit: int | None = None,
    workspace_paper_id: str | None = None,
) -> AnalysisInputSnapshot:
    workspace = Path(workspace)
    if not paper_id:
        raise AnalysisInputError("paper_id 不能为空。")
    if not topic.strip():
        raise AnalysisInputError("topic 不能为空。")
    if limit is not None and limit < 1:
        raise AnalysisInputError("limit 必须 >= 1。")
    if workspace_paper_id is None:
        papers = _read_jsonl(workspace / "_corpus" / "papers.jsonl")
        matching_papers = [
            row for row in papers if str(row.get("paper_id", "")) == paper_id
        ]
        if len(matching_papers) != 1:
            raise AnalysisInputError(
                f"papers.jsonl 必须且只能包含一条 paper_id={paper_id!r} 的记录。"
            )
        paper = matching_papers[0]
        if paper.get("status") != "completed":
            raise AnalysisInputError(
                f"论文当前状态不是 completed：{paper.get('status')!r}。"
            )
        generation_id = str(paper.get("generation_id", ""))
        if not generation_id:
            raise AnalysisInputError("论文记录缺少 generation_id。")
        all_materials = _read_jsonl(workspace / "_corpus" / "materials.jsonl")
        selected = [
            row for row in all_materials if str(row.get("paper_id", "")) == paper_id
        ]
    else:
        workspace_id = workspace_paper_id.strip()
        if (
            not workspace_id
            or workspace_id in {".", ".."}
            or "/" in workspace_id
            or "\\" in workspace_id
        ):
            raise AnalysisInputError(
                f"workspace_paper_id 无效：{workspace_paper_id!r}。"
            )
        paper_dir = (workspace / workspace_id).resolve()
        try:
            paper_dir.relative_to(workspace.resolve())
        except ValueError as exc:
            raise AnalysisInputError(
                f"workspace_paper_id 越过 workspace：{workspace_paper_id!r}。"
            ) from exc
        run = _read_json(paper_dir / "run.json")
        current = _read_json(paper_dir / "materials" / "current.json")
        if run.get("paper_id") != paper_id:
            raise AnalysisInputError(
                "论文 run.json 的 paper_id 与请求身份不一致。"
            )
        if run.get("status") != "completed" or current.get("status") != "completed":
            raise AnalysisInputError("论文 Card 当前状态不是 completed。")
        generation_id = str(run.get("generation_id", ""))
        if (
            not generation_id
            or current.get("generation_id") != generation_id
        ):
            raise AnalysisInputError(
                "论文 run.json 与 materials/current.json 的 generation 不一致。"
            )
        materials_path = current.get("materials_path")
        if not isinstance(materials_path, str) or not materials_path.strip():
            raise AnalysisInputError(
                "materials/current.json 缺少 materials_path。"
            )
        resolved_materials = (paper_dir / "materials" / materials_path).resolve()
        try:
            resolved_materials.relative_to((paper_dir / "materials").resolve())
        except ValueError as exc:
            raise AnalysisInputError(
                "materials_path 越过论文 materials 目录。"
            ) from exc
        selected = _read_jsonl(resolved_materials)
        if any(str(row.get("paper_id", "")) != paper_id for row in selected):
            raise AnalysisInputError(
                "论文隔离目录中的 Card paper_id 与请求身份不一致。"
            )
    if not selected:
        raise AnalysisInputError(f"论文没有当前可分析 Card：{paper_id}")
    material_ids = [str(row.get("material_id", "")) for row in selected]
    if any(not material_id for material_id in material_ids):
        raise AnalysisInputError("输入 Card 存在空 material_id。")
    if len(material_ids) != len(set(material_ids)):
        raise AnalysisInputError("输入 Card 存在重复 material_id。")
    generations = {str(row.get("generation_id", "")) for row in selected}
    if generations != {generation_id}:
        raise AnalysisInputError(
            f"Card generation 与论文当前 generation 不一致：cards={sorted(generations)}, paper={generation_id!r}。"
        )
    missing_fingerprints = [row["material_id"] for row in selected if not row.get("source_fingerprint")]
    if missing_fingerprints:
        raise AnalysisInputError(f"输入 Card 缺少 source_fingerprint：{missing_fingerprints}")

    ordered = sorted(
        selected,
        key=lambda row: (
            int(row.get("order", 0)),
            int((row.get("source_span") or {}).get("start_line", 0)),
            str(row["material_id"]),
        ),
    )
    source_material_count = len(ordered)
    if limit is not None:
        ordered = ordered[:limit]
    paper_title = str(ordered[0].get("paper_title") or paper_id)
    return AnalysisInputSnapshot(
        paper_id=paper_id,
        paper_title=paper_title,
        topic=topic.strip(),
        generation_id=generation_id,
        materials=tuple(ordered),
        input_sha256=_sha256_json(ordered),
        topic_sha256=_sha256_text(topic.strip()),
        scope="partial_smoke" if limit is not None else "full_paper",
        source_material_count=source_material_count,
    )


def build_evidence_prompts(
    snapshot: AnalysisInputSnapshot,
    materials: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> tuple[str, str]:
    return build_projected_evidence_prompts(
        paper_id=snapshot.paper_id,
        paper_title=snapshot.paper_title,
        topic=snapshot.topic,
        projected_cards=project_cards(materials),
        all_projected_cards=project_cards(snapshot.materials),
    )


def build_synthesis_prompts(
    snapshot: AnalysisInputSnapshot,
    evidence_units: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    adjudication_constraints: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    quality_flags = sorted(
        {
            str(flag)
            for material in snapshot.materials
            for flag in [*material.get("confidence_flags", []), *material.get("quality_flags", [])]
        }
    )
    disposition_counts: dict[str, int] = {}
    for row in assessments:
        disposition = str(row["disposition"])
        disposition_counts[disposition] = disposition_counts.get(disposition, 0) + 1
    return build_direct_paper_prompts(
        paper_id=snapshot.paper_id,
        paper_title=snapshot.paper_title,
        topic=snapshot.topic,
        quality_flags=quality_flags,
        disposition_counts=disposition_counts,
        evidence_units=evidence_units,
        adjudication_constraints=adjudication_constraints,
    )


def _load_semantic_audit_source(
    workspace: Path,
    source_run_id: str,
) -> SemanticAuditSource:
    source_dir = workspace / "_llm_analysis" / "runs" / source_run_id
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        raise AnalysisInputError(f"找不到语义审计来源运行：{source_run_id}")
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") not in {
        "llm.statement_revision_run.v1",
        REVISION_RUN_SCHEMA_VERSION,
    }:
        raise AnalysisInputError("语义审计来源必须是显式陈述修订运行。")
    if manifest.get("status") not in {"failed", "completed"}:
        raise AnalysisInputError("语义审计来源必须是已经结束的不可变运行。")
    if manifest.get("scope") != "full_paper":
        raise AnalysisInputError("语义审计只接受完整论文分析候选。")

    paper = _read_json(source_dir / "input" / "paper.json")
    materials = _read_jsonl(source_dir / "input" / "materials.jsonl")
    if not materials:
        raise AnalysisInputError("语义审计来源没有 Card 快照。")
    material_ids = [str(row.get("material_id") or "") for row in materials]
    if "" in material_ids or len(material_ids) != len(set(material_ids)):
        raise AnalysisInputError("语义审计来源 Card ID 为空或重复。")
    input_sha256 = _sha256_json(materials)
    if input_sha256 != paper.get("input_sha256"):
        raise AnalysisInputError("语义审计来源 Card 快照哈希不一致。")
    source_manifest_path = source_dir / "input" / "source_manifest.json"
    if source_manifest_path.exists():
        source_manifest = _read_json(source_manifest_path)
        if source_manifest.get("input_sha256") not in {None, input_sha256}:
            raise AnalysisInputError("语义审计来源的原始分析 manifest 与 Card 快照不一致。")

    paper_id = str(manifest.get("paper_id") or "")
    generation_id = str(manifest.get("source_generation_id") or "")
    if not paper_id or not generation_id:
        raise AnalysisInputError("语义审计来源缺少论文身份或 Card generation。")
    if paper.get("paper_id") != paper_id or paper.get("generation_id") != generation_id:
        raise AnalysisInputError("语义审计来源 input/paper.json 与 manifest 身份不一致。")
    if not _generation_is_current(workspace, paper_id, generation_id):
        raise AnalysisInputError("语义审计来源 Card generation 已不是当前代际。")

    evidence_units = _read_jsonl(source_dir / "output" / "evidence_units.jsonl")
    evidence_ids = [str(row.get("evidence_unit_id") or "") for row in evidence_units]
    if not evidence_units or "" in evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
        raise AnalysisInputError("语义审计来源证据单元为空、缺少 ID 或存在重复 ID。")
    material_id_set = set(material_ids)
    citation_material_ids = [
        str(citation.get("material_id") or "")
        for row in evidence_units
        for citation in row.get("citations", [])
    ]
    if not citation_material_ids or "" in citation_material_ids:
        raise AnalysisInputError("语义审计来源证据单元缺少逐字引文 Card 身份。")
    unknown_material_ids = sorted(set(citation_material_ids) - material_id_set)
    if unknown_material_ids:
        raise AnalysisInputError(
            f"语义审计来源证据单元引用未知 Card：{unknown_material_ids}"
        )
    analysis = validate_paper_analysis(
        _read_json(source_dir / "output" / "revised_analysis_candidate.json"),
        set(evidence_ids),
        evidence_units=evidence_units,
    )
    if not build_statement_records(analysis):
        raise AnalysisInputError("语义审计来源候选分析没有可核验陈述。")

    snapshot = AnalysisInputSnapshot(
        paper_id=paper_id,
        paper_title=str(paper.get("paper_title") or manifest.get("paper_title") or paper_id),
        topic=str(paper.get("topic") or manifest.get("topic") or ""),
        generation_id=generation_id,
        materials=tuple(materials),
        input_sha256=input_sha256,
        topic_sha256=_sha256_text(str(paper.get("topic") or manifest.get("topic") or "")),
        scope="full_paper",
        source_material_count=int(paper.get("source_material_count") or len(materials)),
    )
    return SemanticAuditSource(
        run_dir=source_dir,
        manifest=manifest,
        snapshot=snapshot,
        evidence_units=tuple(evidence_units),
        analysis=analysis,
    )


def _load_semantic_auto_resolution_source(
    workspace: Path,
    audit_run_id: str,
) -> SemanticAutoResolutionSource:
    audit_dir = workspace / "_llm_analysis" / "semantic_audits" / audit_run_id
    source_kind = "semantic_audit"
    if not audit_dir.exists():
        audit_dir = (
            workspace
            / "_llm_analysis"
            / "semantic_auto_resolutions"
            / audit_run_id
        )
        source_kind = "semantic_auto_resolution"
    if not audit_dir.exists():
        audit_dir = workspace / "_llm_analysis" / "runs" / audit_run_id
        source_kind = "analysis_run"
    manifest_path = audit_dir / "manifest.json"
    if not manifest_path.exists():
        raise AnalysisInputError(f"找不到语义自动处置来源运行：{audit_run_id}")
    manifest = _read_json(manifest_path)
    expected_schema = {
        "semantic_audit": SEMANTIC_AUDIT_RUN_SCHEMA_VERSION,
        "semantic_auto_resolution": SEMANTIC_AUTO_RESOLUTION_RUN_SCHEMA_VERSION,
        "analysis_run": RUN_SCHEMA_VERSION,
    }[source_kind]
    if manifest.get("schema_version") != expected_schema:
        raise AnalysisInputError("语义自动处置来源运行 schema 无效。")
    if manifest.get("status") not in {"failed", "completed"}:
        raise AnalysisInputError("语义自动处置来源审计必须已经结束。")
    expected_scope = {
        "semantic_audit": "semantic_audit_only",
        "semantic_auto_resolution": "semantic_auto_resolution_only",
        "analysis_run": "full_paper",
    }[source_kind]
    if manifest.get("scope") != expected_scope:
        raise AnalysisInputError("语义自动处置来源审计 scope 无效。")
    if source_kind == "analysis_run" and set(manifest.get("failure_codes", [])) != {
        "role.statement_misclassified"
    }:
        raise AnalysisInputError("普通分析运行只有字段职责门禁失败时可直接进入语义自动处置。")

    paper = _read_json(audit_dir / "input" / "paper.json")
    materials = _read_jsonl(audit_dir / "input" / "materials.jsonl")
    evidence_units_directory = "input" if source_kind == "semantic_audit" else "output"
    evidence_units = _read_jsonl(
        audit_dir / evidence_units_directory / "evidence_units.jsonl"
    )
    analysis_relative_path = {
        "semantic_audit": "input/candidate_analysis.json",
        "semantic_auto_resolution": "output/auto_resolved_analysis_candidate.json",
        "analysis_run": "output/paper_analysis_candidate.json",
    }[source_kind]
    analysis_raw = _read_json(audit_dir / analysis_relative_path)
    evidence_claim_reviews = _read_jsonl(
        audit_dir / "output" / "evidence_claim_support_reviews.jsonl"
    )
    statement_role_reviews = _read_jsonl(
        audit_dir / "output" / "statement_role_reviews.jsonl"
    )
    if not materials or not evidence_units:
        raise AnalysisInputError("语义自动处置来源缺少 Card 或 Evidence 快照。")
    material_ids = [str(row.get("material_id") or "") for row in materials]
    evidence_ids = [str(row.get("evidence_unit_id") or "") for row in evidence_units]
    if "" in material_ids or len(material_ids) != len(set(material_ids)):
        raise AnalysisInputError("语义自动处置来源 Card ID 为空或重复。")
    if "" in evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
        raise AnalysisInputError("语义自动处置来源 Evidence ID 为空或重复。")
    input_sha256 = _sha256_json(materials)
    if paper.get("input_sha256") != input_sha256:
        raise AnalysisInputError("语义自动处置来源 Card 快照哈希不一致。")
    if manifest.get("input_sha256") not in {None, input_sha256}:
        raise AnalysisInputError("语义自动处置审计 manifest 的 Card 哈希不一致。")
    if source_kind == "analysis_run":
        provenance = _read_json(
            audit_dir / "output" / "paper_analysis_candidate.provenance.json"
        )
        expected_provenance = {
            "paper_id": paper.get("paper_id"),
            "source_generation_id": paper.get("generation_id"),
            "input_sha256": input_sha256,
            "evidence_units_sha256": _sha256_json(evidence_units),
            "candidate_sha256": _sha256_json(analysis_raw),
            "statement_role_reviews_sha256": _sha256_json(statement_role_reviews),
            "role_gate_status": "blocked",
        }
        mismatched = [
            key
            for key, value in expected_provenance.items()
            if provenance.get(key) != value
        ]
        if provenance.get("schema_version") != "llm.paper_analysis_candidate_provenance.v1" or mismatched:
            raise AnalysisInputError(
                f"普通分析候选的不可变来源记录不一致：{mismatched}"
            )
    elif source_kind == "semantic_audit" and manifest.get("candidate_sha256") not in {
        None,
        _sha256_json(analysis_raw),
    }:
        raise AnalysisInputError("语义自动处置来源候选分析哈希不一致。")

    analysis = validate_paper_analysis(
        analysis_raw,
        set(evidence_ids),
        evidence_units=evidence_units,
    )
    statement_records = build_statement_records(analysis)
    observed_claim_ids = [
        str(row.get("evidence_unit_id") or "") for row in evidence_claim_reviews
    ]
    observed_statement_ids = [
        str(row.get("statement_id") or "") for row in statement_role_reviews
    ]
    expected_statement_ids = [str(row["statement_id"]) for row in statement_records]
    if Counter(observed_claim_ids) != Counter(evidence_ids):
        raise AnalysisInputError("语义自动处置来源 Evidence claim 审计覆盖不完整。")
    if Counter(observed_statement_ids) != Counter(expected_statement_ids):
        raise AnalysisInputError("语义自动处置来源字段职责审计覆盖不完整。")
    has_blocker = any(
        row.get("verdict") != "directly_supported" for row in evidence_claim_reviews
    ) or any(row.get("verdict") != "field_aligned" for row in statement_role_reviews)
    if not has_blocker:
        raise AnalysisInputError("语义自动处置来源没有语义阻断项。")

    paper_id = str(manifest.get("paper_id") or paper.get("paper_id") or "")
    generation_id = str(
        manifest.get("source_generation_id") or paper.get("generation_id") or ""
    )
    if not paper_id or not generation_id:
        raise AnalysisInputError("语义自动处置来源缺少论文身份或 Card generation。")
    if paper.get("paper_id") != paper_id or paper.get("generation_id") != generation_id:
        raise AnalysisInputError("语义自动处置来源 paper 与 manifest 身份不一致。")
    if not _generation_is_current(workspace, paper_id, generation_id):
        raise AnalysisInputError("语义自动处置来源 Card generation 已不是当前代际。")
    snapshot = AnalysisInputSnapshot(
        paper_id=paper_id,
        paper_title=str(paper.get("paper_title") or manifest.get("paper_title") or paper_id),
        topic=str(paper.get("topic") or manifest.get("topic") or ""),
        generation_id=generation_id,
        materials=tuple(materials),
        input_sha256=input_sha256,
        topic_sha256=_sha256_text(str(paper.get("topic") or manifest.get("topic") or "")),
        scope="full_paper",
        source_material_count=int(paper.get("source_material_count") or len(materials)),
    )
    return SemanticAutoResolutionSource(
        audit_dir=audit_dir,
        audit_manifest=manifest,
        snapshot=snapshot,
        evidence_units=tuple(evidence_units),
        analysis=analysis,
        evidence_claim_reviews=tuple(evidence_claim_reviews),
        statement_role_reviews=tuple(statement_role_reviews),
    )


def _semantic_auto_resolution_base_manifest(
    *,
    run_id: str,
    started_at: str,
    source: SemanticAutoResolutionSource,
    profile: ModelProfile,
    provider: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SEMANTIC_AUTO_RESOLUTION_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "running",
        "started_at": started_at,
        "source_audit_run_id": source.audit_dir.name,
        "source_audit_manifest_sha256": _sha256_json(source.audit_manifest),
        "paper_id": source.snapshot.paper_id,
        "paper_title": source.snapshot.paper_title,
        "topic": source.snapshot.topic,
        "scope": "semantic_auto_resolution_only",
        "paper_disposition": "not_published",
        "source_generation_id": source.snapshot.generation_id,
        "material_count": len(source.snapshot.materials),
        "evidence_unit_count": len(source.evidence_units),
        "source_evidence_issue_count": len(context["evidence_issues"]),
        "source_statement_issue_count": len(context["statement_issues"]),
        "input_sha256": source.snapshot.input_sha256,
        "candidate_sha256": _sha256_json(source.analysis),
        "model_profile_id": profile.profile_id,
        "model_profile_sha256": profile.sha256,
        "tokenizer_revision": profile.tokenizer_revision,
        "provider": provider,
        "model": profile.request_model,
        "executed_batch_ids": [],
    }


def _write_semantic_auto_resolution_inputs(
    run_dir: Path,
    *,
    source: SemanticAutoResolutionSource,
    profile: ModelProfile,
    context: dict[str, Any],
    planned: PlannedSynthesisRequest,
    system_prompt: str,
) -> None:
    _write_json(run_dir / "input" / "source_audit_manifest.json", source.audit_manifest)
    _write_json(
        run_dir / "input" / "paper.json",
        _read_json(source.audit_dir / "input" / "paper.json"),
    )
    _write_jsonl(run_dir / "input" / "materials.jsonl", list(source.snapshot.materials))
    _write_jsonl(run_dir / "input" / "evidence_units.jsonl", list(source.evidence_units))
    _write_json(run_dir / "input" / "candidate_analysis.json", source.analysis)
    _write_json(run_dir / "input" / "auto_resolution_context.json", context)
    _write_json(run_dir / "plan" / "model_profile.json", profile.to_dict())
    _write_json(
        run_dir / "plan" / "auto_resolution_plan.json",
        {"status": "planned", "request": dataclasses.asdict(planned)},
    )
    _write_json(
        run_dir / "schemas" / "semantic_auto_resolution.schema.json",
        build_auto_resolution_schema(context),
    )
    _write_text(
        run_dir / "prompts" / "semantic_auto_resolution_system.md",
        system_prompt + "\n",
    )


def _execute_semantic_auto_resolution_request(
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    planned: PlannedSynthesisRequest,
    system_prompt: str,
    user_prompt: str,
    *,
    context: dict[str, Any],
    profile: ModelProfile,
) -> dict[str, Any]:
    request_dir = run_dir / "batches" / planned.request_id
    request_dir.mkdir(parents=True)
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(request_dir / "request.json", request_payload)
    started_at = _now()
    input_sha256 = _sha256_json(context)
    provider_result: ProviderResult | None = None
    try:
        provider_result = client.complete(
            "semantic_auto_resolution",
            request_payload,
            {
                "request_id": planned.request_id,
                "paper_id": snapshot.paper_id,
                "topic": snapshot.topic,
                "auto_resolution_context": context,
                "planned_input_tokens": planned.input_tokens,
                "planned_max_output_tokens": planned.max_output_tokens,
            },
        )
        _write_provider_response(request_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        if provider_result.usage.get("prompt_tokens") != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={provider_result.usage.get('prompt_tokens')}"
            )
        parsed = json.loads(extract_chat_content(provider_result.parsed_response))
        _write_json(request_dir / "parsed_response.json", parsed)
        validated = validate_auto_resolution_plan(parsed, context=context)
        _write_json(request_dir / "validated_output.json", validated)
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {"result": result, "plan": validated, "failure": None}
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(request_dir, exc.raw_body)
        failure = _failure("semantic_auto_resolution", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {"result": result, "plan": None, "failure": failure}


def _finish_semantic_auto_resolution_run(
    run_dir: Path,
    base_manifest: dict[str, Any],
    source: SemanticAutoResolutionSource,
    context: dict[str, Any],
    batch_results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    *,
    applied: dict[str, Any] | None,
    evidence_claim_reviews: list[dict[str, Any]],
    statement_role_reviews: list[dict[str, Any]],
    statement_support_reviews: list[dict[str, Any]],
    residual_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    recorded_failures = list(failures)
    if residual_issues and not any(
        row.get("error_code") == "resolution.human_required" for row in recorded_failures
    ):
        recorded_failures.append(
            {
                "recorded_at": _now(),
                "stage": "semantic_auto_resolution_residual",
                "batch_id": None,
                "material_ids": [],
                "error_code": "resolution.human_required",
                "error_message": f"仍有 {len(residual_issues)} 项需要人工裁决。",
                "error_path": None,
                "exception_type": "SemanticAutoResolutionResidual",
            }
        )
    status = "completed" if not recorded_failures and not residual_issues else "failed"
    paper_disposition = "validated_candidate" if status == "completed" else "not_published"
    evidence_actions = list(applied.get("evidence_actions", [])) if applied else []
    statement_actions = list(applied.get("statement_actions", [])) if applied else []
    coverage = _build_semantic_auto_resolution_coverage(
        context=context,
        applied=applied,
        evidence_claim_reviews=evidence_claim_reviews,
        statement_role_reviews=statement_role_reviews,
        statement_support_reviews=statement_support_reviews,
        residual_issues=residual_issues,
    )
    manifest = {
        **base_manifest,
        "status": status,
        "finished_at": _now(),
        "paper_disposition": paper_disposition,
        "failure_count": len(recorded_failures),
        "failure_codes": [str(row["error_code"]) for row in recorded_failures],
        "response_usage": _sum_usage([row["usage"] for row in batch_results]),
        "batches": batch_results,
        "executed_batch_ids": [str(row["request_id"]) for row in batch_results],
        "paths": {
            "source_audit_manifest": "input/source_audit_manifest.json",
            "auto_resolution_context": "input/auto_resolution_context.json",
            "auto_resolution_plan": "output/auto_resolution_plan.json",
            "evidence_actions": "output/evidence_actions.jsonl",
            "statement_actions": "output/statement_actions.jsonl",
            "candidate_analysis": "output/auto_resolved_analysis_candidate.json",
            "validated_analysis": "output/validated_paper_analysis.json",
            "evidence_units": "output/evidence_units.jsonl",
            "coverage": "coverage.json",
            "failures": "audit/failures.jsonl",
            "review": "review/auto_resolution.md",
            "residual_decisions": "review/residual_semantic_decisions.csv",
            "residual_workbook": "review/residual_semantic_workbook.md",
        },
    }
    if status == "completed" and applied is not None:
        validated_result = {
            "schema_version": "llm.semantic_auto_resolved_paper.v1",
            "paper_disposition": "validated_candidate",
            "paper_id": source.snapshot.paper_id,
            "paper_title": source.snapshot.paper_title,
            "topic": source.snapshot.topic,
            "source_audit_run_id": source.audit_dir.name,
            "source_schema_version": source.audit_manifest.get("schema_version"),
            "source_generation_id": source.snapshot.generation_id,
            "analysis": applied["analysis"],
            "automatic_resolutions": [*evidence_actions, *statement_actions],
        }
        _write_json(run_dir / "output" / "validated_paper_analysis.json", validated_result)
    _write_json(run_dir / "coverage.json", coverage)
    _write_jsonl(run_dir / "audit" / "failures.jsonl", recorded_failures)
    _write_json(run_dir / "manifest.json", manifest)
    _write_semantic_auto_resolution_review(
        run_dir,
        manifest=manifest,
        evidence_actions=evidence_actions,
        statement_actions=statement_actions,
        residual_issues=residual_issues,
        failures=recorded_failures,
    )
    return _semantic_auto_resolution_result(run_dir, manifest, source)


def _build_semantic_auto_resolution_coverage(
    *,
    context: dict[str, Any],
    applied: dict[str, Any] | None,
    evidence_claim_reviews: list[dict[str, Any]],
    statement_role_reviews: list[dict[str, Any]],
    statement_support_reviews: list[dict[str, Any]],
    residual_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_actions = list(applied.get("evidence_actions", [])) if applied else []
    statement_actions = list(applied.get("statement_actions", [])) if applied else []
    action_counts = Counter(
        str(row.get("action") or "") for row in [*evidence_actions, *statement_actions]
    )
    claim_counts = Counter(str(row.get("verdict") or "") for row in evidence_claim_reviews)
    role_counts = Counter(str(row.get("verdict") or "") for row in statement_role_reviews)
    support_counts = Counter(str(row.get("verdict") or "") for row in statement_support_reviews)
    return {
        "scope": "semantic_auto_resolution_only",
        "source_issue_count": len(context["evidence_issues"]) + len(context["statement_issues"]),
        "source_evidence_issue_count": len(context["evidence_issues"]),
        "source_statement_issue_count": len(context["statement_issues"]),
        "planned_resolution_count": len(evidence_actions) + len(statement_actions),
        "repair_citations_count": action_counts["repair_citations"],
        "replace_with_supported_evidence_count": action_counts["replace_with_supported_evidence"],
        "automatic_gate_override_count": action_counts["keep_gate_override"],
        "delete_duplicate_statement_count": action_counts["delete_duplicate_statement"],
        "planner_human_required_count": action_counts["human_required"],
        "residual_human_issue_count": len(residual_issues),
        "evidence_claim_review_count": len(evidence_claim_reviews),
        "directly_supported_evidence_claim_count": claim_counts["directly_supported"],
        "partially_supported_evidence_claim_count": claim_counts["partially_supported"],
        "unsupported_evidence_claim_count": claim_counts["unsupported"],
        "statement_role_review_count": len(statement_role_reviews),
        "field_aligned_statement_count": role_counts["field_aligned"],
        "misclassified_statement_count": role_counts["misclassified"],
        "statement_support_review_count": len(statement_support_reviews),
        "directly_supported_statement_count": support_counts["directly_supported"],
        "grounded_inference_statement_count": support_counts["grounded_inference"],
    }


def _empty_semantic_auto_resolution_coverage(
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "scope": "semantic_auto_resolution_only",
        "source_issue_count": (
            len(context["evidence_issues"]) + len(context["statement_issues"])
            if context
            else 0
        ),
        "planned_resolution_count": 0,
        "residual_human_issue_count": 0,
    }


def _write_semantic_auto_resolution_review(
    run_dir: Path,
    *,
    manifest: dict[str, Any],
    evidence_actions: list[dict[str, Any]],
    statement_actions: list[dict[str, Any]],
    residual_issues: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    action_labels = {
        "repair_citations": "补充精确引文",
        "replace_with_supported_evidence": "映射到已验证的重复证据",
        "keep_gate_override": "低风险等值改述自动覆盖",
        "delete_duplicate_statement": "删除错位的重复陈述",
        "human_required": "升级人工裁决",
    }
    lines = [
        "# 语义门禁自动处置报告",
        "",
        f"- 论文：{manifest.get('paper_title') or manifest.get('paper_id') or '未记录'}",
        f"- 运行状态：{manifest.get('status')}",
        f"- 产物状态：{manifest.get('paper_disposition')}",
        f"- 自动处置项：{sum(bool(row.get('applied')) for row in [*evidence_actions, *statement_actions])}",
        f"- 残余人工项：{len(residual_issues)}",
        "",
        "## 处置明细",
        "",
    ]
    for index, row in enumerate([*evidence_actions, *statement_actions], start=1):
        target = row.get("target_evidence_unit_id") or row.get("target_statement_id")
        lines.extend(
            [
                f"### 第{index}项：{action_labels.get(str(row.get('action')), row.get('action'))}",
                "",
                f"- 目标：`{target}`",
                f"- 已应用：{'是' if row.get('applied') else '否'}",
                f"- 原因：{row.get('reason') or '未记录'}",
                f"- 结果 Evidence：`{row.get('result_evidence_unit_id') or '不适用'}`",
                f"- 正确字段副本：`{row.get('canonical_statement_id') or '不适用'}`",
                "",
            ]
        )
    if not evidence_actions and not statement_actions:
        lines.extend(["没有形成可执行处置计划。", ""])
    lines.extend(["## 残余人工问题", ""])
    if residual_issues:
        lines.extend(
            [
                f"仍有 {len(residual_issues)} 项。完整原文、Card、模型输出和门禁理由见 `residual_semantic_workbook.md`，只需在 `residual_semantic_decisions.csv` 中填写。",
                "",
            ]
        )
    else:
        lines.extend(["无。", ""])
    lines.extend(["## 失败记录", ""])
    if failures:
        for row in failures:
            lines.append(f"- `{row.get('error_code')}`：{row.get('error_message')}")
    else:
        lines.append("无。")
    lines.extend(
        [
            "",
            "## 发布边界",
            "",
            "该阶段不改写上游 Card，也不写入下游综述语料。只有全部复核通过时才生成 `validated_paper_analysis.json`，其状态仍是独立的 validated candidate。",
            "",
        ]
    )
    path = run_dir / "review" / "auto_resolution.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _semantic_auto_resolution_result(
    run_dir: Path,
    manifest: dict[str, Any],
    source: SemanticAutoResolutionSource | None,
) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "paper_id": manifest.get("paper_id"),
        "paper_disposition": manifest.get("paper_disposition"),
        "material_count": len(source.snapshot.materials) if source else 0,
        "failure_count": manifest.get("failure_count", 0),
        "failure_codes": list(manifest.get("failure_codes", [])),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "review_path": str(run_dir / "review" / "auto_resolution.md"),
    }


def _semantic_audit_base_manifest(
    *,
    run_id: str,
    started_at: str,
    source: SemanticAuditSource,
    profile: ModelProfile,
    provider: str,
) -> dict[str, Any]:
    return {
        "schema_version": SEMANTIC_AUDIT_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "running",
        "started_at": started_at,
        "source_run_id": source.run_dir.name,
        "source_schema_version": source.manifest.get("schema_version"),
        "paper_id": source.snapshot.paper_id,
        "paper_title": source.snapshot.paper_title,
        "topic": source.snapshot.topic,
        "scope": "semantic_audit_only",
        "paper_disposition": "not_published",
        "source_generation_id": source.snapshot.generation_id,
        "source_material_count": source.snapshot.source_material_count,
        "material_count": len(source.snapshot.materials),
        "evidence_unit_count": len(source.evidence_units),
        "statement_count": len(build_statement_records(source.analysis)),
        "input_sha256": source.snapshot.input_sha256,
        "candidate_sha256": _sha256_json(source.analysis),
        "model_profile_id": profile.profile_id,
        "model_profile_sha256": profile.sha256,
        "tokenizer_revision": profile.tokenizer_revision,
        "provider": provider,
        "model": profile.request_model,
        "executed_batch_ids": [],
        "paths": {
            "source_manifest": "input/source_manifest.json",
            "candidate_analysis": "input/candidate_analysis.json",
            "evidence_units": "input/evidence_units.jsonl",
            "materials": "input/materials.jsonl",
            "evidence_claim_support_plan": "plan/evidence_claim_support_plan.json",
            "statement_role_plan": "plan/statement_role_plan.json",
            "evidence_claim_support_reviews": "output/evidence_claim_support_reviews.jsonl",
            "statement_role_reviews": "output/statement_role_reviews.jsonl",
            "coverage": "coverage.json",
            "failures": "audit/failures.jsonl",
            "review": "review/semantic_audit.md",
        },
    }


def _write_semantic_audit_inputs(
    run_dir: Path,
    *,
    source: SemanticAuditSource,
    profile: ModelProfile,
    token_counter: Any,
    role_system: str,
    role_request: PlannedSynthesisRequest,
    statement_ids: list[str],
) -> None:
    _write_json(run_dir / "input" / "source_manifest.json", source.manifest)
    _write_json(
        run_dir / "input" / "paper.json",
        _read_json(source.run_dir / "input" / "paper.json"),
    )
    _write_jsonl(run_dir / "input" / "materials.jsonl", list(source.snapshot.materials))
    _write_jsonl(run_dir / "input" / "evidence_units.jsonl", list(source.evidence_units))
    _write_json(run_dir / "input" / "candidate_analysis.json", source.analysis)
    _write_json(run_dir / "plan" / "model_profile.json", profile.to_dict())
    manifest_path = Path(token_counter.asset_dir) / "manifest.json"
    _write_json(run_dir / "plan" / "tokenizer_manifest.json", _read_json(manifest_path))
    _write_json(
        run_dir / "schemas" / "statement_role.schema.json",
        build_statement_role_schema(statement_ids),
    )
    _write_text(run_dir / "prompts" / "statement_role_system.md", role_system + "\n")
    _write_statement_role_audit_plan(run_dir, role_request, status="planned")


def _write_statement_role_audit_plan(
    run_dir: Path,
    request: PlannedSynthesisRequest,
    *,
    status: str,
) -> None:
    _write_json(
        run_dir / "plan" / "statement_role_plan.json",
        {
            "strategy": "existing_candidate_statement_role_audit",
            "status": status,
            "requests": [dataclasses.asdict(request)],
        },
    )


def _build_semantic_audit_coverage(
    source: SemanticAuditSource,
    statement_records: list[dict[str, Any]],
    evidence_claim_reviews: list[dict[str, Any]],
    statement_role_reviews: list[dict[str, Any]],
    batch_results: list[dict[str, Any]],
) -> dict[str, Any]:
    claim_counts = Counter(str(row.get("verdict")) for row in evidence_claim_reviews)
    role_counts = Counter(str(row.get("verdict")) for row in statement_role_reviews)
    reviewed_evidence_ids = {str(row.get("evidence_unit_id") or "") for row in evidence_claim_reviews}
    reviewed_statement_ids = {str(row.get("statement_id") or "") for row in statement_role_reviews}
    return {
        "scope": "semantic_audit_only",
        "material_count": len(source.snapshot.materials),
        "evidence_unit_count": len(source.evidence_units),
        "evidence_claim_review_count": len(evidence_claim_reviews),
        "missing_evidence_claim_review_ids": sorted(
            {str(row["evidence_unit_id"]) for row in source.evidence_units}
            - reviewed_evidence_ids
        ),
        "directly_supported_evidence_claim_count": claim_counts["directly_supported"],
        "partially_supported_evidence_claim_count": claim_counts["partially_supported"],
        "unsupported_evidence_claim_count": claim_counts["unsupported"],
        "statement_count": len(statement_records),
        "statement_role_review_count": len(statement_role_reviews),
        "missing_statement_role_review_ids": sorted(
            {str(row["statement_id"]) for row in statement_records}
            - reviewed_statement_ids
        ),
        "field_aligned_statement_count": role_counts["field_aligned"],
        "misclassified_statement_count": role_counts["misclassified"],
        "evidence_claim_gate_passed": (
            len(evidence_claim_reviews) == len(source.evidence_units)
            and claim_counts["directly_supported"] == len(source.evidence_units)
        ),
        "statement_role_gate_passed": (
            len(statement_role_reviews) == len(statement_records)
            and role_counts["field_aligned"] == len(statement_records)
        ),
        "completed_batch_ids": [
            str(row["request_id"]) for row in batch_results if row.get("status") == "completed"
        ],
        "failed_batch_ids": [
            str(row["request_id"]) for row in batch_results if row.get("status") == "failed"
        ],
    }


def _empty_semantic_audit_coverage(
    source: SemanticAuditSource | None,
    statement_records: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "scope": "semantic_audit_only",
        "material_count": len(source.snapshot.materials) if source else 0,
        "evidence_unit_count": len(source.evidence_units) if source else 0,
        "evidence_claim_review_count": 0,
        "missing_evidence_claim_review_ids": [
            str(row["evidence_unit_id"]) for row in source.evidence_units
        ]
        if source
        else [],
        "directly_supported_evidence_claim_count": 0,
        "partially_supported_evidence_claim_count": 0,
        "unsupported_evidence_claim_count": 0,
        "statement_count": len(statement_records),
        "statement_role_review_count": 0,
        "missing_statement_role_review_ids": [
            str(row["statement_id"]) for row in statement_records
        ],
        "field_aligned_statement_count": 0,
        "misclassified_statement_count": 0,
        "evidence_claim_gate_passed": False,
        "statement_role_gate_passed": False,
        "completed_batch_ids": [],
        "failed_batch_ids": [],
    }


def _semantic_audit_result(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    material_count: int,
) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "provider": manifest.get("provider"),
        "model": manifest.get("model"),
        "paper_id": manifest.get("paper_id"),
        "paper_disposition": manifest.get("paper_disposition"),
        "batch_count": len(manifest.get("batches", [])),
        "material_count": material_count,
        "failure_count": manifest.get("failure_count", 0),
        "failure_codes": list(manifest.get("failure_codes", [])),
        "scope": manifest.get("scope"),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "review_path": str(run_dir / "review" / "semantic_audit.md"),
    }


def _load_statement_revision_source(
    workspace: Path,
    source_run_id: str,
) -> StatementRevisionSource:
    source_dir = workspace / "_llm_analysis" / "runs" / source_run_id
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        raise AnalysisInputError(f"找不到陈述修订来源运行：{source_run_id}")
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != RUN_SCHEMA_VERSION:
        raise AnalysisInputError("陈述修订只能读取原始 LLM 分析运行，不能修订另一次修订运行。")
    if manifest.get("status") != "failed" or manifest.get("scope") != "full_paper":
        raise AnalysisInputError("陈述修订来源必须是失败的完整论文分析运行。")
    failures = _read_jsonl(source_dir / "audit" / "failures.jsonl")
    support_failures = [
        row
        for row in failures
        if row.get("stage") == "statement_support"
        and row.get("error_code")
        in {"support.statement_not_fully_supported", "support.direct_statement_only_inferred"}
    ]
    if len(failures) != 1 or len(support_failures) != 1:
        raise AnalysisInputError("来源运行必须且只能包含一条可修订的陈述支持失败。")
    support_request_id = str(support_failures[0].get("batch_id") or "")
    if not support_request_id:
        raise AnalysisInputError("来源陈述支持失败缺少 request_id。")

    paper = _read_json(source_dir / "input" / "paper.json")
    materials = _read_jsonl(source_dir / "input" / "materials.jsonl")
    if _sha256_json(materials) != manifest.get("input_sha256"):
        raise AnalysisInputError("来源运行 Card 快照哈希不一致。")
    generation_id = str(manifest.get("source_generation_id") or "")
    paper_id = str(manifest.get("paper_id") or "")
    if not generation_id or not paper_id:
        raise AnalysisInputError("来源运行缺少论文身份或 generation。")
    if not _generation_is_current(workspace, paper_id, generation_id):
        raise AnalysisInputError("来源运行 Card generation 已不是当前代际。")
    snapshot = AnalysisInputSnapshot(
        paper_id=paper_id,
        paper_title=str(manifest.get("paper_title") or paper_id),
        topic=str(manifest.get("topic") or ""),
        generation_id=generation_id,
        materials=tuple(materials),
        input_sha256=str(manifest["input_sha256"]),
        topic_sha256=str(manifest.get("topic_sha256") or _sha256_text(str(manifest.get("topic") or ""))),
        scope="full_paper",
        source_material_count=int(manifest.get("source_material_count") or len(materials)),
    )
    if paper.get("generation_id") != generation_id or paper.get("paper_id") != paper_id:
        raise AnalysisInputError("来源 input/paper.json 与 manifest 身份不一致。")

    evidence_units = _read_jsonl(source_dir / "output" / "evidence_units.jsonl")
    evidence_ids = {str(row.get("evidence_unit_id") or "") for row in evidence_units}
    if not evidence_units or "" in evidence_ids or len(evidence_ids) != len(evidence_units):
        raise AnalysisInputError("来源运行证据单元为空、缺少 ID 或存在重复 ID。")
    evidence_claim_review = validate_evidence_claim_support_review(
        {
            "schema_version": "llm.evidence_claim_support_review.v1",
            "evidence_claim_reviews": _read_jsonl(
                source_dir / "output" / "evidence_claim_support_reviews.jsonl"
            ),
        },
        evidence_units,
    )
    enforce_evidence_claim_support_policy(evidence_claim_review)
    synthesis_plan = _read_json(source_dir / "plan" / "synthesis_plan.json")
    requests = synthesis_plan.get("requests", [])
    draft_requests = [row for row in requests if row.get("stage") == "paper_synthesis"]
    disposition_requests = [row for row in requests if row.get("stage") == "evidence_disposition"]
    role_requests = [row for row in requests if row.get("stage") == "statement_role"]
    if len(draft_requests) != 1 or len(disposition_requests) != 1 or len(role_requests) != 1:
        raise AnalysisInputError("来源运行必须包含唯一论文草稿、证据分类和字段职责核验请求。")
    draft_request_id = str(draft_requests[0]["request_id"])
    disposition_request_id = str(disposition_requests[0]["request_id"])
    role_request_id = str(role_requests[0]["request_id"])
    for request_id in (draft_request_id, disposition_request_id, role_request_id):
        result = _read_json(source_dir / "batches" / request_id / "result.json")
        if result.get("status") != "completed":
            raise AnalysisInputError(f"来源上游请求未完成：{request_id}")
    draft = validate_paper_analysis_draft(
        _read_json(source_dir / "batches" / draft_request_id / "validated_output.json"),
        evidence_ids,
        evidence_units=evidence_units,
    )
    disposition_payload = validate_non_used_evidence_dispositions(
        _read_json(source_dir / "batches" / disposition_request_id / "validated_output.json"),
        evidence_ids - paper_analysis_referenced_evidence_ids(draft),
    )
    original_analysis = finalize_paper_analysis(
        draft,
        disposition_payload["non_used_evidence_dispositions"],
        evidence_units,
    )
    current_records = build_statement_records(original_analysis)
    role_review = validate_statement_role_review(
        _read_json(source_dir / "batches" / role_request_id / "validated_output.json"),
        current_records,
    )
    enforce_statement_role_policy(role_review)
    archived_records_payload = _read_json(
        source_dir / "batches" / support_request_id / "statement_records.json"
    )
    archived_records = archived_records_payload.get("statements", [])
    if archived_records != current_records:
        raise AnalysisInputError("来源支持核验陈述清单无法由上游分析确定性重建。")
    support_review = validate_statement_support_review(
        _read_json(source_dir / "batches" / support_request_id / "validated_output.json"),
        current_records,
    )
    blocking = blocking_statement_support_reviews(support_review, current_records)
    if not blocking:
        raise AnalysisInputError("来源支持核验没有可修订的阻断陈述。")
    return StatementRevisionSource(
        run_dir=source_dir,
        manifest=manifest,
        snapshot=snapshot,
        evidence_units=tuple(evidence_units),
        evidence_claim_support_reviews=tuple(
            evidence_claim_review["evidence_claim_reviews"]
        ),
        original_analysis=original_analysis,
        blocking_records=tuple(blocking),
        source_support_request_id=support_request_id,
    )


def _statement_revision_base_manifest(
    *,
    run_id: str,
    started_at: str,
    source: StatementRevisionSource,
    profile: ModelProfile,
    provider: str,
) -> dict[str, Any]:
    return {
        "schema_version": REVISION_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "running",
        "started_at": started_at,
        "source_run_id": source.run_dir.name,
        "source_support_request_id": source.source_support_request_id,
        "revision_attempt": 1,
        "paper_id": source.snapshot.paper_id,
        "paper_title": source.snapshot.paper_title,
        "topic": source.snapshot.topic,
        "scope": source.snapshot.scope,
        "source_generation_id": source.snapshot.generation_id,
        "source_material_count": source.snapshot.source_material_count,
        "material_count": len(source.snapshot.materials),
        "evidence_unit_count": len(source.evidence_units),
        "blocking_statement_count": len(source.blocking_records),
        "source_model_profile_sha256": source.manifest.get("model_profile_sha256"),
        "model_profile_id": profile.profile_id,
        "model_profile_sha256": profile.sha256,
        "tokenizer_revision": profile.tokenizer_revision,
        "provider": provider,
        "model": profile.request_model,
        "executed_batch_ids": [],
        "paths": {
            "source_manifest": "input/source_manifest.json",
            "original_analysis": "input/original_analysis.json",
            "blocking_statements": "input/blocking_statements.jsonl",
            "revision": "output/revision.json",
            "candidate": "output/revised_analysis_candidate.json",
            "paper_analysis": "output/paper_analysis.json",
            "evidence_claim_support_reviews": "output/evidence_claim_support_reviews.jsonl",
            "statement_role_reviews": "output/statement_role_reviews.jsonl",
            "statement_support_reviews": "output/statement_support_reviews.jsonl",
            "coverage": "coverage.json",
            "failures": "audit/failures.jsonl",
            "review": "review/review.md",
            "revision_review": "review/revision.md",
        },
    }


def _write_statement_revision_inputs(
    run_dir: Path,
    *,
    source: StatementRevisionSource,
    profile: ModelProfile,
    revision_system: str,
    revision_user: str,
) -> None:
    prompt_payload = json.loads(revision_user)
    _write_json(run_dir / "input" / "source_manifest.json", source.manifest)
    _write_json(run_dir / "input" / "paper.json", _read_json(source.run_dir / "input" / "paper.json"))
    _write_jsonl(run_dir / "input" / "materials.jsonl", list(source.snapshot.materials))
    _write_json(run_dir / "input" / "original_analysis.json", source.original_analysis)
    _write_jsonl(run_dir / "input" / "blocking_statements.jsonl", list(source.blocking_records))
    _write_jsonl(run_dir / "input" / "evidence_units.jsonl", list(source.evidence_units))
    _write_jsonl(run_dir / "output" / "evidence_units.jsonl", list(source.evidence_units))
    _write_jsonl(
        run_dir / "output" / "evidence_claim_support_reviews.jsonl",
        list(source.evidence_claim_support_reviews),
    )
    _write_json(run_dir / "plan" / "model_profile.json", profile.to_dict())
    _write_json(
        run_dir / "plan" / "analysis_plan.json",
        {"strategy": "reuse_source_evidence", "source_run_id": source.run_dir.name, "evidence_batches": []},
    )
    tokenizer_manifest = source.run_dir / "plan" / "tokenizer_manifest.json"
    if tokenizer_manifest.exists():
        _write_json(run_dir / "plan" / "tokenizer_manifest.json", _read_json(tokenizer_manifest))
    _write_json(
        run_dir / "schemas" / "statement_revision.schema.json",
        prompt_payload["输出JSONSchema"],
    )
    _write_json(run_dir / "schemas" / "statement_role.schema.json", STATEMENT_ROLE_SCHEMA)
    _write_json(run_dir / "schemas" / "statement_support.schema.json", STATEMENT_SUPPORT_SCHEMA)
    _write_text(run_dir / "prompts" / "statement_revision_system.md", revision_system + "\n")


def _write_revision_plan(
    run_dir: Path,
    requests: list[PlannedSynthesisRequest],
    *,
    status: str,
) -> None:
    _write_json(
        run_dir / "plan" / "synthesis_plan.json",
        {
            "strategy": "single_explicit_statement_revision",
            "status": status,
            "requests": [dataclasses.asdict(row) for row in requests],
            "cross_section_evidence_ids": [],
        },
    )


def _prepare_statement_revision_support_retry(
    workspace: Path,
    retry_run_id: str,
    *,
    source: StatementRevisionSource,
    planned_revision: PlannedSynthesisRequest,
) -> Path:
    parent_dir = workspace / "_llm_analysis" / "runs" / retry_run_id
    if not (parent_dir / "manifest.json").exists():
        raise AnalysisInputError(f"找不到修订支持核验父运行：{retry_run_id}")
    manifest = _read_json(parent_dir / "manifest.json")
    if (
        manifest.get("schema_version") != REVISION_RUN_SCHEMA_VERSION
        or manifest.get("status") != "failed"
        or manifest.get("source_run_id") != source.run_dir.name
        or manifest.get("retry_mode") is not None
    ):
        raise AnalysisInputError("support-only 重跑来源必须是同一分析的首个失败修订运行。")
    failures = _read_jsonl(parent_dir / "audit" / "failures.jsonl")
    if (
        len(failures) != 1
        or failures[0].get("stage") != "statement_support"
        or failures[0].get("error_code") != "schema.statement_support_invalid"
    ):
        raise AnalysisInputError("support-only 重跑只允许处理陈述支持 Schema 失败。")
    plan = _read_json(parent_dir / "plan" / "synthesis_plan.json")
    revision_requests = [
        row for row in plan.get("requests", []) if row.get("stage") == "statement_revision"
    ]
    if len(revision_requests) != 1:
        raise AnalysisInputError("修订父运行缺少唯一 statement_revision 请求。")
    parent_request_id = str(revision_requests[0]["request_id"])
    parent_result = _read_json(parent_dir / "batches" / parent_request_id / "result.json")
    if (
        parent_request_id != planned_revision.request_id
        or parent_result.get("status") != "completed"
        or parent_result.get("prompt_sha256") != planned_revision.prompt_sha256
    ):
        raise AnalysisInputError("修订父运行的已验证修订请求与当前合同不一致。")
    revision = _read_json(parent_dir / "output" / "revision.json")
    _, rebuilt_analysis, _ = validate_and_apply_statement_revision(
        revision,
        original_analysis=source.original_analysis,
        blocking_records=list(source.blocking_records),
        evidence_units=list(source.evidence_units),
    )
    if rebuilt_analysis != _read_json(parent_dir / "output" / "revised_analysis_candidate.json"):
        raise AnalysisInputError("修订父运行候选分析无法由已验证修订确定性重建。")
    return parent_dir


def _reuse_statement_revision_request(
    parent_dir: Path,
    run_dir: Path,
    source: StatementRevisionSource,
    planned: PlannedSynthesisRequest,
) -> dict[str, Any]:
    source_dir = parent_dir / "batches" / planned.request_id
    target_dir = run_dir / "batches" / planned.request_id
    revision = _read_json(source_dir / "validated_output.json")
    validated, analysis, released_ids = validate_and_apply_statement_revision(
        revision,
        original_analysis=source.original_analysis,
        blocking_records=list(source.blocking_records),
        evidence_units=list(source.evidence_units),
    )
    if analysis != _read_json(source_dir / "revised_analysis.json"):
        raise AnalysisInputError("复用修订请求的候选分析与当前合同重建结果不一致。")
    shutil.copytree(source_dir, target_dir)
    result = _read_json(target_dir / "result.json")
    result["reused_from_run_id"] = parent_dir.name
    result["released_evidence_unit_ids"] = released_ids
    _write_json(target_dir / "result.json", result)
    _write_json(target_dir / "validated_output.json", validated)
    _write_json(target_dir / "revised_analysis.json", analysis)
    return {
        "result": result,
        "revision": validated,
        "analysis": analysis,
        "failure": None,
    }


def _revision_request_audit(batch_results: list[dict[str, Any]]) -> dict[str, Any]:
    reused = [
        str(row["request_id"]) for row in batch_results if row.get("reused_from_run_id")
    ]
    executed = [
        str(row["request_id"]) for row in batch_results if not row.get("reused_from_run_id")
    ]
    return {
        "reused_batch_ids": reused,
        "executed_batch_ids": executed,
        "response_usage": _sum_usage(
            [row["usage"] for row in batch_results if not row.get("reused_from_run_id")]
        ),
    }


def _execute_statement_revision_request(
    run_dir: Path,
    client: Any,
    source: StatementRevisionSource,
    planned: PlannedSynthesisRequest,
    system_prompt: str,
    user_prompt: str,
    *,
    profile: ModelProfile,
) -> dict[str, Any]:
    request_dir = run_dir / "batches" / planned.request_id
    request_dir.mkdir(parents=True)
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(request_dir / "request.json", request_payload)
    _write_jsonl(request_dir / "blocking_statements.jsonl", list(source.blocking_records))
    started_at = _now()
    input_sha256 = _sha256_json(
        {
            "analysis": source.original_analysis,
            "blocking_records": source.blocking_records,
        }
    )
    provider_result: ProviderResult | None = None
    try:
        provider_result = client.complete(
            "statement_revision",
            request_payload,
            {
                "request_id": planned.request_id,
                "paper_id": source.snapshot.paper_id,
                "topic": source.snapshot.topic,
                "blocking_records": list(source.blocking_records),
                "planned_input_tokens": planned.input_tokens,
                "planned_max_output_tokens": planned.max_output_tokens,
            },
        )
        _write_provider_response(request_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        if provider_result.usage.get("prompt_tokens") != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={provider_result.usage.get('prompt_tokens')}"
            )
        parsed = json.loads(extract_chat_content(provider_result.parsed_response))
        _write_json(request_dir / "parsed_response.json", parsed)
        validated, revised_analysis, released_ids = validate_and_apply_statement_revision(
            parsed,
            original_analysis=source.original_analysis,
            blocking_records=list(source.blocking_records),
            evidence_units=list(source.evidence_units),
        )
        _write_json(request_dir / "validated_output.json", validated)
        _write_json(request_dir / "revised_analysis.json", revised_analysis)
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        result["released_evidence_unit_ids"] = released_ids
        _write_json(request_dir / "result.json", result)
        return {
            "result": result,
            "revision": validated,
            "analysis": revised_analysis,
            "failure": None,
        }
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(request_dir, exc.raw_body)
        failure = _failure("statement_revision", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {"result": result, "revision": None, "analysis": None, "failure": failure}


def _finish_failed_statement_revision_run(
    run_dir: Path,
    base_manifest: dict[str, Any],
    source: StatementRevisionSource,
    batch_results: list[dict[str, Any]],
    failure: dict[str, Any],
    *,
    revised_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan_path = run_dir / "plan" / "synthesis_plan.json"
    if plan_path.exists():
        plan = _read_json(plan_path)
        plan["status"] = "failed"
        _write_json(plan_path, plan)
    revision = (
        _read_json(run_dir / "output" / "revision.json")
        if (run_dir / "output" / "revision.json").exists()
        else None
    )
    role_reviews = _read_revision_role_reviews(run_dir, batch_results)
    reviews = _read_revision_support_reviews(run_dir, batch_results)
    coverage = _build_statement_revision_coverage(
        source,
        revised_analysis or source.original_analysis,
        revision,
        role_reviews,
        reviews,
        batch_results,
    )
    _write_json(run_dir / "coverage.json", coverage)
    _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
    manifest = {
        **base_manifest,
        "status": "failed",
        "finished_at": _now(),
        "failure_count": 1,
        "failure_codes": [failure["error_code"]],
        "batches": batch_results,
        **_revision_request_audit(batch_results),
    }
    _write_json(run_dir / "manifest.json", manifest)
    _write_failed_run_review(
        run_dir,
        manifest,
        source.snapshot,
        list(source.evidence_units),
        coverage,
        [failure],
    )
    if revision is not None:
        write_statement_revision_review(
            run_dir / "review",
            source_run_id=source.run_dir.name,
            run_id=str(base_manifest["run_id"]),
            status="failed",
            blocking_records=list(source.blocking_records),
            revision=revision,
            support_reviews=reviews,
        )
    return _result(
        run_dir,
        manifest,
        batch_count=len(batch_results),
        material_count=len(source.snapshot.materials),
    )


def _read_revision_support_reviews(
    run_dir: Path,
    batch_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    support_results = [
        row for row in batch_results if str(row.get("request_id", "")).startswith("statement_support_")
    ]
    if not support_results:
        return []
    path = run_dir / "batches" / str(support_results[-1]["request_id"]) / "validated_output.json"
    if not path.exists():
        return []
    return list(_read_json(path).get("statement_reviews", []))


def _read_revision_role_reviews(
    run_dir: Path,
    batch_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    role_results = [
        row for row in batch_results if str(row.get("request_id", "")).startswith("statement_role_")
    ]
    if not role_results:
        return []
    path = run_dir / "batches" / str(role_results[-1]["request_id"]) / "validated_output.json"
    if not path.exists():
        return []
    return list(_read_json(path).get("statement_role_reviews", []))


def _build_statement_revision_coverage(
    source: StatementRevisionSource,
    analysis: dict[str, Any],
    revision: dict[str, Any] | None,
    role_reviews: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    batch_results: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_ids = {str(row["evidence_unit_id"]) for row in source.evidence_units}
    final_coverage = build_final_evidence_coverage(analysis, evidence_ids)
    verdict_counts = Counter(str(row.get("verdict")) for row in reviews)
    role_counts = Counter(str(row.get("verdict")) for row in role_reviews)
    actions = Counter(
        str(row.get("action")) for row in (revision or {}).get("revisions", [])
    )
    return {
        "scope": "full_paper_revision",
        "planned_material_count": len(source.snapshot.materials),
        "assessed_material_count": len(source.snapshot.materials),
        "missing_material_ids": [],
        "evidence_unit_count": len(evidence_ids),
        **final_coverage,
        "source_blocking_statement_count": len(source.blocking_records),
        "revision_count": len((revision or {}).get("revisions", [])),
        "revision_action_counts": dict(actions),
        "released_evidence_unit_ids": sorted(
            str(row["evidence_unit_id"])
            for row in (revision or {}).get("released_evidence_dispositions", [])
        ),
        "statement_count": len(build_statement_records(analysis)),
        "field_aligned_statement_count": role_counts["field_aligned"],
        "misclassified_statement_count": role_counts["misclassified"],
        "directly_supported_statement_count": verdict_counts["directly_supported"],
        "grounded_inference_statement_count": verdict_counts["grounded_inference"],
        "partially_supported_statement_count": verdict_counts["partially_supported"],
        "unsupported_statement_count": verdict_counts["unsupported"],
        "completed_batch_ids": [
            str(row["request_id"]) for row in batch_results if row.get("status") == "completed"
        ],
        "failed_batch_ids": [
            str(row["request_id"]) for row in batch_results if row.get("status") == "failed"
        ],
    }


def _empty_revision_coverage(source: StatementRevisionSource | None) -> dict[str, Any]:
    return {
        "scope": "full_paper_revision",
        "planned_material_count": len(source.snapshot.materials) if source else 0,
        "assessed_material_count": 0,
        "missing_material_ids": [
            str(row["material_id"]) for row in source.snapshot.materials
        ]
        if source
        else [],
        "evidence_unit_count": len(source.evidence_units) if source else 0,
        "source_blocking_statement_count": len(source.blocking_records) if source else 0,
        "field_aligned_statement_count": 0,
        "misclassified_statement_count": 0,
        "completed_batch_ids": [],
        "failed_batch_ids": [],
    }


def _execute_evidence_request(
    run_dir: Path,
    client: Any,
    planned: PlannedRequest,
    snapshot: AnalysisInputSnapshot,
    materials_by_id: dict[str, dict[str, Any]],
    projected_by_id: dict[str, dict[str, Any]],
    all_projected: list[dict[str, Any]],
    all_quote_candidates: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
) -> dict[str, Any]:
    batch_dir = run_dir / "batches" / planned.request_id
    batch_dir.mkdir(parents=True)
    materials = [materials_by_id[material_id] for material_id in planned.material_ids]
    projected = [projected_by_id[material_id] for material_id in planned.material_ids]
    material_id_set = set(planned.material_ids)
    quote_candidates = [
        row for row in all_quote_candidates if str(row["material_id"]) in material_id_set
    ]
    system_prompt, user_prompt = build_projected_evidence_prompts(
        paper_id=snapshot.paper_id,
        paper_title=snapshot.paper_title,
        topic=snapshot.topic,
        projected_cards=projected,
        all_projected_cards=all_projected,
    )
    prompt_sha256 = _sha256_text(system_prompt + "\n" + user_prompt)
    if prompt_sha256 != planned.prompt_sha256:
        raise AnalysisInputError(
            f"执行时 Prompt 与计划不一致：request_id={planned.request_id!r}。"
        )
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(batch_dir / "request.json", request_payload)
    started_at = _now()
    provider_result: ProviderResult | None = None
    parsed: dict[str, Any] | None = None
    try:
        provider_result = client.complete(
            "evidence",
            request_payload,
            {
                "batch_id": planned.request_id,
                "request_id": planned.request_id,
                "materials": materials,
                "quote_candidates": quote_candidates,
                "paper_id": snapshot.paper_id,
                "topic": snapshot.topic,
                "planned_input_tokens": planned.input_tokens,
                "planned_max_output_tokens": planned.max_output_tokens,
            },
        )
        _write_provider_response(batch_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        actual_prompt_tokens = provider_result.usage.get("prompt_tokens")
        if actual_prompt_tokens != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={actual_prompt_tokens}"
            )
        content = extract_chat_content(provider_result.parsed_response)
        parsed = json.loads(content)
        _write_json(batch_dir / "parsed_response.json", parsed)
        validated = validate_evidence_batch(parsed, materials, quote_candidates)
        assigned = assign_evidence_unit_ids(
            planned.request_id,
            validated["evidence_units"],
            materials,
            generation_id=snapshot.generation_id,
        )
        assessments = [
            {**row, "request_id": planned.request_id, "batch_id": planned.request_id}
            for row in validated["material_assessments"]
        ]
        normalized = {
            "schema_version": validated["schema_version"],
            "material_assessments": assessments,
            "evidence_units": assigned,
        }
        _write_json(batch_dir / "validated_output.json", normalized)
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=list(planned.material_ids),
            prompt_sha256=planned.prompt_sha256,
            input_sha256=_sha256_json(materials),
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(batch_dir / "result.json", result)
        return {
            "result": result,
            "results": [result],
            "assessments": assessments,
            "evidence_units": assigned,
            "failure": None,
        }
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(batch_dir, exc.raw_body)
        failure = _failure(
            "evidence_batch",
            exc,
            batch_id=planned.request_id,
            material_ids=list(planned.material_ids),
        )
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=list(planned.material_ids),
            prompt_sha256=planned.prompt_sha256,
            input_sha256=_sha256_json(materials),
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(batch_dir / "result.json", result)
        if isinstance(exc, ContractViolation) and parsed is not None:
            try:
                local_failures = collect_correctable_material_failures(
                    parsed,
                    materials,
                    quote_candidates,
                )
                correction = _execute_evidence_correction(
                    batch_dir=batch_dir,
                    client=client,
                    source_planned=planned,
                    source_payload=parsed,
                    source_failure=failure,
                    local_failures=local_failures,
                    snapshot=snapshot,
                    materials=materials,
                    projected=projected,
                    quote_candidates=quote_candidates,
                    profile=profile,
                    token_counter=token_counter,
                )
            except (EvidenceCorrectionUnavailable, PlanningError) as correction_exc:
                _write_json(
                    batch_dir / "resolution.json",
                    {
                        "schema_version": EVIDENCE_CORRECTION_SCHEMA_VERSION,
                        "status": "not_attempted",
                        "source_request_id": planned.request_id,
                        "source_failure": failure,
                        "reason_code": getattr(
                            correction_exc,
                            "code",
                            "correction.planning_failed",
                        ),
                        "reason": str(correction_exc),
                    },
                )
            else:
                source_result = copy.deepcopy(result)
                source_result["correction_request_id"] = correction["result"]["request_id"]
                if correction["failure"] is None:
                    source_result["resolution_status"] = "corrected"
                    source_result["resolved_by_request_id"] = correction["result"]["request_id"]
                    _write_json(
                        batch_dir / "resolution.json",
                        {
                            "schema_version": EVIDENCE_CORRECTION_SCHEMA_VERSION,
                            "status": "corrected",
                            "source_request_id": planned.request_id,
                            "source_failure": failure,
                            "correction_request_id": correction["result"]["request_id"],
                            "target_material_ids": correction["target_material_ids"],
                            "changes": correction["changes"],
                        },
                    )
                    return {
                        "result": correction["result"],
                        "results": [source_result, correction["result"]],
                        "assessments": correction["assessments"],
                        "evidence_units": correction["evidence_units"],
                        "failure": None,
                    }
                source_result["resolution_status"] = "correction_failed"
                _write_json(
                    batch_dir / "resolution.json",
                    {
                        "schema_version": EVIDENCE_CORRECTION_SCHEMA_VERSION,
                        "status": "correction_failed",
                        "source_request_id": planned.request_id,
                        "source_failure": failure,
                        "correction_request_id": correction["result"]["request_id"],
                        "target_material_ids": correction["target_material_ids"],
                        "correction_failure": correction["failure"],
                    },
                )
                return {
                    "result": correction["result"],
                    "results": [source_result, correction["result"]],
                    "assessments": [],
                    "evidence_units": [],
                    "failure": correction["failure"],
                }
        return {
            "result": result,
            "results": [result],
            "assessments": [],
            "evidence_units": [],
            "failure": failure,
        }


def _execute_evidence_correction(
    *,
    batch_dir: Path,
    client: Any,
    source_planned: PlannedRequest,
    source_payload: dict[str, Any],
    source_failure: dict[str, Any],
    local_failures: list[dict[str, Any]],
    snapshot: AnalysisInputSnapshot,
    materials: list[dict[str, Any]],
    projected: list[dict[str, Any]],
    quote_candidates: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
) -> dict[str, Any]:
    target_material_ids = [str(row["material_id"]) for row in local_failures]
    target_set = set(target_material_ids)
    target_projected = [
        row for row in projected if str(row["material_id"]) in target_set
    ]
    system_prompt, user_prompt = build_evidence_correction_prompts(
        paper_id=snapshot.paper_id,
        paper_title=snapshot.paper_title,
        topic=snapshot.topic,
        source_request_id=source_planned.request_id,
        source_payload=source_payload,
        projected_cards=target_projected,
        quote_candidates=quote_candidates,
        failures=local_failures,
    )
    planned = plan_evidence_correction(
        generation_id=snapshot.generation_id,
        source_request_id=source_planned.request_id,
        material_ids=target_material_ids,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        profile=profile,
        token_counter=token_counter,
    )
    correction_dir = batch_dir.parent / planned.request_id
    correction_dir.mkdir(parents=True)
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(correction_dir / "request.json", request_payload)
    _write_json(
        correction_dir / "source_failure.json",
        {
            "source_request_id": source_planned.request_id,
            "source_failure": source_failure,
            "local_failures": local_failures,
        },
    )
    started_at = _now()
    provider_result: ProviderResult | None = None
    try:
        provider_result = client.complete(
            "evidence_batch_correction",
            request_payload,
            {
                "batch_id": planned.request_id,
                "request_id": planned.request_id,
                "source_request_id": source_planned.request_id,
                "source_payload": source_payload,
                "source_material_results": [
                    row
                    for row in source_payload["material_results"]
                    if str(row["material_id"]) in target_set
                ],
                "target_material_ids": target_material_ids,
                "materials": [
                    row for row in materials if str(row["material_id"]) in target_set
                ],
                "quote_candidates": [
                    row
                    for row in quote_candidates
                    if str(row["material_id"]) in target_set
                ],
                "failures": local_failures,
                "paper_id": snapshot.paper_id,
                "topic": snapshot.topic,
                "planned_input_tokens": planned.input_tokens,
                "planned_max_output_tokens": planned.max_output_tokens,
            },
        )
        _write_provider_response(correction_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        actual_prompt_tokens = provider_result.usage.get("prompt_tokens")
        if actual_prompt_tokens != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={actual_prompt_tokens}"
            )
        parsed = json.loads(extract_chat_content(provider_result.parsed_response))
        _write_json(correction_dir / "parsed_response.json", parsed)
        applied = validate_and_apply_evidence_correction(
            parsed,
            source_payload=source_payload,
            target_material_ids=target_material_ids,
            materials=materials,
            quote_candidates=quote_candidates,
            failures=local_failures,
        )
        validated = applied["normalized"]
        assigned = assign_evidence_unit_ids(
            planned.request_id,
            validated["evidence_units"],
            materials,
            generation_id=snapshot.generation_id,
        )
        assessments = [
            {**row, "request_id": planned.request_id, "batch_id": planned.request_id}
            for row in validated["material_assessments"]
        ]
        normalized = {
            "schema_version": validated["schema_version"],
            "material_assessments": assessments,
            "evidence_units": assigned,
        }
        _write_json(correction_dir / "applied_correction.json", applied)
        _write_json(correction_dir / "validated_output.json", normalized)
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=list(source_planned.material_ids),
            prompt_sha256=planned.prompt_sha256,
            input_sha256=_sha256_json(
                {
                    "source_payload": source_payload,
                    "target_material_ids": target_material_ids,
                }
            ),
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        result.update(
            {
                "stage": "evidence_batch_correction",
                "source_request_id": source_planned.request_id,
                "correction_pass": 1,
                "corrected_material_ids": target_material_ids,
            }
        )
        _write_json(correction_dir / "result.json", result)
        return {
            "result": result,
            "assessments": assessments,
            "evidence_units": assigned,
            "failure": None,
            "target_material_ids": target_material_ids,
            "changes": applied["changes"],
        }
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(correction_dir, exc.raw_body)
        failure = _failure(
            "evidence_batch_correction",
            exc,
            batch_id=planned.request_id,
            material_ids=target_material_ids,
        )
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=list(source_planned.material_ids),
            prompt_sha256=planned.prompt_sha256,
            input_sha256=_sha256_json(
                {
                    "source_payload": source_payload,
                    "target_material_ids": target_material_ids,
                }
            ),
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        result.update(
            {
                "stage": "evidence_batch_correction",
                "source_request_id": source_planned.request_id,
                "correction_pass": 1,
                "corrected_material_ids": target_material_ids,
            }
        )
        _write_json(correction_dir / "result.json", result)
        return {
            "result": result,
            "assessments": [],
            "evidence_units": [],
            "failure": failure,
            "target_material_ids": target_material_ids,
            "changes": [],
        }


def _execute_synthesis_pipeline(
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    evidence_units: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    *,
    profile: ModelProfile,
    token_counter: Any,
    section_map: SectionMap | None,
    retry_context: Path | None,
    retry_batch_id: str | None,
    adjudication_constraints: list[dict[str, Any]] | None = None,
    accepted_role_override_ids: set[str] | None = None,
    accepted_role_override_reviews: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    system_prompt, user_prompt = build_synthesis_prompts(
        snapshot,
        evidence_units,
        assessments,
        adjudication_constraints,
    )
    evidence_ids = tuple(str(row["evidence_unit_id"]) for row in evidence_units)
    try:
        planned = plan_synthesis_request(
            generation_id=snapshot.generation_id,
            stage="paper_synthesis",
            section_id=None,
            evidence_unit_ids=evidence_ids,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=profile.paper_synthesis_max_output_tokens,
            profile=profile,
            token_counter=token_counter,
        )
    except PlanningError as exc:
        if exc.code != "planning.synthesis_context_window_exceeded":
            return _synthesis_planning_failure(run_dir, exc, [])
        return _execute_hierarchical_synthesis(
            run_dir=run_dir,
            client=client,
            snapshot=snapshot,
            evidence_units=evidence_units,
            profile=profile,
            token_counter=token_counter,
            section_map=section_map,
            adjudication_constraints=adjudication_constraints,
            accepted_role_override_ids=accepted_role_override_ids,
            accepted_role_override_reviews=accepted_role_override_reviews,
        )

    _write_synthesis_plan(
        run_dir,
        strategy="direct_paper_synthesis",
        requests=[planned],
        cross_section_evidence_ids=(),
        status="planned",
    )
    if retry_context is not None and str(retry_batch_id).startswith(
        ("evidence_disposition_", "statement_role_", "statement_support_")
    ):
        outcome = _reuse_paper_synthesis_request(
            retry_context,
            run_dir,
            planned,
            evidence_ids=set(evidence_ids),
            evidence_units=evidence_units,
        )
    else:
        outcome = _execute_paper_synthesis_request(
            run_dir,
            client,
            snapshot,
            planned,
            system_prompt,
            user_prompt,
            evidence_ids=set(evidence_ids),
            evidence_units=evidence_units,
            profile=profile,
        )
    if outcome["failure"]:
        _write_synthesis_plan(
            run_dir,
            strategy="direct_paper_synthesis",
            requests=[planned],
            cross_section_evidence_ids=(),
            status="failed",
        )
        return {"results": [outcome["result"]], "analysis": None, "coverage": None, "failure": outcome["failure"]}
    return _finalize_paper_analysis_draft(
        run_dir=run_dir,
        client=client,
        snapshot=snapshot,
        paper_analysis_draft=outcome["draft"],
        evidence_units=evidence_units,
        profile=profile,
        token_counter=token_counter,
        strategy="direct_paper_synthesis",
        cross_section_evidence_ids=(),
        requests=[planned],
        results=[outcome["result"]],
        retry_context=retry_context,
        retry_batch_id=retry_batch_id,
        accepted_role_override_ids=accepted_role_override_ids,
        accepted_role_override_reviews=accepted_role_override_reviews,
        forbidden_statement_ids={
            str(row["source_statement_id"])
            for row in (adjudication_constraints or [])
        },
    )


def _execute_evidence_claim_support_gate(
    *,
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    evidence_units: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
    section_map: SectionMap | None = None,
    reuse_from: Path | None = None,
    accepted_override_ids: set[str] | None = None,
    accepted_override_reviews: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence_ids = tuple(str(row["evidence_unit_id"]) for row in evidence_units)
    accepted_override_ids = accepted_override_ids or set()
    accepted_override_reviews = accepted_override_reviews or {}
    planned_requests: list[PlannedSynthesisRequest] = []
    first_pass_rows: list[
        tuple[Any, PlannedSynthesisRequest, str, str]
    ] = []
    try:
        review_batches = plan_evidence_claim_review_batches(
            evidence_units,
            section_map=section_map,
            max_batch_size=profile.evidence_claim_batch_size,
        )
        for batch in review_batches:
            batch_units = list(batch.evidence_units)
            system_prompt, user_prompt = build_evidence_claim_support_prompts(
                paper_id=snapshot.paper_id,
                paper_title=snapshot.paper_title,
                evidence_units=batch_units,
            )
            planned = plan_synthesis_request(
                generation_id=snapshot.generation_id,
                stage="evidence_claim_support",
                section_id=batch.section_id,
                evidence_unit_ids=batch.evidence_unit_ids,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=profile.evidence_claim_output_tokens(len(batch_units)),
                profile=profile,
                token_counter=token_counter,
            )
            planned_requests.append(planned)
            first_pass_rows.append((batch, planned, system_prompt, user_prompt))
    except Exception as exc:
        failure = _failure("evidence_claim_support_planning", exc)
        _write_evidence_claim_support_plan(run_dir, [], status="failed")
        return {"results": [], "reviews": [], "coverage": {}, "failure": failure}

    _write_json(
        run_dir / "schemas" / "evidence_claim_support.schema.json",
        build_evidence_claim_support_schema(list(evidence_ids)),
    )
    _write_text(
        run_dir / "prompts" / "evidence_claim_support_system.md",
        EVIDENCE_CLAIM_SUPPORT_SYSTEM_PROMPT + "\n",
    )
    _write_evidence_claim_support_plan(run_dir, planned_requests, status="planned")

    results: list[dict[str, Any]] = []
    first_pass_reviews: list[dict[str, Any]] = []
    first_request_ids: dict[str, str] = {}
    operational_failure: dict[str, Any] | None = None
    for batch, planned, system_prompt, user_prompt in first_pass_rows:
        batch_units = list(batch.evidence_units)
        outcome = (
            _reuse_evidence_claim_support_request(
                reuse_from,
                run_dir,
                planned,
                evidence_units=batch_units,
                enforce_policy=False,
            )
            if reuse_from is not None
            else _execute_or_load_cached_evidence_claim_review(
                run_dir=run_dir,
                client=client,
                snapshot=snapshot,
                planned=planned,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                evidence_units=batch_units,
                profile=profile,
                provider_task="evidence_claim_support",
                review_pass="first_pass",
            )
        )
        results.append(outcome["result"])
        first_pass_reviews.extend(outcome["reviews"])
        first_request_ids.update(
            {str(row["evidence_unit_id"]): planned.request_id for row in outcome["reviews"]}
        )
        if outcome["failure"]:
            operational_failure = outcome["failure"]
            break

    confirmation_reviews: dict[str, dict[str, Any]] = {}
    confirmation_request_ids: dict[str, str] = {}
    evidence_by_id = {str(row["evidence_unit_id"]): row for row in evidence_units}
    deterministic_quote_support_ids = {
        evidence_id
        for evidence_id, unit in evidence_by_id.items()
        if has_ordered_verbatim_quote_support(unit)
    }
    if operational_failure is None:
        for first_review in first_pass_reviews:
            evidence_id = str(first_review["evidence_unit_id"])
            if first_review["verdict"] == "directly_supported":
                continue
            if evidence_id in deterministic_quote_support_ids:
                continue
            if evidence_id in accepted_override_ids and _evidence_claim_override_matches(
                first_review,
                accepted_override_reviews.get(evidence_id),
            ):
                continue
            try:
                context = build_evidence_claim_confirmation_context(
                    target_evidence_unit=evidence_by_id[evidence_id],
                    first_pass_review=first_review,
                    materials=list(snapshot.materials),
                )
                system_prompt, user_prompt = build_evidence_claim_confirmation_prompts(
                    paper_id=snapshot.paper_id,
                    paper_title=snapshot.paper_title,
                    confirmation_context=context,
                )
                planned = plan_synthesis_request(
                    generation_id=snapshot.generation_id,
                    stage="evidence_claim_confirmation",
                    section_id=None,
                    evidence_unit_ids=(evidence_id,),
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_output_tokens=profile.evidence_claim_output_tokens(1),
                    profile=profile,
                    token_counter=token_counter,
                )
                planned_requests.append(planned)
                _write_text(
                    run_dir / "prompts" / "evidence_claim_confirmation_system.md",
                    system_prompt + "\n",
                )
                _write_evidence_claim_support_plan(
                    run_dir,
                    planned_requests,
                    status="planned",
                )
                outcome = _execute_or_load_cached_evidence_claim_review(
                    run_dir=run_dir,
                    client=client,
                    snapshot=snapshot,
                    planned=planned,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    evidence_units=[evidence_by_id[evidence_id]],
                    profile=profile,
                    provider_task="evidence_claim_confirmation",
                    review_pass="confirmation",
                    extra_context={"confirmation_context": context},
                )
            except Exception as exc:
                operational_failure = _failure(
                    "evidence_claim_confirmation_planning",
                    exc,
                    batch_id=evidence_id,
                )
                break
            results.append(outcome["result"])
            if outcome["reviews"]:
                confirmation_reviews[evidence_id] = outcome["reviews"][0]
                confirmation_request_ids[evidence_id] = planned.request_id
            if outcome["failure"]:
                operational_failure = outcome["failure"]
                break

    decisions = build_evidence_claim_gate_decisions(
        first_pass_reviews=first_pass_reviews,
        confirmation_reviews=confirmation_reviews,
        first_request_ids=first_request_ids,
        confirmation_request_ids=confirmation_request_ids,
        accepted_override_ids=accepted_override_ids,
        accepted_override_reviews=accepted_override_reviews,
        deterministic_quote_support_ids=deterministic_quote_support_ids,
    )
    reviews_by_id = {
        str(row["evidence_unit_id"]): row
        for row in effective_evidence_claim_reviews(decisions)
    }
    reviews = [reviews_by_id[evidence_id] for evidence_id in evidence_ids if evidence_id in reviews_by_id]
    _write_jsonl(run_dir / "output" / "evidence_claim_first_pass_reviews.jsonl", first_pass_reviews)
    _write_jsonl(
        run_dir / "output" / "evidence_claim_confirmation_reviews.jsonl",
        list(confirmation_reviews.values()),
    )
    _write_jsonl(run_dir / "output" / "evidence_claim_gate_decisions.jsonl", decisions)
    _write_jsonl(run_dir / "output" / "evidence_claim_support_reviews.jsonl", reviews)

    failure = operational_failure
    if failure is None:
        disagreements = [row for row in decisions if row["decision"] == "gate_disagreement"]
        confirmed = [row for row in decisions if row["decision"] == "confirmed_blocker"]
        missing = [row for row in decisions if row["decision"] == "confirmation_missing"]
        if disagreements:
            failure = _failure(
                "evidence_claim_support_stability",
                AnalysisInputError(
                    "Evidence claim 两轮门禁结论冲突："
                    f"{[row['evidence_unit_id'] for row in disagreements]}"
                ),
                batch_id=str(disagreements[0].get("confirmation_request_id") or ""),
                error_code="support.evidence_claim_gate_unstable",
            )
        elif confirmed:
            first = confirmed[0]["confirmation_review"]
            failure = _failure(
                "evidence_claim_support",
                ContractViolation(
                    "support.evidence_claim_not_fully_supported",
                    f"evidence_unit_id={first['evidence_unit_id']!r}, verdict={first['verdict']!r}, "
                    f"unsupported_fragments={first['unsupported_fragments']}",
                    path="$.evidence_claim_reviews[0]",
                ),
                batch_id=str(confirmed[0].get("confirmation_request_id") or ""),
            )
        elif missing:
            failure = _failure(
                "evidence_claim_support_stability",
                AnalysisInputError("Evidence claim 阻断项缺少窄上下文复核结果。"),
                error_code="coverage.evidence_claim_confirmation_missing",
            )

    counts = Counter(str(row.get("verdict")) for row in reviews)
    decision_counts = Counter(str(row.get("decision")) for row in decisions)
    coverage = {
        "evidence_claim_review_count": len(reviews),
        "directly_supported_evidence_claim_count": counts["directly_supported"],
        "partially_supported_evidence_claim_count": counts["partially_supported"],
        "unsupported_evidence_claim_count": counts["unsupported"],
        "human_override_evidence_claim_count": decision_counts["accepted_human_override"],
        "deterministic_quote_support_count": decision_counts["passed_quote_containment"],
        "evidence_claim_first_pass_batch_count": len(first_pass_rows),
        "evidence_claim_confirmation_count": len(confirmation_reviews),
        "confirmed_evidence_claim_blocker_count": decision_counts["confirmed_blocker"],
        "unstable_evidence_claim_count": decision_counts["gate_disagreement"],
        "evidence_claim_cache_hit_count": sum(
            bool(row.get("cache_hit")) for row in results
        ),
    }
    _write_evidence_claim_support_plan(
        run_dir,
        planned_requests,
        status="failed" if failure else "completed",
    )
    return {
        "results": results,
        "reviews": reviews,
        "coverage": coverage,
        "failure": failure,
    }


def _write_evidence_claim_support_plan(
    run_dir: Path,
    requests: list[PlannedSynthesisRequest],
    *,
    status: str,
) -> None:
    _write_json(
        run_dir / "plan" / "evidence_claim_support_plan.json",
        {
            "schema_version": EVIDENCE_CLAIM_GATE_PLAN_SCHEMA_VERSION,
            "strategy": "section_bounded_evidence_claim_support_with_confirmation",
            "status": status,
            "requests": [dataclasses.asdict(row) for row in requests],
        },
    )


def _execute_evidence_claim_support_request(
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    planned: PlannedSynthesisRequest,
    system_prompt: str,
    user_prompt: str,
    *,
    evidence_units: list[dict[str, Any]],
    profile: ModelProfile,
    provider_task: str = "evidence_claim_support",
    review_pass: str = "first_pass",
    extra_context: dict[str, Any] | None = None,
    enforce_policy: bool = True,
    accepted_override_ids: set[str] | None = None,
    accepted_override_reviews: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request_dir = run_dir / "batches" / planned.request_id
    request_dir.mkdir(parents=True)
    _write_jsonl(request_dir / "evidence_units.jsonl", evidence_units)
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(request_dir / "request.json", request_payload)
    started_at = _now()
    input_sha256 = _sha256_json(evidence_units)
    provider_result: ProviderResult | None = None
    validated: dict[str, Any] | None = None
    try:
        provider_context = {
            "request_id": planned.request_id,
            "paper_id": snapshot.paper_id,
            "topic": snapshot.topic,
            "evidence_units": evidence_units,
            "review_pass": review_pass,
            "planned_input_tokens": planned.input_tokens,
            "planned_max_output_tokens": planned.max_output_tokens,
        }
        provider_context.update(extra_context or {})
        provider_result = client.complete(
            provider_task,
            request_payload,
            provider_context,
        )
        _write_provider_response(request_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        if provider_result.usage.get("prompt_tokens") != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={provider_result.usage.get('prompt_tokens')}"
            )
        parsed = json.loads(extract_chat_content(provider_result.parsed_response))
        _write_json(request_dir / "parsed_response.json", parsed)
        validated = validate_evidence_claim_support_review(parsed, evidence_units)
        _write_json(request_dir / "validated_output.json", validated)
        if enforce_policy:
            _enforce_evidence_claim_support_with_overrides(
                validated,
                accepted_override_ids or set(),
                accepted_override_reviews or {},
            )
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {
            "result": result,
            "reviews": validated["evidence_claim_reviews"],
            "validated": validated,
            "failure": None,
        }
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(request_dir, exc.raw_body)
        failure = _failure("evidence_claim_support", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {
            "result": result,
            "reviews": (
                validated["evidence_claim_reviews"] if validated is not None else []
            ),
            "validated": validated,
            "failure": failure,
        }


def _execute_or_load_cached_evidence_claim_review(
    *,
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    planned: PlannedSynthesisRequest,
    system_prompt: str,
    user_prompt: str,
    evidence_units: list[dict[str, Any]],
    profile: ModelProfile,
    provider_task: str,
    review_pass: str,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_sha256 = _sha256_json(evidence_units)
    identity = {
        "schema_version": "llm.evidence_claim_cache.v1",
        "provider": str(client.provider),
        "model": str(client.model),
        "model_profile_sha256": profile.sha256,
        "tokenizer_revision": profile.tokenizer_revision,
        "provider_task": provider_task,
        "review_pass": review_pass,
        "prompt_sha256": planned.prompt_sha256,
        "input_sha256": input_sha256,
        "evidence_unit_ids": [str(row["evidence_unit_id"]) for row in evidence_units],
    }
    cache_key = _sha256_json(identity)
    cache_path = (
        run_dir.parents[1]
        / "evidence_claim_cache"
        / f"{cache_key.removeprefix('sha256:')[:40]}.json"
    )
    request_dir = run_dir / "batches" / planned.request_id
    if cache_path.exists():
        started_at = _now()
        try:
            record = _read_json(cache_path)
            if record.get("identity") != identity or record.get("cache_key") != cache_key:
                raise AnalysisInputError(
                    f"Evidence claim 缓存身份不一致：{cache_path}"
                )
            validated = validate_evidence_claim_support_review(
                record.get("validated_output"),
                evidence_units,
            )
            request_dir.mkdir(parents=True)
            _write_jsonl(request_dir / "evidence_units.jsonl", evidence_units)
            _write_json(
                request_dir / "request.json",
                build_chat_request(
                    client.model,
                    system_prompt,
                    user_prompt,
                    max_tokens=planned.max_output_tokens,
                    thinking=profile.thinking,
                ),
            )
            _write_json(request_dir / "validated_output.json", validated)
            result = _request_result(
                request_id=planned.request_id,
                status="completed",
                material_ids=[],
                prompt_sha256=planned.prompt_sha256,
                input_sha256=input_sha256,
                started_at=started_at,
                provider_result=None,
                provider="content_hash_cache",
                requested_model=str(client.model),
                planned_input_tokens=planned.input_tokens,
                planned_max_output_tokens=planned.max_output_tokens,
            )
            result.update({"cache_hit": True, "cache_key": cache_key, "cache_path": str(cache_path)})
            _write_json(request_dir / "result.json", result)
            return {
                "result": result,
                "reviews": validated["evidence_claim_reviews"],
                "validated": validated,
                "failure": None,
            }
        except Exception as exc:
            request_dir.mkdir(parents=True, exist_ok=True)
            failure = _failure(
                "evidence_claim_support_cache",
                exc,
                batch_id=planned.request_id,
                error_code="cache.evidence_claim_invalid",
            )
            result = _request_result(
                request_id=planned.request_id,
                status="failed",
                material_ids=[],
                prompt_sha256=planned.prompt_sha256,
                input_sha256=input_sha256,
                started_at=started_at,
                provider_result=None,
                provider="content_hash_cache",
                requested_model=str(client.model),
                failure=failure,
                planned_input_tokens=planned.input_tokens,
                planned_max_output_tokens=planned.max_output_tokens,
            )
            result.update({"cache_hit": True, "cache_key": cache_key, "cache_path": str(cache_path)})
            _write_json(request_dir / "result.json", result)
            return {"result": result, "reviews": [], "validated": None, "failure": failure}

    outcome = _execute_evidence_claim_support_request(
        run_dir,
        client,
        snapshot,
        planned,
        system_prompt,
        user_prompt,
        evidence_units=evidence_units,
        profile=profile,
        provider_task=provider_task,
        review_pass=review_pass,
        extra_context=extra_context,
        enforce_policy=False,
    )
    outcome["result"]["cache_hit"] = False
    outcome["result"]["cache_key"] = cache_key
    _write_json(request_dir / "result.json", outcome["result"])
    if outcome["failure"] is None:
        try:
            _write_json(
                cache_path,
                {
                    "schema_version": "llm.evidence_claim_cache.v1",
                    "cache_key": cache_key,
                    "created_at": _now(),
                    "identity": identity,
                    "validated_output": outcome["validated"],
                },
            )
        except Exception as exc:
            failure = _failure(
                "evidence_claim_support_cache",
                exc,
                batch_id=planned.request_id,
                error_code="cache.evidence_claim_write_failed",
            )
            outcome["failure"] = failure
            outcome["result"].update(
                {
                    "status": "failed",
                    "error_code": failure["error_code"],
                    "error_message": failure["error_message"],
                    "error_path": failure.get("error_path"),
                }
            )
            _write_json(request_dir / "result.json", outcome["result"])
    return outcome


def _enforce_evidence_claim_support_with_overrides(
    validated_review: dict[str, Any],
    accepted_override_ids: set[str],
    accepted_override_reviews: dict[str, dict[str, Any]],
) -> None:
    blockers = blocking_evidence_claim_support_reviews(validated_review)
    unaccepted = [
        row
        for row in blockers
        if str(row["evidence_unit_id"]) not in accepted_override_ids
        or not _evidence_claim_override_matches(
            row,
            accepted_override_reviews.get(str(row["evidence_unit_id"])),
        )
    ]
    if not unaccepted:
        return
    first = unaccepted[0]
    index = validated_review["evidence_claim_reviews"].index(first)
    raise ContractViolation(
        "support.evidence_claim_not_fully_supported",
        f"evidence_unit_id={first['evidence_unit_id']!r}, verdict={first['verdict']!r}, "
        f"unsupported_fragments={first['unsupported_fragments']}",
        path=f"$.evidence_claim_reviews[{index}]",
    )


def _evidence_claim_override_matches(
    current_review: dict[str, Any],
    source_review: dict[str, Any] | None,
) -> bool:
    return bool(
        source_review
        and current_review.get("verdict") == source_review.get("verdict")
        and current_review.get("unsupported_fragments")
        == source_review.get("unsupported_fragments")
    )


def _reuse_evidence_claim_support_request(
    parent_dir: Path,
    run_dir: Path,
    planned: PlannedSynthesisRequest,
    *,
    evidence_units: list[dict[str, Any]],
    enforce_policy: bool = True,
) -> dict[str, Any]:
    source = parent_dir / "batches" / planned.request_id
    target = run_dir / "batches" / planned.request_id
    try:
        source_result = _read_json(source / "result.json")
        if source_result.get("status") != "completed":
            raise AnalysisInputError(f"父运行 Evidence claim 核验未完成：{planned.request_id}")
        if source_result.get("prompt_sha256") != planned.prompt_sha256:
            raise AnalysisInputError(f"父运行 Evidence claim 核验提示词哈希不一致：{planned.request_id}")
        validated = validate_evidence_claim_support_review(
            _read_json(source / "validated_output.json"),
            evidence_units,
        )
        if enforce_policy:
            enforce_evidence_claim_support_policy(validated)
        shutil.copytree(source, target)
        result = _read_json(target / "result.json")
        result["reused_from_run_id"] = parent_dir.name
        _write_json(target / "result.json", result)
        _write_json(target / "validated_output.json", validated)
        return {
            "result": result,
            "reviews": validated["evidence_claim_reviews"],
            "validated": validated,
            "failure": None,
        }
    except Exception as exc:
        target.mkdir(parents=True, exist_ok=True)
        failure = _failure("evidence_claim_support_reuse", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=_sha256_json(evidence_units),
            started_at=_now(),
            provider_result=None,
            provider="not_called",
            requested_model="not_called",
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(target / "result.json", result)
        return {"result": result, "reviews": [], "validated": None, "failure": failure}


def _execute_hierarchical_synthesis(
    *,
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    evidence_units: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
    section_map: SectionMap | None,
    adjudication_constraints: list[dict[str, Any]] | None = None,
    accepted_role_override_ids: set[str] | None = None,
    accepted_role_override_reviews: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if section_map is None:
        try:
            section_map = build_section_map(
                run_dir.parents[2] / snapshot.paper_id / "structure" / "structure.json",
                list(snapshot.materials),
                paper_id=snapshot.paper_id,
                generation_id=snapshot.generation_id,
            )
            _write_json(run_dir / "input" / "section_map.json", _section_map_to_dict(section_map))
        except Exception as exc:
            return _synthesis_planning_failure(run_dir, exc, [])

    try:
        assignment = assign_evidence_to_sections(evidence_units, section_map)
    except Exception as exc:
        return _synthesis_planning_failure(run_dir, exc, [])
    sections = {section.section_id: section for section in section_map.sections}
    direct_by_section: dict[str, list[dict[str, Any]]] = {section_id: [] for section_id in sections}
    paper_root_evidence: list[dict[str, Any]] = []
    for unit in evidence_units:
        section_id = assignment.evidence_to_section[str(unit["evidence_unit_id"])]
        if section_id == PAPER_ROOT_SECTION_ID:
            paper_root_evidence.append(unit)
        else:
            direct_by_section[section_id].append(unit)

    active: set[str] = set()
    for section_id, units in direct_by_section.items():
        if not units:
            continue
        current: str | None = section_id
        while current is not None:
            active.add(current)
            current = sections[current].parent_section_id
    ordered = sorted(active, key=lambda section_id: (sections[section_id].level, sections[section_id].start_line), reverse=True)
    summaries: dict[str, dict[str, Any]] = {}
    requests: list[PlannedSynthesisRequest] = []
    results: list[dict[str, Any]] = []
    for section_id in ordered:
        section = sections[section_id]
        child_summaries = [
            summaries[child.section_id]
            for child in section_map.sections
            if child.parent_section_id == section_id and child.section_id in summaries
        ]
        expected_ids: list[str] = [
            str(unit["evidence_unit_id"]) for unit in direct_by_section[section_id]
        ]
        for summary in child_summaries:
            expected_ids.extend(sorted(summary_evidence_ids(summary)))
        if len(expected_ids) != len(set(expected_ids)):
            return _synthesis_planning_failure(
                run_dir,
                AnalysisInputError(f"章节 {section_id!r} 收到重复 evidence_unit_id。"),
                requests,
                results,
            )
        system_prompt, user_prompt = build_section_summary_prompts(
            paper_id=snapshot.paper_id,
            paper_title=snapshot.paper_title,
            topic=snapshot.topic,
            section=section,
            direct_evidence_units=direct_by_section[section_id],
            child_section_summaries=child_summaries,
        )
        try:
            planned = plan_synthesis_request(
                generation_id=snapshot.generation_id,
                stage="section_summary",
                section_id=section_id,
                evidence_unit_ids=tuple(expected_ids),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=profile.section_summary_max_output_tokens,
                profile=profile,
                token_counter=token_counter,
            )
        except Exception as exc:
            return _synthesis_planning_failure(run_dir, exc, requests, results)
        requests.append(planned)
        outcome = _execute_section_summary_request(
            run_dir,
            client,
            snapshot,
            planned,
            system_prompt,
            user_prompt,
            profile=profile,
        )
        results.append(outcome["result"])
        if outcome["failure"]:
            _write_synthesis_plan(
                run_dir,
                strategy="section_then_paper_synthesis",
                requests=requests,
                cross_section_evidence_ids=assignment.cross_section_evidence_ids,
                status="failed",
            )
            return {"results": results, "analysis": None, "coverage": None, "failure": outcome["failure"]}
        summaries[section_id] = outcome["summary"]

    root_summaries = [
        summaries[section.section_id]
        for section in section_map.sections
        if section.section_id in summaries and section.parent_section_id not in active
    ]
    system_prompt, user_prompt = build_hierarchical_paper_prompts(
        paper_id=snapshot.paper_id,
        paper_title=snapshot.paper_title,
        topic=snapshot.topic,
        root_section_summaries=root_summaries,
        paper_root_evidence_units=paper_root_evidence,
        cross_section_evidence_ids=assignment.cross_section_evidence_ids,
        adjudication_constraints=adjudication_constraints,
    )
    all_evidence_ids = tuple(str(unit["evidence_unit_id"]) for unit in evidence_units)
    try:
        final_plan = plan_synthesis_request(
            generation_id=snapshot.generation_id,
            stage="paper_synthesis",
            section_id=None,
            evidence_unit_ids=all_evidence_ids,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=profile.paper_synthesis_max_output_tokens,
            profile=profile,
            token_counter=token_counter,
        )
    except Exception as exc:
        return _synthesis_planning_failure(run_dir, exc, requests, results)
    requests.append(final_plan)
    _write_synthesis_plan(
        run_dir,
        strategy="section_then_paper_synthesis",
        requests=requests,
        cross_section_evidence_ids=assignment.cross_section_evidence_ids,
        status="planned",
    )
    outcome = _execute_paper_synthesis_request(
        run_dir,
        client,
        snapshot,
        final_plan,
        system_prompt,
        user_prompt,
        evidence_ids=set(all_evidence_ids),
        evidence_units=evidence_units,
        profile=profile,
    )
    results.append(outcome["result"])
    if outcome["failure"]:
        _write_synthesis_plan(
            run_dir,
            strategy="section_then_paper_synthesis",
            requests=requests,
            cross_section_evidence_ids=assignment.cross_section_evidence_ids,
            status="failed",
        )
        return {"results": results, "analysis": None, "coverage": None, "failure": outcome["failure"]}
    return _finalize_paper_analysis_draft(
        run_dir=run_dir,
        client=client,
        snapshot=snapshot,
        paper_analysis_draft=outcome["draft"],
        evidence_units=evidence_units,
        profile=profile,
        token_counter=token_counter,
        strategy="section_then_paper_synthesis",
        cross_section_evidence_ids=assignment.cross_section_evidence_ids,
        requests=requests,
        results=results,
        accepted_role_override_ids=accepted_role_override_ids,
        accepted_role_override_reviews=accepted_role_override_reviews,
        forbidden_statement_ids={
            str(row["source_statement_id"])
            for row in (adjudication_constraints or [])
        },
    )


def _reuse_paper_synthesis_request(
    parent_dir: Path,
    run_dir: Path,
    planned: PlannedSynthesisRequest,
    *,
    evidence_ids: set[str],
    evidence_units: list[dict[str, Any]],
) -> dict[str, Any]:
    source = parent_dir / "batches" / planned.request_id
    target = run_dir / "batches" / planned.request_id
    try:
        source_result = _read_json(source / "result.json")
        if source_result.get("status") != "completed":
            raise AnalysisInputError(f"父运行论文草稿请求未完成：{planned.request_id}")
        if source_result.get("prompt_sha256") != planned.prompt_sha256:
            raise AnalysisInputError(f"父运行论文草稿提示词哈希不一致：{planned.request_id}")
        validated = validate_paper_analysis_draft(
            _read_json(source / "validated_output.json"),
            evidence_ids,
            evidence_units=evidence_units,
        )
        shutil.copytree(source, target)
        result = _read_json(target / "result.json")
        result["reused_from_run_id"] = parent_dir.name
        _write_json(target / "result.json", result)
        _write_json(target / "validated_output.json", validated)
        return {"result": result, "draft": validated, "failure": None}
    except Exception as exc:
        target.mkdir(parents=True, exist_ok=True)
        failure = _failure("paper_synthesis_reuse", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=_sha256_json(sorted(evidence_ids)),
            started_at=_now(),
            provider_result=None,
            provider="not_called",
            requested_model="not_called",
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(target / "result.json", result)
        return {"result": result, "draft": None, "failure": failure}


def _finalize_paper_analysis_draft(
    *,
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    paper_analysis_draft: dict[str, Any],
    evidence_units: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
    strategy: str,
    cross_section_evidence_ids: tuple[str, ...],
    requests: list[PlannedSynthesisRequest],
    results: list[dict[str, Any]],
    retry_context: Path | None = None,
    retry_batch_id: str | None = None,
    accepted_role_override_ids: set[str] | None = None,
    accepted_role_override_reviews: dict[str, dict[str, Any]] | None = None,
    forbidden_statement_ids: set[str] | None = None,
) -> dict[str, Any]:
    reproduced_forbidden = sorted(
        (forbidden_statement_ids or set())
        & {
            str(row["statement_id"])
            for row in build_statement_records(paper_analysis_draft)
        }
    )
    if reproduced_forbidden:
        failure = _failure(
            "semantic_adjudication_role_revision",
            AnalysisInputError(
                "人工确认错位的原陈述在重新综合后原样出现："
                f"{reproduced_forbidden}"
            ),
            error_code="revision.confirmed_role_defect_reproduced",
        )
        _write_synthesis_plan(
            run_dir,
            strategy=strategy,
            requests=requests,
            cross_section_evidence_ids=cross_section_evidence_ids,
            status="failed",
        )
        return {
            "results": results,
            "analysis": None,
            "coverage": None,
            "failure": failure,
        }
    referenced_ids = paper_analysis_referenced_evidence_ids(paper_analysis_draft)
    non_used_units = [
        unit
        for unit in evidence_units
        if str(unit["evidence_unit_id"]) not in referenced_ids
    ]
    non_used_dispositions: list[dict[str, Any]] = []
    if non_used_units:
        system_prompt, user_prompt = build_non_used_evidence_disposition_prompts(
            paper_id=snapshot.paper_id,
            paper_title=snapshot.paper_title,
            topic=snapshot.topic,
            paper_analysis_draft=paper_analysis_draft,
            non_used_evidence_units=non_used_units,
        )
        non_used_ids = tuple(str(unit["evidence_unit_id"]) for unit in non_used_units)
        try:
            planned = plan_synthesis_request(
                generation_id=snapshot.generation_id,
                stage="evidence_disposition",
                section_id=None,
                evidence_unit_ids=non_used_ids,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=profile.paper_synthesis_max_output_tokens,
                profile=profile,
                token_counter=token_counter,
            )
        except Exception as exc:
            return _synthesis_planning_failure(
                run_dir,
                exc,
                requests,
                results,
                strategy=strategy,
                cross_section_evidence_ids=cross_section_evidence_ids,
            )
        requests.append(planned)
        _write_synthesis_plan(
            run_dir,
            strategy=strategy,
            requests=requests,
            cross_section_evidence_ids=cross_section_evidence_ids,
            status="planned",
        )
        if retry_context is not None and str(retry_batch_id).startswith(
            ("statement_role_", "statement_support_")
        ):
            outcome = _reuse_non_used_evidence_disposition_request(
                retry_context,
                run_dir,
                planned,
            )
        else:
            outcome = _execute_non_used_evidence_disposition_request(
                run_dir,
                client,
                snapshot,
                planned,
                system_prompt,
                user_prompt,
                profile=profile,
            )
        results.append(outcome["result"])
        if outcome["failure"]:
            _write_synthesis_plan(
                run_dir,
                strategy=strategy,
                requests=requests,
                cross_section_evidence_ids=cross_section_evidence_ids,
                status="failed",
            )
            return {
                "results": results,
                "analysis": None,
                "coverage": None,
                "failure": outcome["failure"],
            }
        non_used_dispositions = outcome["dispositions"]

    try:
        analysis = finalize_paper_analysis(
            paper_analysis_draft,
            non_used_dispositions,
            evidence_units,
        )
        coverage = build_final_evidence_coverage(
            analysis,
            {str(unit["evidence_unit_id"]) for unit in evidence_units},
        )
    except Exception as exc:
        failure = _failure("paper_finalization", exc, batch_id="paper_finalization")
        _write_synthesis_plan(
            run_dir,
            strategy=strategy,
            requests=requests,
            cross_section_evidence_ids=cross_section_evidence_ids,
            status="failed",
        )
        return {"results": results, "analysis": None, "coverage": None, "failure": failure}

    candidate_path = run_dir / "output" / "paper_analysis_candidate.json"
    candidate_provenance = {
        "schema_version": "llm.paper_analysis_candidate_provenance.v1",
        "paper_id": snapshot.paper_id,
        "source_generation_id": snapshot.generation_id,
        "input_sha256": snapshot.input_sha256,
        "evidence_units_sha256": _sha256_json(evidence_units),
        "candidate_sha256": _sha256_json(analysis),
        "created_at": _now(),
        "status": "awaiting_semantic_gates",
    }
    _write_json(candidate_path, analysis)
    _write_json(
        run_dir / "output" / "paper_analysis_candidate.provenance.json",
        candidate_provenance,
    )

    statement_records = build_statement_records(analysis)
    role_system, role_user = build_statement_role_prompts(
        paper_id=snapshot.paper_id,
        paper_title=snapshot.paper_title,
        topic=snapshot.topic,
        study_type=str(analysis["study_type"]),
        research_focus=str(analysis["research_focus"]["statement"]),
        statement_records=statement_records,
    )
    statement_ids = tuple(str(row["statement_id"]) for row in statement_records)
    try:
        role_plan = plan_synthesis_request(
            generation_id=snapshot.generation_id,
            stage="statement_role",
            section_id=None,
            evidence_unit_ids=(),
            statement_ids=statement_ids,
            system_prompt=role_system,
            user_prompt=role_user,
            max_output_tokens=profile.statement_role_max_output_tokens,
            profile=profile,
            token_counter=token_counter,
        )
    except Exception as exc:
        return _synthesis_planning_failure(
            run_dir,
            exc,
            requests,
            results,
            strategy=strategy,
            cross_section_evidence_ids=cross_section_evidence_ids,
        )
    requests.append(role_plan)
    _write_json(
        run_dir / "schemas" / "statement_role.schema.json",
        build_statement_role_schema(list(statement_ids)),
    )
    _write_text(
        run_dir / "prompts" / "statement_role_system.md",
        role_system + "\n",
    )
    _write_synthesis_plan(
        run_dir,
        strategy=strategy,
        requests=requests,
        cross_section_evidence_ids=cross_section_evidence_ids,
        status="planned",
    )
    role_outcome = (
        _reuse_statement_role_request(
            retry_context,
            run_dir,
            role_plan,
            statement_records=statement_records,
        )
        if retry_context is not None and str(retry_batch_id).startswith("statement_support_")
        else _execute_statement_role_request(
            run_dir,
            client,
            snapshot,
            role_plan,
            role_system,
            role_user,
            statement_records=statement_records,
            profile=profile,
            accepted_override_ids=accepted_role_override_ids,
            accepted_override_reviews=accepted_role_override_reviews,
        )
    )
    results.append(role_outcome["result"])
    statement_role_reviews = role_outcome["reviews"]
    _write_jsonl(
        run_dir / "output" / "statement_role_reviews.jsonl",
        statement_role_reviews,
    )
    candidate_provenance.update(
        {
            "statement_role_reviews_sha256": _sha256_json(statement_role_reviews),
            "role_gate_request_id": role_plan.request_id,
            "role_gate_status": "blocked" if role_outcome["failure"] else "passed",
        }
    )
    _write_json(
        run_dir / "output" / "paper_analysis_candidate.provenance.json",
        candidate_provenance,
    )
    if role_outcome["failure"]:
        _write_synthesis_plan(
            run_dir,
            strategy=strategy,
            requests=requests,
            cross_section_evidence_ids=cross_section_evidence_ids,
            status="failed",
        )
        return {
            "results": results,
            "analysis": None,
            "coverage": None,
            "failure": role_outcome["failure"],
        }

    system_prompt, user_prompt = build_statement_support_prompts(
        paper_id=snapshot.paper_id,
        paper_title=snapshot.paper_title,
        topic=snapshot.topic,
        statement_records=statement_records,
        evidence_units=evidence_units,
    )
    statement_evidence_ids = tuple(
        sorted(
            {
                str(evidence_id)
                for record in statement_records
                for evidence_id in record["evidence_unit_ids"]
            }
        )
    )
    try:
        planned = plan_synthesis_request(
            generation_id=snapshot.generation_id,
            stage="statement_support",
            section_id=None,
            evidence_unit_ids=statement_evidence_ids,
            statement_ids=statement_ids,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=profile.statement_support_max_output_tokens,
            profile=profile,
            token_counter=token_counter,
        )
    except Exception as exc:
        return _synthesis_planning_failure(
            run_dir,
            exc,
            requests,
            results,
            strategy=strategy,
            cross_section_evidence_ids=cross_section_evidence_ids,
        )
    requests.append(planned)
    _write_synthesis_plan(
        run_dir,
        strategy=strategy,
        requests=requests,
        cross_section_evidence_ids=cross_section_evidence_ids,
        status="planned",
    )
    support_outcome = _execute_statement_support_request(
        run_dir,
        client,
        snapshot,
        planned,
        system_prompt,
        user_prompt,
        statement_records=statement_records,
        profile=profile,
    )
    results.append(support_outcome["result"])
    if support_outcome["failure"]:
        _write_synthesis_plan(
            run_dir,
            strategy=strategy,
            requests=requests,
            cross_section_evidence_ids=cross_section_evidence_ids,
            status="failed",
        )
        return {
            "results": results,
            "analysis": None,
            "coverage": None,
            "failure": support_outcome["failure"],
        }
    statement_support_reviews = support_outcome["reviews"]
    verdict_counts = Counter(str(row["verdict"]) for row in statement_support_reviews)
    role_counts = Counter(str(row["verdict"]) for row in statement_role_reviews)
    coverage.update(
        {
            "statement_count": len(statement_records),
            "field_aligned_statement_count": role_counts["field_aligned"],
            "misclassified_statement_count": role_counts["misclassified"],
            "human_override_statement_role_count": len(
                {
                    str(row["statement_id"])
                    for row in statement_role_reviews
                    if row["verdict"] != "field_aligned"
                    and str(row["statement_id"]) in (accepted_role_override_ids or set())
                    and _statement_role_override_matches(
                        row,
                        (accepted_role_override_reviews or {}).get(str(row["statement_id"])),
                    )
                }
            ),
            "directly_supported_statement_count": verdict_counts["directly_supported"],
            "grounded_inference_statement_count": verdict_counts["grounded_inference"],
        }
    )

    _write_synthesis_plan(
        run_dir,
        strategy=strategy,
        requests=requests,
        cross_section_evidence_ids=cross_section_evidence_ids,
        status="completed",
    )
    return {
        "results": results,
        "analysis": analysis,
        "statement_role_reviews": statement_role_reviews,
        "statement_support_reviews": statement_support_reviews,
        "coverage": coverage,
        "failure": None,
    }


def _execute_statement_support_request(
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    planned: PlannedSynthesisRequest,
    system_prompt: str,
    user_prompt: str,
    *,
    statement_records: list[dict[str, Any]],
    profile: ModelProfile,
) -> dict[str, Any]:
    request_dir = run_dir / "batches" / planned.request_id
    request_dir.mkdir(parents=True)
    _write_json(
        request_dir / "statement_records.json",
        {"statements": statement_records},
    )
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(request_dir / "request.json", request_payload)
    started_at = _now()
    input_sha256 = _sha256_json(statement_records)
    provider_result: ProviderResult | None = None
    try:
        provider_result = client.complete(
            "statement_support",
            request_payload,
            {
                "request_id": planned.request_id,
                "paper_id": snapshot.paper_id,
                "topic": snapshot.topic,
                "statements": statement_records,
                "planned_input_tokens": planned.input_tokens,
                "planned_max_output_tokens": planned.max_output_tokens,
            },
        )
        _write_provider_response(request_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        if provider_result.usage.get("prompt_tokens") != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={provider_result.usage.get('prompt_tokens')}"
            )
        parsed = json.loads(extract_chat_content(provider_result.parsed_response))
        _write_json(request_dir / "parsed_response.json", parsed)
        validated = validate_statement_support_review(parsed, statement_records)
        _write_json(request_dir / "validated_output.json", validated)
        enforce_statement_support_policy(validated, statement_records)
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {
            "result": result,
            "reviews": validated["statement_reviews"],
            "failure": None,
        }
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(request_dir, exc.raw_body)
        failure = _failure("statement_support", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {"result": result, "reviews": [], "failure": failure}


def _execute_statement_role_request(
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    planned: PlannedSynthesisRequest,
    system_prompt: str,
    user_prompt: str,
    *,
    statement_records: list[dict[str, Any]],
    profile: ModelProfile,
    accepted_override_ids: set[str] | None = None,
    accepted_override_reviews: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request_dir = run_dir / "batches" / planned.request_id
    request_dir.mkdir(parents=True)
    _write_json(
        request_dir / "statement_records.json",
        {"statements": statement_records},
    )
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(request_dir / "request.json", request_payload)
    started_at = _now()
    input_sha256 = _sha256_json(statement_records)
    provider_result: ProviderResult | None = None
    validated: dict[str, Any] | None = None
    try:
        provider_result = client.complete(
            "statement_role",
            request_payload,
            {
                "request_id": planned.request_id,
                "paper_id": snapshot.paper_id,
                "topic": snapshot.topic,
                "statements": statement_records,
                "planned_input_tokens": planned.input_tokens,
                "planned_max_output_tokens": planned.max_output_tokens,
            },
        )
        _write_provider_response(request_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        if provider_result.usage.get("prompt_tokens") != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={provider_result.usage.get('prompt_tokens')}"
            )
        parsed = json.loads(extract_chat_content(provider_result.parsed_response))
        _write_json(request_dir / "parsed_response.json", parsed)
        validated = validate_statement_role_review(parsed, statement_records)
        _write_json(request_dir / "validated_output.json", validated)
        _enforce_statement_role_with_overrides(
            validated,
            accepted_override_ids or set(),
            accepted_override_reviews or {},
        )
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {
            "result": result,
            "reviews": validated["statement_role_reviews"],
            "failure": None,
        }
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(request_dir, exc.raw_body)
        failure = _failure("statement_role", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {
            "result": result,
            "reviews": validated["statement_role_reviews"] if validated is not None else [],
            "failure": failure,
        }


def _enforce_statement_role_with_overrides(
    validated_review: dict[str, Any],
    accepted_override_ids: set[str],
    accepted_override_reviews: dict[str, dict[str, Any]],
) -> None:
    blockers = blocking_statement_role_reviews(validated_review)
    unaccepted = [
        row
        for row in blockers
        if str(row["statement_id"]) not in accepted_override_ids
        or not _statement_role_override_matches(
            row,
            accepted_override_reviews.get(str(row["statement_id"])),
        )
    ]
    if not unaccepted:
        return
    first = unaccepted[0]
    index = validated_review["statement_role_reviews"].index(first)
    raise ContractViolation(
        "role.statement_misclassified",
        f"statement_id={first['statement_id']!r}, "
        f"misaligned_fragments={first['misaligned_fragments']}",
        path=f"$.statement_role_reviews[{index}]",
    )


def _statement_role_override_matches(
    current_review: dict[str, Any],
    source_review: dict[str, Any] | None,
) -> bool:
    return bool(
        source_review
        and current_review.get("verdict") == source_review.get("verdict")
        and current_review.get("misaligned_fragments")
        == source_review.get("misaligned_fragments")
    )


def _reuse_statement_role_request(
    parent_dir: Path,
    run_dir: Path,
    planned: PlannedSynthesisRequest,
    *,
    statement_records: list[dict[str, Any]],
) -> dict[str, Any]:
    source = parent_dir / "batches" / planned.request_id
    target = run_dir / "batches" / planned.request_id
    try:
        source_result = _read_json(source / "result.json")
        if source_result.get("status") != "completed":
            raise AnalysisInputError(f"父运行字段职责核验未完成：{planned.request_id}")
        if source_result.get("prompt_sha256") != planned.prompt_sha256:
            raise AnalysisInputError(f"父运行字段职责核验提示词哈希不一致：{planned.request_id}")
        validated = validate_statement_role_review(
            _read_json(source / "validated_output.json"),
            statement_records,
        )
        enforce_statement_role_policy(validated)
        shutil.copytree(source, target)
        result = _read_json(target / "result.json")
        result["reused_from_run_id"] = parent_dir.name
        _write_json(target / "result.json", result)
        _write_json(target / "validated_output.json", validated)
        return {
            "result": result,
            "reviews": validated["statement_role_reviews"],
            "failure": None,
        }
    except Exception as exc:
        target.mkdir(parents=True, exist_ok=True)
        failure = _failure("statement_role_reuse", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=_sha256_json(statement_records),
            started_at=_now(),
            provider_result=None,
            provider="not_called",
            requested_model="not_called",
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(target / "result.json", result)
        return {"result": result, "reviews": [], "failure": failure}


def _reuse_non_used_evidence_disposition_request(
    parent_dir: Path,
    run_dir: Path,
    planned: PlannedSynthesisRequest,
) -> dict[str, Any]:
    source = parent_dir / "batches" / planned.request_id
    target = run_dir / "batches" / planned.request_id
    try:
        source_result = _read_json(source / "result.json")
        if source_result.get("status") != "completed":
            raise AnalysisInputError(f"父运行证据分类请求未完成：{planned.request_id}")
        if source_result.get("prompt_sha256") != planned.prompt_sha256:
            raise AnalysisInputError(f"父运行证据分类提示词哈希不一致：{planned.request_id}")
        validated = validate_non_used_evidence_dispositions(
            _read_json(source / "validated_output.json"),
            set(planned.evidence_unit_ids),
        )
        shutil.copytree(source, target)
        result = _read_json(target / "result.json")
        result["reused_from_run_id"] = parent_dir.name
        _write_json(target / "result.json", result)
        _write_json(target / "validated_output.json", validated)
        return {
            "result": result,
            "dispositions": validated["non_used_evidence_dispositions"],
            "failure": None,
        }
    except Exception as exc:
        target.mkdir(parents=True, exist_ok=True)
        failure = _failure("evidence_disposition_reuse", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=_sha256_json(planned.evidence_unit_ids),
            started_at=_now(),
            provider_result=None,
            provider="not_called",
            requested_model="not_called",
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(target / "result.json", result)
        return {"result": result, "dispositions": [], "failure": failure}


def _execute_non_used_evidence_disposition_request(
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    planned: PlannedSynthesisRequest,
    system_prompt: str,
    user_prompt: str,
    *,
    profile: ModelProfile,
) -> dict[str, Any]:
    request_dir = run_dir / "batches" / planned.request_id
    request_dir.mkdir(parents=True)
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(request_dir / "request.json", request_payload)
    started_at = _now()
    input_sha256 = _sha256_json(planned.evidence_unit_ids)
    provider_result: ProviderResult | None = None
    try:
        provider_result = client.complete(
            "evidence_disposition",
            request_payload,
            {
                "request_id": planned.request_id,
                "paper_id": snapshot.paper_id,
                "topic": snapshot.topic,
                "non_used_evidence_unit_ids": list(planned.evidence_unit_ids),
                "planned_input_tokens": planned.input_tokens,
                "planned_max_output_tokens": planned.max_output_tokens,
            },
        )
        _write_provider_response(request_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        if provider_result.usage.get("prompt_tokens") != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={provider_result.usage.get('prompt_tokens')}"
            )
        parsed = json.loads(extract_chat_content(provider_result.parsed_response))
        _write_json(request_dir / "parsed_response.json", parsed)
        validated = validate_non_used_evidence_dispositions(
            parsed,
            set(planned.evidence_unit_ids),
        )
        _write_json(request_dir / "validated_output.json", validated)
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {
            "result": result,
            "dispositions": validated["non_used_evidence_dispositions"],
            "failure": None,
        }
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(request_dir, exc.raw_body)
        failure = _failure("evidence_disposition", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {"result": result, "dispositions": [], "failure": failure}


def _execute_paper_synthesis_request(
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    planned: PlannedSynthesisRequest,
    system_prompt: str,
    user_prompt: str,
    *,
    evidence_ids: set[str],
    evidence_units: list[dict[str, Any]],
    profile: ModelProfile,
) -> dict[str, Any]:
    synthesis_dir = run_dir / "batches" / planned.request_id
    synthesis_dir.mkdir(parents=True)
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(synthesis_dir / "request.json", request_payload)
    started_at = _now()
    input_sha256 = _sha256_json(sorted(evidence_ids))
    provider_result: ProviderResult | None = None
    try:
        provider_result = client.complete(
            "synthesis",
            request_payload,
            {
                "request_id": planned.request_id,
                "paper_id": snapshot.paper_id,
                "topic": snapshot.topic,
                "evidence_unit_ids": sorted(evidence_ids),
                "planned_input_tokens": planned.input_tokens,
                "planned_max_output_tokens": planned.max_output_tokens,
            },
        )
        _write_provider_response(synthesis_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        if provider_result.usage.get("prompt_tokens") != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={provider_result.usage.get('prompt_tokens')}"
            )
        content = extract_chat_content(provider_result.parsed_response)
        parsed = json.loads(content)
        _write_json(synthesis_dir / "parsed_response.json", parsed)
        validated = validate_paper_analysis_draft(
            parsed,
            evidence_ids,
            evidence_units=evidence_units,
        )
        _write_json(synthesis_dir / "validated_output.json", validated)
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(synthesis_dir / "result.json", result)
        return {"result": result, "draft": validated, "failure": None}
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(synthesis_dir, exc.raw_body)
        failure = _failure("paper_synthesis", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=input_sha256,
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(synthesis_dir / "result.json", result)
        return {"result": result, "draft": None, "failure": failure}


def _execute_section_summary_request(
    run_dir: Path,
    client: Any,
    snapshot: AnalysisInputSnapshot,
    planned: PlannedSynthesisRequest,
    system_prompt: str,
    user_prompt: str,
    *,
    profile: ModelProfile,
) -> dict[str, Any]:
    request_dir = run_dir / "batches" / planned.request_id
    request_dir.mkdir(parents=True)
    request_payload = build_chat_request(
        client.model,
        system_prompt,
        user_prompt,
        max_tokens=planned.max_output_tokens,
        thinking=profile.thinking,
    )
    _write_json(request_dir / "request.json", request_payload)
    started_at = _now()
    provider_result: ProviderResult | None = None
    try:
        provider_result = client.complete(
            "section_summary",
            request_payload,
            {
                "request_id": planned.request_id,
                "batch_id": planned.request_id,
                "section_id": planned.section_id,
                "evidence_unit_ids": list(planned.evidence_unit_ids),
                "paper_id": snapshot.paper_id,
                "topic": snapshot.topic,
                "planned_input_tokens": planned.input_tokens,
                "planned_max_output_tokens": planned.max_output_tokens,
            },
        )
        _write_provider_response(request_dir, provider_result)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        if provider_result.usage.get("prompt_tokens") != planned.input_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={planned.input_tokens}, actual={provider_result.usage.get('prompt_tokens')}"
            )
        parsed = json.loads(extract_chat_content(provider_result.parsed_response))
        _write_json(request_dir / "parsed_response.json", parsed)
        validated = validate_section_summary(
            parsed,
            section_id=str(planned.section_id),
            evidence_ids=set(planned.evidence_unit_ids),
        )
        _write_json(request_dir / "validated_output.json", validated)
        result = _request_result(
            request_id=planned.request_id,
            status="completed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=_sha256_json(planned.evidence_unit_ids),
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {"result": result, "summary": validated, "failure": None}
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            _write_raw_body(request_dir, exc.raw_body)
        failure = _failure("section_summary", exc, batch_id=planned.request_id)
        result = _request_result(
            request_id=planned.request_id,
            status="failed",
            material_ids=[],
            prompt_sha256=planned.prompt_sha256,
            input_sha256=_sha256_json(planned.evidence_unit_ids),
            started_at=started_at,
            provider_result=provider_result,
            provider=str(client.provider),
            requested_model=str(client.model),
            failure=failure,
            planned_input_tokens=planned.input_tokens,
            planned_max_output_tokens=planned.max_output_tokens,
        )
        _write_json(request_dir / "result.json", result)
        return {"result": result, "summary": None, "failure": failure}


def _synthesis_planning_failure(
    run_dir: Path,
    exc: Exception,
    requests: list[PlannedSynthesisRequest],
    results: list[dict[str, Any]] | None = None,
    *,
    strategy: str = "section_then_paper_synthesis",
    cross_section_evidence_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    failure = _failure("synthesis_planning", exc, batch_id="synthesis_planning")
    result = _request_result(
        request_id="synthesis_planning",
        status="failed",
        material_ids=[],
        prompt_sha256="",
        input_sha256="",
        started_at=_now(),
        provider_result=None,
        provider="not_called",
        requested_model="not_called",
        failure=failure,
    )
    _write_synthesis_plan(
        run_dir,
        strategy=strategy,
        requests=requests,
        cross_section_evidence_ids=cross_section_evidence_ids,
        status="failed",
    )
    return {"results": [*(results or []), result], "analysis": None, "coverage": None, "failure": failure}


def _write_synthesis_plan(
    run_dir: Path,
    *,
    strategy: str,
    requests: list[PlannedSynthesisRequest],
    cross_section_evidence_ids: tuple[str, ...],
    status: str,
) -> None:
    _write_json(
        run_dir / "plan" / "synthesis_plan.json",
        {
            "schema_version": "llm.synthesis_plan.v1",
            "status": status,
            "strategy": strategy,
            "requests": [dataclasses.asdict(request) for request in requests],
            "cross_section_evidence_ids": list(cross_section_evidence_ids),
        },
    )


def _reuse_batch(parent_dir: Path, run_dir: Path, planned: PlannedRequest) -> dict[str, Any]:
    source = parent_dir / "batches" / planned.request_id
    target = run_dir / "batches" / planned.request_id
    shutil.copytree(source, target)
    result = _read_json(target / "result.json")
    result["reused_from_run_id"] = parent_dir.name
    resolution_path = target / "resolution.json"
    if resolution_path.exists():
        resolution = _read_json(resolution_path)
        if resolution.get("status") == "corrected":
            correction_request_id = str(resolution["correction_request_id"])
            correction_source = parent_dir / "batches" / correction_request_id
            correction_dir = run_dir / "batches" / correction_request_id
            shutil.copytree(correction_source, correction_dir)
            corrected = _read_json(correction_dir / "validated_output.json")
            correction_result = _read_json(correction_dir / "result.json")
            correction_result["reused_from_run_id"] = parent_dir.name
            result["resolution_status"] = "corrected"
            result["resolved_by_request_id"] = correction_request_id
            result["correction_request_id"] = correction_request_id
            _write_json(target / "result.json", result)
            _write_json(correction_dir / "result.json", correction_result)
            return {
                "result": correction_result,
                "results": [result, correction_result],
                "assessments": corrected["material_assessments"],
                "evidence_units": corrected["evidence_units"],
            }
    validated = _read_json(target / "validated_output.json")
    _write_json(target / "result.json", result)
    return {
        "result": result,
        "results": [result],
        "assessments": validated["material_assessments"],
        "evidence_units": validated["evidence_units"],
    }


def _build_coverage(
    snapshot: AnalysisInputSnapshot,
    assessments: list[dict[str, Any]],
    evidence_units: list[dict[str, Any]],
    batch_results: list[dict[str, Any]],
) -> dict[str, Any]:
    material_ids = [str(row["material_id"]) for row in snapshot.materials]
    coverage = verify_material_coverage(material_ids, assessments)
    assessed_count = len({str(row["material_id"]) for row in assessments if row.get("material_id") in material_ids})
    return {
        "scope": snapshot.scope,
        "planned_material_count": len(material_ids),
        "assessed_material_count": assessed_count,
        "material_coverage_ratio": assessed_count / len(material_ids) if material_ids else 0.0,
        **coverage,
        "evidence_unit_count": len(evidence_units),
        "failed_batch_ids": [
            row["request_id"]
            for row in batch_results
            if row["status"] == "failed" and not row.get("resolved_by_request_id")
        ],
        "completed_batch_ids": [row["request_id"] for row in batch_results if row["status"] == "completed"],
        "corrected_batch_ids": [
            row["request_id"]
            for row in batch_results
            if row.get("resolution_status") == "corrected"
        ],
        "evidence_correction_request_ids": [
            row["request_id"]
            for row in batch_results
            if row.get("stage") == "evidence_batch_correction"
        ],
        "invalid_citations": [],
        "invalid_quotes": [],
        "unresolved_evidence_unit_ids": [],
    }


def _build_evidence_correction_audit(
    batch_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result_by_id = {
        str(row["request_id"]): row for row in batch_results
    }
    records: list[dict[str, Any]] = []
    for source in batch_results:
        correction_request_id = str(source.get("correction_request_id") or "")
        if not correction_request_id:
            continue
        correction = result_by_id.get(correction_request_id, {})
        records.append(
            {
                "source_request_id": str(source["request_id"]),
                "source_status": str(source["status"]),
                "source_error_code": source.get("error_code"),
                "source_error_path": source.get("error_path"),
                "resolution_status": source.get("resolution_status"),
                "correction_request_id": correction_request_id,
                "correction_status": correction.get("status"),
                "corrected_material_ids": list(
                    correction.get("corrected_material_ids", [])
                ),
                "correction_error_code": correction.get("error_code"),
                "correction_error_path": correction.get("error_path"),
                "correction_usage": copy.deepcopy(
                    correction.get("usage", _empty_usage())
                ),
            }
        )
    return records


def _write_input_artifacts_v3(
    run_dir: Path,
    snapshot: AnalysisInputSnapshot,
    projected: list[dict[str, Any]],
    quote_candidates: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
    plan: PaperAnalysisPlan,
    section_map: SectionMap | None,
) -> None:
    _write_json(
        run_dir / "input" / "paper.json",
        {
            "paper_id": snapshot.paper_id,
            "paper_title": snapshot.paper_title,
            "topic": snapshot.topic,
            "generation_id": snapshot.generation_id,
            "scope": snapshot.scope,
            "material_count": len(snapshot.materials),
            "source_material_count": snapshot.source_material_count,
            "input_sha256": snapshot.input_sha256,
        },
    )
    _write_jsonl(run_dir / "input" / "materials.jsonl", list(snapshot.materials))
    _write_jsonl(run_dir / "input" / "projected_cards.jsonl", projected)
    _write_jsonl(run_dir / "input" / "quote_candidates.jsonl", quote_candidates)
    if section_map is not None:
        _write_json(run_dir / "input" / "section_map.json", _section_map_to_dict(section_map))
    _write_json(run_dir / "plan" / "model_profile.json", profile.to_dict())
    _write_json(run_dir / "plan" / "analysis_plan.json", plan.to_dict())
    manifest_path = Path(token_counter.asset_dir) / "manifest.json"
    _write_json(run_dir / "plan" / "tokenizer_manifest.json", _read_json(manifest_path))
    _write_json(run_dir / "schemas" / "evidence_batch.schema.json", EVIDENCE_BATCH_SCHEMA)
    _write_json(run_dir / "schemas" / "section_summary.schema.json", SECTION_SUMMARY_SCHEMA)
    _write_json(run_dir / "schemas" / "paper_analysis_draft.schema.json", PAPER_ANALYSIS_DRAFT_SCHEMA)
    _write_json(
        run_dir / "schemas" / "non_used_evidence_disposition.schema.json",
        NON_USED_EVIDENCE_DISPOSITION_SCHEMA,
    )
    _write_json(run_dir / "schemas" / "paper_analysis.schema.json", PAPER_ANALYSIS_SCHEMA)
    _write_json(
        run_dir / "schemas" / "evidence_claim_support.schema.json",
        EVIDENCE_CLAIM_SUPPORT_SCHEMA,
    )
    _write_json(run_dir / "schemas" / "statement_role.schema.json", STATEMENT_ROLE_SCHEMA)
    _write_json(run_dir / "schemas" / "statement_support.schema.json", STATEMENT_SUPPORT_SCHEMA)
    _write_text(run_dir / "prompts" / "evidence_system.md", EVIDENCE_SYSTEM_PROMPT + "\n")
    _write_text(
        run_dir / "prompts" / "evidence_batch_correction_system.md",
        EVIDENCE_CORRECTION_SYSTEM_PROMPT + "\n",
    )
    _write_text(run_dir / "prompts" / "section_summary_system.md", SECTION_SUMMARY_SYSTEM_PROMPT + "\n")
    _write_text(run_dir / "prompts" / "synthesis_system.md", SYNTHESIS_SYSTEM_PROMPT + "\n")
    _write_text(
        run_dir / "prompts" / "non_used_evidence_disposition_system.md",
        NON_USED_EVIDENCE_DISPOSITION_SYSTEM_PROMPT + "\n",
    )
    _write_text(
        run_dir / "prompts" / "evidence_claim_support_system.md",
        EVIDENCE_CLAIM_SUPPORT_SYSTEM_PROMPT + "\n",
    )
    _write_text(
        run_dir / "prompts" / "statement_role_system.md",
        STATEMENT_ROLE_SYSTEM_PROMPT + "\n",
    )
    _write_text(
        run_dir / "prompts" / "statement_support_system.md",
        STATEMENT_SUPPORT_SYSTEM_PROMPT + "\n",
    )


def _write_planning_failure_inputs(
    run_dir: Path,
    snapshot: AnalysisInputSnapshot | None,
    projected: list[dict[str, Any]],
    quote_candidates: list[dict[str, Any]],
    profile: ModelProfile | None,
) -> None:
    if snapshot is not None:
        _write_json(
            run_dir / "input" / "paper.json",
            {
                "paper_id": snapshot.paper_id,
                "paper_title": snapshot.paper_title,
                "topic": snapshot.topic,
                "generation_id": snapshot.generation_id,
                "scope": snapshot.scope,
                "material_count": len(snapshot.materials),
                "source_material_count": snapshot.source_material_count,
                "input_sha256": snapshot.input_sha256,
            },
        )
        _write_jsonl(run_dir / "input" / "materials.jsonl", list(snapshot.materials))
    if projected:
        _write_jsonl(run_dir / "input" / "projected_cards.jsonl", projected)
    if quote_candidates:
        _write_jsonl(run_dir / "input" / "quote_candidates.jsonl", quote_candidates)
    if profile is not None:
        _write_json(run_dir / "plan" / "model_profile.json", profile.to_dict())


def _section_map_to_dict(section_map: SectionMap) -> dict[str, Any]:
    return {
        "paper_id": section_map.paper_id,
        "generation_id": section_map.generation_id,
        "source_sha256": section_map.source_sha256,
        "sections": [dataclasses.asdict(section) for section in section_map.sections],
        "material_to_section": section_map.material_to_section,
    }


def _request_result(
    *,
    request_id: str,
    status: str,
    material_ids: list[str],
    prompt_sha256: str,
    input_sha256: str,
    started_at: str,
    provider_result: ProviderResult | None,
    provider: str,
    requested_model: str,
    failure: dict[str, Any] | None = None,
    planned_input_tokens: int | None = None,
    planned_max_output_tokens: int | None = None,
) -> dict[str, Any]:
    actual_prompt_tokens = provider_result.usage.get("prompt_tokens") if provider_result else None
    result = {
        "request_id": request_id,
        "batch_id": request_id,
        "status": status,
        "material_ids": material_ids,
        "provider": provider,
        "requested_model": requested_model,
        "response_model": provider_result.response_model if provider_result else None,
        "response_id": provider_result.response_id if provider_result else None,
        "system_fingerprint": provider_result.system_fingerprint if provider_result else None,
        "finish_reason": provider_result.finish_reason if provider_result else None,
        "reasoning_content_chars": provider_result.reasoning_content_chars if provider_result else None,
        "planned_input_tokens": planned_input_tokens,
        "actual_prompt_tokens": actual_prompt_tokens,
        "prompt_token_delta": (
            actual_prompt_tokens - planned_input_tokens
            if actual_prompt_tokens is not None and planned_input_tokens is not None
            else None
        ),
        "planned_max_output_tokens": planned_max_output_tokens,
        "actual_completion_tokens": (
            provider_result.usage.get("completion_tokens") if provider_result else None
        ),
        "prompt_sha256": prompt_sha256,
        "input_sha256": input_sha256,
        "started_at": started_at,
        "finished_at": _now(),
        "usage": provider_result.usage if provider_result else _empty_usage(),
    }
    if failure:
        result.update(
            {
                "error_code": failure["error_code"],
                "error_message": failure["error_message"],
                "error_path": failure.get("error_path"),
            }
        )
    return result


def _finish_manifest(
    base: dict[str, Any],
    *,
    status: str,
    batch_results: list[dict[str, Any]],
    usage_rows: list[dict[str, int | None]],
    failures: list[dict[str, Any]],
    reused_batch_ids: list[str],
    executed_batch_ids: list[str],
) -> dict[str, Any]:
    return {
        **base,
        "status": status,
        "finished_at": _now(),
        "batches": batch_results,
        "failure_count": len(failures),
        "failure_codes": [str(row["error_code"]) for row in failures],
        "response_usage": _sum_usage(usage_rows),
        "reused_batch_ids": reused_batch_ids,
        "executed_batch_ids": executed_batch_ids,
        "cache_hit_batch_ids": [
            str(row["request_id"])
            for row in batch_results
            if row.get("cache_hit")
        ],
        "provider_executed_batch_ids": [
            str(row["request_id"])
            for row in batch_results
            if not row.get("cache_hit") and not row.get("reused_from_run_id")
        ],
        "paths": {
            "input": "input/materials.jsonl",
            "projected_cards": "input/projected_cards.jsonl",
            "analysis_plan": "plan/analysis_plan.json",
            "evidence_claim_support_plan": "plan/evidence_claim_support_plan.json",
            "synthesis_plan": "plan/synthesis_plan.json",
            "coverage": "coverage.json",
            "failures": "audit/failures.jsonl",
            "material_assessments": "output/material_assessments.jsonl",
            "evidence_units": "output/evidence_units.jsonl",
            "evidence_claim_support_reviews": "output/evidence_claim_support_reviews.jsonl",
            "evidence_claim_gate_decisions": "output/evidence_claim_gate_decisions.jsonl",
            "paper_analysis_candidate": "output/paper_analysis_candidate.json",
            "paper_analysis_candidate_provenance": "output/paper_analysis_candidate.provenance.json",
            "paper_analysis": "output/paper_analysis.json",
            "statement_role_reviews": "output/statement_role_reviews.jsonl",
            "statement_support_reviews": "output/statement_support_reviews.jsonl",
            "review": "review/review.md",
            "decisions": "review/decisions.csv",
        },
    }


def _failure(
    stage: str,
    exc: Exception,
    *,
    batch_id: str | None = None,
    material_ids: list[str] | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    if isinstance(exc, ContractViolation):
        resolved_code = exc.code
        error_path = exc.path
    elif isinstance(exc, json.JSONDecodeError):
        resolved_code = "response.invalid_json"
        error_path = f"line={exc.lineno},column={exc.colno}"
    elif isinstance(exc, ProviderCallError):
        provider_code = str(exc).split(":", 1)[0]
        resolved_code = provider_code if provider_code.startswith("provider.") else "provider.call_failed"
        error_path = None
    elif isinstance(exc, PlanningError):
        resolved_code = exc.code
        error_path = None
    elif isinstance(exc, AnalysisInputError):
        resolved_code = "input.invalid"
        error_path = None
    else:
        resolved_code = "analysis.unexpected_error"
        error_path = None
    return {
        "recorded_at": _now(),
        "stage": stage,
        "batch_id": batch_id,
        "material_ids": material_ids or [],
        "error_code": error_code or resolved_code,
        "error_message": str(exc),
        "error_path": error_path,
        "exception_type": type(exc).__name__,
    }


def _write_provider_response(directory: Path, result: ProviderResult) -> None:
    _write_raw_body(directory, result.raw_body)


def _write_raw_body(directory: Path, raw_body: bytes) -> None:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        path = directory / "raw_response.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw_body)
        return
    _write_json(directory / "raw_response.json", payload)


def _generation_is_current(workspace: Path, paper_id: str, generation_id: str) -> bool:
    rows = _read_jsonl(workspace / "_corpus" / "papers.jsonl")
    matching = [row for row in rows if str(row.get("paper_id", "")) == paper_id]
    return len(matching) == 1 and matching[0].get("status") == "completed" and matching[0].get("generation_id") == generation_id


def _write_failed_run_review(
    run_dir: Path,
    manifest: dict[str, Any],
    snapshot: AnalysisInputSnapshot | None,
    evidence_units: list[dict[str, Any]],
    coverage: dict[str, Any],
    failures: list[dict[str, Any]],
) -> None:
    write_failed_analysis_review(
        run_dir / "review",
        run_id=str(manifest["run_id"]),
        status=str(manifest["status"]),
        paper_id=snapshot.paper_id if snapshot else str(manifest.get("paper_id") or "未记录"),
        paper_title=snapshot.paper_title if snapshot else str(manifest.get("paper_title") or "未记录"),
        topic=snapshot.topic if snapshot else str(manifest.get("topic") or ""),
        evidence_units=evidence_units,
        coverage=coverage,
        failures=failures,
    )


def _sum_usage(rows: list[dict[str, int | None]]) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [row.get(key) for row in rows if isinstance(row.get(key), int)]
        result[key] = sum(values) if values else None
    return result


def _empty_usage() -> dict[str, int | None]:
    return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}


def _result(run_dir: Path, manifest: dict[str, Any], batch_count: int, material_count: int) -> dict[str, Any]:
    failure_codes = list(manifest.get("failure_codes", []))
    if not failure_codes and manifest.get("error_code"):
        failure_codes = [str(manifest["error_code"])]
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "provider": manifest.get("provider"),
        "model": manifest.get("model"),
        "paper_id": manifest.get("paper_id"),
        "paper_disposition": manifest.get("paper_disposition"),
        "batch_count": batch_count,
        "material_count": material_count,
        "failure_count": manifest.get("failure_count", 0),
        "failure_codes": failure_codes,
        "scope": manifest.get("scope"),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "review_path": str(run_dir / "review" / "review.md"),
    }


def _sha256_json(payload: Any) -> str:
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _write_text(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
