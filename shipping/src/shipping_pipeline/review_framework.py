from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .llm_analysis import (
    AnalysisInputError,
    DEFAULT_MODEL_PROFILE,
    DEFAULT_TOKENIZER_CACHE,
)
from .llm_json_stage import execute_json_stage
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .reference_catalog import (
    REFERENCE_CATALOG_RUN_SCHEMA_VERSION,
    REFERENCE_CATALOG_SCHEMA_VERSION,
)
from .research_landscape import (
    RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION,
    ResearchLandscapeSnapshot,
    create_research_landscape_snapshot,
)
from .research_landscape_contracts import (
    build_research_landscape_schema,
    load_research_landscape_config,
    validate_research_landscape,
)
from .review_framework_contracts import (
    REVIEW_FRAMEWORK_SCHEMA_VERSION,
    build_review_framework_draft_schema,
    derive_review_framework,
    validate_review_framework_draft,
)


REVIEW_FRAMEWORK_RUN_SCHEMA_VERSION = "llm.review_framework_run.v1"
FRAMEWORK_MAX_OUTPUT_TOKENS = 16384
PROJECT_ROOT = Path(__file__).resolve().parents[2]

REVIEW_FRAMEWORK_SYSTEM_PROMPT = """你是文献综述的结构设计器。输入是一份已验证的Research Landscape和对应题录状态，只输出符合给定JSON Schema的JSON对象，不要输出Markdown、解释或Schema之外字段。
你的任务是设计完整综述的章节问题和跨论文比较任务，不是撰写正文，也不是重新分析单篇论文。
必须恰好输出一个introduction、至少一个body和一个conclusion；introduction必须是第一节，conclusion必须是最后一节。每个Landscape dimension必须至少进入一个body章节。
章节只选择dimension、明确分歧和本次语料缺口。不得输出paper_id、contribution_id、citation key、framework_id或section_id；这些来源绑定由程序根据dimension和controversy确定性生成。
body章节的required_comparisons必须是具体可执行的比较，例如比较作用机制、验证水平、适用边界或结果类型，不能只写“综合分析相关研究”。
required_comparisons如果点名某篇论文，该论文必须属于本节所选dimension；需要跨维度比较时必须把相关dimension_index全部加入本节，不能假设程序或写作阶段会补充未绑定论文。
corpus_limitations只描述本次显式语料和题录状态的边界，不得声称整个研究领域缺少文献。没有明确分歧时controversy_ids必须为空。
introduction和conclusion可以绑定全部或部分dimension，但不能代替body对dimension的覆盖。"""


@dataclass(frozen=True)
class ReviewFrameworkSnapshot:
    topic: str
    review_goal: str
    corpus_scope: str
    landscape_run_id: str
    landscape_manifest_sha256: str
    landscape_output_sha256: str
    landscape: dict[str, Any]
    reference_catalog_run_id: str
    reference_manifest_sha256: str
    reference_catalog_sha256: str
    reference_by_paper: dict[str, dict[str, Any]]
    input_sha256: str


def create_review_framework_snapshot(
    workspace: str | Path,
    *,
    landscape_run_id: str,
    reference_catalog_run_id: str,
    review_goal: str | None = None,
) -> ReviewFrameworkSnapshot:
    workspace_path = Path(workspace)
    _safe_segment(landscape_run_id, "landscape_run_id")
    _safe_segment(reference_catalog_run_id, "reference_catalog_run_id")
    (
        landscape,
        landscape_snapshot,
        landscape_manifest_sha256,
        landscape_output_sha256,
    ) = _load_landscape_source(workspace_path, landscape_run_id)
    (
        reference_by_paper,
        reference_manifest_sha256,
        reference_catalog_sha256,
    ) = _load_reference_catalog_source(
        workspace_path,
        reference_catalog_run_id,
        expected_topic=str(landscape["topic"]),
        expected_paper_ids={
            source.paper_id for source in landscape_snapshot.sources
        },
    )
    resolved_review_goal = (
        str(review_goal).strip()
        if review_goal is not None
        else str(landscape["review_goal"]).strip()
    )
    if not resolved_review_goal:
        raise AnalysisInputError("review_goal不能为空。")
    input_sha256 = _sha256_json(
        {
            "landscape_run_id": landscape_run_id,
            "landscape_manifest_sha256": landscape_manifest_sha256,
            "landscape_output_sha256": landscape_output_sha256,
            "reference_catalog_run_id": reference_catalog_run_id,
            "reference_manifest_sha256": reference_manifest_sha256,
            "reference_catalog_sha256": reference_catalog_sha256,
            "review_goal": resolved_review_goal,
        }
    )
    return ReviewFrameworkSnapshot(
        topic=str(landscape["topic"]),
        review_goal=resolved_review_goal,
        corpus_scope=str(landscape["corpus_scope"]),
        landscape_run_id=landscape_run_id,
        landscape_manifest_sha256=landscape_manifest_sha256,
        landscape_output_sha256=landscape_output_sha256,
        landscape=landscape,
        reference_catalog_run_id=reference_catalog_run_id,
        reference_manifest_sha256=reference_manifest_sha256,
        reference_catalog_sha256=reference_catalog_sha256,
        reference_by_paper=reference_by_paper,
        input_sha256=input_sha256,
    )


class ReviewFrameworkRunner:
    def __init__(
        self,
        workspace: str | Path,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
        snapshot_loader: Callable[..., ReviewFrameworkSnapshot] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.snapshot_loader = snapshot_loader or create_review_framework_snapshot

    def run(
        self,
        *,
        landscape_run_id: str,
        reference_catalog_run_id: str,
        review_goal: str | None = None,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved_run_id, "run_id")
        run_dir = (
            self.workspace / "_review_frameworks" / "runs" / resolved_run_id
        )
        if run_dir.exists():
            raise AnalysisInputError(
                f"Review Framework run_id已存在，不能覆盖不可变运行："
                f"{resolved_run_id}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot: ReviewFrameworkSnapshot | None = None
        profile: ModelProfile | None = None
        stage_result: dict[str, Any] | None = None
        framework: dict[str, Any] | None = None
        try:
            snapshot = self.snapshot_loader(
                self.workspace,
                landscape_run_id=landscape_run_id,
                reference_catalog_run_id=reference_catalog_run_id,
                review_goal=review_goal,
            )
            profile = load_model_profile(Path(model_profile_path))
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
            schema = build_review_framework_draft_schema(
                topic=snapshot.topic,
                review_goal=snapshot.review_goal,
                dimension_indexes=[
                    int(row["dimension_index"])
                    for row in snapshot.landscape["dimensions"]
                ],
                controversy_ids=[
                    str(row["disagreement_id"])
                    for row in snapshot.landscape["disagreements"]
                ],
                corpus_gap_indexes=list(
                    range(1, len(snapshot.landscape["corpus_gaps"]) + 1)
                ),
            )
            _write_input_artifacts(run_dir, snapshot, profile, schema)
            user_prompt = build_review_framework_prompt(snapshot, schema=schema)
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="framework",
                task_name="review_framework",
                directory_name="framework",
                system_prompt=REVIEW_FRAMEWORK_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_output_tokens=FRAMEWORK_MAX_OUTPUT_TOKENS,
                context={
                    "topic": snapshot.topic,
                    "review_goal": snapshot.review_goal,
                    "landscape_run_id": snapshot.landscape_run_id,
                    "reference_catalog_run_id": (
                        snapshot.reference_catalog_run_id
                    ),
                    "input_sha256": snapshot.input_sha256,
                },
                client=client,
                profile=profile,
                token_counter=token_counter,
            )
            draft = validate_review_framework_draft(
                parsed,
                schema=schema,
                landscape=snapshot.landscape,
            )
            _write_json(
                run_dir / "framework" / "validated_draft.json",
                draft,
            )
            framework = derive_review_framework(
                draft,
                landscape=snapshot.landscape,
                reference_by_paper=snapshot.reference_by_paper,
                source_landscape_id=str(
                    snapshot.landscape["landscape_id"]
                ),
            )
            _write_json(
                run_dir / "output" / "review_framework.json",
                framework,
            )
            derivation = _build_derivation(snapshot, draft, framework)
            coverage = _build_coverage(snapshot, framework)
            _write_json(run_dir / "audit" / "derivation.json", derivation)
            _write_json(run_dir / "audit" / "coverage.json", coverage)
            _write_text(
                run_dir / "review" / "review_framework.md",
                render_review_framework(framework),
            )
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="completed",
                started_at=started_at,
                snapshot=snapshot,
                profile=profile,
                stage_result=stage_result,
                framework=framework,
                coverage=coverage,
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
                    run_dir / "framework" / "result.json"
                )
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="failed",
                started_at=started_at,
                snapshot=snapshot,
                profile=profile,
                stage_result=stage_result,
                framework=None,
                coverage=None,
                failure=failure,
                requested_landscape_run_id=landscape_run_id,
                requested_reference_catalog_run_id=reference_catalog_run_id,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def build_review_framework_prompt(
    snapshot: ReviewFrameworkSnapshot,
    *,
    schema: dict[str, Any],
) -> str:
    bibliography = [
        {
            "paper_id": paper_id,
            "title": reference["title"],
            "status": reference["status"],
            "missing_fields": reference.get("missing_fields", []),
        }
        for paper_id, reference in sorted(snapshot.reference_by_paper.items())
    ]
    payload = {
        "任务": "根据Research Landscape设计综述章节框架",
        "综述主题": snapshot.topic,
        "综述目标": snapshot.review_goal,
        "语料范围": snapshot.corpus_scope,
        "Research Landscape": snapshot.landscape,
        "题录状态": bibliography,
        "输出前逐项核对": [
            "第一节是introduction，最后一节是conclusion，中间至少一个body",
            "每个dimension_index至少出现在一个body章节",
            "body的required_comparisons是可执行的跨论文比较任务",
            "比较任务点名的每篇论文都属于本节所选dimension",
            "不输出paper、contribution、citation或其他机器ID",
            "语料限制不外推为整个领域缺口",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    )


def render_review_framework(framework: dict[str, Any]) -> str:
    lines = [
        "# 综述框架审核",
        "",
        f"- 工作标题：{framework['working_title']}",
        f"- 中心问题：{framework['central_question']}",
        f"- 综述目标：{framework['review_goal']}",
        f"- 章节数：{len(framework['sections'])}",
        f"- 未进入正文论文：{len(framework['unused_papers'])}",
        f"- 题录问题：{len(framework['bibliography_issues'])}",
        "",
        "## 章节设计",
        "",
    ]
    for section in framework["sections"]:
        lines.extend(
            [
                f"### {section['section_index']}. {section['title']}",
                "",
                f"- 类型：`{section['section_type']}`",
                f"- 章节问题：{section['question']}",
                f"- 章节作用：{section['purpose']}",
                "- 研究维度：" + _display_list(
                    [str(value) for value in section["dimension_indexes"]]
                ),
                "- 论文：" + _display_list(section["paper_ids"]),
                "- 引用Key：" + _display_list(section["citation_keys"]),
                "- 必做比较：" + _display_list(
                    section["required_comparisons"]
                ),
                "- 明确争议：" + _display_list(section["controversy_ids"]),
                "- 语料缺口：" + _display_list(
                    [str(value) for value in section["corpus_gap_indexes"]]
                ),
                "- 本节限制：" + _display_list(
                    section["corpus_limitations"]
                ),
                "",
            ]
        )
    lines.extend(["## 题录状态", ""])
    if framework["bibliography_issues"]:
        for row in framework["bibliography_issues"]:
            lines.append(
                f"- `{row['citation_key']}` {row['paper_id']}："
                f"{row['status']}；缺失字段 "
                + _display_list(row["missing_fields"])
            )
    else:
        lines.append("- 所有正文论文均有完整题录。")
    lines.extend(["", "## 未进入正文论文", ""])
    if framework["unused_papers"]:
        for paper_id in framework["unused_papers"]:
            lines.append(f"- {paper_id}：未进入任何正文章节。")
    else:
        lines.append("- 无。")
    lines.append("")
    return "\n".join(lines)


def _load_landscape_source(
    workspace: Path,
    run_id: str,
) -> tuple[dict[str, Any], ResearchLandscapeSnapshot, str, str]:
    run_dir = workspace / "_research_landscapes" / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "Landscape manifest")
    manifest = _json_object(manifest_bytes, "Landscape manifest")
    if manifest.get("schema_version") != RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION:
        raise AnalysisInputError("Landscape运行Schema版本不受支持。")
    if manifest.get("run_id") != run_id:
        raise AnalysisInputError(
            "Landscape请求目录名与manifest.run_id不一致。"
        )
    if manifest.get("status") != "completed":
        raise AnalysisInputError(
            f"Landscape运行状态不可用：{manifest.get('status')!r}。"
        )
    frozen_collection = run_dir / "input" / "collection.json"
    snapshot = create_research_landscape_snapshot(
        workspace,
        collection_path=frozen_collection,
    )
    if (
        snapshot.input_sha256 != manifest.get("input_sha256")
        or [source.run_id for source in snapshot.sources]
        != manifest.get("source_run_ids")
    ):
        raise AnalysisInputError("Landscape来源Understanding无法重放。")
    config = load_research_landscape_config(
        run_dir / "input" / "landscape_config.json"
    )
    contribution_to_paper = {
        str(contribution["contribution_id"]): source.paper_id
        for source in snapshot.sources
        for contribution in source.understanding["contributions"]
    }
    schema = build_research_landscape_schema(
        topic=snapshot.topic,
        review_goal=snapshot.review_goal,
        paper_ids=[source.paper_id for source in snapshot.sources],
        contribution_to_paper=contribution_to_paper,
        corpus_scope=snapshot.corpus_scope,
        config=config,
    )
    parsed = _read_json(
        run_dir / "landscape" / "parsed_response.json",
        "Landscape原始解析结果",
    )
    output_bytes = _read_bytes(
        run_dir / "output" / "research_landscape.json",
        "Landscape正式输出",
    )
    output = _json_object(output_bytes, "Landscape正式输出")
    replay = validate_research_landscape(
        parsed,
        schema=schema,
        contribution_to_paper=contribution_to_paper,
    )
    if replay != output:
        raise AnalysisInputError("Landscape正式输出无法由原始响应重放。")
    if (
        output.get("landscape_id") != manifest.get("landscape_id")
        or output.get("topic") != manifest.get("topic")
        or output.get("corpus_scope") != manifest.get("corpus_scope")
    ):
        raise AnalysisInputError("Landscape manifest与正式输出身份不一致。")
    return (
        output,
        snapshot,
        _sha256_bytes(manifest_bytes),
        _sha256_bytes(output_bytes),
    )


def _load_reference_catalog_source(
    workspace: Path,
    run_id: str,
    *,
    expected_topic: str,
    expected_paper_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], str, str]:
    run_dir = workspace / "_reference_catalogs" / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "题录manifest")
    manifest = _json_object(manifest_bytes, "题录manifest")
    if manifest.get("schema_version") != REFERENCE_CATALOG_RUN_SCHEMA_VERSION:
        raise AnalysisInputError("题录运行Schema版本不受支持。")
    if manifest.get("run_id") != run_id:
        raise AnalysisInputError("题录请求目录名与manifest.run_id不一致。")
    if manifest.get("status") not in {"completed", "completed_with_gaps"}:
        raise AnalysisInputError(
            f"题录运行状态不可用：{manifest.get('status')!r}。"
        )
    catalog_bytes = _read_bytes(
        run_dir / "output" / "references.json",
        "题录正式输出",
    )
    catalog = _json_object(catalog_bytes, "题录正式输出")
    if catalog.get("schema_version") != REFERENCE_CATALOG_SCHEMA_VERSION:
        raise AnalysisInputError("题录正式输出Schema版本不受支持。")
    if catalog.get("run_id") != run_id:
        raise AnalysisInputError("题录catalog.run_id与请求运行不一致。")
    if catalog.get("topic") != expected_topic:
        raise AnalysisInputError("题录主题与Landscape主题不一致。")
    references = catalog.get("references")
    if not isinstance(references, list):
        raise AnalysisInputError("题录references必须是数组。")
    reference_by_paper: dict[str, dict[str, Any]] = {}
    reference_ids: set[str] = set()
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            raise AnalysisInputError(f"题录第{index + 1}项必须是对象。")
        paper_id = str(reference.get("paper_id", ""))
        reference_id = str(reference.get("reference_id", ""))
        if not paper_id or paper_id in reference_by_paper:
            raise AnalysisInputError("题录paper_id为空或重复。")
        if not reference_id or reference_id in reference_ids:
            raise AnalysisInputError("题录reference_id为空或重复。")
        if reference.get("status") not in {"complete", "partial"}:
            raise AnalysisInputError("题录status不受支持。")
        reference_by_paper[paper_id] = reference
        reference_ids.add(reference_id)
    missing = sorted(expected_paper_ids - set(reference_by_paper))
    if missing:
        raise AnalysisInputError(f"Landscape论文缺少题录：{missing}")
    if (
        int(catalog.get("reference_count", -1)) != len(references)
        or int(manifest.get("reference_count", -1)) != len(references)
    ):
        raise AnalysisInputError("题录manifest或输出计数不一致。")
    return (
        {
            paper_id: reference_by_paper[paper_id]
            for paper_id in sorted(expected_paper_ids)
        },
        _sha256_bytes(manifest_bytes),
        _sha256_bytes(catalog_bytes),
    )


def _write_input_artifacts(
    run_dir: Path,
    snapshot: ReviewFrameworkSnapshot,
    profile: ModelProfile,
    schema: dict[str, Any],
) -> None:
    _write_json(run_dir / "input" / "research_landscape.json", snapshot.landscape)
    _write_json(
        run_dir / "input" / "references.json",
        {
            "schema_version": REFERENCE_CATALOG_SCHEMA_VERSION,
            "references": list(snapshot.reference_by_paper.values()),
        },
    )
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(run_dir / "input" / "output_schema.json", schema)
    _write_json(
        run_dir / "input" / "source.json",
        {
            "landscape_run_id": snapshot.landscape_run_id,
            "landscape_manifest_sha256": snapshot.landscape_manifest_sha256,
            "landscape_output_sha256": snapshot.landscape_output_sha256,
            "reference_catalog_run_id": snapshot.reference_catalog_run_id,
            "reference_manifest_sha256": snapshot.reference_manifest_sha256,
            "reference_catalog_sha256": snapshot.reference_catalog_sha256,
            "review_goal": snapshot.review_goal,
            "input_sha256": snapshot.input_sha256,
        },
    )


def _build_derivation(
    snapshot: ReviewFrameworkSnapshot,
    draft: dict[str, Any],
    framework: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "llm.review_framework_derivation.v1",
        "source_landscape_id": snapshot.landscape["landscape_id"],
        "model_generated_fields": [
            "working_title",
            "central_question",
            "section title/type/question/purpose",
            "dimension_indexes",
            "required_comparisons",
            "controversy_ids",
            "corpus_gap_indexes",
            "corpus_limitations",
        ],
        "program_derived_fields": [
            "framework_id",
            "section_id",
            "paper_ids",
            "contribution_ids",
            "citation_keys",
            "bibliography_status",
            "unused_papers",
            "bibliography_issues",
        ],
        "draft_sha256": _sha256_json(draft),
        "framework_sha256": _sha256_json(framework),
    }


def _build_coverage(
    snapshot: ReviewFrameworkSnapshot,
    framework: dict[str, Any],
) -> dict[str, Any]:
    body_sections = [
        row for row in framework["sections"] if row["section_type"] == "body"
    ]
    body_dimensions = {
        int(value)
        for row in body_sections
        for value in row["dimension_indexes"]
    }
    body_papers = {
        str(value)
        for row in body_sections
        for value in row["paper_ids"]
    }
    return {
        "dimension_count": len(snapshot.landscape["dimensions"]),
        "body_dimension_count": len(body_dimensions),
        "paper_count": len(snapshot.reference_by_paper),
        "body_paper_count": len(body_papers),
        "section_count": len(framework["sections"]),
        "body_section_count": len(body_sections),
        "unused_paper_count": len(framework["unused_papers"]),
        "bibliography_issue_count": len(framework["bibliography_issues"]),
    }


def _build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    snapshot: ReviewFrameworkSnapshot | None,
    profile: ModelProfile | None,
    stage_result: dict[str, Any] | None,
    framework: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
    failure: dict[str, Any] | None,
    requested_landscape_run_id: str | None = None,
    requested_reference_catalog_run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": REVIEW_FRAMEWORK_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "topic": snapshot.topic if snapshot is not None else None,
        "review_goal": snapshot.review_goal if snapshot is not None else None,
        "corpus_scope": snapshot.corpus_scope if snapshot is not None else None,
        "landscape_run_id": (
            snapshot.landscape_run_id
            if snapshot is not None
            else requested_landscape_run_id
        ),
        "reference_catalog_run_id": (
            snapshot.reference_catalog_run_id
            if snapshot is not None
            else requested_reference_catalog_run_id
        ),
        "input_sha256": snapshot.input_sha256 if snapshot is not None else None,
        "provider": profile.provider if profile is not None else None,
        "model": profile.request_model if profile is not None else None,
        "model_profile_id": profile.profile_id if profile is not None else None,
        "model_profile_sha256": profile.sha256 if profile is not None else None,
        "stage_result": stage_result,
        "framework_id": (
            framework.get("framework_id") if framework is not None else None
        ),
        "coverage": coverage,
        "failure": failure,
        "outputs": {
            "review_framework": (
                "output/review_framework.json"
                if framework is not None
                else None
            ),
            "review": (
                "review/review_framework.md"
                if framework is not None
                else None
            ),
            "derivation": (
                "audit/derivation.json"
                if framework is not None
                else None
            ),
            "coverage": (
                "audit/coverage.json" if framework is not None else None
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
        "framework_id": manifest.get("framework_id"),
        "coverage": manifest.get("coverage"),
        "failure": manifest.get("failure"),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "output_path": (
            str(run_dir / "output" / "review_framework.json")
            if manifest["status"] == "completed"
            else None
        ),
        "review_path": (
            str(run_dir / "review" / "review_framework.md")
            if manifest["status"] == "completed"
            else None
        ),
    }


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "framework").exists():
        return "framework"
    if (run_dir / "input").exists():
        return "input_written"
    return "input_snapshot"


def _safe_segment(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise AnalysisInputError(f"{field}必须是单个非空目录名。")
    return value


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise AnalysisInputError(f"无法读取{label}：{path}") from exc


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"{label}不是有效JSON对象。") from exc
    if not isinstance(payload, dict):
        raise AnalysisInputError(f"{label}必须是JSON对象。")
    return payload


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path, str(path))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
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


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


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


def _display_list(values: list[str]) -> str:
    return "、".join(values) if values else "无"
