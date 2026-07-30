from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .llm_analysis import (
    AnalysisInputError,
    DEFAULT_MODEL_PROFILE,
    DEFAULT_TOKENIZER_CACHE,
)
from .llm_json_stage import execute_json_stage
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_projection import project_cards
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .research_landscape_contracts import (
    RESEARCH_LANDSCAPE_SCHEMA_VERSION,
    ResearchLandscapeConfig,
    build_research_landscape_generation_schema,
    build_research_landscape_schema,
    load_research_landscape_config,
    validate_research_landscape,
    validate_research_landscape_collection,
)
from .research_landscape_report import render_research_landscape_report
from .research_understanding import (
    PAPER_UNDERSTANDING_RUN_SCHEMA_VERSION,
)
from .research_understanding_contracts import (
    PAPER_UNDERSTANDING_SCHEMA_VERSION,
    build_paper_understanding_schema,
    load_research_understanding_config,
    validate_paper_understanding,
)


RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION = "llm.research_landscape_run.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESEARCH_LANDSCAPE_CONFIG = (
    PROJECT_ROOT / "config" / "research-landscape-default.json"
)

RESEARCH_LANDSCAPE_SYSTEM_PROMPT = """你是文献综述的跨论文研究图谱分析器。输入是一组显式选择、已通过代码重放验证的Paper Understanding，只输出符合给定JSON Schema的JSON对象，不要输出Markdown、解释或Schema之外字段。
必须围绕综述目标组织研究维度、论文关系、年代关注变化、明确分歧和本次语料缺口。所有跨论文判断必须引用输入中真实存在的paper_id与contribution_id。
dimensions必须覆盖所有输入论文；无法合理进入任何维度的论文必须放入unmapped_papers，不能静默省略。unmapped_papers只能列出完全没有进入任何dimension的论文；已经进入一个或多个dimension的论文禁止再次列入unmapped_papers，如果全部论文都已进入dimension则输出空数组。dimension_index必须从1开始按输出顺序连续编号。每个dimension、research_evolution和disagreement position中，paper_ids列出的每一篇论文都必须至少有一条属于该论文的contribution_id；不能只列论文而不提供该论文贡献，也不能用另一篇论文的贡献代替。
relation必须连接两个不同论文，且supporting_contribution_ids至少包含这两篇论文各自的一项贡献。relation的statement只能表达所引贡献可直接支持的关系，不得把相关概念自行等同，不得补出贡献未明确表达的机制、因果、立场、整合建议或承继关系。两项工作主题相邻时，不得自行声称“可整合”“是具体应用”“共同证明有效”或“为另一论文提供支持”；只能保守描述各自做了什么及其范围差异。contrasts只用于两篇论文贡献明确表达不相容的主张；一篇论文未提及某方案、未表态或证据较少，不构成contrasts，应改用scope_difference或不输出关系。
不要因论文年份先后就声称后文继承、改进或影响前文；research_evolution只描述本次语料可见的时间顺序和研究关注变化。禁止使用“奠定基础”“奠定框架”“推动了”“促进了”“继承”“发展为”“演变为”等因果传承措辞。
disagreement只能在贡献明确评价同一个问题或同一个备选方案，并确实表达互不相容的立场、结论、机制或方法取向时生成，问题与各position的表述均须由所引贡献直接支持。原因解释处于不同层次、单项建议与包含该建议的组合方案、研究范围或方法不同，都不是分歧。不得把“标准化”自行等同于“大型化”等相关但不同的概念来制造分歧。本次定向语料可能没有明确分歧，此时必须输出空数组，不得为填充字段制造争议。
当前corpus_scope如果是targeted_sample，corpus_gaps只表示本次显式语料没有充分回答的问题，禁止输出field_gap_candidates，禁止写成整个领域不存在研究。
不得生成landscape_id、dimension_id、relation_id、evolution_id、disagreement_id、corpus_gap_id或任何其他机器ID；这些ID由程序生成。"""


@dataclass(frozen=True)
class ResearchLandscapeSource:
    run_id: str
    paper_id: str
    paper_title: str
    topic: str
    generation_id: str
    input_sha256: str
    manifest_sha256: str
    output_sha256: str
    understanding: dict[str, Any]
    materials: tuple[dict[str, Any], ...]
    evidence_units: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ResearchLandscapeSnapshot:
    topic: str
    review_goal: str
    corpus_scope: str
    collection: dict[str, Any]
    sources: tuple[ResearchLandscapeSource, ...]
    input_sha256: str


def create_research_landscape_snapshot(
    workspace: str | Path,
    *,
    collection_path: str | Path,
) -> ResearchLandscapeSnapshot:
    workspace_path = Path(workspace)
    collection = validate_research_landscape_collection(
        _read_json(Path(collection_path), "Research Landscape collection")
    )
    sources = tuple(
        _load_understanding_source(
            workspace_path,
            str(run_id),
            expected_topic=str(collection["topic"]),
        )
        for run_id in collection["source_understanding_run_ids"]
    )
    paper_ids = [source.paper_id for source in sources]
    if len(paper_ids) != len(set(paper_ids)):
        duplicates = sorted(
            {
                paper_id
                for paper_id in paper_ids
                if paper_ids.count(paper_id) > 1
            }
        )
        raise AnalysisInputError(
            f"同一paper_id只能选择一个Understanding运行：{duplicates}"
        )
    input_sha256 = _sha256_json(
        {
            "collection": collection,
            "sources": [
                {
                    "run_id": source.run_id,
                    "paper_id": source.paper_id,
                    "input_sha256": source.input_sha256,
                    "manifest_sha256": source.manifest_sha256,
                    "output_sha256": source.output_sha256,
                }
                for source in sources
            ],
        }
    )
    return ResearchLandscapeSnapshot(
        topic=str(collection["topic"]),
        review_goal=str(collection["review_goal"]),
        corpus_scope=str(collection["corpus_scope"]),
        collection=collection,
        sources=sources,
        input_sha256=input_sha256,
    )


class ResearchLandscapeRunner:
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
        collection_path: str | Path,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        landscape_config_path: str | Path = DEFAULT_RESEARCH_LANDSCAPE_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved_run_id, "run_id")
        run_dir = (
            self.workspace / "_research_landscapes" / "runs" / resolved_run_id
        )
        if run_dir.exists():
            raise AnalysisInputError(
                f"Research Landscape run_id已存在，不能覆盖不可变运行：{resolved_run_id}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot: ResearchLandscapeSnapshot | None = None
        profile: ModelProfile | None = None
        config: ResearchLandscapeConfig | None = None
        stage_result: dict[str, Any] | None = None
        landscape: dict[str, Any] | None = None
        try:
            snapshot = create_research_landscape_snapshot(
                self.workspace,
                collection_path=collection_path,
            )
            profile = load_model_profile(Path(model_profile_path))
            config = load_research_landscape_config(
                Path(landscape_config_path)
            )
            if provider != profile.provider:
                raise AnalysisInputError(
                    f"provider与model profile不一致：provider={provider!r}, "
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
            _write_input_artifacts(run_dir, snapshot, profile, config)
            contribution_to_paper = {
                str(contribution["contribution_id"]): source.paper_id
                for source in snapshot.sources
                for contribution in source.understanding["contributions"]
            }
            validation_schema = build_research_landscape_schema(
                topic=snapshot.topic,
                review_goal=snapshot.review_goal,
                paper_ids=[source.paper_id for source in snapshot.sources],
                contribution_to_paper=contribution_to_paper,
                corpus_scope=snapshot.corpus_scope,
                config=config,
            )
            generation_schema = build_research_landscape_generation_schema(
                validation_schema,
                contribution_to_paper=contribution_to_paper,
            )
            _write_json(
                run_dir / "input" / "output_schema.json",
                generation_schema,
            )
            _write_json(
                run_dir / "input" / "validation_schema.json",
                validation_schema,
            )
            user_prompt = build_research_landscape_prompt(
                snapshot,
                schema=generation_schema,
            )
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="landscape",
                task_name="research_landscape",
                directory_name="landscape",
                system_prompt=RESEARCH_LANDSCAPE_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_output_tokens=config.landscape_max_output_tokens,
                context={
                    "topic": snapshot.topic,
                    "review_goal": snapshot.review_goal,
                    "corpus_scope": snapshot.corpus_scope,
                    "source_run_ids": [
                        source.run_id for source in snapshot.sources
                    ],
                    "paper_ids": [
                        source.paper_id for source in snapshot.sources
                    ],
                    "contribution_ids": list(contribution_to_paper),
                    "input_sha256": snapshot.input_sha256,
                },
                client=client,
                profile=profile,
                token_counter=token_counter,
            )
            landscape = validate_research_landscape(
                parsed,
                schema=validation_schema,
                contribution_to_paper=contribution_to_paper,
            )
            _write_json(
                run_dir / "landscape" / "validated_landscape.json",
                landscape,
            )
            coverage = _build_coverage(snapshot, landscape)
            bindings = _build_landscape_bindings(snapshot, landscape)
            _write_json(run_dir / "audit" / "coverage.json", coverage)
            _write_jsonl(
                run_dir / "audit" / "source_bindings.jsonl",
                bindings,
            )
            _write_json(
                run_dir / "output" / "research_landscape.json",
                landscape,
            )
            _write_text(
                run_dir / "review" / "research_landscape.md",
                render_research_landscape_report(
                    landscape,
                    sources=snapshot.sources,
                ),
            )
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="completed",
                started_at=started_at,
                snapshot=snapshot,
                profile=profile,
                config=config,
                stage_result=stage_result,
                landscape=landscape,
                coverage=coverage,
                failure=None,
                requested_collection_path=str(collection_path),
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
                    run_dir / "landscape" / "result.json"
                )
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="failed",
                started_at=started_at,
                snapshot=snapshot,
                profile=profile,
                config=config,
                stage_result=stage_result,
                landscape=None,
                coverage=None,
                failure=failure,
                requested_collection_path=str(collection_path),
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def build_research_landscape_prompt(
    snapshot: ResearchLandscapeSnapshot,
    *,
    schema: dict[str, Any],
) -> str:
    contribution_ownership = {
        source.paper_id: [
            contribution["contribution_id"]
            for contribution in source.understanding["contributions"]
        ]
        for source in snapshot.sources
    }
    payload = {
        "任务": "根据显式Paper Understanding集合建立跨论文Research Landscape",
        "综述主题": snapshot.topic,
        "综述目标": snapshot.review_goal,
        "语料范围": snapshot.corpus_scope,
        "贡献所有权索引": contribution_ownership,
        "论文认知": [
            _project_understanding(source) for source in snapshot.sources
        ],
        "输出前逐项核对": [
            "每个dimension、research_evolution和disagreement position列出的每篇论文，均引用至少一条该论文在贡献所有权索引中的contribution_id",
            "unmapped_papers中的论文没有进入任何dimension，已进入dimension的论文绝不重复列入",
            "每个relation引用from_paper_id和to_paper_id各自至少一条贡献",
            "关系、分歧和年代变化不包含所引贡献无法直接支持的因果、等同或立场推演",
        ],
        "输出JSONSchema": schema,
    }
    return _compact_json(payload)


def _project_understanding(
    source: ResearchLandscapeSource,
) -> dict[str, Any]:
    understanding = source.understanding
    return {
        "paper_id": source.paper_id,
        "paper_title": source.paper_title,
        "paper_relevance": understanding["paper_relevance"],
        "research_questions": [
            {
                "question": row["question"],
                "problem_category": row["problem_category"],
            }
            for row in understanding["research_questions"]
        ],
        "study_context": {
            key: understanding["study_context"][key]
            for key in (
                "study_object",
                "data_sources",
                "time_scope",
                "geographic_scope",
            )
        },
        "methods": [
            {
                "method_category": row["method_category"],
                "method_name": row["method_name"],
                "description": row["description"],
            }
            for row in understanding["methods"]
        ],
        "contributions": [
            {
                key: row[key]
                for key in (
                    "contribution_id",
                    "statement",
                    "result_type",
                    "validation_level",
                    "evidence_strength",
                    "strength_rationale",
                )
            }
            for row in understanding["contributions"]
        ],
        "limitations": [
            {
                "statement": row["statement"],
                "basis": row["basis"],
            }
            for row in understanding["limitations"]
        ],
        "review_roles": understanding["review_roles"],
        "unresolved_questions": understanding["unresolved_questions"],
        "keywords": understanding["keywords"],
    }


def _load_understanding_source(
    workspace: Path,
    run_id: str,
    *,
    expected_topic: str,
) -> ResearchLandscapeSource:
    _safe_segment(run_id, "source_understanding_run_id")
    run_dir = workspace / "_paper_understandings" / "runs" / run_id
    manifest, manifest_bytes = _read_json_bytes(
        run_dir / "manifest.json",
        "Paper Understanding manifest",
    )
    output, output_bytes = _read_json_bytes(
        run_dir / "output" / "paper_understanding.json",
        "Paper Understanding output",
    )
    parsed = _read_json(
        run_dir / "understanding" / "parsed_response.json",
        "Paper Understanding原始解析响应",
    )
    paper = _read_json(run_dir / "input" / "paper.json", "冻结论文身份")
    document_text = _read_text(
        run_dir / "input" / "document.md",
        "冻结完整Markdown",
    )
    materials = _read_jsonl(
        run_dir / "input" / "materials.jsonl",
        "冻结Card",
    )
    projected_cards = _read_jsonl(
        run_dir / "input" / "projected_cards.jsonl",
        "冻结Card投影",
    )
    evidence_units = _read_jsonl(
        run_dir / "input" / "evidence_units.jsonl",
        "冻结Evidence",
    )
    topic_source = _read_json(
        run_dir / "input" / "topic_review_source.json",
        "Topic Review来源",
    )
    understanding_config = load_research_understanding_config(
        run_dir / "input" / "understanding_config.json"
    )
    if (
        manifest.get("schema_version")
        != PAPER_UNDERSTANDING_RUN_SCHEMA_VERSION
    ):
        raise AnalysisInputError(
            f"来源运行{run_id!r}不是当前"
            f"{PAPER_UNDERSTANDING_RUN_SCHEMA_VERSION}。"
        )
    if manifest.get("run_id") != run_id:
        raise AnalysisInputError(
            f"来源运行目录与manifest.run_id不一致：{run_id}"
        )
    if manifest.get("status") != "completed":
        raise AnalysisInputError(
            f"来源Understanding运行状态不可用：{manifest.get('status')!r}。"
        )
    if output.get("schema_version") != PAPER_UNDERSTANDING_SCHEMA_VERSION:
        raise AnalysisInputError(
            f"来源运行{run_id!r}不是当前"
            f"{PAPER_UNDERSTANDING_SCHEMA_VERSION}。"
        )
    paper_id = str(paper.get("paper_id", ""))
    paper_title = str(paper.get("paper_title", ""))
    topic = str(paper.get("topic", ""))
    generation_id = str(paper.get("generation_id", ""))
    if not all((paper_id, paper_title, topic, generation_id)):
        raise AnalysisInputError(f"来源运行{run_id!r}冻结论文身份不完整。")
    if topic != expected_topic:
        raise AnalysisInputError(
            f"来源运行{run_id!r}的综述主题与collection不一致。"
        )
    identity_fields = {
        "paper_id": paper_id,
        "paper_title": paper_title,
        "topic": topic,
        "generation_id": generation_id,
    }
    for field, expected in identity_fields.items():
        if manifest.get(field) != expected:
            raise AnalysisInputError(
                f"来源运行{run_id!r}的manifest.{field}不一致。"
            )
    if (
        output.get("paper_id") != paper_id
        or output.get("paper_title") != paper_title
        or output.get("topic") != topic
    ):
        raise AnalysisInputError(
            f"来源运行{run_id!r}的发布论文身份不一致。"
        )
    if output.get("paper_relevance") == "exclude":
        raise AnalysisInputError(
            f"来源运行{run_id!r}已排除，不应进入Landscape collection。"
        )
    if _sha256_text(document_text) != paper.get("document_sha256"):
        raise AnalysisInputError(
            f"来源运行{run_id!r}的完整Markdown哈希不一致。"
        )
    if project_cards(materials) != projected_cards:
        raise AnalysisInputError(
            f"来源运行{run_id!r}的Card投影不能由冻结Card重建。"
        )
    material_ids: list[str] = []
    for material in materials:
        material_id = str(material.get("material_id", ""))
        if (
            not material_id
            or material.get("paper_id") != paper_id
            or material.get("generation_id") != generation_id
            or not material.get("source_fingerprint")
        ):
            raise AnalysisInputError(
                f"来源运行{run_id!r}的冻结Card身份或指纹无效。"
            )
        material_ids.append(material_id)
    if len(material_ids) != len(set(material_ids)):
        raise AnalysisInputError(f"来源运行{run_id!r}的冻结Card ID重复。")
    evidence_ids: list[str] = []
    for evidence in evidence_units:
        evidence_id = str(evidence.get("evidence_unit_id", ""))
        citations = evidence.get("citations")
        if not evidence_id or not isinstance(citations, list) or not citations:
            raise AnalysisInputError(
                f"来源运行{run_id!r}的Evidence身份或引用无效。"
            )
        unknown = {
            str(citation.get("material_id", ""))
            for citation in citations
            if isinstance(citation, dict)
        } - set(material_ids)
        if unknown:
            raise AnalysisInputError(
                f"来源运行{run_id!r}的Evidence引用未知Card：{sorted(unknown)}"
            )
        evidence_ids.append(evidence_id)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise AnalysisInputError(f"来源运行{run_id!r}的Evidence ID重复。")
    source_topic_review_run_id = topic_source.get(
        "source_topic_review_run_id"
    )
    source_fingerprints = (
        {
            material_id: str(material["source_fingerprint"])
            for material_id, material in sorted(
                zip(material_ids, materials),
                key=lambda row: row[0],
            )
        }
        if source_topic_review_run_id is not None
        else {}
    )
    input_sha256 = _sha256_json(
        {
            "paper_id": paper_id,
            "paper_title": paper_title,
            "topic": topic,
            "workspace_paper_id": str(paper.get("workspace_paper_id", "")),
            "generation_id": generation_id,
            "document_sha256": str(paper.get("document_sha256", "")),
            "card_input_sha256": _sha256_json(materials),
            "source_topic_review_run_id": source_topic_review_run_id,
            "source_material_fingerprints": source_fingerprints,
            "projected_cards": projected_cards,
            "evidence_units": evidence_units,
        }
    )
    if input_sha256 != paper.get("input_sha256") or input_sha256 != manifest.get(
        "input_sha256"
    ):
        raise AnalysisInputError(
            f"来源运行{run_id!r}的Understanding输入哈希无法重建。"
        )
    schema = build_paper_understanding_schema(
        paper_id=paper_id,
        paper_title=paper_title,
        topic=topic,
        material_ids=material_ids,
        evidence_unit_ids=evidence_ids,
        config=understanding_config,
    )
    replay = validate_paper_understanding(
        parsed,
        schema=schema,
        material_by_id={
            str(material["material_id"]): material for material in materials
        },
        evidence_by_id={
            str(evidence["evidence_unit_id"]): evidence
            for evidence in evidence_units
        },
    )
    if replay != output:
        raise AnalysisInputError(
            f"来源运行{run_id!r}的发布结果不能由原始响应重放得到。"
        )
    if (
        manifest.get("understanding_id") != output.get("understanding_id")
        or manifest.get("paper_relevance") != output.get("paper_relevance")
        or int(manifest.get("material_count", -1)) != len(materials)
        or int(manifest.get("evidence_unit_count", -1)) != len(evidence_units)
    ):
        raise AnalysisInputError(
            f"来源运行{run_id!r}的manifest计数或发布身份不一致。"
        )
    return ResearchLandscapeSource(
        run_id=run_id,
        paper_id=paper_id,
        paper_title=paper_title,
        topic=topic,
        generation_id=generation_id,
        input_sha256=input_sha256,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        understanding=output,
        materials=tuple(materials),
        evidence_units=tuple(evidence_units),
    )


def _write_input_artifacts(
    run_dir: Path,
    snapshot: ResearchLandscapeSnapshot,
    profile: ModelProfile,
    config: ResearchLandscapeConfig,
) -> None:
    _write_json(run_dir / "input" / "collection.json", snapshot.collection)
    _write_jsonl(
        run_dir / "input" / "understandings.jsonl",
        [
            {
                "source_run_id": source.run_id,
                "manifest_sha256": source.manifest_sha256,
                "output_sha256": source.output_sha256,
                "understanding": source.understanding,
            }
            for source in snapshot.sources
        ],
    )
    _write_jsonl(
        run_dir / "input" / "source_manifests.jsonl",
        [
            {
                "run_id": source.run_id,
                "paper_id": source.paper_id,
                "paper_title": source.paper_title,
                "generation_id": source.generation_id,
                "input_sha256": source.input_sha256,
                "manifest_sha256": source.manifest_sha256,
                "output_sha256": source.output_sha256,
            }
            for source in snapshot.sources
        ],
    )
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(
        run_dir / "input" / "landscape_config.json",
        config.to_dict(),
    )
    _write_json(
        run_dir / "input" / "snapshot.json",
        {
            "input_sha256": snapshot.input_sha256,
            "source_count": len(snapshot.sources),
        },
    )


def _build_coverage(
    snapshot: ResearchLandscapeSnapshot,
    landscape: dict[str, Any],
) -> dict[str, Any]:
    dimensioned = {
        str(paper_id)
        for dimension in landscape["dimensions"]
        for paper_id in dimension["paper_ids"]
    }
    unmapped = {
        str(row["paper_id"]) for row in landscape["unmapped_papers"]
    }
    return {
        "source_paper_count": len(snapshot.sources),
        "dimension_count": len(landscape["dimensions"]),
        "dimensioned_paper_count": len(dimensioned),
        "unmapped_paper_count": len(unmapped),
        "covered_paper_count": len(dimensioned | unmapped),
        "relation_count": len(landscape["relations"]),
        "evolution_count": len(landscape["research_evolution"]),
        "disagreement_count": len(landscape["disagreements"]),
        "corpus_gap_count": len(landscape["corpus_gaps"]),
        "field_gap_candidate_count": len(
            landscape.get("field_gap_candidates", [])
        ),
        "corpus_scope": snapshot.corpus_scope,
    }


def _build_landscape_bindings(
    snapshot: ResearchLandscapeSnapshot,
    landscape: dict[str, Any],
) -> list[dict[str, Any]]:
    contribution_by_id = {
        str(contribution["contribution_id"]): (source, contribution)
        for source in snapshot.sources
        for contribution in source.understanding["contributions"]
    }
    rows: list[dict[str, Any]] = []

    def append(
        object_type: str,
        object_id: str,
        statement: str,
        contribution_ids: Iterable[str],
    ) -> None:
        for contribution_id in contribution_ids:
            source, contribution = contribution_by_id[str(contribution_id)]
            rows.append(
                {
                    "object_type": object_type,
                    "object_id": object_id,
                    "statement": statement,
                    "paper_id": source.paper_id,
                    "paper_title": source.paper_title,
                    "contribution_id": str(contribution_id),
                    "contribution_statement": contribution["statement"],
                    "result_type": contribution["result_type"],
                    "validation_level": contribution["validation_level"],
                    "evidence_strength": contribution["evidence_strength"],
                    "material_ids": contribution["material_ids"],
                }
            )

    for row in landscape["dimensions"]:
        append(
            "dimension",
            str(row["dimension_id"]),
            str(row["question"]),
            row["contribution_ids"],
        )
    for row in landscape["relations"]:
        append(
            "relation",
            str(row["relation_id"]),
            str(row["statement"]),
            row["supporting_contribution_ids"],
        )
    for row in landscape["research_evolution"]:
        append(
            "evolution",
            str(row["evolution_id"]),
            str(row["statement"]),
            row["contribution_ids"],
        )
    for row in landscape["disagreements"]:
        for position in row["positions"]:
            append(
                "disagreement",
                str(row["disagreement_id"]),
                str(position["statement"]),
                position["contribution_ids"],
            )
    return rows


def _build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    snapshot: ResearchLandscapeSnapshot | None,
    profile: ModelProfile | None,
    config: ResearchLandscapeConfig | None,
    stage_result: dict[str, Any] | None,
    landscape: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
    failure: dict[str, Any] | None,
    requested_collection_path: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "topic": snapshot.topic if snapshot is not None else None,
        "review_goal": snapshot.review_goal if snapshot is not None else None,
        "corpus_scope": snapshot.corpus_scope if snapshot is not None else None,
        "collection_path": requested_collection_path,
        "input_sha256": snapshot.input_sha256 if snapshot is not None else None,
        "source_run_ids": (
            [source.run_id for source in snapshot.sources]
            if snapshot is not None
            else []
        ),
        "source_paper_count": (
            len(snapshot.sources) if snapshot is not None else 0
        ),
        "provider": profile.provider if profile is not None else None,
        "model": profile.request_model if profile is not None else None,
        "model_profile_id": profile.profile_id if profile is not None else None,
        "model_profile_sha256": profile.sha256 if profile is not None else None,
        "landscape_config": config.to_dict() if config is not None else None,
        "stage_result": stage_result,
        "landscape_id": (
            landscape.get("landscape_id") if landscape is not None else None
        ),
        "coverage": coverage,
        "failure": failure,
        "outputs": {
            "research_landscape": (
                "output/research_landscape.json"
                if landscape is not None
                else None
            ),
            "review": (
                "review/research_landscape.md"
                if landscape is not None
                else None
            ),
            "coverage": (
                "audit/coverage.json" if landscape is not None else None
            ),
            "source_bindings": (
                "audit/source_bindings.jsonl"
                if landscape is not None
                else None
            ),
            "failures": (
                "audit/failures.jsonl" if failure is not None else None
            ),
        },
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "topic": manifest.get("topic"),
        "source_paper_count": manifest.get("source_paper_count", 0),
        "landscape_id": manifest.get("landscape_id"),
        "coverage": manifest.get("coverage"),
        "failure": manifest.get("failure"),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "output_path": (
            str(run_dir / "output" / "research_landscape.json")
            if manifest["status"] == "completed"
            else None
        ),
        "review_path": (
            str(run_dir / "review" / "research_landscape.md")
            if manifest["status"] == "completed"
            else None
        ),
    }


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "landscape").exists():
        return "landscape"
    if (run_dir / "input").exists():
        return "input_written"
    return "input_snapshot"


def _safe_segment(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.strip() in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise AnalysisInputError(f"{field}无效：{value!r}。")
    return value.strip()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    payload, _ = _read_json_bytes(path, label)
    return payload


def _read_json_bytes(
    path: Path,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"无法读取{label}：{path}") from exc
    if not isinstance(payload, dict):
        raise AnalysisInputError(f"{label}必须是JSON对象：{path}")
    return payload, raw


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise AnalysisInputError(f"无法读取{label}：{path}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AnalysisInputError(
                f"{label}第{line_number}行不是有效JSON：{path}"
            ) from exc
        if not isinstance(row, dict):
            raise AnalysisInputError(
                f"{label}第{line_number}行必须是JSON对象：{path}"
            )
        rows.append(row)
    return rows


def _read_text(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise AnalysisInputError(f"无法读取{label}：{path}") from exc
    if not value.strip():
        raise AnalysisInputError(f"{label}不能为空：{path}")
    return value


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _compact_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_json(value: object) -> str:
    return _sha256_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
