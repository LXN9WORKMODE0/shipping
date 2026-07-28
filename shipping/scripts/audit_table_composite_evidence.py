from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_quotes import build_quote_candidates
from shipping_pipeline.llm_table_evidence import build_table_percentage_composites


AUDIT_SCHEMA_VERSION = "audit.table_composite_evidence.v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="审计表头单位与数据行的可证明复合证据。")
    parser.add_argument("--materials-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = audit_table_composites(args.materials_jsonl, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def audit_table_composites(materials_path: Path, output_dir: Path) -> dict[str, Any]:
    materials = _read_jsonl(materials_path)
    table_materials = [row for row in materials if row.get("content_kind") == "table"]
    records: list[dict[str, Any]] = []
    for material in table_materials:
        candidates = build_quote_candidates([material])
        quote_text_by_id = {
            str(row["quote_id"]): str(row["text"])
            for row in candidates
        }
        composites = build_table_percentage_composites(material, candidates)
        extract = str(material.get("extract", ""))
        records.append(
            {
                "schema_version": AUDIT_SCHEMA_VERSION,
                "paper_id": str(material.get("paper_id", "")),
                "paper_title": str(material.get("paper_title", "")),
                "material_id": str(material.get("material_id", "")),
                "title": str(material.get("clean_title") or material.get("raw_title") or ""),
                "extract": extract,
                "has_percent_marker": "%" in extract or "％" in extract,
                "status": "recognized" if composites else "not_recognized",
                "composite_count": len(composites),
                "composites": [
                    {
                        **row.to_dict(),
                        "header_quote": quote_text_by_id[row.header_quote_id],
                        "row_quote": quote_text_by_id[row.row_quote_id],
                    }
                    for row in composites
                ],
            }
        )

    recognized = [row for row in records if row["status"] == "recognized"]
    percent_rejected = [
        row
        for row in records
        if row["has_percent_marker"] and row["status"] == "not_recognized"
    ]
    summary = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "materials_path": str(materials_path.resolve()),
        "material_count": len(materials),
        "table_card_count": len(table_materials),
        "table_cards_with_percent_marker": sum(
            1 for row in records if row["has_percent_marker"]
        ),
        "recognized_table_card_count": len(recognized),
        "recognized_composite_count": sum(row["composite_count"] for row in recognized),
        "recognized_paper_count": len({row["paper_id"] for row in recognized}),
        "percent_marker_but_rejected_count": len(percent_rejected),
        "percent_marker_but_rejected_material_ids": [
            row["material_id"] for row in percent_rejected
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_json(output_dir / "summary.json", summary)
    _write_jsonl(output_dir / "table_cards.jsonl", records)
    (output_dir / "report.md").write_text(
        _render_report(summary, recognized, percent_rejected),
        encoding="utf-8",
    )
    return summary


def _render_report(
    summary: dict[str, Any],
    recognized: list[dict[str, Any]],
    percent_rejected: list[dict[str, Any]],
) -> str:
    lines = [
        "# 表格复合证据审计",
        "",
        "## 结论",
        "",
        f"- 共扫描 {summary['material_count']} 张 Card，其中表格 Card {summary['table_card_count']} 张。",
        f"- {summary['recognized_table_card_count']} 张表格形成了 "
        f"{summary['recognized_composite_count']} 个可证明的百分比单元格关系，来自 "
        f"{summary['recognized_paper_count']} 篇论文。",
        f"- {summary['percent_marker_but_rejected_count']} 张表格虽然含百分号，但没有形成合法复合关系；这些记录不会被放行。",
        "- 合法关系要求完整表头行、完整数据行、相同行列数、显式百分比单位，以及可定位的行标签和列标签。",
        "",
        "## 已识别关系",
        "",
    ]
    if not recognized:
        lines.extend(["无。", ""])
    for card_index, card in enumerate(recognized, start=1):
        lines.extend(
            [
                f"### {card_index}. {card['paper_title'] or card['paper_id']}",
                "",
                f"- material_id：`{card['material_id']}`",
                f"- Card 标题：{card['title'] or '（无）'}",
                f"- 可证明关系数：{card['composite_count']}",
                "",
                "| 百分比事实 | 行标签 | 列标签 | 表头 quote_id | 数据行 quote_id |",
                "|---|---|---|---|---|",
            ]
        )
        for row in card["composites"]:
            lines.append(
                f"| {row['fact']} | {row['row_label']} | {row['column_label']} | "
                f"`{row['header_quote_id']}` | `{row['row_quote_id']}` |"
            )
        lines.extend(
            [
                "",
                f"表头原文：`{card['composites'][0]['header_quote']}`",
                "",
                "数据行原文：",
                "",
            ]
        )
        seen_rows: set[str] = set()
        for row in card["composites"]:
            if row["row_quote_id"] in seen_rows:
                continue
            seen_rows.add(row["row_quote_id"])
            lines.append(f"- `{row['row_quote']}`")
        lines.append("")

    lines.extend(["## 含百分号但未识别", ""])
    if not percent_rejected:
        lines.extend(["无。", ""])
    else:
        for index, row in enumerate(percent_rejected, start=1):
            lines.extend(
                [
                    f"### R{index}. {row['paper_title'] or row['paper_id']}",
                    "",
                    f"- material_id：`{row['material_id']}`",
                    f"- Card 标题：{row['title'] or '（无）'}",
                    "",
                    "````text",
                    row["extract"],
                    "````",
                    "",
                ]
            )
    return "\n".join(lines)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} 不是 JSON 对象。")
        rows.append(value)
    return rows


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
