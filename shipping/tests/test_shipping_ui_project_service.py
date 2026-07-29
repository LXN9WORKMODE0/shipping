from __future__ import annotations

import io
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_ui.errors import UIError
from shipping_ui.job_repository import JobRepository
from shipping_ui.project_repository import ProjectRepository
from shipping_ui.services.project_service import ProjectService, SourceInput


class ShippingUIProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / ".test_tmp" / f"ui-project-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        (self.root / "workspace").mkdir()
        self.repository = ProjectRepository(self.root)
        self.service = ProjectService(self.repository)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_import_and_freeze_collection(self):
        project = self.service.create_project(
            project_id="test-review",
            name="测试综述",
            topic="测试主题",
        )
        result = self.service.import_sources(
            "test-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-1",
                    filename="paper-1.md",
                    stream=io.BytesIO(b"# Paper 1\n\nContent."),
                ),
                SourceInput(
                    paper_id="paper-2",
                    filename="paper-2.pdf",
                    stream=io.BytesIO(b"%PDF-test"),
                ),
            ],
        )

        updated = result["project"]
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(len(updated["papers"]), 2)
        self.assertEqual(
            result["counts"],
            {"imported": 2, "skipped_duplicate": 0, "failed": 0},
        )
        self.assertTrue(
            all(row["status"] == "imported" for row in result["results"])
        )
        self.assertTrue(
            all(
                row["workspace_paper_id"].startswith("test-review--")
                for row in updated["papers"]
            )
        )
        collection_path = (
            self.repository.project_dir("test-review")
            / updated["current_collection_path"]
        )
        collection = json.loads(collection_path.read_text(encoding="utf-8"))
        self.assertEqual(collection["schema_version"], "review_pipeline_collection.v1")
        self.assertEqual(collection["topic"], "测试主题")
        self.assertEqual(len(collection["papers"]), 2)
        self.assertTrue(
            all(
                row["paper_id"].startswith("test-review--")
                for row in collection["papers"]
            )
        )
        self.assertTrue(all(Path(row["source"]).is_absolute() for row in collection["papers"]))

    def test_duplicate_content_is_reported_without_rejecting_batch(self):
        project = self.service.create_project(
            project_id="duplicate-review",
            name="重复来源测试",
            topic="测试主题",
        )
        result = self.service.import_sources(
            "duplicate-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-a",
                    filename="a.md",
                    stream=io.BytesIO(b"same"),
                ),
                SourceInput(
                    paper_id="paper-b",
                    filename="b.md",
                    stream=io.BytesIO(b"same"),
                ),
            ],
        )
        current = self.repository.get("duplicate-review")
        self.assertEqual([row["paper_id"] for row in current["papers"]], ["paper-a"])
        self.assertEqual(
            result["counts"],
            {"imported": 1, "skipped_duplicate": 1, "failed": 0},
        )
        duplicate = result["results"][1]
        self.assertEqual(duplicate["status"], "skipped_duplicate")
        self.assertEqual(duplicate["duplicate_of_paper_id"], "paper-a")
        self.assertEqual(
            len(
                list(
                    self.repository.project_dir("duplicate-review")
                    .joinpath("sources")
                    .iterdir()
                )
            ),
            1,
        )

    def test_invalid_items_do_not_block_valid_sources(self):
        project = self.service.create_project(
            project_id="partial-import",
            name="部分导入测试",
            topic="测试主题",
        )
        result = self.service.import_sources(
            "partial-import",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-ok",
                    filename="paper.md",
                    stream=io.BytesIO(b"# Paper"),
                ),
                SourceInput(
                    paper_id="",
                    filename="missing-id.md",
                    stream=io.BytesIO(b"# Missing"),
                ),
                SourceInput(
                    paper_id="empty",
                    filename="empty.md",
                    stream=io.BytesIO(b""),
                ),
            ],
        )

        self.assertEqual(result["project"]["revision"], 2)
        self.assertEqual(
            result["counts"],
            {"imported": 1, "skipped_duplicate": 0, "failed": 2},
        )
        self.assertEqual(
            [row["error_code"] for row in result["results"][1:]],
            ["ui.paper_id_required", "ui.source_empty"],
        )

    def test_all_skipped_import_does_not_advance_revision(self):
        project = self.service.create_project(
            project_id="all-skipped",
            name="全重复测试",
            topic="测试主题",
        )
        first = self.service.import_sources(
            "all-skipped",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-a",
                    filename="a.md",
                    stream=io.BytesIO(b"same"),
                )
            ],
        )
        second = self.service.import_sources(
            "all-skipped",
            expected_revision=first["project"]["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-b",
                    filename="b.md",
                    stream=io.BytesIO(b"same"),
                )
            ],
        )

        self.assertEqual(
            second["project"]["revision"], first["project"]["revision"]
        )
        self.assertEqual(second["counts"]["skipped_duplicate"], 1)

    def test_revision_conflict_is_explicit(self):
        project = self.service.create_project(
            project_id="revision-review",
            name="代际测试",
            topic="测试主题",
        )
        self.repository.update_metadata(
            "revision-review",
            expected_revision=project["revision"],
            description="已更新",
        )
        with self.assertRaisesRegex(UIError, "项目已被修改"):
            self.repository.update_metadata(
                "revision-review",
                expected_revision=project["revision"],
                description="过时写入",
            )

    def test_topic_update_publishes_matching_collection_snapshot(self):
        project = self.service.create_project(
            project_id="topic-review",
            name="主题变更测试",
            topic="旧主题",
        )
        imported = self.service.import_sources(
            "topic-review",
            expected_revision=project["revision"],
            sources=[
                SourceInput(
                    paper_id="paper-1",
                    filename="paper-1.md",
                    stream=io.BytesIO(b"# Paper 1\n"),
                )
            ],
        )["project"]
        old_collection = imported["current_collection_path"]

        updated = self.repository.update_metadata(
            "topic-review",
            expected_revision=imported["revision"],
            topic="新主题",
        )

        self.assertEqual(updated["revision"], 3)
        self.assertNotEqual(updated["current_collection_path"], old_collection)
        collection_path = (
            self.repository.project_dir("topic-review")
            / updated["current_collection_path"]
        )
        collection = json.loads(collection_path.read_text(encoding="utf-8"))
        self.assertEqual(collection["topic"], "新主题")

    def test_metadata_noop_does_not_advance_revision(self):
        project = self.service.create_project(
            project_id="noop-review",
            name="无变化测试",
            topic="测试主题",
        )
        current = self.repository.update_metadata(
            "noop-review",
            expected_revision=project["revision"],
            name=project["name"],
            topic=project["topic"],
            description=project["description"],
        )
        self.assertEqual(current["revision"], project["revision"])

    def test_archive_is_read_only_and_restore_reopens_project(self):
        project = self.service.create_project(
            project_id="archive-review",
            name="归档测试",
            topic="测试主题",
        )
        archived = self.service.set_archived(
            "archive-review",
            expected_revision=project["revision"],
            archived=True,
        )
        self.assertTrue(archived["archived_at"])
        self.assertEqual(archived["revision"], 2)

        with self.assertRaisesRegex(UIError, "项目已归档"):
            self.repository.update_metadata(
                "archive-review",
                expected_revision=archived["revision"],
                description="不能修改",
            )

        restored = self.service.set_archived(
            "archive-review",
            expected_revision=archived["revision"],
            archived=False,
        )
        self.assertIsNone(restored["archived_at"])
        updated = self.repository.update_metadata(
            "archive-review",
            expected_revision=restored["revision"],
            description="恢复后可以修改",
        )
        self.assertEqual(updated["description"], "恢复后可以修改")

    def test_active_job_blocks_archive(self):
        jobs = JobRepository(self.root)
        service = ProjectService(self.repository, jobs)
        project = service.create_project(
            project_id="active-review",
            name="活动任务测试",
            topic="测试主题",
        )
        jobs.create(
            {
                "schema_version": "review_ui_card_job_input.v1",
                "project_id": "active-review",
                "project_revision": project["revision"],
                "topic": project["topic"],
                "papers": [{"paper_id": "paper-1"}],
            }
        )

        with self.assertRaisesRegex(UIError, "仍有活动 Job"):
            service.set_archived(
                "active-review",
                expected_revision=project["revision"],
                archived=True,
            )


if __name__ == "__main__":
    unittest.main()
