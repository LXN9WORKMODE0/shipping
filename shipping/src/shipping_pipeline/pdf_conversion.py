from __future__ import annotations

import io
import json
import http.client
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request


DEFAULT_MINERU_BASE_URL = "https://mineru.net"
DONE_STATES = {"done"}
FAILED_STATES = {"failed"}
PENDING_STATES = {"waiting-file", "pending", "running", "converting"}

MinerUTransport = Callable[[str, str, Any, dict[str, str], int], Any]


@dataclass(frozen=True)
class ConvertedDocument:
    markdown: str
    provider: str
    metadata: dict[str, Any] = field(default_factory=dict)


class MinerUApiClient:
    """用于本地 PDF 转 Markdown 的 MinerU 精确模式 API 客户端。"""

    provider = "mineru"

    def __init__(
        self,
        api_url: str = DEFAULT_MINERU_BASE_URL,
        api_key: str | None = None,
        timeout: int = 120,
        max_wait_seconds: int = 1800,
        poll_interval_seconds: float = 5,
        model_version: str = "vlm",
        language: str = "ch",
        enable_table: bool = True,
        enable_formula: bool = True,
        is_ocr: bool = False,
        page_ranges: str | None = None,
        transport: MinerUTransport | None = None,
    ) -> None:
        if not api_url:
            raise ValueError("必须配置 MinerU api_url。")
        if not api_key:
            raise ValueError("MinerU 精确解析必须配置 API key。")
        self.base_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_wait_seconds = max_wait_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.model_version = model_version
        self.language = language
        self.enable_table = enable_table
        self.enable_formula = enable_formula
        self.is_ocr = is_ocr
        self.page_ranges = page_ranges
        self.transport = transport

    @classmethod
    def from_env(
        cls,
        api_url: str | None = None,
        api_key_env: str = "MINERU_API_KEY",
        timeout: int = 120,
        max_wait_seconds: int | None = None,
        poll_interval_seconds: float | None = None,
        model_version: str | None = None,
        language: str | None = None,
        enable_table: bool | None = None,
        enable_formula: bool | None = None,
        is_ocr: bool | None = None,
        page_ranges: str | None = None,
    ) -> "MinerUApiClient":
        return cls(
            api_url=api_url or os.environ.get("MINERU_API_URL") or DEFAULT_MINERU_BASE_URL,
            api_key=os.environ.get(api_key_env),
            timeout=timeout,
            max_wait_seconds=max_wait_seconds
            if max_wait_seconds is not None
            else _env_int("MINERU_MAX_WAIT_SECONDS", 1800),
            poll_interval_seconds=poll_interval_seconds
            if poll_interval_seconds is not None
            else _env_float("MINERU_POLL_INTERVAL_SECONDS", 5),
            model_version=model_version or os.environ.get("MINERU_MODEL_VERSION") or "vlm",
            language=language or os.environ.get("MINERU_LANGUAGE") or "ch",
            enable_table=enable_table if enable_table is not None else _env_bool("MINERU_ENABLE_TABLE", True),
            enable_formula=enable_formula if enable_formula is not None else _env_bool("MINERU_ENABLE_FORMULA", True),
            is_ocr=is_ocr if is_ocr is not None else _env_bool("MINERU_IS_OCR", False),
            page_ranges=page_ranges or os.environ.get("MINERU_PAGE_RANGES") or None,
        )

    def convert(self, source_path: str | Path) -> ConvertedDocument:
        source = Path(source_path)
        data_id = _data_id_for(source)
        apply_response = self._apply_upload_url(source, data_id)
        apply_data = _require_data(apply_response, "MinerU 上传地址响应")
        batch_id = _require_str(apply_data, "batch_id", "MinerU 上传地址响应")
        file_urls = apply_data.get("file_urls")
        if not isinstance(file_urls, list) or len(file_urls) != 1 or not isinstance(file_urls[0], str):
            raise ValueError("MinerU 上传地址响应必须且只能包含一个 file URL。")

        self._upload_file(file_urls[0], source)
        result, result_response = self._poll_batch_result(batch_id, source.name)
        full_zip_url = _require_str(result, "full_zip_url", "MinerU 批量解析结果")
        markdown = _extract_full_markdown(self._download_zip(full_zip_url))
        if not markdown.strip():
            raise ValueError("MinerU full.md 为空。")

        return ConvertedDocument(
            markdown=markdown,
            provider=self.provider,
            metadata={
                "mode": "precise",
                "batch_id": batch_id,
                "data_id": data_id,
                "state": result.get("state"),
                "full_zip_url": full_zip_url,
                "model_version": self.model_version,
                "language": self.language,
                "enable_table": self.enable_table,
                "enable_formula": self.enable_formula,
                "is_ocr": self.is_ocr,
                "page_ranges": self.page_ranges,
                "trace_id": apply_response.get("trace_id"),
                "result_trace_id": result_response.get("trace_id"),
            },
        )

    def _apply_upload_url(self, source: Path, data_id: str) -> dict[str, Any]:
        file_item: dict[str, Any] = {
            "name": source.name,
            "data_id": data_id,
            "is_ocr": self.is_ocr,
        }
        if self.page_ranges:
            file_item["page_ranges"] = self.page_ranges
        payload = {
            "files": [file_item],
            "model_version": self.model_version,
            "language": self.language,
            "enable_table": self.enable_table,
            "enable_formula": self.enable_formula,
        }
        response = self._request_json("POST", self._url("/api/v4/file-urls/batch"), payload, self._auth_json_headers())
        _ensure_mineru_success(response, "申请上传地址")
        return response

    def _upload_file(self, file_url: str, source: Path) -> None:
        self._request_raw("PUT", file_url, source.read_bytes(), {"User-Agent": "shipping-pipeline/0.1"})

    def _poll_batch_result(self, batch_id: str, file_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        deadline = time.monotonic() + self.max_wait_seconds
        result_url = self._url(f"/api/v4/extract-results/batch/{batch_id}")
        last_state = "unknown"

        while True:
            response = self._request_json("GET", result_url, None, self._auth_headers())
            _ensure_mineru_success(response, "轮询批量解析结果")
            result = _select_extract_result(_require_data(response, "MinerU 批量解析结果"), file_name)
            state = str(result.get("state") or "")
            last_state = state or last_state
            if state in DONE_STATES:
                return result, response
            if state in FAILED_STATES:
                err_msg = result.get("err_msg") or "未知错误"
                raise RuntimeError(f"MinerU 解析 {file_name} 失败：{err_msg}")
            if state not in PENDING_STATES:
                raise RuntimeError(f"MinerU 返回了未预期状态，文件={file_name}，state={state!r}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"MinerU 解析 {file_name} 超时；最后状态：{last_state}")
            if self.poll_interval_seconds > 0:
                time.sleep(self.poll_interval_seconds)

    def _download_zip(self, full_zip_url: str) -> bytes:
        raw = self._request_raw("GET", full_zip_url, None, {"User-Agent": "shipping-pipeline/0.1"})
        if not isinstance(raw, bytes):
            raise TypeError("MinerU zip 下载结果不是 bytes。")
        return raw

    def _request_json(self, method: str, url: str, payload: Any, headers: dict[str, str]) -> dict[str, Any]:
        raw = self._dispatch(method, url, payload, headers)
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, bytes):
            raise TypeError(f"MinerU {method} {url} 返回了不支持的响应类型：{type(raw).__name__}。")
        return json.loads(raw.decode("utf-8"))

    def _request_raw(self, method: str, url: str, payload: Any, headers: dict[str, str]) -> Any:
        return self._dispatch(method, url, payload, headers)

    def _dispatch(self, method: str, url: str, payload: Any, headers: dict[str, str]) -> Any:
        if self.transport is not None:
            return self.transport(method, url, payload, headers, self.timeout)
        return _http_request(method, url, payload, headers, self.timeout)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "shipping-pipeline/0.1",
        }

    def _auth_json_headers(self) -> dict[str, str]:
        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"
        return headers


def _http_request(method: str, url: str, payload: Any, headers: dict[str, str], timeout: int) -> bytes:
    if method == "PUT" and isinstance(payload, bytes):
        return _http_put_without_content_type(url, payload, headers, timeout)

    data: bytes | None
    request_headers = dict(headers)
    if isinstance(payload, dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif isinstance(payload, bytes):
        data = payload
    elif payload is None:
        data = None
    else:
        raise TypeError(f"不支持的 MinerU 请求 payload 类型：{type(payload).__name__}。")

    req = request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MinerU HTTP {exc.code}，请求={method} {url}，响应={body}") from exc


def _http_put_without_content_type(url: str, payload: bytes, headers: dict[str, str], timeout: int) -> bytes:
    parsed = parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"不支持的上传 URL scheme：{parsed.scheme!r}")
    connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    host = parsed.hostname
    if not host:
        raise ValueError("上传 URL 没有 host。")
    port = parsed.port
    connection = connection_cls(host, port=port, timeout=timeout)
    path = parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    request_headers = {key: value for key, value in headers.items() if key.lower() != "content-type"}
    request_headers["Content-Length"] = str(len(payload))
    try:
        connection.request("PUT", path, body=payload, headers=request_headers)
        response = connection.getresponse()
        body = response.read()
    finally:
        connection.close()
    if response.status >= 400:
        detail = body.decode("utf-8", errors="replace")
        raise RuntimeError(f"MinerU HTTP {response.status}，请求=PUT {url}，响应={detail}")
    return body


def _ensure_mineru_success(payload: dict[str, Any], action: str) -> None:
    if payload.get("code") == 0:
        return
    raise RuntimeError(f"MinerU {action} 失败：code={payload.get('code')!r}, msg={payload.get('msg')!r}")


def _require_data(payload: dict[str, Any], context: str) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"{context} 不包含 data 对象。")
    return data


def _require_str(payload: dict[str, Any], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} 不包含 {key}。")
    return value


def _select_extract_result(data: dict[str, Any], file_name: str) -> dict[str, Any]:
    result = data.get("extract_result")
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and item.get("file_name") == file_name:
                return item
        for item in result:
            if isinstance(item, dict):
                return item
    raise ValueError("MinerU 批量结果不包含 extract_result。")


def _extract_full_markdown(zip_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        candidates = [name for name in archive.namelist() if Path(name).name == "full.md"]
        if len(candidates) != 1:
            raise ValueError("MinerU 结果 zip 必须且只能包含一个 full.md 文件。")
        return archive.read(candidates[0]).decode("utf-8-sig")


def _data_id_for(source: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", source.stem).strip(".-") or "paper"
    data_id = f"{stem}-{int(time.time())}"
    return data_id[:128]


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{key} 的布尔值无效：{raw!r}")


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return default if raw is None or raw == "" else int(raw)


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    return default if raw is None or raw == "" else float(raw)
