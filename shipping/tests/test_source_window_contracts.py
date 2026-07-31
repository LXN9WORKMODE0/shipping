from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.source_window_contracts import (
    SOURCE_WINDOW_SCHEMA_VERSION,
    SOURCE_WINDOW_SELECTION_SCHEMA_VERSION,
    SourceWindowContractError,
    stable_id,
    validate_source_window_selection,
)


class SourceWindowContractTests(unittest.TestCase):
    def test_valid_tier_1_selection_is_accepted(self):
        payload = self._selection()

        validated = validate_source_window_selection(payload)

        self.assertEqual(validated, payload)

    def test_tier_1_rejects_expanded_markdown(self):
        payload = self._selection()
        payload["expanded_markdown_sources"] = [
            {
                "paper_id": "paper-a",
                "citation_key": "ref-a",
                "markdown": "# 论文A",
            }
        ]
        self._refresh_selection_id(payload)

        with self.assertRaises(SourceWindowContractError) as raised:
            validate_source_window_selection(payload)

        self.assertEqual(
            raised.exception.code,
            "source_window.tier_1_markdown_forbidden",
        )

    def test_cross_paper_citation_mismatch_is_rejected(self):
        payload = self._selection()
        payload["selected_source_windows"][0]["citation_key"] = "ref-b"
        self._refresh_window_id(payload["selected_source_windows"][0])
        self._refresh_selection_id(payload)

        with self.assertRaises(SourceWindowContractError) as raised:
            validate_source_window_selection(payload)

        self.assertEqual(
            raised.exception.code,
            "source_window.citation_key_mismatch",
        )

    def test_uncovered_contribution_is_rejected(self):
        payload = self._selection()
        payload["coverage"]["covered_contribution_ids"] = []
        payload["coverage"]["uncovered_contribution_ids"] = [
            "contribution-a"
        ]
        self._refresh_selection_id(payload)

        with self.assertRaises(SourceWindowContractError) as raised:
            validate_source_window_selection(payload)

        self.assertEqual(
            raised.exception.code,
            "source_window.contribution_uncovered",
        )

    def test_selection_and_window_ids_are_content_addressed(self):
        payload = self._selection()
        tampered = copy.deepcopy(payload)
        tampered["selected_source_windows"][0]["text"] = "被修改的正文"
        self._refresh_selection_id(tampered)

        with self.assertRaises(SourceWindowContractError) as raised:
            validate_source_window_selection(tampered)

        self.assertEqual(
            raised.exception.code,
            "source_window.window_id_mismatch",
        )

    def _selection(self) -> dict:
        window = {
            "schema_version": SOURCE_WINDOW_SCHEMA_VERSION,
            "window_id": "",
            "section_id": "section-a",
            "paper_id": "paper-a",
            "citation_key": "ref-a",
            "window_type": "result_context",
            "text": "调度方法提高了通航效率。",
            "heading_path": ["结果"],
            "source_spans": [
                {
                    "path": "normalized/document.md",
                    "start_line": 10,
                    "end_line": 10,
                    "start_char": 0,
                    "end_char": 12,
                }
            ],
            "material_ids": ["material-a"],
            "contribution_ids": ["contribution-a"],
            "evidence_unit_ids": [],
            "result_types": ["simulation_result"],
            "validation_levels": ["simulation"],
            "contains_limitation": False,
            "parse_flags": [],
            "quality_flags": [],
            "selection_reasons": ["framework_contribution"],
            "source_fingerprints": ["sha256:source-a"],
        }
        self._refresh_window_id(window)
        payload = {
            "schema_version": SOURCE_WINDOW_SELECTION_SCHEMA_VERSION,
            "source": {
                "framework_run_id": "framework-run",
                "framework_id": "framework-a",
                "framework_manifest_sha256": "sha256:manifest",
                "framework_output_sha256": "sha256:output",
                "landscape_run_id": "landscape-run",
                "landscape_id": "landscape-a",
            },
            "section": {
                "section_id": "section-a",
                "paper_ids": ["paper-a"],
                "contribution_ids": ["contribution-a"],
                "citation_keys": ["ref-a"],
            },
            "material_tier": "tier_1",
            "understanding_projections": [
                {
                    "paper_id": "paper-a",
                    "paper_title": "论文A",
                    "paper_relevance": "core",
                    "research_questions": [],
                    "study_context": {},
                    "methods": [],
                    "contributions": [],
                    "limitations": [],
                    "review_roles": [],
                }
            ],
            "selected_source_windows": [window],
            "expanded_markdown_sources": [],
            "citation_metadata": [
                {
                    "paper_id": "paper-a",
                    "citation_key": "ref-a",
                    "paper_title": "论文A",
                    "year": "2025",
                    "reference_type": "journal",
                    "bibliography_status": "complete",
                }
            ],
            "coverage": {
                "required_contribution_ids": ["contribution-a"],
                "covered_contribution_ids": ["contribution-a"],
                "uncovered_contribution_ids": [],
                "required_material_ids": ["material-a"],
                "covered_material_ids": ["material-a"],
                "uncovered_material_ids": [],
                "window_count": 1,
                "paper_count": 1,
                "window_type_counts": {"result_context": 1},
                "papers_without_method_context": ["paper-a"],
                "papers_without_limitation_context": ["paper-a"],
                "deduplicated_candidate_count": 0,
            },
            "selection_id": "",
        }
        self._refresh_selection_id(payload)
        return payload

    @staticmethod
    def _refresh_window_id(window: dict) -> None:
        window["window_id"] = stable_id(
            "window",
            {
                "section_id": window["section_id"],
                "paper_id": window["paper_id"],
                "text": window["text"],
                "source_spans": window["source_spans"],
                "material_ids": window["material_ids"],
            },
        )

    @staticmethod
    def _refresh_selection_id(payload: dict) -> None:
        payload["selection_id"] = stable_id(
            "source_selection",
            {
                key: value
                for key, value in payload.items()
                if key != "selection_id"
            },
        )


if __name__ == "__main__":
    unittest.main()
