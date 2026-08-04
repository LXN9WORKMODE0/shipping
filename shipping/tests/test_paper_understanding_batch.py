from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

import main
from shipping_pipeline.paper_understanding_batch import PaperUnderstandingBatchRunner


TEST_TMP_ROOT = Path(__file__).resolve().parent / ".tmp"


class FakePaperRunner:
    def __init__(self, workspace: Path, *, fail_paper_id: str | None = None) -> None:
        self.workspace = workspace
        self.fail_paper_id = fail_paper_id
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        failed = kwargs["paper_id"] == self.fail_paper_id
        run_dir = self.workspace / "_paper_understandings" / "runs" / kwargs["run_id"]
        run_dir.mkdir(parents=True)
        manifest = {
            "status": "failed" if failed else "completed",
            "generation_id": f"generation-{kwargs['paper_id']}",
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return {
            "run_id": kwargs["run_id"],
            "status": manifest["status"],
            "paper_id": kwargs["paper_id"],
            "workspace_paper_id": kwargs["workspace_paper_id"],
            "run_dir": str(run_dir),
            "failure": {"error_code": "test.failure"} if failed else None,
        }


class RaisingPaperRunner(FakePaperRunner):
    def run(self, **kwargs):
        if kwargs["paper_id"] == "paper-two":
            raise RuntimeError("意外失败")
        return super().run(**kwargs)


class PaperUnderstandingBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TEST_TMP_ROOT / f"understanding_batch_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self._write_screening()

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_cli_defaults_to_core(self) -> None:
        args = main.build_parser().parse_args([
            "llm-paper-understanding-batch", "--screening-run-id", "screening-one"
        ])
        self.assertEqual(args.command, "llm-paper-understanding-batch")
        self.assertIsNone(args.relevance)
        self.assertIsNone(args.record_id)

    def test_batch_isolates_failure_and_keeps_non_selected_visible(self) -> None:
        fake = FakePaperRunner(self.workspace, fail_paper_id="paper-two")
        result = PaperUnderstandingBatchRunner(
            self.workspace,
            paper_runner=fake,
            screening_loader=self._load_screening,
        ).run(
            screening_run_id="screening-one",
            run_id="batch-one",
            relevance=("core", "supporting"),
        )

        self.assertEqual(result["status"], "completed_with_failures")
        self.assertEqual(len(fake.calls), 2)
        output = self._read_output("batch-one")
        self.assertEqual(output["summary"]["understood_count"], 1)
        self.assertEqual(output["summary"]["understanding_failed_count"], 1)
        self.assertEqual(output["summary"]["not_selected_count"], 1)
        self.assertEqual(output["successful_understanding_run_ids"], ["batch-one--paper-0001"])

    def test_partial_run_can_resume_without_recalling_completed_paper(self) -> None:
        first = FakePaperRunner(self.workspace)
        PaperUnderstandingBatchRunner(
            self.workspace,
            paper_runner=first,
            screening_loader=self._load_screening,
        ).run(
            screening_run_id="screening-one",
            run_id="batch-partial",
            relevance=("core", "supporting"),
            max_papers=1,
        )
        second = FakePaperRunner(self.workspace)
        result = PaperUnderstandingBatchRunner(
            self.workspace,
            paper_runner=second,
            screening_loader=self._load_screening,
        ).run(
            screening_run_id="screening-one",
            run_id="batch-resumed",
            relevance=("core", "supporting"),
            resume_from_run_id="batch-partial",
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual([call["paper_id"] for call in second.calls], ["paper-two"])
        self.assertEqual(self._read_output("batch-resumed")["summary"]["understood_count"], 2)

    def test_unexpected_child_exception_does_not_abort_batch(self) -> None:
        fake = RaisingPaperRunner(self.workspace)
        result = PaperUnderstandingBatchRunner(
            self.workspace,
            paper_runner=fake,
            screening_loader=self._load_screening,
        ).run(
            screening_run_id="screening-one",
            run_id="batch-exception",
            relevance=("core", "supporting"),
        )

        self.assertEqual(result["status"], "completed_with_failures")
        records = self._read_output("batch-exception")["records"]
        failed = next(row for row in records if row["record_id"] == "record-2")
        self.assertEqual(failed["failure"]["error_code"], "RuntimeError")

    def test_explicit_record_ids_preserve_requested_order(self) -> None:
        fake = FakePaperRunner(self.workspace)
        PaperUnderstandingBatchRunner(
            self.workspace,
            paper_runner=fake,
            screening_loader=self._load_screening,
        ).run(
            screening_run_id="screening-one",
            run_id="batch-explicit",
            relevance=("core", "supporting"),
            record_ids=("record-2", "record-1"),
        )

        self.assertEqual(
            [call["paper_id"] for call in fake.calls],
            ["paper-two", "paper-one"],
        )

    def test_explicit_record_id_outside_relevance_is_rejected(self) -> None:
        with self.assertRaisesRegex(Exception, "record_unavailable"):
            PaperUnderstandingBatchRunner(
                self.workspace,
                paper_runner=FakePaperRunner(self.workspace),
                screening_loader=self._load_screening,
            ).run(
                screening_run_id="screening-one",
                run_id="batch-invalid-record",
                relevance=("core",),
                record_ids=("record-2",),
            )

    def _write_screening(self) -> None:
        root = self.workspace / "_paper_pool_screenings" / "runs" / "screening-one"
        (root / "output").mkdir(parents=True)
        (root / "manifest.json").write_text(
            json.dumps({"status": "completed"}), encoding="utf-8"
        )
        records = []
        for index, relevance in enumerate(("core", "supporting", "peripheral"), start=1):
            paper_id = f"paper-{'one' if index == 1 else 'two' if index == 2 else 'three'}"
            screening_run_id = f"screen-{index}"
            records.append({
                "record_id": f"record-{index}",
                "relative_path": f"paper-{index}.pdf",
                "screening_status": "screened",
                "screening_run_id": screening_run_id,
                "paper_relevance": relevance,
            })
            child = self.workspace / "_paper_topic_screenings" / "runs" / screening_run_id
            (child / "output").mkdir(parents=True)
            screening = {
                "paper_id": paper_id,
                "workspace_paper_id": f"workspace-{index}",
                "generation_id": f"generation-{paper_id}",
                "paper_relevance": relevance,
            }
            (child / "manifest.json").write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )
            (child / "output" / "paper_topic_screening.json").write_text(
                json.dumps(screening), encoding="utf-8"
            )
        output = {
            "run_id": "screening-one",
            "topic": "三峡枢纽通航能力提升",
            "records": records,
        }
        (root / "output" / "paper_pool_screening.json").write_text(
            json.dumps(output), encoding="utf-8"
        )

    def _read_output(self, run_id: str):
        path = self.workspace / "_paper_understanding_batches" / "runs" / run_id / "output" / "paper_understanding_batch.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_screening(self, workspace: Path, run_id: str):
        path = workspace / "_paper_topic_screenings" / "runs" / run_id / "output" / "paper_topic_screening.json"
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
