from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request


LLMTransport = Callable[[dict[str, Any], dict[str, str], int], dict[str, Any] | bytes | str]


@dataclass(frozen=True)
class ProviderResult:
    raw_body: bytes
    parsed_response: dict[str, Any] | None
    response_id: str | None
    response_model: str | None
    system_fingerprint: str | None
    finish_reason: str | None
    reasoning_content_chars: int
    usage: dict[str, int | None]


class ProviderCallError(RuntimeError):
    def __init__(self, message: str, *, raw_body: bytes = b"", status_code: int | None = None) -> None:
        self.raw_body = raw_body
        self.status_code = status_code
        super().__init__(message)


class OpenAICompatibleAnalysisClient:
    provider = "openai-compatible"

    def __init__(
        self,
        api_url: str,
        api_key: str | None,
        model: str,
        timeout: int = 120,
        transport: LLMTransport | None = None,
    ) -> None:
        if not api_url:
            raise ValueError("openai-compatible provider 需要配置 LLM api_url。")
        if not model:
            raise ValueError("openai-compatible provider 需要配置 LLM model。")
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport

    @classmethod
    def from_env(
        cls,
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model: str = "",
        timeout: int = 120,
    ) -> "OpenAICompatibleAnalysisClient":
        resolved_url = api_url or os.environ.get("LLM_ANALYSIS_API_URL", "")
        return cls(
            api_url=resolved_url,
            api_key=os.environ.get(api_key_env),
            model=model,
            timeout=timeout,
        )

    def complete(self, task: str, request_payload: dict[str, Any], context: dict[str, Any]) -> ProviderResult:
        del task, context
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = (
                self.transport(request_payload, headers, self.timeout)
                if self.transport
                else _post_json_bytes(self.api_url, request_payload, headers, self.timeout)
            )
        except error.HTTPError as exc:
            raw_body = exc.read()
            raise ProviderCallError(
                f"LLM API 返回 HTTP {exc.code}。",
                raw_body=raw_body,
                status_code=exc.code,
            ) from exc
        except error.URLError as exc:
            raise ProviderCallError(f"LLM API 网络错误：{exc.reason}") from exc

        raw_body, parsed = _normalize_transport_response(response)
        result = ProviderResult(
            raw_body=raw_body,
            parsed_response=parsed,
            response_id=_optional_text(parsed, "id"),
            response_model=_optional_text(parsed, "model"),
            system_fingerprint=_optional_text(parsed, "system_fingerprint"),
            finish_reason=_finish_reason(parsed),
            reasoning_content_chars=_reasoning_content_chars(parsed),
            usage=_extract_usage(parsed),
        )
        try:
            validate_completed_response(
                result,
                thinking_disabled=request_payload.get("thinking") == {"type": "disabled"},
                requested_model=self.model,
            )
        except ProviderCallError as exc:
            raise ProviderCallError(str(exc), raw_body=raw_body) from exc
        return result


def build_chat_request(
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    thinking: dict[str, str],
) -> dict[str, Any]:
    if type(max_tokens) is not int or max_tokens < 1:
        raise ValueError("max_tokens 必须是正整数。")
    if thinking != {"type": "disabled"}:
        raise ValueError("当前 LLM 请求只允许 thinking.type=disabled。")
    return {
        "model": model,
        "max_tokens": max_tokens,
        "thinking": dict(thinking),
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }


def validate_completed_response(
    result: ProviderResult,
    *,
    thinking_disabled: bool,
    requested_model: str | None = None,
) -> None:
    choices = result.parsed_response.get("choices") if result.parsed_response else None
    if not isinstance(choices, list) or len(choices) != 1:
        count = len(choices) if isinstance(choices, list) else "invalid"
        raise ProviderCallError(f"provider.choice_count.{count}")
    if result.finish_reason != "stop":
        raise ProviderCallError(f"provider.finish_reason.{result.finish_reason or 'missing'}")
    if thinking_disabled and result.reasoning_content_chars:
        raise ProviderCallError("provider.non_think_returned_reasoning")
    if requested_model is not None and result.response_model != requested_model:
        raise ProviderCallError(
            "provider.response_model_mismatch: "
            f"requested={requested_model!r}, response={result.response_model!r}"
        )
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = result.usage.get(key)
        if type(value) is not int or value < 0:
            raise ProviderCallError(f"provider.usage_invalid.{key}")
    if result.usage["total_tokens"] != result.usage["prompt_tokens"] + result.usage["completion_tokens"]:
        raise ProviderCallError("provider.usage_total_mismatch")


def extract_chat_content(response: dict[str, Any] | None) -> str:
    if response is None:
        raise ValueError("LLM 响应不是有效 JSON 对象。")
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("LLM 响应中没有 choices[0].message.content。") from exc
    if not isinstance(content, str) or not content:
        raise ValueError("LLM 响应的 choices[0].message.content 必须是非空字符串。")
    return content


def _post_json_bytes(
    api_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(api_url, data=data, headers=headers, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _normalize_transport_response(
    response: dict[str, Any] | bytes | str,
) -> tuple[bytes, dict[str, Any] | None]:
    if isinstance(response, dict):
        raw_body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        return raw_body, response
    raw_body = response.encode("utf-8") if isinstance(response, str) else bytes(response)
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw_body, None
    return raw_body, payload if isinstance(payload, dict) else None


def _optional_text(payload: dict[str, Any] | None, key: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _extract_usage(payload: dict[str, Any] | None) -> dict[str, int | None]:
    usage = payload.get("usage", {}) if payload else {}
    if not isinstance(usage, dict):
        usage = {}
    return {
        "prompt_tokens": _optional_int(usage.get("prompt_tokens")),
        "completion_tokens": _optional_int(usage.get("completion_tokens")),
        "total_tokens": _optional_int(usage.get("total_tokens")),
    }


def _finish_reason(payload: dict[str, Any] | None) -> str | None:
    choice = _single_choice(payload)
    if choice is None:
        return None
    value = choice.get("finish_reason")
    return value if isinstance(value, str) and value else None


def _reasoning_content_chars(payload: dict[str, Any] | None) -> int:
    choice = _single_choice(payload)
    message = choice.get("message") if choice else None
    if not isinstance(message, dict):
        return 0
    value = message.get("reasoning_content")
    if value is None or value == "":
        return 0
    if isinstance(value, str):
        return len(value)
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _single_choice(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    choices = payload.get("choices") if payload else None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return None
    return choices[0]


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
