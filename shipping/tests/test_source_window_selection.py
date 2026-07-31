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
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_projection import project_cards
from shipping_pipeline.research_landscape import (
    ResearchLandscapeSnapshot,
    ResearchLandscapeSource,
)
from shipping_pipeline.review_framework import (
    ReviewFrameworkRunSource,
    ReviewFrameworkSnapshot,
)
from shipping_pipeline.source_window_selection import (
    SourceWindowSelectionBuilder,
    SourceWindowSelectionError,
    load_source_window_run,
)
from main import build_parser, main as cli_main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"


class SourceWindowSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = (
            TEST_TMP_ROOT / f"source_window_{uuid.uuid4().hex}"
        )
        self.workspace.mkdir(parents=True)
        self.sources = (
            self._make_source("paper-a", "论文A", "调度优化"),
            self._make_source("paper-b", "论文B", "工程扩能"),
        )
        self.landscape_snapshot = self._make_landscape_snapshot()
        self.framework_source = self._make_framework_source()
        self._write_landscape_manifest()
        for source in self.sources:
            self._write_understanding_run(source)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_tier_1_selects_bound_materials_without_full_markdown(self):
        result = self._build("source-window-tier-1")

        self.assertEqual(result["status"], "completed")
        selection = self._read_json(Path(result["output_path"]))
        self.assertEqual(selection["material_tier"], "tier_1")
        self.assertEqual(selection["expanded_markdown_sources"], [])
        self.assertEqual(
            selection["coverage"]["required_contribution_ids"],
            ["contribution-a", "contribution-b"],
        )
        self.assertEqual(
            selection["coverage"]["uncovered_contribution_ids"],
            [],
        )
        self.assertEqual(
            [row["paper_id"] for row in selection["selected_source_windows"]],
            ["paper-a", "paper-b"],
        )
        serialized = json.dumps(selection, ensure_ascii=False)
        self.assertNotIn("# 论文A冻结全文", serialized)
        self.assertNotIn("# 论文B冻结全文", serialized)

    def test_tier_2_expands_only_section_papers_explicitly(self):
        result = self._build(
            "source-window-tier-2",
            material_tier="tier_2",
        )

        self.assertEqual(result["status"], "completed")
        selection = self._read_json(Path(result["output_path"]))
        self.assertEqual(
            [row["paper_id"] for row in selection["expanded_markdown_sources"]],
            ["paper-a", "paper-b"],
        )
        self.assertIn(
            "# 论文A冻结全文",
            selection["expanded_markdown_sources"][0]["markdown"],
        )

    def test_method_and_limitation_sources_are_selected_without_overlap(self):
        source = self.sources[0]
        understanding = copy.deepcopy(source.understanding)
        method_card = copy.deepcopy(source.materials[0])
        method_card["material_id"] = "paper-a:method-card"
        method_card["extract"] = "论文A使用离散事件仿真。"
        method_card["source_span"] = {
            "path": "normalized/document.md",
            "start_line": 2,
            "end_line": 2,
            "start_char": 0,
            "end_char": 12,
        }
        method_card["source_spans"] = [method_card["source_span"]]
        limitation_card = copy.deepcopy(source.materials[0])
        limitation_card["material_id"] = "paper-a:limitation-card"
        limitation_card["extract"] = "研究缺少现场验证。"
        limitation_card["source_span"] = {
            "path": "normalized/document.md",
            "start_line": 2,
            "end_line": 2,
            "start_char": 0,
            "end_char": 10,
        }
        limitation_card["source_spans"] = [limitation_card["source_span"]]
        understanding["methods"][0]["material_ids"] = [
            method_card["material_id"]
        ]
        understanding["limitations"][0]["material_ids"] = [
            limitation_card["material_id"]
        ]
        expanded = replace(
            source,
            understanding=understanding,
            materials=(
                source.materials[0],
                method_card,
                limitation_card,
            ),
        )
        self.sources = (expanded, self.sources[1])
        self.landscape_snapshot = self._make_landscape_snapshot()
        self._write_landscape_manifest()
        self._write_understanding_run(expanded)

        result = self._build("source-window-required-context")

        self.assertEqual(result["status"], "completed")
        selection = self._read_json(Path(result["output_path"]))
        paper_a_windows = [
            row
            for row in selection["selected_source_windows"]
            if row["paper_id"] == "paper-a"
        ]
        self.assertEqual(
            {row["window_type"] for row in paper_a_windows},
            {
                "result_context",
                "method_context",
                "limitation_context",
            },
        )
        self.assertNotIn(
            "paper-a",
            selection["coverage"]["papers_without_method_context"],
        )
        self.assertNotIn(
            "paper-a",
            selection["coverage"]["papers_without_limitation_context"],
        )

    def test_source_less_context_is_not_reported_as_missing(self):
        source = self.sources[0]
        understanding = copy.deepcopy(source.understanding)
        understanding["methods"][0]["material_ids"] = []
        understanding["limitations"][0]["material_ids"] = []
        source_less = replace(source, understanding=understanding)
        self.sources = (source_less, self.sources[1])
        self.landscape_snapshot = self._make_landscape_snapshot()
        self._write_landscape_manifest()
        self._write_understanding_run(source_less)

        result = self._build("source-window-source-less-context")

        self.assertEqual(result["status"], "completed")
        selection = self._read_json(Path(result["output_path"]))
        self.assertNotIn(
            "paper-a",
            selection["coverage"]["papers_without_method_context"],
        )
        self.assertNotIn(
            "paper-a",
            selection["coverage"]["papers_without_limitation_context"],
        )
        paper_a_projection = next(
            row
            for row in selection["understanding_projections"]
            if row["paper_id"] == "paper-a"
        )
        self.assertEqual(
            paper_a_projection["limitations"],
            [{"statement": "缺少现场验证。", "basis": "reviewer_inferred"}],
        )

    def test_missing_contribution_material_fails_without_output(self):
        source = self.sources[0]
        understanding = copy.deepcopy(source.understanding)
        understanding["contributions"][0]["material_ids"] = ["missing-card"]
        invalid = replace(source, understanding=understanding)
        self.sources = (invalid, self.sources[1])
        self.landscape_snapshot = self._make_landscape_snapshot()
        self._write_landscape_manifest()
        self._write_understanding_run(invalid)

        result = self._build("source-window-missing-material")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["failure"]["error_code"],
            "source_window.material_missing",
        )
        self.assertFalse(
            (
                Path(result["run_dir"])
                / "output"
                / "source_window_selection.json"
            ).exists()
        )

    def test_section_paper_without_selected_contribution_fails(self):
        framework = copy.deepcopy(self.framework_source.framework)
        framework["sections"][0]["contribution_ids"] = [
            "contribution-b"
        ]
        self.framework_source = replace(
            self.framework_source,
            framework=framework,
        )

        result = self._build("source-window-paper-without-contribution")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["failure"]["error_code"],
            "source_window.paper_contribution_missing",
        )

    def test_same_sources_produce_stable_selection_identity(self):
        first = self._build("source-window-stable-1")
        second = self._build("source-window-stable-2")

        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(first["selection_id"], second["selection_id"])

    def test_completed_run_replays_and_checks_frozen_hashes(self):
        result = self._build("source-window-replay")
        self.assertEqual(result["status"], "completed")

        with self._patch_sources():
            loaded = load_source_window_run(
                self.workspace,
                "source-window-replay",
            )

        self.assertEqual(loaded.selection["selection_id"], result["selection_id"])
        self.assertEqual(len(loaded.selection["selected_source_windows"]), 2)

    def test_existing_run_id_cannot_be_overwritten(self):
        first = self._build("source-window-immutable")
        self.assertEqual(first["status"], "completed")

        with self.assertRaises(SourceWindowSelectionError) as raised:
            self._build("source-window-immutable")

        self.assertEqual(raised.exception.code, "source_window.run_exists")

    def test_cli_requires_explicit_section_and_tier(self):
        args = build_parser().parse_args(
            [
                "source-window-selection",
                "--framework-run-id",
                "framework-run",
                "--section-index",
                "3",
                "--material-tier",
                "tier_2",
            ]
        )

        self.assertEqual(args.command, "source-window-selection")
        self.assertEqual(args.section_index, 3)
        self.assertEqual(args.material_tier, "tier_2")

    def test_cli_returns_zero_only_for_completed(self):
        argv = [
            "source-window-selection",
            "--framework-run-id",
            "framework-run",
            "--section-id",
            "section-body",
        ]
        with patch("main.SourceWindowSelectionBuilder") as builder:
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
        material_tier: str = "tier_1",
    ) -> dict:
        with self._patch_sources():
            return SourceWindowSelectionBuilder(self.workspace).build(
                framework_run_id=self.framework_source.run_id,
                section_id="section-body",
                material_tier=material_tier,
                run_id=run_id,
            )

    def _patch_sources(self):
        return _CombinedPatches(
            patch(
                "shipping_pipeline.source_window_selection."
                "load_review_framework_run",
                return_value=self.framework_source,
            ),
            patch(
                "shipping_pipeline.source_window_selection."
                "create_research_landscape_snapshot",
                return_value=self.landscape_snapshot,
            ),
        )

    def _make_source(
        self,
        paper_id: str,
        paper_title: str,
        method: str,
    ) -> ResearchLandscapeSource:
        material_id = f"{paper_id}:card-1"
        material = {
            "schema_version": "material.v2",
            "material_id": material_id,
            "paper_id": paper_id,
            "paper_title": paper_title,
            "content_kind": "prose",
            "clean_title": "研究结果",
            "extract": f"{paper_title}通过{method}提高通航能力。",
            "heading_path": ["研究结果"],
            "confidence_flags": [],
            "quality_flags": [],
            "source_span": {
                "path": "normalized/document.md",
                "start_line": 3,
                "end_line": 3,
                "start_char": 0,
                "end_char": 20,
            },
            "source_spans": [
                {
                    "path": "normalized/document.md",
                    "start_line": 3,
                    "end_line": 3,
                    "start_char": 0,
                    "end_char": 20,
                }
            ],
            "source_fingerprint": f"sha256:{paper_id}",
        }
        evidence_id = f"evidence-{paper_id}"
        evidence = {
            "evidence_unit_id": evidence_id,
            "claim": f"{paper_title}采用{method}。",
            "citations": [
                {
                    "material_id": material_id,
                    "quote": f"{method}提高通航能力",
                    "source_ref": copy.deepcopy(material["source_span"]),
                }
            ],
        }
        suffix = paper_id[-1]
        contribution_id = f"contribution-{suffix}"
        understanding = {
            "schema_version": "llm.paper_understanding.v1",
            "paper_id": paper_id,
            "paper_title": paper_title,
            "topic": "通航能力提升",
            "paper_relevance": "core",
            "research_questions": [
                {
                    "question": f"{method}如何提升能力？",
                    "problem_category": "方法评价",
                    "material_ids": [material_id],
                }
            ],
            "study_context": {
                "study_object": "三峡船闸",
                "data_sources": ["simulation"],
                "time_scope": "未说明",
                "geographic_scope": "三峡",
                "material_ids": [material_id],
            },
            "methods": [
                {
                    "method_category": "simulation",
                    "method_name": method,
                    "description": f"使用{method}。",
                    "material_ids": [material_id],
                }
            ],
            "contributions": [
                {
                    "contribution_id": contribution_id,
                    "statement": f"提出{method}方案。",
                    "result_type": "simulation_result",
                    "validation_level": "simulation",
                    "evidence_strength": "moderate",
                    "strength_rationale": "基于仿真。",
                    "material_ids": [material_id],
                    "evidence_unit_ids": [evidence_id],
                }
            ],
            "limitations": [
                {
                    "statement": "缺少现场验证。",
                    "basis": "reviewer_inferred",
                    "material_ids": [material_id],
                }
            ],
            "review_roles": [
                {
                    "role": "method_comparison",
                    "reason": "可比较方法。",
                    "contribution_indexes": [1],
                }
            ],
            "unresolved_questions": [],
            "keywords": [method],
            "understanding_id": f"understanding-{paper_id}",
        }
        return ResearchLandscapeSource(
            run_id=f"understanding-{paper_id}",
            paper_id=paper_id,
            paper_title=paper_title,
            topic="通航能力提升",
            generation_id=f"generation-{paper_id}",
            input_sha256=f"sha256:input-{paper_id}",
            manifest_sha256=f"sha256:manifest-{paper_id}",
            output_sha256=f"sha256:output-{paper_id}",
            understanding=understanding,
            materials=(material,),
            evidence_units=(evidence,),
        )

    def _make_landscape_snapshot(self) -> ResearchLandscapeSnapshot:
        return ResearchLandscapeSnapshot(
            topic="通航能力提升",
            review_goal="比较方法与证据边界",
            corpus_scope="targeted_sample",
            collection={
                "schema_version": "llm.research_landscape_collection.v1",
                "topic": "通航能力提升",
                "review_goal": "比较方法与证据边界",
                "corpus_scope": "targeted_sample",
                "source_understanding_run_ids": [
                    row.run_id for row in self.sources
                ],
            },
            sources=self.sources,
            input_sha256="sha256:landscape-input",
        )

    def _make_framework_source(self) -> ReviewFrameworkRunSource:
        references = {
            source.paper_id: {
                "paper_id": source.paper_id,
                "reference_id": f"ref-{source.paper_id[-1]}",
                "title": source.paper_title,
                "authors": ["作者"],
                "year": 2025,
                "entry_type": "article",
                "journal": "测试期刊",
                "institution": None,
                "degree": None,
                "doi": None,
                "status": "complete",
            }
            for source in self.sources
        }
        landscape = {
            "landscape_id": "landscape-one",
            "topic": "通航能力提升",
        }
        snapshot = ReviewFrameworkSnapshot(
            topic="通航能力提升",
            review_goal="比较方法与证据边界",
            corpus_scope="targeted_sample",
            landscape_run_id="landscape-run",
            landscape_manifest_sha256="sha256:landscape-manifest",
            landscape_output_sha256="sha256:landscape-output",
            landscape=landscape,
            reference_catalog_run_id="references-run",
            reference_manifest_sha256="sha256:reference-manifest",
            reference_catalog_sha256="sha256:references",
            reference_by_paper=references,
            input_sha256="sha256:framework-input",
        )
        framework = {
            "schema_version": "llm.review_framework.v1",
            "framework_id": "framework-one",
            "sections": [
                {
                    "section_index": 1,
                    "section_id": "section-body",
                    "title": "方法比较",
                    "section_type": "body",
                    "question": "两种方法如何提升能力？",
                    "purpose": "比较方法与证据。",
                    "dimension_indexes": [1],
                    "required_comparisons": ["比较调度与工程方法"],
                    "controversy_ids": [],
                    "corpus_gap_indexes": [],
                    "corpus_limitations": ["本次语料有限"],
                    "paper_ids": ["paper-a", "paper-b"],
                    "contribution_ids": [
                        "contribution-a",
                        "contribution-b",
                    ],
                    "citation_keys": ["ref_a", "ref_b"],
                    "bibliography_status": "complete",
                }
            ],
        }
        return ReviewFrameworkRunSource(
            run_id="framework-run",
            manifest_sha256="sha256:framework-manifest",
            output_sha256="sha256:framework-output",
            snapshot=snapshot,
            framework=framework,
        )

    def _write_landscape_manifest(self) -> None:
        path = (
            self.workspace
            / "_research_landscapes"
            / "runs"
            / "landscape-run"
            / "manifest.json"
        )
        self._write_json(
            path,
            {"input_sha256": self.landscape_snapshot.input_sha256},
        )

    def _write_understanding_run(
        self,
        source: ResearchLandscapeSource,
    ) -> None:
        run_dir = (
            self.workspace / "_paper_understandings" / "runs" / source.run_id
        )
        document = (
            f"# {source.paper_title}冻结全文\n\n"
            f"{source.understanding['contributions'][0]['statement']}\n"
        )
        (run_dir / "input").mkdir(parents=True, exist_ok=True)
        (run_dir / "output").mkdir(parents=True, exist_ok=True)
        (run_dir / "input" / "document.md").write_text(
            document,
            encoding="utf-8",
        )
        self._write_jsonl(
            run_dir / "input" / "materials.jsonl",
            source.materials,
        )
        self._write_jsonl(
            run_dir / "input" / "projected_cards.jsonl",
            project_cards(source.materials),
        )
        self._write_jsonl(
            run_dir / "input" / "evidence_units.jsonl",
            source.evidence_units,
        )
        self._write_json(
            run_dir / "output" / "paper_understanding.json",
            source.understanding,
        )
        self._write_json(
            run_dir / "manifest.json",
            {
                "run_id": source.run_id,
                "status": "completed",
            },
        )

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_jsonl(path: Path, rows) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False) + "\n" for row in rows
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    @staticmethod
    def _sha256(raw: bytes) -> str:
        return f"sha256:{hashlib.sha256(raw).hexdigest()}"


class _CombinedPatches:
    def __init__(self, *patches) -> None:
        self.patches = patches

    def __enter__(self):
        for value in self.patches:
            value.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        for value in reversed(self.patches):
            value.stop()
        return False


if __name__ == "__main__":
    unittest.main()
