from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import build_parser
from shipping_pipeline.end_to_end import (
    END_TO_END_COLLECTION_SCHEMA_VERSION,
    END_TO_END_RESULT_SCHEMA_VERSION,
    EndToEndPipelineInputError,
    EndToEndPipelineRunner,
)
from shipping_pipeline.models import PipelineResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
TOPIC = "三峡船舶积压疏导策略与长江航运组织"


def make_workspace() -> Path:
    path = TEST_TMP_ROOT / f"end_to_end_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def write_collection(root: Path, papers: list[dict[str, str]]) -> Path:
    path = root / "collection.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": END_TO_END_COLLECTION_SCHEMA_VERSION,
                "topic": TOPIC,
                "papers": papers,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class FakeCardRunner:
    def __init__(
        self,
        workspace: Path,
        statuses: dict[str, str] | None = None,
    ) -> None:
        self.workspace = workspace
        self.statuses = statuses or {}
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        source_path: str | Path,
        paper_id: str | None = None,
        workspace_id: str | None = None,
        review_topic: str = "",
    ) -> PipelineResult:
        assert paper_id is not None
        workspace_id = workspace_id or paper_id
        status = self.statuses.get(paper_id, "completed")
        self.calls.append(
            {
                "source": str(source_path),
                "paper_id": paper_id,
                "workspace_id": workspace_id,
                "topic": review_topic,
            }
        )
        paper_dir = self.workspace / workspace_id
        paper_dir.mkdir(parents=True, exist_ok=True)
        generation_id = f"generation-{paper_id}"
        (paper_dir / "run.json").write_text(
            json.dumps(
                {
                    "paper_id": paper_id,
                    "status": status,
                    "generation_id": generation_id,
                    "structure_quality": "green" if status == "completed" else "red",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return PipelineResult(
            paper_id=paper_id,
            status=status,
            structure_quality="green" if status == "completed" else "red",
            issue_count=0 if status == "completed" else 1,
            workspace_path=str(paper_dir),
        )


class FakeBriefRunner:
    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        self.statuses = statuses or {}
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        paper_id = str(kwargs["paper_id"])
        run_id = str(kwargs["run_id"])
        status = self.statuses.get(paper_id, "completed")
        return {
            "run_id": run_id,
            "status": status,
            "paper_id": paper_id,
            "failure_codes": [] if status != "failed" else ["brief.failed"],
            "run_dir": f"briefs/{run_id}",
            "manifest_path": f"briefs/{run_id}/manifest.json",
            "review_path": (
                None if status == "failed" else f"briefs/{run_id}/review.md"
            ),
            "request_count": 1,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
        }


class FakeSynthesisRunner:
    def __init__(self, status: str = "completed") -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []
        self.collection: dict[str, Any] | None = None

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        collection_path = Path(kwargs["collection_path"])
        self.collection = json.loads(collection_path.read_text(encoding="utf-8"))
        run_id = str(kwargs["run_id"])
        return {
            "run_id": run_id,
            "status": self.status,
            "failure_codes": [] if self.status == "completed" else ["outline.failed"],
            "run_dir": f"syntheses/{run_id}",
            "manifest_path": f"syntheses/{run_id}/manifest.json",
            "review_path": (
                f"syntheses/{run_id}/review/topic_synthesis.md"
                if self.status == "completed"
                else None
            ),
            "request_count": 2,
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 4,
                "total_tokens": 24,
            },
        }


class EndToEndPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = make_workspace()
        self.inputs = self.workspace / "sources"
        self.inputs.mkdir()
        for name in ("a.md", "b.md"):
            (self.inputs / name).write_text(f"# {name}\n\n正文。\n", encoding="utf-8")
        self.collection = write_collection(
            self.workspace,
            [
                {"paper_id": "paper-a", "source": "sources/a.md"},
                {"paper_id": "paper-b", "source": "sources/b.md"},
            ],
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _runner(
        self,
        *,
        card_statuses: dict[str, str] | None = None,
        brief_statuses: dict[str, str] | None = None,
        synthesis_status: str = "completed",
    ) -> tuple[
        EndToEndPipelineRunner,
        FakeCardRunner,
        FakeBriefRunner,
        FakeSynthesisRunner,
    ]:
        cards = FakeCardRunner(self.workspace, card_statuses)
        briefs = FakeBriefRunner(brief_statuses)
        synthesis = FakeSynthesisRunner(synthesis_status)
        return (
            EndToEndPipelineRunner(
                self.workspace,
                card_runner=cards,
                topic_review_runner=briefs,
                topic_synthesis_runner=synthesis,
            ),
            cards,
            briefs,
            synthesis,
        )

    def test_completed_run_links_all_stages_and_publishes_result(self):
        runner, cards, briefs, synthesis = self._runner(
            brief_statuses={"paper-b": "completed_with_revisit_failure"}
        )
        result = runner.run(
            collection_path=self.collection,
            run_id="pipeline-success",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["request_count"], 4)
        self.assertEqual(result["usage"]["total_tokens"], 48)
        self.assertEqual(len(cards.calls), 2)
        self.assertEqual(len(briefs.calls), 2)
        self.assertEqual(len(synthesis.calls), 1)
        self.assertEqual(
            synthesis.collection["source_run_ids"],
            [
                "pipeline-success--brief-001",
                "pipeline-success--brief-002",
            ],
        )
        run_dir = Path(result["run_dir"])
        output = json.loads(
            (run_dir / "output" / "pipeline_result.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            output["schema_version"],
            END_TO_END_RESULT_SCHEMA_VERSION,
        )
        self.assertEqual(output["included_paper_count"], 2)
        self.assertEqual(output["excluded_paper_count"], 0)
        self.assertEqual(output["request_count"], 4)
        self.assertEqual(output["usage"]["total_tokens"], 48)
        self.assertTrue((run_dir / "review" / "pipeline_summary.md").is_file())
        manifest = json.loads(
            (run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["stages"]["cards"]["status"], "completed")
        self.assertEqual(
            manifest["stages"]["topic_synthesis"]["status"],
            "completed",
        )
        self.assertEqual(manifest["request_count"], 4)
        self.assertEqual(
            manifest["usage"],
            {
                "prompt_tokens": 40,
                "completion_tokens": 8,
                "total_tokens": 48,
            },
        )

    def test_completed_with_failures_is_valid_for_synthesis(self):
        runner, _, briefs, synthesis = self._runner(
            brief_statuses={"paper-b": "completed_with_failures"}
        )

        result = runner.run(
            collection_path=self.collection,
            run_id="pipeline-partial-evidence",
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(briefs.calls), 2)
        self.assertEqual(len(synthesis.calls), 1)

    def test_card_failure_is_a_barrier_before_any_llm_call(self):
        runner, cards, briefs, synthesis = self._runner(
            card_statuses={"paper-b": "failed"}
        )
        result = runner.run(
            collection_path=self.collection,
            run_id="pipeline-card-failed",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(cards.calls), 2)
        self.assertEqual(briefs.calls, [])
        self.assertEqual(synthesis.calls, [])
        self.assertIn("pipeline.card_stage_failed", result["failure_codes"])
        self.assertFalse(
            (
                Path(result["run_dir"])
                / "output"
                / "pipeline_result.json"
            ).exists()
        )

    def test_workspace_identity_is_separate_from_logical_paper_id(self):
        self.collection = write_collection(
            self.workspace,
            [
                {
                    "paper_id": "paper-a",
                    "workspace_paper_id": "project-one--paper-a",
                    "source": "sources/a.md",
                },
                {
                    "paper_id": "paper-b",
                    "workspace_paper_id": "project-one--paper-b",
                    "source": "sources/b.md",
                },
            ],
        )
        runner, cards, briefs, _ = self._runner()

        result = runner.run(
            collection_path=self.collection,
            run_id="pipeline-isolated-identities",
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [row["workspace_id"] for row in cards.calls],
            ["project-one--paper-a", "project-one--paper-b"],
        )
        self.assertEqual(
            [row["paper_id"] for row in briefs.calls],
            ["paper-a", "paper-b"],
        )
        self.assertEqual(
            [row["workspace_paper_id"] for row in briefs.calls],
            ["project-one--paper-a", "project-one--paper-b"],
        )
        self.assertFalse((self.workspace / "paper-a").exists())

    def test_exclusion_below_two_papers_blocks_synthesis(self):
        runner, _, briefs, synthesis = self._runner(
            brief_statuses={"paper-b": "excluded"}
        )
        result = runner.run(
            collection_path=self.collection,
            run_id="pipeline-insufficient",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(briefs.calls), 2)
        self.assertEqual(synthesis.calls, [])
        self.assertIn(
            "pipeline.insufficient_included_papers",
            result["failure_codes"],
        )

    def test_existing_run_id_is_never_overwritten(self):
        runner, _, _, _ = self._runner()
        runner.run(
            collection_path=self.collection,
            run_id="pipeline-immutable",
        )
        with self.assertRaisesRegex(
            EndToEndPipelineInputError,
            "不能覆盖",
        ):
            runner.run(
                collection_path=self.collection,
                run_id="pipeline-immutable",
            )

    def test_cli_exposes_full_pipeline_command(self):
        args = build_parser().parse_args(
            [
                "full-pipeline",
                "--collection",
                "collection.json",
            ]
        )
        self.assertEqual(args.command, "full-pipeline")
        self.assertEqual(args.collection, Path("collection.json"))


if __name__ == "__main__":
    unittest.main()
