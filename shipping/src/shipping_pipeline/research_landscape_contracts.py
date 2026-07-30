from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


RESEARCH_LANDSCAPE_CONFIG_SCHEMA_VERSION = (
    "llm.research_landscape_config.v1"
)
RESEARCH_LANDSCAPE_COLLECTION_SCHEMA_VERSION = (
    "llm.research_landscape_collection.v1"
)
RESEARCH_LANDSCAPE_SCHEMA_VERSION = "llm.research_landscape.v1"
CORPUS_SCOPES = ("targeted_sample", "systematic_corpus")
RELATION_TYPES = (
    "converges",
    "complements",
    "contrasts",
    "scope_difference",
    "methodological_alternative",
)
EVOLUTION_CAUSAL_PHRASES = (
    "奠定基础",
    "奠定框架",
    "推动了",
    "促进了",
    "继承",
    "发展为",
    "演变为",
)


class ResearchLandscapeContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class ResearchLandscapeConfig:
    schema_version: str
    max_dimensions: int
    max_relations: int
    max_evolution_items: int
    max_disagreements: int
    max_positions_per_disagreement: int
    max_corpus_gaps: int
    max_field_gap_candidates: int
    landscape_max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


def load_research_landscape_config(path: Path) -> ResearchLandscapeConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchLandscapeContractError(
            "config.read_failed",
            f"无法读取研究图谱配置：{path}",
        ) from exc
    if not isinstance(payload, dict):
        raise ResearchLandscapeContractError(
            "config.invalid",
            "研究图谱配置必须是JSON对象。",
        )
    expected = set(ResearchLandscapeConfig.__dataclass_fields__)
    if set(payload) != expected:
        raise ResearchLandscapeContractError(
            "config.fields_invalid",
            f"配置字段不匹配：missing={sorted(expected - set(payload))}, "
            f"unknown={sorted(set(payload) - expected)}",
        )
    try:
        config = ResearchLandscapeConfig(**payload)
    except TypeError as exc:
        raise ResearchLandscapeContractError(
            "config.invalid",
            "研究图谱配置字段类型错误。",
        ) from exc
    if config.schema_version != RESEARCH_LANDSCAPE_CONFIG_SCHEMA_VERSION:
        raise ResearchLandscapeContractError(
            "config.schema_version_invalid",
            "研究图谱配置版本不受支持。",
        )
    for field in config.__dataclass_fields__:
        if field == "schema_version":
            continue
        value = getattr(config, field)
        if type(value) is not int or value < 1:
            raise ResearchLandscapeContractError(
                "config.value_invalid",
                f"{field}必须是正整数。",
                path=f"$.{field}",
            )
    return config


def validate_research_landscape_collection(
    payload: object,
) -> dict[str, Any]:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "topic",
            "review_goal",
            "corpus_scope",
            "source_understanding_run_ids",
        ],
        "properties": {
            "schema_version": {
                "const": RESEARCH_LANDSCAPE_COLLECTION_SCHEMA_VERSION
            },
            "topic": {"type": "string", "minLength": 1, "maxLength": 1000},
            "review_goal": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
            },
            "corpus_scope": {
                "type": "string",
                "enum": list(CORPUS_SCOPES),
            },
            "source_understanding_run_ids": {
                "type": "array",
                "minItems": 2,
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 240,
                },
            },
        },
    }
    validated = _validate_schema(
        payload,
        schema,
        "schema.research_landscape_collection_invalid",
    )
    run_ids = [
        str(value) for value in validated["source_understanding_run_ids"]
    ]
    if len(run_ids) != len(set(run_ids)):
        raise ResearchLandscapeContractError(
            "collection.duplicate_source_run",
            "source_understanding_run_ids不得重复。",
            path="$.source_understanding_run_ids",
        )
    invalid = [
        value
        for value in run_ids
        if value in {".", ".."} or "/" in value or "\\" in value
    ]
    if invalid:
        raise ResearchLandscapeContractError(
            "collection.source_run_id_invalid",
            f"来源运行ID必须是单个目录名：{invalid}",
            path="$.source_understanding_run_ids",
        )
    return validated


def build_research_landscape_schema(
    *,
    topic: str,
    review_goal: str,
    paper_ids: list[str],
    contribution_to_paper: dict[str, str],
    corpus_scope: str,
    config: ResearchLandscapeConfig,
) -> dict[str, Any]:
    papers = _validated_ids(paper_ids, "paper_ids")
    contributions = _validated_ids(
        list(contribution_to_paper),
        "contribution_ids",
    )
    if set(contribution_to_paper.values()) - set(papers):
        raise ValueError("contribution_to_paper包含未知paper_id。")
    if corpus_scope not in CORPUS_SCOPES:
        raise ValueError(f"corpus_scope不受支持：{corpus_scope!r}。")
    _required_text(topic, "topic")
    _required_text(review_goal, "review_goal")
    text = {"type": "string", "minLength": 1, "maxLength": 800}
    paper_refs = {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"type": "string", "enum": papers},
    }
    contribution_refs = {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"type": "string", "enum": contributions},
    }
    position = {
        "type": "object",
        "additionalProperties": False,
        "required": ["paper_ids", "statement", "contribution_ids"],
        "properties": {
            "paper_ids": paper_refs,
            "statement": text,
            "contribution_ids": contribution_refs,
        },
    }
    properties: dict[str, Any] = {
        "schema_version": {"const": RESEARCH_LANDSCAPE_SCHEMA_VERSION},
        "topic": {"const": topic},
        "review_goal": {"const": review_goal},
        "corpus_scope": {"const": corpus_scope},
        "central_problem": text,
        "dimensions": {
            "type": "array",
            "minItems": 1,
            "maxItems": config.max_dimensions,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "dimension_index",
                    "title",
                    "question",
                    "paper_ids",
                    "contribution_ids",
                ],
                "properties": {
                    "dimension_index": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": config.max_dimensions,
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 160,
                    },
                    "question": text,
                    "paper_ids": paper_refs,
                    "contribution_ids": contribution_refs,
                },
            },
        },
        "relations": {
            "type": "array",
            "maxItems": config.max_relations,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "relation_type",
                    "from_paper_id",
                    "to_paper_id",
                    "statement",
                    "supporting_contribution_ids",
                ],
                "properties": {
                    "relation_type": {
                        "type": "string",
                        "enum": list(RELATION_TYPES),
                    },
                    "from_paper_id": {"type": "string", "enum": papers},
                    "to_paper_id": {"type": "string", "enum": papers},
                    "statement": text,
                    "supporting_contribution_ids": {
                        **contribution_refs,
                        "minItems": 2,
                    },
                },
            },
        },
        "research_evolution": {
            "type": "array",
            "maxItems": config.max_evolution_items,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "period",
                    "statement",
                    "paper_ids",
                    "contribution_ids",
                ],
                "properties": {
                    "period": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 160,
                    },
                    "statement": text,
                    "paper_ids": paper_refs,
                    "contribution_ids": contribution_refs,
                },
            },
        },
        "disagreements": {
            "type": "array",
            "maxItems": config.max_disagreements,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question", "positions"],
                "properties": {
                    "question": text,
                    "positions": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": config.max_positions_per_disagreement,
                        "items": position,
                    },
                },
            },
        },
        "corpus_gaps": {
            "type": "array",
            "maxItems": config.max_corpus_gaps,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "question",
                    "why_it_matters",
                    "related_dimension_indexes",
                ],
                "properties": {
                    "question": text,
                    "why_it_matters": text,
                    "related_dimension_indexes": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": config.max_dimensions,
                        },
                    },
                },
            },
        },
        "unmapped_papers": {
            "type": "array",
            "uniqueItems": True,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["paper_id", "reason"],
                "properties": {
                    "paper_id": {"type": "string", "enum": papers},
                    "reason": text,
                },
            },
        },
    }
    required = list(properties)
    if corpus_scope == "systematic_corpus":
        properties["field_gap_candidates"] = {
            "type": "array",
            "maxItems": config.max_field_gap_candidates,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "question",
                    "why_it_matters",
                    "supporting_corpus_gap_indexes",
                    "caveat",
                ],
                "properties": {
                    "question": text,
                    "why_it_matters": text,
                    "supporting_corpus_gap_indexes": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": config.max_corpus_gaps,
                        },
                    },
                    "caveat": text,
                },
            },
        }
        required.append("field_gap_candidates")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://shipping.local/schema/llm.research_landscape.v1.json",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def build_research_landscape_generation_schema(
    schema: dict[str, Any],
    *,
    contribution_to_paper: dict[str, str],
) -> dict[str, Any]:
    generation_schema = copy.deepcopy(schema)
    contributions_by_paper: dict[str, list[str]] = {}
    for contribution_id, paper_id in contribution_to_paper.items():
        contributions_by_paper.setdefault(paper_id, []).append(
            contribution_id
        )
    for contribution_ids in contributions_by_paper.values():
        contribution_ids.sort()

    ownership_rules = _paper_list_ownership_rules(
        contributions_by_paper,
        paper_field="paper_ids",
        contribution_field="contribution_ids",
    )
    properties = generation_schema["properties"]
    properties["dimensions"]["items"]["allOf"] = copy.deepcopy(
        ownership_rules
    )
    properties["research_evolution"]["items"]["allOf"] = copy.deepcopy(
        ownership_rules
    )
    properties["disagreements"]["items"]["properties"]["positions"][
        "items"
    ]["allOf"] = copy.deepcopy(ownership_rules)
    properties["relations"]["items"]["allOf"] = (
        _relation_ownership_rules(contributions_by_paper)
    )
    return generation_schema


def _paper_list_ownership_rules(
    contributions_by_paper: dict[str, list[str]],
    *,
    paper_field: str,
    contribution_field: str,
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for paper_id, contribution_ids in sorted(
        contributions_by_paper.items()
    ):
        rules.append(
            {
                "if": {
                    "properties": {
                        paper_field: {"contains": {"const": paper_id}}
                    }
                },
                "then": {
                    "properties": {
                        contribution_field: {
                            "contains": {"enum": contribution_ids}
                        }
                    }
                },
            }
        )
        rules.append(
            {
                "if": {
                    "properties": {
                        contribution_field: {
                            "contains": {"enum": contribution_ids}
                        }
                    }
                },
                "then": {
                    "properties": {
                        paper_field: {"contains": {"const": paper_id}}
                    }
                },
            }
        )
    return rules


def _relation_ownership_rules(
    contributions_by_paper: dict[str, list[str]],
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for paper_id, contribution_ids in sorted(
        contributions_by_paper.items()
    ):
        contribution_requirement = {
            "properties": {
                "supporting_contribution_ids": {
                    "contains": {"enum": contribution_ids}
                }
            }
        }
        for paper_field in ("from_paper_id", "to_paper_id"):
            rules.append(
                {
                    "if": {
                        "properties": {
                            paper_field: {"const": paper_id}
                        }
                    },
                    "then": copy.deepcopy(contribution_requirement),
                }
            )
        rules.append(
            {
                "if": contribution_requirement,
                "then": {
                    "anyOf": [
                        {
                            "properties": {
                                "from_paper_id": {"const": paper_id}
                            }
                        },
                        {
                            "properties": {
                                "to_paper_id": {"const": paper_id}
                            }
                        },
                    ]
                },
            }
        )
    return rules


def validate_research_landscape(
    payload: object,
    *,
    schema: dict[str, Any],
    contribution_to_paper: dict[str, str],
) -> dict[str, Any]:
    validated = _validate_schema(
        payload,
        schema,
        "schema.research_landscape_invalid",
    )
    paper_ids = set(
        schema["properties"]["dimensions"]["items"]["properties"]["paper_ids"][
            "items"
        ]["enum"]
    )
    dimension_indexes = [
        int(row["dimension_index"]) for row in validated["dimensions"]
    ]
    expected_indexes = list(range(1, len(dimension_indexes) + 1))
    if dimension_indexes != expected_indexes:
        raise ResearchLandscapeContractError(
            "landscape.dimension_indexes_invalid",
            f"dimension_index必须按输出顺序连续编号：expected={expected_indexes}, "
            f"actual={dimension_indexes}",
            path="$.dimensions",
        )
    for index, dimension in enumerate(validated["dimensions"]):
        _validate_owned_contributions(
            paper_ids=set(dimension["paper_ids"]),
            contribution_ids=dimension["contribution_ids"],
            contribution_to_paper=contribution_to_paper,
            path=f"$.dimensions[{index}]",
            require_every_paper=True,
        )

    dimensioned = {
        str(paper_id)
        for dimension in validated["dimensions"]
        for paper_id in dimension["paper_ids"]
    }
    unmapped_ids = [
        str(row["paper_id"]) for row in validated["unmapped_papers"]
    ]
    if len(unmapped_ids) != len(set(unmapped_ids)):
        raise ResearchLandscapeContractError(
            "landscape.unmapped_paper_duplicate",
            "unmapped_papers中paper_id不得重复。",
            path="$.unmapped_papers",
        )
    overlap = sorted(dimensioned & set(unmapped_ids))
    if overlap:
        raise ResearchLandscapeContractError(
            "landscape.paper_coverage_overlap",
            f"论文不能同时进入维度和unmapped_papers：{overlap}",
        )
    missing = sorted(paper_ids - dimensioned - set(unmapped_ids))
    if missing:
        raise ResearchLandscapeContractError(
            "landscape.paper_coverage_incomplete",
            f"论文未进入任何维度且未显式unmapped：{missing}",
        )

    relation_keys: set[tuple[str, str, str]] = set()
    for index, relation in enumerate(validated["relations"]):
        source_paper = str(relation["from_paper_id"])
        target_paper = str(relation["to_paper_id"])
        if source_paper == target_paper:
            raise ResearchLandscapeContractError(
                "landscape.relation_same_paper",
                "relation必须涉及两个不同paper_id。",
                path=f"$.relations[{index}]",
            )
        _validate_owned_contributions(
            paper_ids={source_paper, target_paper},
            contribution_ids=relation["supporting_contribution_ids"],
            contribution_to_paper=contribution_to_paper,
            path=f"$.relations[{index}]",
            require_every_paper=True,
            incomplete_code="landscape.relation_source_incomplete",
        )
        key = (
            str(relation["relation_type"]),
            *sorted((source_paper, target_paper)),
        )
        if key in relation_keys:
            raise ResearchLandscapeContractError(
                "landscape.relation_duplicate",
                "同一论文对和关系类型不得重复。",
                path=f"$.relations[{index}]",
            )
        relation_keys.add(key)

    for index, evolution in enumerate(validated["research_evolution"]):
        causal_phrases = [
            phrase
            for phrase in EVOLUTION_CAUSAL_PHRASES
            if phrase in str(evolution["statement"])
        ]
        if causal_phrases:
            raise ResearchLandscapeContractError(
                "landscape.evolution_causal_claim",
                "研究演化只能描述时间顺序和关注变化，不能使用未证明的"
                f"因果传承措辞：{causal_phrases}",
                path=f"$.research_evolution[{index}].statement",
            )
        _validate_owned_contributions(
            paper_ids=set(evolution["paper_ids"]),
            contribution_ids=evolution["contribution_ids"],
            contribution_to_paper=contribution_to_paper,
            path=f"$.research_evolution[{index}]",
            require_every_paper=True,
        )

    for disagreement_index, disagreement in enumerate(
        validated["disagreements"]
    ):
        seen_papers: set[str] = set()
        for position_index, position in enumerate(disagreement["positions"]):
            position_papers = set(position["paper_ids"])
            overlap = sorted(seen_papers & position_papers)
            if overlap:
                raise ResearchLandscapeContractError(
                    "landscape.disagreement_paper_overlap",
                    f"同一论文不能出现在分歧的多个立场中：{overlap}",
                    path=(
                        f"$.disagreements[{disagreement_index}]"
                        f".positions[{position_index}]"
                    ),
                )
            seen_papers.update(position_papers)
            _validate_owned_contributions(
                paper_ids=position_papers,
                contribution_ids=position["contribution_ids"],
                contribution_to_paper=contribution_to_paper,
                path=(
                    f"$.disagreements[{disagreement_index}]"
                    f".positions[{position_index}]"
                ),
                require_every_paper=True,
            )

    known_dimension_indexes = set(dimension_indexes)
    for index, gap in enumerate(validated["corpus_gaps"]):
        unknown = sorted(
            set(gap["related_dimension_indexes"]) - known_dimension_indexes
        )
        if unknown:
            raise ResearchLandscapeContractError(
                "landscape.corpus_gap_dimension_unknown",
                f"语料缺口引用未知维度序号：{unknown}",
                path=f"$.corpus_gaps[{index}].related_dimension_indexes",
            )

    result = copy.deepcopy(validated)
    result["landscape_id"] = _stable_id(
        "landscape",
        {
            "topic": result["topic"],
            "review_goal": result["review_goal"],
            "dimensions": result["dimensions"],
            "relations": result["relations"],
        },
    )
    _add_item_ids(
        result["dimensions"],
        prefix="dimension",
        field="dimension_id",
    )
    _add_item_ids(result["relations"], prefix="relation", field="relation_id")
    _add_item_ids(
        result["research_evolution"],
        prefix="evolution",
        field="evolution_id",
    )
    _add_item_ids(
        result["disagreements"],
        prefix="disagreement",
        field="disagreement_id",
    )
    _add_item_ids(
        result["corpus_gaps"],
        prefix="corpus_gap",
        field="corpus_gap_id",
    )
    if "field_gap_candidates" in result:
        _add_item_ids(
            result["field_gap_candidates"],
            prefix="field_gap_candidate",
            field="field_gap_candidate_id",
        )
    return result


def _validate_owned_contributions(
    *,
    paper_ids: set[str],
    contribution_ids: list[str],
    contribution_to_paper: dict[str, str],
    path: str,
    require_every_paper: bool,
    incomplete_code: str = "landscape.contribution_source_incomplete",
) -> None:
    unknown = sorted(
        contribution_id
        for contribution_id in contribution_ids
        if contribution_id not in contribution_to_paper
    )
    if unknown:
        raise ResearchLandscapeContractError(
            "landscape.contribution_unknown",
            f"引用未知contribution_id：{unknown}",
            path=path,
        )
    owners = {
        contribution_to_paper[contribution_id]
        for contribution_id in contribution_ids
    }
    outside = sorted(owners - paper_ids)
    if outside:
        raise ResearchLandscapeContractError(
            "landscape.contribution_owner_mismatch",
            f"贡献所属论文不在当前paper_ids中：{outside}",
            path=path,
        )
    missing = sorted(paper_ids - owners)
    if require_every_paper and missing:
        raise ResearchLandscapeContractError(
            incomplete_code,
            f"以下论文没有对应贡献来源：{missing}",
            path=path,
        )


def _add_item_ids(
    rows: list[dict[str, Any]],
    *,
    prefix: str,
    field: str,
) -> None:
    for row in rows:
        row[field] = _stable_id(prefix, row)


def _stable_id(prefix: str, payload: object) -> str:
    compact = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _validated_ids(values: list[str], field: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field}必须是非空字符串数组。")
    result: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field}[{index}]必须是非空字符串。")
        result.append(value.strip())
    if len(result) != len(set(result)):
        raise ValueError(f"{field}不得重复。")
    return result


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}必须是非空字符串。")
    return value.strip()


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
        raise ResearchLandscapeContractError(code, error.message, path=path)
    if not isinstance(payload, dict):
        raise ResearchLandscapeContractError(
            code,
            "Research Landscape输出必须是JSON对象。",
        )
    return copy.deepcopy(payload)
