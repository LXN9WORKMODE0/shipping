from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import main
from shipping_pipeline.paper_pool_inventory import (
    PaperPoolInventoryRunner,
    load_paper_pool_inventory,
)


class PaperPoolInventoryTest(unittest.TestCase):
    def test_inventory_accounts_for_duplicates_unsupported_and_exact_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = root / "papers"
            workspace = root / "workspace"
            sources.mkdir()
            (sources / "甲.pdf").write_bytes(b"paper-a")
            (sources / "甲_副本.pdf").write_bytes(b"paper-a")
            (sources / "乙.pdf").write_bytes(b"paper-b")
            (sources / "丙.caj").write_bytes(b"paper-c")

            card_dir = workspace / "existing-card"
            (card_dir / "materials").mkdir(parents=True)
            managed = root / "managed.pdf"
            managed.write_bytes(b"paper-b")
            (card_dir / "run.json").write_text(
                json.dumps({
                    "paper_id": "乙",
                    "status": "completed",
                    "source": str(managed),
                    "structure_quality": "gold",
                    "generation_id": "generation-1",
                }), encoding="utf-8"
            )
            (card_dir / "materials" / "current.json").write_text(
                json.dumps({"status": "completed", "generation_id": "generation-1"}),
                encoding="utf-8",
            )

            result = PaperPoolInventoryRunner(workspace).run(
                sources, run_id="inventory-test"
            )
            self.assertEqual(result["status"], "completed")
            summary = result["summary"]
            self.assertEqual(summary["source_file_count"], 4)
            self.assertEqual(summary["unique_content_count"], 3)
            self.assertEqual(summary["duplicate_alias_count"], 1)
            self.assertEqual(summary["canonical_ready_count"], 1)
            self.assertEqual(summary["canonical_needs_card_count"], 1)
            self.assertEqual(summary["canonical_blocked_count"], 1)
            loaded = load_paper_pool_inventory(workspace, "inventory-test")
            self.assertEqual(loaded["summary"], summary)

    def test_cli_exposes_inventory_command(self) -> None:
        args = main.build_parser().parse_args(
            ["paper-pool-inventory", "papers", "--run-id", "inventory"]
        )
        self.assertEqual(args.command, "paper-pool-inventory")
        self.assertEqual(args.source_dir, Path("papers"))
