from __future__ import annotations

import csv
import hashlib
import json
import sys
import unittest
import uuid
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_review import (
    DECISION_HEADERS,
    import_review_decisions,
    migrate_review_decisions,
    write_analysis_review,
)
from shipping_pipeline.llm_statement_support import build_statement_records


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".test_tmp"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)


def make_run_dir() -> Path:
    path = TEST_TMP_ROOT / f"review_{uuid.uuid4().hex}"
    (path / "output").mkdir(parents=True)
    return path


def evidence_unit(
    evidence_id: str = "evidence_000000000000000000000001",
    anchor_id: str = "anchor_000000000000000000000001",
) -> dict:
    return {
        "evidence_unit_id": evidence_id,
        "evidence_anchor_id": anchor_id,
        "request_id": "evidence_request_one",
        "batch_id": "evidence_request_one",
        "claim": "提高闸室利用率能够增加通航能力。",
        "evidence_type": "result",
        "citations": [
            {
                "material_id": "paper-one:card-001",
                "quote": "闸室利用率提高到85%以上",
                "source_ref": {"path": "normalized/document.md", "start_line": 12, "end_line": 13},
                "source_spans": [],
                "card_title": "通航能力分析",
                "confidence_flags": ["parse.unclassified_front_matter"],
                "quality_flags": [],
            }
        ],
        "relevance": "可用于解释通航能力提升措施。",
        "confidence": "medium",
        "caveats": ["需要核对统计年份"],
    }


def paper_result() -> dict:
    evidence_id = "evidence_000000000000000000000001"
    result = {
        "schema_version": "llm.paper_result.v3",
        "paper_disposition": "analyzed",
        "paper_id": "paper-one",
        "paper_title": "论文一",
        "topic": "船舶积压",
        "analysis": {
            "schema_version": "llm.paper_analysis.v4",
            "research_focus": {
                "statement": "研究船闸通航能力。",
                "evidence_unit_ids": [evidence_id],
            },
            "study_type": "quantitative",
            "methods": [{"statement": "统计分析", "evidence_unit_ids": [evidence_id]}],
            "core_findings": [{"statement": "利用率提升可增加能力。", "evidence_unit_ids": [evidence_id]}],
            "limitations": [],
            "review_uses": [{"statement": "用于对策比较。", "evidence_unit_ids": [evidence_id]}],
            "unresolved_questions": [
                {"statement": "统计口径需要复核。", "evidence_unit_ids": [evidence_id]}
            ],
            "evidence_dispositions": [
                {
                    "evidence_unit_id": evidence_id,
                    "disposition": "used",
                    "reason_code": "supports_claim",
                }
            ],
        },
    }
    result["statement_support_reviews"] = [
        {
            "statement_id": record["statement_id"],
            "verdict": (
                "grounded_inference"
                if record["required_support"] == "grounded_inference_allowed"
                else "directly_supported"
            ),
            "reason": "所列证据支持该陈述。",
            "unsupported_fragments": [],
        }
        for record in build_statement_records(result["analysis"])
    ]
    return result


def write_evidence(run_dir: Path, rows: list[dict]) -> None:
    (run_dir / "output" / "evidence_units.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def rewrite_decisions(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DECISION_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


class LLMReviewTests(unittest.TestCase):
    def test_review_is_chinese_traceable_and_generates_editable_decisions(self) -> None:
        run_dir = make_run_dir()
        rows = [evidence_unit()]
        write_evidence(run_dir, rows)
        (run_dir / "plan").mkdir()
        (run_dir / "plan" / "model_profile.json").write_text(
            json.dumps(
                {
                    "profile_id": "siliconflow-deepseek-v4-pro",
                    "tokenizer_revision": "0e1a0e5e52aea73055f50fef6f2423db370265b6",
                    "thinking": {"type": "disabled"},
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "plan" / "analysis_plan.json").write_text(
            json.dumps(
                {
                    "strategy": "single_evidence_batch",
                    "evidence_batches": [
                        {
                            "request_id": "evidence_request_one",
                            "stage": "evidence",
                            "split_reason": "full_paper_within_budget",
                            "section_ids": ["chapter:1"],
                            "input_tokens": 100,
                            "max_output_tokens": 2048,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "plan" / "synthesis_plan.json").write_text(
            json.dumps(
                {
                    "strategy": "direct_paper_synthesis",
                    "requests": [],
                    "cross_section_evidence_ids": [],
                }
            ),
            encoding="utf-8",
        )
        request_dir = run_dir / "batches" / "evidence_request_one"
        request_dir.mkdir(parents=True)
        (request_dir / "result.json").write_text(
            json.dumps(
                {
                    "request_id": "evidence_request_one",
                    "actual_prompt_tokens": 100,
                    "prompt_token_delta": 0,
                    "actual_completion_tokens": 80,
                }
            ),
            encoding="utf-8",
        )

        write_analysis_review(
            run_dir / "review",
            run_id="review-run",
            paper_id="paper-one",
            paper_title="论文一",
            topic="船舶积压",
            evidence_units=rows,
            paper_result=paper_result(),
            coverage={
                "planned_material_count": 1,
                "assessed_material_count": 1,
                "used_in_final_analysis_ids": [rows[0]["evidence_unit_id"]],
                "not_used_in_final_analysis_ids": [],
            },
        )

        report = (run_dir / "review" / "review.md").read_text(encoding="utf-8")
        decisions = (run_dir / "review" / "decisions.csv").read_text(encoding="utf-8-sig")
        self.assertIn("# 论文 LLM 分析人工审核", report)
        self.assertIn("normalized/document.md:12-13", report)
        self.assertIn("闸室利用率提高到85%以上", report)
        self.assertIn("siliconflow-deepseek-v4-pro", report)
        self.assertIn("Non-think（关闭）", report)
        self.assertIn("full_paper_within_budget", report)
        self.assertIn("计划输入 100；实际输入 100；差值 0", report)
        self.assertIn("anchor_000000000000000000000001", report)
        self.assertIn("chapter:1", report)
        self.assertIn("支持核验：直接支持；说明：所列证据支持该陈述。", report)
        self.assertIn("支持核验：有依据的综述推论；说明：所列证据支持该陈述。", report)
        self.assertIn("evidence_000000000000000000000001,anchor_000000000000000000000001,待审核", decisions)

    def test_decision_import_validates_rows_and_does_not_modify_model_output(self) -> None:
        run_dir = make_run_dir()
        rows = [evidence_unit()]
        write_evidence(run_dir, rows)
        decisions_path = run_dir / "review" / "decisions.csv"
        decisions_path.parent.mkdir(parents=True)
        rewrite_decisions(
            decisions_path,
            [
                {
                    "evidence_unit_id": "evidence_000000000000000000000001",
                    "evidence_anchor_id": "anchor_000000000000000000000001",
                    "决策": "修改",
                    "修改后观点": "提高利用率可能增加通航能力。",
                    "备注": "降低因果强度",
                }
            ],
        )
        evidence_path = run_dir / "output" / "evidence_units.jsonl"
        before = hashlib.sha256(evidence_path.read_bytes()).hexdigest()

        result = import_review_decisions(run_dir)

        after = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        self.assertEqual(result["decision_counts"], {"修改": 1})
        self.assertEqual(result["schema_version"], "llm.review_decisions.v2")
        self.assertEqual(before, after)
        self.assertTrue((run_dir / "review" / "decision_state.json").exists())

    def test_decision_import_rejects_unknown_duplicate_and_empty_revision(self) -> None:
        base_row = {
            "evidence_unit_id": "evidence_000000000000000000000001",
            "evidence_anchor_id": "anchor_000000000000000000000001",
            "决策": "待审核",
            "修改后观点": "",
            "备注": "",
        }
        cases = [
            ([{**base_row, "evidence_unit_id": "unknown"}], "未知"),
            ([{**base_row, "evidence_anchor_id": "anchor-wrong"}], "不匹配"),
            ([base_row, base_row], "重复"),
            ([{**base_row, "决策": "修改"}], "必须填写"),
        ]
        for rows, message in cases:
            with self.subTest(message=message):
                run_dir = make_run_dir()
                write_evidence(run_dir, [evidence_unit()])
                decisions_path = run_dir / "review" / "decisions.csv"
                decisions_path.parent.mkdir(parents=True)
                rewrite_decisions(decisions_path, rows)
                with self.assertRaisesRegex(ValueError, message):
                    import_review_decisions(run_dir)

    def test_decision_migration_only_uses_exact_unit_or_same_anchor(self) -> None:
        previous = {
            "schema_version": "llm.review_decisions.v2",
            "decisions": [
                {
                    "evidence_unit_id": "unit-same",
                    "evidence_anchor_id": "anchor-same",
                    "决策": "接受",
                    "修改后观点": "",
                    "备注": "已核对",
                },
                {
                    "evidence_unit_id": "unit-old",
                    "evidence_anchor_id": "anchor-changed-claim",
                    "决策": "拒绝",
                    "修改后观点": "",
                    "备注": "旧观点",
                },
            ],
        }
        current = [
            evidence_unit("unit-same", "anchor-same"),
            evidence_unit("unit-new", "anchor-changed-claim"),
            evidence_unit("unit-unrelated", "anchor-new"),
        ]

        migrated = migrate_review_decisions(previous, current)

        self.assertEqual([row["migration"] for row in migrated["decisions"]], ["exact_unit", "anchor_only"])
        self.assertEqual(migrated["decisions"][0]["决策"], "接受")
        self.assertEqual(migrated["decisions"][1]["决策"], "待审核")
        self.assertEqual(migrated["decisions"][1]["备注"], "来源相同但观点变化")


if __name__ == "__main__":
    unittest.main()
