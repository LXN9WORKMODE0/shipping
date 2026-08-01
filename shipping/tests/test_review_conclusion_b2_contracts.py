from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from shipping_pipeline.review_conclusion_b2_contracts import (
    CONCLUSION_DRAFT_SCHEMA_VERSION,
    ReviewConclusionB2Config,
    ReviewConclusionB2ContractError,
    build_conclusion_draft_schema,
    derive_conclusion_input,
    validate_conclusion_draft,
)


class ReviewConclusionB2ContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ReviewConclusionB2Config(
            schema_version="llm.review_conclusion_b2_config.v1",
            max_output_tokens=4096,
            target_chars=600,
            min_target_ratio=0.6,
            max_target_ratio=1.2,
            max_source_claims_per_paragraph=4,
        )
        self.audited_source, self.chapter_sources = _sources()
        self.conclusion_input = derive_conclusion_input(
            self.audited_source, self.chapter_sources, config=self.config
        )
        self.schema = build_conclusion_draft_schema(self.conclusion_input)

    def test_derives_only_core_and_approved_special_claims(self) -> None:
        claims = self.conclusion_input["eligible_claims"]
        self.assertEqual(len(claims), 8)
        self.assertEqual(
            {row["section_index"] for row in claims}, set(range(1, 9))
        )
        self.assertNotIn("claim_omitted", {row["claim_id"] for row in claims})
        self.assertEqual(
            self.conclusion_input["source"]["audited_assembly_run_id"],
            "audited-run",
        )

    def test_validates_four_group_conclusion_and_computes_identity(self) -> None:
        result = validate_conclusion_draft(
            _valid_draft(),
            conclusion_input=self.conclusion_input,
            schema=self.schema,
        )
        self.assertEqual(result["covered_section_indexes"], list(range(1, 9)))
        self.assertEqual(result["release_status"], "candidate_pending_conclusion_audit")
        self.assertTrue(result["chapter_id"].startswith("review_conclusion_b2_"))
        self.assertEqual(len(result["paragraphs"]), 4)

    def test_rejects_missing_bounded_language(self) -> None:
        draft = _valid_draft()
        draft["paragraphs"][1]["text"] = _text("不同研究比较了若干提升路径")
        with self.assertRaisesRegex(
            ReviewConclusionB2ContractError, "bounded_phrase_missing"
        ):
            validate_conclusion_draft(
                draft, conclusion_input=self.conclusion_input, schema=self.schema
            )

    def test_rejects_citation_union_mismatch(self) -> None:
        draft = _valid_draft()
        draft["paragraphs"][0]["citation_keys"] = ["ref_1"]
        with self.assertRaisesRegex(
            ReviewConclusionB2ContractError, "citation_union_mismatch"
        ):
            validate_conclusion_draft(
                draft, conclusion_input=self.conclusion_input, schema=self.schema
            )

    def test_rejects_source_claim_reuse(self) -> None:
        draft = _valid_draft()
        draft["paragraphs"][1]["source_claim_ids"] = ["claim_1", "claim_4"]
        draft["paragraphs"][1]["citation_keys"] = ["ref_1", "ref_4"]
        with self.assertRaisesRegex(
            ReviewConclusionB2ContractError, "source_claim_reused"
        ):
            validate_conclusion_draft(
                draft, conclusion_input=self.conclusion_input, schema=self.schema
            )

    def test_rejects_domain_level_prohibited_phrase(self) -> None:
        draft = _valid_draft()
        draft["paragraphs"][0]["text"] = _text("学界一致认为路径已经成熟")
        with self.assertRaisesRegex(
            ReviewConclusionB2ContractError, "prohibited_phrase"
        ):
            validate_conclusion_draft(
                draft, conclusion_input=self.conclusion_input, schema=self.schema
            )


def _sources():
    framework = {
        "framework_b2_id": "framework-b2",
        "working_title": "测试综述",
        "central_question": "如何比较不同路径？",
        "sections": [
            {
                "section_id": "section-conclusion",
                "section_index": 9,
                "title": "结论",
                "section_type": "conclusion",
                "target_chars": 400,
            }
        ],
    }
    claim_types = {
        3: ("cross_paper_synthesis", "supporting"),
        5: ("corpus_gap", "supporting"),
        7: ("normative_recommendation", "supporting"),
    }
    chapter_sources = []
    citation_metadata = []
    for index in range(1, 9):
        claim_type, importance = claim_types.get(index, ("direct_fact", "core"))
        claim = _claim(index, claim_type, importance)
        claims = [claim]
        if index == 1:
            claims.append(_claim(99, "direct_fact", "supporting", "claim_omitted"))
        chapter_sources.append(
            SimpleNamespace(
                run_id=f"chapter-run-{index}",
                chapter={
                    "chapter_id": f"chapter-{index}",
                    "section_index": index,
                    "title": f"第{index}节",
                },
                writing_input={
                    "framework_outline": framework,
                    "approved_claims": claims,
                },
            )
        )
        citation_metadata.append(
            {
                "citation_key": f"ref_{index}",
                "paper_id": f"paper-{index}",
                "title": f"论文{index}",
                "year": 2020,
                "document_type": "journal_article",
                "bibliographic_status": "verified",
            }
        )
    draft = {
        "draft_id": "audited-draft",
        "release_set_id": "release-set",
        "body_release_ready": True,
        "conclusion_input_ready": True,
        "working_title": "测试综述",
        "central_question": "如何比较不同路径？",
        "chapter_run_ids": [row.run_id for row in chapter_sources],
        "audit_summary": {
            "chapter_count": 8,
            "blocking_sentence_count": 0,
            "nonblocking_residual_count": 0,
            "status": "passed",
        },
        "audit_outcomes": [
            {"section_index": index, "residual_risks": []}
            for index in range(1, 9)
        ],
        "citation_metadata": citation_metadata,
    }
    audited_source = SimpleNamespace(
        run_id="audited-run",
        manifest_sha256="sha256:audited",
        draft=draft,
    )
    return audited_source, chapter_sources


def _claim(index, claim_type, importance, claim_id=None):
    return {
        "claim_id": claim_id or f"claim_{index}",
        "section_id": f"section-{index}",
        "planned_claim": f"第{index}节的已批准结论内容。",
        "claim_type": claim_type,
        "importance": importance,
        "citation_keys": [f"ref_{index}"],
        "support_mode": "direct",
        "result_type": "reported_result",
        "validation_level": "sample_limited",
        "allowed_strength": "bounded",
        "required_qualifier": "",
        "prohibited_phrasings": [],
    }


def _text(prefix: str) -> str:
    return (
        prefix
        + "；该表述仅压缩已批准内容，不新增事实，并保持原有结果类型、验证范围和结论强度。"
        "各项判断仍受当前样本文献、研究方法和数据条件约束，不能外推为整个领域的一般性结论。"
    )


def _valid_draft():
    return {
        "schema_version": CONCLUSION_DRAFT_SCHEMA_VERSION,
        "section_id": "section-conclusion",
        "section_index": 9,
        "title": "结论",
        "paragraphs": [
            {
                "paragraph_index": 1,
                "group": "overall_findings",
                "claim_type": "direct_summary",
                "text": _text("前文总体呈现了相互关联的能力约束与改进方向"),
                "source_claim_ids": ["claim_1", "claim_2"],
                "citation_keys": ["ref_1", "ref_2"],
            },
            {
                "paragraph_index": 2,
                "group": "path_comparison",
                "claim_type": "multi_paper_synthesis",
                "text": _text("本次纳入的文献显示，不同提升路径具有不同作用边界"),
                "source_claim_ids": ["claim_3", "claim_4"],
                "citation_keys": ["ref_3", "ref_4"],
            },
            {
                "paragraph_index": 3,
                "group": "corpus_limits",
                "claim_type": "corpus_gap",
                "text": _text("在当前样本文献中，若干比较问题尚未获得充分回答"),
                "source_claim_ids": ["claim_5", "claim_6"],
                "citation_keys": ["ref_5", "ref_6"],
            },
            {
                "paragraph_index": 4,
                "group": "future_direction",
                "claim_type": "normative_recommendation",
                "text": _text("据此，未来研究可在既有证据边界内推进统一比较与验证"),
                "source_claim_ids": ["claim_7", "claim_8"],
                "citation_keys": ["ref_7", "ref_8"],
            },
        ],
    }
