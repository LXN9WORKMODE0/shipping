from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_ui.app import create_app


class ShippingUIProjectAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / ".test_tmp" / f"ui-api-{uuid.uuid4().hex}"
        (self.root / "workspace").mkdir(parents=True)
        self.client = TestClient(create_app(self.root))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_import_and_read_project(self):
        created = self.client.post(
            "/api/projects",
            json={
                "project_id": "api-review",
                "name": "API 测试",
                "topic": "测试主题",
            },
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["revision"], 1)
        self.assertEqual(created.json()["paper_count"], 0)

        imported = self.client.post(
            "/api/projects/api-review/sources",
            data={
                "paper_ids": json.dumps(["paper-1"]),
                "expected_revision": "1",
            },
            files={"files": ("paper-1.md", b"# Paper 1\n", "text/markdown")},
        )
        self.assertEqual(imported.status_code, 200)
        payload = imported.json()
        self.assertEqual(payload["project"]["revision"], 2)
        self.assertEqual(payload["counts"]["imported"], 1)
        self.assertEqual(payload["results"][0]["status"], "imported")
        self.assertEqual(payload["summary"]["paper_count"], 1)
        self.assertEqual(payload["summary"]["papers"][0]["card"]["status"], "not_run")

        definition = self.client.get(
            "/api/projects/api-review/definition"
        )
        self.assertEqual(definition.status_code, 200)
        self.assertTrue(definition.json()["current_collection_path"])

    def test_import_returns_per_file_duplicate_results(self):
        self.client.post(
            "/api/projects",
            json={
                "project_id": "partial-review",
                "name": "部分导入",
                "topic": "测试主题",
            },
        )
        imported = self.client.post(
            "/api/projects/partial-review/sources",
            data={
                "paper_ids": json.dumps(["paper-1", "paper-2"]),
                "expected_revision": "1",
            },
            files=[
                ("files", ("paper-1.md", b"same", "text/markdown")),
                ("files", ("paper-2.md", b"same", "text/markdown")),
            ],
        )

        self.assertEqual(imported.status_code, 200)
        payload = imported.json()
        self.assertEqual(payload["summary"]["paper_count"], 1)
        self.assertEqual(
            payload["counts"],
            {"imported": 1, "skipped_duplicate": 1, "failed": 0},
        )
        self.assertEqual(
            payload["results"][1]["duplicate_of_paper_id"],
            "paper-1",
        )

    def test_revision_conflict_returns_stable_error_contract(self):
        self.client.post(
            "/api/projects",
            json={
                "project_id": "conflict-review",
                "name": "冲突测试",
                "topic": "测试主题",
            },
        )
        first = self.client.patch(
            "/api/projects/conflict-review",
            json={"expected_revision": 1, "description": "first"},
        )
        self.assertEqual(first.status_code, 200)

        conflict = self.client.patch(
            "/api/projects/conflict-review",
            json={"expected_revision": 1, "description": "stale"},
        )
        self.assertEqual(conflict.status_code, 422)
        self.assertEqual(
            conflict.json()["error_code"],
            "ui.project_revision_conflict",
        )

    def test_archive_filters_default_list_and_blocks_job_preflight(self):
        created = self.client.post(
            "/api/projects",
            json={
                "project_id": "archive-api",
                "name": "归档 API",
                "topic": "测试主题",
            },
        ).json()
        archived = self.client.post(
            "/api/projects/archive-api/archive",
            json={"expected_revision": created["revision"]},
        )
        self.assertEqual(archived.status_code, 200)
        self.assertTrue(archived.json()["archived"])

        active_list = self.client.get("/api/projects").json()
        self.assertFalse(
            any(row["project_id"] == "archive-api" for row in active_list)
        )
        full_list = self.client.get(
            "/api/projects?include_archived=true"
        ).json()
        self.assertTrue(
            any(row["project_id"] == "archive-api" for row in full_list)
        )

        blocked = self.client.post(
            "/api/projects/archive-api/card-jobs/preflight",
            json={
                "expected_revision": archived.json()["revision"],
                "paper_ids": ["paper-1"],
                "pdf_provider": "none",
            },
        )
        self.assertEqual(blocked.status_code, 422)
        self.assertEqual(blocked.json()["error_code"], "ui.project_archived")

        restored = self.client.post(
            "/api/projects/archive-api/restore",
            json={"expected_revision": archived.json()["revision"]},
        )
        self.assertEqual(restored.status_code, 200)
        self.assertFalse(restored.json()["archived"])

    def test_governance_summary_api_has_stable_schema(self):
        response = self.client.get("/api/governance/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["schema_version"],
            "review_ui_governance.v1",
        )
        self.assertEqual(payload["totals"]["job_count"], 0)
        self.assertIn(
            payload["configuration"]["overall_status"],
            {"ok", "warning", "error"},
        )
