from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .chapter_knowledge_package import load_chapter_knowledge_package_run
from .llm_analysis import DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_claim_audit_contracts import load_review_claim_audit_config
from .review_direct_a2 import load_review_direct_a2_run
from .review_evaluation import (
    DEFAULT_REVIEW_CLAIM_AUDIT_CONFIG,
    STRUCTURAL_EVALUATION_SCHEMA_VERSION,
    audit_review_candidate,
    build_structural_evaluation_schema,
    canonicalize_review_candidate,
    compute_candidate_metrics,
    validate_structural_evaluation,
)
from .review_b2_final_assembly import load_review_b2_final_run
from .review_writing_b2_audited_assembly import load_review_writing_b2_audited_assembly
from .review_writing_b2_revision import load_review_writing_b2_source


AB2_EVALUATION_ROOT = "_review_evaluations_ab2"
AB2_STABILITY_ROOT = "_review_evaluations_ab2_stability"
AB2_EVALUATION_RUN_SCHEMA_VERSION = "llm.review_ab2_evaluation_run.v1"
AB2_STABILITY_RUN_SCHEMA_VERSION = "llm.review_ab2_stability_run.v1"
AB2_EVALUATION_CONFIG_SCHEMA_VERSION = "llm.review_ab2_evaluation_config.v1"
STRUCTURAL_SYSTEM_PROMPT = """你是盲化文献综述结构评价员。输入包含同一论文集合、同一主题、同一九章Framework形成的两份候选综述，但不提供其生成路径。
本阶段只评价结构与综合方式，不判断事实真伪；事实支持由另一个统一Claim审计器完成。
逐份判断核心维度覆盖、真正比较两篇及以上论文的段落、语料缺口是否越界、整体结构和跨论文比较质量。
跨论文比较必须在同一段落中比较、联系、区分或综合至少两篇论文，仅并列列出多篇引用不算比较。
语料缺口越界指把“本次语料未覆盖”写成“整个领域不存在研究”或学界共识。
不得猜测候选的生成方法。只输出符合JSON Schema的对象。"""


class ReviewAB2EvaluationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


def load_ab2_evaluation_config(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    expected = {
        "schema_version",
        "experiment_id",
        "source_package_run_id",
        "audited_assembly_run_id",
        "a2_run_id",
        "b2_final_run_id",
        "structural_max_output_tokens",
        "human_evaluation",
    }
    if set(value) != expected or value["schema_version"] != AB2_EVALUATION_CONFIG_SCHEMA_VERSION:
        raise ReviewAB2EvaluationError("ab2_evaluation.config_invalid", "A2/B2评价配置无效。")
    for field in expected - {"schema_version", "structural_max_output_tokens", "human_evaluation"}:
        if not isinstance(value[field], str) or not value[field].strip():
            raise ReviewAB2EvaluationError("ab2_evaluation.config_value_invalid", f"{field}无效。")
    if type(value["structural_max_output_tokens"]) is not int or value["structural_max_output_tokens"] < 1024:
        raise ReviewAB2EvaluationError("ab2_evaluation.output_budget_invalid", "结构评价预算无效。")
    human = value["human_evaluation"]
    if human != {
        "status": "pending",
        "editing_minutes": {"A2": None, "B2": None},
        "edited_char_ratio": {"A2": None, "B2": None},
    }:
        raise ReviewAB2EvaluationError(
            "ab2_evaluation.human_state_invalid", "首次评价必须保留待填写的人工成本。"
        )
    return value


class ReviewAB2EvaluationRunner:
    def __init__(self, workspace, *, analysis_client=None, token_counter=None) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        config_path: str | Path,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        audit_config_path: str | Path = DEFAULT_REVIEW_CLAIM_AUDIT_CONFIG,
        resume_from_run_id: str | None = None,
        timeout: int = 1800,
    ):
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / AB2_EVALUATION_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewAB2EvaluationError("ab2_evaluation.run_exists", f"run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        config = result = None
        stages = {}
        try:
            config = load_ab2_evaluation_config(Path(config_path))
            package = load_chapter_knowledge_package_run(
                self.workspace, config["source_package_run_id"]
            )
            body = load_review_writing_b2_audited_assembly(
                self.workspace, config["audited_assembly_run_id"]
            )
            first_chapter = load_review_writing_b2_source(
                self.workspace, body.draft["chapter_run_ids"][0]
            )
            framework = first_chapter.writing_input["framework_outline"]
            a2 = load_review_direct_a2_run(self.workspace, config["a2_run_id"])
            b2 = load_review_b2_final_run(self.workspace, config["b2_final_run_id"])
            _validate_sources(config, package, body, a2, b2)
            pathway_candidates = {
                "A2": _canonical_from_chapters("A2", a2.review),
                "B2": _canonical_from_chapters("B2", b2.review),
            }
            blind_order = _blind_order(config["experiment_id"])
            blind_mapping = {
                f"candidate_{index}": pathway
                for index, pathway in enumerate(blind_order, start=1)
            }
            candidates = []
            for blind_label, pathway in blind_mapping.items():
                candidate = json.loads(json.dumps(pathway_candidates[pathway], ensure_ascii=False))
                candidate["candidate"] = blind_label
                candidates.append(candidate)
            dimensions = _extract_ab2_dimensions(framework)
            citation_keys = {row["citation_key"] for row in package.package["papers"]}
            for candidate in candidates:
                unknown = {
                    key for row in candidate["paragraphs"] for key in row["citation_keys"]
                    if key not in citation_keys
                }
                if unknown:
                    raise ReviewAB2EvaluationError(
                        "ab2_evaluation.unknown_citation", f"候选含未知引用：{sorted(unknown)}"
                    )
            _write_json(run_dir / "input" / "config.json", config)
            _write_json(run_dir / "input" / "blind_candidates.json", candidates)
            _write_json(run_dir / "input" / "blind_mapping.json", blind_mapping)
            _write_json(run_dir / "input" / "core_dimensions.json", dimensions)
            resume_dir = None
            if resume_from_run_id is not None:
                _safe_segment(resume_from_run_id)
                resume_dir = self.workspace / AB2_EVALUATION_ROOT / "runs" / resume_from_run_id
                old_config = _read_json(resume_dir / "input" / "config.json")
                old_mapping = _read_json(resume_dir / "input" / "blind_mapping.json")
                if old_config != config or old_mapping != blind_mapping:
                    raise ReviewAB2EvaluationError(
                        "ab2_evaluation.resume_source_mismatch",
                        "续跑来源的配置或盲化映射不一致。",
                    )
            _write_json(
                run_dir / "input" / "resume.json",
                {"resume_from_run_id": resume_from_run_id},
            )
            audit_config = load_review_claim_audit_config(Path(audit_config_path))
            if provider != package.profile.provider:
                raise ReviewAB2EvaluationError("ab2_evaluation.provider_mismatch", "provider不匹配。")
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url, api_key_env=api_key_env,
                model=package.profile.request_model, timeout=timeout
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(
                package.profile, DEFAULT_TOKENIZER_CACHE
            )
            blind_audits = {}
            for candidate in candidates:
                label = candidate["candidate"]
                replay_dir = None
                if (
                    resume_dir is not None
                    and (resume_dir / "audits" / label / "validated_audit.json").is_file()
                ):
                    replay_dir = resume_dir
                audit, stage = audit_review_candidate(
                    candidate,
                    package=package.package,
                    run_dir=run_dir,
                    audit_config=audit_config,
                    client=client,
                    profile=package.profile,
                    counter=counter,
                    replay_dir=replay_dir,
                )
                blind_audits[label] = audit
                stages[f"audit_{label}"] = stage
                _write_json(run_dir / "audits" / label / "validated_audit.json", audit)
            structural_schema = build_structural_evaluation_schema(
                candidates=candidates,
                dimension_indexes=[row["dimension_index"] for row in dimensions],
            )
            _write_json(run_dir / "input" / "structural_schema.json", structural_schema)
            parsed, structural_stage = execute_json_stage(
                run_dir=run_dir,
                stage="blind_structural_evaluation",
                task_name="review_ab2_structural_evaluation",
                directory_name="structural_evaluation",
                system_prompt=STRUCTURAL_SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    {
                        "任务": "盲化比较两份候选综述的结构和跨论文综合质量",
                        "核心维度": dimensions,
                        "候选综述": candidates,
                        "输出JSONSchema": structural_schema,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                max_output_tokens=config["structural_max_output_tokens"],
                context={"experiment_id": config["experiment_id"], "candidate_count": 2},
                client=client,
                profile=package.profile,
                token_counter=counter,
            )
            stages["structural"] = structural_stage
            structural = validate_structural_evaluation(parsed, schema=structural_schema)
            _write_json(run_dir / "structural_evaluation" / "validated_result.json", structural)
            structural_by_blind = {row["candidate"]: row for row in structural["candidates"]}
            automatic = {}
            for blind_label, pathway in blind_mapping.items():
                candidate = next(row for row in candidates if row["candidate"] == blind_label)
                metrics = compute_candidate_metrics(
                    candidate,
                    blind_audits[blind_label],
                    structural_by_blind[blind_label],
                    paper_count=14,
                    dimension_count=len(dimensions),
                )
                metrics["unsupported_claims_per_1000_chars"] = _per_thousand(
                    metrics["unsupported_claim_count"], metrics["body_char_count"]
                )
                metrics["core_unsupported_claims_per_1000_chars"] = _per_thousand(
                    metrics["core_unsupported_claim_count"], metrics["body_char_count"]
                )
                automatic[pathway] = metrics
            gates = _automatic_gates(automatic)
            result = {
                "schema_version": "llm.review_ab2_evaluation.v1",
                "pipeline_generation": "AB2.phase6b",
                "experiment_id": config["experiment_id"],
                "source": {
                    "a2_run_id": a2.run_id,
                    "b2_final_run_id": b2.run_id,
                    "source_package_run_id": package.run_id,
                    "audited_assembly_run_id": body.run_id,
                },
                "blind_mapping": blind_mapping,
                "automatic_metrics": automatic,
                "structural_assessments": {
                    blind_mapping[row["candidate"]]: row
                    for row in structural["candidates"]
                },
                "automatic_gates": gates,
                "human_evaluation": config["human_evaluation"],
                "cost_evaluation": {
                    "status": "incomplete_b2_full_chain_not_aggregated",
                    "evaluation_request_usage": _collect_usage(stages),
                    "a2_generation_usage": _read_a2_usage(self.workspace, a2.run_id),
                    "b2_total_usage": None,
                },
                "decision_status": (
                    "failed_automatic_gates"
                    if not gates["passed"]
                    else "pending_human_and_full_cost_evaluation"
                ),
            }
            _write_json(run_dir / "output" / "review_ab2_evaluation.json", result)
            _write_text(run_dir / "review" / "review_ab2_evaluation.md", _render_report(result))
            manifest = _manifest(
                resolved, "completed", started_at, config, result, stages,
                resume_from_run_id, None
            )
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(
                resolved, "failed", started_at, config, result, stages,
                resume_from_run_id, failure
            )
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": manifest["status"],
            "run_dir": str(run_dir),
            "output_path": str(run_dir / "output" / "review_ab2_evaluation.json") if result else None,
            "report_path": str(run_dir / "review" / "review_ab2_evaluation.md") if result else None,
            "decision_status": result["decision_status"] if result else None,
            "failure": manifest["failure"],
        }


class ReviewAB2StructuralStabilityRunner:
    def __init__(self, workspace, *, analysis_client=None, token_counter=None) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        source_evaluation_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 1800,
    ):
        _safe_segment(source_evaluation_run_id)
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / AB2_STABILITY_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewAB2EvaluationError(
                "ab2_stability.run_exists", f"run_id已存在：{resolved}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        result = stage = None
        source_dir = (
            self.workspace / AB2_EVALUATION_ROOT / "runs" / source_evaluation_run_id
        )
        try:
            source_manifest = _read_json(source_dir / "manifest.json")
            if source_manifest.get("status") != "completed":
                raise ReviewAB2EvaluationError(
                    "ab2_stability.source_not_completed", "来源评价运行尚未完成。"
                )
            config = load_ab2_evaluation_config(source_dir / "input" / "config.json")
            candidates = _read_json_list(source_dir / "input" / "blind_candidates.json")
            dimensions = _read_json_list(source_dir / "input" / "core_dimensions.json")
            mapping = _read_json(source_dir / "input" / "blind_mapping.json")
            original = _read_json(
                source_dir / "structural_evaluation" / "validated_result.json"
            )
            original_by_candidate = {
                row["candidate"]: row for row in original["candidates"]
            }
            reversed_candidates = list(reversed(candidates))
            schema = build_structural_evaluation_schema(
                candidates=reversed_candidates,
                dimension_indexes=[row["dimension_index"] for row in dimensions],
            )
            package = load_chapter_knowledge_package_run(
                self.workspace, config["source_package_run_id"]
            )
            if provider != package.profile.provider:
                raise ReviewAB2EvaluationError(
                    "ab2_stability.provider_mismatch", "provider不匹配。"
                )
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=package.profile.request_model,
                timeout=timeout,
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(
                package.profile, DEFAULT_TOKENIZER_CACHE
            )
            _write_json(run_dir / "input" / "source.json", {
                "source_evaluation_run_id": source_evaluation_run_id,
                "experiment_id": config["experiment_id"],
            })
            _write_json(run_dir / "input" / "blind_candidates_reversed.json", reversed_candidates)
            _write_json(run_dir / "input" / "blind_mapping.json", mapping)
            _write_json(run_dir / "input" / "core_dimensions.json", dimensions)
            _write_json(run_dir / "input" / "structural_schema.json", schema)
            parsed, stage = execute_json_stage(
                run_dir=run_dir,
                stage="reversed_blind_structural_evaluation",
                task_name="review_ab2_reversed_structural_evaluation",
                directory_name="structural_evaluation",
                system_prompt=STRUCTURAL_SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    {
                        "任务": "以反转候选展示顺序重新盲化比较结构和跨论文综合质量",
                        "核心维度": dimensions,
                        "候选综述": reversed_candidates,
                        "输出JSONSchema": schema,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                max_output_tokens=config["structural_max_output_tokens"],
                context={
                    "experiment_id": config["experiment_id"],
                    "source_evaluation_run_id": source_evaluation_run_id,
                    "candidate_order": [row["candidate"] for row in reversed_candidates],
                },
                client=client,
                profile=package.profile,
                token_counter=counter,
            )
            reversed_result = validate_structural_evaluation(parsed, schema=schema)
            _write_json(
                run_dir / "structural_evaluation" / "validated_result.json",
                reversed_result,
            )
            reversed_by_candidate = {
                row["candidate"]: row for row in reversed_result["candidates"]
            }
            comparisons = {
                mapping[label]: _compare_structural_assessments(
                    original_by_candidate[label], reversed_by_candidate[label]
                )
                for label in mapping
            }
            stability = _summarize_structural_stability(comparisons)
            result = {
                "schema_version": "llm.review_ab2_structural_stability.v1",
                "pipeline_generation": "AB2.phase6c",
                "source_evaluation_run_id": source_evaluation_run_id,
                "experiment_id": config["experiment_id"],
                "original_candidate_order": [row["candidate"] for row in candidates],
                "reversed_candidate_order": [row["candidate"] for row in reversed_candidates],
                "blind_mapping": mapping,
                "comparisons": comparisons,
                **stability,
                "usage": _collect_usage({"structural": stage}),
            }
            _write_json(run_dir / "output" / "review_ab2_structural_stability.json", result)
            _write_text(
                run_dir / "review" / "review_ab2_structural_stability.md",
                _render_stability_report(result),
            )
            manifest = _stability_manifest(
                resolved, "completed", started_at, source_evaluation_run_id, result, stage, None
            )
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _stability_manifest(
                resolved, "failed", started_at, source_evaluation_run_id, result, stage, failure
            )
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": manifest["status"],
            "run_dir": str(run_dir),
            "report_path": (
                str(run_dir / "review" / "review_ab2_structural_stability.md")
                if result else None
            ),
            "decision_status": result["decision_status"] if result else None,
            "failure": manifest["failure"],
        }


def _canonical_from_chapters(pathway, review):
    sections = [
        {
            "heading": chapter["title"],
            "paragraphs": [
                {"text": row["text"], "citation_keys": list(row["citation_keys"])}
                for row in chapter["paragraphs"]
            ],
        }
        for chapter in review["chapters"]
    ]
    return canonicalize_review_candidate(pathway, review["working_title"], sections)


def _validate_sources(config, package, body, a2, b2):
    if a2.a2_input["source"]["source_package_run_id"] != package.run_id:
        raise ReviewAB2EvaluationError("ab2_evaluation.a2_package_mismatch", "A2语料来源不一致。")
    if a2.a2_input["source"]["audited_assembly_run_id"] != body.run_id:
        raise ReviewAB2EvaluationError("ab2_evaluation.a2_framework_mismatch", "A2 Framework来源不一致。")
    if b2.review["source"]["audited_assembly_run_id"] != body.run_id:
        raise ReviewAB2EvaluationError("ab2_evaluation.b2_body_mismatch", "B2正文来源不一致。")


def _blind_order(experiment_id):
    digest = hashlib.sha256(experiment_id.encode("utf-8")).digest()[0]
    return ["A2", "B2"] if digest % 2 == 0 else ["B2", "A2"]


def _extract_ab2_dimensions(framework):
    dimensions = [
        {
            "dimension_index": index,
            "title": section["title"],
            "question": section["title"],
        }
        for index, section in enumerate(
            [
                row
                for row in framework["sections"]
                if row["section_type"] == "body"
            ],
            start=1,
        )
    ]
    if not dimensions:
        raise ReviewAB2EvaluationError(
            "ab2_evaluation.dimensions_empty", "统一Framework没有正文维度。"
        )
    return dimensions


def _automatic_gates(metrics):
    checks = [
        {
            "name": "B2核心事实支持率不低于A2超过5个百分点",
            "passed": metrics["B2"]["core_factual_claim_support_rate"]
            >= metrics["A2"]["core_factual_claim_support_rate"] - 0.05,
        },
        {"name": "B2核心unsupported为0", "passed": metrics["B2"]["core_unsupported_claim_count"] == 0},
        {"name": "B2 result type写大为0", "passed": metrics["B2"]["result_type_overstatement_count"] == 0},
        {"name": "B2 validation level写大为0", "passed": metrics["B2"]["validation_level_overstatement_count"] == 0},
        {"name": "B2结构评分至少4", "passed": metrics["B2"]["structure_score"] >= 4},
        {"name": "B2比较评分至少4", "passed": metrics["B2"]["comparison_score"] >= 4},
    ]
    return {"passed": all(row["passed"] for row in checks), "checks": checks}


def _compare_structural_assessments(original, reversed_result):
    original_comparisons = set(original["cross_paper_comparison_paragraph_indexes"])
    reversed_comparisons = set(reversed_result["cross_paper_comparison_paragraph_indexes"])
    original_overreach = {
        row["paragraph_index"] for row in original["corpus_gap_overreachs"]
    }
    reversed_overreach = {
        row["paragraph_index"] for row in reversed_result["corpus_gap_overreachs"]
    }
    return {
        "original_scores": {
            "structure": original["structure_score"],
            "comparison": original["comparison_score"],
        },
        "reversed_scores": {
            "structure": reversed_result["structure_score"],
            "comparison": reversed_result["comparison_score"],
        },
        "score_delta": {
            "structure": reversed_result["structure_score"] - original["structure_score"],
            "comparison": reversed_result["comparison_score"] - original["comparison_score"],
        },
        "score_stable": (
            original["structure_score"] == reversed_result["structure_score"]
            and original["comparison_score"] == reversed_result["comparison_score"]
        ),
        "dimension_selection_stable": set(original["covered_dimension_indexes"])
        == set(reversed_result["covered_dimension_indexes"]),
        "comparison_paragraph_overlap": _set_overlap(
            original_comparisons, reversed_comparisons
        ),
        "corpus_gap_overreach_overlap": _set_overlap(
            original_overreach, reversed_overreach
        ),
    }


def _set_overlap(left, right):
    union = left | right
    return {
        "original_count": len(left),
        "reversed_count": len(right),
        "intersection_count": len(left & right),
        "jaccard": round(len(left & right) / len(union), 4) if union else 1.0,
    }


def _summarize_structural_stability(comparisons):
    score_stable = all(row["score_stable"] for row in comparisons.values())
    relative_ranking_stable = all(
        _score_relation(
            comparisons["A2"][f"{order}_scores"][metric],
            comparisons["B2"][f"{order}_scores"][metric],
        )
        == _score_relation(
            comparisons["A2"]["original_scores"][metric],
            comparisons["B2"]["original_scores"][metric],
        )
        for order in ("reversed",)
        for metric in ("structure", "comparison")
    )
    threshold_classification_stable = all(
        (row["original_scores"][metric] >= 4)
        == (row["reversed_scores"][metric] >= 4)
        for row in comparisons.values()
        for metric in ("structure", "comparison")
    )
    if score_stable:
        decision_status = "stable_scores"
    elif not relative_ranking_stable:
        decision_status = "order_sensitive_ranking_requires_human"
    elif not threshold_classification_stable:
        decision_status = "order_sensitive_threshold_requires_human"
    else:
        decision_status = "stable_ranking_score_scale_shifted"
    return {
        "score_stable": score_stable,
        "relative_ranking_stable": relative_ranking_stable,
        "threshold_classification_stable": threshold_classification_stable,
        "decision_status": decision_status,
    }


def _score_relation(left, right):
    return (left > right) - (left < right)


def _per_thousand(count, chars):
    return round(count * 1000 / chars, 4) if chars else 0.0


def _collect_usage(stages):
    usage = {"request_count": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for stage in stages.values():
        if not isinstance(stage, dict) or not isinstance(stage.get("usage"), dict):
            continue
        usage["request_count"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = stage["usage"].get(key, 0)
            usage[key] += value if type(value) is int else 0
    return usage


def _read_a2_usage(workspace, run_id):
    manifest = _read_json(Path(workspace) / "_review_direct_baselines_a2" / "runs" / run_id / "manifest.json")
    stage = manifest.get("stage_result") or {}
    return _collect_usage({"generation": stage})


def _render_report(result):
    lines = [
        "# A2/B2公平对照自动评价", "",
        f"- 实验：`{result['experiment_id']}`",
        f"- 决策状态：`{result['decision_status']}`",
        "- 人工编辑成本：尚未测量", "",
        "## 自动指标", "",
        "| 指标 | A2 | B2 |", "|---|---:|---:|",
    ]
    keys = [
        ("正文字符", "body_char_count"),
        ("事实Claim支持率", "factual_claim_support_rate"),
        ("核心事实支持率", "core_factual_claim_support_rate"),
        ("核心unsupported", "core_unsupported_claim_count"),
        ("result type写大", "result_type_overstatement_count"),
        ("validation level写大", "validation_level_overstatement_count"),
        ("每千字unsupported", "unsupported_claims_per_1000_chars"),
        ("跨论文比较段落率", "cross_paper_comparison_paragraph_rate"),
        ("结构评分", "structure_score"),
        ("比较评分", "comparison_score"),
        ("题录匹配率", "citation_paper_matching_rate"),
        ("语料缺口越界", "corpus_gap_overreach_count"),
    ]
    for label, key in keys:
        lines.append(f"| {label} | {result['automatic_metrics']['A2'][key]} | {result['automatic_metrics']['B2'][key]} |")
    lines.extend(["", "## 自动门禁", ""])
    for row in result["automatic_gates"]["checks"]:
        lines.append(f"- {'通过' if row['passed'] else '失败'}：{row['name']}")
    lines.extend([
        "", "## 未决事项", "",
        "- 尚未测量两份稿件的实际人工编辑分钟数和修改字符比例。",
        "- 尚未完成B2从Paper Understanding起的全链Token聚合，因此不能判断3倍成本门槛。",
        "- 自动评价通过不等于方案可以产品化，最终架构决策保持未决。", "",
    ])
    return "\n".join(lines)


def _render_stability_report(result):
    lines = [
        "# A2/B2结构盲评顺序稳定性复评",
        "",
        f"- 来源评价：`{result['source_evaluation_run_id']}`",
        f"- 结论状态：`{result['decision_status']}`",
        f"- 评分是否稳定：`{result['score_stable']}`",
        f"- A2/B2相对排序是否稳定：`{result['relative_ranking_stable']}`",
        f"- 4分门槛判定是否稳定：`{result['threshold_classification_stable']}`",
        "",
        "## 评分对照",
        "",
        "| 方案 | 原结构分 | 反转结构分 | 原比较分 | 反转比较分 |",
        "|---|---:|---:|---:|---:|",
    ]
    for pathway in ("A2", "B2"):
        row = result["comparisons"][pathway]
        lines.append(
            f"| {pathway} | {row['original_scores']['structure']} | "
            f"{row['reversed_scores']['structure']} | "
            f"{row['original_scores']['comparison']} | "
            f"{row['reversed_scores']['comparison']} |"
        )
    lines.extend([
        "",
        "## 解释边界",
        "",
        "- 本复评只检验候选展示顺序对结构和比较评分的影响，不重复事实Claim审计。",
        "- 段落选择与语料缺口标记是模型判断，重合度用于显示波动，不作为事实金标准。",
        "- 若评分变化，则自动架构结论必须保持未决并进入人工盲评。",
        "",
    ])
    return "\n".join(lines)


def _manifest(run_id, status, started_at, config, result, stages, resume_from_run_id, failure):
    return {
        "schema_version": AB2_EVALUATION_RUN_SCHEMA_VERSION,
        "pipeline_generation": "AB2.phase6b",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "experiment_id": config["experiment_id"] if config else None,
        "decision_status": result["decision_status"] if result else None,
        "stage_results": stages,
        "resume_from_run_id": resume_from_run_id,
        "failure": failure,
    }


def _stability_manifest(run_id, status, started_at, source_run_id, result, stage, failure):
    return {
        "schema_version": AB2_STABILITY_RUN_SCHEMA_VERSION,
        "pipeline_generation": "AB2.phase6c",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "source_evaluation_run_id": source_run_id,
        "decision_status": result["decision_status"] if result else None,
        "stage_result": stage,
        "failure": failure,
    }


def _safe_segment(value):
    if not value or Path(value).name != value or value in {".", ".."}:
        raise ReviewAB2EvaluationError("ab2_evaluation.run_id_invalid", "run_id不是安全路径段。")


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewAB2EvaluationError("ab2_evaluation.json_read_failed", f"无法读取JSON：{path}") from exc
    if not isinstance(value, dict):
        raise ReviewAB2EvaluationError("ab2_evaluation.json_object_required", "JSON必须为对象。")
    return value


def _read_json_list(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewAB2EvaluationError(
            "ab2_evaluation.json_read_failed", f"无法读取JSON：{path}"
        ) from exc
    if not isinstance(value, list):
        raise ReviewAB2EvaluationError(
            "ab2_evaluation.json_array_required", "JSON必须为数组。"
        )
    return value


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _now():
    return datetime.now(UTC).isoformat()
