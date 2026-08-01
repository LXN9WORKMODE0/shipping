from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .source_window_contracts import stable_id


DECISION_FILE_SCHEMA_VERSION = "llm.review_writing_b2_revision_decisions.v1"
REVISION_CHAPTER_SCHEMA_VERSION = "llm.review_writing_b2_revision.v1"
SENTENCE_ENDINGS = ("。", "！", "？", "!", "?")
USER_CONFIRMED_DELETE = "user_confirmed_delete"
ADJUDICATION_CONFIRMED_DELETE = "adjudication_confirmed_delete"


class ReviewWritingB2RevisionContractError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


def load_revision_decision_set(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.decision_read_failed",
            f"无法读取修订决策文件：{path}",
        ) from exc
    return validate_revision_decision_set(payload)


def validate_revision_decision_set(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.decision_invalid", "修订决策必须是对象。"
        )
    required = {
        "schema_version",
        "decision_basis",
        "confirmed_on",
        "decisions",
    }
    if set(payload) not in (required, required | {"decision_set_id"}):
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.decision_fields_invalid",
            "修订决策字段集合不匹配。",
        )
    if payload["schema_version"] != DECISION_FILE_SCHEMA_VERSION:
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.decision_schema_invalid",
            "修订决策Schema版本不受支持。",
        )
    if payload["decision_basis"] not in {
        "user_confirmed_recommendation_set",
        "adjudication_exact_removal",
    }:
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.decision_basis_invalid",
            "修订决策依据不受支持。",
        )
    if not isinstance(payload["confirmed_on"], str) or not payload[
        "confirmed_on"
    ].strip():
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.confirmed_on_invalid",
            "confirmed_on必须是非空字符串。",
        )
    decisions = payload["decisions"]
    if not isinstance(decisions, list) or not decisions:
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.decisions_empty",
            "修订决策不得为空。",
        )
    run_ids: list[str] = []
    normalized_decisions = []
    for index, row in enumerate(decisions):
        path = f"$.decisions[{index}]"
        if not isinstance(row, dict) or set(row) != {
            "revision_suggestion_run_id",
            "selections",
        }:
            raise ReviewWritingB2RevisionContractError(
                "writing_revision_b2.decision_entry_invalid",
                "章节决策字段集合不匹配。",
                path=path,
            )
        run_id = row["revision_suggestion_run_id"]
        if not isinstance(run_id, str) or not run_id.strip():
            raise ReviewWritingB2RevisionContractError(
                "writing_revision_b2.suggestion_run_id_invalid",
                "revision_suggestion_run_id必须非空。",
                path=f"{path}.revision_suggestion_run_id",
            )
        run_ids.append(run_id)
        selections = row["selections"]
        if not isinstance(selections, list) or not selections:
            raise ReviewWritingB2RevisionContractError(
                "writing_revision_b2.selections_empty",
                "章节选择不得为空。",
                path=f"{path}.selections",
            )
        normalized_selections = []
        for selection_index, selection in enumerate(selections):
            selection_path = f"{path}.selections[{selection_index}]"
            if not isinstance(selection, dict) or set(selection) != {
                "sentence_id",
                "option_id",
            }:
                raise ReviewWritingB2RevisionContractError(
                    "writing_revision_b2.selection_invalid",
                    "句子选择字段集合不匹配。",
                    path=selection_path,
                )
            if any(
                not isinstance(selection[field], str)
                or not selection[field].strip()
                for field in ("sentence_id", "option_id")
            ):
                raise ReviewWritingB2RevisionContractError(
                    "writing_revision_b2.selection_id_invalid",
                    "sentence_id和option_id必须非空。",
                    path=selection_path,
                )
            normalized_selections.append(copy.deepcopy(selection))
        sentence_ids = [row["sentence_id"] for row in normalized_selections]
        if len(sentence_ids) != len(set(sentence_ids)):
            raise ReviewWritingB2RevisionContractError(
                "writing_revision_b2.sentence_selected_twice",
                "同一风险句不得重复选择。",
                path=f"{path}.selections",
            )
        normalized_decisions.append(
            {
                "revision_suggestion_run_id": run_id,
                "selections": normalized_selections,
            }
        )
    if len(run_ids) != len(set(run_ids)):
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.suggestion_run_duplicated",
            "同一修订建议运行不得重复出现。",
        )
    value_without_id = {
        "schema_version": DECISION_FILE_SCHEMA_VERSION,
        "decision_basis": payload["decision_basis"],
        "confirmed_on": payload["confirmed_on"].strip(),
        "decisions": normalized_decisions,
    }
    value = {
        **value_without_id,
        "decision_set_id": stable_id(
            "review_writing_revision_decisions_b2", value_without_id
        ),
    }
    if payload.get("decision_set_id") not in (None, value["decision_set_id"]):
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.decision_id_invalid",
            "decision_set_id与决策内容不一致。",
        )
    return value


def apply_revision_decision(
    *,
    decision_set: dict[str, Any],
    suggestion_run_id: str,
    suggestion_manifest_sha256: str,
    revision_input: dict[str, Any],
    suggestions: dict[str, Any],
    source_chapter: dict[str, Any],
    source_audit_input: dict[str, Any],
) -> dict[str, Any]:
    matches = [
        row
        for row in decision_set["decisions"]
        if row["revision_suggestion_run_id"] == suggestion_run_id
    ]
    if len(matches) != 1:
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.decision_not_found",
            "决策集中无法唯一定位当前修订建议运行。",
        )
    decision = matches[0]
    suggestion_by_sentence = {
        row["sentence_id"]: row for row in suggestions["suggestions"]
    }
    expected_ids = list(suggestion_by_sentence)
    actual_ids = [row["sentence_id"] for row in decision["selections"]]
    if actual_ids != expected_ids:
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.selection_coverage_invalid",
            "决策必须按建议输出顺序覆盖全部风险句。",
        )
    selection_by_sentence: dict[str, dict[str, Any]] = {}
    for index, selection in enumerate(decision["selections"]):
        suggestion = suggestion_by_sentence[selection["sentence_id"]]
        if selection["option_id"] in {
            USER_CONFIRMED_DELETE,
            ADJUDICATION_CONFIRMED_DELETE,
        }:
            option_type = selection["option_id"]
            rationale = (
                "用户确认删除该风险句。"
                if option_type == USER_CONFIRMED_DELETE
                else "独立裁决确认计划外内容后，按最小收束原则删除该句。"
            )
            option = {
                "option_id": option_type,
                "option_type": option_type,
                "operation": "delete",
                "replacement_sentences": [],
                "retained_claim_ids": [],
                "citation_keys": [],
                "rationale": rationale,
            }
        else:
            options = [
                option
                for option in suggestion["revision_options"]
                if option["option_id"] == selection["option_id"]
            ]
            if len(options) != 1:
                raise ReviewWritingB2RevisionContractError(
                    "writing_revision_b2.option_not_found",
                    "option_id不属于对应风险句，且不是显式用户删除动作。",
                    path=f"$.decisions.selections[{index}].option_id",
                )
            option = copy.deepcopy(options[0])
        for replacement in option["replacement_sentences"]:
            if not replacement.rstrip().endswith(SENTENCE_ENDINGS):
                raise ReviewWritingB2RevisionContractError(
                    "writing_revision_b2.replacement_not_sentence",
                    "替换文本必须以句末标点结束。",
                    path=f"$.decisions.selections[{index}]",
                )
        selection_by_sentence[selection["sentence_id"]] = option

    catalogue_by_paragraph: dict[str, list[dict[str, Any]]] = {}
    for sentence in source_audit_input["sentences"]:
        catalogue_by_paragraph.setdefault(
            sentence["paragraph_id"], []
        ).append(sentence)
    new_paragraphs = []
    changes = []
    for paragraph in source_chapter["paragraphs"]:
        catalogue = sorted(
            catalogue_by_paragraph.get(paragraph["paragraph_id"], []),
            key=lambda row: row["sentence_index"],
        )
        reconstructed = "".join(row["text"] for row in catalogue)
        if reconstructed != paragraph["text"]:
            raise ReviewWritingB2RevisionContractError(
                "writing_revision_b2.paragraph_replay_mismatch",
                f"第{paragraph['paragraph_index']}段无法由冻结句子目录无损重建。",
            )
        revised_sentences: list[str] = []
        for sentence in catalogue:
            option = selection_by_sentence.get(sentence["sentence_id"])
            if option is None:
                revised_sentences.append(sentence["text"])
                continue
            replacements = copy.deepcopy(option["replacement_sentences"])
            revised_sentences.extend(replacements)
            changes.append(
                {
                    "sentence_id": sentence["sentence_id"],
                    "paragraph_index": sentence["paragraph_index"],
                    "sentence_index": sentence["sentence_index"],
                    "original_text": sentence["text"],
                    "option_id": option["option_id"],
                    "option_type": option["option_type"],
                    "operation": option["operation"],
                    "replacement_sentences": replacements,
                    "retained_claim_ids": copy.deepcopy(
                        option["retained_claim_ids"]
                    ),
                    "citation_keys": copy.deepcopy(option["citation_keys"]),
                    "rationale": option["rationale"],
                }
            )
        revised_text = "".join(revised_sentences).strip()
        if not revised_text:
            raise ReviewWritingB2RevisionContractError(
                "writing_revision_b2.paragraph_empty",
                f"第{paragraph['paragraph_index']}段应用修订后为空。",
            )
        new_paragraphs.append(
            {
                "paragraph_index": paragraph["paragraph_index"],
                "text": revised_text,
                "implemented_claim_ids": copy.deepcopy(
                    paragraph["implemented_claim_ids"]
                ),
                "citation_keys": copy.deepcopy(paragraph["citation_keys"]),
            }
        )
    if len(changes) != len(expected_ids):
        raise ReviewWritingB2RevisionContractError(
            "writing_revision_b2.change_coverage_invalid",
            "应用变更数与已确认风险句数不一致。",
        )
    chapter_without_ids = {
        "schema_version": REVISION_CHAPTER_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase3e",
        "writing_input_id": source_chapter["writing_input_id"],
        "source_writing_run_id": revision_input["source"]["writing_run_id"],
        "source_chapter_id": source_chapter["chapter_id"],
        "revision_suggestion_run_id": suggestion_run_id,
        "revision_suggestion_manifest_sha256": suggestion_manifest_sha256,
        "revision_suggestion_id": suggestions["revision_suggestion_id"],
        "decision_set_id": decision_set["decision_set_id"],
        "section_id": source_chapter["section_id"],
        "section_index": source_chapter["section_index"],
        "title": source_chapter["title"],
        "paragraphs": new_paragraphs,
        "revision_audit": {
            "selected_sentence_count": len(changes),
            "deleted_sentence_count": sum(
                row["operation"] == "delete" for row in changes
            ),
            "replaced_sentence_count": sum(
                row["operation"] == "replace" for row in changes
            ),
            "split_sentence_count": sum(
                row["operation"] == "split" for row in changes
            ),
            "changes": changes,
            "semantic_claim_audit": "pending",
        },
    }
    chapter_id = stable_id("chapter_b2_revision", chapter_without_ids)
    chapter = {**chapter_without_ids, "chapter_id": chapter_id}
    for paragraph in chapter["paragraphs"]:
        paragraph["paragraph_id"] = stable_id(
            "paragraph_b2",
            {
                "chapter_id": chapter_id,
                "paragraph_index": paragraph["paragraph_index"],
                "text": paragraph["text"],
                "implemented_claim_ids": paragraph[
                    "implemented_claim_ids"
                ],
                "citation_keys": paragraph["citation_keys"],
            },
        )
    return chapter
