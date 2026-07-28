from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shipping_pipeline.pipeline import LiteraturePipeline


class BatchAuditRunner:
    """对已有 normalized Markdown 执行 pipeline，并写出审计报告。"""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(self, source_root: str | Path, topic: str = "", run_id: str | None = None) -> dict[str, Any]:
        source_root_path = Path(source_root)
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        audit_dir = self.workspace / "_audit" / resolved_run_id
        audit_dir.mkdir(parents=True, exist_ok=True)

        papers = []
        for source in _discover_normalized_markdown(source_root_path):
            paper_id = source.parent.parent.name
            result = LiteraturePipeline(self.workspace).run(source, paper_id=paper_id, review_topic=topic)
            run_record = _read_json(self.workspace / paper_id / "run.json")
            papers.append(
                {
                    "paper_id": paper_id,
                    "source": str(source),
                    "status": result.status,
                    "structure_quality": result.structure_quality,
                    "issue_count": result.issue_count,
                    "issues": run_record.get("issues", []),
                    "material_count": run_record.get("stages", {}).get("material", {}).get("material_count", 0),
                    "material_types": run_record.get("stages", {}).get("material", {}).get("material_types", []),
                    "workspace_path": result.workspace_path,
                }
            )

        quality_counts = dict(Counter(item["structure_quality"] for item in papers))
        issue_counts = dict(Counter(issue["code"] for item in papers for issue in item["issues"]))
        summary = {
            "total": len(papers),
            "quality_counts": quality_counts,
            "issue_counts": issue_counts,
        }
        payload = {
            "run_id": resolved_run_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "source_root": str(source_root_path),
            "workspace": str(self.workspace),
            "topic": topic,
            "summary": summary,
            "papers": papers,
        }
        _write_json(audit_dir / "results.json", payload)
        (audit_dir / "report.md").write_text(_render_report(payload), encoding="utf-8")

        return {
            "run_id": resolved_run_id,
            "total": summary["total"],
            "quality_counts": quality_counts,
            "issue_counts": issue_counts,
            "results_path": str(audit_dir / "results.json"),
            "report_path": str(audit_dir / "report.md"),
        }


def _discover_normalized_markdown(source_root: Path) -> list[Path]:
    if source_root.is_file():
        return [source_root]
    if not source_root.exists():
        return []
    return sorted(
        path
        for path in source_root.glob("*/normalized/document.md")
        if path.is_file() and not path.parts[-3].startswith("_")
    )


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# 批量审计报告",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- 来源目录: `{payload['source_root']}`",
        f"- 主题: `{payload['topic']}`",
        f"- 论文总数: {payload['summary']['total']}",
        "",
        "## 结构质量统计",
        "",
    ]
    for label, count in payload["summary"]["quality_counts"].items():
        lines.append(f"- {label}: {count}")
    if not payload["summary"]["quality_counts"]:
        lines.append("- 无: 0")

    lines.extend(["", "## 问题统计", ""])
    for code, count in payload["summary"]["issue_counts"].items():
        lines.append(f"- {code}: {count}")
    if not payload["summary"]["issue_counts"]:
        lines.append("- 无: 0")

    lines.extend(["", "## 论文列表", ""])
    for paper in payload["papers"]:
        issue_codes = ", ".join(issue["code"] for issue in paper["issues"]) or "无"
        material_types = ", ".join(_material_type_label(kind) for kind in paper["material_types"]) or "无"
        lines.append(
            f"- {paper['paper_id']}: 状态={_status_label(paper['status'])}, 结构质量={_quality_label(paper['structure_quality'])}, "
            f"材料卡={paper['material_count']} ({material_types}), 问题={issue_codes}"
        )

    return "\n".join(lines) + "\n"


def _status_label(status: str) -> str:
    return {"completed": "完成", "failed": "失败"}.get(status, status)


def _quality_label(label: str) -> str:
    return {"gold": "可靠", "silver": "需复核", "red": "不可用"}.get(label, label)


def _material_type_label(kind: str) -> str:
    return {"evidence_card": "证据卡", "chapter": "章节材料", "evidence_chunk": "证据片段"}.get(kind, kind)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
