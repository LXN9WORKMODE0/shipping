from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_contracts import (
    ContractViolation,
    assign_evidence_unit_ids,
    validate_evidence_batch,
)
from shipping_pipeline.llm_evidence_correction import (
    EvidenceCorrectionUnavailable,
    collect_correctable_material_failures,
)


AUDIT_SCHEMA_VERSION = "audit.evidence_run_contract.v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="用当前证据合同只读重验历史 LLM 运行。")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = audit_evidence_run_contract(
        workspace=args.workspace,
        source_run_id=args.source_run_id,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def audit_evidence_run_contract(
    *,
    workspace: Path,
    source_run_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    source_dir = workspace / "_llm_analysis" / "runs" / source_run_id
    if not source_dir.is_dir():
        raise ValueError(f"来源运行不存在：{source_dir}")
    if output_dir.exists():
        raise ValueError(f"审计目录已存在，不能覆盖：{output_dir}")

    paper = _read_json(source_dir / "input" / "paper.json")
    materials = _read_jsonl(source_dir / "input" / "materials.jsonl")
    quote_candidates = _read_jsonl(source_dir / "input" / "quote_candidates.jsonl")
    plan = _read_json(source_dir / "plan" / "analysis_plan.json")
    material_by_id = {str(row["material_id"]): row for row in materials}
    records: list[dict[str, Any]] = []
    for batch in plan["evidence_batches"]:
        batch_id = str(batch["request_id"])
        material_ids = [str(item) for item in batch["material_ids"]]
        selected = set(material_ids)
        batch_materials = [material_by_id[item] for item in material_ids]
        batch_quotes = [
            row for row in quote_candidates if str(row["material_id"]) in selected
        ]
        batch_dir = source_dir / "batches" / batch_id
        parsed_path = batch_dir / "parsed_response.json"
        result_path = batch_dir / "result.json"
        original_status = (
            str(_read_json(result_path).get("status", "unknown"))
            if result_path.exists()
            else "missing"
        )
        record: dict[str, Any] = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "batch_id": batch_id,
            "material_ids": material_ids,
            "original_status": original_status,
        }
        if not parsed_path.exists():
            record.update(
                {
                    "current_status": "not_validated",
                    "error_code": "input.parsed_response_missing",
                    "error_path": None,
                    "error_message": f"缺少 {parsed_path}",
                    "failure_units": [],
                }
            )
            records.append(record)
            continue
        payload = _read_json(parsed_path)
        try:
            normalized = validate_evidence_batch(payload, batch_materials, batch_quotes)
            assign_evidence_unit_ids(
                batch_id,
                normalized["evidence_units"],
                batch_materials,
                generation_id=str(paper["generation_id"]),
            )
        except ContractViolation as exc:
            record.update(
                {
                    "current_status": "failed",
                    "error_code": exc.code,
                    "error_path": exc.path,
                    "error_message": str(exc),
                    "failure_units": _failure_units(
                        payload,
                        batch_materials,
                        batch_quotes,
                    ),
                }
            )
        else:
            record.update(
                {
                    "current_status": "passed",
                    "error_code": None,
                    "error_path": None,
                    "error_message": None,
                    "failure_units": [],
                }
            )
        records.append(record)

    errors = Counter(
        str(row["error_code"])
        for row in records
        if row["current_status"] != "passed"
    )
    summary = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "paper_id": str(paper["paper_id"]),
        "paper_title": str(paper["paper_title"]),
        "evidence_batch_count": len(records),
        "current_passed_batch_count": sum(row["current_status"] == "passed" for row in records),
        "current_failed_batch_count": sum(row["current_status"] == "failed" for row in records),
        "not_validated_batch_count": sum(row["current_status"] == "not_validated" for row in records),
        "newly_failed_batch_count": sum(
            row["original_status"] == "completed" and row["current_status"] == "failed"
            for row in records
        ),
        "error_code_counts": dict(sorted(errors.items())),
    }
    output_dir.mkdir(parents=True)
    _write_json(output_dir / "summary.json", summary)
    _write_jsonl(output_dir / "records.jsonl", records)
    (output_dir / "report.md").write_text(
        _render_report(summary, records),
        encoding="utf-8",
    )
    return summary


def _failure_units(
    payload: dict[str, Any],
    materials: list[dict[str, Any]],
    quote_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        failures = collect_correctable_material_failures(
            payload,
            materials,
            quote_candidates,
        )
    except EvidenceCorrectionUnavailable:
        return []
    results_by_id = {
        str(row["material_id"]): row for row in payload["material_results"]
    }
    quote_text_by_id = {
        str(row["quote_id"]): str(row["text"]) for row in quote_candidates
    }
    units: list[dict[str, Any]] = []
    for failure in failures:
        material_id = str(failure["material_id"])
        source_units = results_by_id[material_id]["evidence_units"]
        for violation in failure["violations"]:
            source_index = violation.get("source_evidence_index")
            if not isinstance(source_index, int):
                continue
            unit = source_units[source_index]
            units.append(
                {
                    "material_id": material_id,
                    "source_evidence_index": source_index,
                    "error_code": violation["error_code"],
                    "claim": str(unit["claim"]),
                    "citations": [
                        {
                            "quote_id": str(citation["quote_id"]),
                            "quote": quote_text_by_id.get(str(citation["quote_id"]), "（未知 quote_id）"),
                        }
                        for citation in unit["citations"]
                    ],
                }
            )
    return units


def _render_report(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    lines = [
        "# 历史证据运行当前合同审计",
        "",
        f"- 来源运行：`{summary['source_run_id']}`",
        f"- 论文：{summary['paper_title']}",
        f"- 证据批次：{summary['evidence_batch_count']}",
        f"- 当前通过：{summary['current_passed_batch_count']}",
        f"- 当前失败：{summary['current_failed_batch_count']}",
        f"- 无法重验：{summary['not_validated_batch_count']}",
        f"- 历史已完成但当前新增失败：{summary['newly_failed_batch_count']}",
        "",
        "## 错误分布",
        "",
    ]
    if summary["error_code_counts"]:
        for code, count in summary["error_code_counts"].items():
            lines.append(f"- `{code}`：{count} 个批次")
    else:
        lines.append("无。")
    lines.extend(["", "## 批次结果", "", "| 批次 | 历史状态 | 当前状态 | 错误 |", "|---|---|---|---|"])
    for row in records:
        lines.append(
            f"| `{row['batch_id']}` | {row['original_status']} | {row['current_status']} | "
            f"{row['error_code'] or '无'} |"
        )
    for row in records:
        if row["current_status"] == "passed":
            continue
        lines.extend(
            [
                "",
                f"### {row['batch_id']}",
                "",
                f"- 错误：`{row['error_code']}`",
                f"- 路径：`{row['error_path'] or '无'}`",
                f"- 详情：{row['error_message']}",
                "",
            ]
        )
        if not row["failure_units"]:
            lines.append("无法定位为可局部修正的 Evidence 单元。")
            continue
        for index, unit in enumerate(row["failure_units"], start=1):
            lines.append(
                f"{index}. Card `{unit['material_id']}` 原证据 #{unit['source_evidence_index']}："
                f"{unit['claim']}"
            )
            for citation in unit["citations"]:
                lines.append(f"   - `{citation['quote_id']}`：{citation['quote']}")
    lines.extend(
        [
            "",
            "## 审计边界",
            "",
            "本审计只读取历史输入和模型响应，用当前确定性合同重新校验；不调用模型、不修改来源运行，也不发布修正结果。",
            "",
        ]
    )
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
