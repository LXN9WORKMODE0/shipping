"""MinerU client implementation."""

from __future__ import annotations

import io
import logging
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import requests

from src.clients.base import BaseConversionClient
from src.clients.retry import with_retry, with_status_retry
from src.core.models import RawConversionResult
from src.core.settings import get_settings

logger = logging.getLogger(__name__)


class MinerUError(RuntimeError):
    """Raised when the MinerU API returns an error."""


class MinerUClient(BaseConversionClient):
    """Thin MinerU client. It only talks to the API and returns raw results."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.api_key = self.settings.mineru_api_key
        self.base_url = self.settings.mineru_base_url
        self.timeout = self.settings.mineru_timeout
        self.model_version = self.settings.mineru_model_version
        self.poll_interval = self.settings.mineru_poll_interval
        self.max_wait_time = self.settings.mineru_max_wait_time

        if not self.api_key:
            logger.warning("MINERU_API_KEY 未配置")

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    @with_status_retry
    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}/{endpoint}"
        headers = kwargs.pop("headers", {})
        payload_headers = self._headers()
        payload_headers.update(headers)
        return requests.request(method=method, url=url, headers=payload_headers, timeout=self.timeout, **kwargs)

    def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            # Use a harmless authenticated endpoint probe. The public docs expose
            # `file-urls/batch`, while `user/profile` returns 404 on the current API.
            response = self._request(
                "POST",
                "file-urls/batch",
                json={"files": [], "model_version": self.model_version},
            )
            if response.status_code != 200:
                return False
            payload = response.json()
            return isinstance(payload, dict) and "code" in payload
        except Exception:  # pragma: no cover - network-only path
            return False

    def apply_upload_urls(self, files: list[dict[str, str]]) -> dict[str, Any]:
        response = self._request(
            "POST",
            "file-urls/batch",
            json={"files": files, "model_version": self.model_version},
        )
        if response.status_code != 200:
            raise MinerUError(f"申请上传链接失败: HTTP {response.status_code} - {response.text}")
        result = response.json()
        if result.get("code") != 0:
            raise MinerUError(f"申请上传链接失败: {result.get('message', 'Unknown error')}")
        return result.get("data", {})

    @with_retry(retry_on_exceptions=(ConnectionError, TimeoutError, requests.RequestException))
    def upload_file(self, file_path: Path, upload_url: str) -> bool:
        with open(file_path, "rb") as handle:
            response = requests.put(upload_url, data=handle, timeout=self.timeout)
        return response.status_code == 200

    def batch_upload_files(self, files: list[Path]) -> str:
        file_specs = [{"name": file_path.name, "data_id": str(uuid.uuid4())} for file_path in files]
        data = self.apply_upload_urls(file_specs)
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls", [])
        if not batch_id:
            raise MinerUError("上传链接响应缺少 batch_id")
        for file_path, upload_url in zip(files, file_urls):
            if not self.upload_file(file_path, upload_url):
                raise MinerUError(f"文件上传失败: {file_path.name}")
        return batch_id

    def get_batch_results(self, batch_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"extract-results/batch/{batch_id}")
        if response.status_code != 200:
            raise MinerUError(f"查询结果失败: HTTP {response.status_code} - {response.text}")
        result = response.json()
        if result.get("code") != 0:
            raise MinerUError(f"查询结果失败: {result.get('message', 'Unknown error')}")
        return result.get("data", {}).get("extract_result", [])

    def _zip_to_result(self, payload: bytes) -> RawConversionResult:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            raw_files: dict[str, bytes] = {}
            for name in archive.namelist():
                if name.endswith("/"):
                    continue
                raw_files[name] = archive.read(name)

        markdown_bytes = raw_files.get("full.md")
        if markdown_bytes is None:
            markdown_name = next((name for name in raw_files if name.endswith(".md")), None)
            if markdown_name is None:
                raise MinerUError("ZIP 中没有找到 Markdown 文件")
            markdown_bytes = raw_files[markdown_name]

        return RawConversionResult(markdown=markdown_bytes.decode("utf-8", errors="ignore"), raw_files=raw_files)

    def download_result(self, zip_url: str) -> RawConversionResult:
        response = requests.get(zip_url, timeout=self.timeout)
        if response.status_code != 200:
            raise MinerUError(f"下载结果失败: HTTP {response.status_code}")
        return self._zip_to_result(response.content)

    def wait_for_batch_completion(self, batch_id: str) -> RawConversionResult:
        started_at = time.time()
        while time.time() - started_at <= self.max_wait_time:
            tasks = self.get_batch_results(batch_id)
            if not tasks:
                time.sleep(self.poll_interval)
                continue

            task = tasks[0]
            status = task.get("status") or task.get("state")
            if status in {"done", "completed"}:
                if task.get("full_zip_url"):
                    return self.download_result(task["full_zip_url"])
                markdown = task.get("markdown", "")
                return RawConversionResult(markdown=markdown, raw_files={"full.md": markdown.encode("utf-8")})
            if status == "failed":
                raise MinerUError(task.get("err_msg") or task.get("error") or "MinerU 任务失败")
            time.sleep(self.poll_interval)
        raise MinerUError(f"批次处理超时（{self.max_wait_time}秒）")

    def convert_file(self, pdf_path: Path) -> RawConversionResult:
        self.validate_pdf(pdf_path)
        if not self.api_key:
            raise MinerUError("MINERU_API_KEY 未配置")
        batch_id = self.batch_upload_files([pdf_path])
        return self.wait_for_batch_completion(batch_id)

    def _create_task_from_url(self, file_url: str) -> str:
        response = self._request(
            "POST",
            "extract/task",
            json={"url": file_url, "model_version": self.model_version},
        )
        if response.status_code != 200:
            raise MinerUError(f"创建任务失败: HTTP {response.status_code} - {response.text}")
        result = response.json()
        if result.get("code") != 0:
            raise MinerUError(f"创建任务失败: {result.get('message', 'Unknown error')}")
        task_id = result.get("data", {}).get("task_id")
        if not task_id:
            raise MinerUError("响应中缺少 task_id")
        return task_id

    def _wait_for_task_completion(self, task_id: str) -> RawConversionResult:
        started_at = time.time()
        while time.time() - started_at <= self.max_wait_time:
            response = self._request("GET", f"extract/task/{task_id}")
            if response.status_code != 200:
                raise MinerUError(f"查询任务失败: HTTP {response.status_code}")
            result = response.json()
            if result.get("code") != 0:
                raise MinerUError(f"查询任务失败: {result.get('message', 'Unknown error')}")
            data = result.get("data", {})
            status = data.get("status")
            if status == "completed":
                if data.get("full_zip_url"):
                    return self.download_result(data["full_zip_url"])
                markdown = data.get("markdown") or data.get("result", {}).get("markdown") or ""
                return RawConversionResult(markdown=markdown, raw_files={"full.md": markdown.encode("utf-8")})
            if status == "failed":
                raise MinerUError(data.get("error", "URL 转换失败"))
            time.sleep(self.poll_interval)
        raise MinerUError(f"任务超时（{self.max_wait_time}秒）")

    def convert_url(self, url: str) -> RawConversionResult:
        if not self.api_key:
            raise MinerUError("MINERU_API_KEY 未配置")
        task_id = self._create_task_from_url(url)
        return self._wait_for_task_completion(task_id)
