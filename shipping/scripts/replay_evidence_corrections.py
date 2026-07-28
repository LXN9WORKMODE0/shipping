from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.env import load_env_file
from shipping_pipeline.llm_analysis import (
    DEFAULT_TOKENIZER_CACHE,
    AnalysisInputSnapshot,
    _execute_evidence_correction,
    _failure,
    _now,
    _write_json,
    _write_jsonl,
)
from shipping_pipeline.llm_contracts import (
    ContractViolation,
    assign_evidence_unit_ids,
    validate_evidence_batch,
)
from shipping_pipeline.llm_evidence_correction import (
    EVIDENCE_CORRECTION_SCHEMA_VERSION,
    EvidenceCorrectionUnavailable,
    collect_correctable_material_failures,
    validate_and_apply_evidence_correction,
)
from shipping_pipeline.llm_model_profile import load_model_profile
from shipping_pipeline.llm_planner import PlannedRequest
from shipping_pipeline.llm_provider import OpenAICompatibleAnalysisClient
from shipping_pipeline.llm_tokenizer import DeepSeekV4TokenCounter


REPLAY_SCHEMA_VERSION = "llm.evidence_correction_replay.v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读回放历史证据抽取失败批次，并对仍失败的 material_result 执行一次修正。"
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--batch-id",
        dest="batch_ids",
        action="append",
        help="只回放指定历史失败批次；可重复传入。未提供时回放全部失败批次。",
    )
    parser.add_argument(
        "--scan-current-contract",
        action="store_true",
        help="扫描全部历史证据响应，并选择按当前合同失败的批次，而不只读取历史失败账本。",
    )
    parser.add_argument("--reuse-correction-run-id")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--timeout", type=int, default=180)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_env_file(args.env_file)
    result = replay_evidence_corrections(
        workspace=args.workspace,
        source_run_id=args.source_run_id,
        run_id=args.run_id,
        timeout=args.timeout,
        reuse_correction_run_id=args.reuse_correction_run_id,
        batch_ids=args.batch_ids,
        scan_current_contract=args.scan_current_contract,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 1


def replay_evidence_corrections(
    *,
    workspace: Path,
    source_run_id: str,
    run_id: str,
    timeout: int,
    reuse_correction_run_id: str | None = None,
    batch_ids: list[str] | None = None,
    scan_current_contract: bool = False,
) -> dict[str, Any]:
    source_dir = workspace / "_llm_analysis" / "runs" / source_run_id
    run_dir = workspace / "_llm_analysis" / "evidence_correction_replays" / run_id
    if not source_dir.is_dir():
        raise ValueError(f"来源运行不存在：{source_dir}")
    if run_dir.exists():
        raise ValueError(f"回放 run_id 已存在，不能覆盖：{run_id}")
    started_at = _now()

    source_manifest = _read_json(source_dir / "manifest.json")
    paper = _read_json(source_dir / "input" / "paper.json")
    materials = _read_jsonl(source_dir / "input" / "materials.jsonl")
    projected = _read_jsonl(source_dir / "input" / "projected_cards.jsonl")
    quote_candidates = _read_jsonl(source_dir / "input" / "quote_candidates.jsonl")
    source_plan = _read_json(source_dir / "plan" / "analysis_plan.json")
    profile = load_model_profile(source_dir / "plan" / "model_profile.json")
    historical_failures = _read_jsonl(source_dir / "audit" / "failures.jsonl")
    reuse_dir = (
        workspace / "_llm_analysis" / "evidence_correction_replays" / reuse_correction_run_id
        if reuse_correction_run_id
        else None
    )
    reused_records_by_batch: dict[str, dict[str, Any]] = {}
    if reuse_dir is not None:
        if not reuse_dir.is_dir():
            raise ValueError(f"待复用修正运行不存在：{reuse_dir}")
        reused_records_by_batch = {
            str(row["batch_id"]): row
            for row in _read_jsonl(reuse_dir / "audit" / "records.jsonl")
        }
    planned_by_id = {
        str(row["request_id"]): _planned_request(row)
        for row in source_plan["evidence_batches"]
    }
    material_by_id = {str(row["material_id"]): row for row in materials}
    projected_by_id = {str(row["material_id"]): row for row in projected}
    evidence_failures = (
        _scan_current_contract_failures(
            source_dir=source_dir,
            planned_by_id=planned_by_id,
            material_by_id=material_by_id,
            quote_candidates=quote_candidates,
            generation_id=str(paper["generation_id"]),
        )
        if scan_current_contract
        else [row for row in historical_failures if row.get("stage") == "evidence_batch"]
    )
    if not evidence_failures:
        source = "当前合同扫描" if scan_current_contract else "历史失败账本"
        raise ValueError(f"{source}没有 evidence_batch 失败记录。")
    available_batch_ids = {str(row["batch_id"]) for row in evidence_failures}
    selected_batch_ids = list(dict.fromkeys(batch_ids or []))
    unknown_batch_ids = sorted(set(selected_batch_ids) - available_batch_ids)
    if unknown_batch_ids:
        source = "当前合同失败集合" if scan_current_contract else "来源 evidence 失败记录"
        raise ValueError(f"指定批次不属于{source}：{unknown_batch_ids}")
    if selected_batch_ids:
        selected_set = set(selected_batch_ids)
        evidence_failures = [
            row for row in evidence_failures if str(row["batch_id"]) in selected_set
        ]
    run_dir.mkdir(parents=True)
    snapshot = AnalysisInputSnapshot(
        paper_id=str(paper["paper_id"]),
        paper_title=str(paper["paper_title"]),
        topic=str(paper["topic"]),
        generation_id=str(paper["generation_id"]),
        materials=tuple(materials),
        input_sha256=str(paper["input_sha256"]),
        topic_sha256=str(source_manifest.get("topic_sha256") or _sha256_text(str(paper["topic"]))),
        scope=str(paper["scope"]),
        source_material_count=int(paper["source_material_count"]),
    )
    token_counter = DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
    client = OpenAICompatibleAnalysisClient.from_env(
        model=profile.request_model,
        timeout=timeout,
    )

    _write_json(run_dir / "input" / "paper.json", paper)
    _write_json(run_dir / "input" / "source_manifest.json", source_manifest)
    _write_json(run_dir / "plan" / "model_profile.json", profile.to_dict())
    records: list[dict[str, Any]] = []
    for historical_failure in evidence_failures:
        batch_id = str(historical_failure["batch_id"])
        planned = planned_by_id.get(batch_id)
        if planned is None:
            raise ValueError(f"失败批次不在来源计划中：{batch_id}")
        source_batch_dir = source_dir / "batches" / batch_id
        source_payload = _read_json(source_batch_dir / "parsed_response.json")
        batch_materials = [material_by_id[item] for item in planned.material_ids]
        material_id_set = set(planned.material_ids)
        batch_projected = [projected_by_id[item] for item in planned.material_ids]
        batch_quotes = [
            row
            for row in quote_candidates
            if str(row["material_id"]) in material_id_set
        ]
        replay_batch_dir = run_dir / "batches" / batch_id
        replay_batch_dir.mkdir(parents=True)
        for filename in ("request.json", "raw_response.json", "parsed_response.json", "result.json"):
            source_path = source_batch_dir / filename
            if source_path.exists():
                shutil.copy2(source_path, replay_batch_dir / filename)
        record = {
            "batch_id": batch_id,
            "historical_error_code": str(historical_failure["error_code"]),
            "historical_error_path": historical_failure.get("error_path"),
            "material_ids": list(planned.material_ids),
        }
        try:
            validate_evidence_batch(source_payload, batch_materials, batch_quotes)
        except ContractViolation as current_exc:
            current_failure = _failure(
                "evidence_batch",
                current_exc,
                batch_id=batch_id,
                material_ids=list(planned.material_ids),
            )
            record.update(
                {
                    "current_validation_status": "failed",
                    "current_error_code": current_exc.code,
                    "current_error_path": current_exc.path,
                }
            )
            try:
                local_failures = collect_correctable_material_failures(
                    source_payload,
                    batch_materials,
                    batch_quotes,
                )
            except EvidenceCorrectionUnavailable as exc:
                record.update(
                    {
                        "correction_status": "not_attempted",
                        "correction_error_code": exc.code,
                        "correction_error_message": str(exc),
                    }
                )
            else:
                record["source_failed_units"] = _source_failed_units_for_review(
                    source_payload,
                    local_failures,
                    batch_quotes,
                )
                reused = _reuse_prior_correction_if_valid(
                    reuse_dir=reuse_dir,
                    prior_record=reused_records_by_batch.get(batch_id),
                    run_dir=run_dir,
                    replay_batch_dir=replay_batch_dir,
                    source_payload=source_payload,
                    local_failures=local_failures,
                    batch_materials=batch_materials,
                    batch_quotes=batch_quotes,
                    batch_id=batch_id,
                    current_failure=current_failure,
                    generation_id=snapshot.generation_id,
                )
                if reused is not None:
                    record.update(reused)
                    records.append(record)
                    continue
                outcome = _execute_evidence_correction(
                    batch_dir=replay_batch_dir,
                    client=client,
                    source_planned=planned,
                    source_payload=source_payload,
                    source_failure=current_failure,
                    local_failures=local_failures,
                    snapshot=snapshot,
                    materials=batch_materials,
                    projected=batch_projected,
                    quote_candidates=batch_quotes,
                    profile=profile,
                    token_counter=token_counter,
                )
                resolution = {
                    "schema_version": EVIDENCE_CORRECTION_SCHEMA_VERSION,
                    "source_request_id": batch_id,
                    "source_failure": current_failure,
                    "correction_request_id": outcome["result"]["request_id"],
                    "target_material_ids": outcome["target_material_ids"],
                    "status": "corrected" if outcome["failure"] is None else "correction_failed",
                    "changes": outcome["changes"],
                    "correction_failure": outcome["failure"],
                }
                _write_json(replay_batch_dir / "resolution.json", resolution)
                record.update(
                    {
                        "target_material_ids": outcome["target_material_ids"],
                        "correction_request_id": outcome["result"]["request_id"],
                        "correction_status": resolution["status"],
                        "correction_error_code": (
                            outcome["failure"]["error_code"] if outcome["failure"] else None
                        ),
                        "usage": outcome["result"]["usage"],
                        "changes": outcome["changes"],
                        "corrected_units": _corrected_units_for_review(
                            outcome["evidence_units"],
                            set(outcome["target_material_ids"]),
                        ),
                    }
                )
        else:
            record.update(
                {
                    "current_validation_status": "passed",
                    "current_error_code": None,
                    "current_error_path": None,
                    "correction_status": "not_needed",
                    "correction_error_code": None,
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "changes": [],
                }
            )
        records.append(record)

    unresolved = [
        row
        for row in records
        if row["correction_status"] not in {"not_needed", "corrected", "reused_and_validated"}
    ]
    manifest = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "completed" if not unresolved else "partial",
        "started_at": started_at,
        "finished_at": _now(),
        "source_run_id": source_run_id,
        "reused_correction_run_id": reuse_correction_run_id,
        "failure_selection_mode": (
            "current_contract" if scan_current_contract else "historical_record"
        ),
        "selected_batch_ids": selected_batch_ids,
        "paper_id": snapshot.paper_id,
        "paper_title": snapshot.paper_title,
        "provider": client.provider,
        "model": client.model,
        "historical_failed_batch_count": len(records),
        "current_validator_passed_batch_count": sum(
            row["current_validation_status"] == "passed" for row in records
        ),
        "correction_attempt_count": sum(
            row["correction_status"] in {"corrected", "correction_failed"} for row in records
        ),
        "reused_correction_count": sum(
            row["correction_status"] == "reused_and_validated" for row in records
        ),
        "corrected_batch_count": sum(
            row["correction_status"] in {"corrected", "reused_and_validated"}
            for row in records
        ),
        "unresolved_batch_count": len(unresolved),
        "records": records,
    }
    _write_json(run_dir / "manifest.json", manifest)
    _write_jsonl(run_dir / "audit" / "records.jsonl", records)
    _write_report(run_dir / "review" / "report.md", manifest)
    return {
        "run_id": run_id,
        "status": manifest["status"],
        "historical_failed_batch_count": manifest["historical_failed_batch_count"],
        "current_validator_passed_batch_count": manifest["current_validator_passed_batch_count"],
        "correction_attempt_count": manifest["correction_attempt_count"],
        "reused_correction_count": manifest["reused_correction_count"],
        "corrected_batch_count": manifest["corrected_batch_count"],
        "unresolved_batch_count": manifest["unresolved_batch_count"],
        "run_dir": str(run_dir),
        "report_path": str(run_dir / "review" / "report.md"),
    }


def _reuse_prior_correction_if_valid(
    *,
    reuse_dir: Path | None,
    prior_record: dict[str, Any] | None,
    run_dir: Path,
    replay_batch_dir: Path,
    source_payload: dict[str, Any],
    local_failures: list[dict[str, Any]],
    batch_materials: list[dict[str, Any]],
    batch_quotes: list[dict[str, Any]],
    batch_id: str,
    current_failure: dict[str, Any],
    generation_id: str,
) -> dict[str, Any] | None:
    if reuse_dir is None or not prior_record or not prior_record.get("correction_request_id"):
        return None
    correction_request_id = str(prior_record["correction_request_id"])
    source_correction_dir = reuse_dir / "batches" / correction_request_id
    parsed_path = source_correction_dir / "parsed_response.json"
    if not parsed_path.exists():
        return None
    target_material_ids = [str(row["material_id"]) for row in local_failures]
    if sorted(target_material_ids) != sorted(
        str(item) for item in prior_record.get("target_material_ids", [])
    ):
        return None
    try:
        applied = validate_and_apply_evidence_correction(
            _read_json(parsed_path),
            source_payload=source_payload,
            target_material_ids=target_material_ids,
            materials=batch_materials,
            quote_candidates=batch_quotes,
            failures=local_failures,
        )
        assign_evidence_unit_ids(
            correction_request_id,
            applied["normalized"]["evidence_units"],
            batch_materials,
            generation_id=generation_id,
        )
    except ContractViolation:
        return None
    target_correction_dir = run_dir / "batches" / correction_request_id
    shutil.copytree(source_correction_dir, target_correction_dir)
    _write_json(target_correction_dir / "revalidation.json", applied)
    _write_json(
        replay_batch_dir / "resolution.json",
        {
            "schema_version": EVIDENCE_CORRECTION_SCHEMA_VERSION,
            "status": "corrected",
            "source_request_id": batch_id,
            "source_failure": current_failure,
            "correction_request_id": correction_request_id,
            "target_material_ids": target_material_ids,
            "changes": applied["changes"],
            "reused_from_correction_run_id": reuse_dir.name,
        },
    )
    return {
        "target_material_ids": target_material_ids,
        "correction_request_id": correction_request_id,
        "correction_status": "reused_and_validated",
        "correction_error_code": None,
        "reused_from_correction_run_id": reuse_dir.name,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "changes": applied["changes"],
        "corrected_units": _corrected_units_for_review(
            applied["normalized"]["evidence_units"],
            set(target_material_ids),
        ),
    }


def _scan_current_contract_failures(
    *,
    source_dir: Path,
    planned_by_id: dict[str, PlannedRequest],
    material_by_id: dict[str, dict[str, Any]],
    quote_candidates: list[dict[str, Any]],
    generation_id: str,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for batch_id, planned in planned_by_id.items():
        parsed_path = source_dir / "batches" / batch_id / "parsed_response.json"
        if not parsed_path.exists():
            continue
        material_ids = set(planned.material_ids)
        materials = [material_by_id[item] for item in planned.material_ids]
        quotes = [
            row
            for row in quote_candidates
            if str(row["material_id"]) in material_ids
        ]
        try:
            normalized = validate_evidence_batch(
                _read_json(parsed_path),
                materials,
                quotes,
            )
            assign_evidence_unit_ids(
                batch_id,
                normalized["evidence_units"],
                materials,
                generation_id=generation_id,
            )
        except ContractViolation as exc:
            failures.append(
                {
                    "stage": "evidence_batch",
                    "batch_id": batch_id,
                    "error_code": exc.code,
                    "error_path": exc.path,
                    "error_message": str(exc),
                }
            )
    return failures


def _planned_request(row: dict[str, Any]) -> PlannedRequest:
    return PlannedRequest(
        request_id=str(row["request_id"]),
        stage=str(row["stage"]),
        section_ids=tuple(str(item) for item in row["section_ids"]),
        material_ids=tuple(str(item) for item in row["material_ids"]),
        input_tokens=int(row["input_tokens"]),
        max_output_tokens=int(row["max_output_tokens"]),
        safety_margin_tokens=int(row["safety_margin_tokens"]),
        split_reason=str(row["split_reason"]),
        prompt_sha256=str(row["prompt_sha256"]),
    )


def _write_report(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# 证据抽取失败批次一次性修正回放",
        "",
        f"- 来源运行：`{manifest['source_run_id']}`",
        f"- 论文：{manifest['paper_title']}",
        f"- 失败选择方式：`{manifest['failure_selection_mode']}`",
        f"- 回放状态：{manifest['status']}",
        f"- 历史失败批次：{manifest['historical_failed_batch_count']}",
        f"- 当前规则已直接通过：{manifest['current_validator_passed_batch_count']}",
        f"- 实际修正请求：{manifest['correction_attempt_count']}",
        f"- 复用既有响应并重验通过：{manifest['reused_correction_count']}",
        f"- 修正成功：{manifest['corrected_batch_count']}",
        f"- 仍未解决：{manifest['unresolved_batch_count']}",
        "",
        "## 批次结果",
        "",
        "| 历史批次 | 当前校验 | 修正状态 | 目标 Card 数 | 最终错误 |",
        "|---|---|---|---:|---|",
    ]
    for row in manifest["records"]:
        lines.append(
            "| {batch} | {current} | {correction} | {count} | {error} |".format(
                batch=row["batch_id"],
                current=row["current_validation_status"],
                correction=row["correction_status"],
                count=len(row.get("target_material_ids", [])),
                error=row.get("correction_error_code") or "无",
            )
        )
    lines.extend(["", "## 修正动作", ""])
    for row in manifest["records"]:
        if not row.get("changes"):
            continue
        lines.append(f"### {row['batch_id']}")
        lines.append("")
        for change in row["changes"]:
            lines.append(
                "- Card `{material_id}` 的原证据 #{index}：`{action}`，替换数 {count}。原因：{reason}".format(
                    material_id=change["material_id"],
                    index=change["source_evidence_index"],
                    action=change["action"],
                    count=change["replacement_count"],
                    reason=change["reason"],
                )
            )
        source_units = row.get("source_failed_units", [])
        corrected_units = row.get("corrected_units", [])
        if source_units:
            lines.extend(["", "#### 修正前失败观点", ""])
            for index, unit in enumerate(source_units, start=1):
                lines.append(
                    f"{index}. Card `{unit['material_id']}` 原证据 "
                    f"#{unit['source_evidence_index']}：{unit['claim']}"
                )
                for citation in unit["citations"]:
                    lines.append(f"   - `{citation['quote_id']}`：{citation['quote']}")
        if corrected_units:
            lines.extend(["", "#### 修正后有效观点", ""])
            for index, unit in enumerate(corrected_units, start=1):
                lines.append(f"{index}. {unit['claim']}")
                for citation in unit["citations"]:
                    lines.append(f"   - `{citation['quote_id']}`：{citation['quote']}")
        lines.append("")
    lines.extend(
        [
            "## 审计说明",
            "",
            "历史运行未被修改。本回放只读取历史输入和响应；当前规则可直接通过的批次不调用模型；其余批次只允许一次 `evidence_batch_correction` 请求。",
            "修正结果已重新执行整批 Schema、Card 覆盖、同 Card 引文、数值与限定词校验。第二次失败保留为未解决，不再循环。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _source_failed_units_for_review(
    source_payload: dict[str, Any],
    failures: list[dict[str, Any]],
    quote_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results_by_id = {
        str(row["material_id"]): row for row in source_payload["material_results"]
    }
    quote_text_by_id = {
        str(row["quote_id"]): str(row["text"]) for row in quote_candidates
    }
    units: list[dict[str, Any]] = []
    for failure in failures:
        material_id = str(failure["material_id"])
        source_units = results_by_id[material_id]["evidence_units"]
        for violation in failure.get("violations", []):
            source_index = violation.get("source_evidence_index")
            if not isinstance(source_index, int):
                continue
            unit = source_units[source_index]
            units.append(
                {
                    "material_id": material_id,
                    "source_evidence_index": source_index,
                    "claim": str(unit["claim"]),
                    "citations": [
                        {
                            "quote_id": str(citation["quote_id"]),
                            "quote": quote_text_by_id[str(citation["quote_id"])],
                        }
                        for citation in unit["citations"]
                    ],
                }
            )
    return units


def _corrected_units_for_review(
    evidence_units: list[dict[str, Any]],
    target_material_ids: set[str],
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for unit in evidence_units:
        citations = list(unit.get("citations", []))
        if not citations or not all(
            str(citation.get("material_id", "")) in target_material_ids
            for citation in citations
        ):
            continue
        units.append(
            {
                "evidence_unit_id": unit.get("evidence_unit_id"),
                "claim": str(unit["claim"]),
                "citations": [
                    {
                        "quote_id": str(citation["quote_id"]),
                        "quote": str(citation["quote"]),
                    }
                    for citation in citations
                ],
            }
        )
    return units


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
