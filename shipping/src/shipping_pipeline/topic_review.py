from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .llm_analysis import (
    AnalysisInputError,
    AnalysisInputSnapshot,
    DEFAULT_MODEL_PROFILE,
    DEFAULT_TOKENIZER_CACHE,
    create_input_snapshot,
)
from .llm_contracts import (
    ContractViolation,
    assign_evidence_unit_ids,
    build_evidence_batch_schema,
    validate_evidence_batch,
)
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_json_stage import execute_json_stage
from .llm_planner import EVIDENCE_SYSTEM_PROMPT, build_evidence_prompts
from .llm_projection import project_cards
from .llm_provider import (
    OpenAICompatibleAnalysisClient,
)
from .llm_quotes import build_quote_candidates
from .llm_tokenizer import DeepSeekV4TokenCounter
from .topic_review_contracts import (
    TopicReviewConfig,
    TopicReviewContractError,
    build_topic_brief_schema,
    build_topic_revisit_schema,
    build_topic_scope_schema,
    load_topic_review_config,
    validate_topic_brief,
    validate_topic_revisit,
    validate_topic_scope,
)
from .topic_review_report import render_topic_review_report


TOPIC_REVIEW_RUN_SCHEMA_VERSION = "llm.topic_review_run.v2"
TOPIC_EXCLUSION_SCHEMA_VERSION = "llm.topic_exclusion.v1"
TOPIC_PAPER_RESULT_SCHEMA_VERSION = "llm.topic_paper_result.v2"
TOPIC_REVISIT_RESULT_SCHEMA_VERSION = "llm.topic_revisit_result.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOPIC_REVIEW_CONFIG = PROJECT_ROOT / "config" / "topic-review-default.json"

SCOPE_SYSTEM_PROMPT = """你是面向特定综述主题的论文定界器。你的输入只来自同一篇论文。
这不是完整论文解析任务，也不是证据抽取任务。只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。
先判断整篇论文对当前综述主题属于 core、supporting、peripheral 或 exclude，再只选择最值得进入严格证据抽取的少量 Card。
必须按照最终 paper_relevance 对应的 selected_materials 上限计数；不得把 core 的上限用于 supporting 或 peripheral。
不要为了覆盖论文结构而多选。优先选择能直接支持主题判断、主要发现、关键方法、重要比较或明确限制的 Card。
解析标记严重且存在更清楚替代材料时不要选择。只有事实对主题重要且没有替代来源时，才选择带解析标记的 Card。
selected_materials 只列入选 Card；不要为未入选 Card 逐项输出决定。material_id 必须从输入 Card 原样复制完整字符串，不得改写行号、合并相邻 Card 的行号或生成输入中不存在的 material_id。"""

SCOPED_EVIDENCE_SYSTEM_PROMPT = EVIDENCE_SYSTEM_PROMPT + """
当前输入 Card 已通过论文级主题定界。每张 Card 必须输出 disposition=evidence、reason_code=direct_evidence，并提取1至指定上限条最能服务当前综述主题的原子 Evidence。
不要覆盖 Card 的全部事实，不要抽取只因存在于原文但对主题用途不明确的细节。
主题定界只提供相关性和预期用途标签，不是事实来源；claim 只能来自当前 Card 的逐字引文候选。
原文数字或关键对象因解析损坏而缺失时，不得用省略号、占位符或推测内容生成 Evidence；应选择 Card 中另一条完整且相关的事实。"""

BRIEF_SYSTEM_PROMPT = """你是面向文献综述写作的单篇论文材料整理器。你的输入只来自同一篇论文已经通过代码合同验证的 Evidence。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。
围绕给定综述主题形成简短、可直接审核的材料简报，不要复述全部 Evidence。
每条陈述必须引用促成该判断的 evidence_unit_id。不得增加 Evidence 中没有的具体事实、数字、对象或因果关系。
topic_contribution 概括这篇论文对当前主题最直接的贡献；key_points 只保留最有写作用途的观点，每条只能引用一个 Evidence，statement 必须逐字复制该 Evidence 的 claim，不得改写、合并或推导；review_uses 说明可如何用于综述；cautions 只保留确有证据依据的限制。topic_contribution、review_uses 和 cautions 是导航文字，其中的每个具体数字都必须原样出现于该条所引 Evidence 的 claim 或引文中，不得引入无证据数字。
key_points.point_type 只能是 background、definition、method、finding、result、data、mechanism、argument、recommendation、limitation 之一；禁止输出 fact、support 或其他近义标签。所有 evidence_unit_ids 必须输出为 JSON 数组；即使只有一个 ID，也必须写成 ["evidence_..."]，不得写成字符串。
evidence_gaps 最多列出2个当前已验证 Evidence 尚未回答、但对综述确有价值的具体问题；它们是后续自动回查指令，不是论文事实，不得把已有 Evidence 已经回答的问题伪装成缺口。问题中的具体数字必须原样存在于本篇已验证 Evidence 中，不得加入输入没有出现的具体数字、对象或结论，也不得为了举例自行添加假设数字；需要泛化时写“其他规模”“其他年份”等无数字表述。没有明确缺口时输出空数组。
不得输出 schema 之外的字段。"""

REVISIT_SYSTEM_PROMPT = """你是面向特定综述主题的论文遗漏检查器。输入只来自同一篇论文。
当前已有一组通过严格引文合同的 Evidence；你的任务不是重新概括论文，而是检查未入选 Card 中是否存在能够回答给定证据缺口、或提供当前 Evidence 未覆盖的重要独立维度的少量材料。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。最多选择给定上限内的 Card，也允许明确返回 no_candidate。
只有内容能直接服务综述主题、相对当前 Evidence 有实质新增且原文足够完整时才能选择。不得仅因措辞不同而选择重复内容，不得生成输入中不存在的 material_id。
selection_reason 和 novelty_reason 只用于选择审计，不会成为事实来源；后续 Evidence claim 仍只能来自所选 Card 的逐字引文。"""


class TopicReviewRunner:
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
        paper_id: str,
        topic: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 120,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        review_config_path: str | Path = DEFAULT_TOPIC_REVIEW_CONFIG,
        workspace_paper_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = self.workspace / "_topic_reviews" / "runs" / resolved_run_id
        if run_dir.exists():
            raise AnalysisInputError(
                f"主题简报 run_id 已存在，不能覆盖不可变运行：{resolved_run_id}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot: AnalysisInputSnapshot | None = None
        scope: dict[str, Any] = {}
        brief: dict[str, Any] | None = None
        evidence_units: list[dict[str, Any]] = []
        selected_ids: list[str] = []
        initial_selected_ids: list[str] = []
        omitted_ids: list[str] = []
        evidence_failures: list[dict[str, Any]] = []
        request_results: list[dict[str, Any]] = []
        failure: dict[str, Any] | None = None
        revisit: dict[str, Any] | None = None
        profile: ModelProfile | None = None
        config: TopicReviewConfig | None = None
        try:
            snapshot = create_input_snapshot(
                self.workspace,
                paper_id=paper_id,
                topic=topic,
                workspace_paper_id=workspace_paper_id,
            )
            profile = load_model_profile(Path(model_profile_path))
            config = load_topic_review_config(Path(review_config_path))
            if provider != profile.provider:
                raise AnalysisInputError(
                    f"provider 与 model profile 不一致：provider={provider!r}, "
                    f"profile={profile.provider!r}。"
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
            projected = project_cards(snapshot.materials)
            _write_input_artifacts(run_dir, snapshot, projected, profile, config)

            scope_projected, scope_alias_to_material_id = _build_scope_projection(
                projected
            )
            scope_system, scope_user = build_topic_scope_prompts(
                snapshot,
                scope_projected,
                config,
            )
            scope_parsed, scope_result = _execute_json_stage(
                run_dir=run_dir,
                stage="scope",
                system_prompt=scope_system,
                user_prompt=scope_user,
                max_output_tokens=config.scope_max_output_tokens,
                context={
                    "paper_id": snapshot.paper_id,
                    "topic": snapshot.topic,
                    "material_ids": [
                        str(row["material_id"]) for row in scope_projected
                    ],
                    "scope_alias_to_material_id": scope_alias_to_material_id,
                },
                client=client,
                profile=profile,
                token_counter=token_counter,
            )
            request_results.append(scope_result)
            aliased_scope = validate_topic_scope(
                scope_parsed,
                {str(row["material_id"]) for row in scope_projected},
                config,
            )
            scope = {
                **aliased_scope,
                "selected_materials": [
                    {
                        **row,
                        "material_id": scope_alias_to_material_id[
                            str(row["material_id"])
                        ],
                    }
                    for row in aliased_scope["selected_materials"]
                ],
            }
            _write_json(run_dir / "scope" / "validated_scope.json", scope)
            selected_ids = [str(row["material_id"]) for row in scope["selected_materials"]]
            initial_selected_ids = list(selected_ids)
            selected_set = set(selected_ids)
            omitted_ids = [
                str(row["material_id"])
                for row in projected
                if str(row["material_id"]) not in selected_set
            ]
            _write_jsonl(
                run_dir / "audit" / "initial_omitted_materials.jsonl",
                _build_omitted_rows(projected, omitted_ids),
            )

            if scope["paper_relevance"] == "exclude":
                _write_jsonl(
                    run_dir / "audit" / "omitted_materials.jsonl",
                    _build_omitted_rows(projected, omitted_ids),
                )
                exclusion = {
                    "schema_version": TOPIC_EXCLUSION_SCHEMA_VERSION,
                    "paper_id": snapshot.paper_id,
                    "paper_title": snapshot.paper_title,
                    "topic": snapshot.topic,
                    "paper_relevance": "exclude",
                    "relevance_reason": scope["relevance_reason"],
                    "topic_summary": scope["topic_summary"],
                    "selected_material_count": 0,
                    "omitted_material_count": len(omitted_ids),
                }
                _write_json(run_dir / "output" / "paper_brief.json", exclusion)
                _write_review(
                    run_dir,
                    snapshot,
                    scope,
                    None,
                    [],
                    selected_ids,
                    omitted_ids,
                    status="excluded",
                    initial_selected_ids=initial_selected_ids,
                    revisit=revisit,
                    evidence_failures=[],
                )
                manifest = _build_manifest(
                    run_id=resolved_run_id,
                    status="excluded",
                    started_at=started_at,
                    snapshot=snapshot,
                    profile=profile,
                    config=config,
                    scope=scope,
                    selected_ids=selected_ids,
                    omitted_ids=omitted_ids,
                    evidence_units=[],
                    request_results=request_results,
                    initial_selected_ids=initial_selected_ids,
                    revisit=revisit,
                    evidence_failures=[],
                )
                _write_json(run_dir / "manifest.json", manifest)
                return _result(run_dir, manifest)

            materials_by_id = {
                str(row["material_id"]): row for row in snapshot.materials
            }
            selected_materials = [materials_by_id[material_id] for material_id in selected_ids]
            selections_by_id = {
                str(row["material_id"]): row for row in scope["selected_materials"]
            }
            (
                assessments,
                evidence_units,
                initial_evidence_results,
                evidence_failures,
            ) = _extract_evidence(
                run_dir=run_dir,
                directory_prefix="evidence/initial",
                snapshot=snapshot,
                materials=selected_materials,
                all_projected=projected,
                selections_by_id=selections_by_id,
                scope=scope,
                config=config,
                client=client,
                profile=profile,
                token_counter=token_counter,
                continue_on_contract_failure=True,
            )
            request_results.extend(initial_evidence_results)
            _write_jsonl(
                run_dir / "audit" / "evidence_failures.jsonl",
                evidence_failures,
            )
            if not evidence_units:
                raise TopicReviewContractError(
                    "evidence.no_valid_units",
                    "所有首次入选 Card 的证据抽取均违反合同，没有可用于生成简报的 Evidence。",
                )
            _write_jsonl(
                run_dir / "evidence" / "material_assessments.jsonl",
                assessments,
            )
            _write_jsonl(
                run_dir / "evidence" / "evidence_units.jsonl",
                evidence_units,
            )

            brief_system, brief_user = build_topic_brief_prompts(
                snapshot=snapshot,
                scope=scope,
                evidence_units=evidence_units,
                config=config,
            )
            brief_parsed, brief_result = _execute_json_stage(
                run_dir=run_dir,
                stage="brief",
                directory_name="brief/base",
                system_prompt=brief_system,
                user_prompt=brief_user,
                max_output_tokens=config.brief_max_output_tokens,
                context={
                    "paper_id": snapshot.paper_id,
                    "topic": snapshot.topic,
                    "paper_relevance": scope["paper_relevance"],
                    "evidence_unit_ids": [
                        str(row["evidence_unit_id"]) for row in evidence_units
                    ],
                },
                client=client,
                profile=profile,
                token_counter=token_counter,
            )
            request_results.append(brief_result)
            brief = validate_topic_brief(
                brief_parsed,
                evidence_units,
                relevance=str(scope["paper_relevance"]),
                config=config,
            )
            _write_json(run_dir / "brief" / "base" / "validated_brief.json", brief)
            relevance = str(scope["paper_relevance"])
            gaps = _build_revisit_gaps(
                brief,
                selection_saturated=(
                    len(initial_selected_ids)
                    >= config.max_selected_materials[relevance]
                ),
            )
            revisit_limit = config.max_revisit_materials[relevance]
            revisit = {
                "schema_version": TOPIC_REVISIT_RESULT_SCHEMA_VERSION,
                "status": "not_triggered",
                "trigger_reasons": list(
                    dict.fromkeys(str(row["source"]) for row in gaps)
                ),
                "gaps": gaps,
                "scanned_material_count": 0,
                "review_summary": "当前论文不满足自动回查条件。",
                "candidate_materials": [],
                "recovered_material_ids": [],
                "recovered_evidence_unit_ids": [],
                "remaining_evidence_gaps": list(brief["evidence_gaps"]),
                "failure": None,
            }
            if revisit_limit > 0 and omitted_ids and gaps:
                revisit["status"] = "running"
                revisit["scanned_material_count"] = len(omitted_ids)
                omitted_set = set(omitted_ids)
                omitted_projected = [
                    row
                    for row in projected
                    if str(row["material_id"]) in omitted_set
                ]
                revisit_evidence_units: list[dict[str, Any]] = []
                revisit_assessments: list[dict[str, Any]] = []
                revisit_selection: dict[str, Any] | None = None
                try:
                    revisit_system, revisit_user = build_topic_revisit_prompts(
                        snapshot=snapshot,
                        omitted_projected=omitted_projected,
                        evidence_units=evidence_units,
                        gaps=gaps,
                        max_materials=revisit_limit,
                    )
                    revisit_parsed, revisit_result = _execute_json_stage(
                        run_dir=run_dir,
                        stage="revisit",
                        directory_name="revisit/scope",
                        system_prompt=revisit_system,
                        user_prompt=revisit_user,
                        max_output_tokens=config.revisit_max_output_tokens,
                        context={
                            "paper_id": snapshot.paper_id,
                            "topic": snapshot.topic,
                            "material_ids": list(omitted_ids),
                            "gap_ids": [str(row["gap_id"]) for row in gaps],
                        },
                        client=client,
                        profile=profile,
                        token_counter=token_counter,
                    )
                    request_results.append(revisit_result)
                    revisit_selection = validate_topic_revisit(
                        revisit_parsed,
                        omitted_set,
                        {str(row["gap_id"]) for row in gaps},
                        max_materials=revisit_limit,
                    )
                    _write_json(
                        run_dir / "revisit" / "validated_selection.json",
                        revisit_selection,
                    )
                    revisit["review_summary"] = revisit_selection["review_summary"]
                    revisit["candidate_materials"] = list(
                        revisit_selection["selected_materials"]
                    )
                    if revisit_selection["status"] == "no_candidate":
                        revisit["status"] = "no_candidate"
                    else:
                        revisit_material_ids = [
                            str(row["material_id"])
                            for row in revisit_selection["selected_materials"]
                        ]
                        revisit_materials = [
                            materials_by_id[material_id]
                            for material_id in revisit_material_ids
                        ]
                        revisit_selections_by_id = {
                            str(row["material_id"]): {
                                "material_id": str(row["material_id"]),
                                "priority": "secondary",
                                "intended_use": str(row["intended_use"]),
                            }
                            for row in revisit_selection["selected_materials"]
                        }
                        (
                            revisit_assessments,
                            revisit_evidence_units,
                            revisit_evidence_results,
                            _,
                        ) = _extract_evidence(
                            run_dir=run_dir,
                            directory_prefix="evidence/revisit",
                            snapshot=snapshot,
                            materials=revisit_materials,
                            all_projected=projected,
                            selections_by_id=revisit_selections_by_id,
                            scope=scope,
                            config=config,
                            client=client,
                            profile=profile,
                            token_counter=token_counter,
                        )
                        request_results.extend(revisit_evidence_results)
                        _write_jsonl(
                            run_dir / "evidence" / "revisit_material_assessments.jsonl",
                            revisit_assessments,
                        )
                        _write_jsonl(
                            run_dir / "evidence" / "revisit_evidence_units.jsonl",
                            revisit_evidence_units,
                        )
                        candidate_evidence_units = [
                            *evidence_units,
                            *revisit_evidence_units,
                        ]
                        final_brief_system, final_brief_user = build_topic_brief_prompts(
                            snapshot=snapshot,
                            scope=scope,
                            evidence_units=candidate_evidence_units,
                            config=config,
                            required_key_point_evidence_ids=[
                                str(row["evidence_unit_id"])
                                for row in revisit_evidence_units
                            ],
                        )
                        final_brief_parsed, final_brief_result = _execute_json_stage(
                            run_dir=run_dir,
                            stage="brief",
                            directory_name="brief/final",
                            system_prompt=final_brief_system,
                            user_prompt=final_brief_user,
                            max_output_tokens=config.brief_max_output_tokens,
                            context={
                                "paper_id": snapshot.paper_id,
                                "topic": snapshot.topic,
                                "paper_relevance": relevance,
                                "evidence_unit_ids": [
                                    str(row["evidence_unit_id"])
                                    for row in candidate_evidence_units
                                ],
                            },
                            client=client,
                            profile=profile,
                            token_counter=token_counter,
                        )
                        request_results.append(final_brief_result)
                        final_brief = validate_topic_brief(
                            final_brief_parsed,
                            candidate_evidence_units,
                            relevance=relevance,
                            config=config,
                            required_key_point_evidence_ids=[
                                str(row["evidence_unit_id"])
                                for row in revisit_evidence_units
                            ],
                        )
                        _validate_revisit_brief_uses_recovered(
                            final_brief,
                            revisit_evidence_units,
                        )
                        _write_json(
                            run_dir / "brief" / "final" / "validated_brief.json",
                            final_brief,
                        )
                        recovered_set = set(revisit_material_ids)
                        selected_ids = [*initial_selected_ids, *revisit_material_ids]
                        omitted_ids = [
                            material_id
                            for material_id in omitted_ids
                            if material_id not in recovered_set
                        ]
                        assessments = [*assessments, *revisit_assessments]
                        evidence_units = candidate_evidence_units
                        brief = final_brief
                        revisit["status"] = "completed"
                        revisit["recovered_material_ids"] = revisit_material_ids
                        revisit["recovered_evidence_unit_ids"] = [
                            str(row["evidence_unit_id"])
                            for row in revisit_evidence_units
                        ]
                        revisit["remaining_evidence_gaps"] = list(
                            final_brief["evidence_gaps"]
                        )
                        _write_jsonl(
                            run_dir / "evidence" / "material_assessments.jsonl",
                            assessments,
                        )
                        _write_jsonl(
                            run_dir / "evidence" / "evidence_units.jsonl",
                            evidence_units,
                        )
                except Exception as exc:
                    request_results = _collect_stage_results(run_dir, request_results)
                    revisit_failure = _failure("revisit", exc)
                    _write_jsonl(
                        run_dir / "audit" / "revisit_failures.jsonl",
                        [revisit_failure],
                    )
                    revisit["status"] = "failed"
                    revisit["failure"] = revisit_failure
                    revisit["partial_evidence_unit_ids"] = [
                        str(row["evidence_unit_id"])
                        for row in revisit_evidence_units
                    ]
                    if revisit_selection is not None:
                        revisit["review_summary"] = revisit_selection["review_summary"]
                        revisit["candidate_materials"] = list(
                            revisit_selection["selected_materials"]
                        )
            elif not omitted_ids:
                revisit["review_summary"] = "首次定界已经选择全部 Card，无材料可回查。"
            elif revisit_limit == 0:
                revisit["review_summary"] = "当前相关性级别未启用自动回查。"
            else:
                revisit["review_summary"] = "初版简报没有证据缺口，且首次选卡未达到上限。"
            _write_json(run_dir / "revisit" / "result.json", revisit)
            _write_jsonl(
                run_dir / "audit" / "omitted_materials.jsonl",
                _build_omitted_rows(projected, omitted_ids),
            )
            current = create_input_snapshot(
                self.workspace,
                paper_id=snapshot.paper_id,
                topic=snapshot.topic,
                workspace_paper_id=workspace_paper_id,
            )
            if (
                current.generation_id != snapshot.generation_id
                or current.input_sha256 != snapshot.input_sha256
            ):
                raise AnalysisInputError("主题简报运行期间论文 Card generation 已变化。")
            output = {
                "schema_version": TOPIC_PAPER_RESULT_SCHEMA_VERSION,
                "paper_id": snapshot.paper_id,
                "paper_title": snapshot.paper_title,
                "topic": snapshot.topic,
                "paper_relevance": scope["paper_relevance"],
                "scope": scope,
                "brief": brief,
                "revisit": revisit,
                "evidence_failures": evidence_failures,
            }
            _write_json(run_dir / "output" / "paper_brief.json", output)
            _write_review(
                run_dir,
                snapshot,
                scope,
                brief,
                evidence_units,
                selected_ids,
                omitted_ids,
                status=_completed_status(evidence_failures, revisit),
                initial_selected_ids=initial_selected_ids,
                revisit=revisit,
                evidence_failures=evidence_failures,
            )
            final_status = _completed_status(evidence_failures, revisit)
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status=final_status,
                started_at=started_at,
                snapshot=snapshot,
                profile=profile,
                config=config,
                scope=scope,
                selected_ids=selected_ids,
                omitted_ids=omitted_ids,
                evidence_units=evidence_units,
                request_results=request_results,
                initial_selected_ids=initial_selected_ids,
                revisit=revisit,
                evidence_failures=evidence_failures,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)
        except Exception as exc:
            request_results = _collect_stage_results(run_dir, request_results)
            failure = _failure(
                _infer_failure_stage(run_dir),
                exc,
            )
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            if snapshot is not None:
                if not omitted_ids:
                    all_ids = [str(row["material_id"]) for row in snapshot.materials]
                    omitted_ids = [row for row in all_ids if row not in set(selected_ids)]
                _write_review(
                    run_dir,
                    snapshot,
                    scope,
                    brief,
                    evidence_units,
                    selected_ids,
                    omitted_ids,
                    status="failed",
                    failure=failure,
                    initial_selected_ids=initial_selected_ids,
                    revisit=revisit,
                    evidence_failures=evidence_failures,
                )
            manifest = _build_failure_manifest(
                run_id=resolved_run_id,
                started_at=started_at,
                paper_id=paper_id,
                topic=topic,
                snapshot=snapshot,
                profile=profile,
                config=config,
                scope=scope,
                selected_ids=selected_ids,
                omitted_ids=omitted_ids,
                evidence_units=evidence_units,
                request_results=request_results,
                failure=failure,
                initial_selected_ids=initial_selected_ids,
                revisit=revisit,
                evidence_failures=evidence_failures,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def _build_scope_projection(
    projected_cards: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    alias_to_material_id: dict[str, str] = {}
    aliased: list[dict[str, Any]] = []
    for index, row in enumerate(projected_cards, start=1):
        alias = f"card_{index:04d}"
        alias_to_material_id[alias] = str(row["material_id"])
        aliased.append({**row, "material_id": alias})
    return aliased, alias_to_material_id


def _completed_status(
    evidence_failures: list[dict[str, Any]],
    revisit: dict[str, Any] | None,
) -> str:
    if evidence_failures:
        return "completed_with_failures"
    if revisit is not None and revisit.get("status") == "failed":
        return "completed_with_revisit_failure"
    return "completed"


def build_topic_scope_prompts(
    snapshot: AnalysisInputSnapshot,
    projected_cards: list[dict[str, Any]],
    config: TopicReviewConfig,
) -> tuple[str, str]:
    schema = build_topic_scope_schema(
        [str(row["material_id"]) for row in projected_cards],
        config=config,
    )
    payload = {
        "任务": "围绕综述主题判断论文相关性，并只选择少量值得严格取证的 Card",
        "论文": {
            "paper_id": snapshot.paper_id,
            "paper_title": snapshot.paper_title,
        },
        "综述主题": snapshot.topic,
        "选择上限": config.max_selected_materials,
        "输出JSONSchema": schema,
        "材料卡": projected_cards,
    }
    return SCOPE_SYSTEM_PROMPT, _compact_json(payload)


def build_scoped_evidence_prompts(
    *,
    snapshot: AnalysisInputSnapshot,
    selected_projected: list[dict[str, Any]],
    all_projected: list[dict[str, Any]],
    scope: dict[str, Any],
    config: TopicReviewConfig,
) -> tuple[str, str, dict[str, Any]]:
    quote_candidates = build_quote_candidates(selected_projected)
    material_ids = [str(row["material_id"]) for row in selected_projected]
    quote_ids = [str(row["quote_id"]) for row in quote_candidates]
    quote_ids_by_material = {
        material_id: [
            str(row["quote_id"])
            for row in quote_candidates
            if str(row["material_id"]) == material_id
        ]
        for material_id in material_ids
    }
    schema = build_evidence_batch_schema(
        len(material_ids),
        material_ids=material_ids,
        quote_candidate_ids=quote_ids,
        quote_candidate_ids_by_material=quote_ids_by_material,
    )
    result_schema = schema["properties"]["material_results"]["items"]
    result_schema["properties"]["disposition"] = {"const": "evidence"}
    result_schema["properties"]["reason_code"] = {"const": "direct_evidence"}
    result_schema["properties"]["evidence_units"]["minItems"] = 1
    result_schema["properties"]["evidence_units"]["maxItems"] = (
        config.max_evidence_units_per_material
    )
    _, base_user = build_evidence_prompts(
        paper_id=snapshot.paper_id,
        paper_title=snapshot.paper_title,
        topic=snapshot.topic,
        projected_cards=selected_projected,
        all_projected_cards=all_projected,
    )
    payload = json.loads(base_user)
    payload["任务"] = "只从已入选 Card 中抽取少量、直接服务综述主题的证据单元"
    selected_labels = [
        {
            "material_id": row["material_id"],
            "priority": row["priority"],
            "intended_use": row["intended_use"],
        }
        for row in scope["selected_materials"]
    ]
    payload["主题定界标签"] = {
        "paper_relevance": scope["paper_relevance"],
        "selected_materials": selected_labels,
    }
    payload["每张Card证据上限"] = config.max_evidence_units_per_material
    payload["输出JSONSchema"] = schema
    return SCOPED_EVIDENCE_SYSTEM_PROMPT, _compact_json(payload), schema


def build_topic_brief_prompts(
    *,
    snapshot: AnalysisInputSnapshot,
    scope: dict[str, Any],
    evidence_units: list[dict[str, Any]],
    config: TopicReviewConfig,
    required_key_point_evidence_ids: list[str] | None = None,
) -> tuple[str, str]:
    evidence_projection = []
    for unit in evidence_units:
        evidence_projection.append(
            {
                "evidence_unit_id": unit["evidence_unit_id"],
                "evidence_type": unit["evidence_type"],
                "claim": unit["claim"],
                "confidence": unit["confidence"],
                "citations": [
                    {
                        "card_title": row.get("card_title"),
                        "quote": row.get("quote"),
                    }
                    for row in unit["citations"]
                ],
            }
        )
    schema = build_topic_brief_schema(
        [str(row["evidence_unit_id"]) for row in evidence_units],
        relevance=str(scope["paper_relevance"]),
        config=config,
        evidence_claims_by_id={
            str(row["evidence_unit_id"]): str(row["claim"])
            for row in evidence_units
        },
        required_key_point_evidence_ids=required_key_point_evidence_ids,
    )
    payload = {
        "任务": "把少量已验证证据整理成面向综述写作的单篇论文材料简报",
        "论文": {
            "paper_id": snapshot.paper_id,
            "paper_title": snapshot.paper_title,
        },
        "综述主题": snapshot.topic,
        "主题定界标签": {
            "paper_relevance": scope["paper_relevance"],
        },
        "观点数量上限": config.max_brief_points[str(scope["paper_relevance"])],
        "必须进入主要观点的回查EvidenceID": list(
            required_key_point_evidence_ids or []
        ),
        "输出JSONSchema": schema,
        "已验证Evidence": evidence_projection,
    }
    return BRIEF_SYSTEM_PROMPT, _compact_json(payload)


def build_topic_revisit_prompts(
    *,
    snapshot: AnalysisInputSnapshot,
    omitted_projected: list[dict[str, Any]],
    evidence_units: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    max_materials: int,
) -> tuple[str, str]:
    schema = build_topic_revisit_schema(
        [str(row["material_id"]) for row in omitted_projected],
        [str(row["gap_id"]) for row in gaps],
        max_materials=max_materials,
    )
    evidence_projection = [
        {
            "evidence_unit_id": str(row["evidence_unit_id"]),
            "evidence_type": str(row["evidence_type"]),
            "claim": str(row["claim"]),
        }
        for row in evidence_units
    ]
    payload = {
        "任务": "回查未入选 Card，只选择能填补证据缺口或提供重要新增维度的少量材料",
        "论文": {
            "paper_id": snapshot.paper_id,
            "paper_title": snapshot.paper_title,
        },
        "综述主题": snapshot.topic,
        "回查问题": gaps,
        "当前已验证Evidence": evidence_projection,
        "补选上限": max_materials,
        "输出JSONSchema": schema,
        "未入选材料卡": omitted_projected,
    }
    return REVISIT_SYSTEM_PROMPT, _compact_json(payload)


def _build_revisit_gaps(
    brief: dict[str, Any],
    *,
    selection_saturated: bool,
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for row in brief.get("evidence_gaps", []):
        gap_type = str(row["gap_type"])
        question = str(row["question"])
        gap_id = "gap_" + hashlib.sha256(
            (gap_type + "\n" + " ".join(question.split())).encode("utf-8")
        ).hexdigest()[:16]
        gaps.append(
            {
                "gap_id": gap_id,
                "source": "brief_evidence_gap",
                "gap_type": gap_type,
                "question": question,
                "why_it_matters": str(row["why_it_matters"]),
            }
        )
    if selection_saturated and not gaps:
        gaps.append(
            {
                "gap_id": "gap_selection_cap_challenge",
                "source": "selection_cap_reached",
                "gap_type": "comparison",
                "question": "未入选材料中是否存在当前证据尚未覆盖、但对综述主题具有实质价值的重要独立维度？",
                "why_it_matters": "首次选卡已达到配置上限，需要检查是否因名额限制遗漏更有价值的材料。",
            }
        )
    return gaps


def _extract_evidence(
    *,
    run_dir: Path,
    directory_prefix: str,
    snapshot: AnalysisInputSnapshot,
    materials: list[dict[str, Any]],
    all_projected: list[dict[str, Any]],
    selections_by_id: dict[str, dict[str, Any]],
    scope: dict[str, Any],
    config: TopicReviewConfig,
    client: Any,
    profile: ModelProfile,
    token_counter: Any,
    continue_on_contract_failure: bool = False,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    assessments: list[dict[str, Any]] = []
    evidence_units: list[dict[str, Any]] = []
    request_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, material in enumerate(materials, start=1):
        material_id = str(material["material_id"])
        one_material = [material]
        one_projected = project_cards(one_material)
        quote_candidates = build_quote_candidates(one_projected)
        card_scope = {
            **scope,
            "selected_materials": [selections_by_id[material_id]],
        }
        evidence_system, evidence_user, evidence_schema = build_scoped_evidence_prompts(
            snapshot=snapshot,
            selected_projected=one_projected,
            all_projected=all_projected,
            scope=card_scope,
            config=config,
        )
        evidence_parsed, evidence_result = _execute_json_stage(
            run_dir=run_dir,
            stage="evidence",
            directory_name=f"{directory_prefix}/card_{index:03d}",
            system_prompt=evidence_system,
            user_prompt=evidence_user,
            max_output_tokens=min(
                profile.evidence_output_tokens(1),
                profile.evidence_max_output_tokens,
            ),
            context={
                "paper_id": snapshot.paper_id,
                "topic": snapshot.topic,
                "evidence_phase": (
                    "revisit" if directory_prefix.endswith("revisit") else "initial"
                ),
                "material_ids": [material_id],
                "materials": one_material,
                "quote_candidates": quote_candidates,
            },
            client=client,
            profile=profile,
            token_counter=token_counter,
        )
        request_results.append(evidence_result)
        try:
            _validate_json_schema(
                evidence_parsed,
                evidence_schema,
                code="schema.scoped_evidence_invalid",
            )
            normalized = validate_evidence_batch(
                evidence_parsed,
                one_material,
                quote_candidates,
            )
            assigned = assign_evidence_unit_ids(
                evidence_result["request_id"],
                normalized["evidence_units"],
                one_material,
                generation_id=snapshot.generation_id,
            )
        except (ContractViolation, TopicReviewContractError) as exc:
            if not continue_on_contract_failure:
                raise
            failures.append(
                {
                    **_failure("evidence", exc),
                    "material_id": material_id,
                    "card_index": index,
                    "card_title": str(material.get("clean_title") or material.get("title") or ""),
                    "source_span": material.get("source_span"),
                }
            )
            continue
        assessments.extend(normalized["material_assessments"])
        evidence_units.extend(assigned)
    return assessments, evidence_units, request_results, failures


def _validate_revisit_brief_uses_recovered(
    brief: dict[str, Any],
    recovered_evidence_units: list[dict[str, Any]],
) -> None:
    recovered_ids = {
        str(row["evidence_unit_id"])
        for row in recovered_evidence_units
    }
    used_ids = {
        str(evidence_id)
        for row in brief["key_points"]
        for evidence_id in row["evidence_unit_ids"]
    }
    missing = sorted(recovered_ids - used_ids)
    if missing:
        raise TopicReviewContractError(
            "revisit.recovered_evidence_not_used",
            f"最终简报没有采用全部回查证据：{missing}",
            path="$.key_points",
        )


def _execute_json_stage(
    *,
    run_dir: Path,
    stage: str,
    directory_name: str | None = None,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    context: dict[str, Any],
    client: Any,
    profile: ModelProfile,
    token_counter: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return execute_json_stage(
        run_dir=run_dir,
        stage=stage,
        task_name=f"topic_review_{stage}",
        directory_name=directory_name,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_output_tokens=max_output_tokens,
        context=context,
        client=client,
        profile=profile,
        token_counter=token_counter,
    )


def _validate_json_schema(payload: object, schema: dict[str, Any], *, code: str) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    path = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in error.absolute_path
    )
    raise ContractViolation(code, error.message, path=path)


def _write_input_artifacts(
    run_dir: Path,
    snapshot: AnalysisInputSnapshot,
    projected: list[dict[str, Any]],
    profile: ModelProfile,
    config: TopicReviewConfig,
) -> None:
    _write_json(
        run_dir / "input" / "paper.json",
        {
            "paper_id": snapshot.paper_id,
            "paper_title": snapshot.paper_title,
            "topic": snapshot.topic,
            "generation_id": snapshot.generation_id,
            "input_sha256": snapshot.input_sha256,
            "material_count": len(snapshot.materials),
        },
    )
    _write_jsonl(run_dir / "input" / "materials.jsonl", list(snapshot.materials))
    _write_jsonl(run_dir / "input" / "projected_cards.jsonl", projected)
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(run_dir / "input" / "review_config.json", config.to_dict())


def _build_omitted_rows(
    projected: list[dict[str, Any]],
    omitted_ids: list[str],
) -> list[dict[str, Any]]:
    omitted_set = set(omitted_ids)
    return [
        {
            "material_id": str(row["material_id"]),
            "title": str(row["title"]),
            "heading_path": list(row["heading_path"]),
            "parse_flags": list(row["parse_flags"]),
            "reason_code": "not_selected_by_topic_scope",
        }
        for row in projected
        if str(row["material_id"]) in omitted_set
    ]


def _write_review(
    run_dir: Path,
    snapshot: AnalysisInputSnapshot,
    scope: dict[str, Any],
    brief: dict[str, Any] | None,
    evidence_units: list[dict[str, Any]],
    selected_ids: list[str],
    omitted_ids: list[str],
    *,
    status: str,
    failure: dict[str, Any] | None = None,
    initial_selected_ids: list[str],
    revisit: dict[str, Any] | None,
    evidence_failures: list[dict[str, Any]],
) -> None:
    markdown = render_topic_review_report(
        paper_id=snapshot.paper_id,
        paper_title=snapshot.paper_title,
        topic=snapshot.topic,
        scope=scope,
        brief=brief,
        evidence_units=evidence_units,
        materials=list(snapshot.materials),
        selected_material_ids=selected_ids,
        omitted_material_ids=omitted_ids,
        status=status,
        failure=failure,
        initial_selected_material_ids=initial_selected_ids,
        revisit=revisit,
        evidence_failures=evidence_failures,
    )
    path = run_dir / "review" / "paper_brief.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def _build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    snapshot: AnalysisInputSnapshot,
    profile: ModelProfile,
    config: TopicReviewConfig,
    scope: dict[str, Any],
    selected_ids: list[str],
    omitted_ids: list[str],
    evidence_units: list[dict[str, Any]],
    request_results: list[dict[str, Any]],
    initial_selected_ids: list[str],
    revisit: dict[str, Any] | None,
    evidence_failures: list[dict[str, Any]],
) -> dict[str, Any]:
    revisit_failure = revisit.get("failure") if revisit else None
    failure_codes = [
        str(row["error_code"]) for row in evidence_failures
    ]
    if revisit_failure:
        failure_codes.append(str(revisit_failure["error_code"]))
    return {
        "schema_version": TOPIC_REVIEW_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "paper_id": snapshot.paper_id,
        "paper_title": snapshot.paper_title,
        "topic": snapshot.topic,
        "generation_id": snapshot.generation_id,
        "input_sha256": snapshot.input_sha256,
        "provider": profile.provider,
        "model": profile.request_model,
        "model_profile_id": profile.profile_id,
        "model_profile_sha256": profile.sha256,
        "review_config": config.to_dict(),
        "paper_relevance": scope["paper_relevance"],
        "source_material_count": len(snapshot.materials),
        "selected_material_count": len(selected_ids),
        "initial_selected_material_count": len(initial_selected_ids),
        "revisited_material_count": len(
            revisit.get("recovered_material_ids", []) if revisit else []
        ),
        "omitted_material_count": len(omitted_ids),
        "selected_material_ids": list(selected_ids),
        "evidence_unit_count": len(evidence_units),
        "revisit_status": revisit.get("status") if revisit else "not_triggered",
        "revisit": revisit,
        "evidence_failure_count": len(evidence_failures),
        "evidence_failed_material_ids": [
            str(row["material_id"]) for row in evidence_failures
        ],
        "requests": request_results,
        "usage": _sum_usage(request_results),
        "failure_count": len(evidence_failures) + (1 if revisit_failure else 0),
        "failure_codes": list(dict.fromkeys(failure_codes)),
        "outputs": {
            "paper_brief": "output/paper_brief.json",
            "base_brief": (
                None
                if scope["paper_relevance"] == "exclude"
                else "brief/base/validated_brief.json"
            ),
            "revisit": "revisit/result.json" if revisit else None,
            "review": "review/paper_brief.md",
            "omitted_materials": "audit/omitted_materials.jsonl",
            "initial_omitted_materials": "audit/initial_omitted_materials.jsonl",
            "evidence_failures": (
                "audit/evidence_failures.jsonl"
                if evidence_failures
                else None
            ),
        },
    }


def _build_failure_manifest(
    *,
    run_id: str,
    started_at: str,
    paper_id: str,
    topic: str,
    snapshot: AnalysisInputSnapshot | None,
    profile: ModelProfile | None,
    config: TopicReviewConfig | None,
    scope: dict[str, Any],
    selected_ids: list[str],
    omitted_ids: list[str],
    evidence_units: list[dict[str, Any]],
    request_results: list[dict[str, Any]],
    failure: dict[str, Any],
    initial_selected_ids: list[str],
    revisit: dict[str, Any] | None,
    evidence_failures: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": TOPIC_REVIEW_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "failed",
        "started_at": started_at,
        "finished_at": _now(),
        "paper_id": snapshot.paper_id if snapshot else paper_id,
        "paper_title": snapshot.paper_title if snapshot else paper_id,
        "topic": snapshot.topic if snapshot else topic,
        "generation_id": snapshot.generation_id if snapshot else None,
        "input_sha256": snapshot.input_sha256 if snapshot else None,
        "provider": profile.provider if profile else None,
        "model": profile.request_model if profile else None,
        "review_config": config.to_dict() if config else None,
        "paper_relevance": scope.get("paper_relevance"),
        "source_material_count": len(snapshot.materials) if snapshot else 0,
        "selected_material_count": len(selected_ids),
        "initial_selected_material_count": len(initial_selected_ids),
        "revisited_material_count": len(
            revisit.get("recovered_material_ids", []) if revisit else []
        ),
        "omitted_material_count": len(omitted_ids),
        "selected_material_ids": list(selected_ids),
        "evidence_unit_count": len(evidence_units),
        "revisit_status": revisit.get("status") if revisit else "not_started",
        "revisit": revisit,
        "evidence_failure_count": len(evidence_failures),
        "evidence_failed_material_ids": [
            str(row["material_id"]) for row in evidence_failures
        ],
        "requests": request_results,
        "usage": _sum_usage(request_results),
        "failure_count": len(evidence_failures) + 1,
        "failure_codes": list(
            dict.fromkeys(
                [
                    *(str(row["error_code"]) for row in evidence_failures),
                    str(failure["error_code"]),
                ]
            )
        ),
        "failure": failure,
        "outputs": {
            "review": "review/paper_brief.md" if snapshot else None,
            "failures": "audit/failures.jsonl",
            "evidence_failures": (
                "audit/evidence_failures.jsonl"
                if evidence_failures
                else None
            ),
        },
    }


def _failure(stage: str, exc: Exception) -> dict[str, Any]:
    return {
        "stage": stage,
        "error_code": str(getattr(exc, "code", type(exc).__name__)),
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "error_path": getattr(exc, "path", None),
        "occurred_at": _now(),
    }


def _infer_failure_stage(run_dir: Path) -> str:
    for stage in ("revisit", "brief", "evidence", "scope"):
        if (run_dir / stage).exists():
            return stage
    return "input"


def _sum_usage(request_results: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {
        key: sum(
            int(row.get("usage", {}).get(key) or 0)
            for row in request_results
        )
        for key in keys
    }


def _collect_stage_results(
    run_dir: Path,
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {
        str(row.get("request_id", "")): row
        for row in current
        if str(row.get("request_id", ""))
    }
    ordered: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob("**/result.json")):
        if not path.is_file():
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(row, dict) and str(row.get("request_id", "")):
            by_id[str(row["request_id"])] = row
            ordered.append(row)
    ordered.sort(key=lambda row: str(row.get("started_at", "")))
    known = {str(row["request_id"]) for row in ordered}
    ordered.extend(
        row for request_id, row in by_id.items() if request_id not in known
    )
    return ordered


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    review_path = run_dir / "review" / "paper_brief.md"
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "paper_id": manifest.get("paper_id"),
        "paper_relevance": manifest.get("paper_relevance"),
        "source_material_count": manifest.get("source_material_count", 0),
        "selected_material_count": manifest.get("selected_material_count", 0),
        "initial_selected_material_count": manifest.get(
            "initial_selected_material_count",
            0,
        ),
        "revisited_material_count": manifest.get("revisited_material_count", 0),
        "omitted_material_count": manifest.get("omitted_material_count", 0),
        "evidence_unit_count": manifest.get("evidence_unit_count", 0),
        "revisit_status": manifest.get("revisit_status"),
        "evidence_failure_count": manifest.get("evidence_failure_count", 0),
        "failure_count": manifest.get("failure_count", 0),
        "failure_codes": list(manifest.get("failure_codes", [])),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "review_path": str(review_path) if review_path.is_file() else None,
    }


def _compact_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
