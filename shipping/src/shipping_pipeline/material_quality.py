from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shipping_pipeline.markup import HTML_TAG_PATTERN


TITLE_POLLUTION_PATTERNS = [
    re.compile(r"Record"),
    re.compile(r"\ufffd"),
    re.compile(r"[A-Za-z]{2,}$"),
]
GENERIC_TITLE_PATTERN = re.compile(r"^Evidence chunk \d+$", re.IGNORECASE)
MARKUP_PATTERN = HTML_TAG_PATTERN
SUMMARY_COMPLETE_ENDINGS = tuple("。！？!?；;.)）]】")
CHAPTER_SHORT_LINES = 3
CHAPTER_LONG_LINES = 700
CHUNK_SHORT_CHARS = 80
CHUNK_LONG_CHARS = 1600
EXTRACT_LONG_CHARS = 1400
SUMMARY_TRUNCATION_CHARS = 300
SUMMARY_LONG_CHARS = 500


class MaterialQualityAuditor:
    """审计已生成的材料卡，但不修改材料卡本身。"""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(self, run_id: str | None = None) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        audit_dir = self.workspace / "_audit" / resolved_run_id
        audit_dir.mkdir(parents=True, exist_ok=True)

        materials = _read_jsonl(self.workspace / "_corpus" / "materials.jsonl")
        material_reports = [self._audit_material(material) for material in materials]
        _add_duplicate_content_issues(material_reports)
        structure_reports = self._audit_paper_structures(material_reports)
        _add_duplicate_source_issues(structure_reports)
        paper_reports = _build_paper_reports(material_reports, set(structure_reports))
        _merge_structure_reports(paper_reports, structure_reports)
        issue_counts = dict(
            sorted(
                Counter(
                    [issue["code"] for report in material_reports for issue in report["issues"]]
                    + [
                        issue["code"]
                        for report in structure_reports.values()
                        for issue in report["issues"]
                    ]
                ).items()
            )
        )
        review_pack_reports = self._audit_review_packs(material_reports)
        review_pack_issue_counts = dict(
            sorted(Counter(issue["code"] for report in review_pack_reports for issue in report["issues"]).items())
        )

        payload = {
            "run_id": resolved_run_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "workspace": str(self.workspace),
            "summary": {
                "total_materials": len(material_reports),
                "materials_with_issues": sum(1 for report in material_reports if report["issues"]),
                "issue_counts": issue_counts,
                "paper_count": len(paper_reports),
                "publishable_paper_count": sum(
                    1 for paper in paper_reports if paper.get("run_status") == "completed" and paper["material_count"] > 0
                ),
                "failed_paper_count": sum(1 for paper in paper_reports if paper.get("run_status") == "failed"),
                "run_status_counts": dict(Counter(str(paper.get("run_status") or "unknown") for paper in paper_reports)),
                "review_pack_count": len(review_pack_reports),
                "review_pack_issue_counts": review_pack_issue_counts,
            },
            "papers": paper_reports,
            "materials": material_reports,
            "review_packs": review_pack_reports,
        }

        _write_json(audit_dir / "material-quality.json", payload)
        (audit_dir / "material-quality.md").write_text(_render_report(payload), encoding="utf-8")
        return {
            "run_id": resolved_run_id,
            "total_materials": len(material_reports),
            "materials_with_issues": payload["summary"]["materials_with_issues"],
            "paper_count": payload["summary"]["paper_count"],
            "issue_counts": issue_counts,
            "review_pack_issue_counts": review_pack_issue_counts,
            "results_path": str(audit_dir / "material-quality.json"),
            "report_path": str(audit_dir / "material-quality.md"),
        }

    def _audit_material(self, material: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        ref_result = self._resolve_content_ref(material)
        text = str(material.get("extract") or ref_result["text"])
        issues.extend(ref_result["issues"])
        issues.extend(_extract_issues(material))
        issues.extend(_summary_issues(material))
        issues.extend(_title_issues(material))
        issues.extend(_quality_flag_issues(material))
        issues.extend(_span_issues(material, ref_result))

        return {
            "material_id": material.get("material_id", ""),
            "paper_id": material.get("paper_id", ""),
            "material_type": material.get("material_type", ""),
            "content_kind": material.get("content_kind", ""),
            "clean_title": material.get("clean_title", ""),
            "heading_path": material.get("heading_path", []),
            "content_ref": material.get("content_ref", {}),
            "source_file": ref_result["source_file"],
            "span_lines": ref_result["span_lines"],
            "span_chars": len(text),
            "source_chars": len(ref_result["text"]),
            "confidence_flags": material.get("confidence_flags", []),
            "quality_flags": material.get("quality_flags", []),
            "source_fingerprint": material.get("source_fingerprint", ""),
            "issues": issues,
        }

    def _audit_paper_structures(
        self,
        material_reports: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for report in material_reports:
            grouped.setdefault(report["paper_id"], []).append(report)

        reports: dict[str, dict[str, Any]] = {}
        paper_ids = set(grouped)
        if self.workspace.exists():
            paper_ids.update(
                directory.name
                for directory in self.workspace.iterdir()
                if directory.is_dir()
                and not directory.name.startswith("_")
                and any(
                    (directory / relative).exists()
                    for relative in ("run.json", "structure/structure.json", "structure/quality.json")
                )
            )

        for paper_id in sorted(paper_ids):
            paper_materials = grouped.get(paper_id, [])
            structure_path = self.workspace / paper_id / "structure" / "structure.json"
            quality_path = self.workspace / paper_id / "structure" / "quality.json"
            run_path = self.workspace / paper_id / "run.json"
            current_path = self.workspace / paper_id / "materials" / "current.json"
            issues: list[dict[str, Any]] = []
            structure: dict[str, Any] = {}
            run_record: dict[str, Any] = {}
            if run_path.exists():
                run_record = json.loads(run_path.read_text(encoding="utf-8"))
                if run_record.get("status") == "failed":
                    issues.append(
                        _issue(
                            "run.latest_failed",
                            "论文最新一次运行失败，当前没有可发布材料。",
                            {
                                "generation_id": run_record.get("generation_id"),
                                "structure_quality": run_record.get("structure_quality"),
                            },
                        )
                    )
                elif run_record.get("status") == "completed" and not paper_materials:
                    issues.append(
                        _issue(
                            "material.no_current_cards",
                            "论文最新运行成功但当前 corpus 中没有材料卡。",
                            {"generation_id": run_record.get("generation_id")},
                        )
                    )
            if structure_path.exists():
                structure = json.loads(structure_path.read_text(encoding="utf-8"))
            coverage = structure.get("coverage")
            required_coverage_fields = {
                "source_to_region_ratio",
                "region_to_block_ratio",
                "block_to_card_ratio",
                "unassigned_source_line_numbers",
                "unblocked_source_line_numbers",
                "unmaterialized_block_ids",
                "multiply_materialized_block_ids",
            }
            if not isinstance(coverage, dict) or not required_coverage_fields.issubset(coverage):
                missing_fields = sorted(required_coverage_fields - set(coverage or {}))
                issues.append(
                    _issue(
                        "coverage.missing",
                        "结构文件缺少三层完整性覆盖证明。",
                        {"missing_fields": missing_fields},
                    )
                )
                coverage = coverage if isinstance(coverage, dict) else {}
            else:
                source_ratio = float(coverage["source_to_region_ratio"])
                region_ratio = float(coverage["region_to_block_ratio"])
                block_ratio = float(coverage["block_to_card_ratio"])
                if source_ratio < 1.0 or coverage.get("unassigned_source_line_numbers"):
                    issues.append(
                        _issue(
                            "coverage.unassigned_source_line",
                            "选定原文中存在未归入任何区域的非空行。",
                            {
                                "source_to_region_ratio": source_ratio,
                                "line_numbers": coverage.get("unassigned_source_line_numbers", []),
                            },
                        )
                    )
                if coverage.get("multiply_classified_source_line_numbers"):
                    issues.append(
                        _issue(
                            "coverage.overlapping_source_line",
                            "选定原文中存在被多个区域重复归属的非空行。",
                            {"line_numbers": coverage.get("multiply_classified_source_line_numbers", [])},
                        )
                    )
                if region_ratio < 1.0 or coverage.get("unblocked_source_line_numbers"):
                    issues.append(
                        _issue(
                            "coverage.unblocked_source_line",
                            "应纳入材料的原文行没有进入任何原子内容块。",
                            {
                                "region_to_block_ratio": region_ratio,
                                "line_numbers": coverage.get("unblocked_source_line_numbers", []),
                            },
                        )
                    )
                if block_ratio < 1.0 or coverage.get("unmaterialized_block_ids"):
                    issues.append(
                        _issue(
                            "coverage.missing_body_content",
                            "正文原子内容块没有全部进入材料卡。",
                            {
                                "block_to_card_ratio": block_ratio,
                                "unmaterialized_block_ids": coverage.get("unmaterialized_block_ids", []),
                            },
                        )
                    )
                if coverage.get("multiply_materialized_block_ids"):
                    issues.append(
                        _issue(
                            "coverage.unexpected_overlap",
                            "部分原子内容块被重复分配到多张材料卡。",
                            {
                                "block_ids": coverage.get("multiply_materialized_block_ids", []),
                            },
                        )
                    )
            back_regions = [
                region for region in structure.get("regions", []) if region.get("role") == "back_matter"
            ]
            overlaps = []
            for material in paper_materials:
                content_ref = material.get("content_ref", {})
                start_line = content_ref.get("start_line")
                end_line = content_ref.get("end_line")
                if not isinstance(start_line, int) or not isinstance(end_line, int):
                    continue
                for region in back_regions:
                    if end_line >= int(region.get("start_line", end_line + 1)) and start_line <= int(
                        region.get("end_line", start_line - 1)
                    ):
                        overlaps.append(material["material_id"])
                        break
            if overlaps:
                issues.append(
                    _issue(
                        "region.back_matter_in_material",
                        "参考文献或其他后置区域进入了材料卡。",
                        {"material_ids": sorted(overlaps)},
                    )
                )

            if run_record.get("status") == "completed":
                if not current_path.exists():
                    issues.append(_issue("material.current_generation_missing", "缺少当前材料代际清单。"))
                else:
                    current = json.loads(current_path.read_text(encoding="utf-8"))
                    if current.get("generation_id") != run_record.get("generation_id"):
                        issues.append(
                            _issue(
                                "material.current_generation_mismatch",
                                "当前材料代际与最新成功运行不一致。",
                                {
                                    "run_generation_id": run_record.get("generation_id"),
                                    "material_generation_id": current.get("generation_id"),
                                },
                            )
                        )

            if quality_path.exists():
                quality = json.loads(quality_path.read_text(encoding="utf-8"))
                for issue in quality.get("issues", []):
                    issues.append(
                        _issue(
                            str(issue.get("code", "parse.unknown")),
                            str(issue.get("message", "结构解析记录了未分类问题。")),
                            dict(issue.get("details", {})),
                        )
                    )

            source_path = self.workspace / paper_id / "normalized" / "document.md"
            source_fingerprint = ""
            if source_path.exists():
                source_text = source_path.read_text(encoding="utf-8")
                issues.extend(_line_ledger_issues(structure, source_text.splitlines()))
                normalized = re.sub(r"\s+", " ", source_text).strip()
                source_fingerprint = "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()

            reports[paper_id] = {
                "paper_id": paper_id,
                "selected_segment": structure.get("selected_segment"),
                "run_status": run_record.get("status"),
                "generation_id": run_record.get("generation_id"),
                "source_to_region_ratio": coverage.get("source_to_region_ratio"),
                "region_to_block_ratio": coverage.get("region_to_block_ratio"),
                "block_to_card_ratio": coverage.get("block_to_card_ratio", coverage.get("body_coverage_ratio")),
                "body_coverage_ratio": coverage.get("block_to_card_ratio", coverage.get("body_coverage_ratio")),
                "unmaterialized_block_ids": coverage.get("unmaterialized_block_ids", []),
                "multiply_materialized_block_ids": coverage.get("multiply_materialized_block_ids", []),
                "region_counts": dict(Counter(region.get("role", "") for region in structure.get("regions", []))),
                "inferred_heading_count": int(structure.get("stats", {}).get("inferred_heading_count", 0)),
                "source_fingerprint": source_fingerprint,
                "issues": issues,
            }
        return reports

    def _resolve_content_ref(self, material: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        paper_id = str(material.get("paper_id", ""))
        content_ref = material.get("content_ref", {})
        ref_path = content_ref.get("path") or material.get("source_path")
        source_file = self.workspace / paper_id / str(ref_path)
        if not paper_id or not ref_path or not source_file.exists():
            issues.append(_issue("content_ref.missing_source", "这条材料引用的原文文件不存在。"))
            return {"source_file": str(source_file), "span_lines": 0, "text": "", "issues": issues}

        lines = source_file.read_text(encoding="utf-8").splitlines()
        try:
            start_line = int(content_ref.get("start_line"))
            end_line = int(content_ref.get("end_line"))
        except (TypeError, ValueError):
            issues.append(_issue("content_ref.invalid", "content_ref 的 start_line/end_line 不是有效整数。"))
            return {"source_file": str(source_file), "span_lines": 0, "text": "", "issues": issues}

        if start_line < 1 or end_line < start_line or end_line > len(lines):
            issues.append(
                _issue(
                    "content_ref.out_of_range",
                    "content_ref 指向了原文行号范围之外。",
                    {"start_line": start_line, "end_line": end_line, "source_line_count": len(lines)},
                )
            )
            safe_start = max(min(start_line, len(lines)), 1)
            safe_end = max(min(end_line, len(lines)), safe_start)
            text = "\n".join(lines[safe_start - 1 : safe_end]) if lines else ""
            return {"source_file": str(source_file), "span_lines": max(end_line - start_line + 1, 0), "text": text, "issues": issues}

        text = "\n".join(lines[start_line - 1 : end_line])
        return {"source_file": str(source_file), "span_lines": end_line - start_line + 1, "text": text, "issues": issues}

    def _audit_review_packs(self, material_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pack_dir = self.workspace / "_review_packs"
        if not pack_dir.exists():
            return []
        material_ids = {report["material_id"] for report in material_reports}
        reports = []
        for path in sorted(pack_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            evidence = payload.get("evidence_matrix", [])
            issues = []
            if not evidence:
                issues.append(_issue("review_pack.empty_evidence_matrix", "综述包没有命中任何证据材料。"))
            evidence_ids = [item.get("material_id", "") for item in evidence]
            hit_ids = sorted({material_id for material_id in evidence_ids if material_id in material_ids})
            missing_ids = sorted({material_id for material_id in evidence_ids if material_id and material_id not in material_ids})
            if missing_ids:
                issues.append(
                    _issue(
                        "review_pack.missing_material",
                        "综述包引用了 corpus 中不存在的 material_id。",
                        {"material_ids": missing_ids},
                    )
                )
            paper_hit_counts = dict(sorted(Counter(item.get("paper_id", "") for item in evidence if item.get("material_id") in hit_ids).items()))
            reports.append(
                {
                    "path": str(path),
                    "topic": payload.get("topic", ""),
                    "evidence_count": len(evidence),
                    "material_hit_count": len(hit_ids),
                    "material_coverage_ratio": len(hit_ids) / len(material_ids) if material_ids else 0,
                    "paper_hit_counts": paper_hit_counts,
                    "issues": issues,
                }
            )
        return reports


def _summary_issues(material: dict[str, Any]) -> list[dict[str, Any]]:
    summary = str(material.get("summary", "")).strip()
    clean_title = str(material.get("clean_title", "")).strip()
    raw_title = str(material.get("raw_title", "")).strip()
    issues = []
    if not summary:
        issues.append(_issue("summary.empty", "材料摘要为空。"))
        return issues
    if MARKUP_PATTERN.search(summary):
        issues.append(_issue("summary.contains_markup", "材料摘要仍包含 HTML 或 table 标记。"))
    if len(summary) > SUMMARY_LONG_CHARS:
        issues.append(_issue("summary.too_long", "材料摘要超过审计长度阈值。", {"length": len(summary)}))
    if len(summary) >= SUMMARY_TRUNCATION_CHARS and not summary.endswith(SUMMARY_COMPLETE_ENDINGS):
        issues.append(_issue("summary.possibly_truncated", "材料摘要达到截断阈值，但没有完整句末。"))
    if any(summary.startswith(title) for title in (clean_title, raw_title) if title):
        issues.append(_issue("summary.repeats_title", "材料摘要开头重复了标题。"))
    return issues


def _extract_issues(material: dict[str, Any]) -> list[dict[str, Any]]:
    if material.get("schema_version") != "material.v2":
        return []
    extract = str(material.get("extract", "")).strip()
    issues = []
    if not extract:
        issues.append(_issue("extract.empty", "material.v2 的正文片段为空。"))
        return issues
    if MARKUP_PATTERN.search(extract):
        issues.append(_issue("extract.contains_markup", "material.v2 的正文片段仍包含 HTML 或 table 标记。"))
    allowed_long_table = "table.row_too_long" in set(material.get("quality_flags", []))
    if len(extract) > EXTRACT_LONG_CHARS and not allowed_long_table:
        issues.append(_issue("extract.too_long", "material.v2 的正文片段超过材料卡长度阈值。", {"length": len(extract)}))
    return issues


def _title_issues(material: dict[str, Any]) -> list[dict[str, Any]]:
    clean_title = str(material.get("clean_title", "")).strip()
    raw_title = str(material.get("raw_title", "")).strip()
    title = clean_title or raw_title
    issues = []
    if not title:
        issues.append(_issue("title.empty", "材料标题为空。"))
        return issues
    if GENERIC_TITLE_PATTERN.match(title):
        issues.append(_issue("title.generic", "材料标题只是自动编号，不是语义标题。"))
    if any(pattern.search(title) for pattern in TITLE_POLLUTION_PATTERNS):
        issues.append(_issue("title.polluted", "材料标题疑似含有 OCR 或解析污染。", {"title": title}))
    return issues


def _quality_flag_issues(material: dict[str, Any]) -> list[dict[str, Any]]:
    flags = set(material.get("quality_flags", []))
    issues = []
    if "title.weak_inferred" in flags:
        issues.append(_issue("title.weak_inferred", "材料标题无法稳定推断语义，已标记为弱标题。"))
    if "card.short_tail" in flags:
        issues.append(_issue("card.short_tail", "结构边界产生了无法安全合并的短尾材料卡。"))
    if "table.parse_failed" in flags:
        issues.append(_issue("table.parse_failed", "表格结构解析失败，卡片保留了可见原文。"))
    if "table.row_too_long" in flags:
        issues.append(_issue("table.row_too_long", "表格单行超过卡片长度阈值，已完整保留并暴露。"))
    if "media.image_omitted" in flags:
        issues.append(_issue("media.image_omitted", "图片像素内容未进入材料卡，仅保留图片说明。"))
    return issues


def _span_issues(material: dict[str, Any], ref_result: dict[str, Any]) -> list[dict[str, Any]]:
    material_type = material.get("material_type")
    span_lines = int(ref_result.get("span_lines", 0))
    schema_version = material.get("schema_version")
    span_chars = len(str(material.get("extract") or ref_result.get("text", "")))
    issues = []
    if material_type == "chapter":
        if span_lines < CHAPTER_SHORT_LINES:
            issues.append(_issue("chapter.too_short", "章节材料对应的原文行数过少。", {"span_lines": span_lines}))
        if span_lines > CHAPTER_LONG_LINES:
            issues.append(_issue("chapter.too_long", "章节材料超过长跨度阈值。", {"span_lines": span_lines}))
    if material_type == "evidence_chunk":
        if span_chars < CHUNK_SHORT_CHARS:
            issues.append(_issue("chunk.too_short", "证据片段原文内容过少。", {"span_chars": span_chars}))
        if span_chars > CHUNK_LONG_CHARS:
            issues.append(_issue("chunk.too_long", "证据片段超过分块长度阈值。", {"span_chars": span_chars}))
    if material_type == "evidence_card" and schema_version == "material.v2":
        allowed_long_table = "table.row_too_long" in set(material.get("quality_flags", []))
        if span_chars > EXTRACT_LONG_CHARS and not allowed_long_table:
            issues.append(_issue("card.too_long", "material.v2 证据卡超过正文片段长度阈值。", {"span_chars": span_chars}))
    return issues


def _add_duplicate_content_issues(material_reports: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for report in material_reports:
        fingerprint = str(report.get("source_fingerprint", ""))
        if fingerprint:
            grouped.setdefault(fingerprint, []).append(report)

    for fingerprint, reports in grouped.items():
        paper_ids = sorted({report["paper_id"] for report in reports})
        if len(paper_ids) < 2:
            continue
        material_ids = sorted(report["material_id"] for report in reports)
        for report in reports:
            report["issues"].append(
                _issue(
                    "corpus.duplicate_content",
                    "这条材料与其他论文中的材料正文完全相同。",
                    {
                        "source_fingerprint": fingerprint,
                        "paper_ids": paper_ids,
                        "material_ids": material_ids,
                    },
                )
            )


def _add_duplicate_source_issues(structure_reports: dict[str, dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for report in structure_reports.values():
        fingerprint = str(report.get("source_fingerprint", ""))
        if fingerprint:
            grouped.setdefault(fingerprint, []).append(report)

    for fingerprint, reports in grouped.items():
        if len(reports) < 2:
            continue
        paper_ids = sorted(report["paper_id"] for report in reports)
        for report in reports:
            report["issues"].append(
                _issue(
                    "corpus.duplicate_source",
                    "多篇论文目录使用了完全相同的 normalized Markdown。",
                    {"source_fingerprint": fingerprint, "paper_ids": paper_ids},
                )
            )


def _line_ledger_issues(structure: dict[str, Any], source_lines: list[str]) -> list[dict[str, Any]]:
    selected = structure.get("selected_segment")
    ledger = structure.get("line_ledger")
    if not isinstance(selected, dict) or not isinstance(ledger, list):
        return [_issue("coverage.line_ledger_missing", "结构文件缺少可复算的逐行分类账。")]
    try:
        start_line = max(int(selected["start_line"]), 1)
        end_line = min(int(selected["end_line"]), len(source_lines))
    except (KeyError, TypeError, ValueError):
        return [_issue("coverage.line_ledger_missing", "selected_segment 无法用于复算逐行分类账。")]
    expected_lines = {
        line_number
        for line_number in range(start_line, end_line + 1)
        if source_lines[line_number - 1].strip()
    }
    allowed_roles = {"scope_marker", "front_matter", "abstract", "keywords", "body", "back_matter"}
    ledger_lines = [
        int(item.get("line_number", 0))
        for item in ledger
        if isinstance(item, dict)
    ]
    invalid_entries = [
        item
        for item in ledger
        if not isinstance(item, dict)
        or item.get("assignment_count") != 1
        or item.get("role") not in allowed_roles
    ]
    missing = sorted(expected_lines - set(ledger_lines))
    unexpected = sorted(set(ledger_lines) - expected_lines)
    duplicate_lines = sorted(line for line, count in Counter(ledger_lines).items() if count > 1)
    if not missing and not unexpected and not duplicate_lines and not invalid_entries:
        return []
    return [
        _issue(
            "coverage.line_ledger_mismatch",
            "逐行分类账与 selected segment 的非空原文行不一致。",
            {
                "missing_line_numbers": missing,
                "unexpected_line_numbers": unexpected,
                "duplicate_line_numbers": duplicate_lines,
                "invalid_entries": invalid_entries[:10],
            },
        )
    ]


def _merge_structure_reports(
    paper_reports: list[dict[str, Any]],
    structure_reports: dict[str, dict[str, Any]],
) -> None:
    for paper in paper_reports:
        structure = structure_reports.get(paper["paper_id"], {})
        structure_issues = list(structure.get("issues", []))
        structure_issue_counts = dict(Counter(issue["code"] for issue in structure_issues))
        combined_counts = Counter(paper["issue_counts"])
        combined_counts.update(structure_issue_counts)
        paper["issue_counts"] = dict(sorted(combined_counts.items()))
        paper["structure_issue_counts"] = dict(sorted(structure_issue_counts.items()))
        paper["structure_issues"] = structure_issues
        paper["selected_segment"] = structure.get("selected_segment")
        paper["run_status"] = structure.get("run_status")
        paper["generation_id"] = structure.get("generation_id")
        paper["source_to_region_ratio"] = structure.get("source_to_region_ratio")
        paper["region_to_block_ratio"] = structure.get("region_to_block_ratio")
        paper["block_to_card_ratio"] = structure.get("block_to_card_ratio")
        paper["body_coverage_ratio"] = structure.get("body_coverage_ratio")
        paper["unmaterialized_block_ids"] = structure.get("unmaterialized_block_ids", [])
        paper["multiply_materialized_block_ids"] = structure.get("multiply_materialized_block_ids", [])
        paper["region_counts"] = structure.get("region_counts", {})
        paper["inferred_heading_count"] = structure.get("inferred_heading_count", 0)


def _build_paper_reports(
    material_reports: list[dict[str, Any]],
    paper_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for report in material_reports:
        grouped.setdefault(report["paper_id"], []).append(report)

    paper_reports = []
    for paper_id in sorted(set(grouped) | (paper_ids or set())):
        reports = grouped.get(paper_id, [])
        issue_counts = dict(Counter(issue["code"] for report in reports for issue in report["issues"]))
        material_type_counts = dict(Counter(report["material_type"] for report in reports))
        content_kind_counts = dict(Counter(report["content_kind"] for report in reports if report["content_kind"]))
        paper_reports.append(
            {
                "paper_id": paper_id,
                "material_count": len(reports),
                "material_type_counts": material_type_counts,
                "content_kind_counts": content_kind_counts,
                "materials_with_issues": sum(1 for report in reports if report["issues"]),
                "issue_counts": issue_counts,
            }
        )
    return paper_reports


def _render_report(payload: dict[str, Any]) -> str:
    issue_counts = payload["summary"]["issue_counts"]
    total = payload["summary"]["total_materials"]
    problem_count = payload["summary"]["materials_with_issues"]
    problem_ratio = problem_count / total if total else 0
    content_ref_issues = sum(count for code, count in issue_counts.items() if code.startswith("content_ref."))
    top_issues = sorted(issue_counts.items(), key=lambda item: (-item[1], item[0]))[:5]

    lines = [
        "# 材料质量审计结论",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- workspace: `{payload['workspace']}`",
        f"- 材料卡总数: {payload['summary']['total_materials']}",
        f"- 有问题材料卡: {payload['summary']['materials_with_issues']}",
        "",
        "## 结论",
        "",
    ]

    lines.append(
        f"- 工作区共有 {payload['summary']['paper_count']} 个论文目录；"
        f"当前可发布 {payload['summary']['publishable_paper_count']} 篇，"
        f"最新运行失败 {payload['summary']['failed_paper_count']} 篇。"
    )

    if total == 0:
        lines.append("- 没有发现材料卡；当前 workspace 还不能进入综述材料审查。")
    elif problem_count == 0 and not issue_counts:
        lines.append("- 当前材料卡整体可进入下一步人工抽查。")
    elif problem_ratio >= 0.5:
        lines.append("- 当前材料卡可追溯，但还不能直接用于综述写作；超过一半材料仍有质量问题。")
    else:
        lines.append("- 当前材料卡已有可用基础，但仍需要先处理高频质量问题。")

    if content_ref_issues:
        lines.append(f"- 追溯链路存在 {content_ref_issues} 个问题，需要先修复 source/content_ref。")
    else:
        lines.append("- 追溯链路未发现明显断点，主要问题集中在材料可读性和粒度。")

    if top_issues:
        readable_top = "；".join(f"{_issue_label(code)} {count} 条" for code, count in top_issues)
        lines.append(f"- 高频问题：{readable_top}。")
    else:
        lines.append("- 未发现摘要、标题、正文清洗或粒度问题。")

    lines.extend(
        [
            "",
            "## 影响",
            "",
            "- 如果材料卡仍有标题泛化、摘要重复标题、HTML 残留或过长切片，下游 review pack 会命中脏材料。",
            "- 渐进式披露可以定位到原文行号，但材料本身不可读时，人工审查和后续 LLM 写作都会被拖慢。",
            "",
            "## 建议",
            "",
        ]
    )
    if problem_count == 0 and not issue_counts:
        lines.append("- 保持当前 material v2 结构，下一步可以做人工抽样审查和小规模 review pack 试写。")
    else:
        if "run.latest_failed" in issue_counts:
            lines.append("- 先查看最新失败论文的身份、范围和 coverage 记录；失败论文当前没有进入 corpus。")
        if any(
            code.startswith("coverage.") or code.startswith("region.")
            for code in issue_counts
            if code != "coverage.missing"
        ):
            lines.append("- 先处理正文覆盖率或区域边界问题，再进行材料内容审查。")
        if any(code.startswith("corpus.duplicate") for code in issue_counts):
            lines.append("- 复核重复论文和重复材料来源；当前报告只暴露，不自动删除。")
        if any(code.startswith("content_ref.") for code in issue_counts):
            lines.append("- 先修复 content_ref/source_path，保证每条材料都能回到原文。")
        if any(code in issue_counts for code in ("title.generic", "title.polluted", "title.weak_inferred")):
            lines.append("- 修复材料标题生成，避免无语义编号或 OCR 污染进入综述证据。")
        if any(code.startswith("summary.") or code.startswith("extract.") for code in issue_counts):
            lines.append("- 修复正文清洗和摘要生成，去掉标题重复、HTML/table 残留和硬截断。")
        if any(code.endswith("too_long") for code in issue_counts):
            lines.append("- 继续收紧 evidence card 粒度，避免一个材料卡承载过长章节。")

    lines.extend(
        [
            "",
            "## 数据概览",
            "",
            f"- 论文数: {payload['summary']['paper_count']}",
            f"- 综述包数: {payload['summary']['review_pack_count']}",
            f"- 问题比例: {problem_ratio:.2f}",
            "",
            "## 问题统计",
            "",
        ]
    )
    if issue_counts:
        for code, count in issue_counts.items():
            lines.append(f"- {code}: {count}")
    else:
        lines.append("- 无: 0")

    lines.extend(["", "## 论文分布", ""])
    for paper in payload["papers"]:
        issue_text = ", ".join(f"{code}: {count}" for code, count in paper["issue_counts"].items()) or "无"
        type_text = ", ".join(f"{_material_type_label(kind)}: {count}" for kind, count in paper["material_type_counts"].items()) or "无"
        kind_text = ", ".join(f"{kind}: {count}" for kind, count in paper.get("content_kind_counts", {}).items()) or "无"
        coverage_value = paper.get("body_coverage_ratio")
        coverage_text = f"{float(coverage_value):.2f}" if coverage_value is not None else "缺失"
        layer_values = [
            paper.get("source_to_region_ratio"),
            paper.get("region_to_block_ratio"),
            paper.get("block_to_card_ratio"),
        ]
        layer_text = "/".join(f"{float(value):.2f}" if value is not None else "缺失" for value in layer_values)
        lines.append(
            f"- {paper['paper_id']}: 材料卡={paper['material_count']} ({type_text}), "
            f"状态={paper.get('run_status') or 'unknown'}, 内容类型={kind_text}, "
            f"三层覆盖={layer_text}, 兼容覆盖率={coverage_text}, "
            f"有问题材料={paper['materials_with_issues']}, 问题={issue_text}"
        )

    lines.extend(["", "## 问题材料样本", ""])
    problem_materials = [material for material in payload["materials"] if material["issues"]]
    if problem_materials:
        for material in problem_materials[:50]:
            issue_text = ", ".join(issue["code"] for issue in material["issues"])
            lines.append(
                f"- {material['material_id']}: 类型={_material_type_label(material['material_type'])}, "
                f"跨度行数={material['span_lines']}, 字符数={material['span_chars']}, 问题={issue_text}"
            )
        if len(problem_materials) > 50:
            lines.append(f"- 另有 {len(problem_materials) - 50} 条未展示")
    else:
        lines.append("- 无")

    lines.extend(["", "## 综述包", ""])
    if payload["review_packs"]:
        for pack in payload["review_packs"]:
            issue_text = ", ".join(issue["code"] for issue in pack["issues"]) or "无"
            coverage = f"{pack['material_coverage_ratio']:.2f}"
            paper_hits = ", ".join(f"{paper_id}: {count}" for paper_id, count in pack["paper_hit_counts"].items()) or "无"
            lines.append(
                f"- {pack['topic']}: 证据条数={pack['evidence_count']}, "
                f"命中材料={pack['material_hit_count']}, 覆盖率={coverage}, "
                f"命中论文={paper_hits}, 问题={issue_text}"
            )
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def _issue_label(code: str) -> str:
    labels = {
        "summary.repeats_title": "摘要重复标题",
        "summary.contains_markup": "摘要含 HTML/table 残留",
        "summary.possibly_truncated": "摘要疑似硬截断",
        "title.generic": "标题无语义",
        "title.polluted": "标题疑似污染",
        "title.weak_inferred": "标题弱推断",
        "extract.contains_markup": "正文清洗不完整",
        "extract.too_long": "材料正文过长",
        "card.too_long": "材料卡过长",
        "content_ref.out_of_range": "原文引用越界",
        "content_ref.missing_source": "原文文件缺失",
        "coverage.missing_body_content": "正文覆盖不完整",
        "coverage.missing": "缺少三层覆盖证明",
        "coverage.unassigned_source_line": "原文行未归属区域",
        "coverage.overlapping_source_line": "原文行重复归属区域",
        "coverage.unblocked_source_line": "纳入行未进入内容块",
        "coverage.materialization_failed": "材料完整性证明失败",
        "coverage.line_ledger_missing": "缺少逐行分类账",
        "coverage.line_ledger_mismatch": "逐行分类账与原文不一致",
        "coverage.unexpected_overlap": "正文块重复覆盖",
        "region.back_matter_in_material": "后置区域进入材料",
        "corpus.duplicate_content": "跨论文材料重复",
        "corpus.duplicate_source": "论文源文件重复",
        "card.short_tail": "短尾材料卡",
        "table.parse_failed": "表格解析失败",
        "table.row_too_long": "表格行过长",
        "media.image_omitted": "图片内容未提取",
        "run.latest_failed": "最新运行失败",
        "parse.document_identity_mismatch": "论文身份不匹配",
    }
    return labels.get(code, code)


def _material_type_label(kind: str) -> str:
    return {"evidence_card": "证据卡", "chapter": "章节材料", "evidence_chunk": "证据片段"}.get(kind, kind)


def _issue(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
