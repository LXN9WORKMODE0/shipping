from __future__ import annotations

import unittest

import main


class ReviewConclusionB2CliTest(unittest.TestCase):
    def test_cli_exposes_conclusion_command(self) -> None:
        args = main.build_parser().parse_args(
            [
                "llm-review-conclusion-b2",
                "--audited-assembly-run-id",
                "audited-run",
            ]
        )
        self.assertEqual(args.command, "llm-review-conclusion-b2")
        self.assertEqual(args.audited_assembly_run_id, "audited-run")

    def test_cli_exposes_conclusion_audit_command(self) -> None:
        args = main.build_parser().parse_args(
            [
                "llm-review-conclusion-audit-b2",
                "--conclusion-run-id",
                "conclusion-run",
            ]
        )
        self.assertEqual(args.command, "llm-review-conclusion-audit-b2")
        self.assertEqual(args.conclusion_run_id, "conclusion-run")

    def test_cli_exposes_final_assembly_command(self) -> None:
        args = main.build_parser().parse_args(
            ["assemble-review-b2-final", "--release-file", "release.json"]
        )
        self.assertEqual(args.command, "assemble-review-b2-final")
