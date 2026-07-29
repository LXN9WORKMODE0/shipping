from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REFERENCE_CATALOG_RUN_SCHEMA_VERSION = "reference_catalog.run.v1"
REFERENCE_CATALOG_SCHEMA_VERSION = "reference_catalog.v1"
REFERENCE_OVERRIDE_SCHEMA_VERSION = "reference_catalog.overrides.v1"

REFERENCE_FIELDS = (
    "entry_type",
    "title",
    "authors",
    "year",
    "journal",
    "volume",
    "issue",
    "pages",
    "doi",
    "article_number",
    "institution",
    "degree",
)

ARTICLE_REQUIRED_FIELDS = ("title", "authors", "year", "journal", "pages")
THESIS_REQUIRED_FIELDS = (
    "title",
    "authors",
    "year",
    "institution",
    "degree",
)

_TAG_RE = re.compile(r"<[^>]*>")
_DOI_RE = re.compile(r"(?i)10\.\d{4,9}/[0-9a-z._;()/:+-]+")
_ARTICLE_NUMBER_RE = re.compile(
    r"(?P<issn>\d{4}-\d{3}[\dXx])"
    r"\((?P<year>20\d{2})\)"
    r"(?P<issue>[0-9A-Za-z增]+)-"
    r"(?P<start>\d{4})-(?P<count>\d{2})"
)
_CHINESE_NAME_RE = re.compile(r"^[\u3400-\u9fff]{2,4}$")


class ReferenceCatalogError(ValueError):
    pass


class ReferenceCatalogRunner:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(
        self,
        *,
        project_path: str | Path,
        synthesis_run_dir: str | Path,
        overrides_path: str | Path | None = None,
        review_draft_path: str | Path | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _validate_run_id(resolved_run_id)
        run_dir = self.workspace / "_reference_catalogs" / "runs" / resolved_run_id
        if run_dir.exists():
            raise ReferenceCatalogError(
                f"参考文献 run_id 已存在，不能覆盖不可变运行：{resolved_run_id}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()

        project_file = Path(project_path)
        synthesis_dir = Path(synthesis_run_dir)
        source_runs_file = synthesis_dir / "input" / "source_runs.jsonl"
        project, project_raw = _read_json(project_file, "项目文件")
        source_runs, source_runs_raw = _read_jsonl(
            source_runs_file,
            "跨论文综合输入快照",
        )
        overrides, overrides_raw = _load_overrides(overrides_path)
        review_draft_raw = b""
        review_draft: str | None = None
        if review_draft_path is not None:
            try:
                review_draft_raw = Path(review_draft_path).read_bytes()
                review_draft = review_draft_raw.decode("utf-8-sig")
            except (OSError, UnicodeError) as exc:
                raise ReferenceCatalogError(
                    f"无法读取综述草稿：{review_draft_path}"
                ) from exc

        papers = project.get("papers")
        if not isinstance(papers, list):
            raise ReferenceCatalogError("项目文件 papers 必须是数组。")
        paper_by_id: dict[str, dict[str, Any]] = {}
        for index, paper in enumerate(papers):
            if not isinstance(paper, dict) or not str(paper.get("paper_id", "")):
                raise ReferenceCatalogError(
                    f"项目文件 papers[{index}] 缺少 paper_id。"
                )
            paper_id = str(paper["paper_id"])
            if paper_id in paper_by_id:
                raise ReferenceCatalogError(f"项目中存在重复 paper_id：{paper_id}")
            paper_by_id[paper_id] = paper

        source_ids: set[str] = set()
        selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for index, source in enumerate(source_runs):
            paper_id = str(source.get("paper_id", ""))
            if not paper_id:
                raise ReferenceCatalogError(
                    f"source_runs.jsonl 第 {index + 1} 条缺少 paper_id。"
                )
            if paper_id in source_ids:
                raise ReferenceCatalogError(
                    f"综合输入快照存在重复 paper_id：{paper_id}"
                )
            source_ids.add(paper_id)
            if paper_id not in paper_by_id:
                raise ReferenceCatalogError(
                    f"综合输入论文不在项目中：{paper_id}"
                )
            selected.append((source, paper_by_id[paper_id]))

        override_by_id = _validate_overrides(overrides, source_ids)
        local_candidates: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = []
        for number, (source, paper) in enumerate(selected, start=1):
            workspace_paper_id = str(paper.get("workspace_paper_id", ""))
            if not workspace_paper_id:
                raise ReferenceCatalogError(
                    f"项目论文缺少 workspace_paper_id：{source['paper_id']}"
                )
            markdown_path = (
                self.workspace / workspace_paper_id / "normalized" / "document.md"
            )
            try:
                markdown = markdown_path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                raise ReferenceCatalogError(
                    f"无法读取论文 Markdown：{markdown_path}"
                ) from exc

            candidate = extract_local_reference(
                markdown,
                paper_id=str(source["paper_id"]),
                paper_title=str(paper.get("paper_title") or source["paper_id"]),
                workspace_paper_id=workspace_paper_id,
                markdown_path=markdown_path,
                source_run_id=str(source.get("run_id", "")),
            )
            local_candidates.append(candidate)
            reference = apply_reference_override(
                candidate,
                override_by_id.get(str(source["paper_id"])),
            )
            reference["number"] = number
            reference["reference_id"] = _reference_id(str(source["paper_id"]))
            _finalize_reference(reference)
            references.append(reference)

        catalog = {
            "schema_version": REFERENCE_CATALOG_SCHEMA_VERSION,
            "run_id": resolved_run_id,
            "project_id": str(project.get("project_id", "")),
            "project_name": str(project.get("name", "")),
            "topic": str(project.get("topic", "")),
            "synthesis_run_id": synthesis_dir.name,
            "reference_count": len(references),
            "complete_count": sum(
                row["status"] == "complete" for row in references
            ),
            "partial_count": sum(
                row["status"] == "partial" for row in references
            ),
            "references": references,
        }
        incomplete = [
            {
                "reference_id": row["reference_id"],
                "number": row["number"],
                "paper_id": row["paper_id"],
                "entry_type": row["entry_type"],
                "missing_fields": row["missing_fields"],
                "warnings": row["warnings"],
            }
            for row in references
            if row["status"] != "complete"
        ]

        _write_json(run_dir / "input" / "project.json", project)
        _write_jsonl(run_dir / "input" / "source_runs.jsonl", source_runs)
        _write_jsonl(
            run_dir / "input" / "local_candidates.jsonl",
            local_candidates,
        )
        _write_json(run_dir / "input" / "overrides.json", overrides)
        if review_draft is not None:
            _write_text(
                run_dir / "input" / "source_review_draft.md",
                review_draft,
            )
        _write_json(run_dir / "output" / "references.json", catalog)
        _write_text(
            run_dir / "output" / "references.bib",
            render_bibtex(references),
        )
        _write_text(
            run_dir / "review" / "references.md",
            render_reference_review(catalog),
        )
        _write_jsonl(
            run_dir / "audit" / "incomplete_references.jsonl",
            incomplete,
        )
        referenced_review_path: Path | None = None
        cited_reference_count = 0
        if review_draft is not None:
            referenced_review, cited_reference_count = (
                attach_reference_catalog_to_markdown(
                    review_draft,
                    catalog,
                    source_runs,
                )
            )
            referenced_review_path = (
                run_dir / "review" / "review_draft_with_references.md"
            )
            _write_text(referenced_review_path, referenced_review)
        manifest = {
            "schema_version": REFERENCE_CATALOG_RUN_SCHEMA_VERSION,
            "run_id": resolved_run_id,
            "status": "completed" if not incomplete else "completed_with_gaps",
            "started_at": started_at,
            "finished_at": _now(),
            "project_id": str(project.get("project_id", "")),
            "synthesis_run_id": synthesis_dir.name,
            "input_sha256": _sha256_bytes(
                project_raw
                + source_runs_raw
                + overrides_raw
                + review_draft_raw
            ),
            "reference_count": len(references),
            "complete_count": catalog["complete_count"],
            "partial_count": catalog["partial_count"],
            "source_review_draft": (
                str(review_draft_path) if review_draft_path is not None else None
            ),
            "cited_reference_count": cited_reference_count,
            "outputs": {
                "catalog": "output/references.json",
                "bibtex": "output/references.bib",
                "review": "review/references.md",
                "incomplete_audit": "audit/incomplete_references.jsonl",
                **(
                    {
                        "referenced_review": (
                            "review/review_draft_with_references.md"
                        )
                    }
                    if referenced_review_path is not None
                    else {}
                ),
            },
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved_run_id,
            "status": manifest["status"],
            "reference_count": len(references),
            "complete_count": catalog["complete_count"],
            "partial_count": catalog["partial_count"],
            "run_dir": str(run_dir),
            "catalog_path": str(run_dir / "output" / "references.json"),
            "bibtex_path": str(run_dir / "output" / "references.bib"),
            "review_path": str(run_dir / "review" / "references.md"),
            "referenced_review_path": (
                str(referenced_review_path)
                if referenced_review_path is not None
                else None
            ),
            "audit_path": str(
                run_dir / "audit" / "incomplete_references.jsonl"
            ),
        }


def attach_reference_catalog_to_markdown(
    markdown: str,
    catalog: dict[str, Any],
    source_runs: list[dict[str, Any]],
) -> tuple[str, int]:
    if re.search(r"^##\s+参考文献\s*$", markdown, flags=re.MULTILINE):
        raise ReferenceCatalogError("综述草稿已经包含参考文献章节，拒绝重复装配。")
    reference_by_paper = {
        str(row["paper_id"]): row for row in catalog["references"]
    }
    number_by_title: dict[str, int] = {}
    for source in source_runs:
        paper_id = str(source.get("paper_id", ""))
        reference = reference_by_paper.get(paper_id)
        if reference is None:
            raise ReferenceCatalogError(
                f"综述来源没有对应参考文献：{paper_id}"
            )
        number = int(reference["number"])
        for title in (
            str(source.get("paper_title", "")).strip(),
            paper_id,
            str(reference.get("title", "")).strip(),
        ):
            if title:
                known = number_by_title.get(title)
                if known is not None and known != number:
                    raise ReferenceCatalogError(
                        f"同一题名映射到多个参考文献编号：{title}"
                    )
                number_by_title[title] = number

    source_matches = list(re.finditer(r"（证据来源：([^）]+)）", markdown))
    cited_catalog_numbers: set[int] = set()
    for match in source_matches:
        for title in (row.strip() for row in match.group(1).split("；")):
            number = number_by_title.get(title)
            if number is None:
                raise ReferenceCatalogError(
                    f"综述草稿中的证据来源无法精确映射：{title}"
                )
            cited_catalog_numbers.add(number)
    if not cited_catalog_numbers:
        raise ReferenceCatalogError("综述草稿中没有可装配的“证据来源”标记。")

    ordered_catalog_numbers = [
        int(row["number"])
        for row in catalog["references"]
        if int(row["number"]) in cited_catalog_numbers
    ]
    display_number_by_catalog_number = {
        catalog_number: index
        for index, catalog_number in enumerate(ordered_catalog_numbers, start=1)
    }

    def replace_source(match: re.Match[str]) -> str:
        titles = [row.strip() for row in match.group(1).split("；")]
        rendered: list[str] = []
        for title in titles:
            catalog_number = number_by_title[title]
            display_number = display_number_by_catalog_number[catalog_number]
            rendered.append(f"{title}[{display_number}]")
        return "（证据来源：" + "；".join(rendered) + "）"

    rendered_markdown = re.sub(
        r"（证据来源：([^）]+)）",
        replace_source,
        markdown,
    )
    for title, catalog_number in sorted(
        number_by_title.items(),
        key=lambda row: len(row[0]),
        reverse=True,
    ):
        if catalog_number not in display_number_by_catalog_number:
            continue
        display_number = display_number_by_catalog_number[catalog_number]
        rendered_markdown = rendered_markdown.replace(
            f"｜{title}：",
            f"｜{title}[{display_number}]：",
        )

    references: list[dict[str, Any]] = []
    for row in catalog["references"]:
        catalog_number = int(row["number"])
        if catalog_number not in display_number_by_catalog_number:
            continue
        rendered_reference = dict(row)
        rendered_reference["number"] = display_number_by_catalog_number[
            catalog_number
        ]
        references.append(rendered_reference)
    section = ["", "## 参考文献", ""]
    section.extend(format_gbt_reference(row) for row in references)
    return rendered_markdown.rstrip() + "\n" + "\n".join(section) + "\n", len(
        references
    )


def extract_local_reference(
    markdown: str,
    *,
    paper_id: str,
    paper_title: str,
    workspace_paper_id: str,
    markdown_path: Path,
    source_run_id: str,
) -> dict[str, Any]:
    lines = markdown.splitlines()
    entry_type = _detect_entry_type(markdown)
    fields: dict[str, Any] = {
        "entry_type": entry_type,
        "title": paper_title.strip(),
        "authors": [],
        "year": None,
        "journal": None,
        "volume": None,
        "issue": None,
        "pages": None,
        "doi": None,
        "article_number": None,
        "institution": None,
        "degree": None,
    }
    provenance: dict[str, list[dict[str, Any]]] = {
        field: [] for field in REFERENCE_FIELDS
    }
    provenance["title"].append(
        {
            "source_type": "project",
            "path": "project.json",
            "json_pointer": f"/papers/{paper_id}/paper_title",
            "raw_text": paper_title,
        }
    )
    provenance["entry_type"].append(
        _markdown_provenance(
            markdown_path,
            1,
            lines[0] if lines else "",
            "document_type_marker",
        )
    )

    author_match = _extract_authors(lines, paper_title)
    if author_match:
        fields["authors"] = author_match[0]
        provenance["authors"].append(
            _markdown_provenance(
                markdown_path,
                author_match[1],
                author_match[2],
                "author_line",
            )
        )

    for line_number, raw_line in enumerate(lines[:120], start=1):
        normalized = _normalize_line(raw_line)
        compact = re.sub(r"\s+", "", normalized)
        if fields["doi"] is None:
            doi_match = _DOI_RE.search(compact)
            if doi_match:
                fields["doi"] = _clean_doi(doi_match.group(0))
                provenance["doi"].append(
                    _markdown_provenance(
                        markdown_path,
                        line_number,
                        raw_line,
                        "doi_line",
                    )
                )

        if fields["article_number"] is None:
            article_match = _ARTICLE_NUMBER_RE.search(compact)
            if article_match:
                article_number = article_match.group(0)
                fields["article_number"] = article_number
                source = _markdown_provenance(
                    markdown_path,
                    line_number,
                    raw_line,
                    "article_number_line",
                )
                provenance["article_number"].append(source)
                _apply_article_number_derivations(
                    fields,
                    provenance,
                    article_match,
                    source,
                )

        citation = _parse_local_citation(normalized)
        if citation:
            source = _markdown_provenance(
                markdown_path,
                line_number,
                raw_line,
                "embedded_citation_line",
            )
            for field, value in citation.items():
                fields[field] = value
                provenance[field].append(source)

    if entry_type in {"master_thesis", "doctoral_thesis"}:
        fields["degree"] = (
            "硕士学位论文"
            if entry_type == "master_thesis"
            else "博士学位论文"
        )
        degree_line = _find_line(lines[:80], r"(硕士|博士)学位")
        if degree_line:
            provenance["degree"].append(
                _markdown_provenance(
                    markdown_path,
                    degree_line[0],
                    degree_line[1],
                    "degree_marker",
                )
            )
        institution = _extract_institution(lines[:80])
        if institution:
            fields["institution"] = institution[0]
            provenance["institution"].append(
                _markdown_provenance(
                    markdown_path,
                    institution[1],
                    institution[2],
                    "degree_institution_line",
                )
            )
        if fields["year"] is None:
            year = _extract_thesis_year(lines[:80])
            if year:
                fields["year"] = year[0]
                provenance["year"].append(
                    _markdown_provenance(
                        markdown_path,
                        year[1],
                        year[2],
                        "degree_date_line",
                    )
                )

    return {
        "schema_version": REFERENCE_CATALOG_SCHEMA_VERSION,
        "paper_id": paper_id,
        "workspace_paper_id": workspace_paper_id,
        "source_run_id": source_run_id,
        "markdown_path": str(markdown_path),
        **fields,
        "provenance": provenance,
        "overridden_fields": [],
        "warnings": [],
    }


def apply_reference_override(
    reference: dict[str, Any],
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    result = json.loads(json.dumps(reference, ensure_ascii=False))
    if not override:
        return result
    sources = {
        str(row["source_id"]): row for row in override.get("sources", [])
    }
    for field, value in override["fields"].items():
        previous = result.get(field)
        if previous not in (None, [], "") and previous != value:
            result["overridden_fields"].append(
                {"field": field, "local_value": previous, "verified_value": value}
            )
        result[field] = value
        result["provenance"][field].extend(
            {
                "source_type": str(sources[source_id]["source_type"]),
                "source_id": source_id,
                "source_name": str(sources[source_id]["source_name"]),
                "url": sources[source_id].get("url"),
                "path": sources[source_id].get("path"),
                "accessed_at": sources[source_id].get("accessed_at"),
                "note": sources[source_id].get("note"),
                "reason": "verified_override",
            }
            for source_id in override["field_sources"][field]
        )
    return result


def render_reference_review(catalog: dict[str, Any]) -> str:
    lines = [
        "# 参考文献题录审核",
        "",
        f"- 参考文献总数：{catalog['reference_count']}",
        f"- 完整题录：{catalog['complete_count']}",
        f"- 不完整题录：{catalog['partial_count']}",
        "- 完整状态只表示具备当前文献类型所需的基本引用字段，不表示论文内容质量。",
        "",
        "## 引用列表",
        "",
    ]
    for reference in catalog["references"]:
        lines.append(format_gbt_reference(reference))
    lines.extend(["", "## 逐篇来源链", ""])
    for reference in catalog["references"]:
        status = "完整" if reference["status"] == "complete" else "待补"
        lines.extend(
            [
                f"### [{reference['number']}] {reference['title']}",
                "",
                f"- 状态：{status}",
                f"- 论文 ID：`{reference['paper_id']}`",
                f"- 类型：`{reference['entry_type']}`",
                f"- 缺失字段：{_display_list(reference['missing_fields'])}",
                f"- 核验补充字段：{_display_list(_verified_fields(reference))}",
                f"- 核验替换冲突：{_display_list([row['field'] for row in reference['overridden_fields']])}",
                "",
                "| 字段 | 当前值 | 来源 |",
                "| --- | --- | --- |",
            ]
        )
        for field in REFERENCE_FIELDS:
            value = reference.get(field)
            rendered_value = (
                "；".join(value)
                if isinstance(value, list)
                else (str(value) if value not in (None, "") else "缺失")
            )
            evidence = reference["provenance"].get(field, [])
            rendered_sources = "<br>".join(
                _render_provenance(row) for row in evidence
            ) or "无"
            lines.append(
                f"| {_field_label(field)} | {_escape_table(rendered_value)} | "
                f"{_escape_table(rendered_sources)} |"
            )
        if reference["warnings"]:
            lines.extend(
                ["", "警告：" + "；".join(reference["warnings"])]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_gbt_reference(reference: dict[str, Any]) -> str:
    number = reference["number"]
    authors = "，".join(reference.get("authors") or ["[作者缺失]"])
    title = reference.get("title") or "[题名缺失]"
    entry_type = reference.get("entry_type")
    if entry_type in {"master_thesis", "doctoral_thesis"}:
        institution = reference.get("institution") or "[学校缺失]"
        year = reference.get("year") or "[年份缺失]"
        body = f"[{number}] {authors}. {title}[D]. {institution}, {year}."
    else:
        journal = reference.get("journal") or "[期刊缺失]"
        year = reference.get("year") or "[年份缺失]"
        volume = reference.get("volume")
        issue = reference.get("issue")
        if volume and issue:
            publication = f"{year}, {volume}({issue})"
        elif volume:
            publication = f"{year}, {volume}"
        elif issue:
            publication = f"{year}({issue})"
        else:
            publication = str(year)
        pages = reference.get("pages") or "[页码缺失]"
        body = (
            f"[{number}] {authors}. {title}[J]. {journal}, "
            f"{publication}: {pages}."
        )
        if reference.get("doi"):
            body += f" DOI:{reference['doi']}."
    if reference["status"] != "complete":
        body += " [题录待补：" + "、".join(
            _field_label(row) for row in reference["missing_fields"]
        ) + "]"
    return body


def render_bibtex(references: list[dict[str, Any]]) -> str:
    entries: list[str] = []
    for reference in references:
        entry_type = reference.get("entry_type")
        bib_type = (
            "mastersthesis"
            if entry_type == "master_thesis"
            else "phdthesis"
            if entry_type == "doctoral_thesis"
            else "article"
        )
        fields: list[tuple[str, Any]] = [
            ("title", reference.get("title")),
            (
                "author",
                " and ".join(reference.get("authors") or []),
            ),
            ("year", reference.get("year")),
        ]
        if bib_type == "article":
            fields.extend(
                [
                    ("journal", reference.get("journal")),
                    ("volume", reference.get("volume")),
                    ("number", reference.get("issue")),
                    ("pages", reference.get("pages")),
                    ("doi", reference.get("doi")),
                ]
            )
        else:
            fields.append(("school", reference.get("institution")))
        if reference["status"] != "complete":
            fields.append(
                (
                    "note",
                    "题录待补：" + "、".join(
                        _field_label(row)
                        for row in reference["missing_fields"]
                    ),
                )
            )
        rendered_fields = [
            f"  {key} = {{{_bib_escape(str(value))}}}"
            for key, value in fields
            if value not in (None, "", [])
        ]
        entries.append(
            f"@{bib_type}{{{_bib_key(reference)},\n"
            + ",\n".join(rendered_fields)
            + "\n}"
        )
    return "\n\n".join(entries).rstrip() + "\n"


def _finalize_reference(reference: dict[str, Any]) -> None:
    entry_type = str(reference.get("entry_type", "unknown"))
    if entry_type in {"master_thesis", "doctoral_thesis"}:
        required = THESIS_REQUIRED_FIELDS
    else:
        required = ARTICLE_REQUIRED_FIELDS
    missing = [
        field
        for field in required
        if reference.get(field) in (None, "", [])
    ]
    if entry_type == "article" and not (
        reference.get("volume") or reference.get("issue")
    ):
        missing.append("volume_or_issue")
    reference["missing_fields"] = missing
    if (
        reference.get("institution")
        and "理大学" in str(reference["institution"])
        and "理工大学" not in str(reference["institution"])
    ):
        reference["warnings"].append("institution.ocr_suspected")
        reference["missing_fields"].append("institution_verification")
    reference["status"] = (
        "complete" if not reference["missing_fields"] else "partial"
    )


def _detect_entry_type(markdown: str) -> str:
    normalized = _normalize_line(markdown[:8000])
    if "博士学位论文" in normalized:
        return "doctoral_thesis"
    if "硕士学位论文" in normalized or "申请学位级别 硕士" in normalized:
        return "master_thesis"
    return "article"


def _extract_authors(
    lines: list[str],
    paper_title: str,
) -> tuple[list[str], int, str] | None:
    explicit_patterns = (
        r"(?:研\s*究\s*生(?:\s*姓\s*名)?|作\s*者)\s*[:：]\s*(.+)",
        r"^文\s*/\s*(.+)",
    )
    for line_number, raw_line in enumerate(lines[:50], start=1):
        line = _normalize_line(raw_line).strip()
        for pattern in explicit_patterns:
            match = re.search(pattern, line)
            if match:
                authors = _parse_author_text(match.group(1), explicit=True)
                if authors:
                    return authors, line_number, raw_line

    normalized_title = re.sub(r"\s+", "", paper_title)
    for line_number, raw_line in enumerate(lines[:25], start=1):
        line = _normalize_line(raw_line).strip()
        line = re.sub(r"^#{1,6}\s*", "", line)
        if not line or re.sub(r"\s+", "", line) == normalized_title:
            continue
        author_segment = re.split(r"[\(（]", line, maxsplit=1)[0].strip()
        if any(
            marker in author_segment
            for marker in (
                "摘要",
                "关键词",
                "单位",
                "学院",
                "大学",
                "中图",
                "文章编号",
                "DOI",
                "doi",
                "引用本文",
                "申请",
                "培养",
                "英文",
            )
        ):
            continue
        authors = _parse_author_text(author_segment, explicit=False)
        if authors:
            return authors, line_number, raw_line
    return None


def _parse_author_text(value: str, *, explicit: bool) -> list[str]:
    cleaned = _strip_markup(value)
    cleaned = re.sub(r"\$\^\{[^}]+\}\$", " ", cleaned)
    cleaned = re.sub(r"\[[0-9,，]+\]", " ", cleaned)
    cleaned = re.sub(r"\([^)]+\).*$", "", cleaned)
    cleaned = re.sub(r"（[^）]+）.*$", "", cleaned)
    cleaned = re.sub(r"\b(?:等|et\s+al\.?)$", "", cleaned).strip()
    if any(separator in cleaned for separator in ("，", ",", "、", "；", ";")):
        parts = re.split(r"[，,、；;]+", cleaned)
    else:
        tokens = [row for row in re.split(r"\s+", cleaned) if row]
        if len(tokens) == 2 and all(len(row) == 1 for row in tokens):
            parts = ["".join(tokens)]
        elif explicit or len(tokens) >= 2:
            parts = tokens
        else:
            parts = [cleaned]
    authors: list[str] = []
    for part in parts:
        name = re.sub(r"[\s\d*]+", "", part)
        if not _CHINESE_NAME_RE.fullmatch(name):
            return []
        authors.append(name)
    return authors if 1 <= len(authors) <= 12 else []


def _apply_article_number_derivations(
    fields: dict[str, Any],
    provenance: dict[str, list[dict[str, Any]]],
    match: re.Match[str],
    source: dict[str, Any],
) -> None:
    start = int(match.group("start"))
    count = int(match.group("count"))
    derived = {
        "year": int(match.group("year")),
        "issue": _normalize_issue(match.group("issue")),
        "pages": f"{start}-{start + count - 1}",
    }
    for field, value in derived.items():
        if fields[field] is None:
            fields[field] = value
            provenance[field].append(
                {
                    **source,
                    "source_type": "deterministic_derivation",
                    "reason": f"derived_from_article_number.{field}",
                }
            )


def _parse_local_citation(line: str) -> dict[str, Any] | None:
    compact = re.sub(r"\s+", " ", line)
    match = re.search(
        r"\[J\]\s*[.。]?\s*([^,，]+)[,，]\s*(20\d{2})"
        r"\s*[,，]\s*(\d+)\s*\(([^)]+)\)\s*[:：]\s*"
        r"(\d+)\s*[-–—]\s*(\d+)",
        compact,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return {
        "journal": match.group(1).strip(" .。"),
        "year": int(match.group(2)),
        "volume": match.group(3),
        "issue": match.group(4).replace("增", "S"),
        "pages": f"{int(match.group(5))}-{int(match.group(6))}",
    }


def _extract_institution(
    lines: list[str],
) -> tuple[str, int, str] | None:
    for line_number, raw_line in enumerate(lines, start=1):
        line = _normalize_line(raw_line)
        if "学位授予单位" not in line:
            continue
        value = line.split("学位授予单位", 1)[1]
        value = value.split("学位授予日期", 1)[0]
        value = re.sub(r"[_\s]+", "", value).strip("：:")
        if value:
            return value, line_number, raw_line
    return None


def _extract_thesis_year(
    lines: list[str],
) -> tuple[int, int, str] | None:
    for line_number, raw_line in enumerate(lines, start=1):
        line = _normalize_line(raw_line)
        match = re.search(r"(20\d{2})\s*年\s*\d{1,2}\s*月", line)
        if match:
            return int(match.group(1)), line_number, raw_line
    return None


def _find_line(
    lines: list[str],
    pattern: str,
) -> tuple[int, str] | None:
    for line_number, raw_line in enumerate(lines, start=1):
        if re.search(pattern, _normalize_line(raw_line)):
            return line_number, raw_line
    return None


def _load_overrides(
    overrides_path: str | Path | None,
) -> tuple[dict[str, Any], bytes]:
    if overrides_path is None:
        payload = {
            "schema_version": REFERENCE_OVERRIDE_SCHEMA_VERSION,
            "records": [],
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return payload, raw
    return _read_json(Path(overrides_path), "参考文献核验覆盖表")


def _validate_overrides(
    payload: dict[str, Any],
    allowed_paper_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if payload.get("schema_version") != REFERENCE_OVERRIDE_SCHEMA_VERSION:
        raise ReferenceCatalogError(
            "参考文献核验覆盖表 schema_version 必须为 "
            f"{REFERENCE_OVERRIDE_SCHEMA_VERSION}。"
        )
    records = payload.get("records")
    if not isinstance(records, list):
        raise ReferenceCatalogError("参考文献核验覆盖表 records 必须是数组。")
    result: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ReferenceCatalogError(f"records[{index}] 必须是对象。")
        paper_id = str(record.get("paper_id", ""))
        if paper_id not in allowed_paper_ids:
            raise ReferenceCatalogError(
                f"核验覆盖表包含非本次综合论文：{paper_id}"
            )
        if paper_id in result:
            raise ReferenceCatalogError(f"核验覆盖表 paper_id 重复：{paper_id}")
        fields = record.get("fields")
        field_sources = record.get("field_sources")
        sources = record.get("sources")
        if not isinstance(fields, dict) or not fields:
            raise ReferenceCatalogError(f"{paper_id} fields 必须是非空对象。")
        if not isinstance(field_sources, dict) or not isinstance(sources, list):
            raise ReferenceCatalogError(
                f"{paper_id} 必须提供 field_sources 和 sources。"
            )
        unknown_fields = set(fields) - set(REFERENCE_FIELDS)
        if unknown_fields:
            raise ReferenceCatalogError(
                f"{paper_id} 包含未知字段：{sorted(unknown_fields)}"
            )
        source_ids: set[str] = set()
        for source in sources:
            if not isinstance(source, dict):
                raise ReferenceCatalogError(f"{paper_id} sources 必须是对象数组。")
            source_id = str(source.get("source_id", ""))
            if not source_id or source_id in source_ids:
                raise ReferenceCatalogError(
                    f"{paper_id} source_id 缺失或重复：{source_id}"
                )
            if not str(source.get("source_type", "")) or not str(
                source.get("source_name", "")
            ):
                raise ReferenceCatalogError(
                    f"{paper_id}/{source_id} 缺少 source_type 或 source_name。"
                )
            if not source.get("url") and not source.get("path"):
                raise ReferenceCatalogError(
                    f"{paper_id}/{source_id} 必须提供 url 或 path。"
                )
            source_ids.add(source_id)
        for field in fields:
            linked = field_sources.get(field)
            if not isinstance(linked, list) or not linked:
                raise ReferenceCatalogError(
                    f"{paper_id}/{field} 没有来源关联。"
                )
            missing_sources = set(map(str, linked)) - source_ids
            if missing_sources:
                raise ReferenceCatalogError(
                    f"{paper_id}/{field} 引用了不存在的来源："
                    f"{sorted(missing_sources)}"
                )
        if "authors" in fields and not (
            isinstance(fields["authors"], list)
            and all(str(row).strip() for row in fields["authors"])
        ):
            raise ReferenceCatalogError(f"{paper_id}/authors 必须是非空字符串数组。")
        if "year" in fields and not isinstance(fields["year"], int):
            raise ReferenceCatalogError(f"{paper_id}/year 必须是整数。")
        result[paper_id] = record
    return result


def _markdown_provenance(
    path: Path,
    line_number: int,
    raw_text: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "source_type": "local_markdown",
        "path": str(path),
        "start_line": line_number,
        "end_line": line_number,
        "raw_text": raw_text,
        "reason": reason,
    }


def _normalize_line(value: str) -> str:
    return unicodedata.normalize("NFKC", _strip_markup(value)).replace("—", "-")


def _strip_markup(value: str) -> str:
    return html.unescape(_TAG_RE.sub("", value)).replace("\\_", "_")


def _clean_doi(value: str) -> str:
    return value.rstrip(".,;:。；：").lower()


def _normalize_issue(value: str) -> str:
    return str(int(value)) if value.isdigit() else value


def _verified_fields(reference: dict[str, Any]) -> list[str]:
    return [
        field
        for field in REFERENCE_FIELDS
        if any(
            row.get("reason") == "verified_override"
            for row in reference.get("provenance", {}).get(field, [])
        )
    ]


def _reference_id(paper_id: str) -> str:
    digest = hashlib.sha256(paper_id.encode("utf-8")).hexdigest()[:16]
    return f"ref-{digest}"


def _bib_key(reference: dict[str, Any]) -> str:
    return "ref_" + str(reference["reference_id"]).removeprefix("ref-")


def _bib_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _render_provenance(row: dict[str, Any]) -> str:
    if row.get("source_type") in {
        "local_markdown",
        "deterministic_derivation",
    }:
        line = row.get("start_line")
        reason = row.get("reason", "")
        return f"{row.get('path')}:{line}（{reason}）"
    if row.get("source_type") == "project":
        return f"{row.get('path')} {row.get('json_pointer')}"
    target = row.get("url") or row.get("path") or row.get("source_name")
    return f"{row.get('source_name')}（{target}）"


def _field_label(field: str) -> str:
    return {
        "entry_type": "文献类型",
        "title": "题名",
        "authors": "作者",
        "year": "年份",
        "journal": "期刊",
        "volume": "卷",
        "issue": "期",
        "pages": "页码",
        "doi": "DOI",
        "article_number": "文章编号",
        "institution": "学位授予单位",
        "degree": "学位类型",
        "volume_or_issue": "卷或期",
        "institution_verification": "学位授予单位核验",
    }.get(field, field)


def _display_list(values: list[str]) -> str:
    return "无" if not values else "、".join(_field_label(row) for row in values)


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _validate_run_id(run_id: str) -> None:
    if run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
        raise ReferenceCatalogError(
            "参考文献 run_id 必须是单个目录名，不能包含路径。"
        )


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReferenceCatalogError(f"无法读取{label}：{path}") from exc
    if not isinstance(payload, dict):
        raise ReferenceCatalogError(f"{label}必须是 JSON 对象：{path}")
    return payload, raw


def _read_jsonl(
    path: Path,
    label: str,
) -> tuple[list[dict[str, Any]], bytes]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ReferenceCatalogError(f"无法读取{label}：{path}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReferenceCatalogError(
                f"{label}第 {line_number} 行不是合法 JSON：{path}"
            ) from exc
        if not isinstance(row, dict):
            raise ReferenceCatalogError(
                f"{label}第 {line_number} 行必须是对象：{path}"
            )
        rows.append(row)
    if not rows:
        raise ReferenceCatalogError(f"{label}不能为空：{path}")
    return rows, raw


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: object) -> None:
    _write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _write_text(
        path,
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)
