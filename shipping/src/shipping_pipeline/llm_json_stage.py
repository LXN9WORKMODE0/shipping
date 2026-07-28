from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .llm_analysis import AnalysisInputError
from .llm_model_profile import ModelProfile
from .llm_provider import (
    ProviderCallError,
    ProviderResult,
    build_chat_request,
    extract_chat_content,
    validate_completed_response,
)


class JSONStageInputError(AnalysisInputError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def execute_json_stage(
    *,
    run_dir: Path,
    stage: str,
    task_name: str,
    directory_name: str | None,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    context: dict[str, Any],
    client: Any,
    profile: ModelProfile,
    token_counter: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    stage_dir = run_dir / (directory_name or stage)
    stage_dir.mkdir(parents=True, exist_ok=False)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    prompt_sha256 = _sha256_text(system_prompt + "\n" + user_prompt)
    request_id = stage + "_" + hashlib.sha256(
        (stage + "\n" + prompt_sha256).encode("utf-8")
    ).hexdigest()[:16]
    started_at = _now()
    count = None
    provider_result: ProviderResult | None = None
    try:
        count = token_counter.count_messages(messages)
        if max_output_tokens > profile.model_max_output_tokens:
            raise JSONStageInputError(
                "input.output_budget_exceeded",
                f"{stage} 输出预算超过模型上限：{max_output_tokens}。",
            )
        if (
            count.prompt_tokens
            + max_output_tokens
            + profile.safety_margin_tokens
            > profile.context_window_tokens
        ):
            raise JSONStageInputError(
                "input.context_budget_exceeded",
                f"{stage} 超过上下文窗口：input={count.prompt_tokens}, "
                f"output={max_output_tokens}, margin={profile.safety_margin_tokens}。",
            )
        request_payload = build_chat_request(
            client.model,
            system_prompt,
            user_prompt,
            max_tokens=max_output_tokens,
            thinking=profile.thinking,
        )
        _write_json(stage_dir / "request.json", request_payload)
        provider_result = client.complete(
            task_name,
            request_payload,
            {
                **context,
                "request_id": request_id,
                "planned_input_tokens": count.prompt_tokens,
                "planned_max_output_tokens": max_output_tokens,
            },
        )
        (stage_dir / "raw_response.json").write_bytes(provider_result.raw_body)
        validate_completed_response(
            provider_result,
            thinking_disabled=True,
            requested_model=str(client.model),
        )
        actual_prompt_tokens = provider_result.usage.get("prompt_tokens")
        if actual_prompt_tokens != count.prompt_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"planned={count.prompt_tokens}, actual={actual_prompt_tokens}"
            )
        parsed = json.loads(extract_chat_content(provider_result.parsed_response))
        if not isinstance(parsed, dict):
            raise ValueError("模型输出必须是 JSON 对象。")
        _write_json(stage_dir / "parsed_response.json", parsed)
        result = {
            "request_id": request_id,
            "stage": stage,
            "status": "completed",
            "started_at": started_at,
            "finished_at": _now(),
            "prompt_sha256": prompt_sha256,
            "planned_input_tokens": count.prompt_tokens,
            "planned_max_output_tokens": max_output_tokens,
            "actual_prompt_tokens": actual_prompt_tokens,
            "finish_reason": provider_result.finish_reason,
            "reasoning_content_chars": provider_result.reasoning_content_chars,
            "usage": provider_result.usage,
            "response_id": provider_result.response_id,
            "response_model": provider_result.response_model,
        }
        _write_json(stage_dir / "result.json", result)
        return parsed, result
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            (stage_dir / "raw_response.json").write_bytes(exc.raw_body)
        result = {
            "request_id": request_id,
            "stage": stage,
            "status": "failed",
            "started_at": started_at,
            "finished_at": _now(),
            "prompt_sha256": prompt_sha256,
            "planned_input_tokens": (
                count.prompt_tokens if count is not None else None
            ),
            "planned_max_output_tokens": max_output_tokens,
            "actual_prompt_tokens": (
                provider_result.usage.get("prompt_tokens")
                if provider_result
                else None
            ),
            "finish_reason": provider_result.finish_reason if provider_result else None,
            "reasoning_content_chars": (
                provider_result.reasoning_content_chars
                if provider_result
                else None
            ),
            "usage": provider_result.usage if provider_result else {},
            "error_code": getattr(exc, "code", type(exc).__name__),
            "error_message": str(exc),
        }
        _write_json(stage_dir / "result.json", result)
        raise


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
