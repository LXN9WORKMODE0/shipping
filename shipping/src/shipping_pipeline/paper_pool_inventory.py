from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PAPER_POOL_INVENTORY_ROOT = "_paper_pool_inventories"
PAPER_POOL_INVENTORY_SCHEMA_VERSION = "paper_pool.inventory.v1"
PAPER_POOL_INVENTORY_RUN_SCHEMA_VERSION = "paper_pool.inventory_run.v1"
SUPPORTED_SOURCE_EXTENSIONS = {".md", ".pdf"}
KNOWN_SOURCE_EXTENSIONS = SUPPORTED_SOURCE_EXTENSIONS | {".caj"}


class PaperPoolInventoryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


class PaperPoolInventoryRunner:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(
        self,
        source_dir: str | Path,
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        source_root = Path(source_dir).resolve()
        if not source_root.is_dir():
            raise PaperPoolInventoryError(
                "paper_pool.source_dir_invalid", f"论文源目录不存在：{source_root}"
            )
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / PAPER_POOL_INVENTORY_ROOT / "runs" / resolved
        if run_dir.exists():
            raise PaperPoolInventoryError(
                "paper_pool.run_exists", f"run_id已存在：{resolved}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        try:
            card_runs = _scan_completed_card_runs(self.workspace)
            records = _build_inventory(source_root, card_runs)
            summary = _summarize(records, card_runs)
            inventory = {
                "schema_version": PAPER_POOL_INVENTORY_SCHEMA_VERSION,
                "run_id": resolved,
                "source_dir": str(source_root),
                "summary": summary,
                "records": records,
                "inventory_sha256": _sha256_json(records),
            }
            _write_json(run_dir / "output" / "paper_pool_inventory.json", inventory)
            _write_jsonl(run_dir / "output" / "paper_pool_inventory.jsonl", records)
            _write_text(run_dir / "review" / "paper_pool_inventory.md", _render_report(inventory))
            manifest = {
                "schema_version": PAPER_POOL_INVENTORY_RUN_SCHEMA_VERSION,
                "run_id": resolved,
                "status": "completed",
                "started_at": started_at,
                "finished_at": _now(),
                "source_dir": str(source_root),
                "inventory_sha256": inventory["inventory_sha256"],
                "summary": summary,
                "failure": None,
            }
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            manifest = {
                "schema_version": PAPER_POOL_INVENTORY_RUN_SCHEMA_VERSION,
                "run_id": resolved,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "source_dir": str(source_root),
                "inventory_sha256": None,
                "summary": None,
                "failure": failure,
            }
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": manifest["status"],
            "run_dir": str(run_dir),
            "summary": manifest["summary"],
            "report_path": (
                str(run_dir / "review" / "paper_pool_inventory.md")
                if manifest["status"] == "completed" else None
            ),
            "failure": manifest["failure"],
        }


def load_paper_pool_inventory(
    workspace: str | Path, run_id: str
) -> dict[str, Any]:
    _safe_segment(run_id)
    run_dir = Path(workspace) / PAPER_POOL_INVENTORY_ROOT / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json")
    inventory = _read_json(run_dir / "output" / "paper_pool_inventory.json")
    if (
        manifest.get("schema_version") != PAPER_POOL_INVENTORY_RUN_SCHEMA_VERSION
        or manifest.get("status") != "completed"
        or manifest.get("run_id") != run_id
        or inventory.get("schema_version") != PAPER_POOL_INVENTORY_SCHEMA_VERSION
        or inventory.get("run_id") != run_id
        or manifest.get("inventory_sha256") != inventory.get("inventory_sha256")
        or inventory.get("inventory_sha256") != _sha256_json(inventory.get("records"))
    ):
        raise PaperPoolInventoryError(
            "paper_pool.inventory_replay_mismatch", "论文池清点运行无法重放。"
        )
    return inventory


def _build_inventory(source_root: Path, card_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paths = sorted(
        (path for path in source_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source_root).as_posix().casefold(),
    )
    source_rows = []
    for path in paths:
        relative = path.relative_to(source_root).as_posix()
        digest = _sha256_file(path)
        source_rows.append({
            "record_id": "source_" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20],
            "content_id": "paper_" + digest.split(":", 1)[1][:20],
            "relative_path": relative,
            "filename": path.name,
            "paper_title": path.stem,
            "extension": path.suffix.lower(),
            "size_bytes": path.stat().st_size,
            "sha256": digest,
        })
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        by_hash[row["sha256"]].append(row)
    card_by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in card_runs:
        card_by_hash[row["source_sha256"]].append(row)
    title_to_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in card_runs:
        title_to_runs[row["paper_id"].strip().casefold()].append(row)

    records = []
    for row in source_rows:
        duplicate_group = by_hash[row["sha256"]]
        canonical = duplicate_group[0]
        extension = row["extension"]
        exact_runs = card_by_hash.get(row["sha256"], [])
        possible_title_runs = title_to_runs.get(row["paper_title"].strip().casefold(), [])
        if row["record_id"] != canonical["record_id"]:
            processing_status = "duplicate_source"
        elif extension not in KNOWN_SOURCE_EXTENSIONS:
            processing_status = "unsupported_extension"
        elif extension not in SUPPORTED_SOURCE_EXTENSIONS:
            processing_status = "unsupported_format"
        elif len(exact_runs) == 1:
            processing_status = "card_ready"
        elif len(exact_runs) > 1:
            processing_status = "card_run_ambiguous"
        else:
            processing_status = "needs_card"
        records.append({
            **row,
            "is_canonical_content": row["record_id"] == canonical["record_id"],
            "duplicate_of_record_id": (
                None if row["record_id"] == canonical["record_id"] else canonical["record_id"]
            ),
            "duplicate_group_size": len(duplicate_group),
            "processing_status": processing_status,
            "exact_card_runs": exact_runs,
            "possible_title_match_workspace_ids": sorted({
                item["workspace_paper_id"] for item in possible_title_runs
                if item not in exact_runs
            }),
        })
    return records


def _scan_completed_card_runs(workspace: Path) -> list[dict[str, Any]]:
    rows = []
    if not workspace.is_dir():
        return rows
    for run_path in sorted(workspace.glob("*/run.json"), key=lambda path: path.as_posix()):
        try:
            run = _read_json(run_path)
            current = _read_json(run_path.parent / "materials" / "current.json")
        except PaperPoolInventoryError:
            continue
        if run.get("status") != "completed" or current.get("status") != "completed":
            continue
        generation_id = str(run.get("generation_id") or "")
        if not generation_id or current.get("generation_id") != generation_id:
            continue
        source = Path(str(run.get("source") or ""))
        if not source.is_file():
            continue
        rows.append({
            "workspace_paper_id": run_path.parent.name,
            "paper_id": str(run.get("paper_id") or ""),
            "generation_id": generation_id,
            "source_sha256": _sha256_file(source),
            "structure_quality": run.get("structure_quality"),
        })
    return rows


def _summarize(records: list[dict[str, Any]], card_runs: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    extensions: dict[str, int] = defaultdict(int)
    for row in records:
        counts[row["processing_status"]] += 1
        extensions[row["extension"] or "<none>"] += 1
    canonical = [row for row in records if row["is_canonical_content"]]
    return {
        "source_file_count": len(records),
        "unique_content_count": len(canonical),
        "duplicate_alias_count": len(records) - len(canonical),
        "extension_counts": dict(sorted(extensions.items())),
        "processing_status_counts": dict(sorted(counts.items())),
        "completed_card_run_count_scanned": len(card_runs),
        "canonical_ready_count": sum(row["processing_status"] == "card_ready" for row in canonical),
        "canonical_needs_card_count": sum(row["processing_status"] == "needs_card" for row in canonical),
        "canonical_blocked_count": sum(
            row["processing_status"] in {
                "unsupported_extension", "unsupported_format", "card_run_ambiguous"
            }
            for row in canonical
        ),
    }


def _render_report(inventory: dict[str, Any]) -> str:
    summary = inventory["summary"]
    lines = [
        "# 全论文池清点报告", "",
        f"- 源目录：`{inventory['source_dir']}`",
        f"- 文件总数：{summary['source_file_count']}",
        f"- 唯一内容：{summary['unique_content_count']}",
        f"- 重复别名：{summary['duplicate_alias_count']}",
        f"- 唯一内容中Card已就绪：{summary['canonical_ready_count']}",
        f"- 唯一内容中待制卡：{summary['canonical_needs_card_count']}",
        f"- 唯一内容中阻断：{summary['canonical_blocked_count']}", "",
        "## 处理状态", "", "| 状态 | 数量 |", "|---|---:|",
    ]
    for status, count in summary["processing_status_counts"].items():
        lines.append(f"| `{status}` | {count} |")
    lines.extend(["", "## 阻断与重复", ""])
    exceptional = [
        row for row in inventory["records"]
        if row["processing_status"] not in {"card_ready", "needs_card"}
    ]
    if not exceptional:
        lines.append("- 无。")
    for row in exceptional:
        lines.append(
            f"- `{row['processing_status']}`：{row['relative_path']}"
            + (f"，对应 `{row['duplicate_of_record_id']}`" if row["duplicate_of_record_id"] else "")
        )
    lines.extend([
        "", "## 解释", "",
        "- `card_ready`仅表示源文件哈希唯一匹配一个当前成功Card运行。",
        "- 同名只能列为可能匹配，不会被当作已制卡。",
        "- `needs_card`和阻断论文仍保留在总账中，不会从后续统计中消失。", "",
    ])
    return "\n".join(lines)


def _safe_segment(value: str) -> None:
    if not value or Path(value).name != value or value in {".", ".."}:
        raise PaperPoolInventoryError("paper_pool.run_id_invalid", "run_id不是安全路径段。")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperPoolInventoryError("paper_pool.json_read_failed", f"无法读取JSON：{path}") from exc
    if not isinstance(value, dict):
        raise PaperPoolInventoryError("paper_pool.json_object_required", "JSON必须是对象。")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()
