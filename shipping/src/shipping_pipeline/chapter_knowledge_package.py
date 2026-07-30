from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm_analysis import (
    AnalysisInputError,
    DEFAULT_MODEL_PROFILE,
    DEFAULT_TOKENIZER_CACHE,
)
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_projection import project_cards
from .llm_tokenizer import DeepSeekV4TokenCounter
from .research_landscape import create_research_landscape_snapshot
from .review_framework import (
    ReviewFrameworkRunSource,
    load_review_framework_run,
)
from .review_writing import (
    REVIEW_CHAPTER_SYSTEM_PROMPT,
    build_review_chapter_prompt,
)
from .review_writing_contracts import (
    ReviewWritingConfig,
    build_review_chapter_schema,
    load_review_writing_config,
)


CHAPTER_KNOWLEDGE_PACKAGE_SCHEMA_VERSION = (
    "llm.chapter_knowledge_package.v1"
)
CHAPTER_KNOWLEDGE_PACKAGE_RUN_SCHEMA_VERSION = (
    "llm.chapter_knowledge_package_run.v1"
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW_WRITING_CONFIG = (
    PROJECT_ROOT / "config" / "review-writing-default.json"
)


class ChapterKnowledgePackageError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ChapterPackageBuildSnapshot:
    framework_source: ReviewFrameworkRunSource
    section: dict[str, Any]
    package: dict[str, Any]
    paper_artifacts: tuple[dict[str, Any], ...]
    input_sha256: str


class ChapterKnowledgePackageBuilder:
    def __init__(
        self,
        workspace: str | Path,
        token_counter: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.token_counter = token_counter

    def build(
        self,
        *,
        framework_run_id: str,
        section_id: str | None = None,
        section_index: int | None = None,
        run_id: str | None = None,
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        writing_config_path: str | Path = DEFAULT_REVIEW_WRITING_CONFIG,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved_run_id, "run_id")
        run_dir = (
            self.workspace
            / "_chapter_knowledge_packages"
            / "runs"
            / resolved_run_id
        )
        if run_dir.exists():
            raise ChapterKnowledgePackageError(
                "package.run_exists",
                f"知识包run_id已存在，不能覆盖不可变运行：{resolved_run_id}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        profile: ModelProfile | None = None
        config: ReviewWritingConfig | None = None
        snapshot: ChapterPackageBuildSnapshot | None = None
        budget: dict[str, Any] | None = None
        try:
            profile = load_model_profile(Path(model_profile_path))
            config = load_review_writing_config(Path(writing_config_path))
            framework_source = load_review_framework_run(
                self.workspace,
                framework_run_id,
            )
            section = _select_section(
                framework_source.framework,
                section_id=section_id,
                section_index=section_index,
            )
            snapshot = _build_package_snapshot(
                self.workspace,
                framework_source=framework_source,
                section=section,
            )
            schema = build_review_chapter_schema(
                section_index=int(section["section_index"]),
                section_title=str(section["title"]),
                section_type=str(section["section_type"]),
                allowed_citation_keys=[
                    str(value) for value in section["citation_keys"]
                ],
                config=config,
            )
            _write_input_artifacts(
                run_dir,
                snapshot=snapshot,
                profile=profile,
                config=config,
                schema=schema,
            )
            user_prompt = build_review_chapter_prompt(
                snapshot.package,
                schema=schema,
            )
            messages = [
                {"role": "system", "content": REVIEW_CHAPTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            token_counter = self.token_counter or DeepSeekV4TokenCounter(
                profile,
                DEFAULT_TOKENIZER_CACHE,
            )
            budget = _build_token_budget(
                snapshot.package,
                messages=messages,
                schema=schema,
                profile=profile,
                config=config,
                token_counter=token_counter,
            )
            _write_json(run_dir / "audit" / "token_budget.json", budget)
            if not budget["within_budget"]:
                raise ChapterKnowledgePackageError(
                    "package.context_budget_exceeded",
                    "完整章节写作请求超过上下文窗口："
                    f"input={budget['planned_input_tokens']}, "
                    f"output={budget['planned_max_output_tokens']}, "
                    f"margin={budget['safety_margin_tokens']}, "
                    f"window={budget['context_window_tokens']}, "
                    f"overflow={budget['overflow_tokens']}。",
                    path="$.token_budget",
                )
            _write_json(
                run_dir / "output" / "knowledge_package.json",
                snapshot.package,
            )
            _write_text(
                run_dir / "review" / "knowledge_package.md",
                render_chapter_knowledge_package(snapshot.package, budget),
            )
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="completed",
                started_at=started_at,
                framework_run_id=framework_run_id,
                requested_section_id=section_id,
                requested_section_index=section_index,
                profile=profile,
                config=config,
                snapshot=snapshot,
                budget=budget,
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
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="failed",
                started_at=started_at,
                framework_run_id=framework_run_id,
                requested_section_id=section_id,
                requested_section_index=section_index,
                profile=profile,
                config=config,
                snapshot=snapshot,
                budget=budget,
                failure=failure,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def _build_package_snapshot(
    workspace: Path,
    *,
    framework_source: ReviewFrameworkRunSource,
    section: dict[str, Any],
) -> ChapterPackageBuildSnapshot:
    framework_snapshot = framework_source.snapshot
    landscape_run_dir = (
        workspace
        / "_research_landscapes"
        / "runs"
        / framework_snapshot.landscape_run_id
    )
    landscape_snapshot = create_research_landscape_snapshot(
        workspace,
        collection_path=landscape_run_dir / "input" / "collection.json",
    )
    if (
        landscape_snapshot.input_sha256
        != json.loads(
            (landscape_run_dir / "manifest.json").read_text(
                encoding="utf-8-sig"
            )
        ).get("input_sha256")
    ):
        raise ChapterKnowledgePackageError(
            "package.landscape_source_mismatch",
            "Landscape来源Understanding快照哈希不一致。",
        )
    sources_by_paper = {
        source.paper_id: source for source in landscape_snapshot.sources
    }
    section_paper_ids = [str(value) for value in section["paper_ids"]]
    if len(section_paper_ids) != len(set(section_paper_ids)):
        raise ChapterKnowledgePackageError(
            "package.section_paper_duplicate",
            "Framework章节paper_ids不得重复。",
            path="$.section.paper_ids",
        )
    missing_sources = sorted(set(section_paper_ids) - set(sources_by_paper))
    if missing_sources:
        raise ChapterKnowledgePackageError(
            "package.understanding_source_missing",
            f"Framework论文无法映射到唯一Understanding：{missing_sources}",
            path="$.section.paper_ids",
        )
    citation_by_paper = dict(
        zip(
            section_paper_ids,
            [str(value) for value in section["citation_keys"]],
            strict=True,
        )
    )
    papers: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for paper_index, paper_id in enumerate(section_paper_ids, start=1):
        source = sources_by_paper[paper_id]
        understanding_run_dir = (
            workspace / "_paper_understandings" / "runs" / source.run_id
        )
        document_bytes = _read_bytes(
            understanding_run_dir / "input" / "document.md",
            f"{paper_id}冻结Markdown",
        )
        try:
            document = document_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ChapterKnowledgePackageError(
                "package.document_encoding_invalid",
                f"{paper_id}冻结Markdown不是UTF-8。",
            ) from exc
        raw_materials_bytes = _read_bytes(
            understanding_run_dir / "input" / "materials.jsonl",
            f"{paper_id}冻结Card",
        )
        raw_projected_bytes = _read_bytes(
            understanding_run_dir / "input" / "projected_cards.jsonl",
            f"{paper_id}冻结Card投影",
        )
        raw_evidence_bytes = _read_bytes(
            understanding_run_dir / "input" / "evidence_units.jsonl",
            f"{paper_id}冻结Evidence",
        )
        raw_understanding_bytes = _read_bytes(
            understanding_run_dir / "output" / "paper_understanding.json",
            f"{paper_id}Understanding输出",
        )
        raw_manifest_bytes = _read_bytes(
            understanding_run_dir / "manifest.json",
            f"{paper_id}Understanding manifest",
        )
        materials = _read_jsonl_bytes(raw_materials_bytes, f"{paper_id} Card")
        projected_cards = _read_jsonl_bytes(
            raw_projected_bytes,
            f"{paper_id} Card投影",
        )
        evidence_units = _read_jsonl_bytes(
            raw_evidence_bytes,
            f"{paper_id} Evidence",
        )
        if materials != list(source.materials):
            raise ChapterKnowledgePackageError(
                "package.material_snapshot_mismatch",
                f"{paper_id}冻结Card与Landscape重放结果不一致。",
            )
        if projected_cards != project_cards(materials):
            raise ChapterKnowledgePackageError(
                "package.projected_cards_mismatch",
                f"{paper_id} Card投影无法由冻结Card重建。",
            )
        if evidence_units != list(source.evidence_units):
            raise ChapterKnowledgePackageError(
                "package.evidence_snapshot_mismatch",
                f"{paper_id}冻结Evidence与Landscape重放结果不一致。",
            )
        understanding = _json_object(
            raw_understanding_bytes,
            f"{paper_id} Understanding",
        )
        if understanding != source.understanding:
            raise ChapterKnowledgePackageError(
                "package.understanding_snapshot_mismatch",
                f"{paper_id} Understanding输出与Landscape重放结果不一致。",
            )
        _validate_evidence_quotes(
            paper_id,
            materials=materials,
            evidence_units=evidence_units,
        )
        bibliography = copy.deepcopy(
            framework_snapshot.reference_by_paper[paper_id]
        )
        expected_citation_key = _citation_key(bibliography)
        if citation_by_paper[paper_id] != expected_citation_key:
            raise ChapterKnowledgePackageError(
                "package.citation_key_mismatch",
                f"{paper_id} Framework引用Key与题录不一致。",
                path=f"$.section.paper_ids[{paper_index - 1}]",
            )
        paper_payload = {
            "paper_id": paper_id,
            "paper_title": source.paper_title,
            "source_understanding_run_id": source.run_id,
            "generation_id": source.generation_id,
            "citation_key": expected_citation_key,
            "bibliography": bibliography,
            "understanding": copy.deepcopy(source.understanding),
            "full_markdown": document,
            "cards": projected_cards,
            "evidence_units": evidence_units,
        }
        papers.append(paper_payload)
        artifacts.append(
            {
                "paper_index": paper_index,
                "paper_id": paper_id,
                "document": document_bytes,
                "materials": raw_materials_bytes,
                "projected_cards": raw_projected_bytes,
                "evidence_units": raw_evidence_bytes,
                "understanding": raw_understanding_bytes,
                "manifest": raw_manifest_bytes,
                "bibliography": (
                    json.dumps(
                        bibliography,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                ).encode("utf-8"),
            }
        )
    landscape_context = _select_landscape_context(
        framework_snapshot.landscape,
        section=section,
    )
    package_without_id = {
        "schema_version": CHAPTER_KNOWLEDGE_PACKAGE_SCHEMA_VERSION,
        "source": {
            "framework_run_id": framework_source.run_id,
            "framework_id": framework_source.framework["framework_id"],
            "framework_manifest_sha256": framework_source.manifest_sha256,
            "framework_output_sha256": framework_source.output_sha256,
            "landscape_run_id": framework_snapshot.landscape_run_id,
            "landscape_id": framework_snapshot.landscape["landscape_id"],
            "reference_catalog_run_id": (
                framework_snapshot.reference_catalog_run_id
            ),
        },
        "section": copy.deepcopy(section),
        "landscape_context": landscape_context,
        "papers": papers,
    }
    input_sha256 = _sha256_json(package_without_id)
    package = {
        **package_without_id,
        "package_id": _stable_id("package", package_without_id),
    }
    return ChapterPackageBuildSnapshot(
        framework_source=framework_source,
        section=copy.deepcopy(section),
        package=package,
        paper_artifacts=tuple(artifacts),
        input_sha256=input_sha256,
    )


def _select_landscape_context(
    landscape: dict[str, Any],
    *,
    section: dict[str, Any],
) -> dict[str, Any]:
    dimension_indexes = {
        int(value) for value in section["dimension_indexes"]
    }
    paper_ids = {str(value) for value in section["paper_ids"]}
    controversy_ids = {
        str(value) for value in section["controversy_ids"]
    }
    corpus_gap_indexes = {
        int(value) for value in section["corpus_gap_indexes"]
    }
    return {
        "central_problem": landscape["central_problem"],
        "dimensions": [
            copy.deepcopy(row)
            for row in landscape["dimensions"]
            if int(row["dimension_index"]) in dimension_indexes
        ],
        "relations": [
            copy.deepcopy(row)
            for row in landscape["relations"]
            if str(row["from_paper_id"]) in paper_ids
            and str(row["to_paper_id"]) in paper_ids
        ],
        "disagreements": [
            copy.deepcopy(row)
            for row in landscape["disagreements"]
            if str(row["disagreement_id"]) in controversy_ids
        ],
        "corpus_gaps": [
            copy.deepcopy(row)
            for index, row in enumerate(
                landscape["corpus_gaps"],
                start=1,
            )
            if index in corpus_gap_indexes
        ],
    }


def _build_token_budget(
    package: dict[str, Any],
    *,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    profile: ModelProfile,
    config: ReviewWritingConfig,
    token_counter: Any,
) -> dict[str, Any]:
    if config.chapter_max_output_tokens > profile.model_max_output_tokens:
        raise ChapterKnowledgePackageError(
            "package.output_budget_exceeded",
            "章节输出预算超过模型上限。",
            path="$.writing_config.chapter_max_output_tokens",
        )
    count = token_counter.count_messages(messages)
    total_reserved = (
        count.prompt_tokens
        + config.chapter_max_output_tokens
        + profile.safety_margin_tokens
    )
    paper_diagnostics: list[dict[str, Any]] = []
    for paper in package["papers"]:
        component_tokens = {
            "full_markdown_tokens": token_counter.count_text(
                paper["full_markdown"]
            ),
            "cards_tokens": token_counter.count_text(
                _compact_json(paper["cards"])
            ),
            "understanding_tokens": token_counter.count_text(
                _compact_json(paper["understanding"])
            ),
            "evidence_tokens": token_counter.count_text(
                _compact_json(paper["evidence_units"])
            ),
            "bibliography_tokens": token_counter.count_text(
                _compact_json(paper["bibliography"])
            ),
        }
        without_paper = copy.deepcopy(package)
        without_paper["papers"] = [
            row
            for row in without_paper["papers"]
            if row["paper_id"] != paper["paper_id"]
        ]
        without_prompt = build_review_chapter_prompt(
            without_paper,
            schema=schema,
        )
        without_count = token_counter.count_messages(
            [
                {
                    "role": "system",
                    "content": REVIEW_CHAPTER_SYSTEM_PROMPT,
                },
                {"role": "user", "content": without_prompt},
            ]
        )
        paper_diagnostics.append(
            {
                "paper_id": paper["paper_id"],
                **component_tokens,
                "leave_one_out_marginal_tokens": (
                    count.prompt_tokens - without_count.prompt_tokens
                ),
            }
        )
    return {
        "schema_version": "llm.chapter_token_budget.v1",
        "model_profile_id": profile.profile_id,
        "model_profile_sha256": profile.sha256,
        "planned_input_tokens": count.prompt_tokens,
        "planned_max_output_tokens": config.chapter_max_output_tokens,
        "safety_margin_tokens": profile.safety_margin_tokens,
        "context_window_tokens": profile.context_window_tokens,
        "total_reserved_tokens": total_reserved,
        "overflow_tokens": max(
            0,
            total_reserved - profile.context_window_tokens,
        ),
        "within_budget": total_reserved <= profile.context_window_tokens,
        "encoded_prompt_sha256": count.encoded_prompt_sha256,
        "system_prompt_sha256": _sha256_text(
            REVIEW_CHAPTER_SYSTEM_PROMPT
        ),
        "output_schema_sha256": _sha256_json(schema),
        "paper_diagnostics": paper_diagnostics,
    }


def _write_input_artifacts(
    run_dir: Path,
    *,
    snapshot: ChapterPackageBuildSnapshot,
    profile: ModelProfile,
    config: ReviewWritingConfig,
    schema: dict[str, Any],
) -> None:
    ledger: list[dict[str, Any]] = []

    def write(relative_path: str, data: bytes) -> None:
        path = run_dir / "input" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        ledger.append(
            {
                "path": relative_path.replace("\\", "/"),
                "sha256": _sha256_bytes(data),
                "bytes": len(data),
            }
        )

    write(
        "knowledge_package_candidate.json",
        (
            json.dumps(snapshot.package, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
    )
    write(
        "framework.json",
        (
            json.dumps(
                snapshot.framework_source.framework,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    )
    write(
        "research_landscape.json",
        (
            json.dumps(
                snapshot.framework_source.snapshot.landscape,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    )
    write(
        "references.json",
        (
            json.dumps(
                list(
                    snapshot.framework_source.snapshot.reference_by_paper.values()
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    )
    write(
        "model_profile.json",
        (
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
    )
    write(
        "writing_config.json",
        (
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
    )
    write(
        "chapter_output_schema.json",
        (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        ),
    )
    for artifact in snapshot.paper_artifacts:
        prefix = f"papers/paper-{artifact['paper_index']:03d}"
        write(f"{prefix}/document.md", artifact["document"])
        write(f"{prefix}/materials.jsonl", artifact["materials"])
        write(
            f"{prefix}/projected_cards.jsonl",
            artifact["projected_cards"],
        )
        write(f"{prefix}/evidence_units.jsonl", artifact["evidence_units"])
        write(
            f"{prefix}/paper_understanding.json",
            artifact["understanding"],
        )
        write(
            f"{prefix}/understanding_manifest.json",
            artifact["manifest"],
        )
        write(f"{prefix}/bibliography.json", artifact["bibliography"])
    _write_jsonl(run_dir / "input" / "file_hashes.jsonl", ledger)


def render_chapter_knowledge_package(
    package: dict[str, Any],
    budget: dict[str, Any],
) -> str:
    section = package["section"]
    lines = [
        "# 章节知识包审核",
        "",
        f"- 章节：{section['section_index']}. {section['title']}",
        f"- 类型：`{section['section_type']}`",
        f"- 章节问题：{section['question']}",
        f"- 论文数：{len(package['papers'])}",
        f"- 完整请求Token：{budget['planned_input_tokens']}",
        f"- 最大输出Token：{budget['planned_max_output_tokens']}",
        f"- 安全余量：{budget['safety_margin_tokens']}",
        f"- 上下文窗口：{budget['context_window_tokens']}",
        f"- 预算状态：{'通过' if budget['within_budget'] else '超限'}",
        "",
        "## 必做比较",
        "",
    ]
    if section["required_comparisons"]:
        lines.extend(f"- {value}" for value in section["required_comparisons"])
    else:
        lines.append("- 无。")
    lines.extend(["", "## 论文与输入规模", ""])
    diagnostics = {
        row["paper_id"]: row for row in budget["paper_diagnostics"]
    }
    for paper in package["papers"]:
        row = diagnostics[paper["paper_id"]]
        lines.extend(
            [
                f"### {paper['paper_title']}",
                "",
                f"- 引用Key：`{paper['citation_key']}`",
                f"- Understanding运行：`{paper['source_understanding_run_id']}`",
                f"- Markdown Token：{row['full_markdown_tokens']}",
                f"- Card Token：{row['cards_tokens']}",
                f"- Understanding Token：{row['understanding_tokens']}",
                f"- Evidence Token：{row['evidence_tokens']}",
                f"- 移除此论文后的边际Token："
                f"{row['leave_one_out_marginal_tokens']}",
                "",
            ]
        )
    lines.extend(["## Landscape上下文", ""])
    lines.append(
        "- 维度："
        + "、".join(
            str(row["dimension_index"])
            for row in package["landscape_context"]["dimensions"]
        )
    )
    lines.append(
        f"- 关系：{len(package['landscape_context']['relations'])}"
    )
    lines.append(
        f"- 分歧：{len(package['landscape_context']['disagreements'])}"
    )
    lines.append(
        f"- 语料缺口：{len(package['landscape_context']['corpus_gaps'])}"
    )
    lines.append("")
    return "\n".join(lines)


def _select_section(
    framework: dict[str, Any],
    *,
    section_id: str | None,
    section_index: int | None,
) -> dict[str, Any]:
    if (section_id is None) == (section_index is None):
        raise ChapterKnowledgePackageError(
            "package.section_selector_invalid",
            "section_id和section_index必须且只能提供一个。",
        )
    matches = [
        section
        for section in framework["sections"]
        if (
            section_id is not None
            and section.get("section_id") == section_id
        )
        or (
            section_index is not None
            and section.get("section_index") == section_index
        )
    ]
    if len(matches) != 1:
        raise ChapterKnowledgePackageError(
            "package.section_not_found",
            "Framework中无法唯一定位章节。",
        )
    return copy.deepcopy(matches[0])


def _validate_evidence_quotes(
    paper_id: str,
    *,
    materials: list[dict[str, Any]],
    evidence_units: list[dict[str, Any]],
) -> None:
    material_by_id = {
        str(material["material_id"]): material for material in materials
    }
    for unit_index, evidence in enumerate(evidence_units):
        for citation_index, citation in enumerate(evidence["citations"]):
            path = (
                f"$.papers[{json.dumps(paper_id, ensure_ascii=False)}]"
                f".evidence_units[{unit_index}].citations[{citation_index}]"
            )
            material_id = str(citation.get("material_id", ""))
            material = material_by_id.get(material_id)
            if material is None:
                raise ChapterKnowledgePackageError(
                    "package.evidence_unknown_material",
                    f"Evidence引用未知Card：{material_id}",
                    path=path,
                )
            quote = citation.get("quote")
            if not isinstance(quote, str) or not quote or quote not in str(
                material.get("extract", "")
            ):
                raise ChapterKnowledgePackageError(
                    "package.evidence_quote_not_exact",
                    "Evidence逐字引文不是Card extract的连续子串。",
                    path=f"{path}.quote",
                )
            source_ref = citation.get("source_ref")
            source_span = material.get("source_span")
            if not isinstance(source_ref, dict) or not isinstance(
                source_span,
                dict,
            ):
                raise ChapterKnowledgePackageError(
                    "package.evidence_source_ref_invalid",
                    "Evidence或Card缺少原文坐标。",
                    path=f"{path}.source_ref",
                )
            if (
                source_ref.get("path") != source_span.get("path")
                or int(source_ref.get("start_line", -1))
                < int(source_span.get("start_line", 0))
                or int(source_ref.get("end_line", -1))
                > int(source_span.get("end_line", 0))
            ):
                raise ChapterKnowledgePackageError(
                    "package.evidence_source_ref_mismatch",
                    "Evidence原文坐标超出Card来源范围。",
                    path=f"{path}.source_ref",
                )


def _citation_key(reference: dict[str, Any]) -> str:
    value = reference.get("citation_key")
    if isinstance(value, str) and value.strip():
        return value.strip()
    reference_id = reference.get("reference_id")
    if isinstance(reference_id, str) and reference_id.strip():
        return "ref_" + reference_id.strip().removeprefix("ref-")
    raise ChapterKnowledgePackageError(
        "package.citation_key_missing",
        "题录缺少citation_key或可转换的reference_id。",
    )


def _build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    framework_run_id: str,
    requested_section_id: str | None,
    requested_section_index: int | None,
    profile: ModelProfile | None,
    config: ReviewWritingConfig | None,
    snapshot: ChapterPackageBuildSnapshot | None,
    budget: dict[str, Any] | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    section = snapshot.section if snapshot is not None else None
    return {
        "schema_version": CHAPTER_KNOWLEDGE_PACKAGE_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "framework_run_id": framework_run_id,
        "framework_id": (
            snapshot.framework_source.framework["framework_id"]
            if snapshot is not None
            else None
        ),
        "section_id": (
            section["section_id"]
            if section is not None
            else requested_section_id
        ),
        "section_index": (
            section["section_index"]
            if section is not None
            else requested_section_index
        ),
        "package_id": (
            snapshot.package["package_id"] if snapshot is not None else None
        ),
        "input_sha256": (
            snapshot.input_sha256 if snapshot is not None else None
        ),
        "paper_count": (
            len(snapshot.package["papers"]) if snapshot is not None else 0
        ),
        "model_profile_id": (
            profile.profile_id if profile is not None else None
        ),
        "model_profile_sha256": (
            profile.sha256 if profile is not None else None
        ),
        "writing_config": config.to_dict() if config is not None else None,
        "token_budget": budget,
        "failure": failure,
        "outputs": {
            "knowledge_package": (
                "output/knowledge_package.json"
                if status == "completed"
                else None
            ),
            "review": (
                "review/knowledge_package.md"
                if status == "completed"
                else None
            ),
            "token_budget": (
                "audit/token_budget.json" if budget is not None else None
            ),
            "file_hashes": (
                "input/file_hashes.jsonl"
                if snapshot is not None
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
        "framework_run_id": manifest.get("framework_run_id"),
        "section_id": manifest.get("section_id"),
        "section_index": manifest.get("section_index"),
        "package_id": manifest.get("package_id"),
        "paper_count": manifest.get("paper_count", 0),
        "token_budget": manifest.get("token_budget"),
        "failure": manifest.get("failure"),
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "output_path": (
            str(run_dir / "output" / "knowledge_package.json")
            if manifest["status"] == "completed"
            else None
        ),
        "review_path": (
            str(run_dir / "review" / "knowledge_package.md")
            if manifest["status"] == "completed"
            else None
        ),
    }


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "audit" / "token_budget.json").exists():
        return "token_budget"
    if (run_dir / "input").exists():
        return "input_written"
    return "source_replay"


def _safe_segment(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise ChapterKnowledgePackageError(
            "package.path_segment_invalid",
            f"{field}必须是单个非空目录名。",
        )
    return value


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ChapterKnowledgePackageError(
            "package.source_read_failed",
            f"无法读取{label}：{path}",
        ) from exc


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChapterKnowledgePackageError(
            "package.source_json_invalid",
            f"{label}不是有效JSON对象。",
        ) from exc
    if not isinstance(payload, dict):
        raise ChapterKnowledgePackageError(
            "package.source_json_invalid",
            f"{label}必须是JSON对象。",
        )
    return payload


def _read_jsonl_bytes(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise ChapterKnowledgePackageError(
            "package.source_jsonl_invalid",
            f"{label}不是UTF-8 JSONL。",
        ) from exc
    rows: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ChapterKnowledgePackageError(
                "package.source_jsonl_invalid",
                f"{label}第{line_index}行不是有效JSON。",
            ) from exc
        if not isinstance(row, dict):
            raise ChapterKnowledgePackageError(
                "package.source_jsonl_invalid",
                f"{label}第{line_index}行必须是对象。",
            )
        rows.append(row)
    return rows


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


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_json(value: object) -> str:
    return _sha256_text(_compact_json(value))


def _stable_id(prefix: str, payload: object) -> str:
    return prefix + "_" + hashlib.sha256(
        _compact_json(payload).encode("utf-8")
    ).hexdigest()[:20]


def _now() -> str:
    return datetime.now(UTC).isoformat()
