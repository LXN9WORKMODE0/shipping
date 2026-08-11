from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import main
from shipping_pipeline.hierarchical_review_writing import (
    HierarchicalReviewWritingRunner,
    MAX_BODY_CHARS,
    MIN_BODY_CHARS,
    _calibrate_evidence_language,
    _restore_citation_names,
    _schema,
)


class HierarchicalReviewWritingTests(unittest.TestCase):
    def test_cli_exposes_hierarchical_review_writer(self) -> None:
        args = main.build_parser().parse_args([
            "hierarchical-review-write",
            "--global-run-id",
            "global-one",
        ])
        self.assertEqual(args.command, "hierarchical-review-write")
        self.assertEqual(args.global_run_id, "global-one")

    def test_scale49_source_replay_contains_every_local_paper(self) -> None:
        global_run = (
            PROJECT_ROOT
            / "workspace"
            / "_hierarchical_global_landscapes"
            / "runs"
            / "hierarchical-incremental-scale48-20260810-v2--global"
        )
        if not global_run.is_dir():
            self.skipTest("本地扩容验证产物不存在。")
        source = HierarchicalReviewWritingRunner(
            PROJECT_ROOT / "workspace"
        )._load_source(global_run.name)
        self.assertEqual(len(source["local_landscapes"]), 7)
        self.assertEqual(len(source["papers"]), 49)
        self.assertEqual(len(source["citation_map"]), 49)
        self.assertEqual(
            len({row["citation_key"] for row in source["citation_map"]}),
            49,
        )
        self.assertGreater(len(json.dumps(source, ensure_ascii=False)), 400000)

    def test_citation_aliases_are_restored_to_human_readable_titles(self) -> None:
        value = {
            "introduction": [{"text": "P001与P002采用不同方法。", "citation_keys": ["P001", "P002"]}],
            "chapters": [],
            "discussion": [],
            "conclusion": [],
        }
        source = {"citation_map": [
            {"citation_key": "P001", "paper_title": "论文甲"},
            {"citation_key": "P002", "paper_title": "论文乙"},
        ]}
        self.assertEqual(_restore_citation_names(value, source), 2)
        self.assertEqual(value["introduction"][0]["text"], "《论文甲》与《论文乙》采用不同方法。")

    def test_paragraph_schema_accepts_concise_chinese_paragraphs(self) -> None:
        self.assertEqual((MIN_BODY_CHARS, MAX_BODY_CHARS), (8000, 24000))
        schema = _schema({"global_dimensions": [{"title": "主题一"}]})
        paragraph = schema["properties"]["conclusion"]["items"]
        self.assertEqual(paragraph["properties"]["text"]["minLength"], 80)
        self.assertEqual(paragraph["properties"]["citation_keys"]["maxItems"], 24)
        self.assertEqual(paragraph["properties"]["citation_keys"]["minItems"], 0)
        self.assertIn("P[0-9]{3}", paragraph["properties"]["text"]["pattern"])
        self.assertIn("根本原因", paragraph["properties"]["text"]["pattern"])
        self.assertEqual(
            schema["properties"]["discussion"]["items"]["properties"]["citation_keys"]["minItems"],
            1,
        )

    def test_evidence_language_calibration_is_counted_and_auditable(self) -> None:
        value = {
            "abstract": "该方法被证明有效，但不是根本途径。",
            "introduction": [{"text": "部分研究普遍称其为根本原因。", "citation_keys": ["P001"]}],
            "chapters": [],
            "discussion": [],
            "conclusion": [],
        }
        self.assertEqual(
            _calibrate_evidence_language(value),
            {"被证明": 1, "根本途径": 1, "根本原因": 1, "普遍": 1},
        )
        self.assertNotIn("证明", value["abstract"])
        self.assertNotIn("普遍", value["introduction"][0]["text"])


if __name__ == "__main__":
    unittest.main()
