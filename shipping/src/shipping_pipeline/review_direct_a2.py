from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .chapter_knowledge_package import load_chapter_knowledge_package_run
from .llm_analysis import DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .review_direct_a2_contracts import (
    ReviewDirectA2Config,
    build_direct_a2_schema,
    derive_direct_a2_input,
    load_review_direct_a2_config,
    validate_direct_a2_draft,
)
from .review_writing_b2_audited_assembly import (
    load_review_writing_b2_audited_assembly,
)
from .review_writing_b2_revision import load_review_writing_b2_source


DIRECT_A2_ROOT = "_review_direct_baselines_a2"
DIRECT_A2_RUN_SCHEMA_VERSION = "llm.review_direct_a2_run.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIRECT_A2_CONFIG = PROJECT_ROOT / "config" / "review-direct-a2-default.json"
SYSTEM_PROMPT = """你是A2直接全文综述写作者。只输出符合JSON Schema的对象，不输出Markdown、解释或额外字段。
你只可使用输入中的14篇完整Markdown、题录和统一Framework。不得使用Paper Understanding、Research Landscape、Claim Ledger、Source Window或B2正文。
必须严格按照Framework给定的九章顺序、section_id、标题和任务写作，不得改章、增章或删章。全文字符数必须落在writing_constraints范围内。
请直接阅读全文并形成跨论文比较，不要逐篇摘要。具体数字、方法、结果、限制和作者判断必须在所在段落citation_keys中列出对应论文；citation_keys只能选输入给定值。
text必须是纯中文正文，绝对禁止出现ref_、[ref_...]、[@ref_...]或任何引用标记，即使输入Markdown中存在类似标记也不得复制。错误示例：{"text":"结论[ref_x]。","citation_keys":["ref_x"]}。正确示例：{"text":"结论。","citation_keys":["ref_x"]}。
必须区分历史观察、仿真、算法测试、系统设计、建议和工程实施，不得扩大结果类型或验证水平。语料缺口只能写成“本次纳入文献未覆盖”，不得写成领域空白或普遍共识。
正文不得出现任何机器ID。paragraph_index必须在各章从1连续递增。"""


class ReviewDirectA2Error(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewDirectA2RunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    a2_input: dict[str, Any]
    review: dict[str, Any]


def load_review_direct_a2_run(
    workspace: str | Path, run_id: str
) -> ReviewDirectA2RunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id)
    run_dir = workspace_path / DIRECT_A2_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(run_dir / "manifest.json", "A2 manifest")
    manifest = _json_object(manifest_bytes, "A2 manifest")
    if (
        manifest.get("schema_version") != DIRECT_A2_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
    ):
        raise ReviewDirectA2Error("direct_a2.run_unavailable", "A2运行不可用。")
    package = load_chapter_knowledge_package_run(
        workspace_path, str(manifest["source_package_run_id"])
    )
    body = load_review_writing_b2_audited_assembly(
        workspace_path, str(manifest["audited_assembly_run_id"])
    )
    first_chapter = load_review_writing_b2_source(
        workspace_path, body.draft["chapter_run_ids"][0]
    )
    config = load_review_direct_a2_config(run_dir / "input" / "config.json")
    expected_input = derive_direct_a2_input(
        source_package=package,
        audited_body=body,
        framework=first_chapter.writing_input["framework_outline"],
        config=config,
    )
    frozen_input = _read_json(run_dir / "input" / "a2_input.json", "A2输入")
    if frozen_input != expected_input:
        raise ReviewDirectA2Error("direct_a2.input_replay_mismatch", "A2冻结输入无法重放。")
    schema = _read_json(run_dir / "input" / "output_schema.json", "A2输出Schema")
    if schema != build_direct_a2_schema(frozen_input):
        raise ReviewDirectA2Error("direct_a2.schema_replay_mismatch", "A2输出Schema无法重放。")
    parsed = _read_json(
        run_dir / "direct_a2_generation" / "validated_draft.json", "A2已验证草案"
    )
    replay = validate_direct_a2_draft(parsed, a2_input=frozen_input, schema=schema)
    output_bytes = _read_bytes(run_dir / "output" / "review_direct_a2.json", "A2输出")
    review = _json_object(output_bytes, "A2输出")
    if (
        replay != review
        or manifest.get("review_id") != review.get("review_id")
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
    ):
        raise ReviewDirectA2Error("direct_a2.output_replay_mismatch", "A2输出哈希或重放不一致。")
    return ReviewDirectA2RunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        a2_input=frozen_input,
        review=review,
    )


class ReviewDirectA2Runner:
    def __init__(self, workspace, *, analysis_client=None, token_counter=None) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        source_package_run_id: str,
        audited_assembly_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        config_path: str | Path = DEFAULT_DIRECT_A2_CONFIG,
        timeout: int = 1800,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / DIRECT_A2_ROOT / "runs" / resolved
        if run_dir.exists():
            raise ReviewDirectA2Error("direct_a2.run_exists", f"run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        package = body = a2_input = config = stage_result = review = None
        try:
            package = load_chapter_knowledge_package_run(
                self.workspace, source_package_run_id
            )
            body = load_review_writing_b2_audited_assembly(
                self.workspace, audited_assembly_run_id
            )
            first_chapter = load_review_writing_b2_source(
                self.workspace, body.draft["chapter_run_ids"][0]
            )
            framework = first_chapter.writing_input["framework_outline"]
            config = load_review_direct_a2_config(Path(config_path))
            if provider != package.profile.provider:
                raise ReviewDirectA2Error(
                    "direct_a2.provider_mismatch", "provider与全文知识包模型配置不一致。"
                )
            if config.max_output_tokens > package.profile.model_max_output_tokens:
                raise ReviewDirectA2Error(
                    "direct_a2.output_budget_invalid", "A2输出预算超过模型上限。"
                )
            a2_input = derive_direct_a2_input(
                source_package=package,
                audited_body=body,
                framework=framework,
                config=config,
            )
            schema = build_direct_a2_schema(a2_input)
            _write_json(run_dir / "input" / "a2_input.json", a2_input)
            _write_json(run_dir / "input" / "output_schema.json", schema)
            _write_json(run_dir / "input" / "config.json", config.to_dict())
            _write_json(run_dir / "input" / "model_profile.json", package.profile.to_dict())
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url,
                api_key_env=api_key_env,
                model=package.profile.request_model,
                timeout=timeout,
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(
                package.profile, DEFAULT_TOKENIZER_CACHE
            )
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="direct_a2_generation",
                task_name="review_direct_a2",
                directory_name="direct_a2_generation",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=_build_prompt(a2_input, schema),
                max_output_tokens=config.max_output_tokens,
                context={
                    "source_package_run_id": source_package_run_id,
                    "audited_assembly_run_id": audited_assembly_run_id,
                    "a2_input_id": a2_input["a2_input_id"],
                    "paper_count": 14,
                },
                client=client,
                profile=package.profile,
                token_counter=counter,
            )
            review = validate_direct_a2_draft(parsed, a2_input=a2_input, schema=schema)
            _write_json(run_dir / "direct_a2_generation" / "validated_draft.json", parsed)
            output_path = run_dir / "output" / "review_direct_a2.json"
            _write_json(output_path, review)
            _write_text(run_dir / "review" / "review_direct_a2.md", _render_review(review))
            manifest = _manifest(
                resolved, "completed", started_at, source_package_run_id,
                audited_assembly_run_id, package, a2_input, config, stage_result,
                review, output_path, None
            )
        except Exception as exc:
            if stage_result is None:
                stage_result = _read_optional_json(run_dir / "direct_a2_generation" / "result.json")
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _manifest(
                resolved, "failed", started_at, source_package_run_id,
                audited_assembly_run_id, package, a2_input, config, stage_result,
                review, None, failure
            )
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": manifest["status"],
            "run_dir": str(run_dir),
            "output_path": str(run_dir / "output" / "review_direct_a2.json") if review else None,
            "review_path": str(run_dir / "review" / "review_direct_a2.md") if review else None,
            "review_id": review["review_id"] if review else None,
            "body_char_count": review["body_char_count"] if review else None,
            "failure": manifest["failure"],
        }


def _build_prompt(a2_input, schema):
    return json.dumps(
        {
            "任务": "基于14篇完整Markdown按统一Framework直接撰写A2综述",
            "公平对照说明": {
                "允许输入": "完整Markdown、题录、统一Framework",
                "禁止输入": a2_input["excluded_cognitive_inputs"],
                "篇幅": a2_input["writing_constraints"],
            },
            "A2输入": a2_input,
            "输出前核对": [
                "每一段text必须语义完整，并以中文句末标点。！？之一结束",
                "text不得出现任何ref_或引用标记，引用只放citation_keys",
                "九章标题、顺序和section_id严格复制Framework",
                "正文总字符数满足writing_constraints",
            ],
            "输出JSONSchema": schema,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _render_review(review):
    lines = [f"# {review['working_title']}", ""]
    for chapter in review["chapters"]:
        lines.extend([f"## {chapter['section_index']}. {chapter['title']}", ""])
        for paragraph in chapter["paragraphs"]:
            text = paragraph["text"]
            if paragraph["citation_keys"]:
                text += " [" + "; ".join(f"@{key}" for key in paragraph["citation_keys"]) + "]"
            lines.extend([text, ""])
    return "\n".join(lines).rstrip() + "\n"


def _manifest(run_id, status, started_at, package_run, body_run, package,
              a2_input, config, stage, review, output_path, failure):
    return {
        "schema_version": DIRECT_A2_RUN_SCHEMA_VERSION,
        "pipeline_generation": "A2.phase6a",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "source_package_run_id": package_run,
        "audited_assembly_run_id": body_run,
        "source_package_sha256": package.output_sha256 if package else None,
        "a2_input_id": a2_input["a2_input_id"] if a2_input else None,
        "review_id": review["review_id"] if review else None,
        "body_char_count": review["body_char_count"] if review else None,
        "model_profile_id": package.profile.profile_id if package else None,
        "config": config.to_dict() if config else None,
        "stage_result": stage,
        "output_sha256": _sha256_bytes(output_path.read_bytes()) if output_path else None,
        "failure": failure,
    }


def _safe_segment(value):
    if not value or Path(value).name != value or value in {".", ".."}:
        raise ReviewDirectA2Error("direct_a2.run_id_invalid", "run_id不是安全路径段。")


def _read_optional_json(path):
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else None


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise ReviewDirectA2Error("direct_a2.artifact_missing", f"{label}不存在：{path}")
    return path.read_bytes()


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewDirectA2Error("direct_a2.json_invalid", f"{label}不是有效JSON。") from exc
    if not isinstance(value, dict):
        raise ReviewDirectA2Error("direct_a2.json_object_required", f"{label}必须是对象。")
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _sha256_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _now():
    return datetime.now(UTC).isoformat()
