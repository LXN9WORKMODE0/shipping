from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import main
from shipping_pipeline.paper_pool_card_preparation import (
    PaperPoolCardPreparationRunner,
    request_paper_pool_card_action,
)
from shipping_pipeline.paper_pool_inventory import PaperPoolInventoryRunner


class FakeCardRunner:
    def __init__(
        self,
        workspace: Path,
        *,
        delay_seconds: float = 0.0,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.workspace = workspace
        self.delay_seconds = delay_seconds
        self.entered = entered
        self.release = release
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0
        self.start_times: list[float] = []

    def run(
        self,
        source: Path,
        *,
        paper_id: str,
        workspace_id: str,
        review_topic: str,
    ) -> SimpleNamespace:
        del source, review_topic
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            self.start_times.append(time.monotonic())
        try:
            if self.entered is not None:
                self.entered.set()
            if self.release is not None:
                self.release.wait(timeout=5)
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
            generation_id = f"generation-{workspace_id}"
            paper_dir = self.workspace / workspace_id
            (paper_dir / "materials").mkdir(parents=True, exist_ok=True)
            (paper_dir / "run.json").write_text(
                json.dumps({
                    "paper_id": paper_id,
                    "status": "completed",
                    "generation_id": generation_id,
                    "structure_quality": "gold",
                    "stages": {"material": {"material_count": 2}},
                }),
                encoding="utf-8",
            )
            (paper_dir / "materials" / "current.json").write_text(
                json.dumps({
                    "status": "completed",
                    "generation_id": generation_id,
                }),
                encoding="utf-8",
            )
            return SimpleNamespace(
                status="completed",
                structure_quality="gold",
                issue_count=0,
            )
        finally:
            with self._lock:
                self._active -= 1


class PaperPoolCardPreparationTest(unittest.TestCase):
    def test_cli_exposes_card_preparation_and_control(self) -> None:
        args = main.build_parser().parse_args([
            "paper-pool-prepare-cards",
            "--inventory-run-id", "inventory",
            "--topic", "主题",
            "--max-papers", "10",
            "--max-workers", "3",
            "--min-start-interval-seconds", "2.5",
            "--pdf-provider", "mineru",
        ])
        self.assertEqual(args.command, "paper-pool-prepare-cards")
        self.assertEqual(args.max_papers, 10)
        self.assertEqual(args.max_workers, 3)
        self.assertEqual(args.min_start_interval_seconds, 2.5)
        self.assertEqual(args.pdf_provider, "mineru")
        self.assertEqual(args.workspace, Path("workspace"))

        control = main.build_parser().parse_args([
            "paper-pool-card-control",
            "--run-id", "batch-1",
            "--action", "pause",
        ])
        self.assertEqual(control.command, "paper-pool-card-control")
        self.assertEqual(control.action, "pause")

    def test_concurrency_is_bounded_and_every_result_is_checkpointed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace, inventory_id = self._inventory(root, count=5)
            fake = FakeCardRunner(workspace, delay_seconds=0.05)
            result = PaperPoolCardPreparationRunner(
                workspace, card_runner=fake
            ).run(
                inventory_run_id=inventory_id,
                topic="主题",
                run_id="batch-concurrent",
                max_workers=2,
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["summary"]["completed_count"], 5)
            self.assertEqual(fake.max_active, 2)
            checkpoint = self._read_jsonl(
                workspace
                / "_paper_pool_card_preparations"
                / "runs"
                / "batch-concurrent"
                / "runs"
                / "card_runs.jsonl"
            )
            self.assertEqual(len(checkpoint), 5)
            self.assertTrue(all(row["status"] == "completed" for row in checkpoint))

    def test_pause_stops_new_starts_and_checkpoint_resumes_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace, inventory_id = self._inventory(root, count=3)
            entered = threading.Event()
            release = threading.Event()
            first_fake = FakeCardRunner(
                workspace,
                entered=entered,
                release=release,
            )
            holder: dict[str, object] = {}

            def run_batch() -> None:
                holder["result"] = PaperPoolCardPreparationRunner(
                    workspace, card_runner=first_fake
                ).run(
                    inventory_run_id=inventory_id,
                    topic="主题",
                    run_id="batch-paused",
                    max_workers=1,
                )

            thread = threading.Thread(target=run_batch)
            thread.start()
            self.assertTrue(entered.wait(timeout=3))
            request_paper_pool_card_action(
                workspace,
                run_id="batch-paused",
                action="pause",
            )
            release.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            paused = holder["result"]
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(paused["summary"]["attempted_count"], 1)
            self.assertEqual(paused["summary"]["unstarted_selected_count"], 2)

            old_dir = (
                workspace
                / "_paper_pool_card_preparations"
                / "runs"
                / "batch-paused"
            )
            (old_dir / "output" / "paper_pool_card_preparation.json").unlink()
            resumed = PaperPoolCardPreparationRunner(
                workspace,
                card_runner=FakeCardRunner(workspace),
            ).run(
                inventory_run_id=inventory_id,
                topic="主题",
                run_id="batch-resumed",
                resume_from_run_id="batch-paused",
                max_workers=2,
            )
            self.assertEqual(resumed["status"], "completed")
            self.assertEqual(resumed["summary"]["reused_count"], 1)
            self.assertEqual(resumed["summary"]["attempted_count"], 2)
            self.assertEqual(resumed["summary"]["completed_count"], 3)

    def test_start_interval_limits_submission_burst(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace, inventory_id = self._inventory(root, count=3)
            fake = FakeCardRunner(workspace, delay_seconds=0.01)
            result = PaperPoolCardPreparationRunner(
                workspace, card_runner=fake
            ).run(
                inventory_run_id=inventory_id,
                topic="主题",
                run_id="batch-rate-limited",
                max_workers=3,
                min_start_interval_seconds=0.04,
            )
            self.assertEqual(result["status"], "completed")
            intervals = [
                later - earlier
                for earlier, later in zip(fake.start_times, fake.start_times[1:])
            ]
            self.assertTrue(all(interval >= 0.03 for interval in intervals), intervals)

    def test_source_change_is_exposed_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace, inventory_id = self._inventory(root, count=1)
            source = root / "papers" / "论文-0.md"
            source.write_text("changed after inventory", encoding="utf-8")
            result = PaperPoolCardPreparationRunner(
                workspace,
                card_runner=FakeCardRunner(workspace),
            ).run(
                inventory_run_id=inventory_id,
                topic="主题",
                run_id="batch-source-changed",
            )
            self.assertEqual(result["status"], "completed_with_failures")
            checkpoint = self._read_jsonl(
                workspace
                / "_paper_pool_card_preparations"
                / "runs"
                / "batch-source-changed"
                / "runs"
                / "card_runs.jsonl"
            )
            self.assertEqual(
                checkpoint[0]["failure"]["error_code"],
                "paper_pool_card.source_identity_changed",
            )

    def test_real_pipeline_concurrency_publishes_complete_corpus_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace, inventory_id = self._inventory(root, count=3)
            for source in (root / "papers").glob("*.md"):
                source.write_text(
                    f"# {source.stem}\n\n## 摘要\n\n摘要证据。\n\n"
                    "## 1、方法\n\n正文证据。\n\n## 参考文献\n\n[1] 文献。\n",
                    encoding="utf-8",
                )
            refreshed = PaperPoolInventoryRunner(workspace).run(
                root / "papers",
                run_id="inventory-real-pipeline",
            )
            self.assertEqual(refreshed["status"], "completed")

            result = PaperPoolCardPreparationRunner(workspace).run(
                inventory_run_id="inventory-real-pipeline",
                topic="主题",
                run_id="batch-real-pipeline",
                max_workers=2,
            )
            checkpoint = self._read_jsonl(
                workspace
                / "_paper_pool_card_preparations"
                / "runs"
                / "batch-real-pipeline"
                / "runs"
                / "card_runs.jsonl"
            )
            self.assertEqual(result["status"], "completed", checkpoint)
            papers = self._read_jsonl(workspace / "_corpus" / "papers.jsonl")
            materials = self._read_jsonl(workspace / "_corpus" / "materials.jsonl")
            self.assertEqual(len(papers), 3)
            self.assertEqual({row["status"] for row in papers}, {"completed"})
            self.assertEqual(len({row["paper_id"] for row in materials}), 3)

    @staticmethod
    def _inventory(root: Path, *, count: int) -> tuple[Path, str]:
        sources = root / "papers"
        workspace = root / "workspace"
        sources.mkdir()
        for index in range(count):
            (sources / f"论文-{index}.md").write_text(
                f"# 论文 {index}\n\n正文 {index}\n",
                encoding="utf-8",
            )
        inventory_id = "inventory-test"
        result = PaperPoolInventoryRunner(workspace).run(
            sources,
            run_id=inventory_id,
        )
        if result["status"] != "completed":
            raise AssertionError(result)
        return workspace, inventory_id

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
