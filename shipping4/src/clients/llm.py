"""LLM client and fallback summarization helpers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from src.core.settings import get_settings

logger = logging.getLogger(__name__)


class LLMClient:
    """SiliconFlow-backed summarization client with a local fallback."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.api_key = self.settings.llm_api_key
        self.base_url = self.settings.llm_base_url
        self.model = self.settings.llm_model
        self.timeout = self.settings.llm_timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def health_check(self) -> bool:
        if not self.available:
            return False
        try:
            response = requests.post(
                self.base_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
                timeout=self.timeout,
            )
            return response.status_code == 200
        except Exception:  # pragma: no cover - network-only path
            return False

    def summarize(self, title: str, content: str, max_words: int = 150) -> str:
        if not content.strip():
            return "（章节内容为空）"
        if not self.available:
            return self._fallback_summary(content, max_words=max_words)

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个学术论文摘要专家。请根据给定章节生成简洁准确的中文摘要。"
                    f" 摘要控制在 {max_words} 字左右，只返回摘要正文。"
                ),
            },
            {
                "role": "user",
                "content": f"章节标题：{title}\n\n章节内容：\n{content[:3000]}\n\n请生成摘要：",
            },
        ]

        try:
            response = requests.post(
                self.base_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 512,
                },
                timeout=self.timeout,
            )
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code} - {response.text}")
            payload = response.json()
            text = payload["choices"][0]["message"]["content"].strip()
            return self._cleanup_response(text) or self._fallback_summary(content, max_words=max_words)
        except Exception as exc:  # pragma: no cover - network-only path
            logger.warning("LLM 摘要生成失败，使用本地降级策略: %s", exc)
            return self._fallback_summary(content, max_words=max_words)

    def refine_low_confidence_nodes(
        self,
        paper_id: str,
        detected_schema: str,
        language: str,
        nodes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Request LLM refinement for low-confidence nodes.

        Args:
            paper_id: Paper identifier.
            detected_schema: Schema detected by rules (e.g. "standard_paper", "chinese_enumeration").
            language: "zh", "en", or "mixed".
            nodes: List of low-confidence node dicts containing:
                - id, title_raw, title_norm, role, semantic_level, confidence,
                  evidence, parent_id, ordinal.

        Returns:
            List of refinement suggestion dicts, each containing:
                - node_id, reason, and optionally: role, semantic_level, confidence, evidence.

        The LLM must NOT suggest changes to: id, parent_id, ordinal, content_ref.
        """
        if not nodes:
            return []

        if not self.available:
            logger.warning("LLM unavailable, skipping refinement for paper %s", paper_id)
            return []

        system_prompt = (
            "你是一个学术论文结构校正专家。给定一组低置信度节点，请为每个节点提供修正建议。\n"
            "你只能修改以下字段：role, semantic_level, confidence, evidence。\n"
            "禁止修改：id, parent_id, ordinal, content_ref, title_raw, title_norm。\n"
            "对于每个节点，提供：node_id, reason（简短理由），以及需要修改的字段。\n"
            "如果节点看起来正确，请返回原始值或空修改。\n"
            "只返回JSON数组，不要有其他文字。"
        )

        user_prompt = (
            f"Paper ID: {paper_id}\n"
            f"Detected schema: {detected_schema}\n"
            f"Language: {language}\n\n"
            f"Low-confidence nodes:\n{json.dumps(nodes, ensure_ascii=False, indent=2)}\n\n"
            "请为每个节点提供修正建议，返回JSON数组：\n"
            "[{\"node_id\": \"...\", \"reason\": \"...\", \"role\": \"...\", "
            "\"semantic_level\": ..., \"confidence\": ..., \"evidence\": [...]}, ...]"
        )

        try:
            response = requests.post(
                self.base_url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2048,
                },
                timeout=self.timeout,
            )
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code} - {response.text}")
            payload = response.json()
            raw_content = payload["choices"][0]["message"]["content"].strip()
            cleaned = self._cleanup_response(raw_content)
            suggestions = json.loads(cleaned)
            if not isinstance(suggestions, list):
                raise ValueError(f"Expected list, got {type(suggestions)}")
            return suggestions
        except Exception as exc:  # pragma: no cover - network-only path
            logger.warning("LLM refinement failed for paper %s: %s", paper_id, exc)
            return []

    def _cleanup_response(self, response: str) -> str:
        response = response.strip()
        response = re.sub(r"^```(?:json)?\s*", "", response)
        response = re.sub(r"\s*```$", "", response)
        return response.strip()

    def _fallback_summary(self, content: str, max_words: int = 150) -> str:
        cleaned = re.sub(r"#+\s*", "", content)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return "（章节内容为空）"

        sentences = re.split(r"[。！？!?]", cleaned)
        selected: list[str] = []
        total_length = 0
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            total_length += len(sentence)
            selected.append(sentence)
            if total_length >= max_words:
                break
        if not selected:
            return cleaned[:max_words]
        return "。".join(selected) + "。"
