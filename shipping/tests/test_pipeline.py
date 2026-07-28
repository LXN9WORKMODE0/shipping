from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import unittest
import uuid
import zipfile
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.pipeline import LiteraturePipeline, _slugify
from shipping_pipeline.audit import BatchAuditRunner
from shipping_pipeline.material_quality import MaterialQualityAuditor
from shipping_pipeline.material_review import MaterialReviewExporter
from shipping_pipeline.pdf_conversion import ConvertedDocument, MinerUApiClient
from shipping_pipeline.env import load_env_file

TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".test_tmp"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)


class FakePdfConverter:
    provider = "mineru"

    def convert(self, source_path: Path) -> ConvertedDocument:
        return ConvertedDocument(
            markdown="# PDF Paper\n\n## 第一章 方法\n\n本文使用仿真方法分析船舶积压。\n",
            provider=self.provider,
            metadata={"request_id": "fake-mineru"},
        )


def make_case_dir() -> Path:
    path = TEST_TMP_ROOT / f"case_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class PipelineTests(unittest.TestCase):
    def test_load_env_file_loads_api_settings_without_overriding_shell_env(self) -> None:
        tmp_path = make_case_dir()
        env_path = tmp_path / ".env"
        env_path.write_text(
            """
# comments are ignored
MINERU_API_URL=
MINERU_API_KEY=file-key
LLM_ANALYSIS_API_URL="https://llm.example/v1/chat/completions"
LLM_ANALYSIS_API_KEY=file-llm-key
""",
            encoding="utf-8",
        )
        tracked_keys = ["MINERU_API_URL", "MINERU_API_KEY", "LLM_ANALYSIS_API_URL", "LLM_ANALYSIS_API_KEY"]
        original = {key: os.environ.get(key) for key in tracked_keys}
        try:
            for key in tracked_keys:
                os.environ.pop(key, None)
            os.environ["MINERU_API_KEY"] = "shell-key"

            loaded = load_env_file(env_path)

            self.assertNotIn("MINERU_API_URL", os.environ)
            self.assertEqual(os.environ["MINERU_API_KEY"], "shell-key")
            self.assertEqual(os.environ["LLM_ANALYSIS_API_URL"], "https://llm.example/v1/chat/completions")
            self.assertEqual(os.environ["LLM_ANALYSIS_API_KEY"], "file-llm-key")
            self.assertEqual(loaded["LLM_ANALYSIS_API_KEY"], "file-llm-key")
            self.assertNotIn("MINERU_API_KEY", loaded)
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_pipeline_rejects_ambiguous_document_bundle(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "bundle.md"
        source.write_text(
            "# 第一篇\n\n正文一。\n\n# 第二篇\n\n正文二。\n",
            encoding="utf-8",
        )
        workspace = tmp_path / "workspace"

        result = LiteraturePipeline(workspace).run(source, paper_id="目标论文")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.structure_quality, "red")
        self.assertFalse((workspace / "目标论文" / "materials" / "materials.jsonl").exists())
        quality = read_json(workspace / "目标论文" / "structure" / "quality.json")
        self.assertIn("parse.document_scope_ambiguous", [issue["code"] for issue in quality["issues"]])

    def test_failed_rerun_invalidates_previous_material_generation(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "paper.md"
        workspace = tmp_path / "workspace"
        source.write_text("# 论文\n\n## 1 方法\n\n第一代正文。\n", encoding="utf-8")
        pipeline = LiteraturePipeline(workspace)

        first = pipeline.run(source, paper_id="论文")
        source.write_text("# 第一篇\n\n正文一。\n\n# 第二篇\n\n正文二。\n", encoding="utf-8")
        second = pipeline.run(source, paper_id="论文")

        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "failed")
        self.assertEqual(read_jsonl(workspace / "_corpus" / "materials.jsonl"), [])
        self.assertFalse((workspace / "论文" / "materials" / "materials.jsonl").exists())
        current = read_json(workspace / "论文" / "materials" / "current.json")
        self.assertEqual(current["status"], "failed")
        self.assertIsNone(current["generation_id"])
        archived = list((workspace / "论文" / "materials" / "generations").glob("*/materials.jsonl"))
        self.assertTrue(archived)

    def test_material_integrity_failure_is_recorded_instead_of_escaping(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "paper.md"
        workspace = tmp_path / "workspace"
        source.write_text("# 论文\n\n摘要：<sup></sup>\n", encoding="utf-8")

        result = LiteraturePipeline(workspace).run(source, paper_id="论文")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.structure_quality, "red")
        run = read_json(workspace / "论文" / "run.json")
        self.assertIn("coverage.materialization_failed", [issue["code"] for issue in run["issues"]])
        self.assertEqual(read_jsonl(workspace / "_corpus" / "materials.jsonl"), [])
        self.assertFalse((workspace / "论文" / "materials" / "materials.jsonl").exists())

    def test_unicode_slug_keeps_distinct_chinese_workspace_ids(self) -> None:
        first = _slugify("三峡航运遇瓶颈")
        second = _slugify("长江航运组织")

        self.assertEqual(first, "三峡航运遇瓶颈")
        self.assertEqual(second, "长江航运组织")
        self.assertNotEqual(first, second)

    def test_pipeline_preserves_abstract_and_excludes_references(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "paper.md"
        source.write_text(
            "# 论文\n\n## 摘要\n\n摘要证据。\n\n## 1、方法\n\n正文证据。\n\n## 参考文献：\n\n禁止进入卡片。\n",
            encoding="utf-8",
        )
        workspace = tmp_path / "workspace"

        result = LiteraturePipeline(workspace).run(source, paper_id="论文")
        materials = read_jsonl(workspace / "论文" / "materials" / "materials.jsonl")

        self.assertEqual(result.status, "completed")
        self.assertIn("abstract", {item["content_kind"] for item in materials})
        self.assertNotIn("禁止进入卡片", " ".join(item["extract"] for item in materials))
        structure = read_json(workspace / "论文" / "structure" / "structure.json")
        back_matter = next(region for region in structure["regions"] if region["role"] == "back_matter")
        self.assertEqual(back_matter["start_line"], 11)

    def test_pipeline_builds_gold_chapter_materials_and_review_pack(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "source.md"
        source.write_text(
            """# Test Paper

摘要：这是一篇测试论文。

## 第一章 绪论

研究背景和研究问题。这里说明论文关注的对象。

## 第二章 方法

方法部分说明数据来源和分析方法。

## 第三章 结论

结论部分说明主要发现。

## 参考文献

[1] Example.
""",
            encoding="utf-8",
        )

        workspace = tmp_path / "workspace"
        pipeline = LiteraturePipeline(workspace)

        result = pipeline.run(source, paper_id="test-paper", review_topic="研究 方法")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.structure_quality, "gold")
        self.assertEqual(result.issue_count, 0)

        paper_workspace = workspace / "test-paper"
        self.assertTrue((paper_workspace / "normalized" / "document.md").exists())
        self.assertTrue((paper_workspace / "structure" / "structure.json").exists())
        self.assertTrue((paper_workspace / "materials" / "materials.jsonl").exists())
        self.assertTrue((workspace / "_corpus" / "materials.jsonl").exists())
        self.assertTrue((workspace / "_review_packs" / "yan-jiu-fang-fa.json").exists())

        structure = read_json(paper_workspace / "structure" / "structure.json")
        self.assertEqual(
            [node["title_raw"] for node in structure["nodes"]],
            ["第一章 绪论", "第二章 方法", "第三章 结论"],
        )

        materials = read_jsonl(paper_workspace / "materials" / "materials.jsonl")
        self.assertEqual([item["material_type"] for item in materials], ["evidence_card"] * 4)
        self.assertEqual([item["content_kind"] for item in materials], ["abstract", "prose", "prose", "prose"])
        self.assertEqual(materials[0]["paper_id"], "test-paper")
        self.assertEqual(materials[0]["schema_version"], "material.v2")
        self.assertIn("extract", materials[0])
        self.assertIn("lead_excerpt", materials[0])
        self.assertIn("quality_flags", materials[0])
        self.assertEqual(materials[0]["source_span"], materials[0]["content_ref"])
        self.assertNotIn("第一章", materials[0]["summary"])
        self.assertLessEqual(materials[0]["content_ref"]["start_line"], materials[0]["content_ref"]["end_line"])
        self.assertEqual(materials[0]["confidence_flags"], [])
        self.assertEqual(structure["coverage"]["body_coverage_ratio"], 1.0)
        self.assertEqual(structure["coverage"]["source_to_region_ratio"], 1.0)
        self.assertEqual(structure["coverage"]["region_to_block_ratio"], 1.0)
        self.assertEqual(structure["coverage"]["block_to_card_ratio"], 1.0)
        self.assertEqual(structure["coverage"]["unassigned_source_line_numbers"], [])
        self.assertEqual(structure["coverage"]["unblocked_source_line_numbers"], [])

        corpus_materials = read_jsonl(workspace / "_corpus" / "materials.jsonl")
        self.assertEqual(
            [item["material_id"] for item in corpus_materials],
            [item["material_id"] for item in materials],
        )

        review_pack = read_json(workspace / "_review_packs" / "yan-jiu-fang-fa.json")
        self.assertEqual(review_pack["topic"], "研究 方法")
        self.assertEqual(review_pack["source_corpus"], "_corpus/materials.jsonl")
        self.assertTrue(review_pack["evidence_matrix"])
        self.assertEqual(review_pack["issues"], [])

    def test_pipeline_degrades_to_evidence_chunks_and_records_issue_when_structure_is_unusable(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "source.md"
        source.write_text(
            """# Weak Paper

这篇文档有正文，但是没有可识别的章级标题。

第一段讨论研究背景和问题。

第二段讨论可能的方法和观察。

第三段讨论结论和不足。
""",
            encoding="utf-8",
        )

        workspace = tmp_path / "workspace"
        pipeline = LiteraturePipeline(workspace)

        result = pipeline.run(source, paper_id="weak-paper", review_topic="研究问题")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.structure_quality, "silver")
        self.assertEqual(result.issue_count, 1)

        paper_workspace = workspace / "weak-paper"
        quality = read_json(paper_workspace / "structure" / "quality.json")
        self.assertEqual(quality["label"], "silver")
        self.assertEqual(quality["issues"][0]["code"], "parse.no_structured_body")

        materials = read_jsonl(paper_workspace / "materials" / "materials.jsonl")
        self.assertTrue(materials)
        self.assertEqual({item["material_type"] for item in materials}, {"evidence_card"})
        self.assertTrue(all("parse.no_structured_body" in item["confidence_flags"] for item in materials))
        self.assertTrue(all(item["schema_version"] == "material.v2" for item in materials))
        self.assertTrue(all(item["clean_title"].startswith("正文片段") for item in materials))
        self.assertTrue(all(item["heading_path"] == [] for item in materials))
        self.assertTrue(all(item["source_block_ids"] for item in materials))

        issues = read_jsonl(workspace / "_corpus" / "issues.jsonl")
        self.assertEqual([issue["code"] for issue in issues], ["parse.no_structured_body"])

    def test_pipeline_detects_markdown_arabic_numbered_chapter_roots(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "source.md"
        source.write_text(
            """# Arabic Numbered Paper

## 0 引言

引言说明研究背景。

## 1 研究方法

方法部分说明数据来源。

## 1.1 数据来源

这是方法章节下的子标题，不应被提升为章级根。

## 2 结论

结论说明主要发现。
""",
            encoding="utf-8",
        )

        workspace = tmp_path / "workspace"
        result = LiteraturePipeline(workspace).run(source, paper_id="arabic-numbered", review_topic="研究 方法")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.structure_quality, "gold")
        structure = read_json(workspace / "arabic-numbered" / "structure" / "structure.json")
        self.assertEqual(
            [node["title_raw"] for node in structure["nodes"]],
            ["0 引言", "1 研究方法", "1.1 数据来源", "2 结论"],
        )

    def test_pipeline_marks_plain_numbered_headings_as_inferred_structure(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "source.md"
        source.write_text(
            """# Plain Numbered Paper

1 引言

这里说明三峡船舶积压研究背景和问题来源。

2 研究方法

这里说明采用统计分析和调度观察来讨论通航组织。

3 结论

这里说明主要发现和后续建议。
""",
            encoding="utf-8",
        )

        workspace = tmp_path / "workspace"
        result = LiteraturePipeline(workspace).run(source, paper_id="plain-numbered", review_topic="研究 方法")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.structure_quality, "silver")
        run_record = read_json(workspace / "plain-numbered" / "run.json")
        self.assertEqual([issue["code"] for issue in run_record["issues"]], ["parse.inferred_headings"])
        structure = read_json(workspace / "plain-numbered" / "structure" / "structure.json")
        self.assertEqual([node["title_raw"] for node in structure["nodes"]], ["1 引言", "2 研究方法", "3 结论"])
        self.assertTrue(all("heading.inferred" in node["confidence_flags"] for node in structure["nodes"]))
        materials = read_jsonl(workspace / "plain-numbered" / "materials" / "materials.jsonl")
        self.assertTrue(all("parse.inferred_headings" in material["confidence_flags"] for material in materials))

    def test_unstructured_cards_expose_weak_inferred_titles_without_repeating_summary(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "source.md"
        source.write_text(
            """# Weak Title Paper

假定方案三和方案四两方案滚装车辆在滚装码头完成换装后继续通行。

车辆到达节奏、等待时间和转运组织会影响翻坝运输效率。

相关方案仍需要结合水位、天气和船闸运行计划进行复核。
""",
            encoding="utf-8",
        )

        workspace = tmp_path / "workspace"
        result = LiteraturePipeline(workspace).run(source, paper_id="weak-title", review_topic="翻坝运输")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.structure_quality, "silver")
        materials = read_jsonl(workspace / "weak-title" / "materials" / "materials.jsonl")
        self.assertTrue(materials[0]["clean_title"].startswith("正文片段 001"))
        self.assertNotIn("title.weak_inferred", materials[0]["quality_flags"])
        self.assertFalse(materials[0]["summary"].startswith(materials[0]["clean_title"]))
        audit = MaterialQualityAuditor(workspace).run(run_id="weak-title-audit")
        self.assertEqual(audit["issue_counts"], {"parse.no_structured_body": 1})

    def test_pipeline_splits_long_chapter_into_bounded_clean_cards(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "source.md"
        long_body = "\n\n".join(
            [
                "研究背景说明三峡船舶积压、航运组织和调度策略。" * 18,
                "<table><tr><td>参数</td><td>结果</td></tr></table>",
                "方法部分讨论排队、仿真和调度优化。" * 18,
                "结果部分说明等待时间、排队长度和通航效率。" * 18,
                "结论部分说明策略有效但仍需结合天气和需求波动。" * 18,
            ]
        )
        source.write_text(
            f"""# Long Paper

## 第一章 绪论

{long_body}

## 第二章 结论

主要结论。
""",
            encoding="utf-8",
        )

        workspace = tmp_path / "workspace"
        result = LiteraturePipeline(workspace).run(source, paper_id="long-paper", review_topic="研究 方法 结论")

        self.assertEqual(result.status, "completed")
        materials = read_jsonl(workspace / "long-paper" / "materials" / "materials.jsonl")
        first_chapter_cards = [
            item
            for item in materials
            if item["heading_path"] and item["heading_path"][0] == "第一章 绪论"
        ]
        self.assertGreater(len(first_chapter_cards), 1)
        self.assertTrue(all(item["material_type"] == "evidence_card" for item in materials))
        self.assertTrue(all(len(item["extract"]) <= 1400 for item in first_chapter_cards))
        self.assertTrue(all("<table" not in item["extract"] for item in first_chapter_cards))
        table_card = next(item for item in first_chapter_cards if item["content_kind"] == "table")
        self.assertIn("参数 | 结果", table_card["extract"])
        self.assertNotIn("clean.removed_table", table_card["quality_flags"])
        self.assertTrue(all(not item["summary"].startswith("第一章") for item in first_chapter_cards))

    def test_pipeline_gives_english_abstract_chunks_semantic_titles(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "source.md"
        source.write_text(
            """# Weak English Tail

Abstract: Since the ship lock opened, navigation demand has increased and backlog pressure has become visible.

Keywords: traffic control line; ship lock; simulation; backlog mitigation.

The body discusses research context, method design, simulation results, and conclusion without chapter headings.
""",
            encoding="utf-8",
        )

        workspace = tmp_path / "workspace"
        result = LiteraturePipeline(workspace).run(source, paper_id="english-tail", review_topic="simulation")

        self.assertEqual(result.status, "completed")
        materials = read_jsonl(workspace / "english-tail" / "materials" / "materials.jsonl")
        self.assertEqual(materials[0]["clean_title"], "Weak English Tail > 摘要")
        self.assertEqual(materials[0]["content_kind"], "abstract")
        self.assertIn("prose", {item["content_kind"] for item in materials})
        audit = MaterialQualityAuditor(workspace).run(run_id="english-tail-audit")
        self.assertNotIn("title.polluted", audit["issue_counts"])

    def test_pipeline_fails_unsupported_inputs_without_silent_conversion(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "paper.pdf"
        source.write_bytes(b"%PDF-1.4 fake")

        workspace = tmp_path / "workspace"
        pipeline = LiteraturePipeline(workspace)

        result = pipeline.run(source, paper_id="pdf-paper", review_topic="anything")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.structure_quality, "red")
        self.assertEqual(result.issue_count, 1)

        run_record = read_json(workspace / "pdf-paper" / "run.json")
        self.assertEqual(run_record["status"], "failed")
        self.assertEqual(run_record["stages"]["convert"]["status"], "failed")
        self.assertEqual(run_record["issues"][0]["code"], "convert.unsupported_input")
        self.assertFalse((workspace / "pdf-paper" / "materials" / "materials.jsonl").exists())

    def test_pipeline_uses_configured_mineru_converter_for_pdf(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "paper.pdf"
        source.write_bytes(b"%PDF-1.4 fake")
        workspace = tmp_path / "workspace"

        result = LiteraturePipeline(workspace, pdf_converter=FakePdfConverter()).run(
            source,
            paper_id="pdf-paper",
            review_topic="仿真",
        )

        self.assertEqual(result.status, "completed")
        run_record = read_json(workspace / "pdf-paper" / "run.json")
        self.assertEqual(run_record["stages"]["convert"]["provider"], "mineru")
        self.assertEqual(run_record["stages"]["convert"]["metadata"]["request_id"], "fake-mineru")
        normalized = (workspace / "pdf-paper" / "normalized" / "document.md").read_text(encoding="utf-8")
        self.assertIn("# PDF Paper", normalized)
        materials = read_jsonl(workspace / "pdf-paper" / "materials" / "materials.jsonl")
        self.assertTrue(materials)

    def test_mineru_api_client_extracts_markdown_from_api_response(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "paper.pdf"
        source.write_bytes(b"%PDF-1.4 fake")
        calls = []

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as archive:
            archive.writestr("paper/full.md", "# API Paper\n\n## 第一章 结论\n\n主要结论。")

        poll_count = 0

        def fake_transport(method: str, url: str, payload, headers: dict[str, str], timeout: int):
            nonlocal poll_count
            calls.append({"method": method, "url": url, "payload": payload, "headers": headers, "timeout": timeout})
            if method == "POST":
                self.assertEqual(url, "https://mineru.net/api/v4/file-urls/batch")
                self.assertEqual(headers["Authorization"], "Bearer secret")
                self.assertEqual(payload["model_version"], "vlm")
                self.assertEqual(payload["language"], "ch")
                self.assertEqual(payload["enable_table"], True)
                self.assertEqual(payload["enable_formula"], True)
                self.assertEqual(payload["files"][0]["name"], "paper.pdf")
                self.assertEqual(payload["files"][0]["is_ocr"], False)
                return {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://oss.example/upload"]}, "trace_id": "trace-1"}
            if method == "PUT":
                self.assertEqual(url, "https://oss.example/upload")
                self.assertEqual(payload, b"%PDF-1.4 fake")
                self.assertNotIn("Content-Type", headers)
                return {"status": 200}
            if method == "GET" and "extract-results" in url:
                poll_count += 1
                state = "running" if poll_count == 1 else "done"
                result = {"file_name": "paper.pdf", "state": state, "err_msg": ""}
                if state == "done":
                    result["full_zip_url"] = "https://cdn.example/result.zip"
                return {"code": 0, "data": {"batch_id": "batch-1", "extract_result": [result]}, "trace_id": f"trace-poll-{poll_count}"}
            if method == "GET" and url == "https://cdn.example/result.zip":
                return zip_buffer.getvalue()
            raise AssertionError(f"Unexpected MinerU request: {method} {url}")

        client = MinerUApiClient(
            api_url="https://mineru.net",
            api_key="secret",
            timeout=77,
            max_wait_seconds=10,
            poll_interval_seconds=0,
            transport=fake_transport,
        )

        converted = client.convert(source)

        self.assertEqual(converted.provider, "mineru")
        self.assertIn("# API Paper", converted.markdown)
        self.assertEqual(converted.metadata["batch_id"], "batch-1")
        self.assertEqual(converted.metadata["full_zip_url"], "https://cdn.example/result.zip")
        self.assertEqual(converted.metadata["model_version"], "vlm")
        self.assertEqual([call["method"] for call in calls], ["POST", "PUT", "GET", "GET", "GET"])
        self.assertEqual(calls[0]["timeout"], 77)

    def test_cli_runs_pipeline_and_prints_json_result(self) -> None:
        tmp_path = make_case_dir()
        source = tmp_path / "source.md"
        source.write_text(
            """# CLI Paper

## 第一章 绪论

研究问题。

## 第二章 结论

主要结论。
""",
            encoding="utf-8",
        )
        workspace = tmp_path / "workspace"

        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "main.py"),
                "run",
                str(source),
                "--workspace",
                str(workspace),
                "--paper-id",
                "cli-paper",
                "--topic",
                "研究",
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["paper_id"], "cli-paper")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["structure_quality"], "gold")

    def test_cli_help_writes_utf8_chinese_output(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "main.py"),
                "--help",
            ],
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        stdout = completed.stdout.decode("utf-8")
        self.assertIn("运行通用文献材料处理 pipeline", stdout)

    def test_batch_audit_processes_existing_normalized_markdown_and_writes_report(self) -> None:
        tmp_path = make_case_dir()
        source_root = tmp_path / "source_workspace"
        gold_doc = source_root / "gold-paper" / "normalized" / "document.md"
        silver_doc = source_root / "silver-paper" / "normalized" / "document.md"
        gold_doc.parent.mkdir(parents=True)
        silver_doc.parent.mkdir(parents=True)
        gold_doc.write_text(
            """# Gold Paper

## 第一章 绪论

研究背景。

## 第二章 结论

主要结论。
""",
            encoding="utf-8",
        )
        silver_doc.write_text(
            """# Silver Paper

这篇文档有正文，但是没有章级标题。

它讨论研究背景、方法和结论。

为了满足材料抽取条件，这里继续补充一段正文，说明研究对象、观察现象和可能的综述价值。
""",
            encoding="utf-8",
        )

        workspace = tmp_path / "workspace"
        result = BatchAuditRunner(workspace).run(source_root, topic="研究", run_id="test-run")

        self.assertEqual(result["run_id"], "test-run")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["quality_counts"], {"gold": 1, "silver": 1})
        self.assertEqual(result["issue_counts"], {"parse.no_structured_body": 1})

        audit_dir = workspace / "_audit" / "test-run"
        results = read_json(audit_dir / "results.json")
        report = (audit_dir / "report.md").read_text(encoding="utf-8")
        self.assertEqual(results["summary"]["total"], 2)
        self.assertEqual([item["paper_id"] for item in results["papers"]], ["gold-paper", "silver-paper"])
        self.assertIn("# 批量审计报告", report)
        self.assertIn("gold: 1", report)
        self.assertIn("silver: 1", report)
        self.assertIn("parse.no_structured_body: 1", report)

    def test_cli_audit_runs_batch_audit_and_prints_json_result(self) -> None:
        tmp_path = make_case_dir()
        source_root = tmp_path / "source_workspace"
        document = source_root / "paper-one" / "normalized" / "document.md"
        document.parent.mkdir(parents=True)
        document.write_text(
            """# Paper One

## 第一章 绪论

研究背景。

## 第二章 结论

主要结论。
""",
            encoding="utf-8",
        )
        workspace = tmp_path / "workspace"

        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "main.py"),
                "audit",
                str(source_root),
                "--workspace",
                str(workspace),
                "--topic",
                "研究",
                "--run-id",
                "cli-audit",
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["run_id"], "cli-audit")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["quality_counts"], {"gold": 1})
        self.assertTrue((workspace / "_audit" / "cli-audit" / "report.md").exists())

    def test_material_quality_audit_exposes_bad_materials_and_writes_report(self) -> None:
        tmp_path = make_case_dir()
        workspace = tmp_path / "workspace"
        source = workspace / "paper-one" / "normalized" / "document.md"
        source.parent.mkdir(parents=True)
        source.write_text(
            """# Paper One

## 第一章 绪论

研究背景和问题。
""",
            encoding="utf-8",
        )
        materials = [
            {
                "material_id": "paper-one:chapter_001",
                "material_type": "chapter",
                "paper_id": "paper-one",
                "paper_title": "Paper One",
                "source_path": "normalized/document.md",
                "raw_title": "第一章 绪论",
                "clean_title": "绪论",
                "order": 1,
                "summary": "第一章 绪论 研究背景和问题。",
                "content_ref": {"path": "normalized/document.md", "start_line": 3, "end_line": 5},
                "confidence_flags": [],
            },
            {
                "material_id": "paper-one:chunk_001",
                "material_type": "evidence_chunk",
                "paper_id": "paper-one",
                "paper_title": "Paper One",
                "source_path": "normalized/document.md",
                "raw_title": "仿真结果与结果分析Record",
                "clean_title": "仿真结果与结果分析Record",
                "order": 2,
                "summary": "",
                "content_ref": {"path": "normalized/document.md", "start_line": 10, "end_line": 12},
                "confidence_flags": ["parse.no_chapter_roots"],
            },
            {
                "material_id": "paper-one:chunk_002",
                "material_type": "evidence_chunk",
                "paper_id": "paper-one",
                "paper_title": "Paper One",
                "source_path": "normalized/document.md",
                "raw_title": "Evidence chunk 2",
                "clean_title": "Evidence chunk 2",
                "order": 3,
                "summary": "<table><tr><td>参数</td></tr></table> 表格内容混入摘要。",
                "content_ref": {"path": "normalized/document.md", "start_line": 3, "end_line": 5},
                "confidence_flags": ["parse.no_chapter_roots"],
            },
        ]
        corpus = workspace / "_corpus" / "materials.jsonl"
        corpus.parent.mkdir(parents=True)
        corpus.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in materials), encoding="utf-8")

        result = MaterialQualityAuditor(workspace).run(run_id="mq-test")

        self.assertEqual(result["run_id"], "mq-test")
        self.assertEqual(result["total_materials"], 3)
        self.assertEqual(result["issue_counts"]["content_ref.out_of_range"], 1)
        self.assertEqual(result["issue_counts"]["summary.empty"], 1)
        self.assertEqual(result["issue_counts"]["summary.repeats_title"], 1)
        self.assertEqual(result["issue_counts"]["summary.contains_markup"], 1)
        self.assertEqual(result["issue_counts"]["title.generic"], 1)
        self.assertEqual(result["issue_counts"]["title.polluted"], 1)

        audit_dir = workspace / "_audit" / "mq-test"
        payload = read_json(audit_dir / "material-quality.json")
        report = (audit_dir / "material-quality.md").read_text(encoding="utf-8")
        self.assertEqual(payload["summary"]["total_materials"], 3)
        self.assertIn("# 材料质量审计结论", report)
        self.assertIn("结论", report)
        self.assertIn("影响", report)
        self.assertIn("建议", report)
        self.assertIn("content_ref.out_of_range: 1", report)
        self.assertIn("summary.empty: 1", report)
        self.assertIn("summary.repeats_title: 1", report)
        self.assertIn("summary.contains_markup: 1", report)
        self.assertIn("title.generic: 1", report)
        self.assertIn("title.polluted: 1", report)

    def test_cli_material_audit_prints_json_result(self) -> None:
        tmp_path = make_case_dir()
        workspace = tmp_path / "workspace"
        source = workspace / "paper-one" / "normalized" / "document.md"
        source.parent.mkdir(parents=True)
        source.write_text("# Paper One\n\n## 第一章 绪论\n\n研究背景。\n", encoding="utf-8")
        corpus = workspace / "_corpus" / "materials.jsonl"
        corpus.parent.mkdir(parents=True)
        corpus.write_text(
            json.dumps(
                {
                    "material_id": "paper-one:chapter_001",
                    "material_type": "chapter",
                    "paper_id": "paper-one",
                    "paper_title": "Paper One",
                    "source_path": "normalized/document.md",
                    "raw_title": "第一章 绪论",
                    "clean_title": "绪论",
                    "order": 1,
                    "summary": "研究背景。",
                    "content_ref": {"path": "normalized/document.md", "start_line": 3, "end_line": 5},
                    "confidence_flags": [],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "main.py"),
                "material-audit",
                "--workspace",
                str(workspace),
                "--run-id",
                "cli-mq",
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["run_id"], "cli-mq")
        self.assertEqual(payload["total_materials"], 1)
        self.assertTrue((workspace / "_audit" / "cli-mq" / "material-quality.md").exists())

    def test_material_quality_audit_accepts_clean_v2_materials(self) -> None:
        tmp_path = make_case_dir()
        workspace = tmp_path / "workspace"
        source = workspace / "paper-one" / "normalized" / "document.md"
        source.parent.mkdir(parents=True)
        source.write_text("# Paper One\n\n## 第一章 绪论\n\n研究背景清楚。\n", encoding="utf-8")
        structure = workspace / "paper-one" / "structure" / "structure.json"
        structure.parent.mkdir(parents=True)
        structure.write_text(
            json.dumps(
                {
                    "paper_id": "paper-one",
                    "coverage": {
                        "source_to_region_ratio": 1.0,
                        "region_to_block_ratio": 1.0,
                        "block_to_card_ratio": 1.0,
                        "body_coverage_ratio": 1.0,
                        "unassigned_source_line_numbers": [],
                        "multiply_classified_source_line_numbers": [],
                        "unblocked_source_line_numbers": [],
                        "unmaterialized_block_ids": [],
                        "multiply_materialized_block_ids": [],
                    },
                    "regions": [],
                    "stats": {},
                    "selected_segment": {"start_line": 1, "end_line": 5, "h1_line": 1},
                    "line_ledger": [
                        {"line_number": 1, "role": "scope_marker", "included_in_materials": False, "assignment_count": 1},
                        {"line_number": 3, "role": "scope_marker", "included_in_materials": False, "assignment_count": 1},
                        {"line_number": 5, "role": "body", "included_in_materials": True, "assignment_count": 1},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        material = {
            "schema_version": "material.v2",
            "material_id": "paper-one:chapter_001:card_001",
            "material_type": "evidence_card",
            "paper_id": "paper-one",
            "paper_title": "Paper One",
            "source_path": "normalized/document.md",
            "raw_title": "第一章 绪论",
            "clean_title": "绪论",
            "parent_ref": {"node_id": "chapter_001", "title": "绪论"},
            "order": 1,
            "summary": "研究背景清楚。",
            "lead_excerpt": "研究背景清楚。",
            "extract": "研究背景清楚。",
            "source_span": {"path": "normalized/document.md", "start_line": 3, "end_line": 5},
            "content_ref": {"path": "normalized/document.md", "start_line": 3, "end_line": 5},
            "confidence_flags": [],
            "quality_flags": [],
        }
        corpus = workspace / "_corpus" / "materials.jsonl"
        corpus.parent.mkdir(parents=True)
        corpus.write_text(json.dumps(material, ensure_ascii=False) + "\n", encoding="utf-8")

        result = MaterialQualityAuditor(workspace).run(run_id="mq-clean")

        self.assertEqual(result["total_materials"], 1)
        self.assertEqual(result["materials_with_issues"], 0)
        self.assertEqual(result["issue_counts"], {})
        report = (workspace / "_audit" / "mq-clean" / "material-quality.md").read_text(encoding="utf-8")
        self.assertIn("当前材料卡整体可进入下一步人工抽查。", report)

    def test_material_audit_reports_structure_coverage_and_cross_paper_duplicates(self) -> None:
        tmp_path = make_case_dir()
        workspace = tmp_path / "workspace"
        materials = []
        for paper_id, coverage in (("paper-a", 0.8), ("paper-b", 1.0)):
            paper_dir = workspace / paper_id
            (paper_dir / "normalized").mkdir(parents=True)
            (paper_dir / "structure").mkdir(parents=True)
            (paper_dir / "normalized" / "document.md").write_text(
                "# 论文\n\n## 结果\n\n<table><tr><td>A</td><td>12.5</td></tr></table>\n",
                encoding="utf-8",
            )
            (paper_dir / "structure" / "structure.json").write_text(
                json.dumps(
                    {
                        "paper_id": paper_id,
                        "coverage": {
                            "source_to_region_ratio": 1.0,
                            "region_to_block_ratio": 1.0,
                            "block_to_card_ratio": coverage,
                            "body_coverage_ratio": coverage,
                            "unassigned_source_line_numbers": [],
                            "multiply_classified_source_line_numbers": [],
                            "unblocked_source_line_numbers": [],
                            "unmaterialized_block_ids": ["missing"] if coverage < 1.0 else [],
                            "multiply_materialized_block_ids": [],
                        },
                        "regions": [],
                        "stats": {"inferred_heading_count": 0},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            materials.append(
                {
                    "schema_version": "material.v2",
                    "material_id": f"{paper_id}:table:L00005-L00005:01",
                    "material_type": "evidence_card",
                    "paper_id": paper_id,
                    "paper_title": "论文",
                    "source_path": "normalized/document.md",
                    "raw_title": "结果",
                    "clean_title": "结果 > 表格",
                    "parent_ref": {"node_id": "result", "title": "结果"},
                    "order": 1,
                    "summary": "方案 A 的等待时间为 12.5。",
                    "lead_excerpt": "方案 A 的等待时间为 12.5。",
                    "extract": "方案 | 等待时间\nA | 12.5",
                    "source_span": {"path": "normalized/document.md", "start_line": 5, "end_line": 5},
                    "content_ref": {"path": "normalized/document.md", "start_line": 5, "end_line": 5},
                    "confidence_flags": [],
                    "quality_flags": [],
                    "content_kind": "table",
                    "heading_path": ["结果"],
                    "source_spans": [{"path": "normalized/document.md", "start_line": 5, "end_line": 5}],
                    "source_block_ids": [f"{paper_id}:block"],
                    "source_fingerprint": "sha256:same",
                }
            )

        corpus = workspace / "_corpus"
        corpus.mkdir()
        (corpus / "materials.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in materials),
            encoding="utf-8",
        )

        result = MaterialQualityAuditor(workspace).run(run_id="integrity")

        self.assertEqual(result["issue_counts"]["coverage.missing_body_content"], 1)
        self.assertEqual(result["issue_counts"]["corpus.duplicate_content"], 2)
        self.assertNotIn("extract.contains_markup", result["issue_counts"])
        payload = read_json(workspace / "_audit" / "integrity" / "material-quality.json")
        paper_a = next(item for item in payload["papers"] if item["paper_id"] == "paper-a")
        self.assertEqual(paper_a["body_coverage_ratio"], 0.8)
        self.assertEqual(paper_a["content_kind_counts"], {"table": 1})

    def test_material_audit_includes_failed_zero_card_paper_and_missing_coverage(self) -> None:
        tmp_path = make_case_dir()
        workspace = tmp_path / "workspace"
        paper = workspace / "failed-paper"
        (paper / "normalized").mkdir(parents=True)
        (paper / "structure").mkdir(parents=True)
        (workspace / "_corpus").mkdir(parents=True)
        (paper / "normalized" / "document.md").write_text("# Failed Paper\n\n正文。\n", encoding="utf-8")
        (paper / "structure" / "structure.json").write_text(
            json.dumps(
                {
                    "paper_id": "failed-paper",
                    "selected_segment": {"start_line": 1, "end_line": 3, "h1_line": 1},
                    "regions": [],
                    "stats": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (paper / "structure" / "quality.json").write_text(
            json.dumps({"paper_id": "failed-paper", "label": "red", "issues": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        (paper / "run.json").write_text(
            json.dumps(
                {
                    "paper_id": "failed-paper",
                    "status": "failed",
                    "structure_quality": "red",
                    "generation_id": "run-failed",
                    "source": "failed.md",
                    "issues": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (workspace / "_corpus" / "materials.jsonl").write_text("", encoding="utf-8")

        result = MaterialQualityAuditor(workspace).run(run_id="zero-card")

        self.assertEqual(result["total_materials"], 0)
        self.assertEqual(result["paper_count"], 1)
        self.assertEqual(result["issue_counts"]["run.latest_failed"], 1)
        self.assertEqual(result["issue_counts"]["coverage.missing"], 1)
        self.assertEqual(result["issue_counts"]["coverage.line_ledger_missing"], 1)
        payload = read_json(workspace / "_audit" / "zero-card" / "material-quality.json")
        self.assertEqual(payload["papers"][0]["paper_id"], "failed-paper")

    def test_material_audit_does_not_treat_numeric_comparisons_as_html(self) -> None:
        tmp_path = make_case_dir()
        workspace = tmp_path / "workspace"
        source = workspace / "paper-one" / "normalized" / "document.md"
        source.parent.mkdir(parents=True)
        source.write_text("# Paper One\n\n## 结果\n\n0<K≤0.1，且 K-E>0.1。\n", encoding="utf-8")
        material = {
            "schema_version": "material.v2",
            "material_id": "paper-one:table:L00005-L00005:01",
            "material_type": "evidence_card",
            "paper_id": "paper-one",
            "paper_title": "Paper One",
            "source_path": "normalized/document.md",
            "raw_title": "结果",
            "clean_title": "结果 > 表格",
            "order": 1,
            "summary": "0<K≤0.1，且 K-E>0.1。",
            "lead_excerpt": "0<K≤0.1，且 K-E>0.1。",
            "extract": "0<K≤0.1，且 K-E>0.1。",
            "source_span": {"path": "normalized/document.md", "start_line": 5, "end_line": 5},
            "content_ref": {"path": "normalized/document.md", "start_line": 5, "end_line": 5},
            "confidence_flags": [],
            "quality_flags": [],
            "content_kind": "table",
            "heading_path": ["结果"],
            "source_spans": [{"path": "normalized/document.md", "start_line": 5, "end_line": 5}],
            "source_block_ids": ["block_table_L00005-00005_p001"],
            "source_fingerprint": "sha256:comparison",
        }
        corpus = workspace / "_corpus" / "materials.jsonl"
        corpus.parent.mkdir(parents=True)
        corpus.write_text(json.dumps(material, ensure_ascii=False) + "\n", encoding="utf-8")

        result = MaterialQualityAuditor(workspace).run(run_id="comparison-markup")

        self.assertNotIn("summary.contains_markup", result["issue_counts"])
        self.assertNotIn("extract.contains_markup", result["issue_counts"])

    def test_material_review_exporter_writes_human_review_pack(self) -> None:
        tmp_path = make_case_dir()
        workspace = tmp_path / "workspace"
        material = {
            "schema_version": "material.v2",
            "material_id": "paper-one:chapter_001:card_001",
            "material_type": "evidence_card",
            "paper_id": "paper-one",
            "paper_title": "Paper One",
            "source_path": "normalized/document.md",
            "raw_title": "第一章 绪论",
            "clean_title": "绪论",
            "parent_ref": {"node_id": "chapter_001", "title": "绪论", "raw_title": "第一章 绪论"},
            "order": 1,
            "summary": "研究背景清楚。",
            "lead_excerpt": "研究背景清楚。",
            "extract": "研究背景清楚。这里是供人工审核的正文片段。",
            "source_span": {"path": "normalized/document.md", "start_line": 3, "end_line": 5},
            "content_ref": {"path": "normalized/document.md", "start_line": 3, "end_line": 5},
            "confidence_flags": [],
            "quality_flags": ["source.split"],
            "content_kind": "prose",
            "heading_path": ["第一章 绪论", "1.1 研究背景"],
            "source_spans": [
                {
                    "path": "normalized/document.md",
                    "start_line": 5,
                    "end_line": 5,
                    "start_char": 0,
                    "end_char": 8,
                }
            ],
        }
        corpus = workspace / "_corpus" / "materials.jsonl"
        corpus.parent.mkdir(parents=True)
        corpus.write_text(json.dumps(material, ensure_ascii=False) + "\n", encoding="utf-8")

        result = MaterialReviewExporter(workspace).run(run_id="review-test")

        self.assertEqual(result["run_id"], "review-test")
        self.assertEqual(result["total_materials"], 1)
        self.assertEqual(result["paper_count"], 1)

        index = Path(result["index_path"]).read_text(encoding="utf-8")
        paper = (workspace / "_human_review" / "review-test" / "papers" / "paper_001.md").read_text(encoding="utf-8")
        self.assertIn("# 材料人工审核", index)
        self.assertIn("[paper_001.md](papers/paper_001.md)", index)
        self.assertIn("# Paper One", paper)
        self.assertIn("## 卡片 001：绪论", paper)
        self.assertIn("- [ ] 可用", paper)
        self.assertIn("source.split", paper)
        self.assertIn("- 内容类型: `prose`", paper)
        self.assertIn("- 标题路径: `第一章 绪论 > 1.1 研究背景`", paper)
        self.assertIn("normalized/document.md:3-5", paper)
        self.assertIn("- 精确来源: `normalized/document.md:5:0-5:8`", paper)
        self.assertIn("研究背景清楚。这里是供人工审核的正文片段。", paper)

    def test_cli_material_review_prints_json_result(self) -> None:
        tmp_path = make_case_dir()
        workspace = tmp_path / "workspace"
        corpus = workspace / "_corpus" / "materials.jsonl"
        corpus.parent.mkdir(parents=True)
        corpus.write_text(
            json.dumps(
                {
                    "schema_version": "material.v2",
                    "material_id": "paper-one:chapter_001:card_001",
                    "material_type": "evidence_card",
                    "paper_id": "paper-one",
                    "paper_title": "Paper One",
                    "source_path": "normalized/document.md",
                    "raw_title": "第一章 绪论",
                    "clean_title": "绪论",
                    "parent_ref": {"node_id": "chapter_001", "title": "绪论"},
                    "order": 1,
                    "summary": "研究背景清楚。",
                    "lead_excerpt": "研究背景清楚。",
                    "extract": "研究背景清楚。",
                    "source_span": {"path": "normalized/document.md", "start_line": 3, "end_line": 5},
                    "content_ref": {"path": "normalized/document.md", "start_line": 3, "end_line": 5},
                    "confidence_flags": [],
                    "quality_flags": [],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "main.py"),
                "material-review",
                "--workspace",
                str(workspace),
                "--run-id",
                "cli-review",
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["run_id"], "cli-review")
        self.assertEqual(payload["total_materials"], 1)
        self.assertTrue((workspace / "_human_review" / "cli-review" / "index.md").exists())

if __name__ == "__main__":
    unittest.main()
