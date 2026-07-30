from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


REVIEW_WRITING_CONFIG_SCHEMA_VERSION = "llm.review_writing_config.v1"
REVIEW_WRITING_COLLECTION_SCHEMA_VERSION = (
    "llm.review_writing_collection.v1"
)
REVIEW_CHAPTER_SCHEMA_VERSION = "llm.review_chapter.v1"
SECTION_TYPES = ("introduction", "body", "conclusion")
MACHINE_ID_PATTERN = re.compile(
    r"(?i)(?:ref|evidence|material|contribution|package|section|"
    r"chapter|paragraph|card)[_-][a-z0-9][a-z0-9_-]{5,}"
)


class ReviewWritingContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ReviewWritingConfig:
    schema_version: str
    max_paragraphs_per_section: int
    max_paragraph_chars: int
    chapter_max_output_tokens: int
    full_review_edit_max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class ReviewWritingCollection:
    schema_version: str
    framework_run_id: str
    source_package_run_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "framework_run_id": self.framework_run_id,
            "source_package_run_ids": list(self.source_package_run_ids),
        }


def load_review_writing_collection(
    path: Path,
) -> ReviewWritingCollection:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewWritingContractError(
            "collection.read_failed",
            f"无法读取综述写作集合：{path}",
        ) from exc
    if not isinstance(payload, dict):
        raise ReviewWritingContractError(
            "collection.invalid",
            "综述写作集合必须是JSON对象。",
        )
    expected = {
        "schema_version",
        "framework_run_id",
        "source_package_run_ids",
    }
    if set(payload) != expected:
        raise ReviewWritingContractError(
            "collection.fields_invalid",
            f"集合字段不匹配：missing={sorted(expected - set(payload))}, "
            f"unknown={sorted(set(payload) - expected)}",
        )
    if (
        payload["schema_version"]
        != REVIEW_WRITING_COLLECTION_SCHEMA_VERSION
    ):
        raise ReviewWritingContractError(
            "collection.schema_version_invalid",
            "综述写作集合版本不受支持。",
            path="$.schema_version",
        )
    framework_run_id = _required_text(
        payload["framework_run_id"],
        "framework_run_id",
    )
    values = payload["source_package_run_ids"]
    if not isinstance(values, list) or not values:
        raise ReviewWritingContractError(
            "collection.package_runs_invalid",
            "source_package_run_ids必须是非空数组。",
            path="$.source_package_run_ids",
        )
    package_run_ids = tuple(
        _required_text(value, f"source_package_run_ids[{index}]")
        for index, value in enumerate(values)
    )
    if len(package_run_ids) != len(set(package_run_ids)):
        raise ReviewWritingContractError(
            "collection.package_runs_duplicate",
            "source_package_run_ids不得重复。",
            path="$.source_package_run_ids",
        )
    return ReviewWritingCollection(
        schema_version=REVIEW_WRITING_COLLECTION_SCHEMA_VERSION,
        framework_run_id=framework_run_id,
        source_package_run_ids=package_run_ids,
    )


def load_review_writing_config(path: Path) -> ReviewWritingConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewWritingContractError(
            "config.read_failed",
            f"无法读取综述写作配置：{path}",
        ) from exc
    if not isinstance(payload, dict):
        raise ReviewWritingContractError(
            "config.invalid",
            "综述写作配置必须是JSON对象。",
        )
    expected = set(ReviewWritingConfig.__dataclass_fields__)
    if set(payload) != expected:
        raise ReviewWritingContractError(
            "config.fields_invalid",
            f"配置字段不匹配：missing={sorted(expected - set(payload))}, "
            f"unknown={sorted(set(payload) - expected)}",
        )
    try:
        config = ReviewWritingConfig(**payload)
    except TypeError as exc:
        raise ReviewWritingContractError(
            "config.invalid",
            "综述写作配置字段类型错误。",
        ) from exc
    _validate_config(config)
    return config


def build_review_chapter_schema(
    *,
    section_index: int,
    section_title: str,
    section_type: str,
    allowed_citation_keys: list[str],
    config: ReviewWritingConfig,
) -> dict[str, Any]:
    _validate_config(config)
    if type(section_index) is not int or section_index < 1:
        raise ValueError("section_index必须是正整数。")
    title = _required_text(section_title, "section_title")
    if section_type not in SECTION_TYPES:
        raise ValueError(
            f"section_type必须是以下值之一：{list(SECTION_TYPES)}"
        )
    citation_keys = _validated_citation_keys(allowed_citation_keys)
    minimum_paragraphs = 3 if section_type == "body" else 2
    if config.max_paragraphs_per_section < minimum_paragraphs:
        raise ReviewWritingContractError(
            "config.paragraph_limit_too_small",
            f"{section_type}章节至少需要{minimum_paragraphs}段。",
            path="$.max_paragraphs_per_section",
        )

    citation_schema: dict[str, Any] = {
        "type": "array",
        "minItems": 1 if section_type == "body" else 0,
        "maxItems": len(citation_keys),
        "uniqueItems": True,
        "items": {
            "type": "string",
            "enum": citation_keys,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": (
            "https://shipping.local/schema/"
            "llm.review_chapter.v1.json"
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "section_index",
            "title",
            "paragraphs",
        ],
        "properties": {
            "schema_version": {"const": REVIEW_CHAPTER_SCHEMA_VERSION},
            "section_index": {"const": section_index},
            "title": {"const": title},
            "paragraphs": {
                "type": "array",
                "minItems": minimum_paragraphs,
                "maxItems": config.max_paragraphs_per_section,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "paragraph_index",
                        "text",
                        "citation_keys",
                    ],
                    "properties": {
                        "paragraph_index": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": config.max_paragraphs_per_section,
                        },
                        "text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": config.max_paragraph_chars,
                        },
                        "citation_keys": citation_schema,
                    },
                },
            },
        },
    }


def validate_review_chapter(
    payload: object,
    schema: dict[str, Any],
) -> dict[str, Any]:
    validated = _validate_schema(
        payload,
        schema,
        "schema.review_chapter_invalid",
    )
    paragraphs = validated["paragraphs"]
    actual_indexes = [row["paragraph_index"] for row in paragraphs]
    expected_indexes = list(range(1, len(paragraphs) + 1))
    if actual_indexes != expected_indexes:
        raise ReviewWritingContractError(
            "chapter.paragraph_indexes_invalid",
            f"段落序号必须从1连续递增：expected={expected_indexes}, "
            f"actual={actual_indexes}",
            path="$.paragraphs",
        )

    for index, paragraph in enumerate(paragraphs):
        text = paragraph["text"].strip()
        if not text:
            raise ReviewWritingContractError(
                "chapter.paragraph_text_empty",
                "段落正文不得仅包含空白字符。",
                path=f"$.paragraphs[{index}].text",
            )
        forbidden_keys = [
            key
            for key in schema["properties"]["paragraphs"]["items"][
                "properties"
            ]["citation_keys"]["items"]["enum"]
            if key in text
        ]
        machine_id = MACHINE_ID_PATTERN.search(text)
        if forbidden_keys or machine_id:
            found = (
                forbidden_keys[0]
                if forbidden_keys
                else machine_id.group(0)
            )
            raise ReviewWritingContractError(
                "chapter.machine_id_in_text",
                f"正文text不得包含引用Key或机器ID：{found!r}。",
                path=f"$.paragraphs[{index}].text",
            )
    if not any(row["citation_keys"] for row in paragraphs):
        raise ReviewWritingContractError(
            "chapter.citations_missing",
            "章节至少必须引用一条本知识包参考文献。",
            path="$.paragraphs",
        )

    result = copy.deepcopy(validated)
    chapter_id = _stable_id(
        "chapter",
        {
            "schema_version": result["schema_version"],
            "section_index": result["section_index"],
            "title": result["title"],
            "paragraphs": result["paragraphs"],
        },
    )
    result["chapter_id"] = chapter_id
    for paragraph in result["paragraphs"]:
        paragraph["paragraph_id"] = _stable_id(
            "paragraph",
            {
                "chapter_id": chapter_id,
                "paragraph_index": paragraph["paragraph_index"],
                "text": paragraph["text"],
                "citation_keys": paragraph["citation_keys"],
            },
        )
    return result


def _validate_config(config: ReviewWritingConfig) -> None:
    if not isinstance(config, ReviewWritingConfig):
        raise ReviewWritingContractError(
            "config.invalid",
            "必须提供ReviewWritingConfig实例。",
        )
    if config.schema_version != REVIEW_WRITING_CONFIG_SCHEMA_VERSION:
        raise ReviewWritingContractError(
            "config.schema_version_invalid",
            "综述写作配置版本不受支持。",
            path="$.schema_version",
        )
    for field in config.__dataclass_fields__:
        if field == "schema_version":
            continue
        value = getattr(config, field)
        if type(value) is not int or value < 1:
            raise ReviewWritingContractError(
                "config.value_invalid",
                f"{field}必须是正整数。",
                path=f"$.{field}",
            )


def _validated_citation_keys(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("allowed_citation_keys必须是非空数组。")
    result: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"allowed_citation_keys[{index}]必须是非空字符串。"
            )
        result.append(value.strip())
    if len(result) != len(set(result)):
        raise ValueError("allowed_citation_keys不得重复。")
    return result


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}必须是非空字符串。")
    return value.strip()


def _stable_id(prefix: str, payload: object) -> str:
    compact = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _validate_schema(
    payload: object,
    schema: dict[str, Any],
    code: str,
) -> dict[str, Any]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        raise ReviewWritingContractError(
            code,
            error.message,
            path=path,
        )
    if not isinstance(payload, dict):
        raise ReviewWritingContractError(
            code,
            "章节输出必须是JSON对象。",
        )
    return copy.deepcopy(payload)
