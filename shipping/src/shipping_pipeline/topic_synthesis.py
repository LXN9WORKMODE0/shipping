from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm_analysis import (
    AnalysisInputError,
    DEFAULT_MODEL_PROFILE,
    DEFAULT_TOKENIZER_CACHE,
)
from .llm_contracts import validate_evidence_batch
from .llm_json_stage import execute_json_stage
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_projection import project_cards
from .llm_quotes import build_quote_candidates
from .llm_tokenizer import DeepSeekV4TokenCounter
from .topic_review import (
    TOPIC_PAPER_RESULT_SCHEMA_VERSION,
    TOPIC_REVIEW_RUN_SCHEMA_VERSION,
)
from .topic_review_contracts import (
    TOPIC_REVIEW_CONFIG_SCHEMA_VERSION,
    parse_topic_review_config,
    validate_topic_brief,
)
from .topic_synthesis_contracts import (
    TopicSynthesisContractError,
    TopicSynthesisConfig,
    build_topic_evidence_map_schema,
    build_topic_review_outline_schema,
    load_topic_synthesis_config,
    validate_collection,
    validate_topic_evidence_map,
    validate_topic_review_outline,
)
from .topic_synthesis_report import render_topic_synthesis_report


TOPIC_SYNTHESIS_RUN_SCHEMA_VERSION = "llm.topic_synthesis_run.v1"
TOPIC_SYNTHESIS_RESULT_SCHEMA_VERSION = "llm.topic_synthesis_result.v1"
TOPIC_SYNTHESIS_DERIVATIONS_SCHEMA_VERSION = (
    "llm.topic_synthesis_derivations.v1"
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOPIC_SYNTHESIS_CONFIG = (
    PROJECT_ROOT / "config" / "topic-synthesis-default.json"
)

THEME_MAP_SYSTEM_PROMPT = """你是跨论文主题证据编码器。输入是同一综述主题下多篇论文已经通过单篇合同验证的 Evidence。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。
你的任务是建立有限的主题矩阵，不是撰写综述正文，也不是改写 Evidence claim。
每条 Evidence 必须进入至少一个主题，或者明确进入 unassigned_evidence；可以进入多个不同主题，但不得超过输入给定的单条 Evidence 主题数上限，同一主题内不得重复。
assignments 与 unassigned_evidence 是互斥终态：只要某条 Evidence 已进入任一主题，就绝不能再列入 unassigned_evidence。若认为它与其他 Evidence 重复，要么保留其主题 assignment，要么删除全部 assignment 后仅列入 unassigned_evidence，不能同时执行。
relation_type 对多论文主题表示实质关系。只有 assignments 全部来自同一个 paper_id 时才能写 single_source；只要来自两个或更多 paper_id，single_source 就是非法值，必须在 convergent、complementary、divergent、mixed 中选择。不同论文分别提供同一背景的不同侧面时使用 complementary。程序会把真正的单论文主题确定性改写为 single_source，但不会替多论文主题猜测关系。
同一论文的多条 Evidence 只算一个来源，Evidence 数量不代表研究权重。
core、supporting、peripheral 只表示当前主题用途，不表示论文质量。
convergent 表示不同论文对同一问题提供方向一致的证据；complementary 表示回答同一问题的不同环节；divergent 表示结果、条件或建议存在实质差异；不得把措辞差异当作分歧。
主题名称、关注问题、综合价值和缺口都是导航性判断，不得把它们写成脱离 Evidence 的事实结论。"""

OUTLINE_SYSTEM_PROMPT = """你是跨论文综述结构设计器。输入是已经通过代码验证的主题矩阵和精确 Evidence claim。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。
你的任务是形成跨论文综合单元、章节结构和段落级论证动作，不是生成最终综述正文。
除 single_source_context 和 corpus_gap 外，每个综合单元必须引用至少两篇独立论文。
只有 evidence_unit_ids 全部来自同一个 paper_id 时才能写 single_source_context；只要来自两个或更多 paper_id，single_source_context 就是非法值，必须在 consensus、complement、contrast、causal_chain 中选择。不同论文分别提供同一问题的不同环节时使用 complement。程序会把真正的单论文单元确定性改写为 single_source_context，但不会替多论文单元猜测关系。
corpus_gap 的 evidence_unit_ids 必须为空且 gap_ids 非空；其他单元的 evidence_unit_ids 必须非空且 gap_ids 必须为空。
每个综合单元的 evidence_unit_ids 必须全部来自该单元 theme_ids 所声明主题的 assignments；若要引用另一主题的 Evidence，必须同时把该主题 ID 加入 theme_ids，绝不能只借用 Evidence ID。
每个综合单元内部的 theme_ids、evidence_unit_ids 和 gap_ids 都不得出现重复元素；同一 ID 只写一次。
章节不要输出 theme_ids，段落不要输出 paragraph_role 或 evidence_unit_ids；程序会根据段落引用的 synthesis_unit_indexes 自动生成这些字段。
程序根据段落所引单元生成 evidence_synthesis、research_gap 或 evidence_gap_synthesis，并分别生成 Evidence 与 gap 引用；需要先陈述现有证据再指出缺口时可以混合两类单元。
gap_id 只能写入 corpus_gap 单元的 gap_ids 或检索方向的 source_gap_ids，绝不能写入 evidence_unit_ids。
consensus 是方向一致的证据，complement 是同一问题的不同环节，contrast 是实质差异，causal_chain 是由不同证据共同支撑的机制链。
同一论文的多条 Evidence 不能伪装成跨论文一致。不得根据 Evidence 数量推断研究权重。
synthesis_statement、章节说明和 synthesis_move 是基于所引综合单元的分析导航，不得加入所引 Evidence 无法支持的新事实。
每个主题必须进入至少一个综合单元并至少进入一个章节。尽量让综合单元进入段落；未入纲的候选单元会被程序保留并进入覆盖审计，不要为形式覆盖重复安排段落。检索方向只选择真正值得继续检索的缺口，未优先缺口会保留并进入审计，不要为形式覆盖强行生成方向。主题可以在综合讨论或研究缺口章节中再次出现。"""

CONTRACT_REPAIR_SYSTEM_PROMPT = """你是结构化输出合同修正器。
输入包含原始任务、被合同拒绝的完整 JSON 输出，以及精确的错误代码、路径和说明。
只输出修正后的完整 JSON 对象，不要输出 Markdown、解释或差异。
必须继续满足原始任务中的全部 JSON Schema 和语义约束，只修正已指出的问题及其直接连带问题。
不得删除原始任务、放宽约束、编造输入中不存在的 ID，也不得用空值掩盖问题。
这是唯一一次修正机会；输出仍不合格时程序将终止运行。"""


@dataclass(frozen=True)
class TopicSynthesisSource:
    run_id: str
    status: str
    paper_id: str
    paper_title: str
    topic: str
    generation_id: str
    paper_relevance: str
    manifest_sha256: str
    paper_brief_sha256: str
    evidence_sha256: str
    materials_sha256: str
    paper_result: dict[str, Any]
    evidence_units: tuple[dict[str, Any], ...]

    def record(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "paper_id": self.paper_id,
            "paper_title": self.paper_title,
            "topic": self.topic,
            "generation_id": self.generation_id,
            "paper_relevance": self.paper_relevance,
            "evidence_unit_count": len(self.evidence_units),
            "manifest_sha256": self.manifest_sha256,
            "paper_brief_sha256": self.paper_brief_sha256,
            "evidence_sha256": self.evidence_sha256,
            "materials_sha256": self.materials_sha256,
        }


@dataclass(frozen=True)
class TopicSynthesisSnapshot:
    collection: dict[str, Any]
    topic: str
    sources: tuple[TopicSynthesisSource, ...]
    evidence_units: tuple[dict[str, Any], ...]
    evidence_to_paper: dict[str, str]
    model_projection: tuple[dict[str, Any], ...]
    collection_sha256: str
    input_sha256: str


class TopicSynthesisRunner:
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
        timeout: int = 120,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        synthesis_config_path: str | Path = DEFAULT_TOPIC_SYNTHESIS_CONFIG,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        if (
            resolved_run_id in {".", ".."}
            or "/" in resolved_run_id
            or "\\" in resolved_run_id
        ):
            raise AnalysisInputError(
                "跨论文综合 run_id 必须是单个目录名，不能包含路径。"
            )
        run_dir = self.workspace / "_topic_syntheses" / "runs" / resolved_run_id
        if run_dir.exists():
            raise AnalysisInputError(
                f"跨论文综合 run_id 已存在，不能覆盖不可变运行：{resolved_run_id}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot: TopicSynthesisSnapshot | None = None
        profile: ModelProfile | None = None
        config: TopicSynthesisConfig | None = None
        evidence_map: dict[str, Any] | None = None
        outline: dict[str, Any] | None = None
        coverage: dict[str, Any] | None = None
        request_results: list[dict[str, Any]] = []
        failure: dict[str, Any] | None = None
        try:
            snapshot = create_topic_synthesis_snapshot(
                self.workspace,
                Path(collection_path),
            )
            profile = load_model_profile(Path(model_profile_path))
            config = load_topic_synthesis_config(Path(synthesis_config_path))
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
            _write_json(
                run_dir / "manifest.json",
                _build_manifest(
                    run_id=resolved_run_id,
                    status="running",
                    started_at=started_at,
                    snapshot=snapshot,
                    profile=profile,
                    config=config,
                    evidence_map=None,
                    outline=None,
                    coverage=None,
                    request_results=[],
                    failure=None,
                ),
            )

            theme_system, theme_user = build_theme_map_prompts(snapshot, config)
            theme_parsed, theme_result = execute_json_stage(
                run_dir=run_dir,
                stage="theme_map",
                task_name="topic_synthesis_theme_map",
                directory_name=None,
                system_prompt=theme_system,
                user_prompt=theme_user,
                max_output_tokens=config.theme_map_output_tokens(
                    len(snapshot.evidence_units)
                ),
                context={
                    "topic": snapshot.topic,
                    "paper_ids": [row.paper_id for row in snapshot.sources],
                    "evidence_unit_ids": list(snapshot.evidence_to_paper),
                },
                client=client,
                profile=profile,
                token_counter=token_counter,
            )
            request_results.append(theme_result)
            try:
                evidence_map = validate_topic_evidence_map(
                    theme_parsed,
                    topic=snapshot.topic,
                    evidence_to_paper=snapshot.evidence_to_paper,
                    config=config,
                )
            except TopicSynthesisContractError as exc:
                repair_system, repair_user = build_contract_repair_prompts(
                    stage="主题矩阵",
                    original_system_prompt=theme_system,
                    original_user_prompt=theme_user,
                    rejected_payload=theme_parsed,
                    error=exc,
                )
                repaired_theme, repair_result = execute_json_stage(
                    run_dir=run_dir,
                    stage="theme_map_correction",
                    task_name="topic_synthesis_theme_map_correction",
                    directory_name="theme_map/correction",
                    system_prompt=repair_system,
                    user_prompt=repair_user,
                    max_output_tokens=config.theme_map_output_tokens(
                        len(snapshot.evidence_units)
                    ),
                    context={
                        "topic": snapshot.topic,
                        "paper_ids": [row.paper_id for row in snapshot.sources],
                        "evidence_unit_ids": list(snapshot.evidence_to_paper),
                        "contract_error_code": exc.code,
                        "contract_error_path": exc.path,
                    },
                    client=client,
                    profile=profile,
                    token_counter=token_counter,
                )
                request_results.append(repair_result)
                evidence_map = validate_topic_evidence_map(
                    repaired_theme,
                    topic=snapshot.topic,
                    evidence_to_paper=snapshot.evidence_to_paper,
                    config=config,
                )
            _write_json(run_dir / "theme_map" / "validated_theme_map.json", evidence_map)

            outline_system, outline_user = build_outline_prompts(
                snapshot,
                evidence_map,
                config,
            )
            outline_parsed, outline_result = execute_json_stage(
                run_dir=run_dir,
                stage="outline",
                task_name="topic_synthesis_outline",
                directory_name=None,
                system_prompt=outline_system,
                user_prompt=outline_user,
                max_output_tokens=config.outline_output_tokens(
                    len(evidence_map["themes"])
                ),
                context={
                    "topic": snapshot.topic,
                    "theme_ids": [
                        str(row["theme_id"]) for row in evidence_map["themes"]
                    ],
                    "evidence_unit_ids": [
                        str(item["evidence_unit_id"])
                        for theme in evidence_map["themes"]
                        for item in theme["assignments"]
                    ],
                },
                client=client,
                profile=profile,
                token_counter=token_counter,
            )
            request_results.append(outline_result)
            try:
                outline = validate_topic_review_outline(
                    outline_parsed,
                    theme_map=evidence_map,
                    evidence_to_paper=snapshot.evidence_to_paper,
                    config=config,
                )
            except TopicSynthesisContractError as exc:
                repair_system, repair_user = build_contract_repair_prompts(
                    stage="综述提纲",
                    original_system_prompt=outline_system,
                    original_user_prompt=outline_user,
                    rejected_payload=outline_parsed,
                    error=exc,
                )
                repaired_outline, repair_result = execute_json_stage(
                    run_dir=run_dir,
                    stage="outline_correction",
                    task_name="topic_synthesis_outline_correction",
                    directory_name="outline/correction",
                    system_prompt=repair_system,
                    user_prompt=repair_user,
                    max_output_tokens=config.outline_output_tokens(
                        len(evidence_map["themes"])
                    ),
                    context={
                        "topic": snapshot.topic,
                        "theme_ids": [
                            str(row["theme_id"]) for row in evidence_map["themes"]
                        ],
                        "contract_error_code": exc.code,
                        "contract_error_path": exc.path,
                    },
                    client=client,
                    profile=profile,
                    token_counter=token_counter,
                )
                request_results.append(repair_result)
                outline = validate_topic_review_outline(
                    repaired_outline,
                    theme_map=evidence_map,
                    evidence_to_paper=snapshot.evidence_to_paper,
                    config=config,
                )
            _write_json(run_dir / "outline" / "validated_outline.json", outline)
            coverage = build_coverage(snapshot, evidence_map, outline)
            _write_audit_artifacts(
                run_dir,
                snapshot,
                evidence_map,
                outline,
                coverage,
            )

            output = {
                "schema_version": TOPIC_SYNTHESIS_RESULT_SCHEMA_VERSION,
                "topic": snapshot.topic,
                "source_runs": [row.record() for row in snapshot.sources],
                "evidence_map": evidence_map,
                "outline": outline,
                "coverage": coverage,
            }
            evidence_by_id = {
                str(row["evidence_unit_id"]): row for row in snapshot.evidence_units
            }
            report = render_topic_synthesis_report(
                topic=snapshot.topic,
                sources=[row.record() for row in snapshot.sources],
                evidence_map=evidence_map,
                outline=outline,
                evidence_by_id=evidence_by_id,
                evidence_to_paper=snapshot.evidence_to_paper,
                coverage=coverage,
            )
            _atomic_write_text(run_dir / "review" / "topic_synthesis.md", report)
            _atomic_write_json(run_dir / "output" / "topic_synthesis.json", output)
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="completed",
                started_at=started_at,
                snapshot=snapshot,
                profile=profile,
                config=config,
                evidence_map=evidence_map,
                outline=outline,
                coverage=coverage,
                request_results=request_results,
                failure=None,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)
        except Exception as exc:
            _discard_publication(run_dir)
            failure = {
                "error_code": str(getattr(exc, "code", type(exc).__name__)),
                "error_message": str(exc),
            }
            _write_json(run_dir / "audit" / "failure.json", failure)
            request_results = _collect_stage_results(run_dir, request_results)
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="failed",
                started_at=started_at,
                snapshot=snapshot,
                profile=profile,
                config=config,
                evidence_map=evidence_map,
                outline=outline,
                coverage=coverage,
                request_results=request_results,
                failure=failure,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def create_topic_synthesis_snapshot(
    workspace: str | Path,
    collection_path: Path,
) -> TopicSynthesisSnapshot:
    try:
        collection_bytes = collection_path.read_bytes()
        collection_payload = json.loads(collection_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"无法读取跨论文 collection：{collection_path}") from exc
    return create_topic_synthesis_snapshot_from_payload(
        workspace,
        collection_payload,
        collection_bytes=collection_bytes,
    )


def create_topic_synthesis_snapshot_from_payload(
    workspace: str | Path,
    collection_payload: object,
    *,
    collection_bytes: bytes | None = None,
) -> TopicSynthesisSnapshot:
    root = Path(workspace)
    collection = validate_collection(collection_payload)
    if collection_bytes is None:
        collection_bytes = (
            json.dumps(collection, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
    topic = str(collection["topic"])
    sources = tuple(
        _load_source_run(root, str(run_id), expected_topic=topic)
        for run_id in collection["source_run_ids"]
    )
    paper_ids = [row.paper_id for row in sources]
    if len(paper_ids) != len(set(paper_ids)):
        duplicates = sorted(
            paper_id for paper_id, count in Counter(paper_ids).items() if count > 1
        )
        raise AnalysisInputError(
            f"collection 中同一论文只能选择一个运行：{duplicates}"
        )
    evidence_units: list[dict[str, Any]] = []
    evidence_to_paper: dict[str, str] = {}
    for source in sources:
        for unit in source.evidence_units:
            evidence_id = str(unit["evidence_unit_id"])
            if evidence_id in evidence_to_paper:
                raise AnalysisInputError(
                    f"跨论文 Evidence ID 重复：{evidence_id}"
                )
            evidence_to_paper[evidence_id] = source.paper_id
            evidence_units.append(unit)
    if not evidence_units:
        raise AnalysisInputError("跨论文综合至少需要一条 Evidence。")
    projection = tuple(_project_source(source) for source in sources)
    source_hashes = [row.record() for row in sources]
    input_sha256 = _sha256_text(
        _compact_json(
            {
                "collection": collection,
                "sources": source_hashes,
            }
        )
    )
    return TopicSynthesisSnapshot(
        collection=collection,
        topic=topic,
        sources=sources,
        evidence_units=tuple(evidence_units),
        evidence_to_paper=evidence_to_paper,
        model_projection=projection,
        collection_sha256=_sha256_bytes(collection_bytes),
        input_sha256=input_sha256,
    )


def build_theme_map_prompts(
    snapshot: TopicSynthesisSnapshot,
    config: TopicSynthesisConfig,
) -> tuple[str, str]:
    schema = build_topic_evidence_map_schema(
        list(snapshot.evidence_to_paper),
        config,
    )
    payload = {
        "任务": "把多篇论文的严格 Evidence 编为跨论文主题矩阵",
        "综述主题": snapshot.topic,
        "主题上限": config.max_themes,
        "单条Evidence主题数上限": config.max_themes_per_evidence,
        "输出JSONSchema": schema,
        "论文简报投影": list(snapshot.model_projection),
    }
    return THEME_MAP_SYSTEM_PROMPT, _compact_json(payload)


def build_outline_prompts(
    snapshot: TopicSynthesisSnapshot,
    evidence_map: dict[str, Any],
    config: TopicSynthesisConfig,
) -> tuple[str, str]:
    evidence_by_id = {
        str(row["evidence_unit_id"]): row for row in snapshot.evidence_units
    }
    source_by_paper = {row.paper_id: row for row in snapshot.sources}
    assigned_ids = list(
        dict.fromkeys(
            str(item["evidence_unit_id"])
            for theme in evidence_map["themes"]
            for item in theme["assignments"]
        )
    )
    schema = build_topic_review_outline_schema(
        theme_ids=[str(row["theme_id"]) for row in evidence_map["themes"]],
        assigned_evidence_ids=assigned_ids,
        gap_ids=[str(row["gap_id"]) for row in evidence_map["corpus_gaps"]],
        config=config,
    )
    theme_projection = []
    for theme in evidence_map["themes"]:
        projected = {
            key: copy_value
            for key, copy_value in theme.items()
            if key != "assignments"
        }
        projected["assignments"] = []
        for assignment in theme["assignments"]:
            evidence_id = str(assignment["evidence_unit_id"])
            unit = evidence_by_id[evidence_id]
            paper_id = snapshot.evidence_to_paper[evidence_id]
            source = source_by_paper[paper_id]
            projected["assignments"].append(
                {
                    "evidence_unit_id": evidence_id,
                    "role": assignment["role"],
                    "paper_id": paper_id,
                    "paper_title": source.paper_title,
                    "paper_relevance": source.paper_relevance,
                    "claim": unit["claim"],
                    "evidence_type": unit["evidence_type"],
                    "confidence": unit["confidence"],
                    "caveats": list(unit.get("caveats", [])),
                }
            )
        theme_projection.append(projected)
    payload = {
        "任务": "把已验证主题矩阵组织成跨论文综合单元和段落级综述提纲",
        "综述主题": snapshot.topic,
        "综合单元上限": config.max_synthesis_units,
        "章节上限": config.max_sections,
        "每节段落上限": config.max_paragraphs_per_section,
        "输出JSONSchema": schema,
        "主题矩阵": theme_projection,
        "语料缺口": evidence_map["corpus_gaps"],
    }
    return OUTLINE_SYSTEM_PROMPT, _compact_json(payload)


def build_contract_repair_prompts(
    *,
    stage: str,
    original_system_prompt: str,
    original_user_prompt: str,
    rejected_payload: dict[str, Any],
    error: TopicSynthesisContractError,
) -> tuple[str, str]:
    payload = {
        "任务": f"修正被合同拒绝的{stage}完整 JSON 输出",
        "原始系统约束": original_system_prompt,
        "原始任务输入": json.loads(original_user_prompt),
        "合同错误": {
            "error_code": error.code,
            "error_path": error.path,
            "error_message": error.detail,
        },
        "被拒绝的完整输出": rejected_payload,
        "修正要求": "返回满足全部原始约束的完整 JSON 对象。",
    }
    return CONTRACT_REPAIR_SYSTEM_PROMPT, _compact_json(payload)


def build_coverage(
    snapshot: TopicSynthesisSnapshot,
    evidence_map: dict[str, Any],
    outline: dict[str, Any],
) -> dict[str, Any]:
    assignment_counts = Counter(
        str(item["evidence_unit_id"])
        for theme in evidence_map["themes"]
        for item in theme["assignments"]
    )
    assigned = {
        str(item["evidence_unit_id"])
        for theme in evidence_map["themes"]
        for item in theme["assignments"]
    }
    unassigned = {
        str(row["evidence_unit_id"])
        for row in evidence_map["unassigned_evidence"]
    }
    outline_used = {
        str(evidence_id)
        for section in outline["sections"]
        for paragraph in section["paragraphs"]
        for evidence_id in paragraph["evidence_unit_ids"]
    }
    unused = sorted(assigned - outline_used)
    unplaced_synthesis_unit_ids = list(
        outline.get("unplaced_synthesis_unit_ids", [])
    )
    unprioritized_gap_ids = list(outline.get("unprioritized_gap_ids", []))
    cross_paper_theme_ids = [
        str(row["theme_id"])
        for row, derivation in zip(
            evidence_map["themes"],
            evidence_map["derivations"]["relation_types"],
            strict=True,
        )
        if int(derivation["distinct_paper_count"]) >= 2
    ]
    cross_paper_synthesis_unit_ids = [
        str(row["synthesis_unit_id"])
        for row, derivation in zip(
            outline["synthesis_units"],
            outline["derivations"]["unit_types"],
            strict=True,
        )
        if (
            str(row["unit_type"]) != "corpus_gap"
            and int(derivation["distinct_paper_count"]) >= 2
        )
    ]
    quality_flags = []
    if not cross_paper_synthesis_unit_ids:
        quality_flags.append("cross_paper_synthesis_absent")
    return {
        "source_evidence_count": len(snapshot.evidence_units),
        "theme_assignment_count": sum(assignment_counts.values()),
        "theme_assigned_count": len(assigned),
        "single_theme_evidence_count": sum(
            count == 1 for count in assignment_counts.values()
        ),
        "multi_theme_evidence_count": sum(
            count > 1 for count in assignment_counts.values()
        ),
        "multi_theme_evidence_ids": sorted(
            evidence_id
            for evidence_id, count in assignment_counts.items()
            if count > 1
        ),
        "max_observed_themes_per_evidence": max(
            assignment_counts.values(),
            default=0,
        ),
        "theme_unassigned_count": len(unassigned),
        "outline_used_count": len(outline_used),
        "assigned_but_unused_count": len(unused),
        "assigned_but_unused_evidence_ids": unused,
        "synthesis_unit_count": len(outline["synthesis_units"]),
        "placed_synthesis_unit_count": (
            len(outline["synthesis_units"]) - len(unplaced_synthesis_unit_ids)
        ),
        "unplaced_synthesis_unit_count": len(unplaced_synthesis_unit_ids),
        "unplaced_synthesis_unit_ids": unplaced_synthesis_unit_ids,
        "corpus_gap_count": len(evidence_map["corpus_gaps"]),
        "prioritized_gap_count": (
            len(evidence_map["corpus_gaps"]) - len(unprioritized_gap_ids)
        ),
        "unprioritized_gap_count": len(unprioritized_gap_ids),
        "unprioritized_gap_ids": unprioritized_gap_ids,
        "cross_paper_theme_count": len(cross_paper_theme_ids),
        "cross_paper_theme_ids": cross_paper_theme_ids,
        "cross_paper_synthesis_unit_count": len(
            cross_paper_synthesis_unit_ids
        ),
        "cross_paper_synthesis_unit_ids": cross_paper_synthesis_unit_ids,
        "quality_flags": quality_flags,
        "single_source_theme_ids": [
            str(row["theme_id"])
            for row in evidence_map["themes"]
            if str(row["relation_type"]) == "single_source"
        ],
        "partial_source_run_ids": [
            row.run_id
            for row in snapshot.sources
            if row.status
            in {"completed_with_revisit_failure", "completed_with_failures"}
        ],
    }


def _load_source_run(
    workspace: Path,
    run_id: str,
    *,
    expected_topic: str,
) -> TopicSynthesisSource:
    run_dir = workspace / "_topic_reviews" / "runs" / run_id
    manifest_path = run_dir / "manifest.json"
    brief_path = run_dir / "output" / "paper_brief.json"
    evidence_path = run_dir / "evidence" / "evidence_units.jsonl"
    materials_path = run_dir / "input" / "materials.jsonl"
    manifest, manifest_bytes = _read_json_bytes(manifest_path, "单篇 manifest")
    result, brief_bytes = _read_json_bytes(brief_path, "单篇 paper_brief")
    evidence_units, evidence_bytes = _read_jsonl_bytes(
        evidence_path,
        "单篇 Evidence",
    )
    materials, materials_bytes = _read_jsonl_bytes(
        materials_path,
        "单篇冻结材料",
    )
    if manifest.get("schema_version") != TOPIC_REVIEW_RUN_SCHEMA_VERSION:
        raise AnalysisInputError(
            f"来源运行 {run_id!r} 不是当前 {TOPIC_REVIEW_RUN_SCHEMA_VERSION}。"
        )
    if result.get("schema_version") != TOPIC_PAPER_RESULT_SCHEMA_VERSION:
        raise AnalysisInputError(
            f"来源运行 {run_id!r} 不是当前 {TOPIC_PAPER_RESULT_SCHEMA_VERSION}。"
        )
    status = str(manifest.get("status", ""))
    if status not in {
        "completed",
        "completed_with_revisit_failure",
        "completed_with_failures",
    }:
        raise AnalysisInputError(
            f"来源运行 {run_id!r} 状态不可用于跨论文综合：{status!r}。"
        )
    revisit = result.get("revisit")
    if status == "completed_with_revisit_failure":
        if not isinstance(revisit, dict) or str(revisit.get("status", "")) != "failed":
            raise AnalysisInputError(
                f"来源运行 {run_id!r} 的部分状态与 revisit 结果不一致。"
            )
    elif status == "completed_with_failures":
        if int(manifest.get("failure_count") or 0) < 1:
            raise AnalysisInputError(
                f"来源运行 {run_id!r} 的部分状态没有对应失败账本。"
            )
    elif isinstance(revisit, dict) and str(revisit.get("status", "")) == "failed":
        raise AnalysisInputError(
            f"来源运行 {run_id!r} 的完成状态与 revisit 失败结果不一致。"
        )
    if str(manifest.get("run_id", "")) != run_id:
        raise AnalysisInputError(f"来源运行目录与 manifest.run_id 不一致：{run_id}")
    topic = str(manifest.get("topic", ""))
    if topic != expected_topic or str(result.get("topic", "")) != expected_topic:
        raise AnalysisInputError(
            f"来源运行 {run_id!r} 的综述主题与 collection 不一致。"
        )
    paper_id = str(manifest.get("paper_id", ""))
    paper_title = str(manifest.get("paper_title", ""))
    relevance = str(manifest.get("paper_relevance", ""))
    if (
        not paper_id
        or str(result.get("paper_id", "")) != paper_id
        or str(result.get("paper_title", "")) != paper_title
        or str(result.get("paper_relevance", "")) != relevance
    ):
        raise AnalysisInputError(f"来源运行 {run_id!r} 的论文身份字段不一致。")
    review_config_payload = manifest.get("review_config")
    if not isinstance(review_config_payload, dict):
        raise AnalysisInputError(f"来源运行 {run_id!r} 缺少 review_config。")
    if (
        review_config_payload.get("schema_version")
        != TOPIC_REVIEW_CONFIG_SCHEMA_VERSION
    ):
        raise AnalysisInputError(
            f"来源运行 {run_id!r} 不是当前 {TOPIC_REVIEW_CONFIG_SCHEMA_VERSION}。"
        )
    review_config = parse_topic_review_config(review_config_payload)
    if not evidence_units:
        raise AnalysisInputError(f"来源运行 {run_id!r} 没有 Evidence。")
    evidence_ids = [str(row.get("evidence_unit_id", "")) for row in evidence_units]
    if any(not row for row in evidence_ids) or len(evidence_ids) != len(set(evidence_ids)):
        raise AnalysisInputError(f"来源运行 {run_id!r} 的 Evidence ID 为空或重复。")
    if int(manifest.get("evidence_unit_count", -1)) != len(evidence_units):
        raise AnalysisInputError(
            f"来源运行 {run_id!r} 的 Evidence 数量与 manifest 不一致。"
        )
    _validate_source_evidence(
        run_id=run_id,
        paper_id=paper_id,
        generation_id=str(manifest.get("generation_id", "")),
        evidence_units=evidence_units,
        materials=materials,
    )
    brief = result.get("brief")
    if not isinstance(brief, dict):
        raise AnalysisInputError(f"来源运行 {run_id!r} 缺少正式 brief。")
    validate_topic_brief(
        brief,
        evidence_units,
        relevance=relevance,
        config=review_config,
    )
    return TopicSynthesisSource(
        run_id=run_id,
        status=status,
        paper_id=paper_id,
        paper_title=paper_title,
        topic=topic,
        generation_id=str(manifest.get("generation_id", "")),
        paper_relevance=relevance,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        paper_brief_sha256=_sha256_bytes(brief_bytes),
        evidence_sha256=_sha256_bytes(evidence_bytes),
        materials_sha256=_sha256_bytes(materials_bytes),
        paper_result=result,
        evidence_units=tuple(evidence_units),
    )


def _validate_source_evidence(
    *,
    run_id: str,
    paper_id: str,
    generation_id: str,
    evidence_units: list[dict[str, Any]],
    materials: list[dict[str, Any]],
) -> None:
    if not generation_id:
        raise AnalysisInputError(f"来源运行 {run_id!r} 缺少 generation_id。")
    materials_by_id: dict[str, dict[str, Any]] = {}
    for index, material in enumerate(materials):
        material_id = str(material.get("material_id", ""))
        if not material_id or material_id in materials_by_id:
            raise AnalysisInputError(
                f"来源运行 {run_id!r} 的冻结材料 ID 为空或重复：index={index}。"
            )
        if str(material.get("paper_id", "")) != paper_id:
            raise AnalysisInputError(
                f"来源运行 {run_id!r} 的冻结材料 paper_id 不一致：{material_id}"
            )
        if str(material.get("generation_id", "")) != generation_id:
            raise AnalysisInputError(
                f"来源运行 {run_id!r} 的冻结材料 generation_id 不一致："
                f"{material_id}"
            )
        materials_by_id[material_id] = material
    if not materials_by_id:
        raise AnalysisInputError(f"来源运行 {run_id!r} 没有冻结材料。")

    required_unit_fields = {
        "claim",
        "evidence_type",
        "citations",
        "relevance",
        "confidence",
        "caveats",
        "evidence_anchor_id",
        "evidence_unit_id",
        "request_id",
        "batch_id",
    }
    required_citation_fields = {
        "material_id",
        "quote_id",
        "quote",
        "source_ref",
        "source_spans",
        "card_title",
        "confidence_flags",
        "quality_flags",
    }
    for unit_index, unit in enumerate(evidence_units):
        if set(unit) != required_unit_fields:
            raise AnalysisInputError(
                f"来源运行 {run_id!r} 的 Evidence 字段不完整："
                f"index={unit_index}, missing="
                f"{sorted(required_unit_fields - set(unit))}, "
                f"unknown={sorted(set(unit) - required_unit_fields)}"
            )
        citations = unit.get("citations")
        if not isinstance(citations, list) or not citations:
            raise AnalysisInputError(
                f"来源运行 {run_id!r} 的 Evidence 缺少逐字引文：index={unit_index}"
            )
        material_ids = {
            str(citation.get("material_id", ""))
            for citation in citations
            if isinstance(citation, dict)
        }
        if len(material_ids) != 1:
            raise AnalysisInputError(
                f"来源运行 {run_id!r} 的单条 Evidence 必须绑定同一张 Card："
                f"index={unit_index}"
            )
        material_id = next(iter(material_ids))
        material = materials_by_id.get(material_id)
        if material is None:
            raise AnalysisInputError(
                f"来源运行 {run_id!r} 的 Evidence 引用未知 Card：{material_id}"
            )
        projected = project_cards([material])
        quote_candidates = build_quote_candidates(projected)
        candidates_by_id = {
            str(row["quote_id"]): row for row in quote_candidates
        }
        model_citations: list[dict[str, str]] = []
        for citation_index, citation in enumerate(citations):
            if not isinstance(citation, dict) or set(citation) != required_citation_fields:
                raise AnalysisInputError(
                    f"来源运行 {run_id!r} 的 citation 字段不完整："
                    f"evidence={unit_index}, citation={citation_index}"
                )
            quote_id = str(citation.get("quote_id", ""))
            candidate = candidates_by_id.get(quote_id)
            if candidate is None:
                raise AnalysisInputError(
                    f"来源运行 {run_id!r} 的 citation 引用未知 quote_id："
                    f"{quote_id}"
                )
            if (
                str(candidate["material_id"]) != material_id
                or str(candidate["text"]) != str(citation.get("quote", ""))
            ):
                raise AnalysisInputError(
                    f"来源运行 {run_id!r} 的 citation 不是冻结 Card 的逐字引文："
                    f"{quote_id}"
                )
            expected_source_ref = (
                material.get("source_span")
                or material.get("content_ref")
                or {}
            )
            if citation.get("source_ref") != expected_source_ref:
                raise AnalysisInputError(
                    f"来源运行 {run_id!r} 的 citation 来源坐标与 Card 不一致："
                    f"{quote_id}"
                )
            if citation.get("source_spans") != list(
                material.get("source_spans", [])
            ):
                raise AnalysisInputError(
                    f"来源运行 {run_id!r} 的 citation 来源片段与 Card 不一致："
                    f"{quote_id}"
                )
            if str(citation.get("card_title", "")) != str(
                material.get("clean_title")
                or material.get("raw_title")
                or "未命名材料"
            ):
                raise AnalysisInputError(
                    f"来源运行 {run_id!r} 的 citation Card 标题不一致："
                    f"{quote_id}"
                )
            if list(citation.get("confidence_flags", [])) != list(
                material.get("confidence_flags", [])
            ) or list(citation.get("quality_flags", [])) != list(
                material.get("quality_flags", [])
            ):
                raise AnalysisInputError(
                    f"来源运行 {run_id!r} 的 citation 质量标记与 Card 不一致："
                    f"{quote_id}"
                )
            model_citations.append({"quote_id": quote_id})
        if str(unit.get("batch_id", "")) != str(unit.get("request_id", "")):
            raise AnalysisInputError(
                f"来源运行 {run_id!r} 的 Evidence request/batch 身份不一致："
                f"index={unit_index}"
            )
        validate_evidence_batch(
            {
                "schema_version": "llm.evidence_batch.v4",
                "material_results": [
                    {
                        "material_id": material_id,
                        "disposition": "evidence",
                        "reason_code": "direct_evidence",
                        "evidence_units": [
                            {
                                "claim": unit["claim"],
                                "evidence_type": unit["evidence_type"],
                                "citations": model_citations,
                                "relevance": unit["relevance"],
                                "confidence": unit["confidence"],
                                "caveats": unit["caveats"],
                            }
                        ],
                    }
                ],
            },
            [material],
            quote_candidates,
        )


def _project_source(source: TopicSynthesisSource) -> dict[str, Any]:
    brief = source.paper_result["brief"]
    key_point_ids = {
        str(evidence_id)
        for row in brief["key_points"]
        for evidence_id in row["evidence_unit_ids"]
    }
    revisit = source.paper_result.get("revisit")
    if isinstance(revisit, dict):
        gaps = list(revisit.get("remaining_evidence_gaps", []))
    else:
        gaps = list(brief.get("evidence_gaps", []))
    return {
        "paper_id": source.paper_id,
        "paper_title": source.paper_title,
        "paper_relevance": source.paper_relevance,
        "source_status": source.status,
        "topic_contribution": str(brief["topic_contribution"]["statement"]),
        "cautions": [str(row["statement"]) for row in brief["cautions"]],
        "remaining_evidence_gaps": gaps,
        "evidence": [
            {
                "evidence_unit_id": str(row["evidence_unit_id"]),
                "claim": str(row["claim"]),
                "evidence_type": str(row["evidence_type"]),
                "confidence": str(row["confidence"]),
                "caveats": list(row.get("caveats", [])),
                "is_key_point": str(row["evidence_unit_id"]) in key_point_ids,
            }
            for row in source.evidence_units
        ],
    }


def _write_input_artifacts(
    run_dir: Path,
    snapshot: TopicSynthesisSnapshot,
    profile: ModelProfile,
    config: TopicSynthesisConfig,
) -> None:
    _write_json(run_dir / "input" / "collection.json", snapshot.collection)
    _write_jsonl(
        run_dir / "input" / "source_runs.jsonl",
        [row.record() for row in snapshot.sources],
    )
    _write_jsonl(
        run_dir / "input" / "paper_briefs.jsonl",
        [row.paper_result for row in snapshot.sources],
    )
    _write_jsonl(
        run_dir / "input" / "evidence_catalog.jsonl",
        list(snapshot.evidence_units),
    )
    _write_json(
        run_dir / "input" / "model_projection.json",
        list(snapshot.model_projection),
    )
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(run_dir / "input" / "synthesis_config.json", config.to_dict())


def _write_audit_artifacts(
    run_dir: Path,
    snapshot: TopicSynthesisSnapshot,
    evidence_map: dict[str, Any],
    outline: dict[str, Any],
    coverage: dict[str, Any],
) -> None:
    evidence_by_id = {
        str(row["evidence_unit_id"]): row for row in snapshot.evidence_units
    }
    _write_jsonl(
        run_dir / "audit" / "unassigned_evidence.jsonl",
        [
            {
                **row,
                "paper_id": snapshot.evidence_to_paper[str(row["evidence_unit_id"])],
                "claim": evidence_by_id[str(row["evidence_unit_id"])]["claim"],
            }
            for row in evidence_map["unassigned_evidence"]
        ],
    )
    _write_jsonl(
        run_dir / "audit" / "unused_assigned_evidence.jsonl",
        [
            {
                "evidence_unit_id": evidence_id,
                "paper_id": snapshot.evidence_to_paper[evidence_id],
                "claim": evidence_by_id[evidence_id]["claim"],
            }
            for evidence_id in coverage["assigned_but_unused_evidence_ids"]
        ],
    )
    _write_jsonl(
        run_dir / "audit" / "source_statuses.jsonl",
        [row.record() for row in snapshot.sources],
    )
    unplaced_ids = set(coverage["unplaced_synthesis_unit_ids"])
    _write_jsonl(
        run_dir / "audit" / "unplaced_synthesis_units.jsonl",
        [
            row
            for row in outline["synthesis_units"]
            if str(row["synthesis_unit_id"]) in unplaced_ids
        ],
    )
    unprioritized_gap_ids = set(coverage["unprioritized_gap_ids"])
    _write_jsonl(
        run_dir / "audit" / "unprioritized_corpus_gaps.jsonl",
        [
            row
            for row in evidence_map["corpus_gaps"]
            if str(row["gap_id"]) in unprioritized_gap_ids
        ],
    )
    _write_json(run_dir / "audit" / "coverage.json", coverage)
    _write_json(
        run_dir / "audit" / "contract_derivations.json",
        {
            "schema_version": TOPIC_SYNTHESIS_DERIVATIONS_SCHEMA_VERSION,
            "theme_map": evidence_map["derivations"],
            "outline": outline["derivations"],
        },
    )


def _build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    snapshot: TopicSynthesisSnapshot | None,
    profile: ModelProfile | None,
    config: TopicSynthesisConfig | None,
    evidence_map: dict[str, Any] | None,
    outline: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
    request_results: list[dict[str, Any]],
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": TOPIC_SYNTHESIS_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": None if status == "running" else _now(),
        "topic": snapshot.topic if snapshot else None,
        "collection_sha256": snapshot.collection_sha256 if snapshot else None,
        "input_sha256": snapshot.input_sha256 if snapshot else None,
        "source_runs": (
            [row.record() for row in snapshot.sources] if snapshot else []
        ),
        "source_paper_count": len(snapshot.sources) if snapshot else 0,
        "source_evidence_count": len(snapshot.evidence_units) if snapshot else 0,
        "provider": profile.provider if profile else None,
        "model": profile.request_model if profile else None,
        "model_profile_id": profile.profile_id if profile else None,
        "model_profile_sha256": profile.sha256 if profile else None,
        "synthesis_config": config.to_dict() if config else None,
        "theme_count": len(evidence_map["themes"]) if evidence_map else 0,
        "synthesis_unit_count": (
            len(outline["synthesis_units"]) if outline else 0
        ),
        "section_count": len(outline["sections"]) if outline else 0,
        "paragraph_count": (
            sum(len(row["paragraphs"]) for row in outline["sections"])
            if outline
            else 0
        ),
        "coverage": coverage,
        "requests": request_results,
        "request_count": len(request_results),
        "usage": _sum_usage(request_results),
        "failure_count": 1 if failure else 0,
        "failure_codes": [failure["error_code"]] if failure else [],
        "failure": failure,
        "outputs": (
            {
                "topic_synthesis": "output/topic_synthesis.json",
                "review": "review/topic_synthesis.md",
                "coverage": "audit/coverage.json",
                "contract_derivations": "audit/contract_derivations.json",
            }
            if status == "completed"
            else {}
        ),
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
        try:
            row = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(row, dict) and str(row.get("request_id", "")):
            by_id[str(row["request_id"])] = row
            ordered.append(row)
    ordered.sort(key=lambda row: str(row.get("started_at", "")))
    known = {str(row["request_id"]) for row in ordered}
    ordered.extend(row for key, row in by_id.items() if key not in known)
    return ordered


def _sum_usage(request_results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(
            int(row.get("usage", {}).get(key) or 0)
            for row in request_results
        )
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    review_path = run_dir / "review" / "topic_synthesis.md"
    completed = manifest.get("status") == "completed"
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "topic": manifest.get("topic"),
        "source_paper_count": manifest.get("source_paper_count", 0),
        "source_evidence_count": manifest.get("source_evidence_count", 0),
        "theme_count": manifest.get("theme_count", 0),
        "synthesis_unit_count": manifest.get("synthesis_unit_count", 0),
        "section_count": manifest.get("section_count", 0),
        "request_count": manifest.get("request_count", 0),
        "usage": dict(manifest.get("usage", {})),
        "failure_codes": list(manifest.get("failure_codes", [])),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "review_path": (
            str(review_path) if completed and review_path.is_file() else None
        ),
    }


def _discard_publication(run_dir: Path) -> None:
    for path in (
        run_dir / "output" / "topic_synthesis.json",
        run_dir / "output" / "topic_synthesis.json.tmp",
        run_dir / "review" / "topic_synthesis.md",
        run_dir / "review" / "topic_synthesis.md.tmp",
    ):
        path.unlink(missing_ok=True)


def _read_json_bytes(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"无法读取{label}：{path}") from exc
    if not isinstance(payload, dict):
        raise AnalysisInputError(f"{label}必须是 JSON 对象：{path}")
    return payload, raw


def _read_jsonl_bytes(
    path: Path,
    label: str,
) -> tuple[list[dict[str, Any]], bytes]:
    try:
        raw = path.read_bytes()
        lines = raw.decode("utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AnalysisInputError(f"无法读取{label}：{path}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AnalysisInputError(
                f"{label}第 {line_number} 行不是合法 JSON：{path}"
            ) from exc
        if not isinstance(payload, dict):
            raise AnalysisInputError(
                f"{label}第 {line_number} 行必须是 JSON 对象：{path}"
            )
        rows.append(payload)
    return rows, raw


def _compact_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


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


def _atomic_write_json(path: Path, payload: object) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)
