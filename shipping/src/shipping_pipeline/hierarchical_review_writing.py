from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .llm_analysis import DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_model_profile import load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter


ROOT = "_hierarchical_review_writing"
MIN_BODY_CHARS = 6000
MAX_BODY_CHARS = 24000
CURRENT_BASELINE_MAX_AGE_YEARS = 2
ARGUMENT_PLAN_SYSTEM_PROMPT = """你是学术综述的证据编辑。只输出符合JSON Schema的对象。
你的任务不是写正文，而是按既有章节形成论点计划，并处理相同或近似事实的新旧关系。
事实类型由证据的观察期和论点所指时间决定，不由“现状”等措辞决定。只总结一个已经结束的明确历史区间，例如“2011—2015年的通过量”，必须标为historical_fact；回顾该区间的晚近综述不能覆盖更接近事件、数据更细的原始来源。只有结论确实描述各项证据所覆盖的最新可用状态时，才使用current_status、policy_state或engineering_state。

engineering_state仅用于设施、设备、系统配置或实际运行安排在最新时点的客观状态。算法、模型、调度方案、工程措施、检测方案、仿真、试验或干预产生的效果，无论是否已应用，都归为method_result；不得因为方法用于真实工程就标为engineering_state。尚未实施且主要表达“应当如何做”的方案归为recommendation。method_result和recommendation按研究对象、场景、指标和验证方式判断可比性，不按出版年份机械覆盖。

先判断事实是否可比，再决定更新：current_status、policy_state、engineering_state中，同一对象、指标、口径、观察范围和状态时点的较新证据必须作为current_preferred，旧证据仅作historical_context或corroborating。先逐项核对所选citation_key在题录中的publication_year；只要把一项证据标为corroborating或contrasting，就表示它与当前事实可比，此时current_preferred必须是这些可比证据中年份最新者。较旧论文即使论述更系统，也只能承担解释、历史背景或佐证，不能取代较新观测成为current_preferred。historical_fact保留最接近事件或原始观察的来源；method_result、mechanism和forecast只有在对象、场景、指标与验证方式可比时才可直接比较，否则标为non_comparable，不得因年份较新而覆盖。

证据角色按它对该条conclusion的直接支持关系判定。corroborating必须观测或论证conclusion中的同一事实、指标或机制；只提到同一工程、同一系统或同一主题的背景材料不构成佐证，应标为non_comparable或不纳入该论点。例如，晚近论文只提到检测系统已经建设、却没有复测早期论文报告的测量误差时，不能用它佐证该误差数值，也不能据其出版年份覆盖原始检测结果。
输入中的temporal_audit是整篇综述的时间锚点。current_status、policy_state和engineering_state必须由满足freshness_min_year的观测证据支持；publication_year较新但observation_end_year过旧，仍不是当前证据。若current_baseline_supported为false，不得把历史运行数据写成当前现状，并且必须在第一章论点中纳入latest_system_baseline_citation_keys作为“论文池最新可用运行基线”。
“论文池最新可得”不等于“当前”。例如review_as_of_year为2026、freshness_min_year为2024时，截至2021年的设备故障率必须标为historical_fact或method_result，即使它是论文池中该设备最新数据，也绝不能标为engineering_state或使用current_preferred。
每个论点必须给出明确、可写入正文的结论。boundary_required只在边界会改变结论含义、适用对象或行动判断时为true，不得把常规保守措辞当作边界。不得逐篇罗列摘要。"""

BASELINE_SYSTEM_PROMPT = """你是中文学术综述写作者。只输出符合JSON Schema的对象。
首要硬约束：abstract和所有text字段禁止出现“证明”“普遍”“根本”中的任何一个连续词组；在组织内容前记住该约束，在输出JSON前再次全文检索。
输入包含全局研究景观、各主题局部景观和逐篇Paper Understanding。全局景观决定章节结构，局部景观用于跨论文比较，Paper Understanding用于具体事实、方法、结果、验证水平和局限。
严格服从temporal_audit。若current_baseline_supported为false，引言第一段必须逐字包含temporal_scope_statement；引言第一段的citation_keys必须与latest_system_baseline_citation_keys完全一致，只写该基线能够直接支持的截至年份事实。整个引言只能出现latest_system_observation_year和review_as_of_year这两个四位年份；更早历史年份、远期预测年份、旧佐证数据和历史细节必须移到正文。摘要必须同时写明最新观测年份、综述年份，并明确不能据旧数据判断实时状态。历史数据可以在正文中用于解释演变，但必须写明对应年份或“截至某年”，不得称为当前现状。
严格执行输入中的argument_plan。必须区分现场观察、统计分析、数值仿真、物理模型试验、算法测试、工程实践与方案建议，不得把仿真或建议写成已实施效果。
每段argument_ids必须指向实际展开的计划论点，citation_keys只能列出这些论点已批准的论文短键。正文不得写机器ID或引用编号。
argument_plan中的每个论点必须在正文至少使用一次，不得静默丢弃已经完成的新旧证据决策。
正文不得使用“证明”“普遍”“根本”三个词，包括“从根本上”“根本原因”等组合。输出JSON前逐段检查abstract和全部text字段；发现任一禁用词时必须先改写为具体含义，例如“扩大物理通过能力”或“主要原因”，不得原样输出。"""
BASELINE_SYSTEM_PROMPT += """
仅由historical_fact支持的段落必须使用明确的过去时间范围，不得使用“当前”“目前”“现阶段”“短期内”“如今”“当下”等词把历史观察延伸到综述年份。
最终中文正文总长度必须为6000至24000个字符。每个主题章节应充分展开研究对象、方法和证据类型、主要共识与分歧、验证边界及其对综述主题的意义，不能只写概括性摘要；扩写必须来自输入材料，不得用重复句或无来源常识填充篇幅。"""

SKILL_GUIDED_SYSTEM_PROMPT = BASELINE_SYSTEM_PROMPT + """
采用成熟学术综述写作方法完成正文：先服从论点计划，再写连续论证；按问题和观点综合多篇研究，不得逐篇罗列摘要；每段第一句直接提出本段结论，随后选择最有解释力的证据进行比较、解释并推进到下一判断，不得以“情况复杂”“仍需研究”等空泛句开头。
不要防御式写作。没有决策意义的例外、常规限制和泛泛保守考虑不写；只有计划中boundary_required为true时，才在论证末尾用一句话说明会改变结论适用性的边界。证据足以支持时，应使用清楚、肯定的判断句。"""
SKILL_GUIDED_SYSTEM_PROMPT += """
精炼指删除空话和重复，不是省略证据比较。七个主题均须展开论点形成、关键结果、方法差异及其对主题的意义；总长度不得低于6000个中文字符。"""

WRITING_PROFILES = {
    "baseline": BASELINE_SYSTEM_PROMPT,
    "skill_guided": SKILL_GUIDED_SYSTEM_PROMPT,
}


class HierarchicalReviewWritingError(ValueError):
    pass


class HierarchicalReviewWritingRunner:
    def __init__(self, workspace: str | Path, *, analysis_client=None, token_counter=None) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        global_run_id: str,
        reference_catalog_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path,
        writing_profile: str = "skill_guided",
        review_as_of_year: int | None = None,
        reuse_argument_plan_from_run_id: str | None = None,
        reuse_draft_from_run_id: str | None = None,
        timeout: int = 1800,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = self.workspace / ROOT / "runs" / resolved
        if run_dir.exists():
            raise HierarchicalReviewWritingError(f"run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started = _now()
        failure = None
        output = None
        stage_result = None
        try:
            source = self._load_source(
                global_run_id,
                reference_catalog_run_id,
                review_as_of_year=review_as_of_year or datetime.now(UTC).year,
            )
            _write_json(run_dir / "audit" / "temporal_adequacy.json", source["temporal_audit"])
            plan_schema = _argument_plan_schema(source["global_landscape"])
            schema = _schema(source["global_landscape"])
            generation_schema = _schema(source["global_landscape"], forbid_academic_terms=False)
            _write_json(run_dir / "input" / "writing_input.json", source)
            _write_json(run_dir / "input" / "argument_plan_schema.json", plan_schema)
            _write_json(run_dir / "input" / "output_schema.json", schema)
            profile = load_model_profile(Path(model_profile_path))
            if writing_profile not in WRITING_PROFILES:
                raise HierarchicalReviewWritingError(f"未知写作配置：{writing_profile}")
            if profile.provider != provider:
                raise HierarchicalReviewWritingError("模型provider与命令不一致。")
            _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
            if reuse_draft_from_run_id:
                prior_dir = self.workspace / ROOT / "runs" / reuse_draft_from_run_id
                prior_input = _read_json(prior_dir / "input" / "writing_input.json")
                if prior_input["input_sha256"] != source["input_sha256"]:
                    raise HierarchicalReviewWritingError("复用草稿的冻结输入与当前输入不一致。")
                argument_plan = _read_json(prior_dir / "output" / "argument_plan.json")
                _validate_argument_plan(argument_plan, source, plan_schema)
                parsed = _read_json(prior_dir / "generation" / "parsed_response.json")
                plan_stage_result = {
                    "status": "reused",
                    "reused_from_run_id": reuse_draft_from_run_id,
                }
                stage_result = {
                    "status": "reused",
                    "reused_from_run_id": reuse_draft_from_run_id,
                    "input_sha256": source["input_sha256"],
                }
            else:
                client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                    api_url=api_url,
                    api_key_env=api_key_env,
                    model=profile.request_model,
                    timeout=timeout,
                )
                counter = self.token_counter or DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
                if reuse_argument_plan_from_run_id:
                    prior_dir = self.workspace / ROOT / "runs" / reuse_argument_plan_from_run_id
                    prior_input = _read_json(prior_dir / "input" / "writing_input.json")
                    if prior_input["input_sha256"] != source["input_sha256"]:
                        raise HierarchicalReviewWritingError("复用论点计划的冻结输入与当前输入不一致。")
                    argument_plan = _read_json(prior_dir / "output" / "argument_plan.json")
                    plan_stage_result = {"status": "reused", "reused_from_run_id": reuse_argument_plan_from_run_id}
                else:
                    argument_plan, plan_stage_result = execute_json_stage(
                        run_dir=run_dir,
                        stage="hierarchical_argument_planning",
                        task_name="hierarchical_review_argument_planning",
                        directory_name="argument_plan",
                        system_prompt=ARGUMENT_PLAN_SYSTEM_PROMPT,
                        user_prompt=json.dumps({
                            "任务": "建立章节论点、处理近似事实的新旧关系并决定正文边界",
                            "输入": source,
                            "输出JSONSchema": plan_schema,
                        }, ensure_ascii=False, separators=(",", ":")),
                        max_output_tokens=min(24576, profile.model_max_output_tokens),
                        context={"global_run_id": global_run_id, "reference_catalog_run_id": reference_catalog_run_id},
                        client=client,
                        profile=profile,
                        token_counter=counter,
                    )
                argument_plan = _validate_argument_plan(argument_plan, source, plan_schema)
                generation_source = _build_generation_source(source, argument_plan)
                _write_json(run_dir / "input" / "approved_writing_source.json", generation_source)
                parsed, stage_result = execute_json_stage(
                    run_dir=run_dir,
                    stage="hierarchical_review_generation",
                    task_name="hierarchical_review_writing",
                    directory_name="generation",
                    system_prompt=WRITING_PROFILES[writing_profile],
                    user_prompt=json.dumps({
                        "任务": "依据层级研究景观、论点计划和论文理解撰写完整中文综述",
                        "写作要求": {
                            "正文目标字符数": f"{MIN_BODY_CHARS}-{MAX_BODY_CHARS}",
                            "每个主题章节": "至少2段，比较方法、结果、验证水平和适用边界",
                            "引用规则": "每段至少1个citation_key；具体结果必须直接引用支持论文",
                        },
                        "argument_plan": argument_plan,
                        "批准证据": generation_source,
                        "输出JSONSchema": generation_schema,
                    }, ensure_ascii=False, separators=(",", ":")),
                    max_output_tokens=min(32768, profile.model_max_output_tokens),
                    context={
                        "global_run_id": global_run_id,
                        "paper_count": len(source["papers"]),
                        "chapter_count": len(source["global_landscape"]["global_dimensions"]),
                    },
                    client=client,
                    profile=profile,
                    token_counter=counter,
                )
            _write_json(run_dir / "output" / "argument_plan.json", argument_plan)
            _write_text(run_dir / "review" / "argument_plan.md", _render_argument_plan(argument_plan, source))
            structural_edits = _normalize_repeated_ids(parsed)
            temporal_intro_edit = _normalize_temporal_introduction(parsed, source, argument_plan)
            structural_edits["temporal_introduction_replaced"] = temporal_intro_edit
            argument_id_edits = _normalize_argument_ids_to_citations(parsed, argument_plan)
            structural_edits["argument_ids_removed_without_citation_support"] = argument_id_edits
            structural_edits["removed_duplicate_count"] += argument_id_edits
            _write_json(run_dir / "audit" / "structural_normalization.json", structural_edits)
            lexical_edits = _normalize_academic_terms(parsed)
            _write_json(run_dir / "audit" / "academic_term_normalization.json", lexical_edits)
            restored_citation_mentions = _restore_citation_names(parsed, source)
            output = _validate(parsed, source, schema, argument_plan)
            _write_json(run_dir / "generation" / "validated_review.json", output)
            _write_json(run_dir / "output" / "hierarchical_review.json", output)
            _write_text(run_dir / "review" / "hierarchical_review.md", _render(output, source))
            citation_audit = _citation_audit(output, source)
            citation_audit["restored_citation_mention_count"] = restored_citation_mentions
            citation_audit["argument_plan_coverage"] = _argument_plan_coverage(output, argument_plan)
            citation_audit["cross_argument_citations"] = _cross_argument_citations(output, argument_plan)
            _write_json(run_dir / "audit" / "citation_coverage.json", citation_audit)
            status = "completed"
        except Exception as exc:
            status = "failed"
            failure = {"error_code": type(exc).__name__, "error_message": str(exc), "recorded_at": _now()}
            _write_json(run_dir / "audit" / "failure.json", failure)
        manifest = {
            "schema_version": "llm.hierarchical_review_writing_run.v1",
            "run_id": resolved,
            "status": status,
            "started_at": started,
            "finished_at": _now(),
            "global_run_id": global_run_id,
            "reference_catalog_run_id": reference_catalog_run_id,
            "reuse_draft_from_run_id": reuse_draft_from_run_id,
            "reuse_argument_plan_from_run_id": reuse_argument_plan_from_run_id,
            "writing_profile": writing_profile,
            "review_as_of_year": source["temporal_audit"]["review_as_of_year"] if "source" in locals() else review_as_of_year,
            "paper_count": len(source["papers"]) if "source" in locals() else 0,
            "chapter_count": len(output["chapters"]) if output else 0,
            "body_char_count": _body_chars(output) if output else 0,
            "stage_result": stage_result,
            "argument_plan_stage_result": plan_stage_result if "plan_stage_result" in locals() else None,
            "academic_term_normalization_count": lexical_edits["replacement_count"] if "lexical_edits" in locals() else 0,
            "structural_normalization_count": structural_edits["removed_duplicate_count"] if "structural_edits" in locals() else 0,
            "failure": failure,
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {"run_id": resolved, "status": status, "run_dir": str(run_dir), "review_path": str(run_dir / "review" / "hierarchical_review.md") if output else None, "body_char_count": manifest["body_char_count"], "failure": failure}

    def _load_source(
        self,
        global_run_id: str,
        reference_catalog_run_id: str,
        *,
        review_as_of_year: int,
    ) -> dict[str, Any]:
        global_dir = self.workspace / "_hierarchical_global_landscapes" / "runs" / global_run_id
        manifest = _read_json(global_dir / "manifest.json")
        if manifest.get("status") != "completed":
            raise HierarchicalReviewWritingError("全局研究景观运行不可用。")
        global_landscape = _read_json(global_dir / "output" / "hierarchical_global_landscape.json")
        local_batch_id = str(manifest["local_batch_run_id"])
        local_batch_dir = self.workspace / "_hierarchical_landscapes" / "local_runs" / local_batch_id
        local_batch = _read_json(local_batch_dir / "output" / "local_landscape_batch.json")
        local_landscapes: list[dict[str, Any]] = []
        papers: dict[str, dict[str, Any]] = {}
        for record in local_batch["records"]:
            if record["local_status"] != "completed":
                raise HierarchicalReviewWritingError(f"局部主题未完成：{record['cluster_id']}")
            run = str(record["landscape_run_id"])
            local_dir = self.workspace / "_research_landscapes" / "runs" / run
            landscape = _read_json(local_dir / "output" / "research_landscape.json")
            local_landscapes.append({"cluster_id": record["cluster_id"], "cluster_title": record["cluster_title"], "landscape": landscape})
            for row in _read_jsonl(local_dir / "input" / "understandings.jsonl"):
                understanding = row["understanding"]
                papers[str(understanding["paper_id"])] = understanding
        references = _load_reference_years(self.workspace, reference_catalog_run_id, set(papers))
        aliases = {paper_id: f"P{index:03d}" for index, paper_id in enumerate(sorted(papers), 1)}
        projected = []
        for paper_id in sorted(papers):
            row = dict(papers[paper_id])
            row.pop("understanding_id", None)
            observation_end_year = _extract_observation_end_year(row, references[paper_id])
            projected.append({"citation_key": aliases[paper_id], "publication_year": references[paper_id], "observation_end_year": observation_end_year, **row})
        citation_map = [{
            "citation_key": row["citation_key"],
            "paper_id": row["paper_id"],
            "paper_title": row.get("paper_title", row["paper_id"]),
            "publication_year": row["publication_year"],
            "observation_end_year": row["observation_end_year"],
        } for row in projected]
        temporal_audit = _build_temporal_audit(projected, review_as_of_year)
        result = {
            "schema_version": "llm.hierarchical_review_writing_input.v1",
            "global_run_id": global_run_id,
            "local_batch_run_id": local_batch_id,
            "global_landscape": global_landscape,
            "local_landscapes": local_landscapes,
            "papers": projected,
            "citation_map": citation_map,
            "temporal_audit": temporal_audit,
            "reference_catalog_run_id": reference_catalog_run_id,
        }
        result["input_sha256"] = _sha256(result)
        return result


def _extract_observation_end_year(paper: dict[str, Any], publication_year: int) -> int | None:
    context = paper.get("study_context") or {}
    time_scope = str(context.get("time_scope") or "")
    years = [int(year) for year in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", time_scope)]
    observed = [year for year in years if year <= publication_year]
    return max(observed) if observed else None


def _is_system_operational_baseline(paper: dict[str, Any]) -> bool:
    context = paper.get("study_context") or {}
    data_sources = set(context.get("data_sources") or [])
    method_categories = {row.get("method_category") for row in paper.get("methods") or []}
    return bool(data_sources & {"operational_records", "field_observation"}) and "descriptive_analysis" in method_categories


def _build_temporal_audit(papers: list[dict[str, Any]], review_as_of_year: int) -> dict[str, Any]:
    if not 2000 <= review_as_of_year <= 2999:
        raise HierarchicalReviewWritingError(f"综述时间锚点无效：{review_as_of_year}")
    candidates = [paper for paper in papers if paper.get("observation_end_year") and _is_system_operational_baseline(paper)]
    latest_year = max((paper["observation_end_year"] for paper in candidates), default=None)
    latest = [paper for paper in candidates if paper["observation_end_year"] == latest_year]
    freshness_min_year = review_as_of_year - CURRENT_BASELINE_MAX_AGE_YEARS
    supported = latest_year is not None and latest_year >= freshness_min_year
    if supported:
        statement = f"本文以截至{latest_year}年的系统运行证据作为{review_as_of_year}年综述的当前基线。"
    elif latest_year is not None:
        statement = f"本文所据系统运行证据最新截止{latest_year}年，不能据此判断{review_as_of_year}年的实时运行状态。"
    else:
        statement = f"现有论文池没有可识别的系统运行观测截止年份，不能据此判断{review_as_of_year}年的实时运行状态。"
    return {
        "schema_version": "hierarchical_review.temporal_adequacy.v1",
        "review_as_of_year": review_as_of_year,
        "freshness_max_age_years": CURRENT_BASELINE_MAX_AGE_YEARS,
        "freshness_min_year": freshness_min_year,
        "latest_system_observation_year": latest_year,
        "latest_system_baseline_citation_keys": [paper["citation_key"] for paper in latest],
        "latest_system_baseline_paper_ids": [paper["paper_id"] for paper in latest],
        "current_baseline_supported": supported,
        "temporal_scope_statement": statement,
        "external_current_evidence_needed": not supported,
    }


def _schema(global_landscape: dict[str, Any], *, forbid_academic_terms: bool = True) -> dict[str, Any]:
    titles = [row["title"] for row in global_landscape["global_dimensions"]]
    forbidden = "|证明|普遍|根本" if forbid_academic_terms else ""
    text_pattern = rf"^(?![\s\S]*(?:P[0-9]{{3}}|argument_|dimension_|cluster_|global_{forbidden}))[\s\S]+$"
    paragraph = {"type": "object", "additionalProperties": False, "required": ["text", "argument_ids", "citation_keys"], "properties": {"text": {"type": "string", "minLength": 60, "maxLength": 2600, "pattern": text_pattern}, "argument_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": {"type": "string", "pattern": "^A[0-9]{3}$"}}, "citation_keys": {"type": "array", "minItems": 1, "maxItems": 24, "uniqueItems": True, "items": {"type": "string", "pattern": "^P[0-9]{3}$"}}}}
    introduction_paragraph = json.loads(json.dumps(paragraph))
    introduction_paragraph["properties"]["text"]["minLength"] = 50
    introduction_paragraph["properties"]["argument_ids"]["minItems"] = 0
    introduction_paragraph["properties"]["citation_keys"]["minItems"] = 0
    conclusion_paragraph = json.loads(json.dumps(paragraph))
    conclusion_paragraph["properties"]["text"]["minLength"] = 40
    discussion_paragraph = json.loads(json.dumps(paragraph))
    discussion_paragraph["properties"]["text"]["minLength"] = 80
    conclusion_paragraph["properties"]["citation_keys"]["minItems"] = 0
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False, "required": ["title", "abstract", "keywords", "introduction", "chapters", "discussion", "conclusion"], "properties": {
        "title": {"type": "string", "minLength": 8, "maxLength": 80},
        "abstract": {"type": "string", "minLength": 300, "maxLength": 1200, "pattern": text_pattern},
        "keywords": {"type": "array", "minItems": 4, "maxItems": 8, "uniqueItems": True, "items": {"type": "string"}},
        "introduction": {"type": "array", "minItems": 2, "maxItems": 4, "items": introduction_paragraph},
        "chapters": {"type": "array", "minItems": len(titles), "maxItems": len(titles), "prefixItems": [{"type": "object", "additionalProperties": False, "required": ["chapter_index", "title", "paragraphs"], "properties": {"chapter_index": {"const": i}, "title": {"const": title}, "paragraphs": {"type": "array", "minItems": 2, "maxItems": 6, "items": paragraph}}} for i, title in enumerate(titles, 1)]},
        "discussion": {"type": "array", "minItems": 2, "maxItems": 5, "items": discussion_paragraph},
        "conclusion": {"type": "array", "minItems": 1, "maxItems": 3, "items": conclusion_paragraph},
    }}


def _validate(value: dict[str, Any], source: dict[str, Any], schema: dict[str, Any], argument_plan: dict[str, Any]) -> dict[str, Any]:
    Draft202012Validator(schema).validate(value)
    temporal = source["temporal_audit"]
    if not temporal["current_baseline_supported"]:
        statement = temporal["temporal_scope_statement"]
        latest_year = temporal["latest_system_observation_year"]
        abstract = value["abstract"]
        review_year = temporal["review_as_of_year"]
        abstract_discloses_gap = (
            latest_year is not None
            and str(latest_year) in abstract
            and str(review_year) in abstract
            and any(marker in abstract for marker in ("不能", "不代表", "不反映", "无法"))
        )
        if not abstract_discloses_gap or statement not in value["introduction"][0]["text"]:
            raise HierarchicalReviewWritingError("摘要或引言首段未明确披露系统运行证据的时间缺口。")
        baseline_keys = set(temporal["latest_system_baseline_citation_keys"])
        first_keys = set(value["introduction"][0]["citation_keys"])
        if not baseline_keys or first_keys != baseline_keys:
            raise HierarchicalReviewWritingError("引言首段没有仅使用论文池最新系统运行基线。")
        allowed_intro_years = {latest_year, review_year}
        intro_years = {
            int(year)
            for paragraph in value["introduction"]
            for year in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", paragraph["text"])
        }
        unexpected_intro_years = sorted(intro_years - allowed_intro_years)
        if unexpected_intro_years:
            raise HierarchicalReviewWritingError(f"引言展开了非时间锚点年份：{unexpected_intro_years}")
    forbidden_abstract = [token for token in ("证明", "普遍", "根本") if token in value["abstract"]]
    if forbidden_abstract:
        raise HierarchicalReviewWritingError(f"摘要包含不合适学术措辞：{forbidden_abstract}")
    allowed = {row["citation_key"] for row in source["citation_map"]}
    claims = {claim["argument_id"]: claim for chapter in argument_plan["chapters"] for claim in chapter["arguments"]}
    planned_citations = {item["citation_key"] for claim in claims.values() for item in claim["evidence"]}
    for paragraph in _paragraphs(value):
        unknown = set(paragraph["citation_keys"]) - allowed
        if unknown:
            raise HierarchicalReviewWritingError(f"出现未知引用：{sorted(unknown)}")
        if any(token in paragraph["text"] for token in ("argument_", "dimension_", "cluster_", "global_")) or re.search(r"(?<![A-Za-z0-9_])(?:P|A)\d{3}(?![A-Za-z0-9_])", paragraph["text"]):
            raise HierarchicalReviewWritingError("正文泄露机器标识。")
        forbidden = [token for token in ("证明", "普遍", "根本") if token in paragraph["text"]]
        if forbidden:
            raise HierarchicalReviewWritingError(f"正文包含不合适学术措辞：{forbidden}")
        unknown_arguments = set(paragraph["argument_ids"]) - set(claims)
        if unknown_arguments:
            raise HierarchicalReviewWritingError(f"正文引用未知论点：{sorted(unknown_arguments)}")
        outside_plan = set(paragraph["citation_keys"]) - planned_citations
        if outside_plan:
            raise HierarchicalReviewWritingError(f"正文使用了论点计划未批准的论文证据：{sorted(outside_plan)}")
        paragraph_claims = [claims[argument_id] for argument_id in paragraph["argument_ids"]]
        if paragraph_claims and all(claim["fact_kind"] == "historical_fact" for claim in paragraph_claims):
            present_markers = _historical_present_markers(paragraph["text"])
            if present_markers:
                raise HierarchicalReviewWritingError(f"历史论点使用了现实时态：{present_markers}")
    chars = _body_chars(value)
    if not MIN_BODY_CHARS <= chars <= MAX_BODY_CHARS:
        raise HierarchicalReviewWritingError(f"正文字符数不合格：{chars}")
    coverage = _argument_plan_coverage(value, argument_plan)
    if coverage["unused_argument_ids"]:
        raise HierarchicalReviewWritingError(f"正文遗漏计划论点：{coverage['unused_argument_ids']}")
    value["body_char_count"] = chars
    value["temporal_scope_statement"] = temporal["temporal_scope_statement"]
    return value


def _normalize_academic_terms(value: dict[str, Any]) -> dict[str, Any]:
    replacements = (
        ("从根本上", "直接"),
        ("根本原因", "主要原因"),
        ("根本性", "结构性"),
        ("不能证明", "不足以支持"),
        ("无法证明", "不足以支持"),
        ("证明", "表明"),
        ("普遍", "广泛"),
        ("根本", "主要"),
    )
    counts: dict[str, int] = {}
    targets = [("abstract", value, "abstract")]
    targets.extend(("paragraph", paragraph, "text") for paragraph in _paragraphs(value))
    for _, container, key in targets:
        text = container[key]
        for old, new in replacements:
            count = text.count(old)
            if count:
                counts[f"{old} -> {new}"] = counts.get(f"{old} -> {new}", 0) + count
                text = text.replace(old, new)
        container[key] = text
    return {
        "schema_version": "academic_term_normalization.v1",
        "replacement_count": sum(counts.values()),
        "replacements": counts,
        "scope": "abstract_and_paragraph_text_only",
    }


def _historical_present_markers(text: str) -> list[str]:
    found = []
    for marker in ("当前", "目前", "现阶段", "短期内", "如今", "当下", "截至目前"):
        for match in re.finditer(re.escape(marker), text):
            prefix = text[max(0, match.start() - 18):match.start()]
            if re.search(r"(?:不能|不代表|不反映|无法|不足以|不可|不应|不得).{0,12}$", prefix):
                continue
            found.append(marker)
            break
    return found


def _normalize_repeated_ids(value: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {"argument_ids": 0, "citation_keys": 0}
    for paragraph in _paragraphs(value):
        for key in counts:
            original = paragraph[key]
            normalized = list(dict.fromkeys(original))
            counts[key] += len(original) - len(normalized)
            paragraph[key] = normalized
    return {
        "schema_version": "hierarchical_review.structural_normalization.v1",
        "removed_duplicate_count": sum(counts.values()),
        "removed_by_field": counts,
        "scope": "paragraph_id_arrays_only",
    }


def _normalize_temporal_introduction(
    value: dict[str, Any],
    source: dict[str, Any],
    argument_plan: dict[str, Any],
) -> bool:
    temporal = source["temporal_audit"]
    if temporal["current_baseline_supported"]:
        return False
    baseline_keys = set(temporal["latest_system_baseline_citation_keys"])
    if not baseline_keys:
        raise HierarchicalReviewWritingError("缺少可用于构造时间范围引言的系统运行基线。")
    papers = [paper for paper in source["papers"] if paper["citation_key"] in baseline_keys]
    titles = "、".join(f"《{paper['paper_title']}》" for paper in papers)
    statements = []
    for paper in papers:
        empirical = [
            row["statement"]
            for row in paper.get("contributions") or []
            if row.get("result_type") == "empirical_measurement"
            and row.get("validation_level") == "field_observation"
        ]
        if empirical:
            statements.append(empirical[0])
    evidence_summary = " ".join(statements)
    if not evidence_summary:
        evidence_summary = "该基线汇总了论文池中观察期最新的系统运行记录。"
    argument_ids = [
        argument["argument_id"]
        for argument in argument_plan["chapters"][0]["arguments"]
        if any(item["citation_key"] in baseline_keys for item in argument["evidence"])
    ]
    if not argument_ids:
        raise HierarchicalReviewWritingError("第一章缺少系统运行基线论点。")
    baseline_paragraph = {
        "text": (
            f"{temporal['temporal_scope_statement']} "
            f"论文池最新系统运行基线来自{titles}，观察期截止{temporal['latest_system_observation_year']}年。"
            f"{evidence_summary}"
        ),
        "argument_ids": [argument_ids[0]],
        "citation_keys": sorted(baseline_keys),
    }
    theme_titles = [row["title"] for row in source["global_landscape"]["global_dimensions"]]
    scope_paragraph = {
        "text": (
            "本综述围绕" + "、".join(f"“{title}”" for title in theme_titles)
            + "组织论证，重点比较不同研究的方法、结果类型、验证水平和适用边界。"
            "历史运行数据用于解释技术演进，不承担综述年份的实时状态判断；算法、模型和工程建议按其实际验证层级分别评价。"
        ),
        "argument_ids": [],
        "citation_keys": [],
    }
    value["introduction"] = [baseline_paragraph, scope_paragraph]
    return True


def _normalize_argument_ids_to_citations(value: dict[str, Any], argument_plan: dict[str, Any]) -> int:
    evidence_by_argument = {
        argument["argument_id"]: {item["citation_key"] for item in argument["evidence"]}
        for chapter in argument_plan["chapters"]
        for argument in chapter["arguments"]
    }
    removed = 0
    for paragraph in _paragraphs(value):
        cited = set(paragraph["citation_keys"])
        if not cited:
            continue
        supported = [
            argument_id
            for argument_id in paragraph["argument_ids"]
            if evidence_by_argument.get(argument_id, set()) & cited
        ]
        if supported:
            removed += len(paragraph["argument_ids"]) - len(supported)
            paragraph["argument_ids"] = supported
    return removed


def _build_generation_source(source: dict[str, Any], argument_plan: dict[str, Any]) -> dict[str, Any]:
    approved_keys = {
        evidence["citation_key"]
        for chapter in argument_plan["chapters"]
        for argument in chapter["arguments"]
        for evidence in argument["evidence"]
    }
    papers = [paper for paper in source["papers"] if paper["citation_key"] in approved_keys]
    citation_map = [row for row in source["citation_map"] if row["citation_key"] in approved_keys]
    found = {paper["citation_key"] for paper in papers}
    if found != approved_keys:
        raise HierarchicalReviewWritingError(f"论点计划批准证据缺少论文理解：{sorted(approved_keys - found)}")
    return {
        "schema_version": "hierarchical_review.approved_writing_source.v1",
        "topic": source["global_landscape"].get("topic", ""),
        "temporal_audit": source["temporal_audit"],
        "paper_count": len(papers),
        "citation_map": citation_map,
        "papers": papers,
    }


def _argument_plan_schema(global_landscape: dict[str, Any]) -> dict[str, Any]:
    titles = [row["title"] for row in global_landscape["global_dimensions"]]
    evidence = {"type": "object", "additionalProperties": False, "required": ["citation_key", "role"], "properties": {
        "citation_key": {"type": "string", "pattern": "^P[0-9]{3}$"},
        "role": {"enum": ["current_preferred", "historical_primary", "corroborating", "contrasting", "historical_context", "non_comparable"]},
    }}
    argument = {"type": "object", "additionalProperties": False, "required": ["argument_id", "conclusion", "fact_kind", "evidence", "update_rationale", "boundary_required", "boundary"], "properties": {
        "argument_id": {"type": "string", "pattern": "^A[0-9]{3}$"},
        "conclusion": {"type": "string", "minLength": 20, "maxLength": 300},
        "fact_kind": {"enum": ["current_status", "policy_state", "engineering_state", "historical_fact", "method_result", "mechanism", "forecast", "recommendation"]},
        "evidence": {"type": "array", "minItems": 1, "maxItems": 16, "items": evidence},
        "update_rationale": {"type": "string", "minLength": 12, "maxLength": 500},
        "boundary_required": {"type": "boolean"},
        "boundary": {"type": ["string", "null"], "maxLength": 300},
    }}
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False, "required": ["chapters"], "properties": {"chapters": {"type": "array", "minItems": len(titles), "maxItems": len(titles), "prefixItems": [
        {"type": "object", "additionalProperties": False, "required": ["chapter_index", "title", "thesis", "arguments"], "properties": {"chapter_index": {"const": i}, "title": {"const": title}, "thesis": {"type": "string", "minLength": 20, "maxLength": 300}, "arguments": {"type": "array", "minItems": 2, "maxItems": 5, "items": argument}}}
        for i, title in enumerate(titles, 1)
    ]}}}


def _validate_argument_plan(value: dict[str, Any], source: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    Draft202012Validator(schema).validate(value)
    publication_years = {row["citation_key"]: row["publication_year"] for row in source["citation_map"]}
    observation_years = {row["citation_key"]: row.get("observation_end_year") for row in source["citation_map"]}
    temporal = source["temporal_audit"]
    argument_ids: set[str] = set()
    for chapter in value["chapters"]:
        for argument in chapter["arguments"]:
            argument_id = argument["argument_id"]
            if argument_id in argument_ids:
                raise HierarchicalReviewWritingError(f"论点ID重复：{argument_id}")
            argument_ids.add(argument_id)
            evidence_keys = [row["citation_key"] for row in argument["evidence"]]
            if len(evidence_keys) != len(set(evidence_keys)):
                raise HierarchicalReviewWritingError(f"论点证据重复：{argument_id}")
            unknown = set(evidence_keys) - set(publication_years)
            if unknown:
                raise HierarchicalReviewWritingError(f"论点使用未知论文：{sorted(unknown)}")
            if argument["boundary_required"] != bool(argument["boundary"]):
                raise HierarchicalReviewWritingError(f"论点边界标记与内容不一致：{argument_id}")
            if argument["fact_kind"] in {"current_status", "policy_state", "engineering_state"}:
                preferred = [row["citation_key"] for row in argument["evidence"] if row["role"] == "current_preferred"]
                comparable = [row["citation_key"] for row in argument["evidence"] if row["role"] != "non_comparable"]
                missing_observation_year = [key for key in comparable if observation_years[key] is None]
                if missing_observation_year:
                    raise HierarchicalReviewWritingError(f"当前状态证据缺少观测截止年份：{argument_id}/{missing_observation_year}")
                latest_year = max(observation_years[key] for key in comparable)
                if latest_year < temporal["freshness_min_year"]:
                    raise HierarchicalReviewWritingError(f"当前状态证据已过时：{argument_id}/截至{latest_year}年")
                if not preferred or any(observation_years[key] != latest_year for key in preferred):
                    raise HierarchicalReviewWritingError(f"时效性事实未选择最新可比证据：{argument_id}")
    baseline_keys = set(temporal["latest_system_baseline_citation_keys"])
    first_chapter_keys = {
        item["citation_key"]
        for argument in value["chapters"][0]["arguments"]
        for item in argument["evidence"]
    }
    if baseline_keys and not (baseline_keys & first_chapter_keys):
        raise HierarchicalReviewWritingError("第一章论点未纳入论文池最新系统运行基线。")
    return value


def _restore_citation_names(value: dict[str, Any], source: dict[str, Any]) -> int:
    titles = {
        row["citation_key"]: f"《{row['paper_title']}》"
        for row in source["citation_map"]
    }
    pattern = re.compile(r"(?<![A-Za-z0-9_])P\d{3}(?![A-Za-z0-9_])")
    count = 0
    for paragraph in _paragraphs(value):
        def replace(match: re.Match[str]) -> str:
            nonlocal count
            key = match.group(0)
            title = titles.get(key)
            if title is None:
                return key
            count += 1
            return title
        paragraph["text"] = pattern.sub(replace, paragraph["text"])
    return count


def _paragraphs(value: dict[str, Any]):
    yield from value["introduction"]
    for chapter in value["chapters"]:
        yield from chapter["paragraphs"]
    yield from value["discussion"]
    yield from value["conclusion"]


def _body_chars(value: dict[str, Any]) -> int:
    return len(value["abstract"]) + sum(len(row["text"]) for row in _paragraphs(value))


def _citation_audit(value: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    used = {key for row in _paragraphs(value) for key in row["citation_keys"]}
    all_keys = {row["citation_key"] for row in source["citation_map"]}
    return {"paper_count": len(all_keys), "cited_paper_count": len(used), "uncited_paper_count": len(all_keys - used), "uncited_citation_keys": sorted(all_keys - used), "citation_coverage_ratio": len(used) / len(all_keys)}


def _argument_plan_coverage(value: dict[str, Any], argument_plan: dict[str, Any]) -> dict[str, Any]:
    planned = {row["argument_id"] for chapter in argument_plan["chapters"] for row in chapter["arguments"]}
    used = {argument_id for paragraph in _paragraphs(value) for argument_id in paragraph["argument_ids"]}
    return {
        "planned_argument_count": len(planned),
        "used_argument_count": len(used),
        "unused_argument_ids": sorted(planned - used),
        "coverage_ratio": len(used) / len(planned),
    }


def _cross_argument_citations(value: dict[str, Any], argument_plan: dict[str, Any]) -> dict[str, Any]:
    claims = {row["argument_id"]: row for chapter in argument_plan["chapters"] for row in chapter["arguments"]}
    records = []
    for paragraph_index, paragraph in enumerate(_paragraphs(value), 1):
        local = {
            item["citation_key"]
            for argument_id in paragraph["argument_ids"]
            for item in claims[argument_id]["evidence"]
        }
        extra = sorted(set(paragraph["citation_keys"]) - local)
        if extra:
            records.append({"paragraph_index": paragraph_index, "citation_keys": extra})
    return {"paragraph_count": len(records), "records": records}


def _load_reference_years(workspace: Path, run_id: str, paper_ids: set[str]) -> dict[str, int]:
    run_dir = workspace / "_reference_catalogs" / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json")
    if manifest.get("status") not in {"completed", "completed_with_gaps"}:
        raise HierarchicalReviewWritingError("参考文献目录运行不可用。")
    catalog = _read_json(run_dir / "output" / "references.json")
    years: dict[str, int] = {}
    for row in catalog.get("references", []):
        paper_id = str(row.get("paper_id", ""))
        year = row.get("year")
        if paper_id in paper_ids and isinstance(year, int):
            years[paper_id] = year
    missing = sorted(paper_ids - set(years))
    if missing:
        raise HierarchicalReviewWritingError(f"证据更新缺少论文年份：{missing}")
    return years


def _render_argument_plan(value: dict[str, Any], source: dict[str, Any]) -> str:
    citation_map = {row["citation_key"]: row for row in source["citation_map"]}
    lines = ["# 综述论点与证据更新计划", "", "> 本表是正文生成前的证据决策记录。", ""]
    for chapter in value["chapters"]:
        lines.extend([f"## {chapter['chapter_index']} {chapter['title']}", "", f"**章节主张：** {chapter['thesis']}", ""])
        for argument in chapter["arguments"]:
            boundary = argument["boundary"] if argument["boundary_required"] else "不需要在正文追加边界句"
            lines.extend([
                f"### {argument['argument_id']} {argument['conclusion']}", "",
                f"- 事实类型：`{argument['fact_kind']}`",
                f"- 更新判断：{argument['update_rationale']}",
                f"- 边界：{boundary}",
                "- 证据：",
            ])
            for evidence in argument["evidence"]:
                paper = citation_map[evidence["citation_key"]]
                lines.append(f"  - {paper['paper_title']}（{paper['publication_year']}）：`{evidence['role']}`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render(value: dict[str, Any], source: dict[str, Any]) -> str:
    numbers = {row["citation_key"]: i for i, row in enumerate(source["citation_map"], 1)}
    def paragraph(row: dict[str, Any]) -> str:
        refs = ",".join(str(numbers[key]) for key in row["citation_keys"])
        return f"{row['text']}[{refs}]" if refs else row["text"]
    lines = [
        f"# {value['title']}",
        "",
        f"> **时间范围声明：** {value['temporal_scope_statement']}",
        "",
        "## 摘要",
        "",
        value["abstract"],
        "",
        "**关键词：**" + "；".join(value["keywords"]),
        "",
        "## 引言",
        "",
    ]
    lines.extend(sum(([paragraph(row), ""] for row in value["introduction"]), []))
    for chapter in value["chapters"]:
        lines.extend([f"## {chapter['chapter_index']} {chapter['title']}", ""])
        lines.extend(sum(([paragraph(row), ""] for row in chapter["paragraphs"]), []))
    lines.extend(["## 讨论与研究展望", ""])
    lines.extend(sum(([paragraph(row), ""] for row in value["discussion"]), []))
    lines.extend(["## 结论", ""])
    lines.extend(sum(([paragraph(row), ""] for row in value["conclusion"]), []))
    lines.extend(["## 参考文献", ""])
    for i, row in enumerate(source["citation_map"], 1):
        lines.append(f"[{i}] {row['paper_title']}（本地题录元数据待核验）")
    return "\n".join(lines).rstrip() + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
