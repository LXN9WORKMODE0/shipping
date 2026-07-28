from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class MaterialReviewExporter:
    """把材料 JSONL 渲染成便于人工审核的 Markdown 文件。"""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(self, run_id: str | None = None) -> dict[str, Any]:
        resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        materials = _read_jsonl(self.workspace / "_corpus" / "materials.jsonl")
        grouped = _group_by_paper(materials)

        review_dir = self.workspace / "_human_review" / resolved_run_id
        papers_dir = review_dir / "papers"
        papers_dir.mkdir(parents=True, exist_ok=True)

        paper_outputs = []
        for index, (paper_id, paper_materials) in enumerate(grouped.items(), start=1):
            filename = f"paper_{index:03d}.md"
            path = papers_dir / filename
            path.write_text(_render_paper(paper_id, paper_materials), encoding="utf-8")
            paper_outputs.append(
                {
                    "paper_id": paper_id,
                    "filename": filename,
                    "path": str(path),
                    "material_count": len(paper_materials),
                    "confidence_flags": _flag_counts(paper_materials, "confidence_flags"),
                    "quality_flags": _flag_counts(paper_materials, "quality_flags"),
                    "content_kinds": dict(Counter(str(item.get("content_kind", "unknown")) for item in paper_materials)),
                }
            )

        index_path = review_dir / "index.md"
        index_path.write_text(_render_index(resolved_run_id, self.workspace, materials, paper_outputs), encoding="utf-8")

        manifest = {
            "run_id": resolved_run_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "workspace": str(self.workspace),
            "total_materials": len(materials),
            "paper_count": len(grouped),
            "index_path": str(index_path),
            "papers": paper_outputs,
        }
        _write_json(review_dir / "manifest.json", manifest)
        return {
            "run_id": resolved_run_id,
            "total_materials": len(materials),
            "paper_count": len(grouped),
            "index_path": str(index_path),
            "manifest_path": str(review_dir / "manifest.json"),
            "review_dir": str(review_dir),
        }


def _render_index(run_id: str, workspace: Path, materials: list[dict[str, Any]], paper_outputs: list[dict[str, Any]]) -> str:
    confidence_counts = _flag_counts(materials, "confidence_flags")
    quality_counts = _flag_counts(materials, "quality_flags")
    lines = [
        "# 材料人工审核",
        "",
        "## 总览",
        "",
        f"- run_id: `{run_id}`",
        f"- workspace: `{workspace}`",
        f"- 论文数: {len(paper_outputs)}",
        f"- 材料卡数: {len(materials)}",
        "",
        "## 审核方法",
        "",
        "- 从下面的论文列表进入单篇审核文件。",
        "- 每张材料卡只勾选一个状态：`可用`、`需修改`、`废弃`。",
        "- 在 `备注` 行写具体修改意见，不要只写“有问题”。",
        "- 如果正文片段可疑，用 `source` 行号回到对应的 `normalized/document.md` 复核。",
        "",
        "## 论文列表",
        "",
    ]
    if not paper_outputs:
        lines.append("- 无")
    for paper in paper_outputs:
        confidence_text = _format_counts(paper["confidence_flags"])
        quality_text = _format_counts(paper["quality_flags"])
        kind_text = _format_counts(paper["content_kinds"])
        lines.append(
            f"- [{paper['filename']}](papers/{paper['filename']}): {paper['paper_id']} "
            f"材料卡={paper['material_count']}, 内容类型={kind_text}, "
            f"置信标记={confidence_text}, 质量标记={quality_text}"
        )

    lines.extend(["", "## 标记汇总", ""])
    lines.append(f"- 置信标记(confidence_flags): {_format_counts(confidence_counts)}")
    lines.append(f"- 质量标记(quality_flags): {_format_counts(quality_counts)}")
    return "\n".join(lines) + "\n"


def _render_paper(paper_id: str, materials: list[dict[str, Any]]) -> str:
    title = str(materials[0].get("paper_title") or paper_id) if materials else paper_id
    lines = [
        f"# {title}",
        "",
        f"- paper_id: `{paper_id}`",
        f"- 材料卡数: {len(materials)}",
        "",
        "## 论文级审核",
        "",
        "- [ ] 材料顺序合理",
        "- [ ] 标题能帮助判断内容",
        "- [ ] 需要回原文复核的卡片已标注",
        "- 备注：",
        "",
    ]
    for index, material in enumerate(materials, start=1):
        lines.extend(_render_card(index, material))
    return "\n".join(lines) + "\n"


def _render_card(index: int, material: dict[str, Any]) -> list[str]:
    title = str(material.get("clean_title") or material.get("raw_title") or material.get("material_id") or f"卡片 {index:03d}")
    ref = material.get("source_span") or material.get("content_ref") or {}
    ref_text = _format_ref(ref)
    precise_refs = material.get("source_spans") or [ref]
    precise_ref_text = "; ".join(_format_ref(item) for item in precise_refs if isinstance(item, dict))
    parent = material.get("parent_ref", {})
    confidence_flags = material.get("confidence_flags", [])
    quality_flags = material.get("quality_flags", [])
    content_kind = str(material.get("content_kind", "unknown"))
    heading_path = [str(item) for item in material.get("heading_path", [])]
    summary = str(material.get("lead_excerpt") or material.get("summary") or "").strip()
    extract = str(material.get("extract") or summary).strip()

    lines = [
        f"## 卡片 {index:03d}：{title}",
        "",
        "- [ ] 可用",
        "- [ ] 需修改",
        "- [ ] 废弃",
        "- 备注：",
        "",
        f"- 材料ID: `{material.get('material_id', '')}`",
        f"- 原文位置: `{ref_text}`",
        f"- 精确来源: `{precise_ref_text or ref_text}`",
        f"- 内容类型: `{content_kind}`",
        f"- 标题路径: `{_format_heading_path(heading_path)}`",
        f"- 上级节点: `{parent.get('node_id', '')} / {parent.get('title', '')}`",
        f"- 置信标记(confidence_flags): {_format_list(confidence_flags)}",
        f"- 质量标记(quality_flags): {_format_list(quality_flags)}",
        "",
        "### 摘要",
        "",
        summary or "（空）",
        "",
        "### 正文片段",
        "",
        *_quote_block(extract or "（空）"),
        "",
    ]
    return lines


def _group_by_paper(materials: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for material in materials:
        grouped.setdefault(str(material.get("paper_id", "")), []).append(material)
    return {
        paper_id: sorted(items, key=lambda item: (int(item.get("order", 0) or 0), str(item.get("material_id", ""))))
        for paper_id, items in sorted(grouped.items())
    }


def _flag_counts(materials: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(Counter(flag for material in materials for flag in material.get(field, [])))


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无"
    return ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))


def _format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "无"


def _format_heading_path(values: list[str]) -> str:
    return " > ".join(values) if values else "无"


def _format_ref(ref: dict[str, Any]) -> str:
    path = ref.get("path", "")
    start = ref.get("start_line", "")
    end = ref.get("end_line", "")
    if path and start and end:
        start_char = ref.get("start_char")
        end_char = ref.get("end_char")
        if start_char is not None and end_char is not None:
            return f"{path}:{start}:{start_char}-{end}:{end_char}"
        return f"{path}:{start}-{end}"
    return str(ref)


def _quote_block(text: str) -> list[str]:
    return [f"> {line}" if line else ">" for line in text.splitlines()]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
