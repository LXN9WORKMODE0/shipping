"""Staged corpus audit for the progressive-disclosure workflow."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.clients import LLMClient, MinerUClient
from src.core.paths import DOCS_DIR, PAPERS_DIR, PROJECT_ROOT, WORKSPACE_DIR
from src.parsing import DocumentStructureBuilder
from src.pipeline import ConvertPaperUseCase, GenerateSummaryUseCase, ParseStructureUseCase
from src.pipeline.summarize import SUMMARY_VERSION
from src.workspace import WorkspaceRepository

XS_BUCKET = "xs(<200KB)"
S_BUCKET = "s(200KB-1MB)"
M_BUCKET = "m(1MB-5MB)"
L_BUCKET = "l(5MB-15MB)"
XL_BUCKET = "xl(>15MB)"
REVIEW_DIMENSIONS = [
    "role 是否把封面、声明、目录、参考文献、附录、致谢分对",
    "章节层级是否明显压扁或跳级",
    "diagnostics 是否真的解释了低置信原因",
    "overview.quick_take 是否能快速概括全文",
    "review_notes.body_section_notes 是否适合后续综述写作",
]
SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
ISSUE_METADATA: dict[str, tuple[str, str, str]] = {
    "cli_check_failed": ("P0", "CLI 预检失败", "基础命令 `python main.py check` 未通过，无法信任后续样本结果。"),
    "mineru_health_failed": ("P0", "MinerU 健康检查失败", "MinerU 不可用时，本轮只能输出环境阻塞结论。"),
    "llm_health_failed": ("P0", "LLM 健康检查失败", "LLM 不可用时，phase3 refinement 和 phase4 摘要都无法完整审计。"),
    "preflight_failed": ("P0", "预跑未稳定通过", "3 篇预跑未稳定通过，继续扩大样本会放大环境或系统级问题。"),
    "parse_failed": ("P1", "Parse 阶段失败", "结构树未能稳定落盘，直接影响后续综述使用。"),
    "parse_no_headings": ("P1", "规则解析未识别到标题", "Markdown 标题识别失败，说明 heading 规则对真实文档覆盖不足。"),
    "parse_structure_missing": ("P1", "structure.json 缺失", "解析阶段完成后核心结构产物缺失。"),
    "parse_diagnostics_missing": ("P1", "diagnostics.json 缺失", "低置信诊断产物缺失，无法解释结构风险。"),
    "parse_outline_missing": ("P1", "outline.json 缺失", "调试和人工复核所需的 outline 缺失。"),
    "parse_outline_mismatch": ("P1", "outline 与 structure 不一致", "outline parent/child 关系与结构树不一致，会误导后续审查。"),
    "parse_no_body_nodes": ("P1", "正文节点缺失", "解析结果只有前置页或特殊节点，正文结构不可用。"),
    "parse_low_confidence_high": ("P1", "低置信节点占比异常", "大批节点仍处于低置信状态，结构质量不足以直接支撑综述。"),
    "convert_markdown_short": ("P2", "转换 Markdown 过短", "MinerU 输出为空或极短，后续结构和摘要质量都会退化。"),
    "convert_missing_full_md": ("P2", "缺少 full.md", "转换阶段没有生成原始 Markdown 主产物。"),
    "summary_failed": ("P2", "Summary 阶段失败", "渐进式披露产物没有稳定生成。"),
    "summary_artifacts_missing": ("P2", "摘要产物缺失", "summary_tree / overview / review_notes 不完整。"),
    "summary_version_mismatch": ("P2", "摘要版本不一致", "phase4 产物版本不一致，说明派生链不稳定。"),
    "summary_root_mismatch": ("P2", "摘要树根节点数不匹配", "summary_tree 与 body 章级根节点数不一致。"),
    "summary_depends_on_sections": ("P2", "摘要仍依赖 sections", "删除 sections 后无法重建摘要，违反 phase4 目标。"),
    "parse_refinement_fallback_not_recorded": ("P2", "Refinement fallback 未落盘", "LLM refinement 失败后没有在 diagnostics/run 里留下证据。"),
    "convert_timeout": ("P3", "Convert 超时", "转换超时更偏向性能或调度问题，但会阻碍大样本审计。"),
    "convert_failed": ("P3", "Convert 阶段失败", "转换链路存在操作性问题，需要进一步分类。"),
    "summary_stage_missing": ("P3", "run.json 未记录 summary 阶段", "工作区状态记录不完整，影响审计和排障。"),
    "slow_processing": ("P3", "处理耗时偏高", "单篇耗时明显偏高，需要关注超时和吞吐。"),
}


@dataclass(frozen=True)
class PaperCandidate:
    """Inventory entry for a single PDF."""

    paper_id: str
    pdf_name: str
    pdf_path: Path
    size_bytes: int
    bucket: str
    category_guess: str
    duplicate_key: str
    is_duplicate: bool

    def to_dict(self, project_root: Path) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "pdf_name": self.pdf_name,
            "pdf_path": relativize(self.pdf_path, project_root),
            "size_bytes": self.size_bytes,
            "bucket": self.bucket,
            "category_guess": self.category_guess,
            "duplicate_key": self.duplicate_key,
            "is_duplicate": self.is_duplicate,
        }


@dataclass
class AuditRecord:
    """Structured result for one audited sample."""

    paper_id: str
    pdf_name: str
    size_bytes: int
    bucket: str
    category_guess: str
    sample_phase: str
    stage_reached: str = "preflight"
    durations: dict[str, float] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    parse_warnings: list[str] = field(default_factory=list)
    low_confidence_count: int = 0
    failure_stage: str | None = None
    failure_type: str | None = None
    exception_summary: str | None = None
    manual_review_notes: dict[str, Any] = field(
        default_factory=lambda: {"selected_for_review": False, "review_result": None, "notes": []}
    )
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "pdf_name": self.pdf_name,
            "size_bytes": self.size_bytes,
            "bucket": self.bucket,
            "category_guess": self.category_guess,
            "sample_phase": self.sample_phase,
            "stage_reached": self.stage_reached,
            "durations": self.durations,
            "artifacts": self.artifacts,
            "parse_warnings": self.parse_warnings,
            "low_confidence_count": self.low_confidence_count,
            "failure_stage": self.failure_stage,
            "failure_type": self.failure_type,
            "exception_summary": self.exception_summary,
            "manual_review_notes": self.manual_review_notes,
            "checks": self.checks,
        }


@dataclass(frozen=True)
class CommandCheckResult:
    """Captured result for `python main.py check`."""

    exit_code: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return {"exit_code": self.exit_code, "stdout": self.stdout, "stderr": self.stderr}


@dataclass
class AuditConfig:
    """Runtime configuration for the audit."""

    project_root: Path = PROJECT_ROOT
    papers_dir: Path = PAPERS_DIR
    workspace_dir: Path = WORKSPACE_DIR
    docs_dir: Path = DOCS_DIR
    audit_root: Path = WORKSPACE_DIR / "_audit"
    preflight_count: int = 3
    pilot_count: int = 12
    main_count: int = 36
    review_count: int = 10
    check_only: bool = False
    skip_main: bool = False
    short_markdown_chars: int = 200
    high_low_confidence_ratio: float = 0.4
    high_low_confidence_min: int = 8
    pilot_success_threshold: float = 0.75
    repeated_failure_threshold: int = 3
    slow_total_seconds: float = 180.0
    verify_summary_independence: bool = True


@dataclass
class AuditServices:
    """Concrete services used by the audit runner."""

    repository: WorkspaceRepository
    conversion_client: Any
    llm_client: Any
    convert_use_case: ConvertPaperUseCase
    parse_use_case: ParseStructureUseCase
    summarize_use_case: GenerateSummaryUseCase


def bucket_for_size(size_bytes: int) -> str:
    """Return the size bucket label for a PDF."""
    if size_bytes < 200 * 1024:
        return XS_BUCKET
    if size_bytes < 1024 * 1024:
        return S_BUCKET
    if size_bytes < 5 * 1024 * 1024:
        return M_BUCKET
    if size_bytes < 15 * 1024 * 1024:
        return L_BUCKET
    return XL_BUCKET


def guess_category(pdf_name: str, size_bytes: int) -> str:
    """Heuristic category guess from file name and size."""
    stem = Path(pdf_name).stem
    lowered = stem.lower()

    thesis_keywords = ("学位论文", "硕士", "博士", "毕业论文", "dissertation", "thesis")
    news_keywords = ("人民日报", "新闻报道", "新闻传播", "报纸", "媒体报道", "舆情")
    journal_keywords = ("学报", "期刊", "journal", "综述", "实证", "研究", "分析", "评价", "机制")

    if any(keyword in stem or keyword in lowered for keyword in thesis_keywords):
        return "thesis_like"
    if any(keyword in stem or keyword in lowered for keyword in news_keywords):
        return "news_like"
    if any(keyword in stem or keyword in lowered for keyword in journal_keywords):
        if size_bytes >= 8 * 1024 * 1024 and ("设计" in stem or "系统" in stem or "实现" in stem):
            return "thesis_like"
        return "journal_like"
    if size_bytes >= 15 * 1024 * 1024:
        return "thesis_like"
    return "unknown"


def normalize_duplicate_key(stem: str) -> str:
    """Collapse obvious duplicate suffixes such as `_1` or `(1)`."""
    base = stem.strip()
    for pattern in (r"_[0-9]+$", r"\([0-9]+\)$", r"（[0-9]+）$"):
        base = re.sub(pattern, "", base)
    return base.strip().lower()


def build_inventory(papers_dir: Path) -> list[PaperCandidate]:
    """Scan papers directory and build the audit inventory."""
    pdf_paths = sorted(papers_dir.rglob("*.pdf"), key=lambda path: str(path).lower())
    entries: list[dict[str, Any]] = []
    duplicate_counts: Counter[str] = Counter()
    for pdf_path in pdf_paths:
        duplicate_key = normalize_duplicate_key(pdf_path.stem)
        duplicate_counts[duplicate_key] += 1
        size_bytes = pdf_path.stat().st_size
        entries.append(
            {
                "paper_id": pdf_path.stem,
                "pdf_name": pdf_path.name,
                "pdf_path": pdf_path,
                "size_bytes": size_bytes,
                "bucket": bucket_for_size(size_bytes),
                "category_guess": guess_category(pdf_path.name, size_bytes),
                "duplicate_key": duplicate_key,
            }
        )

    return [
        PaperCandidate(
            paper_id=entry["paper_id"],
            pdf_name=entry["pdf_name"],
            pdf_path=entry["pdf_path"],
            size_bytes=entry["size_bytes"],
            bucket=entry["bucket"],
            category_guess=entry["category_guess"],
            duplicate_key=entry["duplicate_key"],
            is_duplicate=duplicate_counts[entry["duplicate_key"]] > 1,
        )
        for entry in entries
    ]


def inventory_summary(candidates: Sequence[PaperCandidate]) -> dict[str, Any]:
    """Aggregate inventory-level statistics."""
    bucket_counts = Counter(candidate.bucket for candidate in candidates)
    category_counts = Counter(candidate.category_guess for candidate in candidates)
    duplicate_groups: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        duplicate_groups[candidate.duplicate_key].append(candidate.pdf_name)

    duplicates = {key: sorted(names) for key, names in duplicate_groups.items() if len(names) > 1}
    return {
        "total_pdfs": len(candidates),
        "bucket_counts": dict(bucket_counts),
        "category_counts": dict(category_counts),
        "duplicate_group_count": len(duplicates),
        "duplicate_file_count": sum(len(names) for names in duplicates.values()),
        "duplicate_groups": duplicates,
    }


def build_audit_services(
    papers_dir: Path | None = None,
    workspace_dir: Path | None = None,
    conversion_client: Any | None = None,
    llm_client: Any | None = None,
) -> AuditServices:
    """Build repository, clients, and use cases for the audit."""
    repository = WorkspaceRepository(papers_dir=papers_dir, workspace_dir=workspace_dir)
    conversion = conversion_client or MinerUClient()
    llm = llm_client or LLMClient()
    parse_use_case = ParseStructureUseCase(
        repository=repository,
        builder=DocumentStructureBuilder(),
        llm_client=llm,
        refinement_enabled=True,
    )
    convert_use_case = ConvertPaperUseCase(
        repository=repository,
        conversion_client=conversion,
        parse_use_case=parse_use_case,
    )
    summarize_use_case = GenerateSummaryUseCase(repository=repository, llm_client=llm)
    return AuditServices(
        repository=repository,
        conversion_client=conversion,
        llm_client=llm,
        convert_use_case=convert_use_case,
        parse_use_case=parse_use_case,
        summarize_use_case=summarize_use_case,
    )


def run_cli_check(project_root: Path) -> CommandCheckResult:
    """Run `python main.py check` and capture its output."""
    completed = subprocess.run(
        [sys.executable, "main.py", "check"],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return CommandCheckResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_environment_precheck(
    config: AuditConfig,
    services: AuditServices,
    cli_check_runner: Callable[[Path], CommandCheckResult],
) -> dict[str, Any]:
    """Run CLI and direct health checks before touching any sample."""
    cli_result = cli_check_runner(config.project_root)
    mineru_ok = bool(services.conversion_client.health_check())
    llm_ok = bool(services.llm_client.health_check())
    blockers: list[str] = []
    if cli_result.exit_code != 0:
        blockers.append("CLI check command failed")
    if not mineru_ok:
        blockers.append("MinerU health check failed")
    if not llm_ok:
        blockers.append("LLM health check failed")
    return {
        "cli_check": cli_result.to_dict(),
        "mineru_health": mineru_ok,
        "llm_health": llm_ok,
        "blockers": blockers,
        "can_run_samples": not blockers,
        "check_only": config.check_only,
    }


def add_candidate(
    selected: list[PaperCandidate],
    pool: Sequence[PaperCandidate],
    *,
    predicate: Callable[[PaperCandidate], bool] | None = None,
    prefer: str = "largest",
    distinct_bucket: bool = False,
    distinct_category: bool = False,
    avoid_duplicate_groups: bool = True,
) -> None:
    """Append one candidate if any match the selector."""
    used_ids = {candidate.paper_id for candidate in selected}
    used_buckets = {candidate.bucket for candidate in selected}
    used_categories = {candidate.category_guess for candidate in selected}
    used_duplicate_groups = {candidate.duplicate_key for candidate in selected}

    options = [candidate for candidate in pool if candidate.paper_id not in used_ids]
    if predicate is not None:
        options = [candidate for candidate in options if predicate(candidate)]
    if distinct_bucket:
        distinct_options = [candidate for candidate in options if candidate.bucket not in used_buckets]
        if distinct_options:
            options = distinct_options
    if distinct_category:
        distinct_options = [candidate for candidate in options if candidate.category_guess not in used_categories]
        if distinct_options:
            options = distinct_options
    if avoid_duplicate_groups:
        deduped = [candidate for candidate in options if candidate.duplicate_key not in used_duplicate_groups]
        if deduped:
            options = deduped
    if not options:
        return

    if prefer == "smallest":
        choice = min(options, key=lambda candidate: (candidate.size_bytes, candidate.pdf_name.lower()))
    else:
        choice = max(options, key=lambda candidate: (candidate.size_bytes, candidate.pdf_name.lower()))
    selected.append(choice)


def stratified_fill(
    selected: list[PaperCandidate],
    pool: Sequence[PaperCandidate],
    count: int,
    *,
    avoid_duplicate_groups: bool = True,
) -> list[PaperCandidate]:
    """Fill the selection by round-robin over `(bucket, category)` strata."""
    used_ids = {candidate.paper_id for candidate in selected}
    used_groups = {candidate.duplicate_key for candidate in selected}
    groups: dict[tuple[str, str], list[PaperCandidate]] = defaultdict(list)
    for candidate in pool:
        if candidate.paper_id in used_ids:
            continue
        groups[(candidate.bucket, candidate.category_guess)].append(candidate)
    for items in groups.values():
        items.sort(key=lambda candidate: (-candidate.size_bytes, candidate.pdf_name.lower()))

    while len(selected) < count:
        progressed = False
        for key in sorted(groups):
            items = groups[key]
            while items:
                candidate = items.pop(0)
                if candidate.paper_id in used_ids:
                    continue
                if avoid_duplicate_groups and candidate.duplicate_key in used_groups:
                    continue
                selected.append(candidate)
                used_ids.add(candidate.paper_id)
                used_groups.add(candidate.duplicate_key)
                progressed = True
                break
            if len(selected) >= count:
                break
        if not progressed:
            break
    return selected


def select_preflight_samples(
    candidates: Sequence[PaperCandidate],
    count: int,
    excluded_ids: set[str] | None = None,
) -> list[PaperCandidate]:
    """Pick clearly different PDFs for the first real run."""
    excluded_ids = excluded_ids or set()
    pool = [candidate for candidate in candidates if candidate.paper_id not in excluded_ids]
    selected: list[PaperCandidate] = []
    moderate_buckets = {S_BUCKET, M_BUCKET}
    add_candidate(
        selected,
        pool,
        predicate=lambda candidate: candidate.category_guess == "thesis_like" and candidate.bucket in moderate_buckets and not candidate.is_duplicate,
        prefer="smallest",
        distinct_bucket=True,
        distinct_category=True,
    )
    add_candidate(
        selected,
        pool,
        predicate=lambda candidate: candidate.category_guess == "journal_like" and candidate.bucket in moderate_buckets and not candidate.is_duplicate,
        prefer="smallest",
        distinct_bucket=True,
        distinct_category=True,
    )
    add_candidate(
        selected,
        pool,
        predicate=lambda candidate: candidate.category_guess in {"news_like", "unknown"} and candidate.bucket in moderate_buckets and not candidate.is_duplicate,
        prefer="smallest",
        distinct_bucket=True,
        distinct_category=True,
    )
    add_candidate(selected, pool, predicate=lambda candidate: candidate.bucket == XS_BUCKET and not candidate.is_duplicate, prefer="smallest", distinct_bucket=True)
    fast_pool = [candidate for candidate in pool if candidate.bucket in {XS_BUCKET, S_BUCKET, M_BUCKET}]
    stratified_fill(selected, fast_pool, count, avoid_duplicate_groups=True)
    if len(selected) < count:
        stratified_fill(selected, pool, count, avoid_duplicate_groups=True)
    return selected[:count]


def select_pilot_samples(
    candidates: Sequence[PaperCandidate],
    count: int,
    excluded_ids: set[str] | None = None,
) -> list[PaperCandidate]:
    """Build the pilot sample set with required edge cases."""
    excluded_ids = excluded_ids or set()
    pool = [candidate for candidate in candidates if candidate.paper_id not in excluded_ids]
    selected: list[PaperCandidate] = []
    add_candidate(selected, pool, predicate=lambda candidate: candidate.category_guess == "thesis_like" and not candidate.is_duplicate, distinct_bucket=True, distinct_category=True)
    add_candidate(selected, pool, predicate=lambda candidate: candidate.category_guess == "journal_like" and not candidate.is_duplicate, distinct_bucket=True, distinct_category=True)
    add_candidate(selected, pool, predicate=lambda candidate: candidate.category_guess == "news_like" and not candidate.is_duplicate, distinct_bucket=True, distinct_category=True)
    add_candidate(selected, pool, predicate=lambda candidate: candidate.bucket == XL_BUCKET and not candidate.is_duplicate, distinct_bucket=True)
    add_candidate(selected, pool, predicate=lambda candidate: candidate.bucket == XS_BUCKET and not candidate.is_duplicate, prefer="smallest", distinct_bucket=True)
    add_candidate(selected, pool, predicate=lambda candidate: candidate.is_duplicate, distinct_bucket=True, avoid_duplicate_groups=False)
    add_candidate(selected, pool, predicate=lambda candidate: candidate.bucket == L_BUCKET and not candidate.is_duplicate, distinct_bucket=True)
    add_candidate(selected, pool, predicate=lambda candidate: candidate.bucket == M_BUCKET and not candidate.is_duplicate, distinct_bucket=True)
    add_candidate(selected, pool, predicate=lambda candidate: candidate.bucket == S_BUCKET and not candidate.is_duplicate, prefer="smallest", distinct_bucket=True)
    stratified_fill(selected, pool, count, avoid_duplicate_groups=True)
    if len(selected) < count:
        stratified_fill(selected, pool, count, avoid_duplicate_groups=False)
    return selected[:count]


def select_main_samples(
    candidates: Sequence[PaperCandidate],
    count: int,
    excluded_ids: set[str] | None = None,
) -> list[PaperCandidate]:
    """Pick the main audit set with stratified coverage."""
    excluded_ids = excluded_ids or set()
    pool = [candidate for candidate in candidates if candidate.paper_id not in excluded_ids]
    unique_pool = [candidate for candidate in pool if not candidate.is_duplicate]
    selected: list[PaperCandidate] = []
    add_candidate(selected, unique_pool, predicate=lambda candidate: candidate.category_guess == "thesis_like", distinct_bucket=True, distinct_category=True)
    add_candidate(selected, unique_pool, predicate=lambda candidate: candidate.category_guess == "journal_like", distinct_bucket=True, distinct_category=True)
    add_candidate(selected, unique_pool, predicate=lambda candidate: candidate.bucket in {L_BUCKET, XL_BUCKET}, distinct_bucket=True)
    add_candidate(selected, unique_pool, predicate=lambda candidate: candidate.bucket == XS_BUCKET, prefer="smallest", distinct_bucket=True)
    stratified_fill(selected, unique_pool, count, avoid_duplicate_groups=True)
    if len(selected) < count:
        stratified_fill(selected, pool, count, avoid_duplicate_groups=False)
    return selected[:count]


def relativize(path: Path | None, project_root: Path) -> str:
    """Return repo-relative path when possible."""
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path.resolve())


def find_artifact(base_dir: Path, file_name: str) -> Path | None:
    """Find the first artifact matching `file_name` under a directory."""
    matches = sorted(base_dir.rglob(file_name))
    return matches[0] if matches else None


def flatten_structure(nodes: Sequence[Any]) -> dict[str, Any]:
    """Flatten structure nodes into a mapping."""
    flattened: dict[str, Any] = {}

    def walk(items: Sequence[Any]) -> None:
        for item in items:
            flattened[item.id] = item
            walk(item.children)

    walk(nodes)
    return flattened


def outline_matches_structure(structure: Any, outline: Sequence[Any]) -> bool:
    """Check whether outline parent references match structure nodes."""
    structure_nodes = flatten_structure(structure.nodes)
    outline_nodes = {node.id: node for node in outline}
    if len(structure_nodes) != len(outline_nodes):
        return False
    for node_id, structure_node in structure_nodes.items():
        outline_node = outline_nodes.get(node_id)
        if outline_node is None or outline_node.parent_id != structure_node.parent_id:
            return False
    return True


def summarize_exception(exc: BaseException) -> str:
    """Create a short exception summary for machine-readable output."""
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    return f"{exc.__class__.__name__}: {text}"[:240]


def safe_reset_workspace(workspace_dir: Path, paper_id: str) -> None:
    """Remove one paper workspace before rerunning the sample."""
    target = (workspace_dir / paper_id).resolve()
    workspace_root = workspace_dir.resolve()
    audit_root = (workspace_dir / "_audit").resolve()
    if target == audit_root:
        raise ValueError(f"paper_id '{paper_id}' conflicts with reserved audit workspace")
    if workspace_root not in target.parents:
        raise ValueError(f"refusing to reset workspace outside root: {target}")
    if target.exists():
        shutil.rmtree(target)


def refresh_record_artifacts(record: AuditRecord, repository: WorkspaceRepository, project_root: Path) -> dict[str, Any]:
    """Refresh artifact paths and load partial workspace state."""
    workspace = repository.get_workspace(record.paper_id)
    record.artifacts["workspace"] = relativize(workspace.root, project_root)
    record.artifacts["run"] = relativize(workspace.run_path, project_root)

    full_md_path = find_artifact(workspace.raw_dir, "full.md")
    normalized_path = workspace.normalized_dir / "document.md"
    structure_path = workspace.structure_dir / "structure.json"
    diagnostics_path = workspace.structure_dir / "diagnostics.json"
    outline_path = workspace.structure_dir / "outline.json"
    summary_path = workspace.summaries_dir / "summary_tree.json"
    overview_path = workspace.summaries_dir / "overview.json"
    review_notes_path = workspace.summaries_dir / "review_notes.json"

    if full_md_path is not None:
        record.artifacts["full_md"] = relativize(full_md_path, project_root)
    if normalized_path.exists():
        record.artifacts["normalized_markdown"] = relativize(normalized_path, project_root)
    if structure_path.exists():
        record.artifacts["structure"] = relativize(structure_path, project_root)
    if diagnostics_path.exists():
        record.artifacts["diagnostics"] = relativize(diagnostics_path, project_root)
    if outline_path.exists():
        record.artifacts["outline"] = relativize(outline_path, project_root)
    if summary_path.exists():
        record.artifacts["summary_tree"] = relativize(summary_path, project_root)
    if overview_path.exists():
        record.artifacts["overview"] = relativize(overview_path, project_root)
    if review_notes_path.exists():
        record.artifacts["review_notes"] = relativize(review_notes_path, project_root)
    if workspace.sections_dir.exists():
        record.artifacts["sections_dir"] = relativize(workspace.sections_dir, project_root)

    return {
        "workspace": workspace,
        "full_md_path": full_md_path,
        "normalized_path": normalized_path,
        "structure_path": structure_path,
        "diagnostics_path": diagnostics_path,
        "outline_path": outline_path,
        "summary_path": summary_path,
        "overview_path": overview_path,
        "review_notes_path": review_notes_path,
    }


def collect_convert_checks(
    record: AuditRecord,
    repository: WorkspaceRepository,
    project_root: Path,
    short_markdown_chars: int,
) -> None:
    """Populate convert-stage checks."""
    state = refresh_record_artifacts(record, repository, project_root)
    normalized_path = state["normalized_path"]
    full_md_path = state["full_md_path"]

    record.checks["convert_full_md_exists"] = full_md_path is not None and full_md_path.exists()
    if normalized_path.exists():
        markdown = normalized_path.read_text(encoding="utf-8", errors="ignore").strip()
        record.checks["convert_markdown_too_short"] = len(markdown) < short_markdown_chars
        record.checks["convert_markdown_length"] = len(markdown)
    else:
        record.checks["convert_markdown_too_short"] = True
        record.checks["convert_markdown_length"] = 0


def collect_parse_checks(
    record: AuditRecord,
    repository: WorkspaceRepository,
    project_root: Path,
    config: AuditConfig,
) -> None:
    """Populate parse-stage checks and warnings."""
    state = refresh_record_artifacts(record, repository, project_root)
    structure_exists = state["structure_path"].exists()
    diagnostics_exists = state["diagnostics_path"].exists()
    outline_exists = state["outline_path"].exists()
    record.checks["parse_structure_exists"] = structure_exists
    record.checks["parse_diagnostics_exists"] = diagnostics_exists
    record.checks["parse_outline_exists"] = outline_exists

    structure = repository.load_structure(record.paper_id) if structure_exists else None
    diagnostics = repository.load_diagnostics(record.paper_id) if diagnostics_exists else None
    outline = repository.load_outline(record.paper_id) if outline_exists else None
    run_record = repository.load_run_record(record.paper_id)

    if diagnostics is not None:
        record.parse_warnings = list(diagnostics.warnings)
    if structure is not None:
        record.low_confidence_count = len(structure.low_confidence_node_ids)
        total_nodes = max(int(structure.stats.get("total_nodes", 0)), 0)
        abnormal_threshold = max(config.high_low_confidence_min, math.ceil(total_nodes * config.high_low_confidence_ratio))
        record.checks["parse_low_confidence_abnormal"] = total_nodes > 0 and record.low_confidence_count >= abnormal_threshold
        record.checks["parse_total_nodes"] = total_nodes
    else:
        record.checks["parse_low_confidence_abnormal"] = False
        record.checks["parse_total_nodes"] = 0

    record.checks["parse_no_body_nodes"] = any("No body nodes found" in warning for warning in record.parse_warnings)
    record.checks["parse_outline_matches_structure"] = bool(
        structure is not None and outline is not None and outline_matches_structure(structure, outline)
    )

    parse_stage = run_record.artifacts.get("parse_stage") if run_record is not None else None
    record.checks["parse_stage"] = parse_stage
    if parse_stage == "phase3_fallback":
        warning_present = any("LLM refinement failed" in warning for warning in record.parse_warnings)
        run_warning_present = bool(run_record and run_record.artifacts.get("refinement_warning"))
        record.checks["parse_refinement_fallback_recorded"] = warning_present and run_warning_present
    else:
        record.checks["parse_refinement_fallback_recorded"] = None


def verify_summary_independence(
    paper_id: str,
    repository: WorkspaceRepository,
    summarize_use_case: GenerateSummaryUseCase,
) -> tuple[bool, str | None]:
    """Rerun summary generation with `sections/` hidden."""
    workspace = repository.get_workspace(paper_id)
    sections_dir = workspace.sections_dir
    backup_dir = workspace.root / "__audit_sections_backup__"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    try:
        if sections_dir.exists():
            sections_dir.rename(backup_dir)
        summarize_use_case.execute(paper_id, force=True)
        return True, None
    except Exception as exc:
        return False, summarize_exception(exc)
    finally:
        if sections_dir.exists():
            shutil.rmtree(sections_dir)
        if backup_dir.exists():
            backup_dir.rename(sections_dir)


def collect_summary_checks(
    record: AuditRecord,
    repository: WorkspaceRepository,
    project_root: Path,
    summarize_use_case: GenerateSummaryUseCase,
    verify_independence: bool,
) -> None:
    """Populate summary-stage checks."""
    state = refresh_record_artifacts(record, repository, project_root)
    summary_exists = state["summary_path"].exists()
    overview_exists = state["overview_path"].exists()
    review_notes_exists = state["review_notes_path"].exists()
    record.checks["summary_tree_exists"] = summary_exists
    record.checks["overview_exists"] = overview_exists
    record.checks["review_notes_exists"] = review_notes_exists
    record.checks["summary_artifacts_complete"] = summary_exists and overview_exists and review_notes_exists

    if summary_exists and overview_exists and review_notes_exists:
        summary_tree = repository.load_summary_tree(record.paper_id)
        overview = repository.load_overview(record.paper_id)
        review_notes = repository.load_review_notes(record.paper_id)
        structure = repository.load_structure(record.paper_id)
        versions = (
            summary_tree.get("summary_version"),
            overview.get("summary_version"),
            review_notes.get("summary_version"),
        )
        record.checks["summary_version_ok"] = all(version == SUMMARY_VERSION for version in versions)
        body_root_count = len([node for node in structure.nodes if node.role == "body"])
        record.checks["summary_root_count_match"] = len(summary_tree.get("tree", [])) == body_root_count
    else:
        record.checks["summary_version_ok"] = False
        record.checks["summary_root_count_match"] = False

    run_record = repository.load_run_record(record.paper_id)
    record.checks["summary_stage_recorded"] = bool(
        run_record is not None and run_record.artifacts.get("summary_stage") == "phase4_complete"
    )

    if verify_independence and record.checks["summary_artifacts_complete"]:
        ok, error = verify_summary_independence(record.paper_id, repository, summarize_use_case)
        record.checks["summary_independent_of_sections"] = ok
        if error:
            record.checks["summary_independence_error"] = error
    else:
        record.checks["summary_independent_of_sections"] = None


def audit_sample(
    candidate: PaperCandidate,
    phase_name: str,
    config: AuditConfig,
    services: AuditServices,
    progress_callback: Callable[[AuditRecord], None] | None = None,
) -> AuditRecord:
    """Run convert -> parse -> summarize for one paper."""
    record = AuditRecord(
        paper_id=candidate.paper_id,
        pdf_name=candidate.pdf_name,
        size_bytes=candidate.size_bytes,
        bucket=candidate.bucket,
        category_guess=candidate.category_guess,
        sample_phase=phase_name,
    )

    started_at = time.perf_counter()
    current_stage = "check"
    try:
        safe_reset_workspace(config.workspace_dir, candidate.paper_id)
        refresh_record_artifacts(record, services.repository, config.project_root)
        if progress_callback:
            progress_callback(record)

        current_stage = "convert"
        stage_started = time.perf_counter()
        services.convert_use_case.execute_local(candidate.pdf_path, parse_after_convert=False)
        record.durations["convert_seconds"] = round(time.perf_counter() - stage_started, 3)
        record.stage_reached = "converted"
        collect_convert_checks(record, services.repository, config.project_root, config.short_markdown_chars)
        if progress_callback:
            progress_callback(record)

        current_stage = "parse"
        stage_started = time.perf_counter()
        services.parse_use_case.execute(candidate.paper_id, source_type="local_pdf")
        record.durations["parse_seconds"] = round(time.perf_counter() - stage_started, 3)
        record.stage_reached = "parsed"
        collect_parse_checks(record, services.repository, config.project_root, config)
        if progress_callback:
            progress_callback(record)

        current_stage = "summarize"
        stage_started = time.perf_counter()
        services.summarize_use_case.execute(candidate.paper_id, force=True)
        record.durations["summarize_seconds"] = round(time.perf_counter() - stage_started, 3)
        if config.verify_summary_independence:
            stage_started = time.perf_counter()
            collect_summary_checks(
                record,
                services.repository,
                config.project_root,
                services.summarize_use_case,
                verify_independence=True,
            )
            record.durations["summary_independence_seconds"] = round(time.perf_counter() - stage_started, 3)
        else:
            collect_summary_checks(
                record,
                services.repository,
                config.project_root,
                services.summarize_use_case,
                verify_independence=False,
            )
        record.stage_reached = "summarized"
        if progress_callback:
            progress_callback(record)
    except Exception as exc:
        record.failure_stage = current_stage
        record.failure_type = exc.__class__.__name__
        record.exception_summary = summarize_exception(exc)
        record.stage_reached = "failed"
    finally:
        record.durations["total_seconds"] = round(time.perf_counter() - started_at, 3)
        refresh_record_artifacts(record, services.repository, config.project_root)
        if record.failure_stage in {"parse", "summarize"} or record.stage_reached in {"parsed", "summarized"}:
            collect_parse_checks(record, services.repository, config.project_root, config)
        if record.failure_stage == "summarize" or record.stage_reached == "summarized":
            collect_summary_checks(
                record,
                services.repository,
                config.project_root,
                services.summarize_use_case,
                verify_independence=False,
            )
        record.checks["slow_processing"] = record.durations.get("total_seconds", 0.0) >= config.slow_total_seconds
        if progress_callback:
            progress_callback(record)
    return record


def run_phase(
    phase_name: str,
    candidates: Sequence[PaperCandidate],
    config: AuditConfig,
    services: AuditServices,
    progress_callback: Callable[[list[AuditRecord]], None] | None = None,
) -> list[AuditRecord]:
    """Audit all samples in one phase."""
    records: list[AuditRecord] = []

    def handle_progress(record: AuditRecord) -> None:
        for index, existing in enumerate(records):
            if existing.paper_id == record.paper_id:
                records[index] = record
                break
        else:
            records.append(record)
        if progress_callback:
            progress_callback(records)

    for candidate in candidates:
        final_record = audit_sample(candidate, phase_name, config, services, progress_callback=handle_progress)
        handle_progress(final_record)
    return records


def phase_stop_reason(
    phase_name: str,
    records: Sequence[AuditRecord],
    config: AuditConfig,
) -> str | None:
    """Determine whether the audit should stop after this phase."""
    if not records:
        return None

    failures = [record for record in records if record.failure_stage is not None]
    if phase_name == "preflight":
        if failures or any(record.stage_reached != "summarized" for record in records):
            return "preflight_failed"
        return None

    summarized_count = sum(1 for record in records if record.stage_reached == "summarized" and record.failure_stage is None)
    success_rate = summarized_count / len(records)
    if success_rate < config.pilot_success_threshold:
        return f"pilot_success_rate_below_threshold:{success_rate:.2f}"

    failure_patterns = Counter()
    for record in records:
        if record.failure_stage and record.failure_type:
            failure_patterns[(record.failure_stage, record.failure_type)] += 1
    repeated = [pattern for pattern, count in failure_patterns.items() if count >= config.repeated_failure_threshold]
    if repeated:
        stage, failure_type = repeated[0]
        return f"pilot_repeated_failure:{stage}:{failure_type}"

    blocking_checks = Counter()
    for record in records:
        if record.checks.get("parse_outline_matches_structure") is False:
            blocking_checks["parse_outline_mismatch"] += 1
        if record.checks.get("summary_independent_of_sections") is False:
            blocking_checks["summary_depends_on_sections"] += 1
    for code, count in blocking_checks.items():
        if count >= config.repeated_failure_threshold:
            return f"pilot_blocking_check:{code}"
    return None


def compute_sample_stats(records: Sequence[AuditRecord]) -> dict[str, Any]:
    """Aggregate success rates and average durations."""
    if not records:
        return {
            "executed": 0,
            "summarized": 0,
            "success_rate": 0.0,
            "failure_counts": {},
            "average_durations": {},
        }

    failure_counts = Counter(record.failure_stage or "none" for record in records)
    duration_keys = {key for record in records for key in record.durations if key.endswith("_seconds")}
    average_durations = {}
    for key in sorted(duration_keys):
        values = [record.durations[key] for record in records if key in record.durations]
        if values:
            average_durations[key] = round(statistics.mean(values), 3)

    summarized = sum(1 for record in records if record.stage_reached == "summarized" and record.failure_stage is None)
    return {
        "executed": len(records),
        "summarized": summarized,
        "success_rate": round(summarized / len(records), 3),
        "failure_counts": dict(failure_counts),
        "average_durations": average_durations,
    }


def sample_evidence(record: AuditRecord) -> dict[str, Any]:
    """Build a compact evidence block for findings."""
    return {
        "paper_id": record.paper_id,
        "pdf_name": record.pdf_name,
        "sample_phase": record.sample_phase,
        "failure_stage": record.failure_stage,
        "exception_summary": record.exception_summary,
        "artifacts": {
            key: value
            for key, value in record.artifacts.items()
            if key in {"run", "structure", "diagnostics", "outline", "summary_tree", "overview", "review_notes", "full_md"}
        },
    }


def derive_issue_codes(record: AuditRecord) -> list[str]:
    """Map one sample record to issue codes."""
    issue_codes: list[str] = []
    failure_text = (record.exception_summary or "").lower()

    if record.failure_stage == "convert":
        if "timeout" in failure_text or "超时" in (record.exception_summary or ""):
            issue_codes.append("convert_timeout")
        else:
            issue_codes.append("convert_failed")
    if record.failure_stage == "parse":
        if "no headings found" in failure_text:
            issue_codes.append("parse_no_headings")
        else:
            issue_codes.append("parse_failed")
    if record.failure_stage == "summarize":
        issue_codes.append("summary_failed")

    if record.checks.get("convert_full_md_exists") is False:
        issue_codes.append("convert_missing_full_md")
    if record.checks.get("convert_markdown_too_short") is True:
        issue_codes.append("convert_markdown_short")
    if record.checks.get("parse_structure_exists") is False and record.failure_stage != "parse":
        issue_codes.append("parse_structure_missing")
    if record.checks.get("parse_diagnostics_exists") is False and record.failure_stage != "parse":
        issue_codes.append("parse_diagnostics_missing")
    if record.checks.get("parse_outline_exists") is False and record.failure_stage != "parse":
        issue_codes.append("parse_outline_missing")
    if record.checks.get("parse_no_body_nodes") is True:
        issue_codes.append("parse_no_body_nodes")
    if record.checks.get("parse_outline_matches_structure") is False and record.checks.get("parse_structure_exists"):
        issue_codes.append("parse_outline_mismatch")
    if record.checks.get("parse_low_confidence_abnormal") is True:
        issue_codes.append("parse_low_confidence_high")
    if record.checks.get("parse_refinement_fallback_recorded") is False:
        issue_codes.append("parse_refinement_fallback_not_recorded")
    if (
        record.checks.get("summary_artifacts_complete") is False
        and record.failure_stage != "summarize"
        and record.stage_reached in {"summarized", "failed"}
    ):
        issue_codes.append("summary_artifacts_missing")
    if record.checks.get("summary_version_ok") is False and record.checks.get("summary_tree_exists"):
        issue_codes.append("summary_version_mismatch")
    if record.checks.get("summary_root_count_match") is False and record.checks.get("summary_tree_exists"):
        issue_codes.append("summary_root_mismatch")
    if record.checks.get("summary_independent_of_sections") is False:
        issue_codes.append("summary_depends_on_sections")
    if record.checks.get("summary_stage_recorded") is False and record.checks.get("summary_tree_exists"):
        issue_codes.append("summary_stage_missing")
    if record.checks.get("slow_processing") is True:
        issue_codes.append("slow_processing")

    deduped: list[str] = []
    seen: set[str] = set()
    for code in issue_codes:
        if code in seen:
            continue
        seen.add(code)
        deduped.append(code)
    return deduped


def build_findings(environment: dict[str, Any], records: Sequence[AuditRecord], preflight_stop: str | None) -> list[dict[str, Any]]:
    """Aggregate issues into reportable findings."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if environment["cli_check"]["exit_code"] != 0:
        grouped["cli_check_failed"].append({"paper_id": None, "evidence": environment["cli_check"]})
    if not environment["mineru_health"]:
        grouped["mineru_health_failed"].append({"paper_id": None, "evidence": {"blockers": environment["blockers"]}})
    if not environment["llm_health"]:
        grouped["llm_health_failed"].append({"paper_id": None, "evidence": {"blockers": environment["blockers"]}})
    if preflight_stop == "preflight_failed":
        grouped["preflight_failed"].append({"paper_id": None, "evidence": {"reason": preflight_stop}})

    for record in records:
        for code in derive_issue_codes(record):
            grouped[code].append(sample_evidence(record))

    findings: list[dict[str, Any]] = []
    for code, occurrences in grouped.items():
        severity, title, description = ISSUE_METADATA[code]
        paper_ids = [item["paper_id"] for item in occurrences if item.get("paper_id")]
        findings.append(
            {
                "code": code,
                "severity": severity,
                "title": title,
                "description": description,
                "count": len(occurrences),
                "paper_ids": sorted(set(paper_ids)),
                "evidence": occurrences[:3],
            }
        )
    findings.sort(key=lambda item: (SEVERITY_ORDER[item["severity"]], -item["count"], item["title"]))
    return findings


def build_high_frequency_patterns(records: Sequence[AuditRecord], inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarize recurring patterns worth highlighting."""
    patterns: list[dict[str, Any]] = []

    def matching(predicate: Callable[[AuditRecord], bool]) -> list[AuditRecord]:
        return [record for record in records if predicate(record)]

    large_convert = matching(lambda record: record.failure_stage == "convert" and record.bucket in {L_BUCKET, XL_BUCKET})
    if large_convert:
        patterns.append(
            {
                "title": "大文件 convert 阶段更容易失败或超时",
                "count": len(large_convert),
                "paper_ids": [record.paper_id for record in large_convert[:3]],
            }
        )

    thesis_body = matching(lambda record: record.category_guess == "thesis_like" and record.checks.get("parse_no_body_nodes") is True)
    if thesis_body:
        patterns.append(
            {
                "title": "学位论文前置页或正文识别不稳",
                "count": len(thesis_body),
                "paper_ids": [record.paper_id for record in thesis_body[:3]],
            }
        )

    long_summary = matching(
        lambda record: record.bucket in {L_BUCKET, XL_BUCKET}
        and (record.failure_stage == "summarize" or record.checks.get("summary_root_count_match") is False)
    )
    if long_summary:
        patterns.append(
            {
                "title": "长文档 summary 阶段更容易退化",
                "count": len(long_summary),
                "paper_ids": [record.paper_id for record in long_summary[:3]],
            }
        )

    tiny_markdown = matching(lambda record: record.bucket == XS_BUCKET and record.checks.get("convert_markdown_too_short") is True)
    if tiny_markdown:
        patterns.append(
            {
                "title": "超小 PDF 更容易得到过短 Markdown",
                "count": len(tiny_markdown),
                "paper_ids": [record.paper_id for record in tiny_markdown[:3]],
            }
        )

    if inventory.get("duplicate_group_count", 0) > 0:
        patterns.append(
            {
                "title": "语料中存在明显重复文件，后续需要单独去重策略",
                "count": inventory["duplicate_group_count"],
                "paper_ids": [],
            }
        )

    patterns.sort(key=lambda item: (-item["count"], item["title"]))
    return patterns


def build_next_steps(findings: Sequence[dict[str, Any]]) -> list[str]:
    """Generate concise next-step recommendations."""
    severities = Counter(finding["severity"] for finding in findings)
    finding_codes = {finding["code"] for finding in findings}
    steps: list[str] = []
    if severities.get("P0"):
        steps.append("先修复环境或 API 可用性，再重新跑 preflight。")
    if {"parse_no_headings", "parse_no_body_nodes", "parse_low_confidence_high", "parse_outline_mismatch"} & finding_codes:
        steps.append("优先修 phase2 的前置页识别、目录识别和标题编号规则，再扩大样本。")
    if {"summary_failed", "summary_root_mismatch", "summary_depends_on_sections"} & finding_codes:
        steps.append("在 structure.json 稳定后，再修 phase4 摘要树派生和 sections 去依赖问题。")
    if {"convert_timeout", "convert_failed"} & finding_codes:
        steps.append("补大文件 convert 的超时、重试和批次调度策略。")
    if not steps:
        steps.append("当前样本未暴露系统性阻塞问题，可以扩大 pilot 或进入更大样本。")
    return steps


def select_manual_review_queue(records: Sequence[AuditRecord], count: int) -> list[AuditRecord]:
    """Pick successful samples for manual review."""
    successful = [record for record in records if record.stage_reached == "summarized" and record.failure_stage is None]
    if not successful:
        return []

    selected: list[AuditRecord] = []

    def add_record(predicate: Callable[[AuditRecord], bool], prefer_high_risk: bool = False) -> None:
        used = {record.paper_id for record in selected}
        options = [record for record in successful if record.paper_id not in used and predicate(record)]
        if not options:
            return
        if prefer_high_risk:
            choice = max(options, key=lambda record: (record.low_confidence_count, record.size_bytes))
        else:
            choice = max(options, key=lambda record: (record.size_bytes, record.low_confidence_count))
        selected.append(choice)

    add_record(lambda record: record.category_guess == "thesis_like", prefer_high_risk=True)
    add_record(lambda record: record.category_guess == "journal_like", prefer_high_risk=True)
    add_record(lambda record: record.category_guess == "news_like", prefer_high_risk=True)
    add_record(lambda record: record.bucket in {L_BUCKET, XL_BUCKET}, prefer_high_risk=True)
    add_record(lambda record: record.bucket == XS_BUCKET, prefer_high_risk=True)

    remaining = [record for record in successful if record.paper_id not in {item.paper_id for item in selected}]
    remaining.sort(key=lambda record: (-record.low_confidence_count, -record.size_bytes, record.paper_id))
    for record in remaining:
        if len(selected) >= count:
            break
        selected.append(record)

    return selected[:count]


def apply_manual_review_queue(records: Sequence[AuditRecord], review_queue: Sequence[AuditRecord]) -> list[dict[str, Any]]:
    """Mark review targets and return queue payload."""
    queue_ids = {record.paper_id for record in review_queue}
    queue_payload: list[dict[str, Any]] = []
    for record in records:
        if record.paper_id not in queue_ids:
            continue
        artifact_paths = {
            key: value
            for key, value in record.artifacts.items()
            if key in {"structure", "diagnostics", "outline", "summary_tree", "overview", "review_notes", "run"}
        }
        record.manual_review_notes = {
            "selected_for_review": True,
            "review_result": None,
            "notes": [],
            "artifact_paths": artifact_paths,
            "checklist": list(REVIEW_DIMENSIONS),
        }
        queue_payload.append(
            {
                "paper_id": record.paper_id,
                "pdf_name": record.pdf_name,
                "category_guess": record.category_guess,
                "bucket": record.bucket,
                "artifact_paths": artifact_paths,
                "checklist": list(REVIEW_DIMENSIONS),
            }
        )
    return queue_payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON to disk with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def format_counter(counter_payload: dict[str, Any]) -> str:
    """Render dict counters into a compact string."""
    if not counter_payload:
        return "无"
    return "；".join(f"{key}: {value}" for key, value in sorted(counter_payload.items()))


def render_report(results: dict[str, Any], report_path: Path) -> None:
    """Generate the human-readable markdown report."""
    inventory = results["inventory"]
    environment = results["environment"]
    sample_stats = results["sample_stats"]
    findings = results["findings"]
    patterns = results["high_frequency_patterns"]
    manual_review_queue = results["manual_review_queue"]
    phase_payload = results["phases"]

    lines = [
        "# 文献语料渐进式披露工作流审计报告",
        "",
        f"- 运行时间: {results['generated_at']}",
        f"- run_id: `{results['run_id']}`",
        f"- 机器结果: `{results['results_path']}`",
        "",
        "## 语料概览",
        f"- 总 PDF 数: {inventory['total_pdfs']}",
        f"- 大小分布: {format_counter(inventory['bucket_counts'])}",
        f"- 类型猜测分布: {format_counter(inventory['category_counts'])}",
        f"- 重复文件组数: {inventory['duplicate_group_count']}",
        f"- 重复文件数: {inventory['duplicate_file_count']}",
    ]

    duplicate_groups = inventory.get("duplicate_groups", {})
    if duplicate_groups:
        lines.extend(["", "### 重复文件概览"])
        for duplicate_key, names in list(sorted(duplicate_groups.items()))[:10]:
            lines.append(f"- `{duplicate_key}`: {', '.join(names[:4])}")

    lines.extend(
        [
            "",
            "## 环境结论",
            f"- `python main.py check` 退出码: {environment['cli_check']['exit_code']}",
            f"- MinerU health: {'OK' if environment['mineru_health'] else 'FAILED'}",
            f"- LLM health: {'OK' if environment['llm_health'] else 'FAILED'}",
            f"- 是否进入样本真跑: {'是' if results['execution']['samples_executed'] else '否'}",
        ]
    )
    if environment["blockers"]:
        lines.append(f"- 环境阻塞: {'；'.join(environment['blockers'])}")

    lines.extend(
        [
            "",
            "## 分阶段执行",
            f"- preflight: {len(phase_payload['preflight']['records'])} 篇；停止原因: {phase_payload['preflight']['stop_reason'] or '无'}",
            f"- pilot: {len(phase_payload['pilot']['records'])} 篇；停止原因: {phase_payload['pilot']['stop_reason'] or '无'}",
            f"- main: {len(phase_payload['main']['records'])} 篇；状态: {phase_payload['main']['status']}",
        ]
    )

    lines.extend(
        [
            "",
            "## 样本统计",
            f"- 已执行样本数: {sample_stats['executed']}",
            f"- summarize 成功数: {sample_stats['summarized']}",
            f"- 总成功率: {sample_stats['success_rate']}",
            f"- 阶段失败率统计: {format_counter(sample_stats['failure_counts'])}",
            f"- 平均耗时: {format_counter(sample_stats['average_durations'])}",
        ]
    )

    lines.extend(["", "## 问题清单"])
    if not findings:
        lines.append("- 未发现需要记录的问题。")
    else:
        current_severity = None
        for finding in findings:
            if finding["severity"] != current_severity:
                current_severity = finding["severity"]
                lines.extend(["", f"### {current_severity}"])
            lines.append(f"- {finding['title']} ({finding['count']}): {finding['description']}")
            for evidence in finding["evidence"]:
                paper_id = evidence.get("paper_id") or "environment"
                artifacts = evidence.get("artifacts") or {}
                artifact_summary = ", ".join(f"{key}={value}" for key, value in artifacts.items()) or "无 artifact"
                exception_summary = evidence.get("exception_summary") or ""
                suffix = f"；异常: {exception_summary}" if exception_summary else ""
                lines.append(f"- 证据 {paper_id}: {artifact_summary}{suffix}")

    lines.extend(["", "## 高频模式"])
    if not patterns:
        lines.append("- 未观察到需要单列的高频模式。")
    else:
        for pattern in patterns:
            paper_hint = f"；样本: {', '.join(pattern['paper_ids'])}" if pattern["paper_ids"] else ""
            lines.append(f"- {pattern['title']} ({pattern['count']}){paper_hint}")

    lines.extend(["", "## 人工复核队列"])
    if not manual_review_queue:
        lines.append("- 当前没有可进入人工复核的成功样本。")
    else:
        for item in manual_review_queue:
            artifact_summary = ", ".join(f"{key}={value}" for key, value in item["artifact_paths"].items())
            lines.append(f"- {item['paper_id']} ({item['category_guess']}, {item['bucket']}): {artifact_summary}")

    lines.extend(["", "## 下一步建议"])
    for step in results["next_steps"]:
        lines.append(f"- {step}")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(
    config: AuditConfig,
    services: AuditServices | None = None,
    cli_check_runner: Callable[[Path], CommandCheckResult] = run_cli_check,
) -> dict[str, Any]:
    """Execute the staged corpus audit and persist report artifacts."""
    config.docs_dir.mkdir(parents=True, exist_ok=True)
    config.audit_root.mkdir(parents=True, exist_ok=True)
    services = services or build_audit_services(config.papers_dir, config.workspace_dir)

    inventory = build_inventory(config.papers_dir)
    inventory_payload = inventory_summary(inventory)
    generated_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = config.audit_root / run_id
    results_path = run_dir / "results.json"
    report_path = config.docs_dir / f"corpus-audit-{datetime.now().date().isoformat()}.md"

    environment = run_environment_precheck(config, services, cli_check_runner)
    phases = {
        "preflight": {"status": "pending", "selected": [], "records": [], "stop_reason": None},
        "pilot": {"status": "pending", "selected": [], "records": [], "stop_reason": None},
        "main": {"status": "pending", "selected": [], "records": [], "stop_reason": None},
    }

    executed_records: list[AuditRecord] = []
    execution = {"samples_executed": False, "stopped_reason": None}

    def persist_results() -> dict[str, Any]:
        review_queue = select_manual_review_queue(executed_records, config.review_count)
        manual_review_queue = apply_manual_review_queue(executed_records, review_queue)
        sample_stats = compute_sample_stats(executed_records)
        findings = build_findings(environment, executed_records, phases["preflight"]["stop_reason"])
        patterns = build_high_frequency_patterns(executed_records, inventory_payload)
        next_steps = build_next_steps(findings)

        results = {
            "run_id": run_id,
            "generated_at": generated_at,
            "config": {
                "preflight_count": config.preflight_count,
                "pilot_count": config.pilot_count,
                "main_count": config.main_count,
                "review_count": config.review_count,
                "check_only": config.check_only,
                "skip_main": config.skip_main,
                "verify_summary_independence": config.verify_summary_independence,
            },
            "inventory": inventory_payload,
            "environment": environment,
            "execution": execution,
            "phases": phases,
            "records": [record.to_dict() for record in executed_records],
            "sample_stats": sample_stats,
            "findings": findings,
            "high_frequency_patterns": patterns,
            "manual_review_queue": manual_review_queue,
            "next_steps": next_steps,
            "results_path": relativize(results_path, config.project_root),
            "report_path": relativize(report_path, config.project_root),
        }

        write_json(results_path, results)
        render_report(results, report_path)
        return results

    persist_results()

    if not environment["can_run_samples"]:
        execution["stopped_reason"] = "environment_blocked"
        return persist_results()
    elif config.check_only:
        execution["stopped_reason"] = "check_only"
        return persist_results()
    else:
        execution["samples_executed"] = True
        used_ids: set[str] = set()

        preflight_candidates = select_preflight_samples(inventory, config.preflight_count, used_ids)
        phases["preflight"]["selected"] = [candidate.paper_id for candidate in preflight_candidates]
        persist_results()
        preflight_records = run_phase(
            "preflight",
            preflight_candidates,
            config,
            services,
            progress_callback=lambda records: (
                phases["preflight"].update({"records": [record.to_dict() for record in records]}),
                executed_records.__setitem__(slice(None), list(records)),
                persist_results(),
            )[-1],
        )
        phases["preflight"]["records"] = [record.to_dict() for record in preflight_records]
        phases["preflight"]["status"] = "completed"
        phases["preflight"]["stop_reason"] = phase_stop_reason("preflight", preflight_records, config)
        executed_records[:] = list(preflight_records)
        used_ids.update(candidate.paper_id for candidate in preflight_candidates)
        persist_results()

        if phases["preflight"]["stop_reason"] is not None:
            execution["stopped_reason"] = phases["preflight"]["stop_reason"]
            return persist_results()
        else:
            pilot_candidates = select_pilot_samples(inventory, config.pilot_count, used_ids)
            phases["pilot"]["selected"] = [candidate.paper_id for candidate in pilot_candidates]
            persist_results()
            pilot_records = run_phase(
                "pilot",
                pilot_candidates,
                config,
                services,
                progress_callback=lambda records: (
                    phases["pilot"].update({"records": [record.to_dict() for record in records]}),
                    executed_records.__setitem__(slice(None), preflight_records + list(records)),
                    persist_results(),
                )[-1],
            )
            phases["pilot"]["records"] = [record.to_dict() for record in pilot_records]
            phases["pilot"]["status"] = "completed"
            phases["pilot"]["stop_reason"] = phase_stop_reason("pilot", pilot_records, config)
            executed_records[:] = preflight_records + pilot_records
            used_ids.update(candidate.paper_id for candidate in pilot_candidates)
            persist_results()

            if phases["pilot"]["stop_reason"] is not None:
                execution["stopped_reason"] = phases["pilot"]["stop_reason"]
                return persist_results()
            elif config.skip_main:
                phases["main"]["status"] = "skipped"
                execution["stopped_reason"] = "skip_main"
                return persist_results()
            else:
                main_candidates = select_main_samples(inventory, config.main_count, used_ids)
                phases["main"]["selected"] = [candidate.paper_id for candidate in main_candidates]
                persist_results()
                main_records = run_phase(
                    "main",
                    main_candidates,
                    config,
                    services,
                    progress_callback=lambda records: (
                        phases["main"].update({"records": [record.to_dict() for record in records]}),
                        executed_records.__setitem__(slice(None), preflight_records + pilot_records + list(records)),
                        persist_results(),
                    )[-1],
                )
                phases["main"]["records"] = [record.to_dict() for record in main_records]
                phases["main"]["status"] = "completed"
                executed_records[:] = preflight_records + pilot_records + main_records
                persist_results()

    return persist_results()


def parse_args(argv: Sequence[str] | None = None) -> AuditConfig:
    """Parse CLI arguments into `AuditConfig`."""
    parser = argparse.ArgumentParser(description="Run staged corpus audit against papers/*.pdf")
    parser.add_argument("--papers-dir", type=Path, default=PAPERS_DIR)
    parser.add_argument("--workspace-dir", type=Path, default=WORKSPACE_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    parser.add_argument("--audit-root", type=Path, default=WORKSPACE_DIR / "_audit")
    parser.add_argument("--preflight-count", type=int, default=3)
    parser.add_argument("--pilot-count", type=int, default=12)
    parser.add_argument("--main-count", type=int, default=36)
    parser.add_argument("--review-count", type=int, default=10)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--skip-main", action="store_true")
    parser.add_argument("--no-summary-independence-check", action="store_true")
    args = parser.parse_args(argv)
    return AuditConfig(
        papers_dir=args.papers_dir,
        workspace_dir=args.workspace_dir,
        docs_dir=args.docs_dir,
        audit_root=args.audit_root,
        preflight_count=args.preflight_count,
        pilot_count=args.pilot_count,
        main_count=args.main_count,
        review_count=args.review_count,
        check_only=args.check_only,
        skip_main=args.skip_main,
        verify_summary_independence=not args.no_summary_independence_check,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    config = parse_args(argv)
    results = run_audit(config)
    print(
        json.dumps(
            {
                "run_id": results["run_id"],
                "results_path": results["results_path"],
                "report_path": results["report_path"],
                "executed_samples": results["sample_stats"]["executed"],
                "success_rate": results["sample_stats"]["success_rate"],
                "stop_reason": results["execution"]["stopped_reason"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
