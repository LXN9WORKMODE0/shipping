from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import build_parser, main as cli_main
from shipping_pipeline.chapter_knowledge_package import (
    ChapterKnowledgePackageBuilder,
    ChapterKnowledgePackageError,
)
from shipping_pipeline.llm_projection import project_cards
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.research_landscape import (
    ResearchLandscapeSnapshot,
    ResearchLandscapeSource,
)
from shipping_pipeline.review_framework import (
    ReviewFrameworkRunSource,
    ReviewFrameworkSnapshot,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
)
WRITING_CONFIG = PROJECT_ROOT / "config" / "review-writing-default.json"
TOPIC = "三峡枢纽通航能力提升"
REVIEW_GOAL = "比较提升方法、验证水平和适用边界"


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


class ChapterKnowledgePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = (
            TEST_TMP_ROOT / f"chapter_package_{uuid.uuid4().hex}"
        )
        self.workspace.mkdir(parents=True)
        self.sources = (
            self._make_understanding_source("paper-a", "论文A", "调度优化"),
            self._make_understanding_source("paper-b", "论文B", "工程扩能"),
        )
        self.landscape_snapshot = self._make_landscape_snapshot(self.sources)
        self.framework_source = self._make_framework_source()
        self._write_landscape_manifest(self.landscape_snapshot)
        for source in self.sources:
            self._write_frozen_understanding(source)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_body_package_contains_every_framework_paper_and_full_inputs(self):
        result = self._build(run_id="package-success")

        self.assertEqual(result["status"], "completed")
        package = self._read_json(Path(result["output_path"]))
        self.assertEqual(
            [paper["paper_id"] for paper in package["papers"]],
            ["paper-a", "paper-b"],
        )
        self.assertEqual(len(package["papers"]), 2)
        for paper, source in zip(
            package["papers"],
            self.sources,
            strict=True,
        ):
            self.assertEqual(
                paper["full_markdown"],
                self._document_text(source),
            )
            self.assertEqual(
                paper["cards"],
                project_cards(source.materials),
            )
            self.assertEqual(paper["understanding"], source.understanding)
            self.assertEqual(
                paper["evidence_units"],
                list(source.evidence_units),
            )
            self.assertEqual(
                paper["bibliography"],
                self.framework_source.snapshot.reference_by_paper[
                    source.paper_id
                ],
            )
            self.assertEqual(
                paper["citation_key"],
                f"ref-{source.paper_id[-1]}",
            )

    def test_landscape_context_is_strictly_section_scoped(self):
        result = self._build(run_id="package-context")

        self.assertEqual(result["status"], "completed")
        package = self._read_json(Path(result["output_path"]))
        context = package["landscape_context"]
        self.assertEqual(
            [row["dimension_id"] for row in context["dimensions"]],
            ["dimension-1"],
        )
        self.assertEqual(
            [row["relation_id"] for row in context["relations"]],
            ["relation-selected"],
        )
        self.assertEqual(
            [row["disagreement_id"] for row in context["disagreements"]],
            ["disagreement-selected"],
        )
        self.assertEqual(
            [row["corpus_gap_id"] for row in context["corpus_gaps"]],
            ["gap-selected"],
        )
        self.assertNotIn("research_evolution", context)
        serialized = json.dumps(context, ensure_ascii=False)
        self.assertNotIn("dimension-excluded", serialized)
        self.assertNotIn("relation-excluded", serialized)
        self.assertNotIn("disagreement-excluded", serialized)
        self.assertNotIn("gap-excluded", serialized)
        self.assertNotIn("evolution-must-not-enter-package", serialized)

    def test_current_workspace_is_ignored_in_favor_of_frozen_input(self):
        current_dir = (
            self.workspace
            / "paper-a"
            / "materials"
        )
        current_dir.mkdir(parents=True)
        (current_dir / "materials.jsonl").write_text(
            json.dumps(
                {
                    "material_id": "current-card-must-not-be-read",
                    "extract": "当前工作区污染内容",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.workspace / "paper-a" / "document.md").write_text(
            "# 当前工作区文档，不得进入知识包\n",
            encoding="utf-8",
        )

        result = self._build(run_id="package-frozen-only")

        self.assertEqual(result["status"], "completed")
        package = self._read_json(Path(result["output_path"]))
        paper = package["papers"][0]
        self.assertEqual(paper["full_markdown"], self._document_text(self.sources[0]))
        self.assertEqual(
            [row["material_id"] for row in paper["cards"]],
            ["paper-a:card-1"],
        )
        self.assertNotIn(
            "当前工作区",
            json.dumps(package, ensure_ascii=False),
        )

    def test_noncontiguous_evidence_quote_fails_without_output(self):
        source = self.sources[0]
        invalid_evidence = copy.deepcopy(list(source.evidence_units))
        invalid_evidence[0]["citations"][0]["quote"] = "调度提高能力"
        invalid_source = replace(
            source,
            evidence_units=tuple(invalid_evidence),
        )
        sources = (invalid_source, self.sources[1])
        snapshot = self._make_landscape_snapshot(sources)
        self._write_landscape_manifest(snapshot)
        self._write_frozen_understanding(invalid_source)

        result = self._build(
            run_id="package-invalid-quote",
            landscape_snapshot=snapshot,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["failure"]["error_code"],
            "package.evidence_quote_not_exact",
        )
        self.assertFalse(
            (
                Path(result["run_dir"])
                / "output"
                / "knowledge_package.json"
            ).exists()
        )

    def test_citation_key_mismatch_fails_without_output(self):
        framework = copy.deepcopy(self.framework_source.framework)
        framework["sections"][0]["citation_keys"][0] = "ref-wrong"
        source = replace(self.framework_source, framework=framework)

        result = self._build(
            run_id="package-invalid-citation",
            framework_source=source,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["failure"]["error_code"],
            "package.citation_key_mismatch",
        )
        self.assertFalse(
            (
                Path(result["run_dir"])
                / "output"
                / "knowledge_package.json"
            ).exists()
        )

    def test_same_sources_produce_stable_package_and_input_hashes(self):
        first = self._build(run_id="package-stable-one")
        second = self._build(run_id="package-stable-two")

        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        first_manifest = self._read_json(Path(first["manifest_path"]))
        second_manifest = self._read_json(Path(second["manifest_path"]))
        self.assertEqual(
            first_manifest["package_id"],
            second_manifest["package_id"],
        )
        self.assertEqual(
            first_manifest["input_sha256"],
            second_manifest["input_sha256"],
        )

    def test_run_id_cannot_be_overwritten(self):
        first = self._build(run_id="package-immutable")
        self.assertEqual(first["status"], "completed")

        with self.assertRaises(ChapterKnowledgePackageError) as raised:
            self._build(run_id="package-immutable")

        self.assertEqual(raised.exception.code, "package.run_exists")

    def test_exact_token_boundary_passes_and_one_token_overflow_is_audited(self):
        profile_path, config_path = self._write_budget_configs()
        exact = self._build(
            run_id="package-budget-exact",
            token_counter=FakeTokenCounter(prompt_tokens=70),
            model_profile_path=profile_path,
            writing_config_path=config_path,
        )

        self.assertEqual(exact["status"], "completed")
        self.assertEqual(
            exact["token_budget"]["total_reserved_tokens"],
            100,
        )
        self.assertTrue(exact["token_budget"]["within_budget"])

        overflow = self._build(
            run_id="package-budget-overflow",
            token_counter=FakeTokenCounter(prompt_tokens=71),
            model_profile_path=profile_path,
            writing_config_path=config_path,
        )

        self.assertEqual(overflow["status"], "failed")
        self.assertEqual(
            overflow["failure"]["error_code"],
            "package.context_budget_exceeded",
        )
        run_dir = Path(overflow["run_dir"])
        self.assertTrue(
            (run_dir / "input" / "knowledge_package_candidate.json").is_file()
        )
        budget_path = run_dir / "audit" / "token_budget.json"
        self.assertTrue(budget_path.is_file())
        budget = self._read_json(budget_path)
        self.assertEqual(budget["overflow_tokens"], 1)
        self.assertFalse(budget["within_budget"])
        self.assertFalse(
            (run_dir / "output" / "knowledge_package.json").exists()
        )

    def test_file_hash_ledger_matches_every_frozen_input_file(self):
        result = self._build(run_id="package-hash-ledger")

        self.assertEqual(result["status"], "completed")
        run_dir = Path(result["run_dir"])
        ledger_path = run_dir / "input" / "file_hashes.jsonl"
        self.assertTrue(ledger_path.is_file())
        ledger = self._read_jsonl(ledger_path)
        self.assertGreater(len(ledger), 0)
        paths = {row["path"] for row in ledger}
        for paper_index in (1, 2):
            prefix = f"papers/paper-{paper_index:03d}"
            for name in (
                "document.md",
                "materials.jsonl",
                "projected_cards.jsonl",
                "evidence_units.jsonl",
                "paper_understanding.json",
                "understanding_manifest.json",
                "bibliography.json",
            ):
                self.assertIn(f"{prefix}/{name}", paths)
        for row in ledger:
            frozen_path = run_dir / "input" / Path(row["path"])
            self.assertTrue(frozen_path.is_file(), row["path"])
            raw = frozen_path.read_bytes()
            self.assertEqual(row["bytes"], len(raw))
            self.assertEqual(row["sha256"], self._sha256(raw))

    def test_section_selector_must_be_exclusive_and_unique(self):
        cases = [
            {
                "name": "neither",
                "run_id": "package-selector-neither",
                "section_id": None,
                "section_index": None,
                "source": self.framework_source,
                "code": "package.section_selector_invalid",
            },
            {
                "name": "both",
                "run_id": "package-selector-both",
                "section_id": "section-body",
                "section_index": 2,
                "source": self.framework_source,
                "code": "package.section_selector_invalid",
            },
            {
                "name": "duplicate",
                "run_id": "package-selector-duplicate",
                "section_id": None,
                "section_index": 2,
                "source": self._framework_with_duplicate_section(),
                "code": "package.section_not_found",
            },
        ]
        for case in cases:
            with self.subTest(case["name"]):
                result = self._build(
                    run_id=case["run_id"],
                    section_id=case["section_id"],
                    section_index=case["section_index"],
                    framework_source=case["source"],
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(
                    result["failure"]["error_code"],
                    case["code"],
                )

    def test_cli_requires_explicit_framework_and_section(self):
        args = build_parser().parse_args(
            [
                "chapter-knowledge-package",
                "--framework-run-id",
                "framework-one",
                "--section-index",
                "2",
            ]
        )

        self.assertEqual(args.command, "chapter-knowledge-package")
        self.assertEqual(args.framework_run_id, "framework-one")
        self.assertEqual(args.section_index, 2)
        self.assertIsNone(args.section_id)

    def test_cli_returns_zero_only_for_completed(self):
        argv = [
            "chapter-knowledge-package",
            "--framework-run-id",
            "framework-one",
            "--section-id",
            "section-body",
        ]
        with patch("main.ChapterKnowledgePackageBuilder") as builder:
            builder.return_value.build.return_value = {
                "status": "completed",
                "run_id": "package-one",
            }
            self.assertEqual(cli_main(argv), 0)
            builder.return_value.build.return_value = {
                "status": "failed",
                "run_id": "package-two",
            }
            self.assertEqual(cli_main(argv), 1)

    def _build(
        self,
        *,
        run_id: str,
        section_id: str | None = "section-body",
        section_index: int | None = None,
        framework_source: ReviewFrameworkRunSource | None = None,
        landscape_snapshot: ResearchLandscapeSnapshot | None = None,
        token_counter: FakeTokenCounter | None = None,
        model_profile_path: Path = MODEL_PROFILE,
        writing_config_path: Path = WRITING_CONFIG,
    ) -> dict[str, Any]:
        source = framework_source or self.framework_source
        landscape = landscape_snapshot or self.landscape_snapshot
        with (
            patch(
                "shipping_pipeline.chapter_knowledge_package."
                "load_review_framework_run",
                return_value=source,
            ),
            patch(
                "shipping_pipeline.chapter_knowledge_package."
                "create_research_landscape_snapshot",
                return_value=landscape,
            ),
        ):
            return ChapterKnowledgePackageBuilder(
                self.workspace,
                token_counter=token_counter or FakeTokenCounter(),
            ).build(
                framework_run_id=source.run_id,
                section_id=section_id,
                section_index=section_index,
                run_id=run_id,
                model_profile_path=model_profile_path,
                writing_config_path=writing_config_path,
            )

    def _make_understanding_source(
        self,
        paper_id: str,
        paper_title: str,
        method: str,
    ) -> ResearchLandscapeSource:
        card = {
            "material_id": f"{paper_id}:card-1",
            "paper_id": paper_id,
            "paper_title": paper_title,
            "content_kind": "paragraph",
            "heading_path": ["研究方法"],
            "clean_title": paper_title,
            "extract": f"{paper_title}通过{method}提高通航能力。",
            "confidence_flags": [],
            "quality_flags": [],
            "source_span": {
                "path": "normalized/document.md",
                "start_line": 3,
                "end_line": 3,
            },
        }
        quote = f"{method}提高通航能力"
        evidence = {
            "evidence_unit_id": f"{paper_id}:evidence-1",
            "claim": f"{paper_title}采用{method}。",
            "citations": [
                {
                    "material_id": card["material_id"],
                    "quote": quote,
                    "source_ref": copy.deepcopy(card["source_span"]),
                }
            ],
        }
        understanding = {
            "schema_version": "llm.paper_understanding.v1",
            "paper_id": paper_id,
            "paper_title": paper_title,
            "topic": TOPIC,
            "core_question": f"{paper_title}如何提升通航能力？",
            "contributions": [
                {
                    "contribution_id": f"contribution-{paper_id[-1]}",
                    "statement": f"提出{method}方案。",
                }
            ],
        }
        return ResearchLandscapeSource(
            run_id=f"understanding-{paper_id}",
            paper_id=paper_id,
            paper_title=paper_title,
            topic=TOPIC,
            generation_id=f"generation-{paper_id}",
            input_sha256=f"sha256:input-{paper_id}",
            manifest_sha256=f"sha256:manifest-{paper_id}",
            output_sha256=f"sha256:output-{paper_id}",
            understanding=understanding,
            materials=(card,),
            evidence_units=(evidence,),
        )

    def _make_landscape_snapshot(
        self,
        sources: tuple[ResearchLandscapeSource, ...],
    ) -> ResearchLandscapeSnapshot:
        return ResearchLandscapeSnapshot(
            topic=TOPIC,
            review_goal=REVIEW_GOAL,
            corpus_scope="targeted_sample",
            collection={
                "schema_version": "llm.research_landscape_collection.v1",
                "topic": TOPIC,
                "review_goal": REVIEW_GOAL,
                "corpus_scope": "targeted_sample",
                "source_understanding_run_ids": [
                    source.run_id for source in sources
                ],
            },
            sources=sources,
            input_sha256="sha256:landscape-input",
        )

    def _make_framework_source(self) -> ReviewFrameworkRunSource:
        landscape = {
            "schema_version": "llm.research_landscape.v1",
            "landscape_id": "landscape-test",
            "topic": TOPIC,
            "review_goal": REVIEW_GOAL,
            "corpus_scope": "targeted_sample",
            "central_problem": "如何提升三峡枢纽通航能力？",
            "dimensions": [
                {
                    "dimension_index": 1,
                    "dimension_id": "dimension-1",
                    "title": "运行与工程方法",
                    "question": "不同方法如何提升通航能力？",
                    "paper_ids": ["paper-a", "paper-b"],
                    "contribution_ids": [
                        "contribution-a",
                        "contribution-b",
                    ],
                },
                {
                    "dimension_index": 2,
                    "dimension_id": "dimension-excluded",
                    "title": "不属于本节的维度",
                    "question": "其他问题如何处理？",
                    "paper_ids": ["paper-c"],
                    "contribution_ids": ["contribution-c"],
                },
            ],
            "relations": [
                {
                    "relation_id": "relation-selected",
                    "relation_type": "complements",
                    "from_paper_id": "paper-a",
                    "to_paper_id": "paper-b",
                    "statement": "运行与工程方法形成互补。",
                    "supporting_contribution_ids": [
                        "contribution-a",
                        "contribution-b",
                    ],
                },
                {
                    "relation_id": "relation-excluded",
                    "relation_type": "scope_difference",
                    "from_paper_id": "paper-a",
                    "to_paper_id": "paper-c",
                    "statement": "研究范围不同。",
                    "supporting_contribution_ids": [
                        "contribution-a",
                        "contribution-c",
                    ],
                },
            ],
            "research_evolution": [
                {
                    "evolution_id": "evolution-must-not-enter-package",
                    "period": "2010-2020",
                    "statement": "研究关注发生变化。",
                    "paper_ids": ["paper-a", "paper-b"],
                    "contribution_ids": [
                        "contribution-a",
                        "contribution-b",
                    ],
                }
            ],
            "disagreements": [
                {
                    "disagreement_id": "disagreement-selected",
                    "question": "优先采用何种方法？",
                    "positions": [
                        {
                            "paper_ids": ["paper-a"],
                            "statement": "优先优化运行。",
                            "contribution_ids": ["contribution-a"],
                        },
                        {
                            "paper_ids": ["paper-b"],
                            "statement": "优先实施工程。",
                            "contribution_ids": ["contribution-b"],
                        },
                    ],
                },
                {
                    "disagreement_id": "disagreement-excluded",
                    "question": "本节外争议是什么？",
                    "positions": [],
                },
            ],
            "corpus_gaps": [
                {
                    "corpus_gap_id": "gap-selected",
                    "question": "组合效果如何？",
                    "why_it_matters": "影响方案选择。",
                    "related_dimension_indexes": [1],
                },
                {
                    "corpus_gap_id": "gap-excluded",
                    "question": "其他缺口是什么？",
                    "why_it_matters": "不属于本节。",
                    "related_dimension_indexes": [2],
                },
            ],
            "unmapped_papers": [],
        }
        references = {
            "paper-a": {
                "paper_id": "paper-a",
                "reference_id": "ref-a",
                "citation_key": "ref-a",
                "title": "论文A",
                "authors": ["作者甲"],
                "year": 2020,
                "status": "complete",
                "missing_fields": [],
            },
            "paper-b": {
                "paper_id": "paper-b",
                "reference_id": "ref-b",
                "citation_key": "ref-b",
                "title": "论文B",
                "authors": ["作者乙"],
                "year": 2021,
                "status": "partial",
                "missing_fields": ["pages"],
            },
        }
        snapshot = ReviewFrameworkSnapshot(
            topic=TOPIC,
            review_goal=REVIEW_GOAL,
            corpus_scope="targeted_sample",
            landscape_run_id="landscape-run",
            landscape_manifest_sha256="sha256:landscape-manifest",
            landscape_output_sha256="sha256:landscape-output",
            landscape=landscape,
            reference_catalog_run_id="reference-run",
            reference_manifest_sha256="sha256:reference-manifest",
            reference_catalog_sha256="sha256:reference-output",
            reference_by_paper=references,
            input_sha256="sha256:framework-input",
        )
        section = {
            "section_index": 2,
            "section_id": "section-body",
            "title": "提升方法与证据比较",
            "section_type": "body",
            "question": "运行与工程方法的机制和证据有何差异？",
            "purpose": "比较方法、结果和适用边界。",
            "dimension_indexes": [1],
            "paper_ids": ["paper-a", "paper-b"],
            "contribution_ids": [
                "contribution-a",
                "contribution-b",
            ],
            "citation_keys": ["ref-a", "ref-b"],
            "bibliography_status": "partial",
            "required_comparisons": ["比较作用机制和验证水平。"],
            "controversy_ids": ["disagreement-selected"],
            "corpus_gap_indexes": [1],
            "corpus_limitations": ["本次语料缺少长期联合验证。"],
        }
        return ReviewFrameworkRunSource(
            run_id="framework-run",
            manifest_sha256="sha256:framework-manifest",
            output_sha256="sha256:framework-output",
            snapshot=snapshot,
            framework={
                "schema_version": "llm.review_framework.v1",
                "framework_id": "framework-test",
                "topic": TOPIC,
                "review_goal": REVIEW_GOAL,
                "sections": [section],
            },
        )

    def _framework_with_duplicate_section(self) -> ReviewFrameworkRunSource:
        framework = copy.deepcopy(self.framework_source.framework)
        duplicate = copy.deepcopy(framework["sections"][0])
        duplicate["section_id"] = "section-body-duplicate"
        framework["sections"].append(duplicate)
        return replace(self.framework_source, framework=framework)

    def _write_landscape_manifest(
        self,
        snapshot: ResearchLandscapeSnapshot,
    ) -> None:
        run_dir = (
            self.workspace
            / "_research_landscapes"
            / "runs"
            / "landscape-run"
        )
        (run_dir / "input").mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": "landscape-run",
                    "status": "completed",
                    "input_sha256": snapshot.input_sha256,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (run_dir / "input" / "collection.json").write_text(
            json.dumps(snapshot.collection, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _write_frozen_understanding(
        self,
        source: ResearchLandscapeSource,
    ) -> None:
        run_dir = (
            self.workspace
            / "_paper_understandings"
            / "runs"
            / source.run_id
        )
        (run_dir / "input").mkdir(parents=True, exist_ok=True)
        (run_dir / "output").mkdir(parents=True, exist_ok=True)
        (run_dir / "input" / "document.md").write_bytes(
            self._document_text(source).encode("utf-8")
        )
        self._write_jsonl(
            run_dir / "input" / "materials.jsonl",
            list(source.materials),
        )
        self._write_jsonl(
            run_dir / "input" / "projected_cards.jsonl",
            project_cards(source.materials),
        )
        self._write_jsonl(
            run_dir / "input" / "evidence_units.jsonl",
            list(source.evidence_units),
        )
        (run_dir / "output" / "paper_understanding.json").write_text(
            json.dumps(source.understanding, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": source.run_id,
                    "paper_id": source.paper_id,
                    "status": "completed",
                    "input_sha256": source.input_sha256,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_budget_configs(self) -> tuple[Path, Path]:
        profile = self._read_json(MODEL_PROFILE)
        profile.update(
            {
                "profile_id": "test-deepseek-budget",
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
        profile_path = self.workspace / "budget-model-profile.json"
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        config = {
            "schema_version": "llm.review_writing_config.v1",
            "max_paragraphs_per_section": 12,
            "max_paragraph_chars": 4000,
            "chapter_max_output_tokens": 20,
            "full_review_edit_max_output_tokens": 20,
        }
        config_path = self.workspace / "budget-writing-config.json"
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return profile_path, config_path

    @staticmethod
    def _document_text(source: ResearchLandscapeSource) -> str:
        return (
            f"# {source.paper_title}\n\n"
            f"{source.materials[0]['extract']}\n"
        )

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]

    @staticmethod
    def _sha256(raw: bytes) -> str:
        return "sha256:" + hashlib.sha256(raw).hexdigest()


if __name__ == "__main__":
    unittest.main()
