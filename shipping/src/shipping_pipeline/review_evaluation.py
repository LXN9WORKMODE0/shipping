from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .chapter_knowledge_package import (
    load_chapter_knowledge_package_run,
)
from .llm_analysis import DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_claim_audit import (
    CLAIM_AUDIT_SYSTEM_PROMPT,
    build_claim_audit_prompt,
    summarize_claim_audits,
)
from .review_claim_audit_contracts import (
    build_claim_catalogue,
    build_chapter_claim_audit_schema,
    load_review_claim_audit_config,
    validate_chapter_claim_audit,
)
from .review_framework import load_review_framework_run


REVIEW_EVALUATION_CONFIG_SCHEMA_VERSION = "llm.review_evaluation_config.v1"
REVIEW_EVALUATION_RUN_SCHEMA_VERSION = "llm.review_evaluation_run.v1"
STRUCTURAL_EVALUATION_SCHEMA_VERSION = (
    "llm.review_structural_evaluation.v1"
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW_EVALUATION_CONFIG = (
    PROJECT_ROOT
    / "config"
    / "experiments"
    / "review-writing-abc-14papers-20260730.json"
)
DEFAULT_REVIEW_CLAIM_AUDIT_CONFIG = (
    PROJECT_ROOT / "config" / "review-claim-audit-default.json"
)

STRUCTURAL_EVALUATION_SYSTEM_PROMPT = """你是文献综述结构评价员。
输入包含同一批论文形成的A、B、C三份草稿、统一综述目标和核心维度。
本阶段只评价结构与综合方式，不判断事实真伪；事实支持由独立Claim审计完成。
逐份判断核心维度覆盖、真正比较两篇及以上论文的段落、语料缺口越界、
整体结构和跨论文比较质量。
跨论文比较必须在同一段落中比较、联系、区分或综合至少两篇论文，
仅并列列出多篇引用不算比较。
语料缺口越界指把“本次语料未覆盖”写成“整个领域不存在研究”或学界共识。
只输出符合JSON Schema的对象，不输出解释性Markdown。"""


class ReviewEvaluationError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ReviewEvaluationConfig:
    schema_version: str
    experiment_id: str
    topic: str
    review_goal: str
    expected_paper_count: int
    source_package_run_id: str
    a_direct_run_id: str
    b_writing_run_id: str
    b_audit_run_id: str
    c_draft_run_id: str
    c_synthesis_run_id: str
    structural_max_output_tokens: int
    human_evaluation: dict[str, Any]


def load_review_evaluation_config(
    path: str | Path,
) -> ReviewEvaluationConfig:
    payload = _read_json(Path(path))
    expected = set(ReviewEvaluationConfig.__dataclass_fields__)
    if set(payload) != expected:
        raise ReviewEvaluationError(
            "evaluation.config_invalid",
            "A/B/C实验配置字段不匹配。",
        )
    config = ReviewEvaluationConfig(**payload)
    if config.schema_version != REVIEW_EVALUATION_CONFIG_SCHEMA_VERSION:
        raise ReviewEvaluationError(
            "evaluation.config_schema_invalid",
            "A/B/C实验配置版本不受支持。",
        )
    for field in (
        "experiment_id",
        "topic",
        "review_goal",
        "source_package_run_id",
        "a_direct_run_id",
        "b_writing_run_id",
        "b_audit_run_id",
        "c_draft_run_id",
        "c_synthesis_run_id",
    ):
        value = getattr(config, field)
        if not isinstance(value, str) or not value.strip():
            raise ReviewEvaluationError(
                "evaluation.config_value_invalid",
                f"{field}必须是非空字符串。",
            )
    for field in ("expected_paper_count", "structural_max_output_tokens"):
        value = getattr(config, field)
        if type(value) is not int or value < 1:
            raise ReviewEvaluationError(
                "evaluation.config_value_invalid",
                f"{field}必须是正整数。",
            )
    _validate_human_evaluation(config.human_evaluation)
    return config


def canonicalize_direct_review(review: dict[str, Any]) -> dict[str, Any]:
    sections = [
        {
            "heading": section["heading"],
            "paragraphs": [
                {
                    "text": paragraph["text"],
                    "citation_keys": list(paragraph["citation_keys"]),
                }
                for paragraph in section["paragraphs"]
            ],
        }
        for section in review["sections"]
    ]
    sections.append(
        {
            "heading": "结论",
            "paragraphs": [
                {
                    "text": review["conclusion"]["text"],
                    "citation_keys": list(
                        review["conclusion"]["citation_keys"]
                    ),
                }
            ],
        }
    )
    return _canonical_candidate("A", review["title"], sections)


def canonicalize_rich_review(review: dict[str, Any]) -> dict[str, Any]:
    sections = [
        {
            "heading": chapter["title"],
            "paragraphs": [
                {
                    "text": paragraph["text"],
                    "citation_keys": list(paragraph["citation_keys"]),
                }
                for paragraph in chapter["paragraphs"]
            ],
        }
        for chapter in review["chapters"]
    ]
    return _canonical_candidate(
        "B",
        review["working_title"],
        sections,
    )


def canonicalize_evidence_review(
    review: dict[str, Any],
    *,
    citation_by_evidence: dict[str, str],
) -> dict[str, Any]:
    sections = []
    for section in review["sections"]:
        paragraphs = []
        for paragraph in section["paragraphs"]:
            citations = _evidence_citations(
                paragraph["evidence_unit_ids"],
                citation_by_evidence,
            )
            paragraphs.append(
                {
                    "text": paragraph["text"],
                    "citation_keys": citations,
                }
            )
        sections.append(
            {"heading": section["heading"], "paragraphs": paragraphs}
        )
    sections.append(
        {
            "heading": "结论",
            "paragraphs": [
                {
                    "text": review["conclusion"]["text"],
                    "citation_keys": _evidence_citations(
                        review["conclusion"]["evidence_unit_ids"],
                        citation_by_evidence,
                    ),
                }
            ],
        }
    )
    return _canonical_candidate("C", review["title"], sections)


def build_structural_evaluation_schema(
    *,
    candidates: list[dict[str, Any]],
    dimension_indexes: list[int],
) -> dict[str, Any]:
    candidate_schemas = []
    for candidate in candidates:
        paragraph_indexes = [
            row["paragraph_index"] for row in candidate["paragraphs"]
        ]
        candidate_schemas.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "candidate",
                    "covered_dimension_indexes",
                    "cross_paper_comparison_paragraph_indexes",
                    "corpus_gap_overreachs",
                    "structure_score",
                    "comparison_score",
                    "assessment",
                ],
                "properties": {
                    "candidate": {"const": candidate["candidate"]},
                    "covered_dimension_indexes": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {
                            "type": "integer",
                            "enum": dimension_indexes,
                        },
                    },
                    "cross_paper_comparison_paragraph_indexes": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {
                            "type": "integer",
                            "enum": paragraph_indexes,
                        },
                    },
                    "corpus_gap_overreachs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["paragraph_index", "reason"],
                            "properties": {
                                "paragraph_index": {
                                    "type": "integer",
                                    "enum": paragraph_indexes,
                                },
                                "reason": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 1000,
                                },
                            },
                        },
                    },
                    "structure_score": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "comparison_score": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "assessment": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
                    },
                },
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "candidates"],
        "properties": {
            "schema_version": {
                "const": STRUCTURAL_EVALUATION_SCHEMA_VERSION
            },
            "candidates": {
                "type": "array",
                "prefixItems": candidate_schemas,
                "items": False,
                "minItems": len(candidate_schemas),
                "maxItems": len(candidate_schemas),
            },
        },
    }


def validate_structural_evaluation(
    payload: object,
    *,
    schema: dict[str, Any],
) -> dict[str, Any]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda row: list(row.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        raise ReviewEvaluationError(
            "evaluation.structural_schema_invalid",
            error.message,
            path=path,
        )
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    for candidate in result["candidates"]:
        retained = []
        removed = 0
        for row in candidate["corpus_gap_overreachs"]:
            reason = row["reason"]
            if (
                "未越界" in reason
                or "并未越界" in reason
                or "没有越界" in reason
            ):
                removed += 1
            else:
                retained.append(row)
        candidate["corpus_gap_overreachs"] = retained
        candidate["normalization_flags"] = (
            [f"non_overreach_entry_removed:{removed}"]
            if removed
            else []
        )
    return result


def compute_candidate_metrics(
    candidate: dict[str, Any],
    audit: dict[str, Any],
    structural: dict[str, Any],
    *,
    paper_count: int,
    dimension_count: int,
) -> dict[str, Any]:
    factual = [
        row
        for row in audit["claim_audits"]
        if row["claim_type"] == "factual"
    ]
    core = [row for row in factual if row["importance"] == "core"]
    citation_assessments = [
        assessment
        for row in factual
        for assessment in row["source_assessments"]
    ]
    cited_papers = {
        citation
        for row in candidate["paragraphs"]
        for citation in row["citation_keys"]
    }
    paragraph_count = len(candidate["paragraphs"])
    supported = sum(row["status"] == "supported" for row in factual)
    core_supported = sum(row["status"] == "supported" for row in core)
    matching = sum(
        row["source_tier"] != "none" for row in citation_assessments
    )
    return {
        "body_char_count": sum(
            len(row["text"]) for row in candidate["paragraphs"]
        ),
        "paragraph_count": paragraph_count,
        "cited_paper_count": len(cited_papers),
        "paper_coverage_rate": _ratio(len(cited_papers), paper_count),
        "core_dimension_coverage_rate": _ratio(
            len(structural["covered_dimension_indexes"]),
            dimension_count,
        ),
        "cross_paper_comparison_paragraph_count": len(
            structural["cross_paper_comparison_paragraph_indexes"]
        ),
        "cross_paper_comparison_paragraph_rate": _ratio(
            len(structural["cross_paper_comparison_paragraph_indexes"]),
            paragraph_count,
        ),
        "factual_claim_count": len(factual),
        "supported_factual_claim_count": supported,
        "factual_claim_support_rate": _ratio(supported, len(factual)),
        "core_factual_claim_count": len(core),
        "core_supported_factual_claim_count": core_supported,
        "core_factual_claim_support_rate": _ratio(
            core_supported,
            len(core),
        ),
        "qualified_claim_count": sum(
            row["status"] == "qualified" for row in factual
        ),
        "unsupported_claim_count": sum(
            row["status"] == "unsupported" for row in factual
        ),
        "core_unsupported_claim_count": sum(
            row["status"] == "unsupported" for row in core
        ),
        "result_type_overstatement_count": sum(
            row["result_type_check"] == "overstated" for row in factual
        ),
        "validation_level_overstatement_count": sum(
            row["validation_level_check"] == "overstated"
            for row in factual
        ),
        "citation_paper_matching_rate": _ratio(
            matching,
            len(citation_assessments),
        ),
        "corpus_gap_overreach_count": len(
            structural["corpus_gap_overreachs"]
        ),
        "structure_score": structural["structure_score"],
        "comparison_score": structural["comparison_score"],
        "publishable": (
            sum(row["status"] == "unsupported" for row in core) == 0
        ),
    }


class ReviewEvaluationRunner:
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
        config_path: str | Path = DEFAULT_REVIEW_EVALUATION_CONFIG,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        claim_audit_config_path: str | Path = (
            DEFAULT_REVIEW_CLAIM_AUDIT_CONFIG
        ),
        resume_from_run_id: str | None = None,
        timeout: int = 1800,
    ) -> dict[str, Any]:
        config = load_review_evaluation_config(config_path)
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / "_review_evaluations" / "runs" / resolved
        if run_dir.exists():
            raise ReviewEvaluationError(
                "evaluation.run_exists",
                f"评价run_id已存在：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        try:
            source = load_chapter_knowledge_package_run(
                self.workspace,
                config.source_package_run_id,
            )
            if provider != source.profile.provider:
                raise ReviewEvaluationError(
                    "evaluation.provider_mismatch",
                    "provider与冻结知识包模型配置不一致。",
                )
            if len(source.package["papers"]) != config.expected_paper_count:
                raise ReviewEvaluationError(
                    "evaluation.paper_count_mismatch",
                    "冻结论文数与实验配置不一致。",
                )
            candidates = self._load_candidates(config, source.package)
            resume_dir = None
            if resume_from_run_id is not None:
                _safe_segment(resume_from_run_id)
                resume_dir = (
                    self.workspace
                    / "_review_evaluations"
                    / "runs"
                    / resume_from_run_id
                )
                resume_manifest = _read_json(
                    resume_dir / "manifest.json"
                )
                if (
                    resume_manifest.get("experiment_id")
                    != config.experiment_id
                ):
                    raise ReviewEvaluationError(
                        "evaluation.resume_experiment_mismatch",
                        "续跑来源实验身份不一致。",
                    )
            allowed_citations = {
                paper["citation_key"] for paper in source.package["papers"]
            }
            for candidate in candidates:
                unknown = {
                    key
                    for row in candidate["paragraphs"]
                    for key in row["citation_keys"]
                    if key not in allowed_citations
                }
                if unknown:
                    raise ReviewEvaluationError(
                        "evaluation.unknown_citation",
                        f"{candidate['candidate']}包含未知引用：{sorted(unknown)}",
                    )
            _write_json(
                run_dir / "input" / "config.json",
                {
                    **config.__dict__,
                    "resume_from_run_id": resume_from_run_id,
                },
            )
            _write_json(run_dir / "input" / "candidates.json", candidates)
            _write_json(
                run_dir / "input" / "corpus_package.json",
                source.package,
            )
            profile = source.profile
            client = (
                self.analysis_client
                or OpenAICompatibleAnalysisClient.from_env(
                    api_url=api_url,
                    api_key_env=api_key_env,
                    model=profile.request_model,
                    timeout=timeout,
                )
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(
                profile,
                DEFAULT_TOKENIZER_CACHE,
            )
            audit_config = load_review_claim_audit_config(
                Path(claim_audit_config_path)
            )
            audits: dict[str, dict[str, Any]] = {}
            audit_stages: dict[str, dict[str, Any]] = {}
            for candidate in candidates:
                label = candidate["candidate"]
                if label == "B":
                    audits[label] = self._load_b_audit(config)
                    continue
                audit, stage = _audit_candidate(
                    candidate,
                    package=source.package,
                    run_dir=run_dir,
                    audit_config=audit_config,
                    client=client,
                    profile=profile,
                    counter=counter,
                    replay_dir=resume_dir,
                )
                audits[label] = audit
                audit_stages[label] = stage
            framework = self._load_framework(config)
            dimensions = _core_dimensions(framework)
            structural_schema = build_structural_evaluation_schema(
                candidates=candidates,
                dimension_indexes=[
                    row["dimension_index"] for row in dimensions
                ],
            )
            structural_prompt = {
                "任务": "对A/B/C三份综述执行统一结构评价",
                "综述主题": config.topic,
                "综述目标": config.review_goal,
                "核心维度": dimensions,
                "候选草稿": candidates,
                "输出JSONSchema": structural_schema,
            }
            structural_source = (
                resume_dir / "structural" / "llm"
                if resume_dir is not None
                else None
            )
            if (
                structural_source is not None
                and (structural_source / "parsed_response.json").is_file()
            ):
                structural_raw = _read_json(
                    structural_source / "parsed_response.json"
                )
                structural_stage = _read_json(
                    structural_source / "result.json"
                )
                _write_json(
                    run_dir
                    / "structural"
                    / "llm"
                    / "parsed_response.json",
                    structural_raw,
                )
                _write_json(
                    run_dir / "structural" / "llm" / "result.json",
                    structural_stage,
                )
            else:
                structural_raw, structural_stage = execute_json_stage(
                    run_dir=run_dir,
                    stage="structural_evaluation",
                    task_name="review_structural_evaluation",
                    directory_name="structural/llm",
                    system_prompt=STRUCTURAL_EVALUATION_SYSTEM_PROMPT,
                    user_prompt=json.dumps(
                        structural_prompt,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    max_output_tokens=config.structural_max_output_tokens,
                    context={
                        "experiment_id": config.experiment_id,
                        "candidates": ["A", "B", "C"],
                    },
                    client=client,
                    profile=profile,
                    token_counter=counter,
                )
            structural = validate_structural_evaluation(
                structural_raw,
                schema=structural_schema,
            )
            structural_by_label = {
                row["candidate"]: row for row in structural["candidates"]
            }
            automatic = {
                candidate["candidate"]: compute_candidate_metrics(
                    candidate,
                    audits[candidate["candidate"]],
                    structural_by_label[candidate["candidate"]],
                    paper_count=config.expected_paper_count,
                    dimension_count=len(dimensions),
                )
                for candidate in candidates
            }
            pathway_usage = self._collect_pathway_usage(
                config,
                source.package,
                audit_stages,
            )
            decision = _architecture_decision(
                automatic,
                config.human_evaluation,
            )
            result = {
                "schema_version": "llm.review_evaluation.v1",
                "experiment_id": config.experiment_id,
                "automatic_metrics": automatic,
                "human_evaluation": config.human_evaluation,
                "structural_evaluation": structural,
                "architecture_decision": decision,
                "pathway_token_usage": pathway_usage,
                "shared_evaluation_usage": structural_stage["usage"],
                "candidate_audit_usage": {
                    label: stage["usage"]
                    for label, stage in audit_stages.items()
                },
                "api_cost": {
                    "status": "not_calculated",
                    "reason": "实验配置未提供供应商计价表。",
                },
            }
            _write_json(run_dir / "output" / "evaluation.json", result)
            for label, audit in audits.items():
                _write_json(
                    run_dir / "audits" / label / "validated_audit.json",
                    audit,
                )
            _write_text(
                run_dir / "review" / "evaluation_report.md",
                render_evaluation_report(result),
            )
            manifest = {
                "schema_version": REVIEW_EVALUATION_RUN_SCHEMA_VERSION,
                "run_id": resolved,
                "status": "completed",
                "started_at": started_at,
                "finished_at": _now(),
                "experiment_id": config.experiment_id,
                "resume_from_run_id": resume_from_run_id,
                "candidate_run_ids": {
                    "A": config.a_direct_run_id,
                    "B": config.b_writing_run_id,
                    "C": config.c_draft_run_id,
                },
                "decision_status": decision["status"],
            }
            _write_json(run_dir / "manifest.json", manifest)
            return _evaluation_result(run_dir, manifest, result)
        except Exception as exc:
            manifest = {
                "schema_version": REVIEW_EVALUATION_RUN_SCHEMA_VERSION,
                "run_id": resolved,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "experiment_id": config.experiment_id,
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
            }
            _write_json(run_dir / "manifest.json", manifest)
            return _evaluation_result(run_dir, manifest, None)

    def _load_candidates(
        self,
        config: ReviewEvaluationConfig,
        package: dict[str, Any],
    ) -> list[dict[str, Any]]:
        direct_dir = (
            self.workspace
            / "_review_direct_baselines"
            / "runs"
            / config.a_direct_run_id
        )
        direct_manifest = _read_json(direct_dir / "manifest.json")
        _require_completed(
            direct_manifest,
            config.a_direct_run_id,
            "方案A",
        )
        if (
            direct_manifest.get("source_package_run_id")
            != config.source_package_run_id
        ):
            raise ReviewEvaluationError(
                "evaluation.a_source_mismatch",
                "方案A与实验冻结知识包不一致。",
            )
        direct = canonicalize_direct_review(
            _read_json(direct_dir / "output" / "review_draft.json")
        )

        writing_dir = (
            self.workspace
            / "_review_writings"
            / "runs"
            / config.b_writing_run_id
        )
        writing_manifest = _read_json(writing_dir / "manifest.json")
        _require_completed(
            writing_manifest,
            config.b_writing_run_id,
            "方案B",
        )
        writing_output = _read_json(
            writing_dir / "output" / "review_draft.json"
        )
        rich = canonicalize_rich_review(
            {
                "working_title": writing_output["working_title"],
                "chapters": [
                    row["chapter"]
                    for row in writing_manifest["chapter_statuses"]
                ],
            }
        )

        evidence_map = {
            evidence["evidence_unit_id"]: paper["citation_key"]
            for paper in package["papers"]
            for evidence in paper["evidence_units"]
        }
        draft_dir = (
            self.workspace
            / "_review_drafts"
            / "runs"
            / config.c_draft_run_id
        )
        draft_manifest = _read_json(draft_dir / "manifest.json")
        _require_completed(
            draft_manifest,
            config.c_draft_run_id,
            "方案C",
        )
        if (
            draft_manifest.get("source_synthesis_run_id")
            != config.c_synthesis_run_id
        ):
            raise ReviewEvaluationError(
                "evaluation.c_source_mismatch",
                "方案C与配置的Evidence综合运行不一致。",
            )
        evidence = canonicalize_evidence_review(
            _read_json(draft_dir / "output" / "review_draft.json"),
            citation_by_evidence=evidence_map,
        )
        return [direct, rich, evidence]

    def _load_b_audit(
        self,
        config: ReviewEvaluationConfig,
    ) -> dict[str, Any]:
        audit_dir = (
            self.workspace
            / "_review_claim_audits"
            / "runs"
            / config.b_audit_run_id
        )
        manifest = _read_json(audit_dir / "manifest.json")
        _require_completed(manifest, config.b_audit_run_id, "方案B审计")
        if manifest.get("writing_run_id") != config.b_writing_run_id:
            raise ReviewEvaluationError(
                "evaluation.b_audit_source_mismatch",
                "方案B审计与写作运行不一致。",
            )
        claim_audits = []
        chapter_assessments = []
        for path in sorted(
            (audit_dir / "chapters").glob(
                "section-*/validated_audit.json"
            )
        ):
            audit = _read_json(path)
            claim_audits.extend(audit["claim_audits"])
            chapter_assessments.append(audit["chapter_assessment"])
        if not claim_audits:
            raise ReviewEvaluationError(
                "evaluation.b_audit_empty",
                "方案B审计没有Claim。",
            )
        return {
            "schema_version": "llm.review_candidate_claim_audit.v1",
            "candidate": "B",
            "section_index": 1,
            "claim_audits": claim_audits,
            "chapter_assessment": "\n".join(chapter_assessments),
            "source_audit_run_id": config.b_audit_run_id,
        }

    def _load_framework(
        self,
        config: ReviewEvaluationConfig,
    ) -> dict[str, Any]:
        writing_manifest = _read_json(
            self.workspace
            / "_review_writings"
            / "runs"
            / config.b_writing_run_id
            / "manifest.json"
        )
        return load_review_framework_run(
            self.workspace,
            writing_manifest["framework_run_id"],
        ).framework

    def _collect_pathway_usage(
        self,
        config: ReviewEvaluationConfig,
        package: dict[str, Any],
        audit_stages: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        direct_manifest = _read_json(
            self.workspace
            / "_review_direct_baselines"
            / "runs"
            / config.a_direct_run_id
            / "manifest.json"
        )
        a_usage = _collect_request_usage(
            [direct_manifest, audit_stages["A"]]
        )

        writing_dir = (
            self.workspace
            / "_review_writings"
            / "runs"
            / config.b_writing_run_id
        )
        writing_manifest = _read_json(writing_dir / "manifest.json")
        framework_id = writing_manifest["framework_run_id"]
        framework_manifest = _read_json(
            self.workspace
            / "_review_frameworks"
            / "runs"
            / framework_id
            / "manifest.json"
        )
        landscape_id = framework_manifest["landscape_run_id"]
        b_objects: list[object] = [
            writing_manifest,
            framework_manifest,
            _read_json(
                self.workspace
                / "_research_landscapes"
                / "runs"
                / landscape_id
                / "manifest.json"
            ),
        ]
        for paper in package["papers"]:
            b_objects.append(
                _read_json(
                    self.workspace
                    / "_paper_understandings"
                    / "runs"
                    / paper["source_understanding_run_id"]
                    / "manifest.json"
                )
            )
        b_audit_dir = (
            self.workspace
            / "_review_claim_audits"
            / "runs"
            / config.b_audit_run_id
        )
        b_objects.extend(
            _read_json(path)
            for path in sorted(
                (b_audit_dir / "chapters").glob(
                    "section-*/validated_audit.json"
                )
            )
        )
        b_usage = _collect_request_usage(b_objects)

        synthesis_manifest = _read_json(
            self.workspace
            / "_topic_syntheses"
            / "runs"
            / config.c_synthesis_run_id
            / "manifest.json"
        )
        c_objects: list[object] = [
            synthesis_manifest,
            _read_json(
                self.workspace
                / "_review_drafts"
                / "runs"
                / config.c_draft_run_id
                / "manifest.json"
            ),
            audit_stages["C"],
        ]
        for row in synthesis_manifest["source_runs"]:
            c_objects.append(
                _read_json(
                    self.workspace
                    / "_topic_reviews"
                    / "runs"
                    / row["run_id"]
                    / "manifest.json"
                )
            )
        c_usage = _collect_request_usage(c_objects)
        return {"A": a_usage, "B": b_usage, "C": c_usage}


def render_evaluation_report(result: dict[str, Any]) -> str:
    lines = [
        "# 14篇论文A/B/C综述写作评价",
        "",
        "## 自动指标",
        "",
        "| 指标 | A 直接全文 | B 新链路 | C Evidence-only |",
        "|---|---:|---:|---:|",
    ]
    metrics = [
        ("正文字符", "body_char_count"),
        ("论文覆盖率", "paper_coverage_rate"),
        ("核心维度覆盖率", "core_dimension_coverage_rate"),
        (
            "跨论文比较段落比例",
            "cross_paper_comparison_paragraph_rate",
        ),
        ("事实Claim支持率", "factual_claim_support_rate"),
        ("核心事实支持率", "core_factual_claim_support_rate"),
        ("核心不支持", "core_unsupported_claim_count"),
        ("result type写大", "result_type_overstatement_count"),
        (
            "validation level写大",
            "validation_level_overstatement_count",
        ),
        ("引用论文匹配率", "citation_paper_matching_rate"),
        ("语料缺口越界", "corpus_gap_overreach_count"),
        ("结构评分", "structure_score"),
        ("比较评分", "comparison_score"),
    ]
    automatic = result["automatic_metrics"]
    for label, key in metrics:
        values = [
            _format_metric(automatic[candidate][key])
            for candidate in ("A", "B", "C")
        ]
        lines.append(f"| {label} | {' | '.join(values)} |")
    decision = result["architecture_decision"]
    lines.extend(
        [
            "",
            "## 架构门槛",
            "",
            f"- 状态：{decision['status']}",
        ]
    )
    for check in decision["checks"]:
        lines.append(
            f"- {'通过' if check['passed'] else '未通过'}："
            f"{check['name']}（{check['detail']}）"
        )
    lines.extend(["", "## API Token", ""])
    for candidate in ("A", "B", "C"):
        usage = result["pathway_token_usage"][candidate]
        lines.append(
            f"- {candidate}：请求 {usage['request_count']} 次，"
            f"输入 {usage['prompt_tokens']}，"
            f"输出 {usage['completion_tokens']}，"
            f"合计 {usage['total_tokens']}"
        )
    lines.extend(["", "## 人工评价", ""])
    human = result["human_evaluation"]
    lines.append(f"- 状态：{human['status']}")
    for candidate in ("A", "B", "C"):
        row = human["candidates"][candidate]
        lines.append(
            f"- {candidate}：修改分钟={row['editing_minutes']}，"
            f"修改字符比例={row['edited_char_ratio']}"
        )
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 事实支持率来自独立Claim审计，不由结构评分模型代替。",
            "- 结构评分是自动评价，与人工修改时间分开保存。",
            "- 未配置供应商计价表，因此不推算费用。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _audit_candidate(
    candidate: dict[str, Any],
    *,
    package: dict[str, Any],
    run_dir: Path,
    audit_config: Any,
    client: Any,
    profile: Any,
    counter: Any,
    replay_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    chapter = {
        "section_index": 1,
        "paragraphs": [
            {
                "paragraph_index": row["paragraph_index"],
                "text": row["text"],
                "citation_keys": row["citation_keys"],
            }
            for row in candidate["paragraphs"]
        ],
    }
    catalogue = build_claim_catalogue(chapter)
    schema = build_chapter_claim_audit_schema(
        section_index=1,
        claim_count=len(catalogue),
        citation_keys=[
            paper["citation_key"] for paper in package["papers"]
        ],
        config=audit_config,
    )
    label = candidate["candidate"]
    replay_stage = (
        replay_dir / "audits" / label / "llm"
        if replay_dir is not None
        else None
    )
    if (
        replay_stage is not None
        and (replay_stage / "parsed_response.json").is_file()
    ):
        parsed = _read_json(replay_stage / "parsed_response.json")
        stage = _read_json(replay_stage / "result.json")
        _write_json(
            run_dir / "audits" / label / "llm" / "parsed_response.json",
            parsed,
        )
        _write_json(
            run_dir / "audits" / label / "llm" / "result.json",
            stage,
        )
    else:
        parsed, stage = execute_json_stage(
            run_dir=run_dir,
            stage=f"candidate_{label.lower()}_claim_audit",
            task_name="review_evaluation_claim_audit",
            directory_name=f"audits/{label}/llm",
            system_prompt=CLAIM_AUDIT_SYSTEM_PROMPT,
            user_prompt=build_claim_audit_prompt(
                chapter,
                package,
                catalogue,
                schema,
            ),
            max_output_tokens=audit_config.audit_max_output_tokens,
            context={"candidate": label},
            client=client,
            profile=profile,
            token_counter=counter,
        )
    audit = validate_chapter_claim_audit(
        parsed,
        schema=schema,
        catalogue=catalogue,
        package=package,
    )
    audit["candidate"] = label
    audit["stage_result"] = stage
    audit["summary"] = summarize_claim_audits([audit], [], 1)
    return audit, stage


def _canonical_candidate(
    candidate: str,
    title: str,
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    paragraphs = []
    for section_index, section in enumerate(sections, start=1):
        for local_index, paragraph in enumerate(
            section["paragraphs"],
            start=1,
        ):
            paragraphs.append(
                {
                    "paragraph_index": len(paragraphs) + 1,
                    "section_index": section_index,
                    "section_heading": section["heading"],
                    "section_paragraph_index": local_index,
                    "text": paragraph["text"],
                    "citation_keys": list(
                        dict.fromkeys(paragraph["citation_keys"])
                    ),
                }
            )
    if not paragraphs:
        raise ReviewEvaluationError(
            "evaluation.candidate_empty",
            f"方案{candidate}没有正文段落。",
        )
    return {
        "candidate": candidate,
        "title": title,
        "sections": sections,
        "paragraphs": paragraphs,
    }


def _evidence_citations(
    evidence_ids: list[str],
    citation_by_evidence: dict[str, str],
) -> list[str]:
    unknown = [
        evidence_id
        for evidence_id in evidence_ids
        if evidence_id not in citation_by_evidence
    ]
    if unknown:
        raise ReviewEvaluationError(
            "evaluation.unknown_evidence",
            f"方案C引用未知Evidence：{unknown}",
        )
    return list(
        dict.fromkeys(
            citation_by_evidence[evidence_id]
            for evidence_id in evidence_ids
        )
    )


def _core_dimensions(framework: dict[str, Any]) -> list[dict[str, Any]]:
    dimensions = []
    for section in framework["sections"]:
        if section["section_type"] in {"introduction", "conclusion"}:
            continue
        dimensions.append(
            {
                "dimension_index": len(dimensions) + 1,
                "title": section["title"],
                "question": section["question"],
            }
        )
    if not dimensions:
        raise ReviewEvaluationError(
            "evaluation.core_dimensions_empty",
            "Review Framework没有核心正文维度。",
        )
    return dimensions


def _architecture_decision(
    automatic: dict[str, dict[str, Any]],
    human: dict[str, Any],
) -> dict[str, Any]:
    checks = [
        {
            "name": "B核心事实支持率不低于A",
            "passed": (
                automatic["B"]["core_factual_claim_support_rate"]
                >= automatic["A"]["core_factual_claim_support_rate"]
            ),
            "detail": (
                f"B={automatic['B']['core_factual_claim_support_rate']}, "
                f"A={automatic['A']['core_factual_claim_support_rate']}"
            ),
        },
        {
            "name": "B跨论文比较与结构评分高于C",
            "passed": (
                automatic["B"]["comparison_score"]
                > automatic["C"]["comparison_score"]
                and automatic["B"]["structure_score"]
                > automatic["C"]["structure_score"]
            ),
            "detail": (
                f"B比较/结构={automatic['B']['comparison_score']}/"
                f"{automatic['B']['structure_score']}，"
                f"C={automatic['C']['comparison_score']}/"
                f"{automatic['C']['structure_score']}"
            ),
        },
    ]
    if human["status"] == "completed":
        b_minutes = human["candidates"]["B"]["editing_minutes"]
        c_minutes = human["candidates"]["C"]["editing_minutes"]
        passed = b_minutes <= c_minutes * 0.75
        detail = f"B={b_minutes}分钟，C={c_minutes}分钟"
    else:
        passed = False
        detail = "人工修改时间尚未填写"
    checks.append(
        {
            "name": "B人工修改时间低于C至少25%",
            "passed": passed,
            "detail": detail,
        }
    )
    if not all(row["passed"] for row in checks[:2]):
        status = "fail_automatic_gate"
    elif human["status"] != "completed":
        status = "pending_human_evaluation"
    elif all(row["passed"] for row in checks):
        status = "pass"
    else:
        status = "fail"
    return {"status": status, "checks": checks}


def _collect_request_usage(objects: list[object]) -> dict[str, int]:
    requests: dict[str, dict[str, int]] = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            request_id = value.get("request_id")
            usage = value.get("usage")
            if isinstance(request_id, str) and isinstance(usage, dict):
                parsed: dict[str, int] = {}
                for key in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                ):
                    item = usage.get(key, 0)
                    parsed[key] = item if type(item) is int else 0
                requests[request_id] = parsed
                return
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for obj in objects:
        visit(obj)
    return {
        "request_count": len(requests),
        "prompt_tokens": sum(
            row["prompt_tokens"] for row in requests.values()
        ),
        "completion_tokens": sum(
            row["completion_tokens"] for row in requests.values()
        ),
        "total_tokens": sum(
            row["total_tokens"] for row in requests.values()
        ),
    }


def _validate_human_evaluation(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "status",
        "candidates",
    }:
        raise ReviewEvaluationError(
            "evaluation.human_config_invalid",
            "人工评价字段无效。",
        )
    if value["status"] not in {"pending", "completed"}:
        raise ReviewEvaluationError(
            "evaluation.human_status_invalid",
            "人工评价状态必须为pending或completed。",
        )
    candidates = value["candidates"]
    if not isinstance(candidates, dict) or set(candidates) != {"A", "B", "C"}:
        raise ReviewEvaluationError(
            "evaluation.human_candidates_invalid",
            "人工评价必须包含A、B、C。",
        )
    for row in candidates.values():
        if not isinstance(row, dict) or set(row) != {
            "editing_minutes",
            "edited_char_ratio",
        }:
            raise ReviewEvaluationError(
                "evaluation.human_candidate_invalid",
                "人工评价候选字段无效。",
            )
        for field in ("editing_minutes", "edited_char_ratio"):
            item = row[field]
            if value["status"] == "pending" and item is not None:
                raise ReviewEvaluationError(
                    "evaluation.human_pending_value_present",
                    "pending状态不得预填人工指标。",
                )
            if value["status"] == "completed" and not isinstance(
                item,
                (int, float),
            ):
                raise ReviewEvaluationError(
                    "evaluation.human_completed_value_missing",
                    "completed状态必须填写人工指标。",
                )


def _require_completed(
    manifest: dict[str, Any],
    run_id: str,
    label: str,
) -> None:
    if (
        manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewEvaluationError(
            "evaluation.source_run_invalid",
            f"{label}运行身份或状态无效。",
        )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _format_metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2%}"
    return str(value)


def _evaluation_result(
    run_dir: Path,
    manifest: dict[str, Any],
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "decision_status": manifest.get("decision_status"),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "report_path": str(run_dir / "review" / "evaluation_report.md"),
        "automatic_metrics": (
            result["automatic_metrics"] if result is not None else None
        ),
        "error_code": manifest.get("error_code"),
        "error_message": manifest.get("error_message"),
    }


def _safe_segment(value: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ReviewEvaluationError(
            "evaluation.path_segment_invalid",
            f"不安全的运行ID：{value!r}",
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewEvaluationError(
            "evaluation.json_read_failed",
            f"无法读取JSON：{path}",
        ) from exc
    if not isinstance(payload, dict):
        raise ReviewEvaluationError(
            "evaluation.json_not_object",
            f"JSON不是对象：{path}",
        )
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()
