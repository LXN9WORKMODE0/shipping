from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .chapter_knowledge_package import (
    load_chapter_knowledge_package_run,
)
from .llm_analysis import DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter


DIRECT_REVIEW_SCHEMA_VERSION = "llm.review_direct_baseline.v1"
DIRECT_REVIEW_RUN_SCHEMA_VERSION = "llm.review_direct_baseline_run.v1"
DIRECT_REVIEW_SYSTEM_PROMPT = """你是学术文献综述撰写器。
输入只包含综述主题、目标、十四篇论文的题录、引用key和完整Markdown。
不得使用输入之外的事实，不得声称本次定向样本代表完整领域。
请自行阅读论文、组织章节并形成跨论文比较，不要逐篇摘要。
具体数字、研究结果和方法判断必须在段落citation_keys中列出对应论文。
引用key只能从输入给定列表中选择，正文中不要出现机器ID。
必须区分历史观察、仿真、算法benchmark、系统设计、建议和实际工程验证。
只输出符合JSON Schema的对象，不输出Markdown或解释。"""


class DirectReviewBaselineError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


def build_direct_review_schema(
    *,
    citation_keys: list[str],
    min_body_chars: int,
    max_body_chars: int,
) -> dict[str, Any]:
    paragraph = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "citation_keys"],
        "properties": {
            "text": {"type": "string", "minLength": 100, "maxLength": 4000},
            "citation_keys": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "enum": citation_keys},
            },
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "title",
            "abstract",
            "keywords",
            "sections",
            "conclusion",
        ],
        "properties": {
            "schema_version": {"const": DIRECT_REVIEW_SCHEMA_VERSION},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "abstract": {"type": "string", "minLength": 150, "maxLength": 1500},
            "keywords": {
                "type": "array",
                "uniqueItems": True,
                "minItems": 3,
                "maxItems": 8,
                "items": {"type": "string", "minLength": 1, "maxLength": 40},
            },
            "sections": {
                "type": "array",
                "minItems": 4,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["heading", "paragraphs"],
                    "properties": {
                        "heading": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 120,
                        },
                        "paragraphs": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 10,
                            "items": paragraph,
                        },
                    },
                },
            },
            "conclusion": paragraph,
        },
        "x-body-char-range": [min_body_chars, max_body_chars],
    }


def validate_direct_review(
    payload: object,
    *,
    schema: dict[str, Any],
) -> dict[str, Any]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda row: list(row.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        raise DirectReviewBaselineError(
            "direct_review.schema_invalid",
            error.message,
            path=path,
        )
    result = dict(payload)
    paragraphs = [
        paragraph
        for section in result["sections"]
        for paragraph in section["paragraphs"]
    ] + [result["conclusion"]]
    body_chars = sum(len(row["text"]) for row in paragraphs)
    lower, upper = schema["x-body-char-range"]
    if not lower <= body_chars <= upper:
        raise DirectReviewBaselineError(
            "direct_review.body_length_invalid",
            f"正文字符数{body_chars}不在[{lower}, {upper}]内。",
        )
    if not any(len(row["citation_keys"]) >= 2 for row in paragraphs):
        raise DirectReviewBaselineError(
            "direct_review.no_cross_paper_paragraph",
            "正文没有任何引用两篇及以上论文的跨论文段落。",
        )
    result["body_char_count"] = body_chars
    return result


class DirectReviewBaselineRunner:
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
        source_package_run_id: str,
        topic: str,
        review_goal: str,
        expected_paper_count: int,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        min_body_chars: int = 4000,
        max_body_chars: int = 35000,
        max_output_tokens: int = 32768,
        response_replay_run_id: str | None = None,
        timeout: int = 1800,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = (
            self.workspace / "_review_direct_baselines" / "runs" / resolved
        )
        if run_dir.exists():
            raise DirectReviewBaselineError(
                "direct_review.run_exists",
                f"直接写作run_id已存在：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        try:
            source = load_chapter_knowledge_package_run(
                self.workspace,
                source_package_run_id,
            )
            if provider != source.profile.provider:
                raise DirectReviewBaselineError(
                    "direct_review.provider_mismatch",
                    "provider与冻结知识包模型配置不一致。",
                )
            papers = [
                {
                    "paper_id": paper["paper_id"],
                    "paper_title": paper["paper_title"],
                    "citation_key": paper["citation_key"],
                    "bibliography": _project_bibliography(
                        paper["bibliography"]
                    ),
                    "full_markdown": paper["full_markdown"],
                }
                for paper in source.package["papers"]
            ]
            if len(papers) != expected_paper_count:
                raise DirectReviewBaselineError(
                    "direct_review.paper_count_mismatch",
                    f"预期{expected_paper_count}篇，实际{len(papers)}篇。",
                )
            citation_keys = [paper["citation_key"] for paper in papers]
            if len(set(citation_keys)) != len(citation_keys):
                raise DirectReviewBaselineError(
                    "direct_review.citation_key_duplicate",
                    "冻结论文存在重复citation_key。",
                )
            schema = build_direct_review_schema(
                citation_keys=citation_keys,
                min_body_chars=min_body_chars,
                max_body_chars=max_body_chars,
            )
            prompt = {
                "任务": "只基于十四篇完整论文直接撰写中文综述",
                "综述主题": topic,
                "综述目标": review_goal,
                "资料范围": {
                    "corpus_scope": "targeted_sample",
                    "paper_count": len(papers),
                },
                "篇幅要求": {
                    "计数范围": "sections全部段落加conclusion，不含摘要",
                    "最少字符": min_body_chars,
                    "最多字符": max_body_chars,
                    "要求": "必须充分展开比较、证据边界和适用条件，不得用短摘要替代正文。",
                },
                "论文全文": papers,
                "输出JSONSchema": schema,
            }
            _write_json(
                run_dir / "input" / "source.json",
                {
                    "source_package_run_id": source_package_run_id,
                    "source_package_sha256": source.output_sha256,
                    "paper_ids": [paper["paper_id"] for paper in papers],
                    "response_replay_run_id": response_replay_run_id,
                },
            )
            _write_json(run_dir / "input" / "papers.json", papers)
            _write_json(run_dir / "input" / "schema.json", schema)
            profile = source.profile
            if response_replay_run_id is not None:
                _safe_segment(response_replay_run_id)
                replay_dir = (
                    self.workspace
                    / "_review_direct_baselines"
                    / "runs"
                    / response_replay_run_id
                )
                replay_manifest = _read_json(replay_dir / "manifest.json")
                if (
                    replay_manifest.get("source_package_run_id")
                    != source_package_run_id
                ):
                    raise DirectReviewBaselineError(
                        "direct_review.replay_source_mismatch",
                        "响应重放来源知识包不一致。",
                    )
                parsed = _read_json(
                    replay_dir / "llm" / "parsed_response.json"
                )
                stage = _read_json(replay_dir / "llm" / "result.json")
                _write_json(
                    run_dir / "llm" / "parsed_response.json",
                    parsed,
                )
                _write_json(run_dir / "llm" / "result.json", stage)
                _write_json(
                    run_dir / "llm" / "replayed_response.json",
                    parsed,
                )
            else:
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
                parsed, stage = execute_json_stage(
                    run_dir=run_dir,
                    stage="direct_review",
                    task_name="review_direct_baseline",
                    directory_name="llm",
                    system_prompt=DIRECT_REVIEW_SYSTEM_PROMPT,
                    user_prompt=json.dumps(
                        prompt,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    max_output_tokens=max_output_tokens,
                    context={
                        "source_package_run_id": source_package_run_id,
                        "paper_count": len(papers),
                    },
                    client=client,
                    profile=profile,
                    token_counter=counter,
                )
            review = validate_direct_review(parsed, schema=schema)
            _write_json(run_dir / "output" / "review_draft.json", review)
            _write_text(
                run_dir / "review" / "review_draft.md",
                render_direct_review(review),
            )
            manifest = {
                "schema_version": DIRECT_REVIEW_RUN_SCHEMA_VERSION,
                "run_id": resolved,
                "status": "completed",
                "started_at": started_at,
                "finished_at": _now(),
                "source_package_run_id": source_package_run_id,
                "topic": topic,
                "review_goal": review_goal,
                "response_replay_run_id": response_replay_run_id,
                "paper_count": len(papers),
                "body_char_count": review["body_char_count"],
                "request": stage,
            }
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)
        except Exception as exc:
            manifest = {
                "schema_version": DIRECT_REVIEW_RUN_SCHEMA_VERSION,
                "run_id": resolved,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "source_package_run_id": source_package_run_id,
                "topic": topic,
                "review_goal": review_goal,
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
            }
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def render_direct_review(review: dict[str, Any]) -> str:
    lines = [
        f"# {review['title']}",
        "",
        "## 摘要",
        "",
        review["abstract"],
        "",
        f"**关键词：** {'；'.join(review['keywords'])}",
        "",
    ]
    for section in review["sections"]:
        lines.extend([f"## {section['heading']}", ""])
        for paragraph in section["paragraphs"]:
            lines.extend(
                [
                    paragraph["text"]
                    + _citation_suffix(paragraph["citation_keys"]),
                    "",
                ]
            )
    lines.extend(
        [
            "## 结论",
            "",
            review["conclusion"]["text"]
            + _citation_suffix(review["conclusion"]["citation_keys"]),
            "",
        ]
    )
    return "\n".join(lines)


def _citation_suffix(keys: list[str]) -> str:
    if not keys:
        return ""
    return " [" + "; ".join(f"@{key}" for key in keys) + "]"


def _project_bibliography(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "entry_type",
            "title",
            "authors",
            "year",
            "journal",
            "volume",
            "issue",
            "pages",
            "doi",
            "institution",
            "degree",
        )
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "review_path": str(run_dir / "review" / "review_draft.md"),
        "body_char_count": manifest.get("body_char_count"),
        "usage": manifest.get("request", {}).get("usage", {}),
        "error_code": manifest.get("error_code"),
        "error_message": manifest.get("error_message"),
    }


def _safe_segment(value: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise DirectReviewBaselineError(
            "direct_review.path_segment_invalid",
            f"不安全的运行ID：{value!r}",
        )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DirectReviewBaselineError(
            "direct_review.json_read_failed",
            f"无法读取JSON：{path}",
        ) from exc
    if not isinstance(payload, dict):
        raise DirectReviewBaselineError(
            "direct_review.json_not_object",
            f"JSON不是对象：{path}",
        )
    return payload


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()
