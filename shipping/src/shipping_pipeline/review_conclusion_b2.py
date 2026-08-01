from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .llm_analysis import DEFAULT_MODEL_PROFILE, DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_conclusion_b2_contracts import (
    ReviewConclusionB2Config,
    build_conclusion_draft_schema,
    derive_conclusion_input,
    load_review_conclusion_b2_config,
    validate_conclusion_draft,
)
from .review_conclusion_b2_report import render_review_conclusion_b2_report
from .review_writing_b2_audited_assembly import (
    ReviewWritingB2AuditedAssemblySource,
    load_review_writing_b2_audited_assembly,
)
from .review_writing_b2_revision import load_review_writing_b2_source


CONCLUSION_B2_ROOT = "_review_conclusions_b2"
CONCLUSION_B2_RUN_SCHEMA_VERSION = "llm.review_conclusion_b2_run.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW_CONCLUSION_B2_CONFIG = (
    PROJECT_ROOT / "config" / "review-conclusion-b2-default.json"
)
SYSTEM_PROMPT = """你是B2受约束综述结论写作者。只输出符合给定JSON Schema的JSON对象，不输出Markdown、解释或额外字段。
输入只包含已经通过正文审计的核心Claim，以及已批准的cross_paper_synthesis、corpus_gap和normative_recommendation。你不得重新分析论文，不得引入输入之外的事实、论文、结论、数字或建议。
严格生成四段：总体发现、路径比较、样本文献局限、未来方向。source_claim_ids只能选择输入Claim；citation_keys必须按字典序恰好等于所选Claim引用的并集。
四段合计必须覆盖前文第1至8节，同一来源Claim不得跨段重复使用。路径比较必须跨至少两个章节或使用已批准cross_paper_synthesis；语料局限必须使用corpus_gap；未来方向必须使用normative_recommendation。
路径比较和样本文献局限必须显式使用输入给定的样本文献限定语。建议必须明确写成建议或未来研究方向，不能写成已实施成果。
不得扩大result_type、validation_level、allowed_strength；必须保留required_qualifier并避免所有prohibited_phrasings。
正文不得出现claim_id、citation key、Source Window、Evidence、Card、material、section、package等机器ID，也不得手写括号引用。你的职责是压缩和组织已批准Claim，不是生成新的高层判断。"""


class ReviewConclusionB2Error(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewConclusionB2RunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    conclusion_input: dict[str, Any]
    conclusion: dict[str, Any]
    audited_source: ReviewWritingB2AuditedAssemblySource


class ReviewConclusionB2Runner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        analysis_client: Any | None = None,
        token_counter: Any | None = None,
        audited_loader: Callable[
            [str | Path, str], ReviewWritingB2AuditedAssemblySource
        ] = load_review_writing_b2_audited_assembly,
        chapter_loader: Callable[[str | Path, str], Any] = load_review_writing_b2_source,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.audited_loader = audited_loader
        self.chapter_loader = chapter_loader

    def run(
        self,
        *,
        audited_assembly_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        conclusion_config_path: str | Path = DEFAULT_REVIEW_CONCLUSION_B2_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / CONCLUSION_B2_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewConclusionB2Error(
                "conclusion_b2.run_exists", f"run_id已存在，不能覆盖：{resolved}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        audited_source = None
        conclusion_input = None
        profile = None
        config = None
        stage_result = None
        conclusion = None
        try:
            audited_source = self.audited_loader(
                self.workspace, audited_assembly_run_id
            )
            chapter_sources = [
                self.chapter_loader(self.workspace, chapter_run_id)
                for chapter_run_id in audited_source.draft["chapter_run_ids"]
            ]
            profile = load_model_profile(Path(model_profile_path))
            config = load_review_conclusion_b2_config(Path(conclusion_config_path))
            if provider != profile.provider:
                raise ReviewConclusionB2Error(
                    "conclusion_b2.provider_mismatch",
                    "provider与model profile不一致。",
                )
            profile_ids = {
                source.package_source.token_budget["model_profile_id"]
                for source in chapter_sources
            }
            if profile_ids != {profile.profile_id}:
                raise ReviewConclusionB2Error(
                    "conclusion_b2.model_profile_mismatch",
                    "受审计章节与当前结论模型profile不一致。",
                )
            conclusion_input = derive_conclusion_input(
                audited_source, chapter_sources, config=config
            )
            schema = build_conclusion_draft_schema(conclusion_input)
            _write_inputs(run_dir, conclusion_input, profile, config, schema)
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=profile.request_model,
                timeout=timeout,
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(
                profile, DEFAULT_TOKENIZER_CACHE
            )
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="conclusion_generation",
                task_name="review_conclusion_b2",
                directory_name="conclusion_generation",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_conclusion_prompt(conclusion_input, schema=schema),
                max_output_tokens=config.max_output_tokens,
                context={
                    "audited_assembly_run_id": audited_assembly_run_id,
                    "conclusion_input_id": conclusion_input["conclusion_input_id"],
                    "section_id": conclusion_input["section_task"]["section_id"],
                },
                client=client,
                profile=profile,
                token_counter=counter,
            )
            conclusion = validate_conclusion_draft(
                parsed, conclusion_input=conclusion_input, schema=schema
            )
            _write_json(
                run_dir / "conclusion_generation" / "validated_draft.json", parsed
            )
            output_path = run_dir / "output" / "review_conclusion_b2.json"
            _write_json(output_path, conclusion)
            manifest = _manifest(
                resolved,
                "completed",
                started_at,
                audited_assembly_run_id,
                audited_source,
                conclusion_input,
                profile,
                config,
                stage_result,
                conclusion,
                output_path,
                None,
            )
            _write_text(
                run_dir / "review" / "review_conclusion_b2.md",
                render_review_conclusion_b2_report(
                    manifest, conclusion, conclusion_input
                ),
            )
        except Exception as exc:
            if stage_result is None:
                stage_result = _read_optional_json(
                    run_dir / "conclusion_generation" / "result.json"
                )
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "stage": _failure_stage(run_dir),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(
                resolved,
                "failed",
                started_at,
                audited_assembly_run_id,
                audited_source,
                conclusion_input,
                profile,
                config,
                stage_result,
                conclusion,
                None,
                failure,
            )
        _write_json(run_dir / "manifest.json", manifest)
        return _result(run_dir, manifest)


def load_review_conclusion_b2_run(
    workspace: str | Path,
    run_id: str,
) -> ReviewConclusionB2RunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / CONCLUSION_B2_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "B2结论manifest")
    manifest = _json_object(manifest_bytes, "B2结论manifest")
    if (
        manifest.get("schema_version") != CONCLUSION_B2_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewConclusionB2Error(
            "conclusion_b2.run_unavailable", "B2结论运行Schema、身份或状态不可用。"
        )
    audited_source = load_review_writing_b2_audited_assembly(
        workspace_path, str(manifest["audited_assembly_run_id"])
    )
    chapter_sources = [
        load_review_writing_b2_source(workspace_path, value)
        for value in audited_source.draft["chapter_run_ids"]
    ]
    config = load_review_conclusion_b2_config(
        run_dir / "input" / "conclusion_config.json"
    )
    expected_input = derive_conclusion_input(
        audited_source, chapter_sources, config=config
    )
    frozen_input = _read_json(
        run_dir / "input" / "conclusion_input.json", "B2结论输入"
    )
    if frozen_input != expected_input:
        raise ReviewConclusionB2Error(
            "conclusion_b2.input_replay_mismatch", "冻结结论输入无法由受审计正文重放。"
        )
    schema = _read_json(run_dir / "input" / "output_schema.json", "B2结论Schema")
    if schema != build_conclusion_draft_schema(frozen_input):
        raise ReviewConclusionB2Error(
            "conclusion_b2.schema_replay_mismatch", "冻结结论Schema无法由输入重建。"
        )
    parsed = _read_json(
        run_dir / "conclusion_generation" / "validated_draft.json",
        "B2结论已验证草案",
    )
    replay = validate_conclusion_draft(
        parsed, conclusion_input=frozen_input, schema=schema
    )
    output_bytes = _read_bytes(
        run_dir / "output" / "review_conclusion_b2.json", "B2结论输出"
    )
    conclusion = _json_object(output_bytes, "B2结论输出")
    if (
        replay != conclusion
        or manifest.get("chapter_id") != conclusion.get("chapter_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewConclusionB2Error(
            "conclusion_b2.output_replay_mismatch", "B2结论身份、哈希或确定性重放不一致。"
        )
    return ReviewConclusionB2RunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        conclusion_input=frozen_input,
        conclusion=conclusion,
        audited_source=audited_source,
    )


def build_conclusion_prompt(
    conclusion_input: dict[str, Any], *, schema: dict[str, Any]
) -> str:
    payload = {
        "任务": "只依据受审计Claim目录生成独立结论章",
        "四段固定职责": {
            "overall_findings": "压缩前文章节的总体发现，不新增事实",
            "path_comparison": "比较不同提升路径，只作有边界的样本文献综合",
            "corpus_limits": "说明本次语料的证据缺口，不上升为领域空白",
            "future_direction": "只重述已批准规范性建议，明确写成建议",
        },
        "B2结论输入": conclusion_input,
        "输出前核对": [
            "四段顺序、group和claim_type严格复制Schema",
            "source_claim_ids只选输入Claim，同一Claim不跨段复用",
            "四段合计覆盖第1至8节",
            "citation_keys按字典序等于所选Claim引用并集",
            "路径比较与语料局限使用language_policy中的限定语",
            "不使用language_policy中的禁止表述",
            "总正文字符数满足writing_constraints",
            "不输出程序计算字段",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _write_inputs(run_dir, conclusion_input, profile, config, schema):
    _write_json(run_dir / "input" / "conclusion_input.json", conclusion_input)
    _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
    _write_json(run_dir / "input" / "conclusion_config.json", config.to_dict())
    _write_json(run_dir / "input" / "output_schema.json", schema)


def _manifest(
    run_id,
    status,
    started_at,
    audited_assembly_run_id,
    audited_source,
    conclusion_input,
    profile,
    config,
    stage_result,
    conclusion,
    output_path,
    failure,
):
    return {
        "schema_version": CONCLUSION_B2_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3g",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "audited_assembly_run_id": audited_assembly_run_id,
        "audited_assembly_manifest_sha256": (
            audited_source.manifest_sha256 if audited_source else None
        ),
        "audited_draft_id": audited_source.draft["draft_id"] if audited_source else None,
        "conclusion_input_id": (
            conclusion_input["conclusion_input_id"] if conclusion_input else None
        ),
        "chapter_id": conclusion["chapter_id"] if conclusion else None,
        "model_profile_id": profile.profile_id if profile else None,
        "conclusion_config": config.to_dict() if config else None,
        "stage_result": stage_result,
        "release_status": conclusion["release_status"] if conclusion else None,
        "output_sha256": (
            _sha256_bytes(output_path.read_bytes()) if output_path else None
        ),
        "failure": failure,
        "artifacts": {
            "conclusion_input": "input/conclusion_input.json" if conclusion_input else None,
            "raw_response": (
                "conclusion_generation/raw_response.json" if stage_result else None
            ),
            "conclusion": "output/review_conclusion_b2.json" if output_path else None,
            "report": "review/review_conclusion_b2.md" if output_path else None,
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
            str(run_dir / manifest["artifacts"]["conclusion"])
            if manifest["artifacts"]["conclusion"]
            else None
        ),
        "report_path": (
            str(run_dir / manifest["artifacts"]["report"])
            if manifest["artifacts"]["report"]
            else None
        ),
        "chapter_id": manifest["chapter_id"],
        "failure": manifest["failure"],
    }


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "output" / "review_conclusion_b2.json").exists():
        return "publish"
    if (run_dir / "conclusion_generation" / "raw_response.json").exists():
        return "conclusion_generation"
    if (run_dir / "input" / "conclusion_input.json").exists():
        return "prepare"
    return "load_source"


def _safe_segment(value: str) -> None:
    if not value or Path(value).name != value or value in {".", ".."}:
        raise ReviewConclusionB2Error(
            "conclusion_b2.run_id_invalid", "run_id不是安全路径段。"
        )


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewConclusionB2Error(
            "conclusion_b2.artifact_missing", f"{label}不存在：{path}"
        )
    return path.read_bytes()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    return _read_json(path, str(path)) if path.is_file() else None


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewConclusionB2Error(
            "conclusion_b2.json_invalid", f"{label}不是有效UTF-8 JSON。"
        ) from exc
    if not isinstance(value, dict):
        raise ReviewConclusionB2Error(
            "conclusion_b2.json_object_required", f"{label}必须是JSON对象。"
        )
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
