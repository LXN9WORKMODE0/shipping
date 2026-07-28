from __future__ import annotations

import csv
import json
import sys
import unittest
import uuid
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_contracts import ContractViolation, build_evidence_anchor_id, build_evidence_unit_id
from shipping_pipeline.llm_semantic_adjudication import (
    EVIDENCE_REVISION_SCHEMA_VERSION,
    SEMANTIC_DECISION_HEADERS,
    SemanticAdjudicationError,
    build_evidence_revision_prompts,
    build_semantic_issues,
    enrich_semantic_issues_for_review,
    load_semantic_decisions,
    validate_and_apply_evidence_revision,
    write_semantic_decision_workbook,
    write_semantic_decisions_template,
)


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".test_tmp"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)


def make_temp_dir() -> Path:
    path = TEST_TMP_ROOT / f"semantic_adjudication_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def make_evidence(claim: str = "该模型提高了鲁棒性。") -> dict:
    citations = [{"material_id": "paper:card-1", "quote": "论文构建了优化模型。"}]
    anchor = build_evidence_anchor_id("generation-one", "result", citations)
    return {
        "evidence_unit_id": build_evidence_unit_id(anchor, claim),
        "evidence_anchor_id": anchor,
        "claim": claim,
        "evidence_type": "result",
        "citations": citations,
        "relevance": "用于比较模型。",
        "confidence": "medium",
        "caveats": [],
        "request_id": "evidence_batch",
        "batch_id": "evidence_batch",
    }


class SemanticAdjudicationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = make_evidence()
        self.statement = {
            "statement_id": "statement_1234567890abcdef12345678",
            "field": "review_uses",
            "index": 0,
            "required_support": "grounded_inference_allowed",
            "statement": "论文构建了优化模型。",
            "evidence_unit_ids": [self.evidence["evidence_unit_id"]],
        }
        self.claim_review = {
            "evidence_unit_id": self.evidence["evidence_unit_id"],
            "verdict": "partially_supported",
            "reason": "引文没有说明提高鲁棒性。",
            "unsupported_fragments": ["提高了鲁棒性"],
        }
        self.role_review = {
            "statement_id": self.statement["statement_id"],
            "verdict": "misclassified",
            "reason": "只复述论文事实，没有说明综述用途。",
            "misaligned_fragments": ["论文构建了优化模型"],
        }
        self.issues = build_semantic_issues(
            evidence_units=[self.evidence],
            statement_records=[self.statement],
            evidence_claim_reviews=[self.claim_review],
            statement_role_reviews=[self.role_review],
        )

    def test_decision_file_requires_complete_explicit_decisions_and_preserves_readonly_fields(self) -> None:
        root = make_temp_dir()
        path = root / "semantic_decisions.csv"
        write_semantic_decisions_template(path, self.issues)
        with self.assertRaisesRegex(SemanticAdjudicationError, "待裁决"):
            load_semantic_decisions(
                path,
                issues=self.issues,
                audit_run_id="audit-one",
                audit_manifest_sha256="sha256:audit",
                candidate_sha256="sha256:candidate",
                evidence_units_sha256="sha256:evidence",
            )

        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["人工裁决"] = "确认缺陷"
        rows[0]["人工说明"] = "claim 增加了引文没有表达的效果判断。"
        rows[1]["人工裁决"] = "门禁误判"
        rows[1]["人工说明"] = "该字段在当前综述中包含明确用途。"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SEMANTIC_DECISION_HEADERS)
            writer.writeheader()
            writer.writerows(rows)
        payload = load_semantic_decisions(
            path,
            issues=self.issues,
            audit_run_id="audit-one",
            audit_manifest_sha256="sha256:audit",
            candidate_sha256="sha256:candidate",
            evidence_units_sha256="sha256:evidence",
        )
        self.assertEqual(len(payload["decisions"]), 2)
        self.assertTrue(payload["decisions_sha256"].startswith("sha256:"))

        rows[0]["原观点或陈述"] = "被篡改的 claim"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SEMANTIC_DECISION_HEADERS)
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaisesRegex(SemanticAdjudicationError, "只读字段被修改"):
            load_semantic_decisions(
                path,
                issues=self.issues,
                audit_run_id="audit-one",
                audit_manifest_sha256="sha256:audit",
                candidate_sha256="sha256:candidate",
                evidence_units_sha256="sha256:evidence",
            )

    def test_human_review_artifacts_are_self_contained_and_numbered(self) -> None:
        root = make_temp_dir()
        materials = [
            {
                "material_id": "paper:card-0",
                "order": 0,
                "clean_title": "前一节",
                "extract": "前一张 Card 的原文。",
                "source_span": {"path": "normalized/document.md", "start_line": 1, "end_line": 2},
                "confidence_flags": [],
                "quality_flags": [],
            },
            {
                "material_id": "paper:card-1",
                "order": 1,
                "clean_title": "模型结果",
                "extract": "论文构建了优化模型。这里是进入抽取器的完整 Card 原文。",
                "source_span": {"path": "normalized/document.md", "start_line": 3, "end_line": 5},
                "confidence_flags": ["parse.ocr_noise"],
                "quality_flags": [],
            },
            {
                "material_id": "paper:card-2",
                "order": 2,
                "clean_title": "后一节",
                "extract": "后一张 Card 的原文。",
                "source_span": {"path": "normalized/document.md", "start_line": 6, "end_line": 7},
                "confidence_flags": [],
                "quality_flags": [],
            },
        ]
        self.evidence["citations"][0]["source_ref"] = {
            "path": "normalized/document.md",
            "start_line": 3,
            "end_line": 5,
        }
        enriched = enrich_semantic_issues_for_review(
            issues=self.issues,
            paper_title="测试论文",
            evidence_units=[self.evidence],
            statement_records=[self.statement],
            materials=materials,
        )
        csv_path = root / "semantic_decisions.csv"
        workbook_path = root / "semantic_decision_workbook.md"
        write_semantic_decisions_template(csv_path, enriched)
        write_semantic_decision_workbook(workbook_path, enriched)

        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["裁决项"], "第1项")
        self.assertEqual(rows[0]["论文标题"], "测试论文")
        self.assertIn("完整 Card 原文", rows[0]["Card完整文本"])
        self.assertIn("前一张 Card 的原文", rows[0]["相邻Card上下文"])
        self.assertIn("论文构建了优化模型", rows[0]["逐字引文"])
        self.assertEqual(rows[0]["内部目标ID"], self.evidence["evidence_unit_id"])
        workbook = workbook_path.read_text(encoding="utf-8")
        self.assertIn("第1项：证据观点", workbook)
        self.assertIn("Card → Evidence Unit", workbook)
        self.assertIn("论文级综合输出", workbook)
        self.assertIn("不需要检索内部 Evidence ID", workbook)

    def test_pending_template_can_be_replaced_but_decided_template_cannot(self) -> None:
        root = make_temp_dir()
        path = root / "semantic_decisions.csv"
        write_semantic_decisions_template(path, self.issues)
        write_semantic_decisions_template(path, self.issues, overwrite=True)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["人工裁决"] = "确认缺陷"
        rows[0]["人工说明"] = "已经进行人工判断。"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SEMANTIC_DECISION_HEADERS)
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaisesRegex(SemanticAdjudicationError, "已包含实际裁决"):
            write_semantic_decisions_template(path, self.issues, overwrite=True)

    def test_evidence_revision_keeps_anchor_and_citations_but_changes_unit_id(self) -> None:
        decisions = [
            {
                **self.issues[0],
                "human_decision": "确认缺陷",
                "human_note": "删除未经引文支持的效果判断。",
            }
        ]
        system, user = build_evidence_revision_prompts(
            paper_id="paper-one",
            paper_title="论文一",
            topic="船舶运输",
            evidence_units=[self.evidence],
            blocking_reviews=[self.claim_review],
            human_decisions=decisions,
        )
        self.assertIn("逐字引文", system)
        self.assertEqual(json.loads(user)["待修订证据"][0]["人工说明"], decisions[0]["human_note"])
        payload = {
            "schema_version": EVIDENCE_REVISION_SCHEMA_VERSION,
            "revisions": [
                {
                    "evidence_unit_id": self.evidence["evidence_unit_id"],
                    "action": "replace",
                    "replacement": {
                        "claim": "论文构建了优化模型。",
                        "caveats": ["引文没有报告鲁棒性效果。"],
                    },
                }
            ],
        }
        validated, units, mappings = validate_and_apply_evidence_revision(
            payload,
            evidence_units=[self.evidence],
            target_evidence_ids=[self.evidence["evidence_unit_id"]],
        )
        self.assertEqual(validated, payload)
        self.assertEqual(units[0]["evidence_anchor_id"], self.evidence["evidence_anchor_id"])
        self.assertEqual(units[0]["citations"], self.evidence["citations"])
        self.assertNotEqual(units[0]["evidence_unit_id"], self.evidence["evidence_unit_id"])
        self.assertEqual(mappings[0]["result_evidence_unit_id"], units[0]["evidence_unit_id"])

    def test_evidence_revision_rejects_unchanged_or_duplicate_identity(self) -> None:
        unchanged = {
            "schema_version": EVIDENCE_REVISION_SCHEMA_VERSION,
            "revisions": [
                {
                    "evidence_unit_id": self.evidence["evidence_unit_id"],
                    "action": "replace",
                    "replacement": {"claim": self.evidence["claim"], "caveats": []},
                }
            ],
        }
        with self.assertRaisesRegex(ContractViolation, "未发生变化"):
            validate_and_apply_evidence_revision(
                unchanged,
                evidence_units=[self.evidence],
                target_evidence_ids=[self.evidence["evidence_unit_id"]],
            )


if __name__ == "__main__":
    unittest.main()
