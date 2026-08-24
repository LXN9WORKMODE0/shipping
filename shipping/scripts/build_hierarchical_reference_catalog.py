from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.reference_catalog import (  # noqa: E402
    _finalize_reference,
    extract_local_reference,
    render_bibtex,
    render_reference_review,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="为层级综述冻结论文集合生成参考文献目录。")
    parser.add_argument("--global-run-id", required=True)
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--year-overrides", type=Path)
    args = parser.parse_args()
    result = build_catalog(args.workspace, args.global_run_id, args.run_id, year_overrides_path=args.year_overrides)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


def build_catalog(
    workspace: Path,
    global_run_id: str,
    run_id: str,
    *,
    year_overrides_path: Path | None = None,
) -> dict[str, Any]:
    run_dir = workspace / "_reference_catalogs" / "runs" / run_id
    if run_dir.exists():
        raise ValueError(f"run_id已存在：{run_id}")
    run_dir.mkdir(parents=True)
    global_dir = workspace / "_hierarchical_global_landscapes" / "runs" / global_run_id
    global_manifest = _read_json(global_dir / "manifest.json")
    if global_manifest.get("status") != "completed":
        raise ValueError("全局研究景观运行不可用。")
    local_batch_run_id = str(global_manifest["local_batch_run_id"])
    local_batch = _read_json(workspace / "_hierarchical_landscapes" / "local_runs" / local_batch_run_id / "output" / "local_landscape_batch.json")

    sources: dict[str, dict[str, str]] = {}
    for record in local_batch["records"]:
        rows = _read_jsonl(workspace / "_research_landscapes" / "runs" / str(record["landscape_run_id"]) / "input" / "understandings.jsonl")
        for row in rows:
            understanding = row["understanding"]
            paper_id = str(understanding["paper_id"])
            source = {
                "paper_id": paper_id,
                "paper_title": str(understanding.get("paper_title") or paper_id),
                "source_run_id": str(row["source_run_id"]),
            }
            known = sources.get(paper_id)
            if known is not None and known != source:
                raise ValueError(f"同一论文映射到不同理解运行：{paper_id}")
            sources[paper_id] = source

    year_overrides = _load_year_overrides(year_overrides_path)
    references: list[dict[str, Any]] = []
    for number, paper_id in enumerate(sorted(sources), 1):
        source = sources[paper_id]
        understanding_manifest = _read_json(workspace / "_paper_understandings" / "runs" / source["source_run_id"] / "manifest.json")
        workspace_paper_id = str(understanding_manifest["workspace_paper_id"])
        markdown_path = workspace / workspace_paper_id / "normalized" / "document.md"
        markdown = markdown_path.read_text(encoding="utf-8-sig")
        reference = extract_local_reference(
            markdown,
            paper_id=paper_id,
            paper_title=source["paper_title"],
            workspace_paper_id=workspace_paper_id,
            markdown_path=markdown_path,
            source_run_id=source["source_run_id"],
        )
        reference["number"] = number
        reference["reference_id"] = "reference_" + hashlib.sha256(paper_id.encode("utf-8")).hexdigest()[:20]
        _finalize_reference(reference)
        override = year_overrides.get(paper_id)
        if override is not None:
            parsed_year = reference.get("year")
            reference["year"] = override["year"]
            reference.setdefault("provenance", {})["year_override"] = override
            if parsed_year != override["year"]:
                reference["provenance"]["parsed_year_before_override"] = parsed_year
            _finalize_reference(reference)
        references.append(reference)

    unknown_override_ids = sorted(set(year_overrides) - set(sources))
    if unknown_override_ids:
        raise ValueError(f"年份修订包含不在冻结论文集合中的paper_id：{unknown_override_ids}")

    missing_years = [row["paper_id"] for row in references if not isinstance(row.get("year"), int)]
    catalog = {
        "schema_version": "reference_catalog.v1",
        "run_id": run_id,
        "project_id": global_run_id,
        "project_name": "层级综述写作论文集合",
        "topic": str(local_batch.get("topic", "")),
        "synthesis_run_id": global_run_id,
        "reference_count": len(references),
        "complete_count": sum(row["status"] == "complete" for row in references),
        "partial_count": sum(row["status"] == "partial" for row in references),
        "references": references,
    }
    status = "completed" if not missing_years else "failed"
    manifest = {
        "schema_version": "reference_catalog.run.v1",
        "run_id": run_id,
        "status": status,
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "global_run_id": global_run_id,
        "local_batch_run_id": local_batch_run_id,
        "reference_count": len(references),
        "complete_count": catalog["complete_count"],
        "partial_count": catalog["partial_count"],
        "missing_year_paper_ids": missing_years,
        "year_override_count": len(year_overrides),
    }
    if year_overrides_path is not None:
        _write_json(
            run_dir / "input" / "year_overrides.json",
            {
                "schema_version": "reference_year_overrides.v1",
                "overrides": list(year_overrides.values()),
            },
        )
    _write_json(run_dir / "output" / "references.json", catalog)
    _write_text(run_dir / "output" / "references.bib", render_bibtex(references))
    _write_text(run_dir / "review" / "references.md", render_reference_review(catalog))
    _write_json(run_dir / "audit" / "missing_years.json", {"paper_ids": missing_years})
    _write_json(run_dir / "manifest.json", manifest)
    return {"run_id": run_id, "status": status, "reference_count": len(references), "missing_year_count": len(missing_years), "missing_year_paper_ids": missing_years}


def _load_year_overrides(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = _read_json(path)
    if payload.get("schema_version") != "reference_year_overrides.v1":
        raise ValueError("年份修订文件schema_version必须为reference_year_overrides.v1。")
    overrides: dict[str, dict[str, Any]] = {}
    for row in payload.get("overrides", []):
        paper_id = str(row.get("paper_id", "")).strip()
        year = row.get("year")
        source = str(row.get("source", "")).strip()
        if not paper_id or not isinstance(year, int) or not 1000 <= year <= 2999 or not source:
            raise ValueError(f"年份修订记录不完整：{row}")
        if paper_id in overrides:
            raise ValueError(f"年份修订paper_id重复：{paper_id}")
        overrides[paper_id] = dict(row)
    return overrides


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
