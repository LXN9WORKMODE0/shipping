from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODEL_PROFILE_SCHEMA_VERSION = "llm.model_profile.v4"


class ModelProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ModelProfile:
    schema_version: str
    profile_id: str
    provider: str
    request_model: str
    tokenizer_repo: str
    tokenizer_revision: str
    prompt_token_overhead: int
    context_window_tokens: int
    model_max_output_tokens: int
    safety_margin_tokens: int
    evidence_output_base_tokens: int
    evidence_output_tokens_per_card: int
    evidence_max_output_tokens: int
    large_paper_evidence_batch_max_cards: int
    section_summary_max_output_tokens: int
    paper_synthesis_max_output_tokens: int
    evidence_claim_batch_size: int
    evidence_claim_output_base_tokens: int
    evidence_claim_output_tokens_per_unit: int
    evidence_claim_support_max_output_tokens: int
    evidence_revision_max_output_tokens: int
    statement_revision_max_output_tokens: int
    statement_role_max_output_tokens: int
    statement_support_max_output_tokens: int
    thinking: dict[str, str]

    def evidence_output_tokens(self, card_count: int) -> int:
        if isinstance(card_count, bool) or card_count < 1:
            raise ModelProfileError("card_count 必须是正整数。")
        return self.evidence_output_base_tokens + self.evidence_output_tokens_per_card * card_count

    def evidence_claim_output_tokens(self, evidence_count: int) -> int:
        if isinstance(evidence_count, bool) or evidence_count < 1:
            raise ModelProfileError("evidence_count 必须是正整数。")
        planned = (
            self.evidence_claim_output_base_tokens
            + self.evidence_claim_output_tokens_per_unit * evidence_count
        )
        return min(planned, self.evidence_claim_support_max_output_tokens)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_model_profile(path: Path) -> ModelProfile:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelProfileError(f"无法读取模型配置：{path}") from exc
    if not isinstance(payload, dict):
        raise ModelProfileError("模型配置必须是 JSON 对象。")

    expected = {field.name for field in dataclasses.fields(ModelProfile)}
    if set(payload) != expected:
        missing = sorted(expected - set(payload))
        unknown = sorted(set(payload) - expected)
        raise ModelProfileError(f"模型配置字段不完整或包含未知字段：missing={missing}, unknown={unknown}")

    try:
        profile = ModelProfile(**payload)
    except TypeError as exc:
        raise ModelProfileError("模型配置字段类型不正确。") from exc
    _validate_profile(profile)
    return profile


def _validate_profile(profile: ModelProfile) -> None:
    string_fields = (
        "schema_version",
        "profile_id",
        "provider",
        "request_model",
        "tokenizer_repo",
        "tokenizer_revision",
    )
    if any(not isinstance(getattr(profile, name), str) or not getattr(profile, name).strip() for name in string_fields):
        raise ModelProfileError("模型配置字符串字段必须是非空字符串。")
    if profile.schema_version != MODEL_PROFILE_SCHEMA_VERSION:
        raise ModelProfileError(f"不支持的模型配置版本：{profile.schema_version}")

    integer_fields = (
        "context_window_tokens",
        "model_max_output_tokens",
        "safety_margin_tokens",
        "evidence_output_base_tokens",
        "evidence_output_tokens_per_card",
        "evidence_max_output_tokens",
        "section_summary_max_output_tokens",
        "paper_synthesis_max_output_tokens",
        "evidence_claim_output_base_tokens",
        "evidence_claim_output_tokens_per_unit",
        "evidence_claim_support_max_output_tokens",
        "evidence_revision_max_output_tokens",
        "statement_revision_max_output_tokens",
        "statement_role_max_output_tokens",
        "statement_support_max_output_tokens",
    )
    for name in integer_fields:
        value = getattr(profile, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ModelProfileError("所有 Token 预算必须是正整数。")
    if (
        isinstance(profile.prompt_token_overhead, bool)
        or not isinstance(profile.prompt_token_overhead, int)
        or profile.prompt_token_overhead < 0
    ):
        raise ModelProfileError("prompt_token_overhead 必须是非负整数。")
    if isinstance(profile.evidence_claim_batch_size, bool) or not isinstance(
        profile.evidence_claim_batch_size, int
    ) or profile.evidence_claim_batch_size < 1:
        raise ModelProfileError("evidence_claim_batch_size 必须是正整数。")
    if isinstance(profile.large_paper_evidence_batch_max_cards, bool) or not isinstance(
        profile.large_paper_evidence_batch_max_cards, int
    ) or profile.large_paper_evidence_batch_max_cards < 1:
        raise ModelProfileError("large_paper_evidence_batch_max_cards 必须是正整数。")

    task_limits = (
        profile.evidence_max_output_tokens,
        profile.section_summary_max_output_tokens,
        profile.paper_synthesis_max_output_tokens,
        profile.evidence_claim_support_max_output_tokens,
        profile.evidence_revision_max_output_tokens,
        profile.statement_revision_max_output_tokens,
        profile.statement_role_max_output_tokens,
        profile.statement_support_max_output_tokens,
    )
    if any(limit > profile.model_max_output_tokens for limit in task_limits):
        raise ModelProfileError("任务输出预算超过 model_max_output_tokens。")
    if profile.safety_margin_tokens >= profile.context_window_tokens:
        raise ModelProfileError("safety_margin_tokens 必须小于 context_window_tokens。")
    if any(limit + profile.safety_margin_tokens >= profile.context_window_tokens for limit in task_limits):
        raise ModelProfileError("任务输出预算与安全余量之和必须小于上下文窗口。")
    if profile.evidence_output_tokens(1) > profile.evidence_max_output_tokens:
        raise ModelProfileError("单张 Card 的计划输出已超过 evidence_max_output_tokens。")
    if (
        profile.evidence_claim_output_base_tokens
        + profile.evidence_claim_output_tokens_per_unit
        > profile.evidence_claim_support_max_output_tokens
    ):
        raise ModelProfileError("单个 Evidence 的计划核验输出已超过上限。")
    if not re.fullmatch(r"[0-9a-f]{40}", profile.tokenizer_revision):
        raise ModelProfileError("tokenizer_revision 必须是40位小写十六进制 revision。")
    if profile.thinking != {"type": "disabled"}:
        raise ModelProfileError("当前模型配置只允许 Non-think。")
