from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .chapter_knowledge_package import DEFAULT_REVIEW_WRITING_CONFIG
from .chapter_knowledge_package_b2_v2 import (
    B2PackageV2RunSource,
    load_b2_package_v2_run,
)
from .claim_ledger import ClaimLedgerRunSource, load_claim_ledger_run
from .llm_analysis import DEFAULT_MODEL_PROFILE, DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_writing_b2_contracts import (
    WRITING_POLICY,
    build_chapter_draft_schema,
    derive_writing_input,
    validate_chapter_draft,
    validate_writing_input,
)
from .review_writing_contracts import (
    ReviewWritingConfig,
    load_review_writing_config,
)


WRITING_B2_ROOT = "_review_writings_b2"
WRITING_B2_RUN_SCHEMA_VERSION = "llm.review_writing_b2_run.v1"
SYSTEM_PROMPT = """你是B2受约束综述章节写作者。只输出符合给定JSON Schema的JSON对象，不输出Markdown、解释或额外字段。
只写当前章节，只实现Approved Claim；禁止新增核心事实、未批准的跨论文结论、论文、citation key或验证层级。
程序已经在paragraph_plan中确定每段必须实现的Claim和引用。implemented_claim_ids和citation_keys必须逐项原样复制对应段落计划，不得增删、调序或自行选择。
每段在达到paragraph_plan.generation_target_chars前不得结束；段落可以长短不同，但章节总长必须处于writing_constraints范围内。
段落citation_keys必须恰好等于该段implemented_claim_ids所允许引用的并集，不得漏引或补引。
正文text不得出现claim_id、citation key、Source Window、Evidence、Card、material、section、package等机器ID，也不得手写括号引用。
必须保留每条Claim的required_qualifier，避免prohibited_phrasings；不得把仿真、建议、设计或有限验证写成工程事实。
你的职责是组织、连接和准确表达已批准Claim，不是重新分析论文或补充知识。"""


class ReviewWritingB2Error(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewWritingB2RunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    writing_input: dict[str, Any]
    chapter: dict[str, Any]
    package_source: B2PackageV2RunSource


def load_review_writing_b2_run(
    workspace: str | Path,
    run_id: str,
    *,
    package_loader: Callable[[str | Path, str], B2PackageV2RunSource]
    | None = None,
    claim_loader: Callable[[str | Path, str], ClaimLedgerRunSource]
    | None = None,
) -> ReviewWritingB2RunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / WRITING_B2_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "B2写作manifest")
    manifest = _json_object(manifest_bytes, "B2写作manifest")
    if (
        manifest.get("schema_version") != WRITING_B2_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewWritingB2Error(
            "writing_b2.run_unavailable",
            "B2写作运行Schema、身份或状态不可用。",
        )
    load_package = package_loader or load_b2_package_v2_run
    load_claim = claim_loader or load_claim_ledger_run
    package_source = load_package(
        workspace_path,
        str(manifest["package_run_id"]),
    )
    claim_source = load_claim(
        workspace_path,
        package_source.package["source"]["claim_ledger_run_id"],
    )
    config = load_review_writing_config(
        run_dir / "input" / "writing_config.json"
    )
    expected_input = derive_writing_input(
        package_source.package,
        claim_source.framework_b2,
        package_run_id=package_source.run_id,
        package_manifest_sha256=package_source.manifest_sha256,
        max_paragraph_chars=config.max_paragraph_chars,
    )
    frozen_input = validate_writing_input(
        _read_json(run_dir / "input" / "writing_input.json", "B2写作输入")
    )
    if frozen_input != expected_input:
        raise ReviewWritingB2Error(
            "writing_b2.input_replay_mismatch",
            "冻结写作输入无法由Phase 2包和Claim Ledger重放。",
        )
    schema = _read_json(
        run_dir / "input" / "output_schema.json",
        "B2写作输出Schema",
    )
    expected_schema = build_chapter_draft_schema(frozen_input)
    if schema != expected_schema:
        raise ReviewWritingB2Error(
            "writing_b2.schema_replay_mismatch",
            "冻结输出Schema无法由写作输入重建。",
        )
    draft = _read_json(
        run_dir / "chapter_generation" / "validated_draft.json",
        "B2已验证章节草案",
    )
    replay = validate_chapter_draft(
        draft,
        writing_input=frozen_input,
        schema=schema,
    )
    output_bytes = _read_bytes(
        run_dir / "output" / "review_chapter.json",
        "B2正式章节",
    )
    chapter = _json_object(output_bytes, "B2正式章节")
    if (
        replay != chapter
        or manifest.get("chapter_id") != chapter.get("chapter_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewWritingB2Error(
            "writing_b2.output_replay_mismatch",
            "B2章节输出身份、哈希或确定性重放不一致。",
        )
    return ReviewWritingB2RunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        writing_input=frozen_input,
        chapter=chapter,
        package_source=package_source,
    )


class ReviewWritingB2Runner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
        package_loader: Callable[
            [str | Path, str], B2PackageV2RunSource
        ]
        | None = None,
        claim_loader: Callable[[str | Path, str], ClaimLedgerRunSource]
        | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.package_loader = package_loader or load_b2_package_v2_run
        self.claim_loader = claim_loader or load_claim_ledger_run

    def run(
        self,
        *,
        package_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        writing_config_path: str | Path = DEFAULT_REVIEW_WRITING_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / WRITING_B2_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewWritingB2Error(
                "writing_b2.run_exists",
                f"run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        package_source: B2PackageV2RunSource | None = None
        writing_input: dict[str, Any] | None = None
        profile: ModelProfile | None = None
        config: ReviewWritingConfig | None = None
        stage_result: dict[str, Any] | None = None
        chapter: dict[str, Any] | None = None
        try:
            package_source = self.package_loader(
                self.workspace,
                package_run_id,
            )
            claim_source = self.claim_loader(
                self.workspace,
                package_source.package["source"]["claim_ledger_run_id"],
            )
            if (
                claim_source.ledger["ledger_id"]
                != package_source.package["source"]["claim_ledger_id"]
            ):
                raise ReviewWritingB2Error(
                    "writing_b2.claim_ledger_mismatch",
                    "知识包与Claim Ledger身份不一致。",
                )
            profile = load_model_profile(Path(model_profile_path))
            config = load_review_writing_config(Path(writing_config_path))
            if provider != profile.provider:
                raise ReviewWritingB2Error(
                    "writing_b2.provider_mismatch",
                    "provider与model profile不一致。",
                )
            if (
                package_source.token_budget["model_profile_id"]
                != profile.profile_id
                or package_source.token_budget["planned_max_output_tokens"]
                != config.chapter_max_output_tokens
            ):
                raise ReviewWritingB2Error(
                    "writing_b2.package_runtime_mismatch",
                    "Phase 2包与当前模型或写作输出预算不一致。",
                )
            writing_input = derive_writing_input(
                package_source.package,
                claim_source.framework_b2,
                package_run_id=package_source.run_id,
                package_manifest_sha256=package_source.manifest_sha256,
                max_paragraph_chars=config.max_paragraph_chars,
            )
            schema = build_chapter_draft_schema(writing_input)
            _write_inputs(
                run_dir,
                writing_input=writing_input,
                profile=profile,
                config=config,
                schema=schema,
            )
            if writing_input["writing_ready"] is not True:
                raise ReviewWritingB2Error(
                    "writing_b2.conclusion_not_ready",
                    "结论章必须等待前文章节完成Claim审计后再生成。",
                )
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=profile.request_model,
                timeout=timeout,
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(
                profile,
                DEFAULT_TOKENIZER_CACHE,
            )
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="chapter_generation",
                task_name="review_writing_b2",
                directory_name="chapter_generation",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_chapter_prompt(writing_input, schema=schema),
                max_output_tokens=config.chapter_max_output_tokens,
                context={
                    "package_run_id": package_run_id,
                    "writing_input_id": writing_input["writing_input_id"],
                    "section_id": writing_input["section_task"]["section_id"],
                    "section_index": writing_input["section_task"]["section_index"],
                    "section_title": writing_input["section_task"]["title"],
                },
                client=client,
                profile=profile,
                token_counter=counter,
            )
            chapter = validate_chapter_draft(
                parsed,
                writing_input=writing_input,
                schema=schema,
            )
            _write_json(
                run_dir / "chapter_generation" / "validated_draft.json",
                parsed,
            )
            _write_json(run_dir / "output" / "review_chapter.json", chapter)
            _write_text(
                run_dir / "review" / "review_chapter.md",
                render_chapter_review(chapter, writing_input),
            )
            manifest = _manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                package_run_id=package_run_id,
                package_source=package_source,
                writing_input=writing_input,
                profile=profile,
                config=config,
                stage_result=stage_result,
                chapter=chapter,
                output_path=run_dir / "output" / "review_chapter.json",
                failure=None,
            )
        except Exception as exc:
            if stage_result is None:
                stage_result = _read_optional_json(
                    run_dir / "chapter_generation" / "result.json"
                )
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "stage": _failure_stage(run_dir),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(
                run_id=resolved,
                status="failed",
                started_at=started_at,
                package_run_id=package_run_id,
                package_source=package_source,
                writing_input=writing_input,
                profile=profile,
                config=config,
                stage_result=stage_result,
                chapter=chapter,
                output_path=None,
                failure=failure,
            )
        _write_json(run_dir / "manifest.json", manifest)
        return _result(run_dir, manifest)


def build_chapter_prompt(
    writing_input: dict[str, Any],
    *,
    schema: dict[str, Any],
) -> str:
    constraints = writing_input["writing_constraints"]
    payload = {
        "任务": "只依据B2写作输入实现当前章节Approved Claim",
        "硬性篇幅": {
            "目标字符": constraints["target_chars"],
            "允许最少字符": constraints["min_chars"],
            "允许最多字符": constraints["max_chars"],
            "段落数": (
                f"{constraints['min_paragraphs']}至"
                f"{constraints['max_paragraphs']}段"
            ),
            "逐段字符范围": "见paragraph_plan，属于硬性范围",
        },
        "程序确定的逐段Claim与引用计划": writing_input[
            "paragraph_plan"
        ],
        "B2写作输入": writing_input,
        "输出前核对": [
            "逐段原样复制paragraph_plan中的required_claim_ids和required_citation_keys",
            "单段不超过两个core Claim、总Claim不超过四个",
            "每段citation_keys恰好等于本段Claim引用并集",
            "保留required_qualifier，不使用prohibited_phrasings",
            "text不出现任何机器ID或手写引用",
            "不新增Ledger之外的事实、论文、结论或建议",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def render_chapter_review(
    chapter: dict[str, Any],
    writing_input: dict[str, Any],
) -> str:
    lines = [
        f"# {chapter['section_index']}. {chapter['title']}",
        "",
        f"- 章节ID：`{chapter['chapter_id']}`",
        f"- 正文字符：{chapter['implementation_audit']['total_chars']}",
        f"- Approved Claim：{chapter['implementation_audit']['approved_claim_count']}",
        f"- 已实现Claim：{chapter['implementation_audit']['implemented_claim_count']}",
        f"- 未实现supporting Claim：{len(chapter['implementation_audit']['omitted_supporting_claim_ids'])}",
        "- 计划外语义Claim审计：尚未运行",
        "",
    ]
    claims = {
        row["claim_id"]: row for row in writing_input["approved_claims"]
    }
    for paragraph in chapter["paragraphs"]:
        lines.extend(
            [
                f"## 段落 {paragraph['paragraph_index']}",
                "",
                paragraph["text"],
                "",
                "实现Claim：",
            ]
        )
        for claim_id in paragraph["implemented_claim_ids"]:
            lines.append(
                f"- `{claim_id}` [{claims[claim_id]['importance']}] "
                f"{claims[claim_id]['planned_claim']}"
            )
        citations = "、".join(paragraph["citation_keys"]) or "无"
        lines.extend([f"- 引用：{citations}", ""])
    return "\n".join(lines).rstrip() + "\n"


def _write_inputs(
    run_dir: Path,
    *,
    writing_input: dict[str, Any],
    profile: ModelProfile,
    config: ReviewWritingConfig,
    schema: dict[str, Any],
) -> None:
    _write_json(run_dir / "input" / "writing_input.json", writing_input)
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(run_dir / "input" / "writing_config.json", config.to_dict())
    _write_json(run_dir / "input" / "writing_policy.json", WRITING_POLICY)
    _write_json(run_dir / "input" / "output_schema.json", schema)


def _manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    package_run_id: str,
    package_source: B2PackageV2RunSource | None,
    writing_input: dict[str, Any] | None,
    profile: ModelProfile | None,
    config: ReviewWritingConfig | None,
    stage_result: dict[str, Any] | None,
    chapter: dict[str, Any] | None,
    output_path: Path | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": WRITING_B2_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3a",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "package_run_id": package_run_id,
        "package_id": (
            package_source.package["package_id"] if package_source else None
        ),
        "writing_input_id": (
            writing_input["writing_input_id"] if writing_input else None
        ),
        "section_id": (
            writing_input["section_task"]["section_id"]
            if writing_input
            else None
        ),
        "section_index": (
            writing_input["section_task"]["section_index"]
            if writing_input
            else None
        ),
        "chapter_id": chapter["chapter_id"] if chapter else None,
        "model_profile_id": profile.profile_id if profile else None,
        "writing_config": config.to_dict() if config else None,
        "stage_result": stage_result,
        "output_sha256": (
            _sha256_bytes(output_path.read_bytes()) if output_path else None
        ),
        "semantic_unplanned_claim_audit": "not_run",
        "failure": failure,
        "artifacts": {
            "writing_input": (
                "input/writing_input.json" if writing_input else None
            ),
            "raw_response": (
                "chapter_generation/raw_response.json"
                if stage_result
                else None
            ),
            "chapter": "output/review_chapter.json" if output_path else None,
            "report": "review/review_chapter.md" if output_path else None,
            "failures": "audit/failures.jsonl" if failure else None,
        },
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "output_path": (
            str(run_dir / manifest["artifacts"]["chapter"])
            if manifest["artifacts"]["chapter"]
            else None
        ),
        "report_path": (
            str(run_dir / manifest["artifacts"]["report"])
            if manifest["artifacts"]["report"]
            else None
        ),
        "chapter_id": manifest["chapter_id"],
        "section_index": manifest["section_index"],
        "failure": manifest["failure"],
    }


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "output" / "review_chapter.json").exists():
        return "publish"
    if (run_dir / "chapter_generation" / "raw_response.json").exists():
        return "chapter_generation"
    if (run_dir / "input" / "writing_input.json").exists():
        return "prepare"
    return "load_source"


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewWritingB2Error(
            "writing_b2.artifact_missing",
            f"{label}不存在：{path}",
        )
    return path.read_bytes()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return _read_json(path, str(path))


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewWritingB2Error(
            "writing_b2.json_invalid",
            f"{label}不是有效UTF-8 JSON。",
        ) from exc
    if not isinstance(value, dict):
        raise ReviewWritingB2Error(
            "writing_b2.json_object_required",
            f"{label}必须是JSON对象。",
        )
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ).encode("utf-8")
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((value.rstrip() + "\n").encode("utf-8"))


def _safe_segment(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in {".", ".."}
        or any(char in value for char in ("/", "\\", "\x00"))
    ):
        raise ReviewWritingB2Error(
            "writing_b2.run_id_invalid",
            "run_id不是安全路径段。",
        )
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat()
