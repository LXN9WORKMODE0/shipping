from __future__ import annotations

import copy
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_model_profile import load_model_profile
from shipping_pipeline.llm_provider import ProviderResult
from shipping_pipeline.llm_tokenizer import TokenCount
from shipping_pipeline.review_writing import ReviewWritingRunner
from shipping_pipeline.review_writing_contracts import (
    build_review_chapter_schema,
    load_review_writing_collection,
    load_review_writing_config,
    validate_review_chapter,
)
from main import main as cli_main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / ".test_tmp"
MODEL_PROFILE = (
    PROJECT_ROOT / "config" / "models" / "deepseek-v4-pro-official.json"
)
WRITING_CONFIG = PROJECT_ROOT / "config" / "review-writing-default.json"


class FakeTokenCounter:
    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        count = max(1, len(json.dumps(messages, ensure_ascii=False)) // 4)
        return TokenCount(
            prompt_tokens=count,
            encoded_prompt_sha256=f"sha256:{count:064x}",
        )


class FakeWritingClient:
    provider = "openai-compatible"

    def __init__(self, *, fail_section: int | None = None) -> None:
        self.model = "deepseek-v4-pro"
        self.fail_section = fail_section
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        task: str,
        request_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> ProviderResult:
        self.calls.append(
            {
                "task": task,
                "request": copy.deepcopy(request_payload),
                "context": copy.deepcopy(context),
            }
        )
        section_index = int(context["section_index"])
        if section_index == self.fail_section:
            raise RuntimeError(f"section-{section_index}-failed")
        paragraph_count = (
            3 if context["section_type"] == "body" else 2
        )
        citation_keys = list(context["citation_keys"])
        content = {
            "schema_version": "llm.review_chapter.v1",
            "section_index": section_index,
            "title": context["section_title"],
            "paragraphs": [
                {
                    "paragraph_index": index,
                    "text": f"第{section_index}章第{index}段自然综述正文。",
                    "citation_keys": [citation_keys[0]],
                }
                for index in range(1, paragraph_count + 1)
            ],
        }
        prompt_tokens = int(context["planned_input_tokens"])
        response = {
            "id": f"response-{section_index}",
            "model": self.model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            content,
                            ensure_ascii=False,
                        ),
                        "reasoning_content": "",
                    },
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 100,
                "total_tokens": prompt_tokens + 100,
            },
        }
        return ProviderResult(
            raw_body=json.dumps(response, ensure_ascii=False).encode(),
            parsed_response=response,
            response_id=response["id"],
            response_model=self.model,
            system_fingerprint=None,
            finish_reason="stop",
            reasoning_content_chars=0,
            usage=dict(response["usage"]),
        )


class ReviewWritingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = TEST_TMP_ROOT / f"writing_{uuid.uuid4().hex}"
        self.workspace.mkdir(parents=True)
        self.profile = load_model_profile(MODEL_PROFILE)
        self.config = load_review_writing_config(WRITING_CONFIG)
        self.framework = self._framework()
        self.sources = [
            self._source(index)
            for index in range(1, 4)
        ]
        self.collection = self.workspace / "collection.json"
        self.collection.write_text(
            json.dumps(
                {
                    "schema_version": "llm.review_writing_collection.v1",
                    "framework_run_id": "framework-test",
                    "source_package_run_ids": [
                        source.run_id for source in self.sources
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_collection_rejects_duplicate_package_run_ids(self):
        payload = json.loads(self.collection.read_text(encoding="utf-8"))
        payload["source_package_run_ids"][1] = (
            payload["source_package_run_ids"][0]
        )
        self.collection.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "不得重复"):
            load_review_writing_collection(self.collection)

    def test_chapter_text_rejects_citation_key_and_machine_id(self):
        source = self.sources[1]
        base = {
            "schema_version": "llm.review_chapter.v1",
            "section_index": 2,
            "title": "比较",
            "paragraphs": [
                {
                    "paragraph_index": index,
                    "text": f"第{index}段。",
                    "citation_keys": ["ref-paper-a"],
                }
                for index in range(1, 4)
            ],
        }
        for invalid_text in (
            "结论来自ref-paper-a。",
            "结论来自evidence_12345678。",
        ):
            payload = copy.deepcopy(base)
            payload["paragraphs"][0]["text"] = invalid_text
            with self.subTest(invalid_text=invalid_text):
                with self.assertRaisesRegex(
                    ValueError,
                    "chapter.machine_id_in_text",
                ):
                    validate_review_chapter(payload, source.schema)

    def test_each_chapter_receives_only_its_own_package(self):
        client = FakeWritingClient()
        result = self._run(client, run_id="writing-isolated")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(client.calls), 3)
        for index, call in enumerate(client.calls, start=1):
            user_content = call["request"]["messages"][1]["content"]
            self.assertIn(f"package-{index}", user_content)
            for other in range(1, 4):
                if other != index:
                    self.assertNotIn(f"package-{other}", user_content)
        markdown = Path(result["review_path"]).read_text(encoding="utf-8")
        self.assertIn("## 资料范围", markdown)
        self.assertIn("## 参考文献", markdown)
        self.assertIn("[@ref-paper-a]", markdown)
        manifest = self._json(Path(result["manifest_path"]))
        self.assertTrue(manifest["formal_review_published"])
        self.assertEqual(
            manifest["final_edit"]["status"],
            "not_run_audit_required",
        )

    def test_failed_chapter_does_not_stop_later_chapters_or_publish_review(self):
        client = FakeWritingClient(fail_section=2)
        result = self._run(client, run_id="writing-partial")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(result["completed_chapter_count"], 2)
        self.assertEqual(result["failed_chapter_count"], 1)
        self.assertIsNone(result["review_path"])
        self.assertTrue(Path(result["partial_review_path"]).exists())
        run_dir = Path(result["run_dir"])
        self.assertTrue(
            (
                run_dir
                / "chapters"
                / "section-003"
                / "validated_chapter.json"
            ).exists()
        )
        self.assertFalse(
            (run_dir / "output" / "review_draft.json").exists()
        )
        partial = Path(result["partial_review_path"]).read_text(
            encoding="utf-8"
        )
        self.assertIn("第2章", partial)
        self.assertIn("RuntimeError", partial)

    def test_out_of_order_packages_fail_before_any_api_call(self):
        payload = json.loads(self.collection.read_text(encoding="utf-8"))
        payload["source_package_run_ids"][0:2] = reversed(
            payload["source_package_run_ids"][0:2]
        )
        self.collection.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        client = FakeWritingClient()

        result = self._run(client, run_id="writing-order-invalid")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(client.calls, [])
        manifest = self._json(Path(result["manifest_path"]))
        self.assertEqual(
            manifest["failure"]["error_code"],
            "writing.section_order_invalid",
        )

    def test_cli_dispatches_review_writing_command(self):
        class FakeRunner:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def run(self, **kwargs: Any) -> dict[str, Any]:
                self.kwargs = kwargs
                return {
                    "status": "completed",
                    "run_id": "cli-test",
                }

        with patch("main.ReviewWritingRunner", FakeRunner):
            code = cli_main(
                [
                    "llm-review-writing",
                    "--workspace",
                    str(self.workspace),
                    "--collection",
                    str(self.collection),
                    "--run-id",
                    "cli-test",
                ]
            )
        self.assertEqual(code, 0)

    def _run(
        self,
        client: FakeWritingClient,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        source_by_run = {source.run_id: source for source in self.sources}

        def loader(_workspace: Path, source_run_id: str) -> Any:
            return source_by_run[source_run_id]

        return ReviewWritingRunner(
            self.workspace,
            analysis_client=client,
            token_counter=FakeTokenCounter(),
            package_loader=loader,
        ).run(
            collection_path=self.collection,
            run_id=run_id,
        )

    def _source(self, section_index: int) -> Any:
        section = self.framework["sections"][section_index - 1]
        citation_keys = ["ref-paper-a"]
        schema = build_review_chapter_schema(
            section_index=section_index,
            section_title=section["title"],
            section_type=section["section_type"],
            allowed_citation_keys=citation_keys,
            config=self.config,
        )
        reference = {
            "number": 1,
            "reference_id": "ref-paper-a",
            "status": "complete",
            "missing_fields": [],
            "authors": ["作者甲"],
            "title": "论文A",
            "entry_type": "journal_article",
            "journal": "测试学报",
            "year": 2025,
            "volume": "1",
            "issue": "2",
            "pages": "1-10",
            "doi": None,
        }
        package = {
            "schema_version": "llm.chapter_knowledge_package.v1",
            "source": {
                "framework_run_id": "framework-test",
                "framework_id": "framework-id",
            },
            "section": {
                **copy.deepcopy(section),
                "paper_ids": ["paper-a"],
                "contribution_ids": ["contribution-a"],
                "citation_keys": citation_keys,
                "bibliography_status": "complete",
                "section_id": f"section-{section_index}",
            },
            "landscape_context": {},
            "papers": [
                {
                    "paper_id": "paper-a",
                    "citation_key": "ref-paper-a",
                    "bibliography": reference,
                }
            ],
            "package_id": f"package-{section_index}",
        }
        return SimpleNamespace(
            run_id=f"package-run-{section_index}",
            manifest_sha256=f"sha256:manifest-{section_index}",
            output_sha256=f"sha256:output-{section_index}",
            package=package,
            budget={"within_budget": True},
            profile=self.profile,
            config=self.config,
            schema=schema,
            framework=copy.deepcopy(self.framework),
        )

    @staticmethod
    def _framework() -> dict[str, Any]:
        return {
            "working_title": "测试综述",
            "central_question": "如何比较不同方法？",
            "sections": [
                {
                    "section_index": 1,
                    "title": "引言",
                    "section_type": "introduction",
                    "question": "为什么研究？",
                    "purpose": "界定范围。",
                    "dimension_indexes": [1],
                    "required_comparisons": [],
                    "controversy_ids": [],
                    "corpus_gap_indexes": [],
                    "corpus_limitations": ["仅限测试语料。"],
                },
                {
                    "section_index": 2,
                    "title": "比较",
                    "section_type": "body",
                    "question": "方法有何不同？",
                    "purpose": "开展比较。",
                    "dimension_indexes": [1],
                    "required_comparisons": ["比较两种方法。"],
                    "controversy_ids": [],
                    "corpus_gap_indexes": [],
                    "corpus_limitations": ["证据有限。"],
                },
                {
                    "section_index": 3,
                    "title": "结论",
                    "section_type": "conclusion",
                    "question": "可以得出什么？",
                    "purpose": "总结边界。",
                    "dimension_indexes": [1],
                    "required_comparisons": [],
                    "controversy_ids": [],
                    "corpus_gap_indexes": [],
                    "corpus_limitations": ["仅限测试语料。"],
                },
            ],
        }

    @staticmethod
    def _json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
