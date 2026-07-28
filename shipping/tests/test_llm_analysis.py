from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_analysis import (
    AnalysisInputError,
    LLMAnalysisRunner,
    LLMSemanticAutoResolutionRunner,
    LLMSemanticAdjudicationRunner,
    LLMSemanticAuditRunner,
    LLMStatementRevisionRunner,
    build_evidence_prompts,
    create_input_snapshot,
)
from shipping_pipeline.llm_contracts import (
    EVIDENCE_BATCH_SCHEMA_VERSION,
    NON_USED_EVIDENCE_DISPOSITION_SCHEMA_VERSION,
    PAPER_ANALYSIS_SCHEMA_VERSION,
    PAPER_ANALYSIS_DRAFT_SCHEMA_VERSION,
    SECTION_SUMMARY_SCHEMA_VERSION,
    assign_evidence_unit_ids,
    validate_paper_analysis,
)
from shipping_pipeline.llm_provider import (
    OpenAICompatibleAnalysisClient,
    ProviderCallError,
    ProviderResult,
    build_chat_request,
    validate_completed_response,
)
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.llm_statement_support import STATEMENT_SUPPORT_SCHEMA_VERSION
from shipping_pipeline.llm_statement_revision import STATEMENT_REVISION_SCHEMA_VERSION
from shipping_pipeline.llm_evidence_claim_support import EVIDENCE_CLAIM_SUPPORT_SCHEMA_VERSION
from shipping_pipeline.llm_evidence_correction import EVIDENCE_CORRECTION_SCHEMA_VERSION
from shipping_pipeline.llm_statement_role import STATEMENT_ROLE_SCHEMA_VERSION
from shipping_pipeline.llm_semantic_adjudication import (
    EVIDENCE_REVISION_SCHEMA_VERSION,
    SEMANTIC_DECISION_HEADERS,
)
from shipping_pipeline.llm_semantic_auto_resolution import (
    SEMANTIC_AUTO_RESOLUTION_SCHEMA_VERSION,
    apply_auto_resolution_plan,
    build_auto_resolution_context,
)
from shipping_pipeline.llm_projection import project_cards
from shipping_pipeline.llm_quotes import build_quote_candidates
from shipping_pipeline.llm_statement_support import build_statement_records


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".test_tmp"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)


def make_workspace() -> Path:
    path = TEST_TMP_ROOT / f"llm_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def make_material(index: int, *, generation_id: str = "generation-one", extract_size: int = 80) -> dict:
    extract = f"材料{index}采用仿真方法分析船舶积压。" + ("研究数据" * extract_size)
    return {
        "schema_version": "material.v2",
        "material_id": f"paper-one:card-{index:03d}",
        "material_type": "evidence_card",
        "paper_id": "paper-one",
        "paper_title": "论文一",
        "source_path": "normalized/document.md",
        "clean_title": f"第{index}节",
        "raw_title": f"第{index}节",
        "order": index,
        "extract": extract,
        "source_span": {"path": "normalized/document.md", "start_line": index * 10, "end_line": index * 10 + 1},
        "source_spans": [{"path": "normalized/document.md", "start_line": index * 10, "end_line": index * 10 + 1}],
        "heading_path": [f"第{index}节"],
        "content_kind": "prose",
        "confidence_flags": [],
        "quality_flags": [],
        "source_fingerprint": f"sha256:fingerprint-{index}",
        "generation_id": generation_id,
    }


def write_corpus(workspace: Path, materials: list[dict], *, generation_id: str = "generation-one") -> None:
    corpus = workspace / "_corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "materials.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in materials),
        encoding="utf-8",
    )
    (corpus / "papers.jsonl").write_text(
        json.dumps(
            {
                "paper_id": "paper-one",
                "status": "completed",
                "structure_quality": "silver",
                "source": "paper-one/normalized/document.md",
                "generation_id": generation_id,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    structure_dir = workspace / "paper-one" / "structure"
    structure_dir.mkdir(parents=True, exist_ok=True)
    (structure_dir / "structure.json").write_text(
        json.dumps(
            {
                "paper_id": "paper-one",
                "selected_segment": {"start_line": 1, "end_line": 50},
                "headings": [
                    {"title_norm": "第1章 第一部分", "depth": 1, "start_line": 1, "ordinal_path": [1]},
                    {"title_norm": "第2章 第二部分", "depth": 1, "start_line": 15, "ordinal_path": [2]},
                    {"title_norm": "第3章 第三部分", "depth": 1, "start_line": 25, "ordinal_path": [3]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_small_batch_profile(workspace: Path) -> Path:
    source = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "models"
        / "deepseek-v4-pro-official.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload.update(
        {
            "profile_id": "test-small-evidence-batches",
            "evidence_output_base_tokens": 1024,
            "evidence_output_tokens_per_card": 1024,
            "evidence_max_output_tokens": 2048,
        }
    )
    path = workspace / "test-model-profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_hierarchical_synthesis_profile(workspace: Path) -> Path:
    source = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "models"
        / "deepseek-v4-pro-official.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload.update(
        {
            "profile_id": "test-hierarchical-synthesis",
            "context_window_tokens": 1000,
            "model_max_output_tokens": 500,
            "safety_margin_tokens": 100,
            "evidence_output_base_tokens": 100,
            "evidence_output_tokens_per_card": 10,
            "evidence_max_output_tokens": 500,
            "section_summary_max_output_tokens": 200,
            "paper_synthesis_max_output_tokens": 200,
            "evidence_claim_output_base_tokens": 50,
            "evidence_claim_output_tokens_per_unit": 25,
            "evidence_claim_support_max_output_tokens": 200,
            "evidence_revision_max_output_tokens": 200,
            "statement_revision_max_output_tokens": 200,
            "statement_role_max_output_tokens": 200,
            "statement_support_max_output_tokens": 200,
        }
    )
    path = workspace / "hierarchical-model-profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class StageAwareTokenCounter:
    def __init__(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        self.asset_dir = (
            project_root
            / ".model_cache"
            / "deepseek-v4-pro"
            / "0e1a0e5e52aea73055f50fef6f2423db370265b6"
        )

    def count_messages(self, messages: list[dict]) -> TokenCount:
        user_prompt = str(messages[-1]["content"])
        prompt_tokens = 900 if '"已验证证据单元"' in user_prompt else 100
        return TokenCount(prompt_tokens=prompt_tokens, encoded_prompt_sha256="sha256:test")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def clear_evidence_claim_cache(workspace: Path) -> None:
    shutil.rmtree(workspace / "_llm_analysis" / "evidence_claim_cache", ignore_errors=True)


def provider_result(
    payload: dict,
    *,
    model: str = "deepseek-v4-pro",
    prompt_tokens: int = 12,
    finish_reason: str = "stop",
    reasoning_content: str | None = None,
) -> ProviderResult:
    response = {
        "id": "response-1",
        "model": model,
        "system_fingerprint": "fingerprint-1",
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "content": json.dumps(payload, ensure_ascii=False),
                    "reasoning_content": reasoning_content,
                },
            }
        ],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 8, "total_tokens": prompt_tokens + 8},
    }
    raw = json.dumps(response, ensure_ascii=False).encode("utf-8")
    return ProviderResult(
        raw_body=raw,
        parsed_response=response,
        response_id="response-1",
        response_model=model,
        system_fingerprint="fingerprint-1",
        finish_reason=finish_reason,
        reasoning_content_chars=len(reasoning_content or ""),
        usage={"prompt_tokens": prompt_tokens, "completion_tokens": 8, "total_tokens": prompt_tokens + 8},
    )


class ScriptedProvider:
    provider = "openai-compatible"
    model = "deepseek-v4-pro"

    def __init__(
        self,
        *,
        fail_evidence_call: int | None = None,
        no_evidence: bool = False,
        prompt_token_delta: int = 0,
        fail_synthesis: bool = False,
        fail_evidence_disposition: bool = False,
        fail_evidence_claim_support: bool = False,
        disagree_evidence_claim_support: bool = False,
        fail_statement_role: bool = False,
        fail_statement_support: bool = False,
        invalid_statement_support_coverage: bool = False,
    ) -> None:
        self.fail_evidence_call = fail_evidence_call
        self.no_evidence = no_evidence
        self.prompt_token_delta = prompt_token_delta
        self.fail_synthesis = fail_synthesis
        self.fail_evidence_disposition = fail_evidence_disposition
        self.fail_evidence_claim_support = fail_evidence_claim_support
        self.disagree_evidence_claim_support = disagree_evidence_claim_support
        self.fail_statement_role = fail_statement_role
        self.fail_statement_support = fail_statement_support
        self.invalid_statement_support_coverage = invalid_statement_support_coverage
        self.calls: list[tuple[str, str]] = []

    def complete(self, task: str, request_payload: dict, context: dict) -> ProviderResult:
        self.calls.append((task, str(context.get("batch_id", "synthesis"))))
        if task == "evidence_revision":
            payload = {
                "schema_version": EVIDENCE_REVISION_SCHEMA_VERSION,
                "revisions": [
                    {
                        "evidence_unit_id": row["evidence_unit_id"],
                        "action": "replace",
                        "replacement": {
                            "claim": str(row["citations"][0]["quote"]),
                            "caveats": ["观点已收窄到当前逐字引文。"],
                        },
                    }
                    for row in context["target_evidence_units"]
                ],
            }
            return provider_result(payload, prompt_tokens=context["planned_input_tokens"])
        if task in {"evidence_claim_support", "evidence_claim_confirmation"}:
            payload = {
                "schema_version": EVIDENCE_CLAIM_SUPPORT_SCHEMA_VERSION,
                "evidence_claim_reviews": [
                    {
                        "evidence_unit_id": row["evidence_unit_id"],
                        "verdict": "directly_supported",
                        "reason": "逐字引文直接支持证据观点。",
                        "unsupported_fragments": [],
                    }
                    for row in context["evidence_units"]
                ],
            }
            if self.fail_evidence_claim_support or (
                self.disagree_evidence_claim_support
                and task == "evidence_claim_support"
            ):
                payload["evidence_claim_reviews"][0].update(
                    {
                        "verdict": "partially_supported",
                        "reason": "证据观点增加了引文未表达的解释。",
                        "unsupported_fragments": ["未支持的解释"],
                    }
                )
            return provider_result(payload, prompt_tokens=context["planned_input_tokens"])

        if task == "evidence":
            evidence_call = sum(call[0] == "evidence" for call in self.calls)
            materials = context["materials"]
            quote_candidate_by_material = {
                row["material_id"]: row for row in reversed(context["quote_candidates"])
            }
            material_results = []
            for row in materials:
                disposition = "context" if self.no_evidence else "evidence"
                units = []
                if not self.no_evidence:
                    quote_candidate = quote_candidate_by_material[row["material_id"]]
                    units.append(
                        {
                            "claim": f"{row['clean_title']}包含可用研究证据。",
                            "evidence_type": "method",
                            "citations": [
                                {
                                    "quote_id": quote_candidate["quote_id"],
                                }
                            ],
                            "relevance": "可用于综述的方法比较。",
                            "confidence": "high",
                            "caveats": [],
                        }
                    )
                material_results.append(
                    {
                        "material_id": row["material_id"],
                        "disposition": disposition,
                        "reason_code": "context_only" if self.no_evidence else "direct_evidence",
                        "evidence_units": units,
                    }
                )
            payload = {
                "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
                "material_results": material_results,
            }
            if evidence_call == self.fail_evidence_call:
                payload["material_results"][0]["evidence_units"][0]["confidence"] = "certain"
            return provider_result(
                payload,
                prompt_tokens=context["planned_input_tokens"] + self.prompt_token_delta,
            )

        if task == "section_summary":
            evidence_ids = list(context["evidence_unit_ids"])
            payload = {
                "schema_version": SECTION_SUMMARY_SCHEMA_VERSION,
                "section_id": context["section_id"],
                "section_focus": "章节证据摘要。",
                "claims": [{"statement": "章节包含可用证据。", "evidence_unit_ids": evidence_ids[:1]}],
                "evidence_dispositions": [
                    {
                        "evidence_unit_id": evidence_id,
                        "disposition": "used" if index == 0 else "redundant",
                        "reason_code": "supports_claim" if index == 0 else "duplicate_support",
                    }
                    for index, evidence_id in enumerate(evidence_ids)
                ],
            }
            return provider_result(payload, prompt_tokens=context["planned_input_tokens"])

        if task == "evidence_disposition":
            evidence_ids = list(context["non_used_evidence_unit_ids"])
            payload = {
                "schema_version": NON_USED_EVIDENCE_DISPOSITION_SCHEMA_VERSION,
                "non_used_evidence_dispositions": [
                    {
                        "evidence_unit_id": evidence_id,
                        "disposition": "peripheral",
                        "reason_code": "background_only",
                    }
                    for evidence_id in evidence_ids
                ],
            }
            if self.fail_evidence_disposition:
                payload["non_used_evidence_dispositions"][0]["disposition"] = "used"
            return provider_result(payload, prompt_tokens=context["planned_input_tokens"])

        if task == "statement_revision":
            replacements_by_field = {
                "research_focus": "研究船舶积压。",
                "methods": "采用模型分析。",
                "core_findings": "船舶发生积压。",
                "limitations": "研究存在明确限制。",
                "review_uses": "可用于综述中的方法比较。",
                "unresolved_questions": "论文提出后续研究问题。",
            }
            payload = {
                "schema_version": STATEMENT_REVISION_SCHEMA_VERSION,
                "revisions": [
                    {
                        "statement_id": row["statement_id"],
                        "action": "replace",
                        "replacements": [
                            {
                                "statement": replacements_by_field[row["field"]],
                                "evidence_unit_ids": list(row["evidence_unit_ids"]),
                            }
                        ],
                    }
                    for row in context["blocking_records"]
                ],
                "released_evidence_dispositions": [],
            }
            return provider_result(payload, prompt_tokens=context["planned_input_tokens"])

        if task == "statement_support":
            payload = {
                "schema_version": STATEMENT_SUPPORT_SCHEMA_VERSION,
                "statement_reviews": [
                    {
                        "statement_id": row["statement_id"],
                        "verdict": (
                            "grounded_inference"
                            if row["required_support"] == "grounded_inference_allowed"
                            else "directly_supported"
                        ),
                        "reason": "所列证据支持该陈述。",
                        "unsupported_fragments": [],
                    }
                    for row in context["statements"]
                ],
            }
            if self.fail_statement_support:
                payload["statement_reviews"][0].update(
                    {
                        "verdict": "partially_supported",
                        "reason": "陈述包含证据未支持的内容。",
                        "unsupported_fragments": ["未支持的内容"],
                    }
                )
            if self.invalid_statement_support_coverage:
                payload["statement_reviews"].pop()
            return provider_result(payload, prompt_tokens=context["planned_input_tokens"])

        if task == "statement_role":
            payload = {
                "schema_version": STATEMENT_ROLE_SCHEMA_VERSION,
                "statement_role_reviews": [
                    {
                        "statement_id": row["statement_id"],
                        "verdict": "field_aligned",
                        "reason": "陈述履行当前字段职责。",
                        "misaligned_fragments": [],
                    }
                    for row in context["statements"]
                ],
            }
            if self.fail_statement_role:
                payload["statement_role_reviews"][0].update(
                    {
                        "verdict": "misclassified",
                        "reason": "陈述不履行当前字段职责。",
                        "misaligned_fragments": ["字段错位内容"],
                    }
                )
            return provider_result(payload, prompt_tokens=context["planned_input_tokens"])

        evidence_ids = list(context["evidence_unit_ids"])
        payload = {
            "schema_version": PAPER_ANALYSIS_DRAFT_SCHEMA_VERSION,
            "research_focus": {
                "statement": "研究船舶积压及其疏导方法。",
                "evidence_unit_ids": evidence_ids[:1],
            },
            "study_type": "simulation",
            "methods": [
                {"statement": "仿真分析", "evidence_unit_ids": evidence_ids[:1]}
            ],
            "core_findings": [
                {"statement": "论文提供了船舶积压分析证据。", "evidence_unit_ids": evidence_ids[:1]}
            ],
            "limitations": [],
            "review_uses": [
                {"statement": "可用于方法比较。", "evidence_unit_ids": evidence_ids[:1]}
            ],
            "unresolved_questions": [],
        }
        if self.fail_synthesis:
            payload["core_findings"][0]["evidence_unit_ids"] = ["evidence_typo"]
        return provider_result(payload, prompt_tokens=context["planned_input_tokens"])


class EvidenceCorrectionProvider(ScriptedProvider):
    def __init__(self, *, fail_correction: bool = False) -> None:
        super().__init__()
        self.fail_correction = fail_correction

    def complete(self, task: str, request_payload: dict, context: dict) -> ProviderResult:
        if task == "evidence":
            self.calls.append((task, str(context["batch_id"])))
            material = context["materials"][0]
            quotes = context["quote_candidates"]
            wrong_quote = next(row for row in quotes if "3000" in row["text"])
            payload = {
                "schema_version": EVIDENCE_BATCH_SCHEMA_VERSION,
                "material_results": [
                    {
                        "material_id": material["material_id"],
                        "disposition": "evidence",
                        "reason_code": "direct_evidence",
                        "evidence_units": [
                            {
                                "claim": "该方案运量为5000吨。",
                                "evidence_type": "result",
                                "citations": [{"quote_id": wrong_quote["quote_id"]}],
                                "relevance": "可用于比较方案运量。",
                                "confidence": "high",
                                "caveats": [],
                            }
                        ],
                    }
                ],
            }
            return provider_result(payload, prompt_tokens=context["planned_input_tokens"])
        if task == "evidence_batch_correction":
            self.calls.append((task, str(context["batch_id"])))
            quotes = context["quote_candidates"]
            selected = next(
                row
                for row in quotes
                if ("3000" if self.fail_correction else "5000") in row["text"]
            )
            payload = {
                "schema_version": EVIDENCE_CORRECTION_SCHEMA_VERSION,
                "material_corrections": [
                    {
                        "material_id": context["target_material_ids"][0],
                        "unit_corrections": [
                            {
                                "source_evidence_index": 0,
                                "action": "replace",
                                "replacements": [
                                    {
                                        "claim": "该方案运量为5000吨。",
                                        "citations": [{"quote_id": selected["quote_id"]}],
                                    }
                                ],
                                "reason": "重新绑定支持5000吨的同 Card 逐字引文。",
                            }
                        ],
                    }
                ],
            }
            return provider_result(payload, prompt_tokens=context["planned_input_tokens"])
        return super().complete(task, request_payload, context)


class GenerationChangingProvider(ScriptedProvider):
    def __init__(self, workspace: Path) -> None:
        super().__init__()
        self.workspace = workspace
        self.changed = False

    def complete(self, task: str, request_payload: dict, context: dict) -> ProviderResult:
        result = super().complete(task, request_payload, context)
        if task == "synthesis" and not self.changed:
            paper_path = self.workspace / "_corpus" / "papers.jsonl"
            paper = json.loads(paper_path.read_text(encoding="utf-8").strip())
            paper["generation_id"] = "generation-two"
            paper_path.write_text(json.dumps(paper, ensure_ascii=False) + "\n", encoding="utf-8")
            self.changed = True
        return result


class AdjudicationRevisionProvider(ScriptedProvider):
    def complete(self, task: str, request_payload: dict, context: dict) -> ProviderResult:
        result = super().complete(task, request_payload, context)
        if task != "synthesis":
            return result
        user_prompt = str(request_payload["messages"][-1]["content"])
        if "人工确认的字段职责缺陷" not in user_prompt:
            return result
        payload = json.loads(result.parsed_response["choices"][0]["message"]["content"])
        payload["research_focus"]["statement"] = "研究船舶积压问题及其疏导方法。"
        return provider_result(payload, prompt_tokens=context["planned_input_tokens"])


class AdjudicationOverrideProvider(ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(
            fail_evidence_claim_support=True,
            fail_statement_role=True,
        )

    def complete(self, task: str, request_payload: dict, context: dict) -> ProviderResult:
        result = super().complete(task, request_payload, context)
        if task != "synthesis":
            return result
        payload = json.loads(result.parsed_response["choices"][0]["message"]["content"])
        payload["research_focus"]["statement"] = "研究船舶积压。"
        return provider_result(payload, prompt_tokens=context["planned_input_tokens"])


class AdjudicationReproducedDefectProvider(ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(fail_evidence_claim_support=True)

    def complete(self, task: str, request_payload: dict, context: dict) -> ProviderResult:
        result = super().complete(task, request_payload, context)
        if task != "synthesis":
            return result
        payload = json.loads(result.parsed_response["choices"][0]["message"]["content"])
        payload["research_focus"]["statement"] = "研究船舶积压。"
        return provider_result(payload, prompt_tokens=context["planned_input_tokens"])


class HumanRequiredAutoResolutionProvider(ScriptedProvider):
    def complete(self, task: str, request_payload: dict, context: dict) -> ProviderResult:
        if task != "semantic_auto_resolution":
            return super().complete(task, request_payload, context)
        self.calls.append((task, "synthesis"))
        auto_context = context["auto_resolution_context"]
        payload = {
            "schema_version": SEMANTIC_AUTO_RESOLUTION_SCHEMA_VERSION,
            "evidence_resolutions": [
                {
                    "target_evidence_unit_id": row["target_evidence_unit_id"],
                    "action": "human_required",
                    "selected_quote_ids": [],
                    "replacement_evidence_unit_id": None,
                    "reason": "候选信息不足，保留给人工判断。",
                }
                for row in auto_context["evidence_issues"]
            ],
            "statement_resolutions": [
                {
                    "target_statement_id": row["target_statement_id"],
                    "action": "human_required",
                    "canonical_statement_id": None,
                    "reason": "没有可确认的正确字段副本。",
                }
                for row in auto_context["statement_issues"]
            ],
        }
        return provider_result(payload, prompt_tokens=context["planned_input_tokens"])


class OverrideAutoResolutionProvider(ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(fail_evidence_claim_support=True)

    def complete(self, task: str, request_payload: dict, context: dict) -> ProviderResult:
        if task != "semantic_auto_resolution":
            return super().complete(task, request_payload, context)
        self.calls.append((task, "synthesis"))
        auto_context = context["auto_resolution_context"]
        payload = {
            "schema_version": SEMANTIC_AUTO_RESOLUTION_SCHEMA_VERSION,
            "evidence_resolutions": [
                {
                    "target_evidence_unit_id": row["target_evidence_unit_id"],
                    "action": "keep_gate_override",
                    "selected_quote_ids": [],
                    "replacement_evidence_unit_id": None,
                    "reason": "阻断片段不涉及数字、公式、因果或范围扩张，属于低风险等值表达。",
                }
                for row in auto_context["evidence_issues"]
            ],
            "statement_resolutions": [],
        }
        return provider_result(payload, prompt_tokens=context["planned_input_tokens"])


def decide_semantic_issues(path: Path, decisions: dict[str, tuple[str, str]]) -> None:
    import csv

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        issue_type = "evidence_claim" if row["问题类型"] == "证据观点" else "statement_role"
        decision, note = decisions[issue_type]
        row["人工裁决"] = decision
        row["人工说明"] = note
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SEMANTIC_DECISION_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def create_blocked_semantic_audit(workspace: Path, *, prefix: str) -> Path:
    original = LLMAnalysisRunner(
        workspace,
        analysis_client=ScriptedProvider(fail_statement_support=True),
    ).run(
        run_id=f"{prefix}-analysis",
        topic="船舶积压",
        paper_id="paper-one",
        provider="openai-compatible",
    )
    if original["failure_codes"] != ["support.statement_not_fully_supported"]:
        raise AssertionError(original)
    revision = LLMStatementRevisionRunner(
        workspace,
        analysis_client=ScriptedProvider(fail_statement_support=True),
    ).run(
        source_run_id=f"{prefix}-analysis",
        run_id=f"{prefix}-revision",
        provider="openai-compatible",
    )
    if revision["failure_codes"] != ["support.statement_not_fully_supported"]:
        raise AssertionError(revision)
    clear_evidence_claim_cache(workspace)
    audit = LLMSemanticAuditRunner(
        workspace,
        analysis_client=ScriptedProvider(
            fail_evidence_claim_support=True,
            fail_statement_role=True,
        ),
        token_counter=StageAwareTokenCounter(),
    ).run(
        source_run_id=f"{prefix}-revision",
        run_id=f"{prefix}-audit",
        provider="openai-compatible",
    )
    if audit["failure_codes"] != [
        "support.evidence_claim_not_fully_supported",
        "role.statement_misclassified",
    ]:
        raise AssertionError(audit)
    return workspace / "_llm_analysis" / "semantic_audits" / f"{prefix}-audit"


class LLMAnalysisTests(unittest.TestCase):
    def test_chat_request_explicitly_disables_thinking_and_sets_output_budget(self) -> None:
        request = build_chat_request(
            model="deepseek-ai/DeepSeek-V4-Pro",
            system_prompt="system",
            user_prompt="user",
            max_tokens=8192,
            thinking={"type": "disabled"},
        )

        self.assertEqual(request["thinking"], {"type": "disabled"})
        self.assertEqual(request["max_tokens"], 8192)
        self.assertNotIn("temperature", request)

    def test_completed_response_requires_stop_single_choice_and_no_reasoning(self) -> None:
        base = provider_result({"ok": True})
        validate_completed_response(base, thinking_disabled=True)

        for name, response, finish_reason, reasoning_chars, expected in (
            ("length", base.parsed_response, "length", 0, "finish_reason.length"),
            ("content_filter", base.parsed_response, "content_filter", 0, "finish_reason.content_filter"),
            ("missing", base.parsed_response, None, 0, "finish_reason.missing"),
            ("reasoning", base.parsed_response, "stop", 10, "non_think_returned_reasoning"),
            (
                "multiple",
                {"choices": [{"finish_reason": "stop"}, {"finish_reason": "stop"}]},
                None,
                0,
                "choice_count.2",
            ),
        ):
            with self.subTest(name=name):
                result = ProviderResult(
                    raw_body=base.raw_body,
                    parsed_response=response,
                    response_id=base.response_id,
                    response_model=base.response_model,
                    system_fingerprint=base.system_fingerprint,
                    finish_reason=finish_reason,
                    reasoning_content_chars=reasoning_chars,
                    usage=base.usage,
                )
                with self.assertRaisesRegex(ProviderCallError, expected):
                    validate_completed_response(result, thinking_disabled=True)

        with self.assertRaisesRegex(ProviderCallError, "response_model_mismatch"):
            validate_completed_response(
                base,
                thinking_disabled=True,
                requested_model="different-model",
            )

        missing_usage = ProviderResult(
            raw_body=base.raw_body,
            parsed_response=base.parsed_response,
            response_id=base.response_id,
            response_model=base.response_model,
            system_fingerprint=base.system_fingerprint,
            finish_reason=base.finish_reason,
            reasoning_content_chars=base.reasoning_content_chars,
            usage={**base.usage, "completion_tokens": None},
        )
        with self.assertRaisesRegex(ProviderCallError, "usage_invalid.completion_tokens"):
            validate_completed_response(missing_usage, thinking_disabled=True)

        bad_total = ProviderResult(
            raw_body=base.raw_body,
            parsed_response=base.parsed_response,
            response_id=base.response_id,
            response_model=base.response_model,
            system_fingerprint=base.system_fingerprint,
            finish_reason=base.finish_reason,
            reasoning_content_chars=base.reasoning_content_chars,
            usage={**base.usage, "total_tokens": 999},
        )
        with self.assertRaisesRegex(ProviderCallError, "usage_total_mismatch"):
            validate_completed_response(bad_total, thinking_disabled=True)

    def test_snapshot_and_projected_prompt_are_generation_bound_and_compact(self) -> None:
        workspace = make_workspace()
        materials = [make_material(index) for index in range(1, 4)]
        write_corpus(workspace, materials)
        snapshot = create_input_snapshot(workspace, paper_id="paper-one", topic="船舶积压")
        system_prompt, prompt = build_evidence_prompts(snapshot, list(snapshot.materials))

        self.assertNotIn("source_span", prompt)
        self.assertNotIn("source_fingerprint", prompt)
        self.assertNotIn("generation_id", prompt)
        self.assertIn("material_id", prompt)
        self.assertIn("不得输出 quote 文本", system_prompt)
        self.assertIn("不得自行生成 quote_id", system_prompt)
        self.assertIn("证据单元必须嵌套在当前 Card 结果", system_prompt)
        self.assertIn("引文候选", prompt)
        self.assertIn("每一个阿拉伯数字", system_prompt)
        self.assertIn("未选中的引文候选不是 citation", system_prompt)
        self.assertIn("不得从标题取得年份", system_prompt)
        self.assertIn("不得把原文中的连续数字自行解释为科学计数法", system_prompt)
        self.assertIn("最多8条 citation", system_prompt)
        self.assertIn("至少一条、最多12条", system_prompt)
        self.assertIn("必须保留引文中的关键条件和模态词", system_prompt)
        prompt_payload = json.loads(prompt)
        self.assertNotIn("引文候选", prompt_payload)
        self.assertTrue(all(card["引文候选"] for card in prompt_payload["材料卡"]))
        self.assertTrue(
            all(
                set(candidate) == {"quote_id", "text"}
                for card in prompt_payload["材料卡"]
                for candidate in card["引文候选"]
            )
        )

        materials[1]["generation_id"] = "different-generation"
        write_corpus(workspace, materials)
        with self.assertRaisesRegex(AnalysisInputError, "generation"):
            create_input_snapshot(workspace, paper_id="paper-one", topic="船舶积压")

    def test_dry_run_writes_snapshot_batches_and_full_coverage_without_provider_calls(self) -> None:
        workspace = make_workspace()
        materials = [make_material(index) for index in range(1, 4)]
        write_corpus(workspace, materials)

        result = LLMAnalysisRunner(workspace).run(
            run_id="dry-run",
            topic="船舶积压",
            paper_id="paper-one",
            dry_run=True,
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "dry-run"
        self.assertEqual(result["status"], "dry_run_completed")
        self.assertEqual(read_json(run_dir / "coverage.json")["planned_material_count"], 3)
        self.assertEqual(
            len(read_json(run_dir / "plan" / "analysis_plan.json")["evidence_batches"]),
            result["batch_count"],
        )
        self.assertTrue((run_dir / "input" / "projected_cards.jsonl").exists())
        self.assertTrue((run_dir / "input" / "quote_candidates.jsonl").exists())
        self.assertTrue((run_dir / "plan" / "model_profile.json").exists())
        self.assertTrue((run_dir / "plan" / "tokenizer_manifest.json").exists())
        self.assertFalse((run_dir / "batches").exists())

    def test_provider_must_match_model_profile_before_planning(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1)])

        result = LLMAnalysisRunner(workspace).run(
            run_id="provider-mismatch",
            topic="船舶积压",
            paper_id="paper-one",
            provider="mock",
            dry_run=True,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["batch_count"], 0)
        failure = read_jsonl(
            workspace / "_llm_analysis" / "runs" / "provider-mismatch" / "audit" / "failures.jsonl"
        )[0]
        self.assertIn("provider 与 model profile 不一致", failure["error_message"])

    def test_complete_run_archives_raw_responses_outputs_manifest_and_review(self) -> None:
        workspace = make_workspace()
        materials = [make_material(index) for index in range(1, 4)]
        write_corpus(workspace, materials)
        provider = ScriptedProvider()

        result = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="complete-run",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "complete-run"
        manifest = read_json(run_dir / "manifest.json")
        coverage = read_json(run_dir / "coverage.json")
        paper_analysis = read_json(run_dir / "output" / "paper_analysis.json")
        evidence = read_jsonl(run_dir / "output" / "evidence_units.jsonl")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(coverage["material_coverage_ratio"], 1.0)
        self.assertEqual(len(evidence), 3)
        self.assertEqual(paper_analysis["paper_disposition"], "analyzed")
        self.assertGreater(manifest["response_usage"]["total_tokens"], 20)
        self.assertTrue(manifest["prompt_sha256"])
        plan = read_json(run_dir / "plan" / "analysis_plan.json")
        request_id = plan["evidence_batches"][0]["request_id"]
        request_dir = run_dir / "batches" / request_id
        self.assertTrue((request_dir / "raw_response.json").exists())
        request_result = read_json(request_dir / "result.json")
        self.assertEqual(request_result["prompt_token_delta"], 0)
        self.assertEqual(request_result["planned_max_output_tokens"], 5120)
        self.assertIn("# 论文 LLM 分析人工审核", (run_dir / "review" / "review.md").read_text(encoding="utf-8"))
        self.assertTrue((run_dir / "review" / "decisions.csv").exists())

    def test_evidence_failure_is_corrected_once_without_reextracting_the_card(self) -> None:
        workspace = make_workspace()
        material = make_material(1)
        material["extract"] = "该方案运量为5000吨。\n对照方案运量为3000吨。"
        write_corpus(workspace, [material])
        provider = EvidenceCorrectionProvider()

        result = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="evidence-correction-success",
            topic="运输方案运量",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "evidence-correction-success"
        source_request_id = read_json(run_dir / "plan" / "analysis_plan.json")["evidence_batches"][0][
            "request_id"
        ]
        source_dir = run_dir / "batches" / source_request_id
        source_result = read_json(source_dir / "result.json")
        resolution = read_json(source_dir / "resolution.json")
        correction_dir = run_dir / "batches" / resolution["correction_request_id"]
        manifest = read_json(run_dir / "manifest.json")
        coverage = read_json(run_dir / "coverage.json")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(source_result["status"], "failed")
        self.assertEqual(resolution["status"], "corrected")
        self.assertEqual(read_json(correction_dir / "result.json")["status"], "completed")
        self.assertTrue((correction_dir / "raw_response.json").exists())
        self.assertTrue((correction_dir / "applied_correction.json").exists())
        self.assertEqual(
            [task for task, _ in provider.calls].count("evidence"),
            1,
        )
        self.assertEqual(
            [task for task, _ in provider.calls].count("evidence_batch_correction"),
            1,
        )
        self.assertEqual(coverage["failed_batch_ids"], [])
        self.assertEqual(coverage["corrected_batch_ids"], [source_request_id])
        source_manifest_result = next(
            row for row in manifest["batches"] if row["request_id"] == source_request_id
        )
        self.assertEqual(
            source_manifest_result["resolved_by_request_id"],
            resolution["correction_request_id"],
        )
        correction_audit = read_jsonl(run_dir / "audit" / "evidence_corrections.jsonl")
        self.assertEqual(len(correction_audit), 1)
        self.assertEqual(correction_audit[0]["resolution_status"], "corrected")
        self.assertEqual(
            correction_audit[0]["correction_request_id"],
            resolution["correction_request_id"],
        )
        self.assertEqual(read_jsonl(run_dir / "audit" / "failures.jsonl"), [])

    def test_failed_evidence_correction_stops_after_one_pass(self) -> None:
        workspace = make_workspace()
        material = make_material(1)
        material["extract"] = "该方案运量为5000吨。\n对照方案运量为3000吨。"
        write_corpus(workspace, [material])
        provider = EvidenceCorrectionProvider(fail_correction=True)

        result = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="evidence-correction-failed",
            topic="运输方案运量",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "evidence-correction-failed"
        source_request_id = read_json(run_dir / "plan" / "analysis_plan.json")["evidence_batches"][0][
            "request_id"
        ]
        resolution = read_json(run_dir / "batches" / source_request_id / "resolution.json")
        failures = read_jsonl(run_dir / "audit" / "failures.jsonl")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(resolution["status"], "correction_failed")
        self.assertEqual(
            [task for task, _ in provider.calls],
            ["evidence", "evidence_batch_correction"],
        )
        self.assertEqual(failures[0]["stage"], "evidence_batch_correction")
        self.assertEqual(
            failures[0]["error_code"],
            "citation.evidence_claim_numeric_fact_unsupported",
        )
        self.assertFalse((run_dir / "output" / "paper_analysis.json").exists())

    def test_retry_reuses_the_complete_evidence_correction_chain(self) -> None:
        workspace = make_workspace()
        material = make_material(1)
        material["extract"] = "该方案运量为5000吨。\n对照方案运量为3000吨。"
        write_corpus(workspace, [material])
        parent_provider = EvidenceCorrectionProvider()
        parent_provider.fail_synthesis = True
        parent = LLMAnalysisRunner(workspace, analysis_client=parent_provider).run(
            run_id="corrected-evidence-retry-parent",
            topic="运输方案运量",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        parent_dir = workspace / "_llm_analysis" / "runs" / "corrected-evidence-retry-parent"
        failed_synthesis_id = read_jsonl(parent_dir / "audit" / "failures.jsonl")[0]["batch_id"]
        source_request_id = read_json(parent_dir / "plan" / "analysis_plan.json")["evidence_batches"][0][
            "request_id"
        ]
        correction_request_id = read_json(
            parent_dir / "batches" / source_request_id / "resolution.json"
        )["correction_request_id"]
        self.assertEqual(parent["status"], "failed")

        retry_provider = ScriptedProvider()
        child = LLMAnalysisRunner(workspace, analysis_client=retry_provider).run(
            run_id="corrected-evidence-retry-child",
            topic="运输方案运量",
            paper_id="paper-one",
            provider="openai-compatible",
            retry_from="corrected-evidence-retry-parent",
            retry_batch_id=failed_synthesis_id,
        )

        child_dir = workspace / "_llm_analysis" / "runs" / "corrected-evidence-retry-child"
        manifest = read_json(child_dir / "manifest.json")
        self.assertEqual(child["status"], "completed")
        self.assertFalse(any(task in {"evidence", "evidence_batch_correction"} for task, _ in retry_provider.calls))
        self.assertTrue((child_dir / "batches" / correction_request_id / "validated_output.json").exists())
        self.assertIn(source_request_id, manifest["reused_batch_ids"])
        self.assertIn(correction_request_id, manifest["reused_batch_ids"])

    def test_failed_middle_batch_is_recorded_other_batches_continue_and_synthesis_is_blocked(self) -> None:
        workspace = make_workspace()
        materials = [make_material(index, extract_size=400) for index in range(1, 4)]
        write_corpus(workspace, materials)
        profile_path = write_small_batch_profile(workspace)
        provider = ScriptedProvider(fail_evidence_call=2)

        result = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="partial-run",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
            model_profile_path=profile_path,
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "partial-run"
        failures = read_jsonl(run_dir / "audit" / "failures.jsonl")
        self.assertEqual(result["status"], "partial")
        evidence_calls = [call for call in provider.calls if call[0] == "evidence"]
        self.assertEqual(len(evidence_calls), 3)
        self.assertFalse(any(call[0] == "synthesis" for call in provider.calls))
        self.assertEqual(failures[0]["batch_id"], evidence_calls[1][1])
        self.assertIn("confidence", failures[0]["error_message"])
        self.assertFalse((run_dir / "output" / "paper_analysis.json").exists())
        self.assertTrue((run_dir / "batches" / evidence_calls[2][1] / "validated_output.json").exists())
        self.assertTrue((run_dir / "review" / "review.md").exists())
        self.assertIn("运行失败", (run_dir / "review" / "review.md").read_text(encoding="utf-8"))

    def test_prompt_token_mismatch_fails_the_batch_and_blocks_synthesis(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        provider = ScriptedProvider(prompt_token_delta=1)

        result = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="token-mismatch",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        failures = read_jsonl(workspace / "_llm_analysis" / "runs" / "token-mismatch" / "audit" / "failures.jsonl")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(failures[0]["error_code"], "provider.prompt_token_mismatch")
        self.assertFalse(any(call[0] == "synthesis" for call in provider.calls))

    def test_over_budget_direct_synthesis_runs_section_then_paper_synthesis(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2), make_material(3)])
        profile_path = write_hierarchical_synthesis_profile(workspace)
        provider = ScriptedProvider()

        result = LLMAnalysisRunner(
            workspace,
            analysis_client=provider,
            token_counter=StageAwareTokenCounter(),
        ).run(
            run_id="hierarchical-synthesis",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
            model_profile_path=profile_path,
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "hierarchical-synthesis"
        synthesis_plan = read_json(run_dir / "plan" / "synthesis_plan.json")
        coverage = read_json(run_dir / "coverage.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(synthesis_plan["strategy"], "section_then_paper_synthesis")
        self.assertEqual(
            [request["stage"] for request in synthesis_plan["requests"]],
            [
                "section_summary",
                "section_summary",
                "section_summary",
                "paper_synthesis",
                "evidence_disposition",
                "statement_role",
                "statement_support",
            ],
        )
        self.assertEqual(len(coverage["all_evidence_unit_ids"]), 3)
        self.assertEqual(coverage["unresolved_evidence_unit_ids"], [])
        self.assertEqual(
            set(coverage["all_evidence_unit_ids"]),
            set(coverage["used_in_final_analysis_ids"]) | set(coverage["not_used_in_final_analysis_ids"]),
        )

    def test_zero_evidence_is_completed_without_synthesis_or_generated_findings(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        provider = ScriptedProvider(no_evidence=True)

        result = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="no-evidence",
            topic="无关主题",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        paper_analysis = read_json(
            workspace / "_llm_analysis" / "runs" / "no-evidence" / "output" / "paper_analysis.json"
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(paper_analysis["paper_disposition"], "no_usable_evidence")
        self.assertIsNone(paper_analysis["analysis"])
        self.assertFalse(any(call[0] == "synthesis" for call in provider.calls))

    def test_explicit_retry_reuses_validated_batches_and_only_executes_failed_batch(self) -> None:
        workspace = make_workspace()
        materials = [make_material(index, extract_size=400) for index in range(1, 4)]
        write_corpus(workspace, materials)
        profile_path = write_small_batch_profile(workspace)
        failed_provider = ScriptedProvider(fail_evidence_call=2)
        LLMAnalysisRunner(workspace, analysis_client=failed_provider).run(
            run_id="retry-parent",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
            model_profile_path=profile_path,
        )
        parent_dir = workspace / "_llm_analysis" / "runs" / "retry-parent"
        failed_request_id = read_jsonl(parent_dir / "audit" / "failures.jsonl")[0]["batch_id"]
        planned_ids = [
            row["request_id"] for row in read_json(parent_dir / "plan" / "analysis_plan.json")["evidence_batches"]
        ]
        retry_provider = ScriptedProvider()

        result = LLMAnalysisRunner(workspace, analysis_client=retry_provider).run(
            run_id="retry-child",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
            model_profile_path=profile_path,
            retry_from="retry-parent",
            retry_batch_id=failed_request_id,
        )

        manifest = read_json(workspace / "_llm_analysis" / "runs" / "retry-child" / "manifest.json")
        synthesis_ids = [
            row["request_id"]
            for row in read_json(
                workspace / "_llm_analysis" / "runs" / "retry-child" / "plan" / "synthesis_plan.json"
            )["requests"]
        ]
        claim_ids = [
            row["request_id"]
            for row in read_json(
                workspace
                / "_llm_analysis"
                / "runs"
                / "retry-child"
                / "plan"
                / "evidence_claim_support_plan.json"
            )["requests"]
        ]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(manifest["reused_batch_ids"], [item for item in planned_ids if item != failed_request_id])
        self.assertEqual(
            manifest["executed_batch_ids"],
            [failed_request_id, *claim_ids, *synthesis_ids],
        )
        self.assertEqual(
            retry_provider.calls,
            [
                ("evidence", failed_request_id),
                *[("evidence_claim_support", "synthesis") for _ in claim_ids],
                ("synthesis", "synthesis"),
                ("evidence_disposition", "synthesis"),
                ("statement_role", "synthesis"),
                ("statement_support", "synthesis"),
            ],
        )

    def test_explicit_retry_can_reuse_all_evidence_and_only_rerun_direct_synthesis(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        failed_provider = ScriptedProvider(fail_synthesis=True)
        parent_result = LLMAnalysisRunner(workspace, analysis_client=failed_provider).run(
            run_id="synthesis-retry-parent",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        parent_dir = workspace / "_llm_analysis" / "runs" / "synthesis-retry-parent"
        failed_request_id = read_jsonl(parent_dir / "audit" / "failures.jsonl")[0]["batch_id"]
        planned_evidence_ids = [
            row["request_id"]
            for row in read_json(parent_dir / "plan" / "analysis_plan.json")["evidence_batches"]
        ]
        self.assertEqual(parent_result["status"], "failed")
        self.assertTrue(failed_request_id.startswith("paper_synthesis_"))
        self.assertTrue((parent_dir / "review" / "review.md").exists())

        retry_provider = ScriptedProvider()
        result = LLMAnalysisRunner(workspace, analysis_client=retry_provider).run(
            run_id="synthesis-retry-child",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
            retry_from="synthesis-retry-parent",
            retry_batch_id=failed_request_id,
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "synthesis-retry-child"
        manifest = read_json(run_dir / "manifest.json")
        synthesis_ids = [
            row["request_id"]
            for row in read_json(run_dir / "plan" / "synthesis_plan.json")["requests"]
        ]
        claim_id = read_json(run_dir / "plan" / "evidence_claim_support_plan.json")[
            "requests"
        ][0]["request_id"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(manifest["reused_batch_ids"], [*planned_evidence_ids, claim_id])
        self.assertEqual(manifest["executed_batch_ids"], synthesis_ids)
        self.assertEqual(
            retry_provider.calls,
            [
                ("synthesis", "synthesis"),
                ("evidence_disposition", "synthesis"),
                ("statement_role", "synthesis"),
                ("statement_support", "synthesis"),
            ],
        )

    def test_explicit_retry_of_disposition_reuses_validated_paper_draft(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        failed_provider = ScriptedProvider(fail_evidence_disposition=True)
        parent_result = LLMAnalysisRunner(workspace, analysis_client=failed_provider).run(
            run_id="disposition-retry-parent",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        parent_dir = workspace / "_llm_analysis" / "runs" / "disposition-retry-parent"
        failed_request_id = read_jsonl(parent_dir / "audit" / "failures.jsonl")[0]["batch_id"]
        parent_synthesis_plan = read_json(parent_dir / "plan" / "synthesis_plan.json")
        draft_request_id = parent_synthesis_plan["requests"][0]["request_id"]
        self.assertEqual(parent_result["status"], "failed")
        self.assertTrue(failed_request_id.startswith("evidence_disposition_"))

        retry_provider = ScriptedProvider()
        result = LLMAnalysisRunner(workspace, analysis_client=retry_provider).run(
            run_id="disposition-retry-child",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
            retry_from="disposition-retry-parent",
            retry_batch_id=failed_request_id,
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "disposition-retry-child"
        draft_result = read_json(run_dir / "batches" / draft_request_id / "result.json")
        synthesis_plan = read_json(run_dir / "plan" / "synthesis_plan.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            retry_provider.calls,
            [
                ("evidence_disposition", "synthesis"),
                ("statement_role", "synthesis"),
                ("statement_support", "synthesis"),
            ],
        )
        self.assertEqual(draft_result["reused_from_run_id"], "disposition-retry-parent")
        self.assertEqual(
            [request["stage"] for request in synthesis_plan["requests"]],
            ["paper_synthesis", "evidence_disposition", "statement_role", "statement_support"],
        )

    def test_statement_support_failure_blocks_publication_and_retry_reuses_prior_synthesis(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        failed_provider = ScriptedProvider(fail_statement_support=True)
        parent_result = LLMAnalysisRunner(workspace, analysis_client=failed_provider).run(
            run_id="support-retry-parent",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        parent_dir = workspace / "_llm_analysis" / "runs" / "support-retry-parent"
        failure = read_jsonl(parent_dir / "audit" / "failures.jsonl")[0]
        parent_plan = read_json(parent_dir / "plan" / "synthesis_plan.json")
        draft_request_id = parent_plan["requests"][0]["request_id"]
        disposition_request_id = parent_plan["requests"][1]["request_id"]
        self.assertEqual(parent_result["status"], "failed")
        self.assertEqual(failure["error_code"], "support.statement_not_fully_supported")
        self.assertFalse((parent_dir / "output" / "paper_analysis.json").exists())
        self.assertTrue((parent_dir / "batches" / failure["batch_id"] / "validated_output.json").exists())
        failure_review = (parent_dir / "review" / "review.md").read_text(encoding="utf-8")
        self.assertIn("## 陈述支持核验阻断详情", failure_review)
        self.assertIn("核验结论：部分支持", failure_review)
        self.assertIn("不受支持片段：未支持的内容", failure_review)

        retry_provider = ScriptedProvider()
        result = LLMAnalysisRunner(workspace, analysis_client=retry_provider).run(
            run_id="support-retry-child",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
            retry_from="support-retry-parent",
            retry_batch_id=failure["batch_id"],
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "support-retry-child"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(retry_provider.calls, [("statement_support", "synthesis")])
        self.assertEqual(
            read_json(run_dir / "batches" / draft_request_id / "result.json")["reused_from_run_id"],
            "support-retry-parent",
        )
        self.assertEqual(
            read_json(run_dir / "batches" / disposition_request_id / "result.json")
            ["reused_from_run_id"],
            "support-retry-parent",
        )

        restart_provider = ScriptedProvider()
        restart_result = LLMAnalysisRunner(workspace, analysis_client=restart_provider).run(
            run_id="support-upstream-restart-child",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
            retry_from="support-retry-parent",
            retry_batch_id=draft_request_id,
        )

        restart_dir = workspace / "_llm_analysis" / "runs" / "support-upstream-restart-child"
        restart_manifest = read_json(restart_dir / "manifest.json")
        self.assertEqual(restart_result["status"], "completed")
        self.assertEqual(
            restart_provider.calls,
            [
                ("synthesis", "synthesis"),
                ("evidence_disposition", "synthesis"),
                ("statement_role", "synthesis"),
                ("statement_support", "synthesis"),
            ],
        )
        self.assertEqual(restart_manifest["retry_mode"], "restart_from_completed_synthesis")
        self.assertEqual(restart_manifest["retry_target_stage"], "paper_synthesis")
        restarted_draft_id = read_json(restart_dir / "plan" / "synthesis_plan.json")["requests"][0][
            "request_id"
        ]
        self.assertNotIn(
            "reused_from_run_id",
            read_json(restart_dir / "batches" / restarted_draft_id / "result.json"),
        )

    def test_explicit_statement_revision_revalidates_and_only_then_publishes(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        parent_provider = ScriptedProvider(fail_statement_support=True)
        parent = LLMAnalysisRunner(workspace, analysis_client=parent_provider).run(
            run_id="revision-source",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        parent_manifest_path = workspace / "_llm_analysis" / "runs" / "revision-source" / "manifest.json"
        parent_manifest_before = parent_manifest_path.read_bytes()
        self.assertEqual(parent["status"], "failed")

        revision_provider = ScriptedProvider()
        result = LLMStatementRevisionRunner(
            workspace,
            analysis_client=revision_provider,
        ).run(
            source_run_id="revision-source",
            run_id="revision-child",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "revision-child"
        manifest = read_json(run_dir / "manifest.json")
        coverage = read_json(run_dir / "coverage.json")
        plan = read_json(run_dir / "plan" / "synthesis_plan.json")
        revision = read_json(run_dir / "output" / "revision.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            revision_provider.calls,
            [
                ("statement_revision", "synthesis"),
                ("statement_role", "synthesis"),
                ("statement_support", "synthesis"),
            ],
        )
        self.assertEqual(manifest["schema_version"], "llm.statement_revision_run.v2")
        self.assertEqual(manifest["source_run_id"], "revision-source")
        self.assertEqual(manifest["revision_attempt"], 1)
        self.assertEqual(coverage["source_blocking_statement_count"], 1)
        self.assertEqual(coverage["revision_count"], 1)
        self.assertEqual(len(revision["revisions"]), 1)
        self.assertEqual(
            [row["max_output_tokens"] for row in plan["requests"]],
            [8192, 16384, 16384],
        )
        self.assertTrue((run_dir / "output" / "paper_analysis.json").exists())
        self.assertTrue((run_dir / "review" / "review.md").exists())
        revision_report = (run_dir / "review" / "revision.md").read_text(encoding="utf-8")
        self.assertIn("# 论文分析陈述修订对照", revision_report)
        self.assertIn("**原陈述：**", revision_report)
        self.assertIn("**修订动作：** `replace`", revision_report)
        self.assertEqual(parent_manifest_path.read_bytes(), parent_manifest_before)

    def test_explicit_statement_revision_keeps_candidate_but_blocks_failed_reverification(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        LLMAnalysisRunner(
            workspace,
            analysis_client=ScriptedProvider(fail_statement_support=True),
        ).run(
            run_id="revision-failed-source",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        result = LLMStatementRevisionRunner(
            workspace,
            analysis_client=ScriptedProvider(fail_statement_support=True),
        ).run(
            source_run_id="revision-failed-source",
            run_id="revision-failed-child",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "revision-failed-child"
        report = (run_dir / "review" / "review.md").read_text(encoding="utf-8")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_codes"], ["support.statement_not_fully_supported"])
        self.assertTrue((run_dir / "output" / "revised_analysis_candidate.json").exists())
        self.assertFalse((run_dir / "output" / "paper_analysis.json").exists())
        self.assertIn("陈述支持核验阻断详情", report)
        self.assertIn(
            "候选修订不得作为正式论文分析发布",
            (run_dir / "review" / "revision.md").read_text(encoding="utf-8"),
        )

        second_provider = ScriptedProvider()
        second = LLMStatementRevisionRunner(
            workspace,
            analysis_client=second_provider,
        ).run(
            source_run_id="revision-failed-child",
            run_id="revision-second-attempt",
            provider="openai-compatible",
        )
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second_provider.calls, [])
        self.assertIn("input.invalid", second["failure_codes"])

    def test_statement_revision_support_schema_failure_allows_one_support_only_retry(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        LLMAnalysisRunner(
            workspace,
            analysis_client=ScriptedProvider(fail_statement_support=True),
        ).run(
            run_id="revision-support-retry-source",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        failed_provider = ScriptedProvider(invalid_statement_support_coverage=True)
        failed = LLMStatementRevisionRunner(
            workspace,
            analysis_client=failed_provider,
        ).run(
            source_run_id="revision-support-retry-source",
            run_id="revision-support-retry-parent",
            provider="openai-compatible",
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failure_codes"], ["schema.statement_support_invalid"])
        self.assertEqual(
            failed_provider.calls,
            [
                ("statement_revision", "synthesis"),
                ("statement_role", "synthesis"),
                ("statement_support", "synthesis"),
            ],
        )

        retry_provider = ScriptedProvider()
        retried = LLMStatementRevisionRunner(
            workspace,
            analysis_client=retry_provider,
        ).run(
            source_run_id="revision-support-retry-source",
            run_id="revision-support-retry-child",
            provider="openai-compatible",
            retry_support_from="revision-support-retry-parent",
        )

        child_dir = workspace / "_llm_analysis" / "runs" / "revision-support-retry-child"
        child_manifest = read_json(child_dir / "manifest.json")
        revision_request_id = next(
            row["request_id"]
            for row in read_json(child_dir / "plan" / "synthesis_plan.json")["requests"]
            if row["stage"] == "statement_revision"
        )
        role_request_id = next(
            row["request_id"]
            for row in read_json(child_dir / "plan" / "synthesis_plan.json")["requests"]
            if row["stage"] == "statement_role"
        )
        self.assertEqual(retried["status"], "completed")
        self.assertEqual(retry_provider.calls, [("statement_support", "synthesis")])
        self.assertEqual(child_manifest["retry_mode"], "statement_support_only")
        self.assertEqual(child_manifest["source_revision_run_id"], "revision-support-retry-parent")
        self.assertEqual(
            child_manifest["reused_batch_ids"],
            [revision_request_id, role_request_id],
        )
        self.assertEqual(len(child_manifest["executed_batch_ids"]), 1)
        self.assertEqual(
            read_json(child_dir / "batches" / revision_request_id / "result.json")
            ["reused_from_run_id"],
            "revision-support-retry-parent",
        )
        self.assertTrue((child_dir / "output" / "paper_analysis.json").exists())

        second_retry_provider = ScriptedProvider()
        second_retry = LLMStatementRevisionRunner(
            workspace,
            analysis_client=second_retry_provider,
        ).run(
            source_run_id="revision-support-retry-source",
            run_id="revision-support-retry-second-child",
            provider="openai-compatible",
            retry_support_from="revision-support-retry-child",
        )
        self.assertEqual(second_retry["status"], "failed")
        self.assertEqual(second_retry_provider.calls, [])
        self.assertIn("input.invalid", second_retry["failure_codes"])

    def test_evidence_claim_support_failure_blocks_synthesis_and_publication(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        provider = ScriptedProvider(fail_evidence_claim_support=True)

        result = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="evidence-claim-blocked",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "evidence-claim-blocked"
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["failure_codes"],
            ["support.evidence_claim_not_fully_supported"],
        )
        self.assertEqual(
            [task for task, _ in provider.calls],
            ["evidence", "evidence_claim_support", "evidence_claim_confirmation"],
        )
        self.assertTrue((run_dir / "output" / "evidence_claim_support_reviews.jsonl").exists())
        self.assertFalse((run_dir / "plan" / "synthesis_plan.json").exists())
        self.assertFalse((run_dir / "output" / "paper_analysis.json").exists())
        report = (run_dir / "review" / "review.md").read_text(encoding="utf-8")
        self.assertIn("Evidence claim 支持核验阻断详情", report)
        self.assertIn("证据观点增加了引文未表达的解释", report)

    def test_evidence_claim_gate_disagreement_is_not_converted_to_human_defect(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1)])
        provider = ScriptedProvider(disagree_evidence_claim_support=True)

        result = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="evidence-claim-unstable",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "evidence-claim-unstable"
        decisions = read_jsonl(run_dir / "output" / "evidence_claim_gate_decisions.jsonl")
        coverage = read_json(run_dir / "coverage.json")
        self.assertEqual(result["failure_codes"], ["support.evidence_claim_gate_unstable"])
        self.assertEqual([row["decision"] for row in decisions], ["gate_disagreement"])
        self.assertEqual(coverage["unstable_evidence_claim_count"], 1)
        self.assertEqual(coverage["confirmed_evidence_claim_blocker_count"], 0)
        self.assertFalse((run_dir / "review" / "semantic_decisions.csv").exists())

    def test_evidence_claim_content_hash_cache_prevents_identical_rejudgment(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        first_provider = ScriptedProvider()
        first = LLMAnalysisRunner(workspace, analysis_client=first_provider).run(
            run_id="evidence-cache-first",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        self.assertEqual(first["status"], "completed")

        second_provider = ScriptedProvider()
        second = LLMAnalysisRunner(workspace, analysis_client=second_provider).run(
            run_id="evidence-cache-second",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "evidence-cache-second"
        coverage = read_json(run_dir / "coverage.json")
        self.assertEqual(second["status"], "completed")
        self.assertNotIn(
            "evidence_claim_support",
            [task for task, _ in second_provider.calls],
        )
        self.assertEqual(coverage["evidence_claim_cache_hit_count"], 1)

    def test_corrupt_evidence_claim_cache_fails_without_provider_fallback(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1)])
        first = LLMAnalysisRunner(
            workspace,
            analysis_client=ScriptedProvider(),
        ).run(
            run_id="evidence-cache-valid",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        self.assertEqual(first["status"], "completed")
        cache_files = list(
            (workspace / "_llm_analysis" / "evidence_claim_cache").glob("*.json")
        )
        self.assertEqual(len(cache_files), 1)
        cache_record = read_json(cache_files[0])
        cache_record["identity"]["model"] = "tampered-model"
        cache_files[0].write_text(
            json.dumps(cache_record, ensure_ascii=False),
            encoding="utf-8",
        )

        provider = ScriptedProvider()
        second = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="evidence-cache-corrupt",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        self.assertEqual(second["failure_codes"], ["cache.evidence_claim_invalid"])
        self.assertEqual([task for task, _ in provider.calls], ["evidence"])

    def test_statement_role_failure_blocks_support_review_and_publication(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        provider = ScriptedProvider(fail_statement_role=True)

        result = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="statement-role-blocked",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "statement-role-blocked"
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_codes"], ["role.statement_misclassified"])
        self.assertIn("statement_role", [task for task, _ in provider.calls])
        self.assertNotIn("statement_support", [task for task, _ in provider.calls])
        self.assertTrue((run_dir / "output" / "statement_role_reviews.jsonl").exists())
        self.assertFalse((run_dir / "output" / "paper_analysis.json").exists())
        report = (run_dir / "review" / "review.md").read_text(encoding="utf-8")
        self.assertIn("字段职责核验阻断详情", report)
        self.assertIn("职责错位片段", report)
        self.assertTrue((run_dir / "output" / "paper_analysis_candidate.json").exists())
        self.assertTrue(
            (run_dir / "output" / "paper_analysis_candidate.provenance.json").exists()
        )

    def test_role_failed_analysis_can_directly_enter_auto_resolution(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        blocked = LLMAnalysisRunner(
            workspace,
            analysis_client=ScriptedProvider(fail_statement_role=True),
        ).run(
            run_id="direct-auto-source",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        self.assertEqual(blocked["failure_codes"], ["role.statement_misclassified"])

        result = LLMSemanticAutoResolutionRunner(
            workspace,
            analysis_client=HumanRequiredAutoResolutionProvider(),
            token_counter=StageAwareTokenCounter(),
        ).run(
            audit_run_id="direct-auto-source",
            run_id="direct-auto-result",
            provider="openai-compatible",
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("resolution.human_required", result["failure_codes"])
        self.assertNotIn("input.invalid", result["failure_codes"])

    def test_semantic_audit_runs_both_gates_and_never_publishes_candidate(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        original = LLMAnalysisRunner(
            workspace,
            analysis_client=ScriptedProvider(fail_statement_support=True),
        ).run(
            run_id="semantic-audit-source-analysis",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        self.assertEqual(original["failure_codes"], ["support.statement_not_fully_supported"])

        revision = LLMStatementRevisionRunner(
            workspace,
            analysis_client=ScriptedProvider(fail_statement_support=True),
        ).run(
            source_run_id="semantic-audit-source-analysis",
            run_id="semantic-audit-source-revision",
            provider="openai-compatible",
        )
        self.assertEqual(revision["failure_codes"], ["support.statement_not_fully_supported"])
        source_candidate_path = (
            workspace
            / "_llm_analysis"
            / "runs"
            / "semantic-audit-source-revision"
            / "output"
            / "revised_analysis_candidate.json"
        )
        source_candidate = read_json(source_candidate_path)

        audit_provider = ScriptedProvider(
            fail_evidence_claim_support=True,
            fail_statement_role=True,
        )
        clear_evidence_claim_cache(workspace)
        audited = LLMSemanticAuditRunner(
            workspace,
            analysis_client=audit_provider,
            token_counter=StageAwareTokenCounter(),
        ).run(
            source_run_id="semantic-audit-source-revision",
            run_id="semantic-audit-both-blocked",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "semantic_audits" / "semantic-audit-both-blocked"
        coverage = read_json(run_dir / "coverage.json")
        self.assertEqual(audit_provider.calls, [
            ("evidence_claim_support", "synthesis"),
            ("evidence_claim_confirmation", "synthesis"),
            ("statement_role", "synthesis"),
        ])
        self.assertEqual(audited["status"], "failed")
        self.assertEqual(
            audited["failure_codes"],
            [
                "support.evidence_claim_not_fully_supported",
                "role.statement_misclassified",
            ],
        )
        self.assertFalse(coverage["evidence_claim_gate_passed"])
        self.assertFalse(coverage["statement_role_gate_passed"])
        self.assertEqual(coverage["evidence_claim_review_count"], coverage["evidence_unit_count"])
        self.assertEqual(coverage["statement_role_review_count"], coverage["statement_count"])
        self.assertFalse((run_dir / "output" / "paper_analysis.json").exists())
        self.assertEqual(read_json(source_candidate_path), source_candidate)
        self.assertEqual(read_json(run_dir / "input" / "candidate_analysis.json"), source_candidate)
        report = (run_dir / "review" / "semantic_audit.md").read_text(encoding="utf-8")
        self.assertIn("Evidence claim 阻断项", report)
        self.assertIn("字段职责阻断项", report)
        self.assertIn("不生成正式 paper_analysis.json", report)
        self.assertTrue((run_dir / "review" / "semantic_decisions.csv").exists())
        workbook = (run_dir / "review" / "semantic_decision_workbook.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("人工语义裁决工作单", workbook)
        self.assertIn("Card 完整原文", workbook)

    def test_auto_resolution_applies_citation_repair_evidence_mapping_and_statement_deletion(self) -> None:
        materials = [make_material(index) for index in range(1, 4)]
        quote_candidates = build_quote_candidates(project_cards(materials))
        quote_by_material = {
            material["material_id"]: next(
                row
                for row in quote_candidates
                if row["material_id"] == material["material_id"]
            )
            for material in materials
        }
        raw_units = []
        for index, material in enumerate(materials, start=1):
            quote = quote_by_material[material["material_id"]]
            raw_units.append(
                {
                    "claim": f"证据观点{index}",
                    "evidence_type": "method",
                    "citations": [
                        {
                            "material_id": material["material_id"],
                            "quote_id": quote["quote_id"],
                            "quote": quote["text"],
                        }
                    ],
                    "relevance": "用于方法比较。",
                    "confidence": "high",
                    "caveats": [],
                }
            )
        evidence_units = assign_evidence_unit_ids(
            "source-request",
            raw_units,
            materials,
            generation_id="generation-one",
        )
        evidence_ids = [row["evidence_unit_id"] for row in evidence_units]
        analysis = {
            "schema_version": PAPER_ANALYSIS_SCHEMA_VERSION,
            "research_focus": {
                "statement": "研究第一个问题。",
                "evidence_unit_ids": [evidence_ids[0]],
            },
            "study_type": "modeling",
            "methods": [
                {
                    "statement": "采用第三项方法。",
                    "evidence_unit_ids": [evidence_ids[2]],
                }
            ],
            "core_findings": [
                {
                    "statement": "第二项结果与第三项证据重复。",
                    "evidence_unit_ids": [evidence_ids[1]],
                }
            ],
            "limitations": [],
            "review_uses": [
                {
                    "statement": "采用第三项方法。",
                    "evidence_unit_ids": [evidence_ids[2]],
                }
            ],
            "unresolved_questions": [],
            "evidence_dispositions": [
                {
                    "evidence_unit_id": evidence_id,
                    "disposition": "used",
                    "reason_code": "supports_claim",
                }
                for evidence_id in evidence_ids
            ],
        }
        validate_paper_analysis(analysis, set(evidence_ids), evidence_units=evidence_units)
        statement_records = build_statement_records(analysis)
        duplicate_statement = next(
            row for row in statement_records if row["field"] == "review_uses"
        )
        canonical_statement = next(
            row for row in statement_records if row["field"] == "methods"
        )
        claim_reviews = [
            {
                "evidence_unit_id": evidence_ids[0],
                "verdict": "partially_supported",
                "reason": "当前引文不完整。",
                "unsupported_fragments": ["第一个问题"],
            },
            {
                "evidence_unit_id": evidence_ids[1],
                "verdict": "partially_supported",
                "reason": "当前项与作者直接陈述重复。",
                "unsupported_fragments": ["重复"],
            },
            {
                "evidence_unit_id": evidence_ids[2],
                "verdict": "directly_supported",
                "reason": "引文直接支持。",
                "unsupported_fragments": [],
            },
        ]
        role_reviews = [
            {
                "statement_id": row["statement_id"],
                "verdict": "misclassified" if row["field"] == "review_uses" else "field_aligned",
                "reason": "重复事实没有综述用途。" if row["field"] == "review_uses" else "字段职责正确。",
                "misaligned_fragments": [row["statement"]] if row["field"] == "review_uses" else [],
            }
            for row in statement_records
        ]
        context = build_auto_resolution_context(
            materials=materials,
            evidence_units=evidence_units,
            analysis=analysis,
            evidence_claim_reviews=claim_reviews,
            statement_role_reviews=role_reviews,
        )
        first_issue = next(
            row
            for row in context["evidence_issues"]
            if row["target_evidence_unit_id"] == evidence_ids[0]
        )
        current_quote_id = evidence_units[0]["citations"][0]["quote_id"]
        additional_quote_id = next(
            row["quote_id"]
            for row in first_issue["allowed_quote_candidates"]
            if row["quote_id"] != current_quote_id
        )
        plan = {
            "schema_version": SEMANTIC_AUTO_RESOLUTION_SCHEMA_VERSION,
            "evidence_resolutions": [
                {
                    "target_evidence_unit_id": evidence_ids[0],
                    "action": "repair_citations",
                    "selected_quote_ids": [current_quote_id, additional_quote_id],
                    "replacement_evidence_unit_id": None,
                    "reason": "相邻 Card 的逐字引文补齐了观点。",
                },
                {
                    "target_evidence_unit_id": evidence_ids[1],
                    "action": "replace_with_supported_evidence",
                    "selected_quote_ids": [],
                    "replacement_evidence_unit_id": evidence_ids[2],
                    "reason": "第三项是同一内容的作者直接陈述。",
                },
            ],
            "statement_resolutions": [
                {
                    "target_statement_id": duplicate_statement["statement_id"],
                    "action": "delete_duplicate_statement",
                    "canonical_statement_id": canonical_statement["statement_id"],
                    "reason": "相同事实已在方法字段完整保留。",
                }
            ],
        }
        applied = apply_auto_resolution_plan(
            plan,
            context=context,
            materials=materials,
            evidence_units=evidence_units,
            analysis=analysis,
            generation_id="generation-one",
            request_id="auto-resolution-request",
        )

        revised_ids = {row["evidence_unit_id"] for row in applied["evidence_units"]}
        repaired_id = applied["evidence_id_mapping"][evidence_ids[0]]
        self.assertNotEqual(repaired_id, evidence_ids[0])
        self.assertNotIn(evidence_ids[0], revised_ids)
        self.assertNotIn(evidence_ids[1], revised_ids)
        self.assertEqual(applied["analysis"]["research_focus"]["evidence_unit_ids"], [repaired_id])
        self.assertEqual(applied["analysis"]["core_findings"][0]["evidence_unit_ids"], [evidence_ids[2]])
        self.assertEqual(applied["analysis"]["review_uses"], [])
        validate_paper_analysis(
            applied["analysis"],
            revised_ids,
            evidence_units=applied["evidence_units"],
        )

    def test_auto_resolution_preserves_human_required_items_even_when_regates_pass(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        create_blocked_semantic_audit(workspace, prefix="auto-human-required")
        provider = HumanRequiredAutoResolutionProvider()

        result = LLMSemanticAutoResolutionRunner(
            workspace,
            analysis_client=provider,
            token_counter=StageAwareTokenCounter(),
        ).run(
            audit_run_id="auto-human-required-audit",
            run_id="auto-human-required-result",
            provider="openai-compatible",
        )

        run_dir = (
            workspace
            / "_llm_analysis"
            / "semantic_auto_resolutions"
            / "auto-human-required-result"
        )
        coverage = read_json(run_dir / "coverage.json")
        self.assertEqual(result["status"], "failed")
        self.assertIn("resolution.human_required", result["failure_codes"])
        self.assertEqual(coverage["planner_human_required_count"], 2)
        self.assertEqual(coverage["residual_human_issue_count"], 2)
        self.assertTrue((run_dir / "review" / "residual_semantic_workbook.md").exists())
        self.assertFalse((run_dir / "output" / "validated_paper_analysis.json").exists())

    def test_low_risk_auto_override_is_bound_to_reproduced_gate_signature(self) -> None:
        workspace = make_workspace()
        materials = [make_material(1), make_material(2)]
        materials[0]["clean_title"] = "研究方法"
        materials[0]["raw_title"] = "研究方法"
        materials[1]["clean_title"] = "研究结果"
        materials[1]["raw_title"] = "研究结果"
        write_corpus(workspace, materials)
        original = LLMAnalysisRunner(
            workspace,
            analysis_client=ScriptedProvider(fail_statement_support=True),
        ).run(
            run_id="auto-override-analysis",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        self.assertEqual(original["failure_codes"], ["support.statement_not_fully_supported"])
        revision = LLMStatementRevisionRunner(
            workspace,
            analysis_client=ScriptedProvider(fail_statement_support=True),
        ).run(
            source_run_id="auto-override-analysis",
            run_id="auto-override-revision",
            provider="openai-compatible",
        )
        self.assertEqual(revision["failure_codes"], ["support.statement_not_fully_supported"])
        clear_evidence_claim_cache(workspace)
        audit = LLMSemanticAuditRunner(
            workspace,
            analysis_client=ScriptedProvider(fail_evidence_claim_support=True),
            token_counter=StageAwareTokenCounter(),
        ).run(
            source_run_id="auto-override-revision",
            run_id="auto-override-audit",
            provider="openai-compatible",
        )
        self.assertEqual(audit["failure_codes"], ["support.evidence_claim_not_fully_supported"])

        provider = OverrideAutoResolutionProvider()
        result = LLMSemanticAutoResolutionRunner(
            workspace,
            analysis_client=provider,
            token_counter=StageAwareTokenCounter(),
        ).run(
            audit_run_id="auto-override-audit",
            run_id="auto-override-result",
            provider="openai-compatible",
        )

        run_dir = (
            workspace
            / "_llm_analysis"
            / "semantic_auto_resolutions"
            / "auto-override-result"
        )
        coverage = read_json(run_dir / "coverage.json")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(coverage["automatic_gate_override_count"], 1)
        self.assertEqual(coverage["residual_human_issue_count"], 0)
        self.assertTrue((run_dir / "output" / "validated_paper_analysis.json").exists())

    def test_human_confirmed_semantic_defects_are_revised_and_fully_revalidated(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        audit_dir = create_blocked_semantic_audit(workspace, prefix="adjudication-confirmed")
        decisions_path = audit_dir / "review" / "semantic_decisions.csv"
        decide_semantic_issues(
            decisions_path,
            {
                "evidence_claim": ("确认缺陷", "claim 增加了逐字引文没有表达的内容。"),
                "statement_role": ("确认缺陷", "该陈述只复述论文事实，没有履行字段职责。"),
            },
        )
        provider = AdjudicationRevisionProvider()
        result = LLMSemanticAdjudicationRunner(
            workspace,
            analysis_client=provider,
            token_counter=StageAwareTokenCounter(),
        ).run(
            audit_run_id="adjudication-confirmed-audit",
            run_id="adjudication-confirmed-result",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "adjudication-confirmed-result"
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [task for task, _ in provider.calls],
            [
                "evidence_revision",
                "evidence_claim_support",
                "synthesis",
                "evidence_disposition",
                "statement_role",
                "statement_support",
            ],
        )
        mappings = read_jsonl(run_dir / "output" / "evidence_revision_mappings.jsonl")
        self.assertEqual(mappings[0]["action"], "replace")
        self.assertNotEqual(
            mappings[0]["source_evidence_unit_id"],
            mappings[0]["result_evidence_unit_id"],
        )
        paper_result = read_json(run_dir / "output" / "paper_analysis.json")
        self.assertEqual(paper_result["schema_version"], "llm.paper_result.v4")
        self.assertEqual(len(paper_result["semantic_adjudications"]), 2)
        self.assertTrue((run_dir / "review" / "adjudication.md").exists())

    def test_human_gate_rejections_are_explicit_and_bound_to_unchanged_ids(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        audit_dir = create_blocked_semantic_audit(workspace, prefix="adjudication-overrides")
        decisions_path = audit_dir / "review" / "semantic_decisions.csv"
        decide_semantic_issues(
            decisions_path,
            {
                "evidence_claim": ("门禁误判", "claim 与引文在当前语境中语义等价。"),
                "statement_role": ("门禁误判", "该陈述在当前字段中确实履行职责。"),
            },
        )
        provider = AdjudicationOverrideProvider()
        result = LLMSemanticAdjudicationRunner(
            workspace,
            analysis_client=provider,
            token_counter=StageAwareTokenCounter(),
        ).run(
            audit_run_id="adjudication-overrides-audit",
            run_id="adjudication-overrides-result",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "adjudication-overrides-result"
        self.assertEqual(result["status"], "completed")
        coverage = read_json(run_dir / "coverage.json")
        self.assertEqual(coverage["human_override_evidence_claim_count"], 1)
        self.assertEqual(coverage["human_override_statement_role_count"], 1)
        paper_result = read_json(run_dir / "output" / "paper_analysis.json")
        self.assertEqual(
            sum(row["override_applied"] for row in paper_result["semantic_adjudications"]),
            2,
        )
        self.assertEqual(read_jsonl(run_dir / "audit" / "failures.jsonl"), [])

    def test_confirmed_role_defect_cannot_reappear_before_role_and_support_calls(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        audit_dir = create_blocked_semantic_audit(workspace, prefix="adjudication-reproduced")
        decide_semantic_issues(
            audit_dir / "review" / "semantic_decisions.csv",
            {
                "evidence_claim": ("门禁误判", "claim 与引文在当前语境中语义等价。"),
                "statement_role": ("确认缺陷", "原陈述没有履行字段职责。"),
            },
        )
        provider = AdjudicationReproducedDefectProvider()
        result = LLMSemanticAdjudicationRunner(
            workspace,
            analysis_client=provider,
            token_counter=StageAwareTokenCounter(),
        ).run(
            audit_run_id="adjudication-reproduced-audit",
            run_id="adjudication-reproduced-result",
            provider="openai-compatible",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["failure_codes"],
            ["revision.confirmed_role_defect_reproduced"],
        )
        self.assertEqual(
            [task for task, _ in provider.calls],
            ["synthesis"],
        )
        self.assertFalse(
            (
                workspace
                / "_llm_analysis"
                / "runs"
                / "adjudication-reproduced-result"
                / "output"
                / "paper_analysis.json"
            ).exists()
        )

    def test_source_generation_change_during_run_blocks_paper_analysis_publication(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        provider = GenerationChangingProvider(workspace)

        result = LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="generation-change",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "generation-change"
        failures = read_jsonl(run_dir / "audit" / "failures.jsonl")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(failures[0]["error_code"], "input.source_generation_changed")
        self.assertFalse((run_dir / "output" / "paper_analysis.json").exists())

    def test_invalid_retry_is_recorded_as_failed_run_instead_of_escaping(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])
        provider = ScriptedProvider()
        LLMAnalysisRunner(workspace, analysis_client=provider).run(
            run_id="completed-parent",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
        )
        parent_plan = read_json(
            workspace / "_llm_analysis" / "runs" / "completed-parent" / "plan" / "analysis_plan.json"
        )
        completed_request_id = parent_plan["evidence_batches"][0]["request_id"]

        result = LLMAnalysisRunner(workspace, analysis_client=ScriptedProvider()).run(
            run_id="invalid-retry",
            topic="船舶积压",
            paper_id="paper-one",
            provider="openai-compatible",
            retry_from="completed-parent",
            retry_batch_id=completed_request_id,
        )

        run_dir = workspace / "_llm_analysis" / "runs" / "invalid-retry"
        failures = read_jsonl(run_dir / "audit" / "failures.jsonl")
        self.assertEqual(result["status"], "failed")
        self.assertIn("只允许显式重跑", failures[0]["error_message"])

    def test_openai_compatible_provider_captures_response_metadata_without_archiving_secret(self) -> None:
        observed: dict = {}

        def transport(payload: dict, headers: dict, timeout: int) -> dict:
            observed.update({"payload": payload, "headers": headers, "timeout": timeout})
            return {
                "id": "deepseek-response",
                "model": "deepseek-1m",
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
            }

        client = OpenAICompatibleAnalysisClient(
            api_url="https://example.invalid/chat/completions",
            api_key="top-secret",
            model="deepseek-1m",
            timeout=66,
            transport=transport,
        )
        request_payload = {"model": "deepseek-1m", "messages": []}

        result = client.complete("evidence", request_payload, {})

        self.assertEqual(result.response_model, "deepseek-1m")
        self.assertEqual(result.usage["total_tokens"], 9)
        self.assertEqual(observed["headers"]["Authorization"], "Bearer top-secret")
        self.assertNotIn("top-secret", result.raw_body.decode("utf-8"))
        self.assertNotIn("top-secret", json.dumps(request_payload))
        self.assertEqual(observed["timeout"], 66)

    def test_cli_dry_run_uses_v3_plan_directory_and_prints_chinese_safe_json(self) -> None:
        workspace = make_workspace()
        write_corpus(workspace, [make_material(1), make_material(2)])

        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "main.py"),
                "llm-analysis",
                "--workspace",
                str(workspace),
                "--topic",
                "船舶积压",
                "--run-id",
                "cli-dry-run",
                "--paper-id",
                "paper-one",
                "--dry-run",
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "dry_run_completed")
        self.assertTrue(
            (workspace / "_llm_analysis" / "runs" / "cli-dry-run" / "plan" / "analysis_plan.json").exists()
        )


if __name__ == "__main__":
    unittest.main()
