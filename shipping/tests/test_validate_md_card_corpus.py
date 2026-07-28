from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validate_md_card_corpus.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_md_card_corpus",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ValidateMdCardCorpusTests(unittest.TestCase):
    def test_collection_loader_requires_unique_nonempty_paper_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            collection = Path(tmp) / "collection.json"
            collection.write_text(
                json.dumps(
                    {
                        "papers": [
                            {"paper_id": "paper-a"},
                            {"paper_id": "paper-b"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                MODULE._load_collection_paper_ids(collection),
                ["paper-a", "paper-b"],
            )

            collection.write_text(
                json.dumps(
                    {
                        "papers": [
                            {"paper_id": "paper-a"},
                            {"paper_id": "paper-a"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "重复 paper_id"):
                MODULE._load_collection_paper_ids(collection)

    def test_selected_collection_reports_missing_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "paper-a").mkdir()
            failures = MODULE.validate(
                workspace,
                selected_paper_ids=["paper-a", "paper-b"],
            )
            self.assertIn(
                "paper-a: 清单论文缺少可验收的当前结构产物。",
                failures,
            )
            self.assertIn(
                "paper-b: 清单论文缺少可验收的当前结构产物。",
                failures,
            )


if __name__ == "__main__":
    unittest.main()
