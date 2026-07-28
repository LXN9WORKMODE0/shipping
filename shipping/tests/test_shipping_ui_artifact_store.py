from __future__ import annotations

import unittest
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_ui.artifact_store import ArtifactStore


class ShippingUIArtifactStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store = ArtifactStore(PROJECT_ROOT)

    def test_real_project_summary_matches_validated_corpus(self):
        project = self.store.get_project("adversarial-8papers-20260728")

        self.assertEqual(project["paper_count"], 8)
        self.assertEqual(project["card_count"], 957)
        self.assertEqual(project["evidence_count"], 29)
        self.assertEqual(
            project["analysis_status_counts"],
            {
                "completed": 3,
                "completed_with_failures": 5,
            },
        )
        self.assertEqual(project["synthesis"]["theme_count"], 6)
        self.assertEqual(project["synthesis"]["synthesis_unit_count"], 10)
        self.assertEqual(project["synthesis"]["section_count"], 8)

    def test_paper_detail_joins_failure_to_frozen_card(self):
        paper = self.store.get_paper(
            "adversarial-8papers-20260728",
            "三峡水利枢纽货运过闸_翻坝线路优选模型及其应用",
        )

        self.assertGreater(len(paper["document"]["lines"]), 100)
        self.assertEqual(len(paper["cards"]), 19)
        self.assertEqual(len(paper["evidence"]), 2)
        self.assertEqual(len(paper["failures"]), 4)
        self.assertTrue(all(row["material"] for row in paper["failures"]))

    def test_synthesis_exposes_evidence_catalog_and_coverage(self):
        synthesis = self.store.get_synthesis("adversarial-8papers-20260728")

        self.assertEqual(len(synthesis["evidence_catalog"]), 29)
        self.assertEqual(
            synthesis["output"]["coverage"]["multi_theme_evidence_count"],
            0,
        )
        self.assertEqual(
            synthesis["output"]["coverage"]["theme_unassigned_count"],
            2,
        )
        self.assertEqual(
            synthesis["output"]["coverage"]["assigned_but_unused_count"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
