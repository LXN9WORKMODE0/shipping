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

from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.topic_review import (
    BRIEF_SYSTEM_PROMPT,
    SCOPE_SYSTEM_PROMPT,
    TopicReviewRunner,
)
from main import build_parser


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
REVIEW_CONFIG = PROJECT_ROOT / "config" / "topic-review-default.json"


def make_workspace() -> Path:
    path = TEST_TMP_ROOT / f"topic_review_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def make_material(
    index: int,
    *,
    generation_id: str = "generation-one",
    damaged: bool = False,
) -> dict[str, Any]:
    text = f"材料{index}说明三峡船闸能力受到约束。"
    return {
        "schema_version": "material.v2",
        "material_id": f"paper-one:card-{index}",
        "material_type": "evidence_card",
        "paper_id": "paper-one",
        "paper_title": "论文一",
        "source_path": "normalized/document.md",
        "clean_title": f"第{index}节",
        "raw_title": f"第{index}节",
        "order": index,
        "extract": text,
        "source_span": {
            "path": "normalized/document.md",
            "start_line": index * 10,
            "end_line": index * 10 + 1,
        },
        "source_spans": [
            {
                "path": "normalized/document.md",
                "start_line": index * 10,
                "end_line": index * 10 + 1,
                "start_char": 0,
                "end_char": len(text),
            }
        ],
        "heading_path": [f"第{index}节"],
        "content_kind": "table" if damaged else "prose",
        "confidence_flags": ["table.parse_failed"] if damaged else [],
        "quality_flags": [],
        "source_fingerprint": f"sha256:fingerprint-{index}",
        "generation_id": generation_id,
    }


def write_corpus(workspace: Path, materials: list[dict[str, Any]]) -> None:
    corpus = workspace / "_corpus"
    corpus.mkdir(parents=True)
    generation_id = str(materials[0]["generation_id"])
    (corpus / "materials.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in materials),
        encoding="utf-8",
    )
    (corpus / "papers.jsonl").write_text(
        json.dumps(
            {
                "paper_id": "paper-one",
                "status": "completed",
                "structure_quality": "silver",
                "source": "paper-one/normalized/document.md",
                "generation_id": generation_id,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


class FakeTokenCounter:
    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        prompt_tokens = max(1, len(json.dumps(messages, ensure_ascii=False)) // 4)
        return TokenCount(
            prompt_tokens=prompt_tokens,
            encoded_prompt_sha256=f"sha256:{prompt_tokens:064x}",
        )


class FakeTopicClient:
    provider = "openai-compatible"

    def __init__(
        self,
        *,
        relevance: str = "supporting",
        selected_ids: list[str] | None = None,
        invalid_evidence: bool = False,
        invalid_evidence_ids: set[str] | None = None,
        brief_gaps: list[dict[str, str]] | None = None,
        revisit_selected_ids: list[str] | None = None,
        invalid_revisit_evidence: bool = False,
    ) -> None:
        self.model = "deepseek-ai/DeepSeek-V4-Pro"
        self.relevance = relevance
        self.selected_ids = selected_ids
        self.invalid_evidence = invalid_evidence
        self.invalid_evidence_ids = set(invalid_evidence_ids or set())
        self.brief_gaps = list(brief_gaps or [])
        self.revisit_selected_ids = revisit_selected_ids
        self.invalid_revisit_evidence = invalid_revisit_evidence
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        task: str,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> ProviderResult:
        self.calls.append({"task": task, "context": context, "request": request_payload})
        if task.endswith("scope"):
            selected = self.selected_ids
            if selected is None:
                selected = [] if self.relevance == "exclude" else [context["material_ids"][0]]
            else:
                alias_by_material_id = {
                    material_id: alias
                    for alias, material_id in context.get(
                        "scope_alias_to_material_id",
                        {},
                    ).items()
                }
                selected = [
                    alias_by_material_id.get(material_id, material_id)
                    for material_id in selected
                ]
            content = {
                "schema_version": "llm.topic_scope.v1",
                "paper_relevance": self.relevance,
                "relevance_reason": "论文提供当前主题所需的局部材料。",
                "topic_summary": "论文讨论三峡船闸运行压力。",
                "selected_materials": [
                    {
                        "material_id": material_id,
                        "priority": "primary",
                        "intended_use": "finding",
                        "selection_reason": "直接讨论船闸运行压力。",
                    }
                    for material_id in selected
                ],
            }
        elif task.endswith("evidence"):
            material_id = str(context["material_ids"][0])
            quote = next(
                row
                for row in context["quote_candidates"]
                if str(row["material_id"]) == material_id
            )
            evidence_invalid = (
                self.invalid_evidence
                or material_id in self.invalid_evidence_ids
                or (
                self.invalid_revisit_evidence
                and context.get("evidence_phase") == "revisit"
                )
            )
            content = {
                "schema_version": "llm.evidence_batch.v4",
                "material_results": [
                    {
                        "material_id": material_id,
                        "disposition": "not_relevant" if evidence_invalid else "evidence",
                        "reason_code": "off_topic" if evidence_invalid else "direct_evidence",
                        "evidence_units": []
                        if evidence_invalid
                        else [
                            {
                                "claim": str(quote["text"]),
                                "evidence_type": "result",
                                "citations": [{"quote_id": str(quote["quote_id"])}],
                                "relevance": "直接说明当前综述主题。",
                                "confidence": "high",
                                "caveats": [],
                            }
                        ],
                    }
                ],
            }
        elif task.endswith("revisit"):
            selected = list(self.revisit_selected_ids or [])
            content = {
                "schema_version": "llm.topic_revisit.v1",
                "status": "selected" if selected else "no_candidate",
                "review_summary": (
                    "发现能够补充当前证据缺口的材料。"
                    if selected
                    else "未入选材料没有提供实质新增证据。"
                ),
                "selected_materials": [
                    {
                        "material_id": material_id,
                        "gap_ids": [str(context["gap_ids"][0])],
                        "intended_use": "finding",
                        "selection_reason": "直接回答当前证据缺口。",
                        "novelty_reason": "当前已验证 Evidence 尚未覆盖该维度。",
                    }
                    for material_id in selected
                ],
            }
        elif task.endswith("brief"):
            user_payload = json.loads(request_payload["messages"][1]["content"])
            units = user_payload["已验证Evidence"]
            unit = units[0]
            evidence_id = str(unit["evidence_unit_id"])
            statement = str(unit["claim"])
            prior_brief_calls = sum(
                row["task"].endswith("brief")
                for row in self.calls[:-1]
            )
            content = {
                "schema_version": "llm.topic_brief.v2",
                "topic_contribution": {
                    "statement": "论文提供了船闸运行压力的量化材料。",
                    "evidence_unit_ids": [evidence_id],
                },
                "key_points": [
                    {
                        "point_type": "finding",
                        "statement": str(current["claim"]),
                        "evidence_unit_ids": [str(current["evidence_unit_id"])],
                    }
                    for current in units
                ],
                "review_uses": [
                    {
                        "use_type": "support",
                        "statement": "可用于说明三峡船闸运行压力。",
                        "evidence_unit_ids": [evidence_id],
                    }
                ],
                "cautions": [],
                "evidence_gaps": (
                    self.brief_gaps if prior_brief_calls == 0 else []
                ),
            }
        else:
            raise AssertionError(f"未知任务：{task}")
        prompt_tokens = int(context["planned_input_tokens"])
        completion_tokens = 128
        response = {
            "id": f"response-{len(self.calls)}",
            "model": self.model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(content, ensure_ascii=False),
                        "reasoning_content": "",
                    },
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        return ProviderResult(
            raw_body=json.dumps(response, ensure_ascii=False).encode("utf-8"),
            parsed_response=response,
            response_id=str(response["id"]),
            response_model=self.model,
            system_fingerprint=None,
            finish_reason="stop",
            reasoning_content_chars=0,
            usage=dict(response["usage"]),
        )


class TopicReviewFlowTests(unittest.TestCase):
    def test_scope_prompt_requires_material_id_to_be_copied_verbatim(self):
        self.assertIn("material_id 必须从输入 Card 原样复制完整字符串", SCOPE_SYSTEM_PROMPT)
        self.assertIn("不得改写行号", SCOPE_SYSTEM_PROMPT)

    def test_brief_prompt_forbids_alias_types_and_hypothetical_numbers(self):
        self.assertIn("禁止输出 fact", BRIEF_SYSTEM_PROMPT)
        self.assertIn("不得为了举例自行添加假设数字", BRIEF_SYSTEM_PROMPT)
        self.assertIn("其他规模", BRIEF_SYSTEM_PROMPT)

    def setUp(self) -> None:
        self.workspace = make_workspace()

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def run_review(
        self,
        client: FakeTopicClient,
        materials: list[dict[str, Any]],
        *,
        run_id: str,
    ) -> dict[str, Any]:
        write_corpus(self.workspace, materials)
        return TopicReviewRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
        ).run(
            paper_id="paper-one",
            topic="三峡船舶积压与疏导",
            run_id=run_id,
            model_profile_path=MODEL_PROFILE,
            review_config_path=REVIEW_CONFIG,
        )

    def test_excluded_paper_stops_after_scope_and_records_all_omitted_cards(self) -> None:
        materials = [make_material(1), make_material(2), make_material(3)]
        client = FakeTopicClient(relevance="exclude")
        result = self.run_review(client, materials, run_id="scope-exclude")

        self.assertEqual("excluded", result["status"])
        self.assertEqual(1, len(client.calls))
        omitted = _read_jsonl(Path(result["run_dir"]) / "audit" / "omitted_materials.jsonl")
        self.assertEqual(3, len(omitted))
        self.assertEqual([], result["failure_codes"])

    def test_unselected_damaged_table_does_not_block_selected_prose(self) -> None:
        prose = make_material(1)
        damaged = make_material(2, damaged=True)
        client = FakeTopicClient(selected_ids=[prose["material_id"]])
        result = self.run_review(client, [prose, damaged], run_id="omit-damaged")

        self.assertEqual("completed", result["status"])
        self.assertEqual(3, len(client.calls))
        self.assertEqual(1, result["selected_material_count"])
        self.assertEqual(1, result["omitted_material_count"])
        report = Path(result["review_path"]).read_text(encoding="utf-8")
        self.assertIn("未入选材料中带解析标记：1 张", report)
        self.assertIn("原文：材料1说明三峡船闸能力受到约束。", report)
        self.assertIn("normalized/document.md L10-L11", report)

    def test_selected_evidence_contract_failure_blocks_without_touching_omitted_card(self) -> None:
        materials = [make_material(1), make_material(2, damaged=True)]
        client = FakeTopicClient(
            selected_ids=[materials[0]["material_id"]],
            invalid_evidence=True,
        )
        result = self.run_review(client, materials, run_id="selected-fails")

        self.assertEqual("failed", result["status"])
        self.assertIn("schema.scoped_evidence_invalid", result["failure_codes"])
        self.assertEqual(2, len(client.calls))
        manifest = json.loads(
            (Path(result["run_dir"]) / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(["completed", "completed"], [row["status"] for row in manifest["requests"]])
        report = Path(result["review_path"]).read_text(encoding="utf-8")
        self.assertIn("## 阻断问题", report)
        self.assertIn("## Evidence 失败账本", report)
        self.assertIn("未入选材料中带解析标记：1 张", report)

    def test_one_selected_card_contract_failure_keeps_other_evidence(self) -> None:
        materials = [make_material(1), make_material(2), make_material(3)]
        selected_ids = [materials[0]["material_id"], materials[1]["material_id"]]
        client = FakeTopicClient(
            selected_ids=selected_ids,
            invalid_evidence_ids={selected_ids[0]},
        )

        result = self.run_review(client, materials, run_id="partial-evidence")
        run_dir = Path(result["run_dir"])

        self.assertEqual("completed_with_failures", result["status"])
        self.assertEqual(1, result["evidence_unit_count"])
        self.assertEqual(1, result["failure_count"])
        self.assertEqual(
            [selected_ids[0]],
            json.loads(
                (run_dir / "manifest.json").read_text(encoding="utf-8")
            )["evidence_failed_material_ids"],
        )
        failures = _read_jsonl(run_dir / "audit" / "evidence_failures.jsonl")
        self.assertEqual(selected_ids[0], failures[0]["material_id"])
        self.assertIn("schema.scoped_evidence_invalid", failures[0]["error_code"])
        report = (run_dir / "review" / "paper_brief.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## Evidence 失败账本", report)
        self.assertIn("材料1说明三峡船闸能力受到约束", report)
        self.assertIn("其他已验证 Evidence 不受影响", report)

    def test_completed_run_archives_scope_evidence_brief_and_usage(self) -> None:
        materials = [make_material(1), make_material(2)]
        client = FakeTopicClient(selected_ids=[materials[0]["material_id"]])
        result = self.run_review(client, materials, run_id="complete")
        run_dir = Path(result["run_dir"])

        self.assertEqual("completed", result["status"])
        for relative in (
            "scope/validated_scope.json",
            "evidence/evidence_units.jsonl",
            "output/paper_brief.json",
            "review/paper_brief.md",
            "manifest.json",
        ):
            self.assertTrue((run_dir / relative).is_file(), relative)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(3, len(manifest["requests"]))
        self.assertGreater(manifest["usage"]["prompt_tokens"], 0)
        self.assertEqual(0, manifest["failure_count"])
        report = (run_dir / "review" / "paper_brief.md").read_text(encoding="utf-8")
        self.assertIn("未做逐句引证校验，不应直接作为综述引文", report)
        self.assertIn("具体事实和可引用表述以“可用于综述的观点”及其原文为准", report)

    def test_each_selected_card_gets_an_isolated_evidence_request(self) -> None:
        materials = [make_material(1), make_material(2), make_material(3)]
        selected_ids = [materials[0]["material_id"], materials[1]["material_id"]]
        client = FakeTopicClient(selected_ids=selected_ids)
        result = self.run_review(client, materials, run_id="isolated-evidence")

        self.assertEqual("completed", result["status"])
        evidence_calls = [
            row for row in client.calls if row["task"].endswith("evidence")
        ]
        self.assertEqual(2, len(evidence_calls))
        self.assertEqual(
            [[selected_ids[0]], [selected_ids[1]]],
            [row["context"]["material_ids"] for row in evidence_calls],
        )
        scope_call = next(row for row in client.calls if row["task"].endswith("scope"))
        self.assertEqual(
            ["card_0001", "card_0002", "card_0003"],
            scope_call["context"]["material_ids"],
        )
        validated_scope = json.loads(
            (
                Path(result["run_dir"])
                / "scope"
                / "validated_scope.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            selected_ids,
            [
                row["material_id"]
                for row in validated_scope["selected_materials"]
            ],
        )
        prompt_payload = json.loads(evidence_calls[0]["request"]["messages"][1]["content"])
        scope_labels = prompt_payload["主题定界标签"]["selected_materials"]
        self.assertNotIn("selection_reason", scope_labels[0])
        self.assertNotIn("topic_summary", prompt_payload["主题定界标签"])
        self.assertEqual(2, result["evidence_unit_count"])

        brief_call = next(row for row in client.calls if row["task"].endswith("brief"))
        brief_prompt = json.loads(brief_call["request"]["messages"][1]["content"])
        projected_evidence = brief_prompt["已验证Evidence"][0]
        self.assertNotIn("relevance", projected_evidence)
        self.assertNotIn("caveats", projected_evidence)
        self.assertEqual(
            {"paper_relevance": "supporting"},
            brief_prompt["主题定界标签"],
        )
        self.assertNotIn("主题定界", brief_prompt)

    def test_evidence_gap_triggers_one_revisit_and_merges_recovered_card(self) -> None:
        materials = [make_material(1), make_material(2), make_material(3)]
        gap = {
            "gap_type": "implementation",
            "question": "是否存在尚未纳入的具体疏导实施措施？",
            "why_it_matters": "当前证据只有问题描述，缺少实施层材料。",
        }
        client = FakeTopicClient(
            selected_ids=[materials[0]["material_id"]],
            brief_gaps=[gap],
            revisit_selected_ids=[materials[1]["material_id"]],
        )
        result = self.run_review(client, materials, run_id="revisit-success")
        run_dir = Path(result["run_dir"])

        self.assertEqual("completed", result["status"])
        self.assertEqual("completed", result["revisit_status"])
        self.assertEqual(1, result["initial_selected_material_count"])
        self.assertEqual(1, result["revisited_material_count"])
        self.assertEqual(2, result["selected_material_count"])
        self.assertEqual(1, result["omitted_material_count"])
        self.assertEqual(2, result["evidence_unit_count"])
        self.assertEqual(6, len(client.calls))
        self.assertEqual(
            ["scope", "evidence", "brief", "revisit", "evidence", "brief"],
            [row["task"].removeprefix("topic_review_") for row in client.calls],
        )
        revisit_evidence_call = client.calls[4]
        self.assertEqual("revisit", revisit_evidence_call["context"]["evidence_phase"])
        evidence_prompt = json.loads(
            revisit_evidence_call["request"]["messages"][1]["content"]
        )
        labels = evidence_prompt["主题定界标签"]["selected_materials"]
        self.assertNotIn("selection_reason", labels[0])
        self.assertNotIn("novelty_reason", labels[0])
        self.assertEqual(
            2,
            len(_read_jsonl(run_dir / "audit" / "initial_omitted_materials.jsonl")),
        )
        self.assertEqual(
            1,
            len(_read_jsonl(run_dir / "audit" / "omitted_materials.jsonl")),
        )
        report = (run_dir / "review" / "paper_brief.md").read_text(encoding="utf-8")
        self.assertIn("## 自动回查", report)
        self.assertIn("已完成并合并补充证据", report)
        self.assertIn("入选阶段：自动回查", report)

    def test_revisit_can_finish_with_no_candidate(self) -> None:
        materials = [make_material(1), make_material(2)]
        client = FakeTopicClient(
            selected_ids=[materials[0]["material_id"]],
            brief_gaps=[
                {
                    "gap_type": "comparison",
                    "question": "是否存在可用的方案比较？",
                    "why_it_matters": "比较材料有助于形成综述判断。",
                }
            ],
        )
        result = self.run_review(client, materials, run_id="revisit-none")

        self.assertEqual("completed", result["status"])
        self.assertEqual("no_candidate", result["revisit_status"])
        self.assertEqual(4, len(client.calls))
        self.assertEqual(0, result["revisited_material_count"])
        self.assertEqual(1, result["evidence_unit_count"])

    def test_revisit_failure_keeps_base_brief_and_exposes_partial_status(self) -> None:
        materials = [make_material(1), make_material(2)]
        client = FakeTopicClient(
            selected_ids=[materials[0]["material_id"]],
            brief_gaps=[
                {
                    "gap_type": "effect",
                    "question": "是否存在疏导效果数据？",
                    "why_it_matters": "当前证据不能说明策略效果。",
                }
            ],
            revisit_selected_ids=[materials[1]["material_id"]],
            invalid_revisit_evidence=True,
        )
        result = self.run_review(client, materials, run_id="revisit-fails")
        run_dir = Path(result["run_dir"])

        self.assertEqual("completed_with_revisit_failure", result["status"])
        self.assertEqual("failed", result["revisit_status"])
        self.assertEqual(1, result["selected_material_count"])
        self.assertEqual(0, result["revisited_material_count"])
        self.assertEqual(1, result["evidence_unit_count"])
        self.assertIn("schema.scoped_evidence_invalid", result["failure_codes"])
        self.assertTrue((run_dir / "brief" / "base" / "validated_brief.json").is_file())
        output = json.loads(
            (run_dir / "output" / "paper_brief.json").read_text(encoding="utf-8")
        )
        self.assertEqual("failed", output["revisit"]["status"])
        report = (run_dir / "review" / "paper_brief.md").read_text(encoding="utf-8")
        self.assertIn("回查失败，基础简报仍保留", report)
        self.assertIn("未将部分回查结果并入正式简报", report)

    def test_selection_cap_triggers_revisit_without_model_reported_gap(self) -> None:
        materials = [make_material(index) for index in range(1, 6)]
        selected_ids = [row["material_id"] for row in materials[:4]]
        client = FakeTopicClient(selected_ids=selected_ids)
        result = self.run_review(client, materials, run_id="revisit-cap")

        self.assertEqual("completed", result["status"])
        self.assertEqual("no_candidate", result["revisit_status"])
        revisit_call = next(row for row in client.calls if row["task"].endswith("revisit"))
        prompt = json.loads(revisit_call["request"]["messages"][1]["content"])
        self.assertEqual("selection_cap_reached", prompt["回查问题"][0]["source"])

    def test_peripheral_paper_does_not_auto_revisit(self) -> None:
        materials = [make_material(1), make_material(2)]
        client = FakeTopicClient(
            relevance="peripheral",
            selected_ids=[materials[0]["material_id"]],
            brief_gaps=[
                {
                    "gap_type": "method",
                    "question": "是否还有其他方法材料？",
                    "why_it_matters": "可能补充边缘方法背景。",
                }
            ],
        )
        result = self.run_review(client, materials, run_id="peripheral-no-revisit")

        self.assertEqual("completed", result["status"])
        self.assertEqual("not_triggered", result["revisit_status"])
        self.assertEqual(3, len(client.calls))

    def test_cli_parser_exposes_topic_brief_as_separate_command(self) -> None:
        args = build_parser().parse_args(
            [
                "llm-topic-brief",
                "--paper-id",
                "paper-one",
                "--topic",
                "三峡船舶积压与疏导",
                "--run-id",
                "cli-run",
            ]
        )
        self.assertEqual("llm-topic-brief", args.command)
        self.assertEqual("paper-one", args.paper_id)
        self.assertEqual(REVIEW_CONFIG.resolve(), args.review_config.resolve())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


if __name__ == "__main__":
    unittest.main()
