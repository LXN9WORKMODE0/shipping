from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.review_writing_contracts import (
    REVIEW_CHAPTER_SCHEMA_VERSION,
    REVIEW_WRITING_CONFIG_SCHEMA_VERSION,
    ReviewWritingConfig,
    ReviewWritingContractError,
    build_review_chapter_schema,
    load_review_writing_config,
    validate_review_chapter,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReviewWritingContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_review_writing_config(
            PROJECT_ROOT / "config" / "review-writing-default.json"
        )
        self.allowed_keys = ["ref_alpha", "ref_beta"]
        self.body_schema = self._schema("body")

    def test_valid_body_derives_stable_program_ids(self):
        Draft202012Validator.check_schema(self.body_schema)
        payload = self._payload("body")

        first = validate_review_chapter(payload, self.body_schema)
        second = validate_review_chapter(
            copy.deepcopy(payload),
            copy.deepcopy(self.body_schema),
        )

        self.assertEqual(
            first["schema_version"],
            REVIEW_CHAPTER_SCHEMA_VERSION,
        )
        self.assertTrue(first["chapter_id"].startswith("chapter_"))
        self.assertEqual(first["chapter_id"], second["chapter_id"])
        self.assertEqual(
            [row["paragraph_id"] for row in first["paragraphs"]],
            [row["paragraph_id"] for row in second["paragraphs"]],
        )
        self.assertTrue(
            all(
                row["paragraph_id"].startswith("paragraph_")
                for row in first["paragraphs"]
            )
        )

    def test_unknown_and_duplicate_citation_keys_are_rejected(self):
        unknown = self._payload("body")
        unknown["paragraphs"][0]["citation_keys"] = ["ref_unknown"]
        with self.assertRaisesRegex(
            ReviewWritingContractError,
            "schema.review_chapter_invalid",
        ):
            validate_review_chapter(unknown, self.body_schema)

        duplicate = self._payload("body")
        duplicate["paragraphs"][0]["citation_keys"] = [
            "ref_alpha",
            "ref_alpha",
        ]
        with self.assertRaisesRegex(
            ReviewWritingContractError,
            "schema.review_chapter_invalid",
        ):
            validate_review_chapter(duplicate, self.body_schema)

    def test_paragraph_indexes_must_be_continuous_and_ordered(self):
        payload = self._payload("body")
        payload["paragraphs"][1]["paragraph_index"] = 3
        payload["paragraphs"][2]["paragraph_index"] = 2

        with self.assertRaises(
            ReviewWritingContractError,
        ) as raised:
            validate_review_chapter(payload, self.body_schema)

        self.assertEqual(
            raised.exception.code,
            "chapter.paragraph_indexes_invalid",
        )

    def test_section_paragraph_minimums_are_enforced(self):
        for section_type, minimum in (
            ("introduction", 2),
            ("body", 3),
            ("conclusion", 2),
        ):
            schema = self._schema(section_type)
            payload = self._payload(section_type)
            payload["paragraphs"] = payload["paragraphs"][: minimum - 1]
            with self.subTest(section_type=section_type):
                with self.assertRaisesRegex(
                    ReviewWritingContractError,
                    "schema.review_chapter_invalid",
                ):
                    validate_review_chapter(payload, schema)

    def test_paragraph_maximum_is_enforced(self):
        config = ReviewWritingConfig(
            schema_version=REVIEW_WRITING_CONFIG_SCHEMA_VERSION,
            max_paragraphs_per_section=3,
            max_paragraph_chars=200,
            chapter_max_output_tokens=100,
            full_review_edit_max_output_tokens=100,
        )
        schema = build_review_chapter_schema(
            section_index=2,
            section_title="方法比较",
            section_type="body",
            allowed_citation_keys=self.allowed_keys,
            config=config,
        )
        payload = self._payload("body")
        payload["paragraphs"].append(
            {
                "paragraph_index": 4,
                "text": "第四段。",
                "citation_keys": ["ref_beta"],
            }
        )

        with self.assertRaisesRegex(
            ReviewWritingContractError,
            "schema.review_chapter_invalid",
        ):
            validate_review_chapter(payload, schema)

    def test_body_paragraph_without_citation_is_rejected(self):
        payload = self._payload("body")
        payload["paragraphs"][1]["citation_keys"] = []

        with self.assertRaisesRegex(
            ReviewWritingContractError,
            "schema.review_chapter_invalid",
        ):
            validate_review_chapter(payload, self.body_schema)

    def test_introduction_allows_navigation_but_requires_chapter_citation(self):
        schema = self._schema("introduction")
        payload = self._payload("introduction")
        payload["paragraphs"][0]["citation_keys"] = []
        validated = validate_review_chapter(payload, schema)
        self.assertEqual(validated["paragraphs"][0]["citation_keys"], [])

        for paragraph in payload["paragraphs"]:
            paragraph["citation_keys"] = []
        with self.assertRaises(
            ReviewWritingContractError,
        ) as raised:
            validate_review_chapter(payload, schema)
        self.assertEqual(raised.exception.code, "chapter.citations_missing")

    def test_model_generated_machine_ids_are_rejected(self):
        cases = (
            ("chapter_id", "model-chapter", None),
            ("paragraph_id", "model-paragraph", 0),
            ("evidence_unit_ids", ["evidence-1"], 0),
            ("material_ids", ["material-1"], 1),
            ("card_ids", ["card-1"], 2),
        )
        for field, value, paragraph_index in cases:
            payload = self._payload("body")
            if paragraph_index is None:
                payload[field] = value
            else:
                payload["paragraphs"][paragraph_index][field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    ReviewWritingContractError,
                    "schema.review_chapter_invalid",
                ):
                    validate_review_chapter(payload, self.body_schema)

    def test_text_must_not_be_blank_or_exceed_config_limit(self):
        blank = self._payload("body")
        blank["paragraphs"][0]["text"] = " "
        with self.assertRaises(
            ReviewWritingContractError,
        ) as raised:
            validate_review_chapter(blank, self.body_schema)
        self.assertEqual(
            raised.exception.code,
            "chapter.paragraph_text_empty",
        )

        long_text = self._payload("body")
        long_text["paragraphs"][0]["text"] = (
            "文" * (self.config.max_paragraph_chars + 1)
        )
        with self.assertRaisesRegex(
            ReviewWritingContractError,
            "schema.review_chapter_invalid",
        ):
            validate_review_chapter(long_text, self.body_schema)

    def test_config_rejects_missing_unknown_and_non_positive_fields(self):
        base = self.config.to_dict()
        cases = []

        missing = dict(base)
        del missing["max_paragraph_chars"]
        cases.append(missing)

        unknown = dict(base)
        unknown["unexpected"] = 1
        cases.append(unknown)

        zero = dict(base)
        zero["chapter_max_output_tokens"] = 0
        cases.append(zero)

        boolean = dict(base)
        boolean["max_paragraphs_per_section"] = True
        cases.append(boolean)

        wrong_version = dict(base)
        wrong_version["schema_version"] = "llm.review_writing_config.v0"
        cases.append(wrong_version)

        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "config.json"
                    path.write_text(
                        json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ReviewWritingContractError):
                        load_review_writing_config(path)

    def test_config_limit_must_support_body_minimum(self):
        config = ReviewWritingConfig(
            schema_version=REVIEW_WRITING_CONFIG_SCHEMA_VERSION,
            max_paragraphs_per_section=2,
            max_paragraph_chars=100,
            chapter_max_output_tokens=100,
            full_review_edit_max_output_tokens=100,
        )

        with self.assertRaises(
            ReviewWritingContractError,
        ) as raised:
            build_review_chapter_schema(
                section_index=2,
                section_title="方法比较",
                section_type="body",
                allowed_citation_keys=self.allowed_keys,
                config=config,
            )
        self.assertEqual(
            raised.exception.code,
            "config.paragraph_limit_too_small",
        )

    def _schema(self, section_type: str) -> dict:
        title = {
            "introduction": "引言",
            "body": "方法比较",
            "conclusion": "结论",
        }[section_type]
        index = {
            "introduction": 1,
            "body": 2,
            "conclusion": 3,
        }[section_type]
        return build_review_chapter_schema(
            section_index=index,
            section_title=title,
            section_type=section_type,
            allowed_citation_keys=self.allowed_keys,
            config=self.config,
        )

    def _payload(self, section_type: str) -> dict:
        title = {
            "introduction": "引言",
            "body": "方法比较",
            "conclusion": "结论",
        }[section_type]
        index = {
            "introduction": 1,
            "body": 2,
            "conclusion": 3,
        }[section_type]
        count = 3 if section_type == "body" else 2
        return {
            "schema_version": REVIEW_CHAPTER_SCHEMA_VERSION,
            "section_index": index,
            "title": title,
            "paragraphs": [
                {
                    "paragraph_index": paragraph_index,
                    "text": f"第{paragraph_index}段正文。",
                    "citation_keys": [
                        self.allowed_keys[
                            (paragraph_index - 1) % len(self.allowed_keys)
                        ]
                    ],
                }
                for paragraph_index in range(1, count + 1)
            ],
        }


if __name__ == "__main__":
    unittest.main()
