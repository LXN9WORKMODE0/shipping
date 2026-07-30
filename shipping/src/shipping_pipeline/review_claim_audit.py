from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .chapter_knowledge_package import load_chapter_knowledge_package_run
from .llm_analysis import DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_claim_audit_contracts import (
    ReviewClaimAuditError,
    build_claim_catalogue,
    build_chapter_claim_audit_schema,
    load_review_claim_audit_config,
    validate_chapter_claim_audit,
)
from .review_writing_contracts import validate_review_chapter


REVIEW_CLAIM_AUDIT_RUN_SCHEMA_VERSION = "llm.review_claim_audit_run.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW_CLAIM_AUDIT_CONFIG = (
    PROJECT_ROOT / "config" / "review-claim-audit-default.json"
)

CLAIM_AUDIT_SYSTEM_PROMPT = """你是文献综述正文Claim审计员。输入包含程序已经切分并编号的Claim目录和该章完整知识包。只输出符合JSON Schema的对象。
不得重新切分、复制或改写Claim；claim_audits必须严格按Claim目录原顺序逐项返回paragraph_index和claim_index。
事实Claim逐项判定supported、qualified或unsupported。不要因为段落列出了论文引用就默认支持；必须实际阅读对应论文的Evidence、Card和完整Markdown。优先使用Evidence，Evidence不足时查Card，再不足时查完整Markdown，并为每个采用的citation_key标记最深的source_tier。
supported表示原文直接支持该表述；qualified表示存在支持但正文省略限制、扩大范围或需要降格；unsupported表示本知识包找不到足够支持。
单独检查正文是否把历史观察、仿真结果、算法benchmark、系统设计、工程应用或作者建议写成了更高级别的事实，并分别填写result_type_check与validation_level_check。
跨论文归纳、因果解释、效果判断和领域共识如果包含可核验外部事实，仍属于factual。
只有两类句子可标为review_synthesis：（1）真值仅涉及当前输入语料或本次综述自身的覆盖范围、语料空缺与处理边界；（2）综述者提出的纯规范性未来研究建议，且不声称该建议已被论文证明。必须使用status=not_applicable、空citation_keys、空source_assessments，两项写大检查均为not_applicable，importance只能是core或supporting。
跨论文比较、路径互补或替代关系、技术演进、因果解释、效果推断、证据强弱判断、研究共识及“具有重要意义”等句子都不是review_synthesis；只要它们描述论文、研究领域或现实对象，就必须标为factual并接受论文证据审计。
只有章节导语、结构导航等不主张外部事实且不承载 substantive synthesis 的句子才可标为navigation_synthesis。
Claim的citation_keys只能从待审计段落已有citation_keys中选择；如果需要段落外论文才能支持，应判为qualified或unsupported，不得擅自补引。source_id必须逐字复制Schema枚举值，不得使用“证据1”等自造简称。
navigation_synthesis只能用于不主张外部事实的结构导航，必须使用importance=navigation、status=not_applicable、空citation_keys、空source_assessments，两项写大检查均为not_applicable。
不要输出Evidence ID、Card ID、逐字引文或Claim文本；程序会依据citation_key和source_tier确定性展开冻结来源。"""


class ReviewClaimAuditRunner:
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
        writing_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        audit_config_path: str | Path = DEFAULT_REVIEW_CLAIM_AUDIT_CONFIG,
        response_replay_run_id: str | None = None,
        resume_from_run_id: str | None = None,
        timeout: int = 1800,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / "_review_claim_audits" / "runs" / resolved
        if run_dir.exists():
            raise ReviewClaimAuditError(
                "audit.run_exists",
                f"Claim审计run_id已存在：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        audits: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        sources: list[Any] = []
        try:
            writing_dir, writing_manifest, chapters = _load_writing_run(
                self.workspace,
                writing_run_id,
            )
            sources = [
                load_chapter_knowledge_package_run(self.workspace, value)
                for value in writing_manifest["source_package_run_ids"]
            ]
            if len(sources) != len(chapters):
                raise ReviewClaimAuditError(
                    "audit.chapter_package_count_mismatch",
                    "章节数与知识包数不一致。",
                )
            config = load_review_claim_audit_config(
                Path(audit_config_path)
            )
            profile = sources[0].profile
            if provider != profile.provider:
                raise ReviewClaimAuditError(
                    "audit.provider_mismatch",
                    "provider与知识包模型profile不一致。",
                )
            if (
                response_replay_run_id is not None
                and resume_from_run_id is not None
            ):
                raise ReviewClaimAuditError(
                    "audit.replay_resume_conflict",
                    "响应重放与失败续跑不能同时启用。",
                )
            replay_dir = None
            resume_dir = None
            if response_replay_run_id is not None:
                _safe_segment(response_replay_run_id)
                replay_dir = (
                    self.workspace
                    / "_review_claim_audits"
                    / "runs"
                    / response_replay_run_id
                )
                replay_manifest = _read_json(
                    replay_dir / "manifest.json"
                )
                if replay_manifest.get("writing_run_id") != writing_run_id:
                    raise ReviewClaimAuditError(
                        "audit.replay_writing_mismatch",
                        "响应重放运行与目标写作运行不一致。",
                    )
                client = None
                counter = None
            else:
                if resume_from_run_id is not None:
                    _safe_segment(resume_from_run_id)
                    resume_dir = (
                        self.workspace
                        / "_review_claim_audits"
                        / "runs"
                        / resume_from_run_id
                    )
                    resume_manifest = _read_json(
                        resume_dir / "manifest.json"
                    )
                    if (
                        resume_manifest.get("writing_run_id")
                        != writing_run_id
                    ):
                        raise ReviewClaimAuditError(
                            "audit.resume_writing_mismatch",
                            "续跑来源与目标写作运行不一致。",
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
                counter = self.token_counter or DeepSeekV4TokenCounter(
                    profile,
                    DEFAULT_TOKENIZER_CACHE,
                )
            _write_json(
                run_dir / "input" / "source.json",
                {
                    "writing_run_id": writing_run_id,
                    "review_id": writing_manifest["review_id"],
                    "source_package_run_ids": writing_manifest[
                        "source_package_run_ids"
                    ],
                    "response_replay_run_id": response_replay_run_id,
                    "resume_from_run_id": resume_from_run_id,
                },
            )
            _write_json(run_dir / "input" / "config.json", config.to_dict())
            for source, chapter in zip(sources, chapters, strict=True):
                index = int(chapter["section_index"])
                stage_dir = f"chapters/section-{index:03d}"
                try:
                    schema = _build_schema(
                        chapter=chapter,
                        package=source.package,
                        config=config,
                    )
                    catalogue = build_claim_catalogue(chapter)
                    _write_json(
                        run_dir / stage_dir / "schema.json",
                        schema,
                    )
                    reusable = (
                        resume_dir is not None
                        and (
                            resume_dir
                            / stage_dir
                            / "validated_audit.json"
                        ).is_file()
                    )
                    if replay_dir is not None or reusable:
                        source_run_dir = (
                            replay_dir if replay_dir is not None else resume_dir
                        )
                        source_stage_dir = source_run_dir / stage_dir
                        parsed_path = (
                            source_stage_dir
                            / "llm"
                            / "parsed_response.json"
                        )
                        if not parsed_path.is_file():
                            parsed_path = source_stage_dir / (
                                "replayed_response.json"
                                if (
                                    source_stage_dir
                                    / "replayed_response.json"
                                ).is_file()
                                else "reused_response.json"
                            )
                        parsed = _read_json(parsed_path)
                        stage_path = (
                            source_stage_dir / "llm" / "result.json"
                        )
                        if stage_path.is_file():
                            stage = _read_json(stage_path)
                        else:
                            stage = _read_json(
                                source_stage_dir / "validated_audit.json"
                            )["stage_result"]
                        _write_json(
                            run_dir
                            / stage_dir
                            / "llm"
                            / "parsed_response.json",
                            parsed,
                        )
                        _write_json(
                            run_dir
                            / stage_dir
                            / "llm"
                            / "result.json",
                            stage,
                        )
                        _write_json(
                            run_dir
                            / stage_dir
                            / (
                                "replayed_response.json"
                                if replay_dir is not None
                                else "reused_response.json"
                            ),
                            parsed,
                        )
                    else:
                        parsed, stage = execute_json_stage(
                            run_dir=run_dir,
                            stage=f"claim_audit_{index:03d}",
                            task_name="review_claim_audit",
                            directory_name=stage_dir + "/llm",
                            system_prompt=CLAIM_AUDIT_SYSTEM_PROMPT,
                            user_prompt=_build_prompt(
                                chapter,
                                source.package,
                                catalogue,
                                schema,
                            ),
                            max_output_tokens=config.audit_max_output_tokens,
                            context={
                                "writing_run_id": writing_run_id,
                                "section_index": index,
                                "package_run_id": source.run_id,
                            },
                            client=client,
                            profile=profile,
                            token_counter=counter,
                        )
                    audit = validate_chapter_claim_audit(
                        parsed,
                        schema=schema,
                        catalogue=catalogue,
                        package=source.package,
                    )
                    audit["stage_result"] = stage
                    _write_json(
                        run_dir / stage_dir / "validated_audit.json",
                        audit,
                    )
                    audits.append(audit)
                except Exception as exc:
                    failures.append(
                        {
                            "section_index": index,
                            "error_code": getattr(
                                exc, "code", type(exc).__name__
                            ),
                            "error_message": str(exc),
                            "recorded_at": _now(),
                        }
                    )
            summary = _summarize(audits, failures, len(chapters))
            _write_json(run_dir / "output" / "audit_summary.json", summary)
            _write_jsonl(run_dir / "audit" / "failures.jsonl", failures)
            status = "completed" if not failures else "failed"
            manifest = {
                "schema_version": REVIEW_CLAIM_AUDIT_RUN_SCHEMA_VERSION,
                "run_id": resolved,
                "status": status,
                "started_at": started_at,
                "finished_at": _now(),
                "writing_run_id": writing_run_id,
                "response_replay_run_id": response_replay_run_id,
                "resume_from_run_id": resume_from_run_id,
                "review_id": writing_manifest["review_id"],
                "chapter_count": len(chapters),
                "completed_chapter_count": len(audits),
                "failed_chapter_count": len(failures),
                "summary": summary,
                "failures": failures,
                "publishable": summary["publishable"] if not failures else False,
            }
            _write_json(run_dir / "manifest.json", manifest)
            from .review_claim_audit_report import render_claim_audit_report

            _write_text(
                run_dir / "review" / "claim_audit_report.md",
                render_claim_audit_report(
                    manifest,
                    audits=audits,
                    package_sources=sources,
                ),
            )
            return _result(run_dir, manifest)
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = {
                "schema_version": REVIEW_CLAIM_AUDIT_RUN_SCHEMA_VERSION,
                "run_id": resolved,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "writing_run_id": writing_run_id,
                "response_replay_run_id": response_replay_run_id,
                "resume_from_run_id": resume_from_run_id,
                "chapter_count": 0,
                "completed_chapter_count": 0,
                "failed_chapter_count": 0,
                "summary": None,
                "failures": [failure],
                "publishable": False,
            }
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def _load_writing_run(
    workspace: Path,
    run_id: str,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    _safe_segment(run_id)
    run_dir = workspace / "_review_writings" / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json")
    if (
        manifest.get("schema_version") != "llm.review_writing_run.v1"
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
        or not manifest.get("formal_review_published")
    ):
        raise ReviewClaimAuditError(
            "audit.writing_run_invalid",
            "来源写作运行未完成或未发布正式草稿。",
        )
    _read_json(run_dir / "output" / "review_draft.json")
    chapters = []
    for index, package_run_id in enumerate(
        manifest["source_package_run_ids"],
        start=1,
    ):
        chapter = _read_json(
            run_dir
            / "chapters"
            / f"section-{index:03d}"
            / "validated_chapter.json"
        )
        source = load_chapter_knowledge_package_run(
            workspace, package_run_id
        )
        validate_review_chapter(chapter_without_ids(chapter), source.schema)
        chapters.append(chapter)
    return run_dir, manifest, chapters


def chapter_without_ids(chapter: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": chapter["schema_version"],
        "section_index": chapter["section_index"],
        "title": chapter["title"],
        "paragraphs": [
            {
                "paragraph_index": row["paragraph_index"],
                "text": row["text"],
                "citation_keys": row["citation_keys"],
            }
            for row in chapter["paragraphs"]
        ],
    }


def _build_schema(
    *,
    chapter: dict[str, Any],
    package: dict[str, Any],
    config: Any,
) -> dict[str, Any]:
    catalogue = build_claim_catalogue(chapter)
    return build_chapter_claim_audit_schema(
        section_index=chapter["section_index"],
        claim_count=len(catalogue),
        citation_keys=package["section"]["citation_keys"],
        config=config,
    )


def _build_prompt(
    chapter: dict[str, Any],
    package: dict[str, Any],
    catalogue: list[dict[str, Any]],
    schema: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "任务": "按程序生成的Claim目录逐项审计",
            "待审计章节": chapter,
            "程序生成Claim目录": catalogue,
            "章节知识包": package,
            "输出前核对": [
                "claim_audits数量和顺序与Claim目录完全一致",
                "事实Claim不得仅凭citation_key判定支持",
                "语料边界和综述建议使用review_synthesis，不冒充外部事实",
                "每个citation_key给出evidence、card、markdown或none层级",
                "不得使用段落citation_keys之外的论文补强Claim",
                "仿真、benchmark、设计、建议和工程应用不得混淆",
            ],
            "输出JSONSchema": schema,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _summarize(
    audits: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    expected_chapters: int,
) -> dict[str, Any]:
    claims = [
        claim
        for audit in audits
        for claim in audit["claim_audits"]
    ]
    factual = [row for row in claims if row["claim_type"] == "factual"]
    review_synthesis = [
        row for row in claims if row["claim_type"] == "review_synthesis"
    ]
    navigation = [
        row for row in claims if row["claim_type"] == "navigation_synthesis"
    ]
    counts = {
        status: sum(row["status"] == status for row in factual)
        for status in ("supported", "qualified", "unsupported")
    }
    core_unsupported = sum(
        row["status"] == "unsupported" and row["importance"] == "core"
        for row in factual
    )
    result_overstated = sum(
        row["result_type_check"] == "overstated" for row in factual
    )
    validation_overstated = sum(
        row["validation_level_check"] == "overstated" for row in factual
    )
    normalized = sum(bool(row["normalization_flags"]) for row in claims)
    usage = Counter(
        {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    for audit in audits:
        stage_usage = audit.get("stage_result", {}).get("usage", {})
        for key in usage:
            value = stage_usage.get(key, 0)
            if type(value) is int:
                usage[key] += value
    return {
        "expected_chapter_count": expected_chapters,
        "audited_chapter_count": len(audits),
        "claim_count": len(claims),
        "factual_claim_count": len(factual),
        "review_synthesis_claim_count": len(review_synthesis),
        "navigation_claim_count": len(navigation),
        **{f"{key}_claim_count": value for key, value in counts.items()},
        "core_unsupported_claim_count": core_unsupported,
        "result_type_overstatement_count": result_overstated,
        "validation_level_overstatement_count": validation_overstated,
        "normalized_claim_count": normalized,
        "api_call_count": len(audits),
        "usage": dict(usage),
        "publishable": (
            not failures
            and len(audits) == expected_chapters
            and core_unsupported == 0
        ),
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "publishable": manifest["publishable"],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "report_path": str(
            run_dir / "review" / "claim_audit_report.md"
        ),
        "summary": manifest["summary"],
    }


def _safe_segment(value: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ReviewClaimAuditError(
            "audit.path_segment_invalid",
            f"不安全的运行ID：{value!r}",
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewClaimAuditError(
            "audit.source_read_failed",
            f"无法读取来源文件：{path}",
        ) from exc
    if not isinstance(value, dict):
        raise ReviewClaimAuditError(
            "audit.source_invalid",
            f"来源文件不是JSON对象：{path}",
        )
    return value


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


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()
