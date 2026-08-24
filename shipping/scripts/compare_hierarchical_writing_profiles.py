from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFENSIVE_MARKERS = (
    "但仍需",
    "仍需进一步",
    "需要指出",
    "值得注意",
    "仍存在局限",
    "仍有不足",
    "尚需",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="在同一论点计划上对比基础写作与 skill-guided 写作。")
    parser.add_argument("--global-run-id", required=True)
    parser.add_argument("--reference-catalog-run-id", required=True)
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--model-profile", type=Path, default=Path("config/models/deepseek-v4-pro-official.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--argument-plan-run-id", default=None, help="复用已经通过校验的共享论点计划。")
    parser.add_argument("--baseline-writing-run-id", default=None, help="复用已完成的基础版写作运行。")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--review-as-of-year", type=int, default=None)
    args = parser.parse_args()

    run_id = args.run_id or datetime.now(UTC).strftime("writing-ab-%Y%m%d-%H%M%S")
    baseline_id = f"{run_id}--baseline"
    skill_id = f"{run_id}--skill-guided"
    if args.baseline_writing_run_id:
        baseline_id = args.baseline_writing_run_id
        baseline = {"status": "completed"}
    else:
        baseline = _run_writer(args, baseline_id, "baseline", args.argument_plan_run_id)
    shared_plan_id = args.argument_plan_run_id or baseline_id
    skill = _run_writer(args, skill_id, "skill_guided", shared_plan_id)

    comparison_dir = args.workspace / "_hierarchical_review_comparisons" / "runs" / run_id
    comparison_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": "hierarchical_review_writing_comparison.v1",
        "run_id": run_id,
        "global_run_id": args.global_run_id,
        "reference_catalog_run_id": args.reference_catalog_run_id,
        "shared_argument_plan_run_id": shared_plan_id,
        "baseline": _measure_result(args.workspace, baseline_id, baseline),
        "skill_guided": _measure_result(args.workspace, skill_id, skill),
    }
    (comparison_dir / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (comparison_dir / "comparison.md").write_text(_render(report), encoding="utf-8")
    print(json.dumps({"run_id": run_id, "status": "completed", "report": str(comparison_dir / "comparison.md")}, ensure_ascii=False))
    return 0


def _run_writer(args: argparse.Namespace, run_id: str, profile: str, plan_run_id: str | None = None) -> dict[str, Any]:
    command = [
        sys.executable,
        "main.py",
        "--env-file",
        str(args.env_file),
        "hierarchical-review-write",
        "--global-run-id",
        args.global_run_id,
        "--reference-catalog-run-id",
        args.reference_catalog_run_id,
        "--workspace",
        str(args.workspace),
        "--model-profile",
        str(args.model_profile),
        "--writing-profile",
        profile,
        "--run-id",
        run_id,
        "--timeout",
        str(args.timeout),
    ]
    if plan_run_id:
        command.extend(["--reuse-argument-plan-from-run-id", plan_run_id])
    if args.review_as_of_year is not None:
        command.extend(["--review-as-of-year", str(args.review_as_of_year)])
    completed = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8")
    if not completed.stdout.strip():
        raise RuntimeError(f"写作命令没有返回结果：{completed.stderr.strip()}")
    result = json.loads(completed.stdout.splitlines()[-1])
    result["stderr"] = completed.stderr
    return result


def _measure(workspace: Path, run_id: str) -> dict[str, Any]:
    run_dir = workspace / "_hierarchical_review_writing" / "runs" / run_id
    review = json.loads((run_dir / "output" / "hierarchical_review.json").read_text(encoding="utf-8"))
    paragraphs = [*review["introduction"]]
    for chapter in review["chapters"]:
        paragraphs.extend(chapter["paragraphs"])
    paragraphs.extend(review["discussion"])
    paragraphs.extend(review["conclusion"])
    texts = [row["text"] for row in paragraphs]
    marker_counts = {marker: sum(text.count(marker) for text in texts) for marker in DEFENSIVE_MARKERS}
    return {
        "writing_run_id": run_id,
        "body_char_count": review["body_char_count"],
        "paragraph_count": len(paragraphs),
        "average_paragraph_chars": round(sum(map(len, texts)) / len(texts), 1),
        "multi_source_paragraph_ratio": round(sum(len(row["citation_keys"]) >= 2 for row in paragraphs) / len(paragraphs), 4),
        "defensive_marker_count": sum(marker_counts.values()),
        "defensive_marker_counts": marker_counts,
        "review_path": str(run_dir / "review" / "hierarchical_review.md"),
        "argument_plan_path": str(run_dir / "review" / "argument_plan.md"),
    }


def _measure_result(workspace: Path, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["status"] == "completed":
        return {"status": "completed", **_measure(workspace, run_id)}
    return {
        "status": "failed",
        "writing_run_id": run_id,
        "failure": result.get("failure"),
    }


def _render(report: dict[str, Any]) -> str:
    baseline = report["baseline"]
    skill = report["skill_guided"]
    if baseline["status"] != "completed" or skill["status"] != "completed":
        rows = [
            "# 层级综述写作配置 A/B 对比",
            "",
            f"- 共用论点计划：`{report['shared_argument_plan_run_id']}`",
            f"- 基础版：{baseline['status']}，`{baseline['writing_run_id']}`",
            f"- Skill-guided：{skill['status']}，`{skill['writing_run_id']}`",
            "",
        ]
        for label, item in (("基础版", baseline), ("Skill-guided", skill)):
            if item["status"] == "failed":
                rows.append(f"## {label}失败")
                rows.append("")
                rows.append(f"- 错误：`{item['failure']['error_code']}`")
                rows.append(f"- 原因：{item['failure']['error_message']}")
                rows.append("")
            else:
                rows.append(f"- {label}正文：`{item['review_path']}`")
        return "\n".join(rows)
    return "\n".join([
        "# 层级综述写作配置 A/B 对比",
        "",
        f"- 共用论点计划：`{report['shared_argument_plan_run_id']}`",
        f"- 基础版：`{baseline['writing_run_id']}`",
        f"- Skill-guided：`{skill['writing_run_id']}`",
        "",
        "| 指标 | 基础版 | Skill-guided |",
        "|---|---:|---:|",
        f"| 正文字符数 | {baseline['body_char_count']} | {skill['body_char_count']} |",
        f"| 段落数 | {baseline['paragraph_count']} | {skill['paragraph_count']} |",
        f"| 平均段长 | {baseline['average_paragraph_chars']} | {skill['average_paragraph_chars']} |",
        f"| 多来源段落比例 | {baseline['multi_source_paragraph_ratio']} | {skill['multi_source_paragraph_ratio']} |",
        f"| 防御性标记次数 | {baseline['defensive_marker_count']} | {skill['defensive_marker_count']} |",
        "",
        "> 自动指标只用于定位差异。最终判断仍需比较论点清晰度、段落推进、证据综合和可读性。",
        "",
        f"- [基础版正文]({baseline['review_path']})",
        f"- [Skill-guided 正文]({skill['review_path']})",
        "",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
