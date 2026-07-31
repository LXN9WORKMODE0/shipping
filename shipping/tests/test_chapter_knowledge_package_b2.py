from __future__ import annotations

import copy
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.chapter_knowledge_package_b2 import (
    B2ChapterKnowledgePackageBuilder,
    B2ChapterKnowledgePackageError,
    load_b2_preview_package_run,
)
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.source_window_contracts import (
    SOURCE_WINDOW_SCHEMA_VERSION,
    SOURCE_WINDOW_SELECTION_SCHEMA_VERSION,
    stable_id,
)
from shipping_pipeline.source_window_selection import SourceWindowRunSource
from main import build_parser, main as cli_main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
)
WRITING_CONFIG = PROJECT_ROOT / "config" / "review-writing-default.json"


class FakeTokenCounter:
    def __init__(self, prompt_tokens: int = 100) -> None:
        self.prompt_tokens = prompt_tokens

    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        return TokenCount(
            prompt_tokens=self.prompt_tokens,
            encoded_prompt_sha256=(
                f"sha256:{self.prompt_tokens:064x}"
            ),
        )

    def count_text(self, text: str) -> int:
        return max(1, len(text.encode("utf-8")) // 4)


class B2ChapterKnowledgePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = (
            TEST_TMP_ROOT / f"package_b2_{uuid.uuid4().hex}"
        )
        self.workspace.mkdir(parents=True)
        self.source = self._source()

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_tier_1_preview_contains_only_projected_sources(self):
        result = self._build("package-b2-tier-1")

        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["writing_ready"])
        package = self._read_json(Path(result["output_path"]))
        self.assertFalse(package["writing_ready"])
        self.assertEqual(package["material_tier"], "tier_1")
        self.assertEqual(package["expanded_markdown_sources"], [])
        serialized = json.dumps(package, ensure_ascii=False)
        self.assertNotIn('"full_markdown"', serialized)
        self.assertNotIn('"cards"', serialized)
        self.assertNotIn('"evidence_units"', serialized)
        self.assertEqual(package["coverage"]["window_count"], 1)

    def test_same_source_produces_stable_package_id(self):
        first = self._build("package-b2-stable-1")
        second = self._build("package-b2-stable-2")

        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["package_id"], second["package_id"])

    def test_token_overflow_is_recorded_without_formal_output(self):
        profile_path, config_path = self._budget_configs()
        result = self._build(
            "package-b2-overflow",
            token_counter=FakeTokenCounter(prompt_tokens=71),
            model_profile_path=profile_path,
            writing_config_path=config_path,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["failure"]["error_code"],
            "package_b2.context_budget_exceeded",
        )
        self.assertFalse(
            (
                Path(result["run_dir"])
                / "output"
                / "knowledge_package_b2_preview.json"
            ).exists()
        )
        budget = self._read_json(
            Path(result["run_dir"]) / "audit" / "token_budget.json"
        )
        self.assertEqual(budget["overflow_tokens"], 1)

    def test_completed_run_replays(self):
        result = self._build("package-b2-replay")
        self.assertEqual(result["status"], "completed")

        loaded = load_b2_preview_package_run(
            self.workspace,
            "package-b2-replay",
            source_loader=lambda workspace, run_id: self.source,
        )

        self.assertEqual(loaded.package["package_id"], result["package_id"])
        self.assertFalse(loaded.package["writing_ready"])

    def test_existing_run_id_cannot_be_overwritten(self):
        first = self._build("package-b2-immutable")
        self.assertEqual(first["status"], "completed")

        with self.assertRaises(B2ChapterKnowledgePackageError) as raised:
            self._build("package-b2-immutable")

        self.assertEqual(raised.exception.code, "package_b2.run_exists")

    def test_cli_uses_explicit_source_window_run(self):
        args = build_parser().parse_args(
            [
                "chapter-knowledge-package-b2-preview",
                "--source-window-run-id",
                "source-window-run",
            ]
        )

        self.assertEqual(
            args.command,
            "chapter-knowledge-package-b2-preview",
        )
        self.assertEqual(args.source_window_run_id, "source-window-run")

    def test_cli_returns_zero_only_for_completed(self):
        argv = [
            "chapter-knowledge-package-b2-preview",
            "--source-window-run-id",
            "source-window-run",
        ]
        with patch("main.B2ChapterKnowledgePackageBuilder") as builder:
            builder.return_value.build.return_value = {
                "status": "completed"
            }
            self.assertEqual(cli_main(argv), 0)
            builder.return_value.build.return_value = {"status": "failed"}
            self.assertEqual(cli_main(argv), 1)

    def _build(
        self,
        run_id: str,
        *,
        token_counter: FakeTokenCounter | None = None,
        model_profile_path: Path = MODEL_PROFILE,
        writing_config_path: Path = WRITING_CONFIG,
    ) -> dict:
        return B2ChapterKnowledgePackageBuilder(
            self.workspace,
            token_counter=token_counter or FakeTokenCounter(),
            source_loader=lambda workspace, source_run_id: self.source,
        ).build(
            source_window_run_id=self.source.run_id,
            run_id=run_id,
            model_profile_path=model_profile_path,
            writing_config_path=writing_config_path,
        )

    def _source(self) -> SourceWindowRunSource:
        span = {
            "path": "normalized/document.md",
            "start_line": 3,
            "end_line": 3,
            "start_char": 0,
            "end_char": 20,
        }
        window = {
            "schema_version": SOURCE_WINDOW_SCHEMA_VERSION,
            "window_id": "",
            "section_id": "section-body",
            "paper_id": "paper-a",
            "citation_key": "ref_a",
            "window_type": "result_context",
            "text": "调度优化提高通航能力。",
            "heading_path": ["结果"],
            "source_spans": [span],
            "material_ids": ["material-a"],
            "contribution_ids": ["contribution-a"],
            "evidence_unit_ids": ["evidence-a"],
            "result_types": ["simulation_result"],
            "validation_levels": ["simulation"],
            "contains_limitation": True,
            "parse_flags": [],
            "quality_flags": [],
            "selection_reasons": [
                "framework_contribution",
                "method_context:methods",
                "limitation_context:limitations",
            ],
            "source_fingerprints": ["sha256:material-a"],
        }
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
        selection_without_id = {
            "schema_version": SOURCE_WINDOW_SELECTION_SCHEMA_VERSION,
            "source": {
                "framework_run_id": "framework-run",
                "framework_id": "framework-one",
                "framework_manifest_sha256": "sha256:framework-manifest",
                "framework_output_sha256": "sha256:framework-output",
                "landscape_run_id": "landscape-run",
                "landscape_id": "landscape-one",
            },
            "section": {
                "section_index": 1,
                "section_id": "section-body",
                "title": "方法比较",
                "section_type": "body",
                "question": "如何提升通航能力？",
                "purpose": "比较方法。",
                "dimension_indexes": [1],
                "required_comparisons": ["比较方法"],
                "controversy_ids": [],
                "corpus_gap_indexes": [],
                "corpus_limitations": ["定向样本"],
                "paper_ids": ["paper-a"],
                "contribution_ids": ["contribution-a"],
                "citation_keys": ["ref_a"],
                "bibliography_status": "complete",
            },
            "material_tier": "tier_1",
            "understanding_projections": [
                {
                    "paper_id": "paper-a",
                    "paper_title": "论文A",
                    "paper_relevance": "core",
                    "research_questions": [
                        {
                            "question": "如何提升能力？",
                            "problem_category": "方法评价",
                        }
                    ],
                    "study_context": {
                        "study_object": "船闸",
                        "data_sources": ["simulation"],
                        "time_scope": "未说明",
                        "geographic_scope": "三峡",
                    },
                    "methods": [
                        {
                            "method_category": "simulation",
                            "method_name": "调度优化",
                            "description": "仿真比较。",
                        }
                    ],
                    "contributions": [
                        {
                            "contribution_id": "contribution-a",
                            "statement": "提出调度优化方法。",
                            "result_type": "simulation_result",
                            "validation_level": "simulation",
                            "evidence_strength": "moderate",
                            "strength_rationale": "基于仿真。",
                        }
                    ],
                    "limitations": [
                        {
                            "statement": "缺少现场验证。",
                            "basis": "reviewer_inferred",
                        }
                    ],
                    "review_roles": [],
                }
            ],
            "selected_source_windows": [window],
            "expanded_markdown_sources": [],
            "citation_metadata": [
                {
                    "paper_id": "paper-a",
                    "citation_key": "ref_a",
                    "paper_title": "论文A",
                    "authors": ["作者"],
                    "year": 2025,
                    "reference_type": "article",
                    "journal": "测试期刊",
                    "institution": None,
                    "degree": None,
                    "doi": None,
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
                "window_type_counts": {
                    "implementation_status": 0,
                    "author_conclusion": 0,
                    "result_context": 1,
                    "method_context": 0,
                    "limitation_context": 0,
                    "problem_context": 0,
                },
                "papers_without_method_context": [],
                "papers_without_limitation_context": [],
                "deduplicated_candidate_count": 2,
            },
        }
        selection = {
            **selection_without_id,
            "selection_id": stable_id(
                "source_selection",
                selection_without_id,
            ),
        }
        return SourceWindowRunSource(
            run_id="source-window-run",
            manifest_sha256="sha256:source-manifest",
            output_sha256="sha256:source-output",
            selection=selection,
            framework={
                "schema_version": "llm.review_framework.v1",
                "framework_id": "framework-one",
            },
        )

    def _budget_configs(self) -> tuple[Path, Path]:
        profile = json.loads(
            MODEL_PROFILE.read_text(encoding="utf-8-sig")
        )
        profile.update(
            {
                "profile_id": "test-b2-budget",
                "context_window_tokens": 100,
                "model_max_output_tokens": 50,
                "safety_margin_tokens": 10,
                "evidence_output_base_tokens": 5,
                "evidence_output_tokens_per_card": 5,
                "evidence_max_output_tokens": 20,
                "section_summary_max_output_tokens": 20,
                "paper_synthesis_max_output_tokens": 20,
                "evidence_claim_output_base_tokens": 5,
                "evidence_claim_output_tokens_per_unit": 5,
                "evidence_claim_support_max_output_tokens": 20,
                "evidence_revision_max_output_tokens": 20,
                "statement_revision_max_output_tokens": 20,
                "statement_role_max_output_tokens": 20,
                "statement_support_max_output_tokens": 20,
            }
        )
        profile_path = self.workspace / "profile.json"
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        config = json.loads(
            WRITING_CONFIG.read_text(encoding="utf-8-sig")
        )
        config["chapter_max_output_tokens"] = 20
        config["full_review_edit_max_output_tokens"] = 20
        config_path = self.workspace / "writing.json"
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return profile_path, config_path

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
