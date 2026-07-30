from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .llm_analysis import (
    AnalysisInputError,
    DEFAULT_TOKENIZER_CACHE,
)
from .llm_json_stage import execute_json_stage
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .reference_catalog import format_gbt_reference
from .review_writing_contracts import (
    ReviewWritingCollection,
    load_review_writing_collection,
    validate_review_chapter,
)


REVIEW_WRITING_RUN_SCHEMA_VERSION = "llm.review_writing_run.v1"
REVIEW_WRITING_OUTPUT_SCHEMA_VERSION = "llm.review_draft.v1"

REVIEW_CHAPTER_SYSTEM_PROMPT = """你是文献综述章节写作者。输入是程序确定性组装并通过来源重放的单章知识包，只输出符合给定JSON Schema的JSON对象，不要输出Markdown、解释或Schema之外字段。
只写当前章节，不写其他章节，不自行检索、补充或引用知识包之外的论文。
正文必须是连贯自然的中文学术段落，围绕章节问题和required_comparisons组织跨论文比较，不按论文逐篇罗列。
事实、数字、论文结论和方法描述必须在citation_keys字段中列出相应引用Key。引用Key只能出现在citation_keys数组中，text字段不得自行写括号引用、方括号引用、ref_字符串或任何citation_key。正文text中也不得出现Evidence ID、Card ID、material_id、contribution_id、section_id、package_id等机器ID。
必须区分历史观察、仿真结果、算法benchmark、工程应用、系统设计、作者建议和模型综合判断。不得把仿真写成实际工程效果，不得把建议写成已实施事实，不得把有限或不确定证据写成确定结论。
当知识包不足以回答某项比较任务时，明确说明本次语料无法回答，并结合corpus_limitations或corpus_gaps界定范围；不得编造答案。
引用只表示该段使用了对应论文，不能代替具体论证。每段应尽量完成至少一项清晰的分析动作：界定、比较、解释、评价边界或综合。"""


class ReviewWritingError(AnalysisInputError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def build_review_chapter_prompt(
    knowledge_package: dict[str, Any],
    *,
    schema: dict[str, Any],
) -> str:
    payload = {
        "任务": "依据单章知识包撰写当前综述章节",
        "章节知识包": knowledge_package,
        "输出前逐项核对": [
            "只回答当前section的问题和比较任务",
            "每项事实和论文判断使用知识包允许的citation_key",
            "引用Key只写入citation_keys数组，text不得出现ref_或任何引用Key",
            "正文不出现任何Evidence、Card、material、contribution、section或package机器ID",
            "不把仿真、建议、系统设计或有限证据升级为工程事实",
            "资料不足时明确写出本次语料边界，不编造补全",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    )


class ReviewWritingRunner:
    def __init__(
        self,
        workspace: str | Path,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
        package_loader: Callable[[str | Path, str], Any] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.package_loader = package_loader

    def run(
        self,
        *,
        collection_path: str | Path,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime(
            "%Y%m%d-%H%M%S"
        )
        _safe_segment(resolved_run_id, "run_id")
        run_dir = self.workspace / "_review_writings" / "runs" / resolved_run_id
        if run_dir.exists():
            raise ReviewWritingError(
                "writing.run_exists",
                f"综述写作run_id已存在，不能覆盖不可变运行：{resolved_run_id}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        collection: ReviewWritingCollection | None = None
        package_sources: list[Any] = []
        chapter_statuses: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        draft: dict[str, Any] | None = None
        try:
            collection = load_review_writing_collection(
                Path(collection_path)
            )
            package_sources = self._load_packages(collection)
            _write_input_artifacts(
                run_dir,
                collection=collection,
                package_sources=package_sources,
            )
            profile = package_sources[0].profile
            config = package_sources[0].config
            if provider != profile.provider:
                raise ReviewWritingError(
                    "writing.provider_mismatch",
                    f"provider与知识包模型profile不一致："
                    f"provider={provider!r}, profile={profile.provider!r}。",
                )
            client = (
                self.analysis_client
                or OpenAICompatibleAnalysisClient.from_env(
                    api_url=api_url,
                    api_key_env=api_key_env,
                    model=profile.request_model,
                    timeout=timeout,
                )
            )
            token_counter = (
                self.token_counter
                or DeepSeekV4TokenCounter(
                    profile,
                    DEFAULT_TOKENIZER_CACHE,
                )
            )
            for source in package_sources:
                section = source.package["section"]
                section_index = int(section["section_index"])
                directory_name = (
                    f"chapters/section-{section_index:03d}"
                )
                stage_result: dict[str, Any] | None = None
                try:
                    parsed, stage_result = execute_json_stage(
                        run_dir=run_dir,
                        stage=f"chapter_{section_index:03d}",
                        task_name="review_chapter",
                        directory_name=directory_name,
                        system_prompt=REVIEW_CHAPTER_SYSTEM_PROMPT,
                        user_prompt=build_review_chapter_prompt(
                            source.package,
                            schema=source.schema,
                        ),
                        max_output_tokens=config.chapter_max_output_tokens,
                        context={
                            "writing_run_id": resolved_run_id,
                            "package_run_id": source.run_id,
                            "package_id": source.package["package_id"],
                            "section_index": section_index,
                            "section_title": section["title"],
                            "section_type": section["section_type"],
                            "citation_keys": section["citation_keys"],
                        },
                        client=client,
                        profile=profile,
                        token_counter=token_counter,
                    )
                    chapter = validate_review_chapter(
                        parsed,
                        source.schema,
                    )
                    _write_json(
                        run_dir
                        / directory_name
                        / "validated_chapter.json",
                        chapter,
                    )
                    chapter_statuses.append(
                        _chapter_status(
                            source,
                            status="completed",
                            stage_result=stage_result,
                            chapter=chapter,
                            failure=None,
                        )
                    )
                except Exception as exc:
                    stage_result = stage_result or _read_optional_json(
                        run_dir / directory_name / "result.json"
                    )
                    failure = {
                        "section_index": section_index,
                        "section_title": section["title"],
                        "package_run_id": source.run_id,
                        "error_code": getattr(
                            exc,
                            "code",
                            type(exc).__name__,
                        ),
                        "error_message": str(exc),
                        "recorded_at": _now(),
                    }
                    failures.append(failure)
                    chapter_statuses.append(
                        _chapter_status(
                            source,
                            status="failed",
                            stage_result=stage_result,
                            chapter=None,
                            failure=failure,
                        )
                    )

            _write_jsonl(
                run_dir / "audit" / "chapter_statuses.jsonl",
                chapter_statuses,
            )
            _write_jsonl(
                run_dir / "audit" / "failures.jsonl",
                failures,
            )
            if failures:
                _write_text(
                    run_dir / "review" / "review_partial.md",
                    assemble_review_markdown(
                        package_sources,
                        chapter_statuses,
                        include_failures=True,
                    ),
                )
                status = "failed"
            else:
                draft = assemble_review_draft(
                    package_sources,
                    chapter_statuses,
                )
                _write_json(
                    run_dir / "output" / "review_draft.json",
                    draft,
                )
                _write_text(
                    run_dir / "review" / "review_draft.md",
                    draft["markdown"],
                )
                status = "completed"

            manifest = _build_manifest(
                run_id=resolved_run_id,
                status=status,
                started_at=started_at,
                collection=collection,
                package_sources=package_sources,
                chapter_statuses=chapter_statuses,
                failures=failures,
                draft=draft,
                failure=None,
            )
            _write_json(run_dir / "manifest.json", manifest)
            from .review_writing_report import render_review_writing_report

            _write_text(
                run_dir / "review" / "review_writing_report.md",
                render_review_writing_report(manifest),
            )
            return _result(run_dir, manifest)
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "stage": "input_validation",
                "recorded_at": _now(),
            }
            failures.append(failure)
            _write_jsonl(
                run_dir / "audit" / "failures.jsonl",
                failures,
            )
            manifest = _build_manifest(
                run_id=resolved_run_id,
                status="failed",
                started_at=started_at,
                collection=collection,
                package_sources=package_sources,
                chapter_statuses=chapter_statuses,
                failures=failures,
                draft=None,
                failure=failure,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)

    def _load_packages(
        self,
        collection: ReviewWritingCollection,
    ) -> list[Any]:
        loader = self.package_loader
        if loader is None:
            from .chapter_knowledge_package import (
                load_chapter_knowledge_package_run,
            )

            loader = load_chapter_knowledge_package_run
        sources = [
            loader(self.workspace, run_id)
            for run_id in collection.source_package_run_ids
        ]
        _validate_package_collection(collection, sources)
        return sources


def assemble_review_draft(
    package_sources: list[Any],
    chapter_statuses: list[dict[str, Any]],
) -> dict[str, Any]:
    if any(row["status"] != "completed" for row in chapter_statuses):
        raise ReviewWritingError(
            "writing.chapters_incomplete",
            "存在失败章节，不能装配正式综述。",
        )
    markdown = assemble_review_markdown(
        package_sources,
        chapter_statuses,
        include_failures=False,
    )
    framework = package_sources[0].framework
    draft_without_id = {
        "schema_version": REVIEW_WRITING_OUTPUT_SCHEMA_VERSION,
        "framework_run_id": (
            package_sources[0].package["source"]["framework_run_id"]
        ),
        "working_title": framework["working_title"],
        "chapter_ids": [
            row["chapter"]["chapter_id"] for row in chapter_statuses
        ],
        "paper_ids": _all_paper_ids(package_sources),
        "citation_keys": sorted(_reference_by_key(package_sources)),
        "markdown": markdown,
        "final_edit": {
            "status": "not_run_audit_required",
            "reason": (
                "最终全文编辑只能在所有章节通过Claim审计后运行，"
                "编辑后必须重新审计。"
            ),
        },
    }
    return {
        **draft_without_id,
        "review_id": _stable_id("review", draft_without_id),
    }


def assemble_review_markdown(
    package_sources: list[Any],
    chapter_statuses: list[dict[str, Any]],
    *,
    include_failures: bool,
) -> str:
    framework = package_sources[0].framework
    paper_ids = _all_paper_ids(package_sources)
    lines = [
        f"# {framework['working_title']}",
        "",
        "## 资料范围",
        "",
        (
            f"本文仅基于本次显式纳入的{len(paper_ids)}篇论文展开，"
            f"围绕“{framework['central_question']}”进行综合。"
            "文中判断不代表对该领域全部文献的系统穷尽。"
        ),
        "",
    ]
    for source, status in zip(
        package_sources,
        chapter_statuses,
        strict=True,
    ):
        section = source.package["section"]
        if status["status"] == "failed":
            if include_failures:
                lines.extend(
                    [
                        f"## {section['section_index']}. {section['title']}",
                        "",
                        (
                            "[本章生成失败，未进入正式综述。"
                            f"错误：{status['failure']['error_code']}。]"
                        ),
                        "",
                    ]
                )
            continue
        lines.extend(
            [
                f"## {section['section_index']}. {section['title']}",
                "",
            ]
        )
        for paragraph in status["chapter"]["paragraphs"]:
            text = paragraph["text"].strip()
            citations = paragraph["citation_keys"]
            if citations:
                text += " [" + "; ".join(f"@{key}" for key in citations) + "]"
            lines.extend([text, ""])

    lines.extend(["## 参考文献", ""])
    for reference in _ordered_references(package_sources):
        lines.extend([format_gbt_reference(reference), ""])
    if include_failures:
        failures = [
            row for row in chapter_statuses if row["status"] == "failed"
        ]
        lines.extend(["## 本次运行未完成章节", ""])
        for row in failures:
            lines.append(
                f"- 第{row['section_index']}章“{row['section_title']}”："
                f"{row['failure']['error_code']}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _validate_package_collection(
    collection: ReviewWritingCollection,
    sources: list[Any],
) -> None:
    if not sources:
        raise ReviewWritingError(
            "writing.packages_empty",
            "综述写作集合没有知识包。",
        )
    framework_run_ids = {
        source.package["source"]["framework_run_id"]
        for source in sources
    }
    if framework_run_ids != {collection.framework_run_id}:
        raise ReviewWritingError(
            "writing.framework_identity_mismatch",
            "知识包不属于集合声明的同一Framework运行。",
        )
    framework_ids = {
        source.package["source"]["framework_id"] for source in sources
    }
    if len(framework_ids) != 1:
        raise ReviewWritingError(
            "writing.framework_mixed",
            "知识包来源包含多个Framework身份。",
        )
    section_indexes = [
        int(source.package["section"]["section_index"])
        for source in sources
    ]
    expected = list(range(1, len(sources) + 1))
    if section_indexes != expected:
        raise ReviewWritingError(
            "writing.section_order_invalid",
            f"知识包必须按连续章节顺序列出："
            f"expected={expected}, actual={section_indexes}。",
        )
    framework = sources[0].framework
    expected_sections = [
        int(row["section_index"]) for row in framework["sections"]
    ]
    if section_indexes != expected_sections:
        raise ReviewWritingError(
            "writing.framework_sections_incomplete",
            "知识包集合未完整覆盖Framework章节。",
        )
    if any(source.framework != framework for source in sources[1:]):
        raise ReviewWritingError(
            "writing.framework_snapshot_mismatch",
            "各知识包冻结的Framework内容不一致。",
        )
    profile_sha256s = {source.profile.sha256 for source in sources}
    configs = {
        json.dumps(
            source.config.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        )
        for source in sources
    }
    if len(profile_sha256s) != 1 or len(configs) != 1:
        raise ReviewWritingError(
            "writing.runtime_config_mixed",
            "各知识包必须使用同一模型profile和写作配置。",
        )


def _write_input_artifacts(
    run_dir: Path,
    *,
    collection: ReviewWritingCollection,
    package_sources: list[Any],
) -> None:
    _write_json(run_dir / "input" / "collection.json", collection.to_dict())
    rows: list[dict[str, Any]] = []
    for source in package_sources:
        section_index = int(source.package["section"]["section_index"])
        relative_path = (
            f"input/packages/section-{section_index:03d}.json"
        )
        _write_json(run_dir / relative_path, source.package)
        rows.append(
            {
                "package_run_id": source.run_id,
                "section_index": section_index,
                "section_id": source.package["section"]["section_id"],
                "package_id": source.package["package_id"],
                "manifest_sha256": source.manifest_sha256,
                "output_sha256": source.output_sha256,
                "frozen_path": relative_path,
            }
        )
    _write_jsonl(run_dir / "input" / "package_sources.jsonl", rows)
    _write_json(
        run_dir / "input" / "model_profile.json",
        package_sources[0].profile.to_dict(),
    )
    _write_json(
        run_dir / "input" / "writing_config.json",
        package_sources[0].config.to_dict(),
    )


def _chapter_status(
    source: Any,
    *,
    status: str,
    stage_result: dict[str, Any] | None,
    chapter: dict[str, Any] | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    section = source.package["section"]
    return {
        "section_index": section["section_index"],
        "section_id": section["section_id"],
        "section_title": section["title"],
        "package_run_id": source.run_id,
        "package_id": source.package["package_id"],
        "status": status,
        "stage_result": copy.deepcopy(stage_result),
        "chapter": copy.deepcopy(chapter),
        "failure": copy.deepcopy(failure),
    }


def _build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    collection: ReviewWritingCollection | None,
    package_sources: list[Any],
    chapter_statuses: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    draft: dict[str, Any] | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    completed = sum(
        row["status"] == "completed" for row in chapter_statuses
    )
    failed = sum(row["status"] == "failed" for row in chapter_statuses)
    usage = _aggregate_usage(chapter_statuses)
    return {
        "schema_version": REVIEW_WRITING_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "framework_run_id": (
            collection.framework_run_id if collection else None
        ),
        "source_package_run_ids": (
            list(collection.source_package_run_ids) if collection else []
        ),
        "package_count": len(package_sources),
        "chapter_count": len(chapter_statuses),
        "completed_chapter_count": completed,
        "failed_chapter_count": failed,
        "chapter_statuses": copy.deepcopy(chapter_statuses),
        "usage": usage,
        "review_id": draft.get("review_id") if draft else None,
        "formal_review_published": draft is not None,
        "final_edit": (
            copy.deepcopy(draft["final_edit"])
            if draft
            else {
                "status": "not_run_chapters_incomplete",
                "reason": "存在失败章节，不能执行全文编辑。",
            }
        ),
        "failures": copy.deepcopy(failures),
        "failure": copy.deepcopy(failure),
        "artifacts": {
            "collection": "input/collection.json" if collection else None,
            "package_sources": (
                "input/package_sources.jsonl" if package_sources else None
            ),
            "chapter_statuses": (
                "audit/chapter_statuses.jsonl"
                if chapter_statuses
                else None
            ),
            "failures": "audit/failures.jsonl",
            "review_draft_json": (
                "output/review_draft.json" if draft else None
            ),
            "review_draft_markdown": (
                "review/review_draft.md" if draft else None
            ),
            "partial_review_markdown": (
                "review/review_partial.md"
                if chapter_statuses and draft is None
                else None
            ),
            "report": "review/review_writing_report.md",
        },
    }


def _aggregate_usage(
    chapter_statuses: list[dict[str, Any]],
) -> dict[str, int]:
    result = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    for row in chapter_statuses:
        stage = row.get("stage_result") or {}
        usage = stage.get("usage") or {}
        for key in result:
            value = usage.get(key)
            if type(value) is int:
                result[key] += value
    return result


def _all_paper_ids(package_sources: list[Any]) -> list[str]:
    return sorted(
        {
            str(paper["paper_id"])
            for source in package_sources
            for paper in source.package["papers"]
        }
    )


def _reference_by_key(
    package_sources: list[Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source in package_sources:
        for paper in source.package["papers"]:
            key = str(paper["citation_key"])
            reference = copy.deepcopy(paper["bibliography"])
            existing = result.get(key)
            if existing is not None and existing != reference:
                raise ReviewWritingError(
                    "writing.reference_conflict",
                    f"引用Key {key!r} 对应多个不同题录。",
                )
            result[key] = reference
    return result


def _ordered_references(
    package_sources: list[Any],
) -> list[dict[str, Any]]:
    references = list(_reference_by_key(package_sources).values())
    return sorted(references, key=lambda row: int(row["number"]))


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "review_path": (
            str(run_dir / manifest["artifacts"]["review_draft_markdown"])
            if manifest["artifacts"]["review_draft_markdown"]
            else None
        ),
        "partial_review_path": (
            str(run_dir / manifest["artifacts"]["partial_review_markdown"])
            if manifest["artifacts"]["partial_review_markdown"]
            else None
        ),
        "report_path": str(
            run_dir / "review" / "review_writing_report.md"
        ),
        "completed_chapter_count": manifest["completed_chapter_count"],
        "failed_chapter_count": manifest["failed_chapter_count"],
    }


def _safe_segment(value: str, field: str) -> None:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise ReviewWritingError(
            "writing.path_segment_invalid",
            f"{field}不是安全目录名：{value!r}",
        )


def _stable_id(prefix: str, payload: object) -> str:
    compact = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


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
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
