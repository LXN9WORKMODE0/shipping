from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .llm_projection import project_cards
from .research_landscape import (
    ResearchLandscapeSnapshot,
    ResearchLandscapeSource,
    create_research_landscape_snapshot,
)
from .review_framework import (
    ReviewFrameworkRunSource,
    load_review_framework_run,
)
from .source_window_contracts import (
    MATERIAL_TIERS,
    SOURCE_WINDOW_RUN_SCHEMA_VERSION,
    SOURCE_WINDOW_SCHEMA_VERSION,
    SOURCE_WINDOW_SELECTION_SCHEMA_VERSION,
    SourceWindowContractError,
    sha256_json,
    stable_id,
    validate_source_window_selection,
)


SOURCE_WINDOW_ROOT = "_source_windows"
WINDOW_TYPE_PRIORITY = {
    "implementation_status": 0,
    "author_conclusion": 1,
    "result_context": 2,
    "method_context": 3,
    "limitation_context": 4,
    "problem_context": 5,
}


class SourceWindowSelectionError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.detail = message
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True)
class SourceWindowBuildSnapshot:
    framework_source: ReviewFrameworkRunSource
    landscape_snapshot: ResearchLandscapeSnapshot
    section: dict[str, Any]
    selection: dict[str, Any]
    paper_artifacts: tuple[dict[str, Any], ...]
    input_sha256: str


@dataclass(frozen=True)
class SourceWindowRunSource:
    run_id: str
    manifest_sha256: str
    output_sha256: str
    selection: dict[str, Any]
    framework: dict[str, Any]


def load_source_window_run(
    workspace: str | Path,
    run_id: str,
) -> SourceWindowRunSource:
    workspace_path = Path(workspace)
    _safe_segment(run_id, "source_window_run_id")
    run_dir = workspace_path / SOURCE_WINDOW_ROOT / "runs" / run_id
    manifest_bytes = _read_bytes(
        run_dir / "manifest.json",
        "Source Window manifest",
    )
    manifest = _json_object(manifest_bytes, "Source Window manifest")
    if manifest.get("schema_version") != SOURCE_WINDOW_RUN_SCHEMA_VERSION:
        raise SourceWindowSelectionError(
            "source_window.run_schema_invalid",
            "Source Window运行Schema版本不受支持。",
        )
    if manifest.get("run_id") != run_id:
        raise SourceWindowSelectionError(
            "source_window.run_identity_mismatch",
            "Source Window目录名与manifest.run_id不一致。",
        )
    if manifest.get("status") != "completed":
        raise SourceWindowSelectionError(
            "source_window.run_status_invalid",
            f"Source Window运行状态不可用：{manifest.get('status')!r}。",
        )
    output_bytes = _read_bytes(
        run_dir / "output" / "source_window_selection.json",
        "Source Window正式输出",
    )
    selection = validate_source_window_selection(
        _json_object(output_bytes, "Source Window正式输出")
    )
    if (
        manifest.get("selection_id") != selection["selection_id"]
        or manifest.get("output_sha256") != _sha256_bytes(output_bytes)
        or manifest.get("input_sha256")
        != sha256_json(
            {
                key: value
                for key, value in selection.items()
                if key != "selection_id"
            }
        )
    ):
        raise SourceWindowSelectionError(
            "source_window.output_identity_mismatch",
            "Source Window输出身份或哈希不一致。",
        )
    framework_source = load_review_framework_run(
        workspace_path,
        str(manifest["framework_run_id"]),
    )
    section = _select_section(
        framework_source.framework,
        section_id=str(manifest["section_id"]),
        section_index=None,
    )
    replay = _build_source_window_snapshot(
        workspace_path,
        framework_source=framework_source,
        section=section,
        material_tier=str(manifest["material_tier"]),
    )
    if replay.selection != selection:
        raise SourceWindowSelectionError(
            "source_window.output_replay_mismatch",
            "Source Window正式输出无法由冻结来源重放。",
        )
    _verify_file_hash_ledger(run_dir)
    return SourceWindowRunSource(
        run_id=run_id,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        output_sha256=_sha256_bytes(output_bytes),
        selection=selection,
        framework=copy.deepcopy(framework_source.framework),
    )


class SourceWindowSelectionBuilder:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def build(
        self,
        *,
        framework_run_id: str,
        section_id: str | None = None,
        section_index: int | None = None,
        material_tier: str = "tier_1",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved, "run_id")
        run_dir = self.workspace / SOURCE_WINDOW_ROOT / "runs" / resolved
        if run_dir.exists():
            raise SourceWindowSelectionError(
                "source_window.run_exists",
                f"Source Window run_id已存在，不能覆盖：{resolved}",
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot: SourceWindowBuildSnapshot | None = None
        try:
            if material_tier not in MATERIAL_TIERS:
                raise SourceWindowSelectionError(
                    "source_window.material_tier_invalid",
                    f"material_tier必须是以下值之一：{list(MATERIAL_TIERS)}。",
                    path="$.material_tier",
                )
            framework_source = load_review_framework_run(
                self.workspace,
                framework_run_id,
            )
            section = _select_section(
                framework_source.framework,
                section_id=section_id,
                section_index=section_index,
            )
            snapshot = _build_source_window_snapshot(
                self.workspace,
                framework_source=framework_source,
                section=section,
                material_tier=material_tier,
            )
            _write_input_artifacts(run_dir, snapshot)
            _write_json(
                run_dir / "output" / "source_window_selection.json",
                snapshot.selection,
            )
            _write_json(
                run_dir / "audit" / "coverage.json",
                snapshot.selection["coverage"],
            )
            from .source_window_report import render_source_window_report

            _write_text(
                run_dir / "review" / "source_window_report.md",
                render_source_window_report(snapshot.selection),
            )
            manifest = _build_manifest(
                run_id=resolved,
                status="completed",
                started_at=started_at,
                framework_run_id=framework_run_id,
                requested_section_id=section_id,
                requested_section_index=section_index,
                material_tier=material_tier,
                snapshot=snapshot,
                failure=None,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "stage": _failure_stage(run_dir),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = _build_manifest(
                run_id=resolved,
                status="failed",
                started_at=started_at,
                framework_run_id=framework_run_id,
                requested_section_id=section_id,
                requested_section_index=section_index,
                material_tier=material_tier,
                snapshot=snapshot,
                failure=failure,
            )
            _write_json(run_dir / "manifest.json", manifest)
            return _result(run_dir, manifest)


def _build_source_window_snapshot(
    workspace: Path,
    *,
    framework_source: ReviewFrameworkRunSource,
    section: dict[str, Any],
    material_tier: str,
) -> SourceWindowBuildSnapshot:
    framework_snapshot = framework_source.snapshot
    landscape_run_dir = (
        workspace
        / "_research_landscapes"
        / "runs"
        / framework_snapshot.landscape_run_id
    )
    landscape_snapshot = create_research_landscape_snapshot(
        workspace,
        collection_path=landscape_run_dir / "input" / "collection.json",
    )
    landscape_manifest = _read_json(
        landscape_run_dir / "manifest.json",
        "Research Landscape manifest",
    )
    if (
        landscape_snapshot.input_sha256
        != landscape_manifest.get("input_sha256")
    ):
        raise SourceWindowSelectionError(
            "source_window.landscape_source_mismatch",
            "Landscape来源Understanding快照哈希不一致。",
        )
    source_by_paper = {
        source.paper_id: source for source in landscape_snapshot.sources
    }
    section_paper_ids = [str(value) for value in section["paper_ids"]]
    section_contribution_ids = [
        str(value) for value in section["contribution_ids"]
    ]
    citation_by_paper = dict(
        zip(
            section_paper_ids,
            [str(value) for value in section["citation_keys"]],
            strict=True,
        )
    )
    missing_papers = sorted(set(section_paper_ids) - set(source_by_paper))
    if missing_papers:
        raise SourceWindowSelectionError(
            "source_window.paper_source_missing",
            f"章节论文缺少冻结Understanding来源：{missing_papers}。",
            path="$.section.paper_ids",
        )
    corpus_paper_ids = [
        source.paper_id for source in landscape_snapshot.sources
    ]
    if material_tier == "tier_3" and section_paper_ids != corpus_paper_ids:
        raise SourceWindowSelectionError(
            "source_window.tier_3_requires_full_corpus_section",
            "Tier 3当前只允许章节论文按语料顺序完整覆盖全部论文。",
            path="$.material_tier",
        )

    projections: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    expanded_markdown: list[dict[str, Any]] = []
    citation_metadata: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    covered_contributions: list[str] = []
    required_materials: list[str] = []
    candidate_count = 0
    paper_context_roles: dict[str, set[str]] = {}
    paper_expected_context_roles: dict[str, set[str]] = {}

    for paper_index, paper_id in enumerate(section_paper_ids, start=1):
        source = source_by_paper[paper_id]
        run_dir = (
            workspace / "_paper_understandings" / "runs" / source.run_id
        )
        raw_document = _read_bytes(
            run_dir / "input" / "document.md",
            f"{paper_id}冻结Markdown",
        )
        try:
            document = raw_document.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SourceWindowSelectionError(
                "source_window.document_encoding_invalid",
                f"{paper_id}冻结Markdown不是UTF-8。",
            ) from exc
        raw_materials = _read_bytes(
            run_dir / "input" / "materials.jsonl",
            f"{paper_id}冻结Card",
        )
        raw_projected = _read_bytes(
            run_dir / "input" / "projected_cards.jsonl",
            f"{paper_id}冻结Card投影",
        )
        raw_evidence = _read_bytes(
            run_dir / "input" / "evidence_units.jsonl",
            f"{paper_id}冻结Evidence",
        )
        raw_understanding = _read_bytes(
            run_dir / "output" / "paper_understanding.json",
            f"{paper_id}Understanding",
        )
        raw_manifest = _read_bytes(
            run_dir / "manifest.json",
            f"{paper_id}Understanding manifest",
        )
        materials = _read_jsonl_bytes(raw_materials, f"{paper_id} Card")
        projected = _read_jsonl_bytes(
            raw_projected,
            f"{paper_id} Card投影",
        )
        evidence_units = _read_jsonl_bytes(
            raw_evidence,
            f"{paper_id} Evidence",
        )
        understanding = _json_object(
            raw_understanding,
            f"{paper_id} Understanding",
        )
        if materials != list(source.materials):
            raise SourceWindowSelectionError(
                "source_window.material_snapshot_mismatch",
                f"{paper_id}冻结Card与Landscape来源不一致。",
            )
        if projected != project_cards(materials):
            raise SourceWindowSelectionError(
                "source_window.projected_cards_mismatch",
                f"{paper_id}冻结Card投影无法重建。",
            )
        if evidence_units != list(source.evidence_units):
            raise SourceWindowSelectionError(
                "source_window.evidence_snapshot_mismatch",
                f"{paper_id}冻结Evidence与Landscape来源不一致。",
            )
        if understanding != source.understanding:
            raise SourceWindowSelectionError(
                "source_window.understanding_snapshot_mismatch",
                f"{paper_id}Understanding与Landscape来源不一致。",
            )

        selected_contributions = [
            row
            for row in understanding.get("contributions", [])
            if str(row.get("contribution_id"))
            in set(section_contribution_ids)
        ]
        candidates = _select_material_candidates(
            paper_id,
            understanding=understanding,
            evidence_units=evidence_units,
            selected_contributions=selected_contributions,
        )
        paper_expected_context_roles[paper_id] = (
            _expected_context_roles(understanding, paper_id=paper_id)
        )
        candidate_count += sum(
            len(row["candidate_events"]) for row in candidates.values()
        )
        material_by_id = _unique_by_id(
            materials,
            field="material_id",
            code="source_window.material_id_duplicate",
        )
        projected_by_id = _unique_by_id(
            projected,
            field="material_id",
            code="source_window.projected_material_id_duplicate",
        )
        paper_windows = [
            _build_window(
                section_id=str(section["section_id"]),
                paper_id=paper_id,
                citation_key=citation_by_paper[paper_id],
                candidate=candidate,
                material=material_by_id.get(material_id),
                projected=projected_by_id.get(material_id),
                document=document,
            )
            for material_id, candidate in candidates.items()
        ]
        paper_windows.sort(
            key=lambda row: (
                int(row["source_spans"][0]["start_line"]),
                int(row["source_spans"][0]["start_char"]),
                row["window_id"],
            )
        )
        windows.extend(paper_windows)
        roles = {
            reason.split(":", 1)[0]
            for window in paper_windows
            for reason in window["selection_reasons"]
        }
        paper_context_roles[paper_id] = roles
        selected_window_materials = {
            material_id
            for window in paper_windows
            for material_id in window["material_ids"]
        }
        projections.append(
            _project_understanding(
                understanding,
                selected_contribution_ids={
                    str(row["contribution_id"])
                    for row in selected_contributions
                },
                selected_material_ids=selected_window_materials,
            )
        )
        for contribution in selected_contributions:
            contribution_id = str(contribution["contribution_id"])
            _append_unique(covered_contributions, [contribution_id])
            _append_unique(
                required_materials,
                [
                    str(value)
                    for value in contribution.get("material_ids", [])
                ],
            )
        reference = copy.deepcopy(
            framework_snapshot.reference_by_paper[paper_id]
        )
        expected_key = _citation_key(reference)
        if citation_by_paper[paper_id] != expected_key:
            raise SourceWindowSelectionError(
                "source_window.citation_key_mismatch",
                f"{paper_id} Framework引用Key与题录不一致。",
            )
        citation_metadata.append(
            _project_citation_metadata(
                paper_id,
                citation_key=expected_key,
                reference=reference,
                bibliography_status=str(section["bibliography_status"]),
            )
        )
        if material_tier in {"tier_2", "tier_3"}:
            expanded_markdown.append(
                {
                    "paper_id": paper_id,
                    "citation_key": expected_key,
                    "markdown": document,
                }
            )
        artifacts.append(
            {
                "paper_index": paper_index,
                "paper_id": paper_id,
                "document": raw_document,
                "materials": raw_materials,
                "projected_cards": raw_projected,
                "evidence_units": raw_evidence,
                "understanding": raw_understanding,
                "manifest": raw_manifest,
                "bibliography": (
                    json.dumps(
                        reference,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                ).encode("utf-8"),
            }
        )

    uncovered_contributions = [
        value
        for value in section_contribution_ids
        if value not in set(covered_contributions)
    ]
    covered_materials = [
        material_id
        for window in windows
        for material_id in window["material_ids"]
        if material_id in set(required_materials)
    ]
    covered_materials = list(dict.fromkeys(covered_materials))
    uncovered_materials = [
        value
        for value in required_materials
        if value not in set(covered_materials)
    ]
    window_type_counts = Counter(
        str(window["window_type"]) for window in windows
    )
    coverage = {
        "required_contribution_ids": section_contribution_ids,
        "covered_contribution_ids": [
            value
            for value in section_contribution_ids
            if value in set(covered_contributions)
        ],
        "uncovered_contribution_ids": uncovered_contributions,
        "required_material_ids": required_materials,
        "covered_material_ids": covered_materials,
        "uncovered_material_ids": uncovered_materials,
        "window_count": len(windows),
        "paper_count": len(section_paper_ids),
        "window_type_counts": {
            key: window_type_counts.get(key, 0)
            for key in WINDOW_TYPE_PRIORITY
        },
        "papers_without_method_context": [
            paper_id
            for paper_id in section_paper_ids
            if (
                "method_context"
                in paper_expected_context_roles[paper_id]
                and "method_context" not in paper_context_roles[paper_id]
            )
        ],
        "papers_without_limitation_context": [
            paper_id
            for paper_id in section_paper_ids
            if (
                "limitation_context"
                in paper_expected_context_roles[paper_id]
                and "limitation_context"
                not in paper_context_roles[paper_id]
            )
        ],
        "deduplicated_candidate_count": candidate_count - len(windows),
    }
    selection_without_id = {
        "schema_version": SOURCE_WINDOW_SELECTION_SCHEMA_VERSION,
        "source": {
            "framework_run_id": framework_source.run_id,
            "framework_id": framework_source.framework["framework_id"],
            "framework_manifest_sha256": framework_source.manifest_sha256,
            "framework_output_sha256": framework_source.output_sha256,
            "landscape_run_id": framework_snapshot.landscape_run_id,
            "landscape_id": framework_snapshot.landscape["landscape_id"],
        },
        "section": {
            key: copy.deepcopy(section[key])
            for key in (
                "section_index",
                "section_id",
                "title",
                "section_type",
                "question",
                "purpose",
                "dimension_indexes",
                "required_comparisons",
                "controversy_ids",
                "corpus_gap_indexes",
                "corpus_limitations",
                "paper_ids",
                "contribution_ids",
                "citation_keys",
                "bibliography_status",
            )
        },
        "material_tier": material_tier,
        "understanding_projections": projections,
        "selected_source_windows": windows,
        "expanded_markdown_sources": expanded_markdown,
        "citation_metadata": citation_metadata,
        "coverage": coverage,
    }
    selection = {
        **selection_without_id,
        "selection_id": stable_id(
            "source_selection",
            selection_without_id,
        ),
    }
    try:
        validated = validate_source_window_selection(selection)
    except SourceWindowContractError as exc:
        raise SourceWindowSelectionError(
            exc.code,
            exc.detail,
            path=exc.path,
        ) from exc
    return SourceWindowBuildSnapshot(
        framework_source=framework_source,
        landscape_snapshot=landscape_snapshot,
        section=copy.deepcopy(section),
        selection=validated,
        paper_artifacts=tuple(artifacts),
        input_sha256=sha256_json(selection_without_id),
    )


def _select_material_candidates(
    paper_id: str,
    *,
    understanding: dict[str, Any],
    evidence_units: list[dict[str, Any]],
    selected_contributions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    evidence_by_id = _unique_by_id(
        evidence_units,
        field="evidence_unit_id",
        code="source_window.evidence_unit_id_duplicate",
    )
    direct_material_ids: set[str] = set()
    selected_contribution_ids = [
        _required_text(
            row.get("contribution_id"),
            "contribution_id",
        )
        for row in selected_contributions
    ]
    if not selected_contribution_ids:
        raise SourceWindowSelectionError(
            "source_window.paper_contribution_missing",
            f"{paper_id}在本章节中没有入选contribution，无法绑定Source Window。",
        )
    for contribution in selected_contributions:
        contribution_id = _required_text(
            contribution.get("contribution_id"),
            "contribution_id",
        )
        result_type = _optional_text(contribution.get("result_type"))
        validation_level = _optional_text(
            contribution.get("validation_level")
        )
        window_type = _window_type_for_result(result_type)
        material_ids = _text_list(
            contribution.get("material_ids"),
            f"{contribution_id}.material_ids",
        )
        if not material_ids:
            raise SourceWindowSelectionError(
                "source_window.contribution_materials_missing",
                f"{paper_id} contribution缺少material_ids："
                f"{contribution_id}。",
            )
        for material_id in material_ids:
            direct_material_ids.add(material_id)
            _add_candidate_event(
                candidates,
                material_id,
                window_type=window_type,
                reason="framework_contribution",
                contribution_id=contribution_id,
                evidence_unit_id=None,
                result_type=result_type,
                validation_level=validation_level,
                contains_limitation=False,
            )
        for evidence_unit_id in _text_list(
            contribution.get("evidence_unit_ids", []),
            f"{contribution_id}.evidence_unit_ids",
        ):
            evidence = evidence_by_id.get(evidence_unit_id)
            if evidence is None:
                raise SourceWindowSelectionError(
                    "source_window.contribution_evidence_missing",
                    f"{paper_id} contribution引用未知Evidence："
                    f"{evidence_unit_id}。",
                )
            for citation in evidence.get("citations", []):
                if not isinstance(citation, dict):
                    raise SourceWindowSelectionError(
                        "source_window.evidence_citation_invalid",
                        f"{paper_id} Evidence citation不是对象。",
                    )
                material_id = _required_text(
                    citation.get("material_id"),
                    "evidence.citation.material_id",
                )
                _add_candidate_event(
                    candidates,
                    material_id,
                    window_type=window_type,
                    reason="contribution_evidence",
                    contribution_id=contribution_id,
                    evidence_unit_id=evidence_unit_id,
                    result_type=result_type,
                    validation_level=validation_level,
                    contains_limitation=False,
                )

    for field, window_type in (
        ("research_questions", "problem_context"),
        ("methods", "method_context"),
        ("limitations", "limitation_context"),
    ):
        rows = understanding.get(field, [])
        if not isinstance(rows, list):
            raise SourceWindowSelectionError(
                "source_window.understanding_field_invalid",
                f"{paper_id} Understanding.{field}不是数组。",
            )
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise SourceWindowSelectionError(
                    "source_window.understanding_row_invalid",
                    f"{paper_id} Understanding.{field}[{row_index}]不是对象。",
                )
            row_materials = _text_list(
                row.get("material_ids", []),
                f"{field}[{row_index}].material_ids",
            )
            is_required_context = field in {"methods", "limitations"}
            if (
                not is_required_context
                and not direct_material_ids.intersection(row_materials)
            ):
                continue
            for material_id in row_materials:
                for contribution_id in selected_contribution_ids:
                    _add_candidate_event(
                        candidates,
                        material_id,
                        window_type=window_type,
                        reason=f"{window_type}:{field}",
                        contribution_id=contribution_id,
                        evidence_unit_id=None,
                        result_type=None,
                        validation_level=None,
                        contains_limitation=(
                            window_type == "limitation_context"
                        ),
                    )
    return candidates


def _expected_context_roles(
    understanding: dict[str, Any],
    *,
    paper_id: str,
) -> set[str]:
    expected: set[str] = set()
    for field, role in (
        ("methods", "method_context"),
        ("limitations", "limitation_context"),
    ):
        rows = understanding.get(field, [])
        if not isinstance(rows, list):
            raise SourceWindowSelectionError(
                "source_window.understanding_field_invalid",
                f"{paper_id} Understanding.{field}不是数组。",
            )
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise SourceWindowSelectionError(
                    "source_window.understanding_row_invalid",
                    f"{paper_id} Understanding.{field}[{row_index}]不是对象。",
                )
            if _text_list(
                row.get("material_ids", []),
                f"{field}[{row_index}].material_ids",
            ):
                expected.add(role)
                break
    return expected


def _add_candidate_event(
    candidates: dict[str, dict[str, Any]],
    material_id: str,
    *,
    window_type: str,
    reason: str,
    contribution_id: str | None,
    evidence_unit_id: str | None,
    result_type: str | None,
    validation_level: str | None,
    contains_limitation: bool,
) -> None:
    candidate = candidates.setdefault(
        material_id,
        {
            "candidate_events": [],
            "window_types": [],
            "selection_reasons": [],
            "contribution_ids": [],
            "evidence_unit_ids": [],
            "result_types": [],
            "validation_levels": [],
            "contains_limitation": False,
        },
    )
    candidate["candidate_events"].append(
        {
            "window_type": window_type,
            "reason": reason,
            "contribution_id": contribution_id,
            "evidence_unit_id": evidence_unit_id,
        }
    )
    _append_unique(candidate["window_types"], [window_type])
    _append_unique(candidate["selection_reasons"], [reason])
    if contribution_id is not None:
        _append_unique(candidate["contribution_ids"], [contribution_id])
    if evidence_unit_id is not None:
        _append_unique(candidate["evidence_unit_ids"], [evidence_unit_id])
    if result_type is not None:
        _append_unique(candidate["result_types"], [result_type])
    if validation_level is not None:
        _append_unique(
            candidate["validation_levels"],
            [validation_level],
        )
    candidate["contains_limitation"] = (
        candidate["contains_limitation"] or contains_limitation
    )


def _build_window(
    *,
    section_id: str,
    paper_id: str,
    citation_key: str,
    candidate: dict[str, Any],
    material: dict[str, Any] | None,
    projected: dict[str, Any] | None,
    document: str,
) -> dict[str, Any]:
    if material is None or projected is None:
        raise SourceWindowSelectionError(
            "source_window.material_missing",
            f"{paper_id}候选material在冻结Card中不存在。",
        )
    material_id = _required_text(material.get("material_id"), "material_id")
    if projected.get("material_id") != material_id:
        raise SourceWindowSelectionError(
            "source_window.material_projection_mismatch",
            f"{paper_id} Card与投影material_id不一致。",
        )
    text = _required_text(projected.get("extract"), "projected.extract")
    spans = _normalized_spans(material)
    _validate_spans_against_document(
        spans,
        document=document,
        paper_id=paper_id,
        material_id=material_id,
    )
    primary_type = min(
        candidate["window_types"],
        key=lambda value: WINDOW_TYPE_PRIORITY[value],
    )
    material_ids = [material_id]
    identity = {
        "section_id": section_id,
        "paper_id": paper_id,
        "text": text,
        "source_spans": spans,
        "material_ids": material_ids,
    }
    return {
        "schema_version": SOURCE_WINDOW_SCHEMA_VERSION,
        "window_id": stable_id("window", identity),
        "section_id": section_id,
        "paper_id": paper_id,
        "citation_key": citation_key,
        "window_type": primary_type,
        "text": text,
        "heading_path": [
            str(value) for value in projected.get("heading_path", [])
        ],
        "source_spans": spans,
        "material_ids": material_ids,
        "contribution_ids": candidate["contribution_ids"],
        "evidence_unit_ids": candidate["evidence_unit_ids"],
        "result_types": candidate["result_types"],
        "validation_levels": candidate["validation_levels"],
        "contains_limitation": bool(candidate["contains_limitation"]),
        "parse_flags": [
            str(value) for value in material.get("confidence_flags", [])
        ],
        "quality_flags": [
            str(value) for value in material.get("quality_flags", [])
        ],
        "selection_reasons": candidate["selection_reasons"],
        "source_fingerprints": (
            [str(material["source_fingerprint"])]
            if isinstance(material.get("source_fingerprint"), str)
            and material["source_fingerprint"].strip()
            else []
        ),
    }


def _project_understanding(
    understanding: dict[str, Any],
    *,
    selected_contribution_ids: set[str],
    selected_material_ids: set[str],
) -> dict[str, Any]:
    contributions = [
        row
        for row in understanding.get("contributions", [])
        if str(row.get("contribution_id")) in selected_contribution_ids
    ]
    contribution_indexes = {
        index
        for index, row in enumerate(
            understanding.get("contributions", []),
            start=1,
        )
        if str(row.get("contribution_id")) in selected_contribution_ids
    }
    return {
        "paper_id": understanding["paper_id"],
        "paper_title": understanding["paper_title"],
        "paper_relevance": understanding.get("paper_relevance"),
        "research_questions": [
            _pick(
                row,
                ("question", "problem_category"),
            )
            for row in understanding.get("research_questions", [])
            if selected_material_ids.intersection(
                str(value) for value in row.get("material_ids", [])
            )
        ],
        "study_context": _pick(
            understanding.get("study_context", {}),
            (
                "study_object",
                "data_sources",
                "time_scope",
                "geographic_scope",
            ),
        ),
        "methods": [
            _pick(
                row,
                ("method_category", "method_name", "description"),
            )
            for row in understanding.get("methods", [])
            if selected_material_ids.intersection(
                str(value) for value in row.get("material_ids", [])
            )
        ],
        "contributions": [
            _pick(
                row,
                (
                    "contribution_id",
                    "statement",
                    "result_type",
                    "validation_level",
                    "evidence_strength",
                    "strength_rationale",
                ),
            )
            for row in contributions
        ],
        "limitations": [
            _pick(row, ("statement", "basis"))
            for row in understanding.get("limitations", [])
        ],
        "review_roles": [
            copy.deepcopy(row)
            for row in understanding.get("review_roles", [])
            if contribution_indexes.intersection(
                int(value)
                for value in row.get("contribution_indexes", [])
                if type(value) is int
            )
        ],
    }


def _project_citation_metadata(
    paper_id: str,
    *,
    citation_key: str,
    reference: dict[str, Any],
    bibliography_status: str,
) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "citation_key": citation_key,
        "paper_title": reference.get("title") or paper_id,
        "authors": copy.deepcopy(reference.get("authors", [])),
        "year": reference.get("year"),
        "reference_type": reference.get("entry_type"),
        "journal": reference.get("journal"),
        "institution": reference.get("institution"),
        "degree": reference.get("degree"),
        "doi": reference.get("doi"),
        "bibliography_status": reference.get(
            "status",
            bibliography_status,
        ),
    }


def _window_type_for_result(result_type: str | None) -> str:
    if result_type in {
        "engineering_implementation",
        "field_implementation",
        "operational_practice",
    }:
        return "implementation_status"
    if result_type in {
        "recommendation",
        "author_recommendation",
        "conceptual_proposal",
    }:
        return "author_conclusion"
    return "result_context"


def _normalized_spans(material: dict[str, Any]) -> list[dict[str, Any]]:
    raw = material.get("source_spans")
    if not isinstance(raw, list) or not raw:
        raw = [material.get("source_span")]
    spans: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise SourceWindowSelectionError(
                "source_window.source_span_invalid",
                f"Card source_spans[{index}]不是对象。",
            )
        try:
            span = {
                "path": _required_text(value.get("path"), "span.path"),
                "start_line": int(value["start_line"]),
                "end_line": int(value["end_line"]),
                "start_char": int(value.get("start_char", 0)),
                "end_char": int(value.get("end_char", 0)),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceWindowSelectionError(
                "source_window.source_span_invalid",
                "Card来源坐标字段无效。",
            ) from exc
        spans.append(span)
    return spans


def _validate_spans_against_document(
    spans: list[dict[str, Any]],
    *,
    document: str,
    paper_id: str,
    material_id: str,
) -> None:
    line_count = len(document.splitlines())
    for span in spans:
        if (
            span["path"].replace("\\", "/") != "normalized/document.md"
            or span["start_line"] < 1
            or span["end_line"] < span["start_line"]
            or span["end_line"] > line_count
            or span["start_char"] < 0
            or span["end_char"] < 0
        ):
            raise SourceWindowSelectionError(
                "source_window.source_span_out_of_bounds",
                f"{paper_id} Card来源坐标无法映射冻结Markdown："
                f"{material_id}。",
            )


def _select_section(
    framework: dict[str, Any],
    *,
    section_id: str | None,
    section_index: int | None,
) -> dict[str, Any]:
    if (section_id is None) == (section_index is None):
        raise SourceWindowSelectionError(
            "source_window.section_selector_invalid",
            "section_id与section_index必须且只能提供一个。",
        )
    matches = [
        row
        for row in framework.get("sections", [])
        if (
            section_id is not None
            and str(row.get("section_id")) == section_id
        )
        or (
            section_index is not None
            and row.get("section_index") == section_index
        )
    ]
    if len(matches) != 1:
        raise SourceWindowSelectionError(
            "source_window.section_not_found",
            "Framework中无法唯一定位目标章节。",
        )
    return copy.deepcopy(matches[0])


def _write_input_artifacts(
    run_dir: Path,
    snapshot: SourceWindowBuildSnapshot,
) -> None:
    ledger: list[dict[str, Any]] = []

    def write(relative_path: str, data: bytes) -> None:
        path = run_dir / "input" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        ledger.append(
            {
                "path": relative_path.replace("\\", "/"),
                "sha256": _sha256_bytes(data),
                "bytes": len(data),
            }
        )

    write(
        "framework.json",
        _json_bytes(snapshot.framework_source.framework),
    )
    write(
        "research_landscape.json",
        _json_bytes(snapshot.framework_source.snapshot.landscape),
    )
    write(
        "source_window_candidate.json",
        _json_bytes(snapshot.selection),
    )
    for artifact in snapshot.paper_artifacts:
        prefix = f"papers/paper-{artifact['paper_index']:03d}"
        write(f"{prefix}/document.md", artifact["document"])
        write(f"{prefix}/materials.jsonl", artifact["materials"])
        write(
            f"{prefix}/projected_cards.jsonl",
            artifact["projected_cards"],
        )
        write(f"{prefix}/evidence_units.jsonl", artifact["evidence_units"])
        write(
            f"{prefix}/paper_understanding.json",
            artifact["understanding"],
        )
        write(
            f"{prefix}/understanding_manifest.json",
            artifact["manifest"],
        )
        write(f"{prefix}/bibliography.json", artifact["bibliography"])
    _write_jsonl(run_dir / "input" / "file_hashes.jsonl", ledger)


def _build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: str,
    framework_run_id: str,
    requested_section_id: str | None,
    requested_section_index: int | None,
    material_tier: str,
    snapshot: SourceWindowBuildSnapshot | None,
    failure: dict[str, Any] | None,
) -> dict[str, Any]:
    section = snapshot.section if snapshot is not None else None
    selection = snapshot.selection if snapshot is not None else None
    return {
        "schema_version": SOURCE_WINDOW_RUN_SCHEMA_VERSION,
        "pipeline_generation": "B2.phase1",
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "framework_run_id": framework_run_id,
        "framework_id": (
            snapshot.framework_source.framework["framework_id"]
            if snapshot is not None
            else None
        ),
        "section_id": (
            section["section_id"]
            if section is not None
            else requested_section_id
        ),
        "section_index": (
            section["section_index"]
            if section is not None
            else requested_section_index
        ),
        "material_tier": material_tier,
        "selection_id": (
            selection["selection_id"] if selection is not None else None
        ),
        "input_sha256": (
            snapshot.input_sha256 if snapshot is not None else None
        ),
        "output_sha256": (
            _sha256_bytes(_json_bytes(selection))
            if status == "completed" and selection is not None
            else None
        ),
        "paper_count": (
            selection["coverage"]["paper_count"]
            if selection is not None
            else 0
        ),
        "window_count": (
            selection["coverage"]["window_count"]
            if selection is not None
            else 0
        ),
        "coverage": (
            copy.deepcopy(selection["coverage"])
            if selection is not None
            else None
        ),
        "failure": failure,
        "artifacts": {
            "selection": (
                "output/source_window_selection.json"
                if status == "completed"
                else None
            ),
            "coverage": (
                "audit/coverage.json"
                if selection is not None
                else None
            ),
            "file_hashes": (
                "input/file_hashes.jsonl"
                if snapshot is not None
                else None
            ),
            "report": (
                "review/source_window_report.md"
                if status == "completed"
                else None
            ),
            "failures": (
                "audit/failures.jsonl" if failure is not None else None
            ),
        },
    }


def _result(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "output_path": (
            str(run_dir / "output" / "source_window_selection.json")
            if manifest["status"] == "completed"
            else None
        ),
        "report_path": (
            str(run_dir / "review" / "source_window_report.md")
            if manifest["status"] == "completed"
            else None
        ),
        "selection_id": manifest.get("selection_id"),
        "window_count": manifest.get("window_count", 0),
        "coverage": manifest.get("coverage"),
        "failure": manifest.get("failure"),
    }


def _verify_file_hash_ledger(run_dir: Path) -> None:
    rows = _read_jsonl(
        run_dir / "input" / "file_hashes.jsonl",
        "Source Window输入哈希账本",
    )
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SourceWindowSelectionError(
                "source_window.file_hash_row_invalid",
                f"输入哈希账本第{index + 1}行不是对象。",
            )
        relative = _required_text(row.get("path"), "file_hash.path")
        path = run_dir / "input" / Path(relative)
        raw = _read_bytes(path, f"冻结输入{relative}")
        if (
            row.get("bytes") != len(raw)
            or row.get("sha256") != _sha256_bytes(raw)
        ):
            raise SourceWindowSelectionError(
                "source_window.file_hash_mismatch",
                f"冻结输入哈希不一致：{relative}。",
            )


def _failure_stage(run_dir: Path) -> str:
    if (run_dir / "input" / "file_hashes.jsonl").is_file():
        return "output_validation"
    return "input_validation"


def _unique_by_id(
    rows: Iterable[dict[str, Any]],
    *,
    field: str,
    code: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = _required_text(row.get(field), field)
        if value in result:
            raise SourceWindowSelectionError(
                code,
                f"{field}不得重复：{value}。",
            )
        result[value] = row
    return result


def _citation_key(reference: dict[str, Any]) -> str:
    value = reference.get("citation_key")
    if isinstance(value, str) and value.strip():
        return value.strip()
    reference_id = reference.get("reference_id")
    if isinstance(reference_id, str) and reference_id.strip():
        return "ref_" + reference_id.strip().removeprefix("ref-")
    raise SourceWindowSelectionError(
        "source_window.citation_key_missing",
        "题录缺少citation_key或可转换的reference_id。",
    )


def _pick(row: object, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {
        field: copy.deepcopy(row[field])
        for field in fields
        if field in row
    }


def _append_unique(target: list[str], values: Iterable[str]) -> None:
    known = set(target)
    for value in values:
        if value not in known:
            target.append(value)
            known.add(value)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceWindowSelectionError(
            "source_window.text_required",
            f"{field}必须是非空字符串。",
        )
    return value.strip()


def _optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _text_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SourceWindowSelectionError(
            "source_window.text_list_invalid",
            f"{field}必须是字符串数组。",
        )
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_required_text(item, f"{field}[{index}]"))
    if len(result) != len(set(result)):
        raise SourceWindowSelectionError(
            "source_window.text_list_duplicate",
            f"{field}不得包含重复值。",
        )
    return result


def _safe_segment(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise SourceWindowSelectionError(
            "source_window.run_id_invalid",
            f"{field}不是安全目录名。",
        )


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SourceWindowSelectionError(
            "source_window.input_read_failed",
            f"无法读取{label}：{path}",
        ) from exc


def _read_json(path: Path, label: str) -> dict[str, Any]:
    return _json_object(_read_bytes(path, label), label)


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceWindowSelectionError(
            "source_window.json_invalid",
            f"{label}不是有效JSON对象。",
        ) from exc
    if not isinstance(value, dict):
        raise SourceWindowSelectionError(
            "source_window.json_object_required",
            f"{label}必须是JSON对象。",
        )
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    return _read_jsonl_bytes(_read_bytes(path, label), label)


def _read_jsonl_bytes(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceWindowSelectionError(
            "source_window.jsonl_encoding_invalid",
            f"{label}不是UTF-8。",
        ) from exc
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SourceWindowSelectionError(
                "source_window.jsonl_invalid",
                f"{label}第{index + 1}行不是有效JSON。",
            ) from exc
        if not isinstance(row, dict):
            raise SourceWindowSelectionError(
                "source_window.jsonl_row_invalid",
                f"{label}第{index + 1}行不是对象。",
            )
        rows.append(row)
    return rows


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(payload))


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows
        ).encode("utf-8")
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.encode("utf-8"))


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _now() -> str:
    return datetime.now(UTC).isoformat()
