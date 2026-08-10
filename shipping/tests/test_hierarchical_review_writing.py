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


if __name__ == "__main__":
    unittest.main()
