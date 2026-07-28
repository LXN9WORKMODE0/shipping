from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_MATERIAL_FIELDS = {
    "schema_version",
    "material_id",
    "material_type",
    "paper_id",
    "paper_title",
    "source_path",
    "raw_title",
    "clean_title",
    "parent_ref",
    "order",
    "summary",
    "lead_excerpt",
    "extract",
    "source_span",
    "content_ref",
    "confidence_flags",
    "quality_flags",
}

EXPECTED_CASES: dict[str, dict[str, Any]] = {
    "三峡航运遇瓶颈": {
        "paper_title": "三峡航运遇瓶颈",
        "min_source_line": 25,
        "max_source_line": 44,
        "forbidden_text": ["堤溪大桥", "老城六百年不倒"],
    },
    "基于Arena的三峡船舶积压疏导策略效果研究": {
        "required_source_lines": [15, 23, 44, 71, 95],
        "max_source_line": 104,
        "forbidden_text": ["Key words:traffic control line"],
    },
    "基于运输化理论的三峡船闸过闸货运需求量增长趋势分析": {
        "required_headings": [
            "1、运输化与工业化的联系",
            "2、部分国家及地区水运货运量与经济发展数据分析",
            "3、三峡船闸过闸需求趋势分析",
            "4、小结",
        ],
        "forbidden_text": ["[1]荣朝和"],
    },
    "考虑翻坝和天气的长江班轮运网鲁棒优化模型": {
        "forbidden_headings": ["2000 年，Yu 和 Li 提出了"],
        "required_headings": ["第1章 绪论", "第 7 章 结论与展望"],
    },
    "慎防信息不对称影响船闸管理": {
        "required_content_kinds": ["abstract", "prose"],
        "forbidden_text": ["【参考文献】", "【作者简介】"],
    },
    "长江上游地区产业布局及航运适应性研究": {
        "forbidden_paper_titles": ["专业硕士学位论文"],
        "minimum_table_cards": 1,
        "forbidden_text": ["[table removed]"],
    },
}


@dataclass(frozen=True)
class PaperArtifacts:
    paper_id: str
    directory: Path
    source_lines: list[str]
    structure: dict[str, Any]
    quality: dict[str, Any]
    materials: list[dict[str, Any]]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_papers(
    workspace: Path,
    failures: list[str],
    selected_paper_ids: set[str] | None = None,
) -> dict[str, PaperArtifacts]:
    papers: dict[str, PaperArtifacts] = {}
    for directory in sorted((item for item in workspace.iterdir() if item.is_dir()), key=lambda item: item.name):
        if directory.name.startswith("_"):
            continue
        if selected_paper_ids is not None and directory.name not in selected_paper_ids:
            continue
        structure_path = directory / "structure" / "structure.json"
        if not structure_path.exists():
            continue
        source_path = directory / "normalized" / "document.md"
        quality_path = directory / "structure" / "quality.json"
        materials_path = directory / "materials" / "materials.jsonl"
        missing = [path for path in (source_path, quality_path) if not path.exists()]
        if missing:
            failures.append(f"{directory.name}: 缺少验收输入文件：{', '.join(str(path) for path in missing)}")
            continue
        structure = _read_json(structure_path)
        quality = _read_json(quality_path)
        materials = _read_jsonl(materials_path) if materials_path.exists() else []
        paper_id = str(structure.get("paper_id") or directory.name)
        if paper_id in papers:
            failures.append(f"{paper_id}: paper_id 重复，目录为 {directory}。")
            continue
        papers[paper_id] = PaperArtifacts(
            paper_id=paper_id,
            directory=directory,
            source_lines=source_path.read_text(encoding="utf-8").splitlines(),
            structure=structure,
            quality=quality,
            materials=materials,
        )
    return papers


def _load_collection_paper_ids(path: Path) -> list[str]:
    payload = _read_json(path)
    papers = payload.get("papers")
    if not isinstance(papers, list) or not papers:
        raise ValueError("collection.papers 必须是非空数组。")
    paper_ids: list[str] = []
    for index, row in enumerate(papers):
        if not isinstance(row, dict):
            raise ValueError(f"collection.papers[{index}] 必须是对象。")
        paper_id = row.get("paper_id")
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise ValueError(f"collection.papers[{index}].paper_id 必须是非空字符串。")
        paper_ids.append(paper_id.strip())
    if len(paper_ids) != len(set(paper_ids)):
        raise ValueError("collection.papers 中存在重复 paper_id。")
    return paper_ids


def _material_spans(material: dict[str, Any]) -> list[dict[str, Any]]:
    spans = material.get("source_spans")
    if isinstance(spans, list) and spans:
        return [span for span in spans if isinstance(span, dict)]
    span = material.get("source_span")
    return [span] if isinstance(span, dict) else []


def _overlaps(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return int(left["start_line"]) <= int(right["end_line"]) and int(right["start_line"]) <= int(left["end_line"])


def _heading_titles(structure: dict[str, Any]) -> set[str]:
    titles = set()
    for heading in structure.get("headings", []):
        if not isinstance(heading, dict):
            continue
        value = heading.get("raw_title") or heading.get("title_raw") or heading.get("clean_title") or heading.get("title_norm")
        if value:
            titles.add(str(value))
    return titles


def _validate_common(paper: PaperArtifacts, failures: list[str]) -> None:
    label = str(paper.quality.get("label", ""))
    coverage = paper.structure.get("coverage", {})
    selected = paper.structure.get("selected_segment")
    ledger = paper.structure.get("line_ledger")
    allowed_roles = {"scope_marker", "front_matter", "abstract", "keywords", "body", "back_matter"}
    if not isinstance(selected, dict):
        failures.append(f"{paper.paper_id}: 缺少 selected_segment。")
    elif not isinstance(ledger, list):
        failures.append(f"{paper.paper_id}: 缺少逐行分类账 line_ledger。")
    else:
        start_line = max(int(selected["start_line"]), 1)
        end_line = min(int(selected["end_line"]), len(paper.source_lines))
        expected_lines = {
            line_number
            for line_number in range(start_line, end_line + 1)
            if paper.source_lines[line_number - 1].strip()
        }
        ledger_lines = [int(item.get("line_number", 0)) for item in ledger if isinstance(item, dict)]
        if len(ledger_lines) != len(set(ledger_lines)):
            failures.append(f"{paper.paper_id}: line_ledger 存在重复行号。")
        if set(ledger_lines) != expected_lines:
            missing = sorted(expected_lines - set(ledger_lines))
            unexpected = sorted(set(ledger_lines) - expected_lines)
            failures.append(
                f"{paper.paper_id}: line_ledger 与 selected segment 非空行不一致，缺少={missing}，多余={unexpected}。"
            )
        invalid = [
            item
            for item in ledger
            if not isinstance(item, dict)
            or item.get("assignment_count") != 1
            or item.get("role") not in allowed_roles
        ]
        if invalid:
            failures.append(f"{paper.paper_id}: line_ledger 存在未归属、重叠或非法角色记录：{invalid[:5]}。")

    run_path = paper.directory / "run.json"
    current_path = paper.directory / "materials" / "current.json"
    run = _read_json(run_path) if run_path.exists() else {}
    current = _read_json(current_path) if current_path.exists() else {}
    if not run:
        failures.append(f"{paper.paper_id}: 缺少最新 run.json。")
    if label != "red":
        for field in ("source_to_region_ratio", "region_to_block_ratio", "block_to_card_ratio"):
            if coverage.get(field) != 1.0:
                failures.append(f"{paper.paper_id}: coverage.{field}={coverage.get(field)!r}，应为 1.0。")
        for field in (
            "unassigned_source_line_numbers",
            "multiply_classified_source_line_numbers",
            "unblocked_source_line_numbers",
            "unmaterialized_block_ids",
            "multiply_materialized_block_ids",
        ):
            if coverage.get(field):
                failures.append(f"{paper.paper_id}: coverage.{field} 非空：{coverage[field]}。")
        if not paper.materials:
            failures.append(f"{paper.paper_id}: 非 red 文档没有生成材料卡。")
        if run.get("status") != "completed":
            failures.append(f"{paper.paper_id}: 非 red 文档最新运行状态不是 completed。")
        if current.get("status") != "completed" or current.get("generation_id") != run.get("generation_id"):
            failures.append(f"{paper.paper_id}: current.json 与最新成功运行代际不一致。")
    else:
        if paper.materials:
            failures.append(f"{paper.paper_id}: red 文档仍暴露 {len(paper.materials)} 张当前材料卡。")
        if current.get("status") != "failed" or current.get("generation_id") is not None:
            failures.append(f"{paper.paper_id}: red 文档没有撤下当前材料代际。")

    source_line_count = len(paper.source_lines)
    back_regions = [
        region
        for region in paper.structure.get("regions", [])
        if isinstance(region, dict) and region.get("role") == "back_matter"
    ]
    seen_block_ids: set[str] = set()
    for material in paper.materials:
        material_id = str(material.get("material_id", "<missing>"))
        missing_fields = sorted(REQUIRED_MATERIAL_FIELDS - set(material))
        if missing_fields:
            failures.append(f"{material_id}: 缺少 material.v2 必需字段 {missing_fields}。")
        extract = str(material.get("extract", ""))
        if not extract.strip():
            failures.append(f"{material_id}: extract 为空。")
        quality_flags = {str(flag) for flag in material.get("quality_flags", [])}
        confidence_flags = {str(flag) for flag in material.get("confidence_flags", [])}
        if material.get("generation_id") != run.get("generation_id"):
            failures.append(f"{material_id}: generation_id 与最新运行不一致。")
        if len(extract) > 1400 and not (
            material.get("content_kind") == "table" and "table.row_too_long" in quality_flags
        ):
            failures.append(f"{material_id}: extract 长度为 {len(extract)}，超过 1400。")
        if "extract.trimmed" in quality_flags | confidence_flags:
            failures.append(f"{material_id}: 出现禁止的静默截断标记 extract.trimmed。")
        if "[table removed]" in extract:
            failures.append(f"{material_id}: 表格仍被替换为 [table removed]。")

        spans = _material_spans(material)
        if not spans:
            failures.append(f"{material_id}: 缺少可验证的 source_span。")
        for span in spans:
            try:
                start_line = int(span["start_line"])
                end_line = int(span["end_line"])
            except (KeyError, TypeError, ValueError):
                failures.append(f"{material_id}: source_span 格式无效：{span!r}。")
                continue
            if start_line < 1 or end_line < start_line or end_line > source_line_count:
                failures.append(
                    f"{material_id}: source_span L{start_line}-L{end_line} 超出原文 1-{source_line_count}。"
                )
            start_char = span.get("start_char")
            end_char = span.get("end_char")
            if start_char is not None or end_char is not None:
                if (
                    start_line != end_line
                    or not isinstance(start_char, int)
                    or not isinstance(end_char, int)
                    or start_char < 0
                    or end_char < start_char
                    or end_char > len(paper.source_lines[start_line - 1])
                ):
                    failures.append(f"{material_id}: 字符坐标不是有效的单行原文坐标：{span!r}。")
            for region in back_regions:
                if _overlaps(span, region):
                    failures.append(
                        f"{material_id}: source_span L{start_line}-L{end_line} 与后置区域 "
                        f"L{region['start_line']}-L{region['end_line']} 相交。"
                    )

        for block_id in material.get("source_block_ids", []):
            block_id = str(block_id)
            if block_id in seen_block_ids:
                failures.append(f"{paper.paper_id}: 内容块 {block_id} 被多张卡引用。")
            seen_block_ids.add(block_id)
    expected_blocks = coverage.get("included_block_count")
    if label != "red" and isinstance(expected_blocks, int) and len(seen_block_ids) != expected_blocks:
        failures.append(
            f"{paper.paper_id}: 唯一 source_block_ids 数量为 {len(seen_block_ids)}，结构记录为 {expected_blocks}。"
        )


def _validate_expected_case(paper: PaperArtifacts, expected: dict[str, Any], failures: list[str]) -> None:
    materials = paper.materials
    combined_text = "\n".join(str(material.get("extract", "")) for material in materials)
    spans = [span for material in materials for span in _material_spans(material)]
    headings = _heading_titles(paper.structure)
    paper_titles = {str(paper.structure.get("paper_title", ""))}
    paper_titles.update(str(material.get("paper_title", "")) for material in materials)

    expected_title = expected.get("paper_title")
    if expected_title and paper.structure.get("paper_title") != expected_title:
        failures.append(
            f"{paper.paper_id}: paper_title={paper.structure.get('paper_title')!r}，应为 {expected_title!r}。"
        )
    for value in expected.get("forbidden_text", []):
        if value in combined_text:
            failures.append(f"{paper.paper_id}: 材料卡中出现禁止文本 {value!r}。")
    for value in expected.get("forbidden_paper_titles", []):
        if value in paper_titles:
            failures.append(f"{paper.paper_id}: 使用了错误论文标题 {value!r}。")
    for value in expected.get("required_headings", []):
        if value not in headings:
            failures.append(f"{paper.paper_id}: 缺少必需标题 {value!r}。")
    for value in expected.get("forbidden_headings", []):
        if any(value in heading for heading in headings):
            failures.append(f"{paper.paper_id}: 错误提升了标题 {value!r}。")

    required_kinds = set(expected.get("required_content_kinds", []))
    actual_kinds = {str(material.get("content_kind", "")) for material in materials}
    if not required_kinds.issubset(actual_kinds):
        failures.append(f"{paper.paper_id}: 内容类型为 {sorted(actual_kinds)}，缺少 {sorted(required_kinds - actual_kinds)}。")
    minimum_tables = expected.get("minimum_table_cards")
    if minimum_tables is not None:
        table_count = sum(material.get("content_kind") == "table" for material in materials)
        if table_count < int(minimum_tables):
            failures.append(f"{paper.paper_id}: 表格卡数量为 {table_count}，至少需要 {minimum_tables}。")

    if spans:
        minimum_line = expected.get("min_source_line")
        maximum_line = expected.get("max_source_line")
        if minimum_line is not None and any(int(span["start_line"]) < int(minimum_line) for span in spans):
            failures.append(f"{paper.paper_id}: 存在来源行早于 L{minimum_line} 的材料卡。")
        if maximum_line is not None and any(int(span["end_line"]) > int(maximum_line) for span in spans):
            failures.append(f"{paper.paper_id}: 存在来源行晚于 L{maximum_line} 的材料卡。")

    included_regions = [
        region
        for region in paper.structure.get("regions", [])
        if isinstance(region, dict) and region.get("included_in_materials")
    ]
    for line_number in expected.get("required_source_lines", []):
        containing = [
            region
            for region in included_regions
            if int(region["start_line"]) <= int(line_number) <= int(region["end_line"])
        ]
        if not containing:
            failures.append(f"{paper.paper_id}: 必需来源行 L{line_number} 未落入任何制卡区域。")
            continue
        if not any(
            tuple(material.get("heading_path", []))[: len(tuple(region.get("heading_path", [])))]
            == tuple(region.get("heading_path", []))
            for region in containing
            for material in materials
        ):
            failures.append(f"{paper.paper_id}: L{line_number} 所在结构区域没有对应材料卡。")


def validate(
    workspace: Path,
    *,
    selected_paper_ids: list[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    if not workspace.is_dir():
        return [f"工作区不存在或不是目录：{workspace}"]
    selected_set = set(selected_paper_ids) if selected_paper_ids is not None else None
    papers = _load_papers(workspace, failures, selected_set)
    if selected_paper_ids is not None:
        missing = [paper_id for paper_id in selected_paper_ids if paper_id not in papers]
        for paper_id in missing:
            failures.append(f"{paper_id}: 清单论文缺少可验收的当前结构产物。")

    for paper in papers.values():
        _validate_common(paper, failures)
    for paper_id, expected in EXPECTED_CASES.items():
        paper = papers.get(paper_id)
        if paper is not None:
            _validate_expected_case(paper, expected, failures)

    all_material_ids = [material.get("material_id") for paper in papers.values() for material in paper.materials]
    if len(all_material_ids) != len(set(all_material_ids)):
        failures.append("语料中存在重复 material_id。")
    corpus_path = workspace / "_corpus" / "materials.jsonl"
    if not corpus_path.exists():
        failures.append(f"缺少合并材料文件：{corpus_path}")
    else:
        corpus_materials = _read_jsonl(corpus_path)
        if selected_set is not None:
            corpus_materials = [
                material
                for material in corpus_materials
                if material.get("paper_id") in selected_set
            ]
        corpus_ids = [material.get("material_id") for material in corpus_materials]
        if set(corpus_ids) != set(all_material_ids) or len(corpus_ids) != len(all_material_ids):
            scope = "清单论文" if selected_set is not None else "各论文"
            failures.append(f"_corpus/materials.jsonl 与{scope}材料卡集合不一致。")
    return failures


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="验证当前发布的 Markdown 到材料卡结构与内容完整性。")
    parser.add_argument("--workspace", type=Path, required=True, help="流水线工作区目录。")
    parser.add_argument(
        "--collection",
        type=Path,
        default=None,
        help="可选；只验收清单 papers 中显式列出的论文。",
    )
    args = parser.parse_args(argv)

    try:
        selected_paper_ids = (
            _load_collection_paper_ids(args.collection)
            if args.collection is not None
            else None
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"验收清单无效：{exc}", file=sys.stderr)
        return 2

    failures = validate(
        args.workspace,
        selected_paper_ids=selected_paper_ids,
    )
    if failures:
        print(f"真实语料验收失败：共 {len(failures)} 项。", file=sys.stderr)
        for index, failure in enumerate(failures, start=1):
            print(f"{index}. {failure}", file=sys.stderr)
        return 1
    paper_count = (
        len(selected_paper_ids)
        if selected_paper_ids is not None
        else len(_load_papers(args.workspace, []))
    )
    print(f"真实语料验收通过：{paper_count} 个论文目录；所有非 red 文档三层覆盖完整，red 文档未暴露材料。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
