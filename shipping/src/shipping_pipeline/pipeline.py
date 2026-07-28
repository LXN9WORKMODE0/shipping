from __future__ import annotations

import json
import hashlib
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from shipping_pipeline.models import Issue, PipelineResult
from shipping_pipeline.pdf_conversion import ConvertedDocument
from shipping_pipeline.card_builder import build_material_cards
from shipping_pipeline.content_blocks import build_blocks_for_document_map
from shipping_pipeline.markdown_structure import DocumentMap, HeadingNode, Region, build_document_map


SUPPORTED_MARKDOWN_SUFFIXES = {".md", ".markdown"}
TEXT_SUFFIXES = {".txt"}
PINYIN_SLUG = {
    "研": "yan",
    "究": "jiu",
    "方": "fang",
    "法": "fa",
    "问": "wen",
    "题": "ti",
    "综": "zong",
    "述": "shu",
    "证": "zheng",
    "据": "ju",
}
@dataclass(frozen=True)
class ParseOutput:
    document_map: DocumentMap
    quality_label: str
    issues: list[Issue]


class LiteraturePipeline:
    """五阶段 pipeline：暴露质量问题，而不是在流程中掩盖问题。"""

    def __init__(self, workspace: str | Path, pdf_converter: Any | None = None) -> None:
        self.workspace = Path(workspace)
        self.pdf_converter = pdf_converter

    def run(
        self,
        source_path: str | Path,
        paper_id: str | None = None,
        review_topic: str = "",
        workspace_id: str | None = None,
    ) -> PipelineResult:
        source = Path(source_path)
        resolved_paper_id = paper_id or _slugify(source.stem)
        generation_id = _new_generation_id()
        resolved_workspace_id = workspace_id or resolved_paper_id
        if (
            not resolved_workspace_id.strip()
            or Path(resolved_workspace_id).name != resolved_workspace_id
            or resolved_workspace_id in {".", ".."}
        ):
            raise ValueError(
                f"workspace_id 必须是单个非空目录名：{resolved_workspace_id}"
            )
        paper_workspace = self.workspace / resolved_workspace_id
        try:
            paper_workspace.resolve().relative_to(self.workspace.resolve())
        except ValueError as exc:
            raise ValueError(
                f"workspace_id 越过工作区边界：{resolved_workspace_id}"
            ) from exc
        _ensure_workspace(paper_workspace)

        stages: dict[str, dict[str, Any]] = {}
        issues: list[Issue] = []
        artifacts: dict[str, str] = {}

        content = self._convert(source, paper_workspace, stages, issues, artifacts)
        if content is None:
            self._invalidate_current_materials(paper_workspace, generation_id, stages, issues)
            self._write_run_record(
                paper_workspace,
                resolved_paper_id,
                source,
                status="failed",
                structure_quality="red",
                stages=stages,
                issues=issues,
                artifacts=artifacts,
                generation_id=generation_id,
            )
            self._rebuild_corpus(stages, artifacts)
            self._build_review_pack(review_topic, stages, artifacts)
            return PipelineResult(resolved_paper_id, "failed", "red", len(issues), str(paper_workspace))

        parsed = self._parse(resolved_paper_id, content, paper_workspace, stages, artifacts)
        issues.extend(parsed.issues)
        effective_quality_label = parsed.quality_label

        if parsed.quality_label == "red":
            self._invalidate_current_materials(paper_workspace, generation_id, stages, issues)
            status = "failed"
        else:
            try:
                self._materialize(
                    resolved_paper_id,
                    parsed,
                    content,
                    paper_workspace,
                    stages,
                    artifacts,
                    generation_id,
                )
            except ValueError as exc:
                issue = Issue(
                    stage="material",
                    code="coverage.materialization_failed",
                    message="原文行、区域、内容块和材料卡之间的完整性证明失败，未发布材料。",
                    severity="error",
                    details={"error": str(exc)},
                )
                issues.append(issue)
                effective_quality_label = "red"
                quality_path = paper_workspace / "structure" / "quality.json"
                quality = json.loads(quality_path.read_text(encoding="utf-8"))
                quality["label"] = "red"
                quality.setdefault("issues", []).append(issue.to_dict())
                _write_json(quality_path, quality)
                self._invalidate_current_materials(paper_workspace, generation_id, stages, issues)
                status = "failed"
            else:
                self._write_run_record(
                    paper_workspace,
                    resolved_paper_id,
                    source,
                    status="completed",
                    structure_quality=effective_quality_label,
                    stages=stages,
                    issues=issues,
                    artifacts=artifacts,
                    generation_id=generation_id,
                )
                self._rebuild_corpus(stages, artifacts)
                self._build_review_pack(review_topic, stages, artifacts)
                status = "completed"

        self._write_run_record(
            paper_workspace,
            resolved_paper_id,
            source,
            status=status,
            structure_quality=effective_quality_label,
            stages=stages,
            issues=issues,
            artifacts=artifacts,
            generation_id=generation_id,
        )
        if status == "failed":
            self._rebuild_corpus(stages, artifacts)
            self._build_review_pack(review_topic, stages, artifacts)
        return PipelineResult(resolved_paper_id, status, effective_quality_label, len(issues), str(paper_workspace))

    def _convert(
        self,
        source: Path,
        paper_workspace: Path,
        stages: dict[str, dict[str, Any]],
        issues: list[Issue],
        artifacts: dict[str, str],
    ) -> str | None:
        suffix = source.suffix.lower()
        if not source.exists():
            issue = Issue(
                stage="convert",
                code="convert.source_missing",
                message="来源文件不存在。",
                details={"source": str(source)},
            )
            issues.append(issue)
            stages["convert"] = {"status": "failed", "issues": [issue.to_dict()]}
            return None

        if suffix == ".pdf":
            return self._convert_pdf(source, paper_workspace, stages, issues, artifacts)

        if suffix not in SUPPORTED_MARKDOWN_SUFFIXES and suffix not in TEXT_SUFFIXES:
            issue = Issue(
                stage="convert",
                code="convert.unsupported_input",
                message=f"不支持的输入类型：{suffix or '<none>'}。除非配置 PDF 解析器，否则当前版本只接受 Markdown 或文本文件。",
                details={"source": str(source)},
            )
            issues.append(issue)
            stages["convert"] = {"status": "failed", "issues": [issue.to_dict()]}
            return None

        content = source.read_text(encoding="utf-8")
        normalized_path = paper_workspace / "normalized" / "document.md"
        normalized_path.write_text(content, encoding="utf-8")
        artifacts["normalized_document"] = _relative_to_workspace(normalized_path, self.workspace)
        stages["convert"] = {"status": "completed", "source": str(source), "artifact": artifacts["normalized_document"]}
        return content

    def _convert_pdf(
        self,
        source: Path,
        paper_workspace: Path,
        stages: dict[str, dict[str, Any]],
        issues: list[Issue],
        artifacts: dict[str, str],
    ) -> str | None:
        if self.pdf_converter is None:
            issue = Issue(
                stage="convert",
                code="convert.unsupported_input",
                message="PDF 输入必须显式配置 PDF 解析 provider，例如 MinerU。",
                details={"source": str(source)},
            )
            issues.append(issue)
            stages["convert"] = {"status": "failed", "issues": [issue.to_dict()]}
            return None

        try:
            converted: ConvertedDocument = self.pdf_converter.convert(source)
        except Exception as exc:
            issue = Issue(
                stage="convert",
                code="convert.pdf_api_failed",
                message="PDF 解析器在产出 Markdown 之前失败。",
                details={"source": str(source), "error": str(exc), "provider": getattr(self.pdf_converter, "provider", "unknown")},
            )
            issues.append(issue)
            stages["convert"] = {"status": "failed", "provider": getattr(self.pdf_converter, "provider", "unknown"), "issues": [issue.to_dict()]}
            return None

        content = converted.markdown
        if not content.strip():
            issue = Issue(
                stage="convert",
                code="convert.empty_markdown",
                message="PDF 解析器产出了空 Markdown。",
                details={"source": str(source), "provider": converted.provider},
            )
            issues.append(issue)
            stages["convert"] = {"status": "failed", "provider": converted.provider, "issues": [issue.to_dict()]}
            return None

        normalized_path = paper_workspace / "normalized" / "document.md"
        normalized_path.write_text(content, encoding="utf-8")
        artifacts["normalized_document"] = _relative_to_workspace(normalized_path, self.workspace)
        stages["convert"] = {
            "status": "completed",
            "source": str(source),
            "artifact": artifacts["normalized_document"],
            "provider": converted.provider,
            "metadata": converted.metadata,
        }
        return content

    def _parse(
        self,
        paper_id: str,
        content: str,
        paper_workspace: Path,
        stages: dict[str, dict[str, Any]],
        artifacts: dict[str, str],
    ) -> ParseOutput:
        lines = content.splitlines()
        document_map = build_document_map(paper_id, lines)
        issues = [_issue_from_dict(issue) for issue in document_map.issues]
        quality_label = document_map.quality_label
        structure = _document_map_payload(document_map, lines)
        quality = {
            "paper_id": paper_id,
            "label": quality_label,
            "issues": [issue.to_dict() for issue in issues],
        }

        structure_path = paper_workspace / "structure" / "structure.json"
        quality_path = paper_workspace / "structure" / "quality.json"
        _write_json(structure_path, structure)
        _write_json(quality_path, quality)
        artifacts["structure"] = _relative_to_workspace(structure_path, self.workspace)
        artifacts["structure_quality"] = _relative_to_workspace(quality_path, self.workspace)
        stages["parse"] = {
            "status": "completed" if quality_label != "red" else "failed",
            "quality": quality_label,
            "issues": [issue.to_dict() for issue in issues],
        }
        return ParseOutput(document_map, quality_label, issues)

    def _materialize(
        self,
        paper_id: str,
        parsed: ParseOutput,
        content: str,
        paper_workspace: Path,
        stages: dict[str, dict[str, Any]],
        artifacts: dict[str, str],
        generation_id: str,
    ) -> None:
        lines = content.splitlines()
        issue_codes = [issue.code for issue in parsed.issues]
        blocks = build_blocks_for_document_map(lines, parsed.document_map)
        materials = build_material_cards(
            paper_id=paper_id,
            paper_title=parsed.document_map.paper_title,
            blocks=blocks,
            confidence_flags=issue_codes,
        )
        block_rows = [{**block.to_dict(), "generation_id": generation_id} for block in blocks]
        for material in materials:
            material["generation_id"] = generation_id
        coverage = _coverage_payload(parsed.document_map, blocks, materials)
        structure_path = paper_workspace / "structure" / "structure.json"
        structure = json.loads(structure_path.read_text(encoding="utf-8"))
        structure["coverage"] = coverage
        _write_json(structure_path, structure)
        if (
            coverage["source_to_region_ratio"] != 1.0
            or coverage["region_to_block_ratio"] != 1.0
            or coverage["block_to_card_ratio"] != 1.0
            or coverage["multiply_materialized_block_ids"]
        ):
            raise ValueError("原文行、区域、内容块和材料卡之间没有形成完整且唯一的覆盖链。")

        blocks_path = paper_workspace / "materials" / "blocks.jsonl"
        materials_path = paper_workspace / "materials" / "materials.jsonl"
        generation_dir = paper_workspace / "materials" / "generations" / generation_id
        self._archive_current_materials(paper_workspace)
        _write_jsonl(generation_dir / "blocks.jsonl", block_rows)
        _write_jsonl(generation_dir / "materials.jsonl", materials)
        _write_jsonl(blocks_path, block_rows)
        _write_jsonl(materials_path, materials)
        _write_json(
            paper_workspace / "materials" / "current.json",
            {
                "status": "completed",
                "generation_id": generation_id,
                "blocks_path": f"generations/{generation_id}/blocks.jsonl",
                "materials_path": f"generations/{generation_id}/materials.jsonl",
                "published_at": _now(),
            },
        )
        artifacts["content_blocks"] = _relative_to_workspace(blocks_path, self.workspace)
        artifacts["materials"] = _relative_to_workspace(materials_path, self.workspace)
        stages["material"] = {
            "status": "completed",
            "block_count": len(blocks),
            "material_count": len(materials),
            "material_types": sorted({item["material_type"] for item in materials}),
            "content_kinds": sorted({item["content_kind"] for item in materials}),
            "coverage": coverage,
            "generation_id": generation_id,
        }

    def _archive_current_materials(self, paper_workspace: Path) -> str | None:
        materials_root = paper_workspace / "materials"
        stable_paths = [materials_root / "blocks.jsonl", materials_root / "materials.jsonl"]
        existing = [path for path in stable_paths if path.exists()]
        if not existing:
            return None
        current_path = materials_root / "current.json"
        previous_generation = None
        if current_path.exists():
            current = json.loads(current_path.read_text(encoding="utf-8"))
            previous_generation = current.get("generation_id")
        if not previous_generation:
            run_path = paper_workspace / "run.json"
            if run_path.exists():
                previous_generation = json.loads(run_path.read_text(encoding="utf-8")).get("generation_id")
        archive_id = str(previous_generation or f"legacy-{_new_generation_id()}")
        archive_dir = materials_root / "generations" / archive_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        for path in existing:
            target = archive_dir / path.name
            if not target.exists():
                shutil.copy2(path, target)
        return archive_id

    def _invalidate_current_materials(
        self,
        paper_workspace: Path,
        failed_generation_id: str,
        stages: dict[str, dict[str, Any]],
        issues: list[Issue],
    ) -> None:
        previous_generation = self._archive_current_materials(paper_workspace)
        materials_root = paper_workspace / "materials"
        removed = []
        for filename in ("blocks.jsonl", "materials.jsonl"):
            path = materials_root / filename
            if path.exists():
                path.unlink()
                removed.append(filename)
        _write_json(
            materials_root / "current.json",
            {
                "status": "failed",
                "generation_id": None,
                "failed_generation_id": failed_generation_id,
                "previous_success_generation_id": previous_generation,
                "published_at": _now(),
            },
        )
        stages["material"] = {
            "status": "invalidated",
            "generation_id": failed_generation_id,
            "previous_success_generation_id": previous_generation,
            "removed_current_files": removed,
        }
        if previous_generation is not None:
            issues.append(
                Issue(
                    stage="material",
                    code="material.previous_generation_invalidated",
                    message="最新运行失败，上一成功代际已归档并从当前材料入口撤下。",
                    severity="warning",
                    details={"previous_generation_id": previous_generation},
                )
            )

    def _rebuild_corpus(self, stages: dict[str, dict[str, Any]], artifacts: dict[str, str]) -> None:
        corpus_dir = self.workspace / "_corpus"
        corpus_dir.mkdir(parents=True, exist_ok=True)
        papers: list[dict[str, Any]] = []
        materials: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []

        for paper_dir in sorted(path for path in self.workspace.iterdir() if path.is_dir() and not path.name.startswith("_")):
            run_path = paper_dir / "run.json"
            if not run_path.exists():
                continue
            run_record = json.loads(run_path.read_text(encoding="utf-8"))
            papers.append(
                {
                    "paper_id": run_record["paper_id"],
                    "status": run_record["status"],
                    "structure_quality": run_record["structure_quality"],
                    "source": run_record["source"],
                    "generation_id": run_record.get("generation_id"),
                }
            )
            issues.extend(run_record.get("issues", []))
            if run_record.get("status") != "completed":
                continue

            current_path = paper_dir / "materials" / "current.json"
            materials_path = paper_dir / "materials" / "materials.jsonl"
            if not current_path.exists() or not materials_path.exists():
                issues.append(
                    Issue(
                        stage="corpus",
                        code="corpus.current_generation_missing",
                        message="成功运行缺少当前材料代际入口，未加入 corpus。",
                        details={"paper_id": run_record["paper_id"]},
                    ).to_dict()
                )
                continue
            current = json.loads(current_path.read_text(encoding="utf-8"))
            generation_id = run_record.get("generation_id")
            if current.get("status") != "completed" or current.get("generation_id") != generation_id:
                issues.append(
                    Issue(
                        stage="corpus",
                        code="corpus.stale_generation_excluded",
                        message="材料代际与最新成功运行不一致，已从 corpus 排除。",
                        details={"paper_id": run_record["paper_id"], "generation_id": generation_id},
                    ).to_dict()
                )
                continue
            rows = _read_jsonl(materials_path)
            if any(row.get("generation_id") != generation_id for row in rows):
                issues.append(
                    Issue(
                        stage="corpus",
                        code="corpus.stale_generation_excluded",
                        message="材料行包含非当前代际数据，整篇论文已从 corpus 排除。",
                        details={"paper_id": run_record["paper_id"], "generation_id": generation_id},
                    ).to_dict()
                )
                continue
            materials.extend(rows)

        papers_path = corpus_dir / "papers.jsonl"
        materials_path = corpus_dir / "materials.jsonl"
        issues_path = corpus_dir / "issues.jsonl"
        _write_jsonl(papers_path, papers)
        _write_jsonl(materials_path, materials)
        _write_jsonl(issues_path, issues)
        artifacts["corpus_papers"] = _relative_to_workspace(papers_path, self.workspace)
        artifacts["corpus_materials"] = _relative_to_workspace(materials_path, self.workspace)
        artifacts["corpus_issues"] = _relative_to_workspace(issues_path, self.workspace)
        stages["corpus"] = {"status": "completed", "paper_count": len(papers), "material_count": len(materials), "issue_count": len(issues)}

    def _build_review_pack(self, topic: str, stages: dict[str, dict[str, Any]], artifacts: dict[str, str]) -> None:
        pack_dir = self.workspace / "_review_packs"
        pack_dir.mkdir(parents=True, exist_ok=True)
        materials_path = self.workspace / "_corpus" / "materials.jsonl"
        issues_path = self.workspace / "_corpus" / "issues.jsonl"
        materials = _read_jsonl(materials_path) if materials_path.exists() else []
        issues = _read_jsonl(issues_path) if issues_path.exists() else []
        terms = _topic_terms(topic)
        evidence_matrix = []
        for material in materials:
            score = _relevance_score(terms, material)
            if score <= 0:
                continue
            evidence_matrix.append(
                {
                    "material_id": material["material_id"],
                    "paper_id": material["paper_id"],
                    "title": material["clean_title"],
                    "material_type": material["material_type"],
                    "relevance_score": score,
                    "content_ref": material["content_ref"],
                    "source_span": material.get("source_span", material["content_ref"]),
                    "confidence_flags": material["confidence_flags"],
                    "quality_flags": material.get("quality_flags", []),
                    "summary": material["summary"],
                    "lead_excerpt": material.get("lead_excerpt", material["summary"]),
                    "extract": material.get("extract", ""),
                }
            )

        evidence_matrix.sort(key=lambda item: (-item["relevance_score"], item["paper_id"], item["material_id"]))
        pack = {
            "topic": topic,
            "generated_at": _now(),
            "source_corpus": "_corpus/materials.jsonl",
            "evidence_matrix": evidence_matrix,
            "claim_evidence_map": [],
            "outline_seed": [item["title"] for item in evidence_matrix[:8]],
            "issues": issues,
        }
        pack_path = pack_dir / f"{_slugify(topic)}.json"
        _write_json(pack_path, pack)
        artifacts["review_pack"] = _relative_to_workspace(pack_path, self.workspace)
        stages["review_pack"] = {"status": "completed", "topic": topic, "evidence_count": len(evidence_matrix), "issue_count": len(issues)}

    def _write_run_record(
        self,
        paper_workspace: Path,
        paper_id: str,
        source: Path,
        status: str,
        structure_quality: str,
        stages: dict[str, dict[str, Any]],
        issues: list[Issue],
        artifacts: dict[str, str],
        generation_id: str,
    ) -> None:
        run_record = {
            "paper_id": paper_id,
            "status": status,
            "source": str(source),
            "structure_quality": structure_quality,
            "generated_at": _now(),
            "generation_id": generation_id,
            "stages": stages,
            "artifacts": artifacts,
            "issues": [issue.to_dict() for issue in issues],
        }
        _write_json(paper_workspace / "run.json", run_record)
        _write_json(paper_workspace / "runs" / generation_id / "run.json", run_record)


def _issue_from_dict(payload: dict[str, Any]) -> Issue:
    return Issue(
        stage=str(payload.get("stage", "parse")),
        code=str(payload.get("code", "parse.unknown")),
        message=str(payload.get("message", "解析阶段记录了未分类问题。")),
        severity=str(payload.get("severity", "warning")),
        details=dict(payload.get("details", {})),
    )


def _document_map_payload(document_map: DocumentMap, lines: list[str]) -> dict[str, Any]:
    body_regions = {
        region.span.start_line: region
        for region in document_map.regions
        if region.role == "body" and region.included_in_materials
    }
    nodes = []
    for heading in document_map.headings:
        region = body_regions.get(heading.start_line)
        if region is None:
            continue
        nodes.append(_body_node_payload(heading, region, len(nodes) + 1))

    selected = document_map.selected_segment
    selected_visible_length = 0
    if selected is not None:
        lower = max(selected.start_line, 1)
        upper = min(selected.end_line, len(lines))
        selected_visible_length = _visible_text_length(lines[lower - 1 : upper])

    return {
        "paper_id": document_map.paper_id,
        "paper_title": document_map.paper_title,
        "generated_at": _now(),
        "selected_segment": selected.to_dict() if selected is not None else None,
        "regions": [region.to_dict() for region in document_map.regions],
        "line_ledger": [assignment.to_dict() for assignment in document_map.line_ledger],
        "headings": [heading.to_dict() for heading in document_map.headings],
        "nodes": nodes,
        "stats": {
            "heading_count": len(document_map.headings),
            "inferred_heading_count": sum(heading.source == "plain" for heading in document_map.headings),
            "body_heading_count": len(nodes),
            "visible_text_length": selected_visible_length,
            "region_counts": dict(Counter(region.role for region in document_map.regions)),
        },
    }


def _body_node_payload(heading: HeadingNode, region: Region, ordinal: int) -> dict[str, Any]:
    return {
        "id": heading.node_id,
        "title_raw": heading.raw_title,
        "title_norm": heading.clean_title,
        "role": "body",
        "semantic_level": heading.depth,
        "depth": heading.depth,
        "ordinal": ordinal,
        "ordinal_path": list(heading.ordinal_path),
        "content_ref": region.span.to_dict(),
        "confidence_flags": list(heading.confidence_flags),
        "heading_source": heading.source,
    }


def _coverage_payload(
    document_map: DocumentMap,
    blocks: list[Any],
    materials: list[dict[str, Any]],
) -> dict[str, Any]:
    ledger = list(document_map.line_ledger)
    selected_line_numbers = {assignment.line_number for assignment in ledger}
    classified_line_numbers = {
        assignment.line_number
        for assignment in ledger
        if assignment.assignment_count == 1 and assignment.role is not None
    }
    unassigned_source_lines = sorted(
        assignment.line_number for assignment in ledger if assignment.assignment_count == 0
    )
    multiply_classified_source_lines = sorted(
        assignment.line_number for assignment in ledger if assignment.assignment_count > 1
    )
    included_source_lines = {
        assignment.line_number
        for assignment in ledger
        if assignment.included_in_materials and assignment.role in {"abstract", "body"}
    }
    blocked_source_lines: set[int] = set()
    for block in blocks:
        for span in block.source_spans:
            blocked_source_lines.update(range(span.start_line, span.end_line + 1))
    blocked_source_lines &= included_source_lines
    unblocked_source_lines = sorted(included_source_lines - blocked_source_lines)

    block_ids = [block.block_id for block in blocks]
    assigned_ids = [
        block_id
        for material in materials
        for block_id in material.get("source_block_ids", [])
    ]
    assignment_counts = Counter(assigned_ids)
    known_ids = set(block_ids)
    materialized_ids = known_ids & set(assigned_ids)
    unmaterialized = sorted(known_ids - set(assigned_ids))
    multiply_materialized = sorted(
        block_id
        for block_id, count in assignment_counts.items()
        if block_id in known_ids and count > 1
    )
    source_to_region_ratio = (
        len(classified_line_numbers) / len(selected_line_numbers) if selected_line_numbers else 0.0
    )
    region_to_block_ratio = (
        len(blocked_source_lines) / len(included_source_lines) if included_source_lines else 0.0
    )
    block_to_card_ratio = len(materialized_ids) / len(known_ids) if known_ids else 0.0
    return {
        "selected_nonempty_line_count": len(selected_line_numbers),
        "classified_source_line_count": len(classified_line_numbers),
        "unassigned_source_line_numbers": unassigned_source_lines,
        "multiply_classified_source_line_numbers": multiply_classified_source_lines,
        "source_to_region_ratio": source_to_region_ratio,
        "included_source_line_count": len(included_source_lines),
        "blocked_source_line_count": len(blocked_source_lines),
        "unblocked_source_line_numbers": unblocked_source_lines,
        "region_to_block_ratio": region_to_block_ratio,
        "included_block_count": len(block_ids),
        "materialized_block_count": len(materialized_ids),
        "unmaterialized_block_ids": unmaterialized,
        "multiply_materialized_block_ids": multiply_materialized,
        "block_to_card_ratio": block_to_card_ratio,
        "body_coverage_ratio": block_to_card_ratio,
        "line_to_region": {
            "ratio": source_to_region_ratio,
            "unassigned_line_numbers": unassigned_source_lines,
            "multiply_classified_line_numbers": multiply_classified_source_lines,
        },
        "region_to_block": {
            "ratio": region_to_block_ratio,
            "unblocked_line_numbers": unblocked_source_lines,
        },
        "block_to_card": {
            "ratio": block_to_card_ratio,
            "unmaterialized_block_ids": unmaterialized,
            "multiply_materialized_block_ids": multiply_materialized,
        },
    }


def _ensure_workspace(paper_workspace: Path) -> None:
    for name in ("normalized", "structure", "materials"):
        (paper_workspace / name).mkdir(parents=True, exist_ok=True)


def _visible_text_length(lines: list[str]) -> int:
    return len("".join(line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")))


def _topic_terms(topic: str) -> list[str]:
    return [term for term in re.split(r"\s+", topic.strip()) if term]


def _relevance_score(terms: list[str], material: dict[str, Any]) -> int:
    if not terms:
        return 1
    haystack = " ".join(
        [
            str(material.get("clean_title", "")),
            str(material.get("summary", "")),
            str(material.get("lead_excerpt", "")),
            str(material.get("extract", "")),
        ]
    )
    return sum(1 for term in terms if term in haystack)


def _slugify(value: str) -> str:
    pieces: list[str] = []
    for char in value.strip().lower():
        if char.isascii() and char.isalnum():
            pieces.append(char)
        elif char in PINYIN_SLUG:
            pieces.append(f"-{PINYIN_SLUG[char]}-")
        elif char.isalnum():
            pieces.append(char)
        elif char.isspace() or char in {"-", "_"}:
            pieces.append("-")
    slug = re.sub(r"-+", "-", "".join(pieces)).strip("-")
    if slug:
        return slug
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"paper-{digest}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _relative_to_workspace(path: Path, workspace: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_generation_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid4().hex[:8]}"
