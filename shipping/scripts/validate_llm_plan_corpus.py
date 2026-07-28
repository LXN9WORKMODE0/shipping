from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_analysis import create_input_snapshot
from shipping_pipeline.llm_model_profile import load_model_profile
from shipping_pipeline.llm_planner import plan_paper_analysis
from shipping_pipeline.llm_tokenizer import DeepSeekV4TokenCounter


DEFAULT_PROFILE = PROJECT_ROOT / "config" / "models" / "siliconflow-deepseek-v4-pro.json"
DEFAULT_TOPIC = "三峡船舶积压与长江航运组织"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="对当前 Card 语料执行只读 LLM Token 规划审计。")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--model-profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--run-id", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = audit_corpus_plan(
        workspace=args.workspace,
        model_profile_path=args.model_profile,
        topic=args.topic,
        run_id=args.run_id,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "paper_record_count": result["paper_record_count"],
                "planned_paper_count": result["planned_paper_count"],
                "corpus_card_count": result["corpus_card_count"],
                "planned_card_count": result["planned_card_count"],
                "single_batch_paper_count": result["single_batch_paper_count"],
                "section_split_paper_count": result["section_split_paper_count"],
                "evidence_request_count": result["evidence_request_count"],
                "failure_count": result["failure_count"],
                "report_json": str(Path(result["output_dir"]) / "report.json"),
                "report_markdown": str(Path(result["output_dir"]) / "report.md"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "passed" else 1


def audit_corpus_plan(
    *,
    workspace: Path,
    model_profile_path: Path,
    topic: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    if not topic.strip():
        raise ValueError("topic 不能为空。")
    resolved_run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = workspace / "_llm_analysis" / "planning_audits" / resolved_run_id
    if output_dir.exists():
        raise ValueError(f"规划审计 run_id 已存在，不能覆盖：{resolved_run_id}")

    papers = _read_jsonl(workspace / "_corpus" / "papers.jsonl")
    materials = _read_jsonl(workspace / "_corpus" / "materials.jsonl")
    profile = load_model_profile(model_profile_path)
    token_counter = DeepSeekV4TokenCounter(profile, PROJECT_ROOT / ".model_cache")
    paper_ids = [str(row.get("paper_id", "")) for row in papers]
    material_counts = Counter(str(row.get("paper_id", "")) for row in materials)

    failures: list[dict[str, Any]] = []
    duplicate_paper_ids = sorted(paper_id for paper_id, count in Counter(paper_ids).items() if count != 1)
    if duplicate_paper_ids:
        failures.append(
            {
                "error_code": "corpus.paper_id_not_unique",
                "error_message": f"论文记录 ID 不唯一：{duplicate_paper_ids}",
            }
        )
    unknown_material_paper_ids = sorted(set(material_counts) - set(paper_ids))
    if unknown_material_paper_ids:
        failures.append(
            {
                "error_code": "corpus.material_paper_unknown",
                "error_message": f"Card 指向未知论文：{unknown_material_paper_ids}",
            }
        )

    paper_results: list[dict[str, Any]] = []
    planned_material_ids: list[str] = []
    for paper in papers:
        paper_id = str(paper.get("paper_id", ""))
        card_count = material_counts.get(paper_id, 0)
        if paper.get("status") != "completed":
            row = {
                "paper_id": paper_id,
                "source_status": paper.get("status"),
                "card_count": card_count,
                "planning_status": "not_plannable",
                "reason_code": "paper.status_not_completed",
                "reason": f"论文状态为 {paper.get('status')!r}，不允许进入 LLM 规划。",
            }
            paper_results.append(row)
            if card_count:
                failures.append(
                    {
                        "paper_id": paper_id,
                        "error_code": "corpus.inactive_paper_has_cards",
                        "error_message": f"非 completed 论文仍在当前 corpus 中暴露 {card_count} 张 Card。",
                    }
                )
            continue

        try:
            snapshot = create_input_snapshot(workspace, paper_id=paper_id, topic=topic)
            plan = plan_paper_analysis(
                paper_id=snapshot.paper_id,
                paper_title=snapshot.paper_title,
                topic=snapshot.topic,
                generation_id=snapshot.generation_id,
                materials=list(snapshot.materials),
                profile=profile,
                token_counter=token_counter,
                structure_path=workspace / paper_id / "structure" / "structure.json",
            )
            expected_ids = [str(material["material_id"]) for material in snapshot.materials]
            actual_ids = [material_id for batch in plan.evidence_batches for material_id in batch.material_ids]
            duplicate_ids = sorted(material_id for material_id, count in Counter(actual_ids).items() if count != 1)
            missing_ids = sorted(set(expected_ids) - set(actual_ids))
            extra_ids = sorted(set(actual_ids) - set(expected_ids))
            if actual_ids != expected_ids or duplicate_ids or missing_ids or extra_ids:
                raise ValueError(
                    "证据规划没有保持 Card 的完整、有序、唯一覆盖："
                    f"missing={missing_ids}, extra={extra_ids}, duplicate={duplicate_ids}"
                )
            batches = [
                {
                    "request_id": batch.request_id,
                    "section_ids": list(batch.section_ids),
                    "card_count": len(batch.material_ids),
                    "input_tokens": batch.input_tokens,
                    "max_output_tokens": batch.max_output_tokens,
                    "safety_margin_tokens": batch.safety_margin_tokens,
                    "total_reserved_tokens": (
                        batch.input_tokens + batch.max_output_tokens + batch.safety_margin_tokens
                    ),
                    "split_reason": batch.split_reason,
                }
                for batch in plan.evidence_batches
            ]
            row = {
                "paper_id": paper_id,
                "source_status": "completed",
                "generation_id": snapshot.generation_id,
                "card_count": len(expected_ids),
                "planning_status": "planned",
                "strategy": plan.strategy,
                "evidence_batch_count": len(batches),
                "synthesis_strategy": plan.synthesis_strategy,
                "max_batch_input_tokens": max(batch["input_tokens"] for batch in batches),
                "max_batch_reserved_tokens": max(batch["total_reserved_tokens"] for batch in batches),
                "batches": batches,
                "unplanned_material_ids": [],
                "multiply_planned_material_ids": [],
            }
            paper_results.append(row)
            planned_material_ids.extend(actual_ids)
        except Exception as exc:
            error_code = getattr(exc, "code", f"{type(exc).__module__}.{type(exc).__name__}")
            failure = {
                "paper_id": paper_id,
                "error_code": str(error_code),
                "error_message": str(exc),
            }
            failures.append(failure)
            paper_results.append(
                {
                    "paper_id": paper_id,
                    "source_status": "completed",
                    "card_count": card_count,
                    "planning_status": "failed",
                    **failure,
                }
            )

    corpus_material_ids = [str(material.get("material_id", "")) for material in materials]
    completed_material_ids = [
        str(material.get("material_id", ""))
        for material in materials
        if str(material.get("paper_id", "")) in {
            str(paper.get("paper_id", "")) for paper in papers if paper.get("status") == "completed"
        }
    ]
    if planned_material_ids != completed_material_ids:
        failures.append(
            {
                "error_code": "corpus.planning_coverage_incomplete",
                "error_message": "全语料规划结果与 completed 论文的 Card 集合或顺序不一致。",
            }
        )

    planned_rows = [row for row in paper_results if row["planning_status"] == "planned"]
    result = {
        "schema_version": "llm.corpus_planning_audit.v1",
        "run_id": resolved_run_id,
        "status": "passed" if not failures else "failed",
        "created_at": datetime.now(UTC).isoformat(),
        "topic": topic.strip(),
        "workspace": str(workspace.resolve()),
        "model_profile_id": profile.profile_id,
        "model_profile_sha256": profile.sha256,
        "request_model": profile.request_model,
        "provider": profile.provider,
        "thinking": profile.thinking,
        "tokenizer_repo": profile.tokenizer_repo,
        "tokenizer_revision": profile.tokenizer_revision,
        "context_window_tokens": profile.context_window_tokens,
        "paper_record_count": len(papers),
        "completed_paper_count": sum(paper.get("status") == "completed" for paper in papers),
        "non_completed_paper_count": sum(paper.get("status") != "completed" for paper in papers),
        "planned_paper_count": len(planned_rows),
        "corpus_card_count": len(corpus_material_ids),
        "planned_card_count": len(planned_material_ids),
        "single_batch_paper_count": sum(row.get("strategy") == "single_evidence_batch" for row in planned_rows),
        "section_split_paper_count": sum(row.get("strategy") == "section_evidence_batches" for row in planned_rows),
        "evidence_request_count": sum(row.get("evidence_batch_count", 0) for row in planned_rows),
        "failure_count": len(failures),
        "failures": failures,
        "papers": paper_results,
        "output_dir": str(output_dir.resolve()),
    }
    output_dir.mkdir(parents=True)
    _write_json(output_dir / "report.json", result)
    (output_dir / "report.md").write_text(_render_markdown(result), encoding="utf-8")
    return result


def _render_markdown(result: dict[str, Any]) -> str:
    conclusion = (
        "通过：当前所有 completed 论文均获得完整、唯一且不跨论文的 Token 规划。"
        if result["status"] == "passed"
        else "未通过：存在无法规划或覆盖不闭合的问题，禁止进入批量 API 调用。"
    )
    lines = [
        "# Card-to-LLM 全语料 Token 规划审计",
        "",
        "## 结论",
        "",
        conclusion,
        "",
        "## 总览",
        "",
        f"- 论文记录：{result['paper_record_count']}（completed {result['completed_paper_count']}，其他状态 {result['non_completed_paper_count']}）",
        f"- 当前 Card：{result['corpus_card_count']}；已规划 Card：{result['planned_card_count']}",
        f"- 已规划论文：{result['planned_paper_count']}；整篇单批：{result['single_batch_paper_count']}；章节拆分：{result['section_split_paper_count']}",
        f"- 证据抽取请求：{result['evidence_request_count']}；失败：{result['failure_count']}",
        f"- 模型：`{result['request_model']}`；profile：`{result['model_profile_id']}`",
        f"- Tokenizer revision：`{result['tokenizer_revision']}`；推理模式：`Non-think`",
        "",
        "## 逐篇结果",
        "",
        "| 论文 | 来源状态 | Card | 规划状态 | 策略 | 请求数 | 最大输入 Token | 最大预留 Token |",
        "|---|---:|---:|---:|---|---:|---:|---:|",
    ]
    for row in result["papers"]:
        strategy = {
            "single_evidence_batch": "整篇单批",
            "section_evidence_batches": "论文内按章节拆分",
        }.get(row.get("strategy"), row.get("reason_code", "-"))
        source_status = {"completed": "已完成", "failed": "失败"}.get(
            row["source_status"], str(row["source_status"])
        )
        planning_status = {
            "planned": "已规划",
            "failed": "规划失败",
            "not_plannable": "不允许规划",
        }.get(row["planning_status"], str(row["planning_status"]))
        lines.append(
            "| {paper} | {source} | {cards} | {status} | {strategy} | {requests} | {input_tokens} | {reserved} |".format(
                paper=_escape_table(str(row["paper_id"])),
                source=source_status,
                cards=row["card_count"],
                status=planning_status,
                strategy=_escape_table(str(strategy)),
                requests=row.get("evidence_batch_count", "-"),
                input_tokens=row.get("max_batch_input_tokens", "-"),
                reserved=row.get("max_batch_reserved_tokens", "-"),
            )
        )
    lines.extend(["", "## 问题账本", ""])
    if not result["failures"]:
        lines.append("没有发现规划失败或 Card 覆盖异常。")
    else:
        for failure in result["failures"]:
            prefix = f"{failure.get('paper_id')}：" if failure.get("paper_id") else ""
            lines.append(f"- `{failure['error_code']}`：{prefix}{failure['error_message']}")
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "本报告只验证输入隔离、Token 预算、章节拆分和 Card 规划覆盖；未调用 LLM，也不代表模型输出质量已经通过。",
            "",
        ]
    )
    return "\n".join(lines)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path} 第 {line_number} 行必须是 JSON 对象。")
        rows.append(payload)
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
