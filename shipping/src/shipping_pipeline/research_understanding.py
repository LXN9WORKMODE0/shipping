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
    create_input_snapshot,
)
from .llm_json_stage import execute_json_stage
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_projection import project_cards
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .research_understanding_contracts import (
    PAPER_UNDERSTANDING_SCHEMA_VERSION,
    ResearchUnderstandingConfig,
    build_paper_understanding_schema,
    load_research_understanding_config,
    validate_paper_understanding,
)
from .research_understanding_report import render_paper_understanding_report
from .topic_review import TOPIC_REVIEW_RUN_SCHEMA_VERSION


PAPER_UNDERSTANDING_RUN_SCHEMA_VERSION = "llm.paper_understanding_run.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UNDERSTANDING_CONFIG = (
    PROJECT_ROOT / "config" / "research-understanding-default.json"
)

PAPER_UNDERSTANDING_SYSTEM_PROMPT = """你是单篇论文整体认知分析器。输入只来自同一篇论文，包括完整规范化Markdown、全部Card和可选的既有严格Evidence。
只输出符合给定JSON Schema的JSON对象，不要输出Markdown、解释或Schema之外的字段。
你的任务是理解论文自身的研究问题、研究对象、数据来源、技术方法、主要贡献、结果性质、验证水平、限制及其对当前综述主题的用途；不要判断该论文与其他论文之间的继承、冲突或演化关系。
完整Markdown用于理解论文论证过程；Card用于定位所有事实性判断；已有Evidence只表示此前已严格核验的事实锚点，不代表论文的全部内容。
每个研究问题、研究对象、方法、贡献和限制都必须引用输入中真实存在的material_id。只有贡献确实受到已有Evidence支持时，才填写对应evidence_unit_id；没有匹配Evidence时输出空数组，禁止牵强绑定。
必须区分historical_observation、empirical_measurement、experimental_result、simulation_result、algorithm_benchmark、engineering_implementation、system_design、recommendation和conceptual_argument。不得把仿真、算法测试、设计建议或概念论证写成已经完成的工程实施或现场验证。
每项contribution必须是单一、原子的结果陈述，不能把“已实施的工程事实”和“作者建议”合并成一项。engineering_implementation只能使用engineering_application验证；simulation_result只能使用simulation；algorithm_benchmark只能使用benchmark或simulation；recommendation只能使用none或conceptual。若一句原文同时包含已实施措施和后续建议，必须拆成两个contribution并分别分类。
reviewer_inferred限制必须能由所引Card直接支持，不能凭常识补充。未决问题只描述本文范围边界、缺少的验证或数据，不得声称是整个研究领域的空白。
review_roles中的contribution_indexes必须包含至少一个真实存在的、从1开始计数的贡献序号。只基于研究背景或研究对象、无法绑定到具体贡献的用途不要输出；禁止输出空的contribution_indexes。
如果论文与当前主题无关，可以输出paper_relevance=exclude；此时仍客观描述论文自身研究，但review_roles必须为空数组。模型不得生成understanding_id或任何子对象ID。"""


@dataclass(frozen=True)
class PaperUnderstandingSnapshot:
    paper_id: str
    paper_title: str
    topic: str
    workspace_paper_id: str
    generation_id: str
    document_text: str
    document_sha256: str
    materials: tuple[dict[str, Any], ...]
    projected_cards: tuple[dict[str, Any], ...]
    evidence_units: tuple[dict[str, Any], ...]
    source_topic_review_run_id: str | None
    input_sha256: str


def create_paper_understanding_snapshot(
    workspace: str | Path,
    *,
    paper_id: str,
    workspace_paper_id: str,
    topic: str,
    source_topic_review_run_id: str | None = None,
) -> PaperUnderstandingSnapshot:
    workspace_path = Path(workspace)
    workspace_id = _safe_segment(workspace_paper_id, "workspace_paper_id")
    card_snapshot = create_input_snapshot(
        workspace_path,
        paper_id=paper_id,
        topic=topic,
        workspace_paper_id=workspace_id,
    )
    document_path = (
        workspace_path / workspace_id / "normalized" / "document.md"
    )
    try:
        document_text = document_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise AnalysisInputError(
            f"缺少或无法读取规范化全文：{document_path}"
        ) from exc
    if not document_text.strip():
        raise AnalysisInputError(
            f"规范化全文 document.md 不能为空：{document_path}"
        )
    document_sha256 = _sha256_text(document_text)
    projected = tuple(project_cards(card_snapshot.materials))
    evidence_units: tuple[dict[str, Any], ...] = ()
    source_run_id: str | None = None
    source_fingerprints: dict[str, str] = {}
    if source_topic_review_run_id is not None:
        source_run_id = _safe_segment(
            source_topic_review_run_id,
            "source_topic_review_run_id",
        )
        evidence_units, source_fingerprints = _load_topic_review_evidence(
            workspace_path,
            source_run_id,
            expected_paper_id=card_snapshot.paper_id,
            expected_paper_title=card_snapshot.paper_title,
            expected_topic=card_snapshot.topic,
            expected_generation_id=card_snapshot.generation_id,
            current_materials=card_snapshot.materials,
        )
    input_sha256 = _sha256_json(
        {
            "paper_id": card_snapshot.paper_id,
            "paper_title": card_snapshot.paper_title,
            "topic": card_snapshot.topic,
            "workspace_paper_id": workspace_id,
            "generation_id": card_snapshot.generation_id,
            "document_sha256": document_sha256,
            "card_input_sha256": card_snapshot.input_sha256,
            "source_topic_review_run_id": source_run_id,
            "source_material_fingerprints": source_fingerprints,
            "projected_cards": projected,
            "evidence_units": evidence_units,
        }
    )
    return PaperUnderstandingSnapshot(
        paper_id=card_snapshot.paper_id,
        paper_title=card_snapshot.paper_title,
        topic=card_snapshot.topic,
        workspace_paper_id=workspace_id,
        generation_id=card_snapshot.generation_id,
        document_text=document_text,
        document_sha256=document_sha256,
        materials=card_snapshot.materials,
        projected_cards=projected,
        evidence_units=evidence_units,
        source_topic_review_run_id=source_run_id,
        input_sha256=input_sha256,
    )


class PaperUnderstandingRunner:
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
        workspace_paper_id: str,
        topic: str,
        source_topic_review_run_id: str | None = None,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        understanding_config_path: str | Path = DEFAULT_UNDERSTANDING_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved_run_id, "run_id")
        run_dir = (
            self.workspace
            / "_paper_understandings"
            / "runs"
            / resolved_run_id
        )
        if run_dir.exists():
            raise AnalysisInputError(
                f"论文认知 run_id 已存在，不能覆盖不可变运行：{resolved_run_id}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot: PaperUnderstandingSnapshot | None = None
        profile: ModelProfile | None = None
        config: ResearchUnderstandingConfig | None = None
        stage_result: dict[str, Any] | None = None
        try:
            snapshot = create_paper_understanding_snapshot(
                self.workspace,
                paper_id=paper_id,
                workspace_paper_id=workspace_paper_id,
                topic=topic,
                source_topic_review_run_id=source_topic_review_run_id,
            )
            profile = load_model_profile(Path(model_profile_path))
            config = load_research_understanding_config(
                Path(understanding_config_path)
            )
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
            _write_input_artifacts(run_dir, snapshot, profile, config)
            schema = build_paper_understanding_schema(
                paper_id=snapshot.paper_id,
                paper_title=snapshot.paper_title,
                topic=snapshot.topic,
                material_ids=[
                    str(row["material_id"]) for row in snapshot.materials
                ],
                evidence_unit_ids=[
                    str(row["evidence_unit_id"])
                    for row in snapshot.evidence_units
                ],
                config=config,
            )
            _write_json(run_dir / "input" / "output_schema.json", schema)
            user_prompt = build_paper_understanding_prompt(
                snapshot,
                schema=schema,
            )
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="understanding",
                task_name="paper_understanding",
                directory_name="understanding",
                system_prompt=PAPER_UNDERSTANDING_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_output_tokens=config.understanding_max_output_tokens,
                context={
                    "paper_id": snapshot.paper_id,
                    "topic": snapshot.topic,
                    "workspace_paper_id": snapshot.workspace_paper_id,
                    "generation_id": snapshot.generation_id,
                    "input_sha256": snapshot.input_sha256,
                    "material_ids": [
                        str(row["material_id"])
                        for row in snapshot.materials
                    ],
                    "evidence_unit_ids": [
                        str(row["evidence_unit_id"])
                        for row in snapshot.evidence_units
                    ],
                },
                client=client,
                profile=profile,
                token_counter=token_counter,
            )
            material_by_id = {
                str(row["material_id"]): row for row in snapshot.materials
            }
            evidence_by_id = {
                str(row["evidence_unit_id"]): row
                for row in snapshot.evidence_units
            }
            understanding = validate_paper_understanding(
                parsed,
                schema=schema,
                material_by_id=material_by_id,
                evidence_by_id=evidence_by_id,
            )
            _write_json(
                run_dir / "understanding" / "validated_understanding.json",
                understanding,
            )
            bindings = _build_source_bindings(
                understanding,
                material_by_id=material_by_id,
                evidence_by_id=evidence_by_id,
            )
            referenced_ids = {
                str(row["material_id"]) for row in bindings
            }
            unreferenced = [
                _material_audit_row(row)
                for row in snapshot.materials
                if str(row["material_id"]) not in referenced_ids
            ]
            _write_jsonl(
                run_dir / "audit" / "source_bindings.jsonl",
                bindings,
            )
            _write_jsonl(
                run_dir / "audit" / "unreferenced_materials.jsonl",
                unreferenced,
            )
            _write_json(
                run_dir / "output" / "paper_understanding.json",
                understanding,
            )
            review = render_paper_understanding_report(
                understanding,
                materials=snapshot.materials,
                evidence_units=snapshot.evidence_units,
            )
            _write_text(
                run_dir / "review" / "paper_understanding.md",
                review,
            )
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="completed",
                started_at=started_at,
                snapshot=snapshot,
                profile=profile,
                config=config,
                stage_result=stage_result,
                understanding=understanding,
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
                    run_dir / "understanding" / "result.json"
                )
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="failed",
                started_at=started_at,
                snapshot=snapshot,
                profile=profile,
                config=config,
                stage_result=stage_result,
                understanding=None,
                failure=failure,
                requested_paper_id=paper_id,
                requested_topic=topic,
                requested_workspace_paper_id=workspace_paper_id,
                requested_source_run_id=source_topic_review_run_id,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def build_paper_understanding_prompt(
    snapshot: PaperUnderstandingSnapshot,
    *,
    schema: dict[str, Any],
) -> str:
    payload = {
        "任务": "完整理解单篇论文，并形成可追溯的论文认知对象",
        "论文": {
            "paper_id": snapshot.paper_id,
            "paper_title": snapshot.paper_title,
            "workspace_paper_id": snapshot.workspace_paper_id,
            "generation_id": snapshot.generation_id,
        },
        "综述主题": snapshot.topic,
        "完整Markdown": snapshot.document_text,
        "全部Card": list(snapshot.projected_cards),
        "已有Evidence": list(snapshot.evidence_units),
        "输出JSONSchema": schema,
    }
    return _compact_json(payload)


def _load_topic_review_evidence(
    workspace: Path,
    run_id: str,
    *,
    expected_paper_id: str,
    expected_paper_title: str,
    expected_topic: str,
    expected_generation_id: str,
    current_materials: Iterable[dict[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], dict[str, str]]:
    run_dir = workspace / "_topic_reviews" / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json", "Topic Review manifest")
    evidence_units = _read_jsonl(
        run_dir / "evidence" / "evidence_units.jsonl",
        "Topic Review Evidence",
    )
    source_materials = _read_jsonl(
        run_dir / "input" / "materials.jsonl",
        "Topic Review冻结材料",
    )
    if manifest.get("schema_version") != TOPIC_REVIEW_RUN_SCHEMA_VERSION:
        raise AnalysisInputError(
            f"Topic Review来源运行不是当前{TOPIC_REVIEW_RUN_SCHEMA_VERSION}：{run_id}"
        )
    if manifest.get("run_id") != run_id:
        raise AnalysisInputError("Topic Review来源运行目录与manifest.run_id不一致。")
    if manifest.get("status") not in {
        "completed",
        "completed_with_revisit_failure",
        "completed_with_failures",
    }:
        raise AnalysisInputError(
            f"Topic Review来源运行状态不可用：{manifest.get('status')!r}。"
        )
    if (
        manifest.get("paper_id") != expected_paper_id
        or manifest.get("paper_title") != expected_paper_title
    ):
        raise AnalysisInputError("Topic Review来源运行的论文身份与当前论文不一致。")
    if manifest.get("topic") != expected_topic:
        raise AnalysisInputError("Topic Review来源运行的综述主题不一致。")
    if manifest.get("generation_id") != expected_generation_id:
        raise AnalysisInputError("Topic Review来源运行的Card generation不一致。")
    if int(manifest.get("evidence_unit_count", -1)) != len(evidence_units):
        raise AnalysisInputError("Topic Review来源Evidence数量与manifest不一致。")

    current_by_id = {
        str(row["material_id"]): row for row in current_materials
    }
    source_by_id: dict[str, dict[str, Any]] = {}
    for row in source_materials:
        material_id = str(row.get("material_id", ""))
        if not material_id or material_id in source_by_id:
            raise AnalysisInputError("Topic Review冻结材料ID为空或重复。")
        if row.get("paper_id") != expected_paper_id:
            raise AnalysisInputError("Topic Review冻结材料的论文身份不一致。")
        if row.get("generation_id") != expected_generation_id:
            raise AnalysisInputError("Topic Review冻结材料的Card generation不一致。")
        source_by_id[material_id] = row
    if set(source_by_id) != set(current_by_id):
        raise AnalysisInputError(
            "Topic Review冻结材料与当前完整Card集合不一致。"
        )
    source_fingerprints: dict[str, str] = {}
    for material_id, source in source_by_id.items():
        source_fingerprint = str(source.get("source_fingerprint", ""))
        current_fingerprint = str(
            current_by_id[material_id].get("source_fingerprint", "")
        )
        if not source_fingerprint or source_fingerprint != current_fingerprint:
            raise AnalysisInputError(
                f"Topic Review冻结材料与当前Card内容不一致：{material_id}"
            )
        source_fingerprints[material_id] = source_fingerprint

    evidence_ids: set[str] = set()
    for evidence in evidence_units:
        evidence_id = str(evidence.get("evidence_unit_id", ""))
        if not evidence_id or evidence_id in evidence_ids:
            raise AnalysisInputError("Topic Review Evidence ID为空或重复。")
        evidence_ids.add(evidence_id)
        citations = evidence.get("citations")
        if not isinstance(citations, list) or not citations:
            raise AnalysisInputError(
                f"Topic Review Evidence缺少引用：{evidence_id}"
            )
        cited_ids = {
            str(row.get("material_id", ""))
            for row in citations
            if isinstance(row, dict)
        }
        unknown = sorted(cited_ids - set(current_by_id))
        if "" in cited_ids or unknown:
            raise AnalysisInputError(
                f"Topic Review Evidence引用未知Card：{unknown or ['空ID']}"
            )
    return tuple(evidence_units), dict(sorted(source_fingerprints.items()))


def _write_input_artifacts(
    run_dir: Path,
    snapshot: PaperUnderstandingSnapshot,
    profile: ModelProfile,
    config: ResearchUnderstandingConfig,
) -> None:
    _write_json(
        run_dir / "input" / "paper.json",
        {
            "paper_id": snapshot.paper_id,
            "paper_title": snapshot.paper_title,
            "topic": snapshot.topic,
            "workspace_paper_id": snapshot.workspace_paper_id,
            "generation_id": snapshot.generation_id,
            "document_sha256": snapshot.document_sha256,
            "input_sha256": snapshot.input_sha256,
        },
    )
    _write_text(run_dir / "input" / "document.md", snapshot.document_text)
    _write_jsonl(run_dir / "input" / "materials.jsonl", snapshot.materials)
    _write_jsonl(
        run_dir / "input" / "projected_cards.jsonl",
        snapshot.projected_cards,
    )
    _write_jsonl(
        run_dir / "input" / "evidence_units.jsonl",
        snapshot.evidence_units,
    )
    _write_json(
        run_dir / "input" / "topic_review_source.json",
        {
            "source_topic_review_run_id": (
                snapshot.source_topic_review_run_id
            )
        },
    )
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(
        run_dir / "input" / "understanding_config.json",
        config.to_dict(),
    )


def _build_source_bindings(
    understanding: dict[str, Any],
    *,
    material_by_id: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    objects: list[tuple[str, str, str, dict[str, Any]]] = []
    for row in understanding["research_questions"]:
        objects.append(
            (
                "research_question",
                str(row["research_question_id"]),
                str(row["question"]),
                row,
            )
        )
    context = understanding["study_context"]
    objects.append(
        ("study_context", "study_context", str(context["study_object"]), context)
    )
    for row in understanding["methods"]:
        objects.append(
            ("method", str(row["method_id"]), str(row["description"]), row)
        )
    for row in understanding["contributions"]:
        objects.append(
            (
                "contribution",
                str(row["contribution_id"]),
                str(row["statement"]),
                row,
            )
        )
    for row in understanding["limitations"]:
        objects.append(
            (
                "limitation",
                str(row["limitation_id"]),
                str(row["statement"]),
                row,
            )
        )

    bindings: list[dict[str, Any]] = []
    for object_type, object_id, statement, row in objects:
        evidence_ids = [
            str(value) for value in row.get("evidence_unit_ids", [])
        ]
        for material_id in row["material_ids"]:
            material = material_by_id[str(material_id)]
            bindings.append(
                {
                    "object_type": object_type,
                    "object_id": object_id,
                    "statement": statement,
                    "material_id": str(material_id),
                    "material_title": str(
                        material.get("clean_title")
                        or material.get("raw_title")
                        or material.get("paper_title")
                        or ""
                    ),
                    "source_span": material.get("source_span"),
                    "extract": str(material.get("extract", "")),
                    "evidence_unit_ids": evidence_ids,
                    "evidence_claims": [
                        str(evidence_by_id[evidence_id].get("claim", ""))
                        for evidence_id in evidence_ids
                    ],
                }
            )
    return bindings


def _material_audit_row(material: dict[str, Any]) -> dict[str, Any]:
    return {
        "material_id": str(material["material_id"]),
        "title": str(
            material.get("clean_title")
            or material.get("raw_title")
            or material.get("paper_title")
            or ""
        ),
        "source_span": material.get("source_span"),
        "extract": str(material.get("extract", "")),
        "reason": "未被Paper Understanding中的事实对象引用；Card仍保留在输入快照中。",
    }


def _build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    snapshot: PaperUnderstandingSnapshot | None,
    profile: ModelProfile | None,
    config: ResearchUnderstandingConfig | None,
    stage_result: dict[str, Any] | None,
    understanding: dict[str, Any] | None,
    failure: dict[str, Any] | None,
    requested_paper_id: str | None = None,
    requested_topic: str | None = None,
    requested_workspace_paper_id: str | None = None,
    requested_source_run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": PAPER_UNDERSTANDING_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "paper_id": (
            snapshot.paper_id if snapshot is not None else requested_paper_id
        ),
        "paper_title": snapshot.paper_title if snapshot is not None else None,
        "topic": snapshot.topic if snapshot is not None else requested_topic,
        "workspace_paper_id": (
            snapshot.workspace_paper_id
            if snapshot is not None
            else requested_workspace_paper_id
        ),
        "generation_id": (
            snapshot.generation_id if snapshot is not None else None
        ),
        "input_sha256": snapshot.input_sha256 if snapshot is not None else None,
        "document_sha256": (
            snapshot.document_sha256 if snapshot is not None else None
        ),
        "source_topic_review_run_id": (
            snapshot.source_topic_review_run_id
            if snapshot is not None
            else requested_source_run_id
        ),
        "material_count": len(snapshot.materials) if snapshot is not None else 0,
        "evidence_unit_count": (
            len(snapshot.evidence_units) if snapshot is not None else 0
        ),
        "provider": profile.provider if profile is not None else None,
        "model": profile.request_model if profile is not None else None,
        "model_profile_id": profile.profile_id if profile is not None else None,
        "model_profile_sha256": profile.sha256 if profile is not None else None,
        "understanding_config": (
            config.to_dict() if config is not None else None
        ),
        "stage_result": stage_result,
        "understanding_id": (
            understanding.get("understanding_id")
            if understanding is not None
            else None
        ),
        "paper_relevance": (
            understanding.get("paper_relevance")
            if understanding is not None
            else None
        ),
        "failure": failure,
        "outputs": {
            "paper_understanding": (
                "output/paper_understanding.json"
                if understanding is not None
                else None
            ),
            "review": (
                "review/paper_understanding.md"
                if understanding is not None
                else None
            ),
            "source_bindings": (
                "audit/source_bindings.jsonl"
                if understanding is not None
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
        "paper_id": manifest.get("paper_id"),
        "paper_title": manifest.get("paper_title"),
        "paper_relevance": manifest.get("paper_relevance"),
        "material_count": manifest.get("material_count", 0),
        "evidence_unit_count": manifest.get("evidence_unit_count", 0),
        "failure": manifest.get("failure"),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "output_path": (
            str(run_dir / "output" / "paper_understanding.json")
            if manifest["status"] == "completed"
            else None
        ),
        "review_path": (
            str(run_dir / "review" / "paper_understanding.md")
            if manifest["status"] == "completed"
            else None
        ),
    }


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "understanding").exists():
        return "understanding"
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
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"无法读取{label}：{path}") from exc
    if not isinstance(payload, dict):
        raise AnalysisInputError(f"{label}必须是JSON对象：{path}")
    return payload


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
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _compact_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


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
