from __future__ import annotations

import unittest
from pathlib import Path

import main


class PaperPoolCardPreparationCliTest(unittest.TestCase):
    def test_cli_exposes_card_preparation(self) -> None:
        args = main.build_parser().parse_args([
            "paper-pool-prepare-cards",
            "--inventory-run-id", "inventory",
            "--topic", "主题",
            "--max-papers", "10",
            "--pdf-provider", "mineru",
        ])
        self.assertEqual(args.command, "paper-pool-prepare-cards")
        self.assertEqual(args.max_papers, 10)
        self.assertEqual(args.pdf_provider, "mineru")
        self.assertEqual(args.workspace, Path("workspace"))
