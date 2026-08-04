from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .research_landscape import create_research_landscape_snapshot
from .research_landscape import (
    DEFAULT_RESEARCH_LANDSCAPE_CONFIG,
    RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION,
    ResearchLandscapeRunner,
)
from .llm_analysis import DEFAULT_MODEL_PROFILE


HIERARCHICAL_LANDSCAPE_ROOT = "_hierarchical_landscapes"
HIERARCHICAL_PLAN_RUN_SCHEMA_VERSION = "llm.hierarchical_landscape_plan_run.v1"
HIERARCHICAL_PLAN_SCHEMA_VERSION = "llm.hierarchical_landscape_plan.v1"
HIERARCHICAL_ROUTING_SCHEMA_VERSION = "llm.hierarchical_landscape_routing.v1"
HIERARCHICAL_LOCAL_BATCH_RUN_SCHEMA_VERSION = (
    "llm.hierarchical_local_landscape_batch_run.v1"
)
LOCAL_COLLECTION_SCHEMA_VERSION = "llm.research_landscape_collection.v1"


class HierarchicalLandscapeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


class HierarchicalLandscapePlanRunner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        snapshot_loader: Callable[..., Any] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.snapshot_loader = snapshot_loader or create_research_landscape_snapshot

    def run(
        self,
        *,
        collection_path: str | Path,
        routing_path: str | Path,
        run_id: str | None = None,
        max_papers_per_cluster: int = 40,
        max_memberships_per_paper: int = 2,
    ) -> dict[str, Any]:
        if max_papers_per_cluster < 2:
            raise HierarchicalLandscapeError(
                "hierarchical_plan.cluster_limit_invalid",
                "max_papers_per_cluster必须至少为2。",
            )
        if max_memberships_per_paper not in {1, 2, 3}:
            raise HierarchicalLandscapeError(
                "hierarchical_plan.membership_limit_invalid",
                "max_memberships_per_paper只允许1至3。",
            )
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved, "run_id")
        run_dir = self.workspace / HIERARCHICAL_LANDSCAPE_ROOT / "runs" / resolved
        if run_dir.exists():
            raise HierarchicalLandscapeError(
                "hierarchical_plan.run_exists", f"run_id已存在：{resolved}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot = None
        try:
            snapshot = self.snapshot_loader(
                self.workspace, collection_path=collection_path
            )
            _write_json(run_dir / "input" / "source_collection.json", snapshot.collection)
            _write_jsonl(
                run_dir / "input" / "paper_identity_catalog.jsonl",
                [
                    {
                        "paper_id": source.paper_id,
                        "paper_title": source.paper_title,
                        "understanding_run_id": source.run_id,
                    }
                    for source in snapshot.sources
                ],
            )
            routing = _read_json(Path(routing_path), "主题路由文件")
            _write_json(run_dir / "input" / "routing.json", routing)
            plan = build_hierarchical_plan(
                snapshot,
                routing,
                max_papers_per_cluster=max_papers_per_cluster,
                max_memberships_per_paper=max_memberships_per_paper,
            )
            _write_json(run_dir / "output" / "hierarchical_plan.json", plan)
            _write_json(run_dir / "audit" / "coverage.json", plan["coverage"])
            _write_jsonl(
                run_dir / "audit" / "paper_memberships.jsonl",
                plan["paper_memberships"],
            )
            for cluster in plan["clusters"]:
                _write_json(
                    run_dir / "local_collections" / f"{cluster['cluster_id']}.json",
                    cluster["local_collection"],
                )
            _write_text(
                run_dir / "review" / "hierarchical_plan.md", _render_plan(plan)
            )
            manifest = {
                "schema_version": HIERARCHICAL_PLAN_RUN_SCHEMA_VERSION,
                "run_id": resolved,
                "status": "completed",
                "started_at": started_at,
                "finished_at": _now(),
                "source_collection_path": str(collection_path),
                "routing_path": str(routing_path),
                "source_input_sha256": snapshot.input_sha256,
                "plan_sha256": _sha256_json(plan),
                "summary": plan["coverage"],
                "failure": None,
            }
            _write_json(run_dir / "manifest.json", manifest)
            return {
                "run_id": resolved,
                "status": "completed",
                "summary": plan["coverage"],
                "plan_path": str(run_dir / "output" / "hierarchical_plan.json"),
                "report_path": str(run_dir / "review" / "hierarchical_plan.md"),
                "failure": None,
            }
        except Exception as exc:
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "stage": "routing_plan",
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            _write_json(run_dir / "manifest.json", {
                "schema_version": HIERARCHICAL_PLAN_RUN_SCHEMA_VERSION,
                "run_id": resolved,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "source_collection_path": str(collection_path),
                "routing_path": str(routing_path),
                "source_input_sha256": getattr(snapshot, "input_sha256", None),
                "plan_sha256": None,
                "summary": None,
                "failure": failure,
            })
            return {
                "run_id": resolved,
                "status": "failed",
                "summary": None,
                "plan_path": None,
                "report_path": None,
                "failure": failure,
            }


class HierarchicalLocalLandscapeBatchRunner:
    def __init__(self, workspace: str | Path, *, landscape_runner=None) -> None:
        self.workspace = Path(workspace)
        self.landscape_runner = landscape_runner or ResearchLandscapeRunner(
            self.workspace
        )

    def run(
        self,
        *,
        plan_run_id: str,
        run_id: str | None = None,
        max_clusters: int | None = None,
        resume_from_run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        landscape_config_path: str | Path = DEFAULT_RESEARCH_LANDSCAPE_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        if max_clusters is not None and max_clusters < 1:
            raise HierarchicalLandscapeError(
                "hierarchical_local.limit_invalid", "max_clusters必须至少为1。"
            )
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved, "run_id")
        _safe_segment(plan_run_id, "plan_run_id")
        run_dir = self.workspace / HIERARCHICAL_LANDSCAPE_ROOT / "local_runs" / resolved
        if run_dir.exists():
            raise HierarchicalLandscapeError(
                "hierarchical_local.run_exists", f"run_id已存在：{resolved}"
            )
        run_dir.mkdir(parents=True)
        started_at = _now()
        plan_dir, plan, plan_sha256 = _load_plan(self.workspace, plan_run_id)
        reused = self._load_reusable(
            resume_from_run_id=resume_from_run_id,
            plan_run_id=plan_run_id,
            plan_sha256=plan_sha256,
            clusters=plan["clusters"],
        )
        remaining = [
            cluster for cluster in plan["clusters"]
            if cluster["cluster_id"] not in reused
        ]
        selected = remaining[:max_clusters] if max_clusters is not None else remaining
        _write_json(run_dir / "input" / "request.json", {
            "plan_run_id": plan_run_id,
            "plan_sha256": plan_sha256,
            "max_clusters": max_clusters,
            "resume_from_run_id": resume_from_run_id,
            "selected_cluster_ids": [row["cluster_id"] for row in selected],
            "reused_cluster_ids": sorted(reused),
        })
        children = list(reused.values())
        for index, cluster in enumerate(selected, start=1):
            child_run_id = f"{resolved}--cluster-{index:03d}-{cluster['cluster_id']}"
            try:
                result = self.landscape_runner.run(
                    collection_path=(
                        plan_dir / "local_collections" / f"{cluster['cluster_id']}.json"
                    ),
                    run_id=child_run_id,
                    provider=provider,
                    api_url=api_url,
                    api_key_env=api_key_env,
                    model_profile_path=model_profile_path,
                    landscape_config_path=landscape_config_path,
                    timeout=timeout,
                )
            except Exception as exc:
                result = {
                    "run_id": child_run_id,
                    "status": "failed",
                    "failure": {
                        "error_code": getattr(exc, "code", type(exc).__name__),
                        "error_message": str(exc),
                        "stage": "landscape_runner",
                    },
                    "coverage": None,
                }
            children.append({
                "cluster_id": cluster["cluster_id"],
                "cluster_title": cluster["title"],
                "paper_count": len(cluster["paper_ids"]),
                **result,
            })
            _write_jsonl(run_dir / "runs" / "child_runs.jsonl", children)
        child_by_cluster = {row["cluster_id"]: row for row in children}
        records = []
        for cluster in plan["clusters"]:
            child = child_by_cluster.get(cluster["cluster_id"])
            status = (
                "completed" if child and child["status"] == "completed"
                else "failed" if child
                else "pending"
            )
            records.append({
                "cluster_id": cluster["cluster_id"],
                "cluster_title": cluster["title"],
                "paper_count": len(cluster["paper_ids"]),
                "local_status": status,
                "landscape_run_id": child.get("run_id") if child else None,
                "landscape_id": child.get("landscape_id") if child else None,
                "coverage": child.get("coverage") if child else None,
                "failure": child.get("failure") if child else None,
            })
        summary = {
            "cluster_count": len(records),
            "completed_cluster_count": sum(row["local_status"] == "completed" for row in records),
            "failed_cluster_count": sum(row["local_status"] == "failed" for row in records),
            "pending_cluster_count": sum(row["local_status"] == "pending" for row in records),
            "source_paper_count": plan["coverage"]["source_paper_count"],
            "assigned_paper_count": plan["coverage"]["assigned_paper_count"],
        }
        status = (
            "completed_with_failures" if summary["failed_cluster_count"]
            else "completed_partial" if summary["pending_cluster_count"]
            else "completed"
        )
        output = {
            "schema_version": "llm.hierarchical_local_landscape_batch.v1",
            "run_id": resolved,
            "plan_run_id": plan_run_id,
            "plan_sha256": plan_sha256,
            "topic": plan["topic"],
            "review_goal": plan["review_goal"],
            "summary": summary,
            "records": records,
            "successful_landscape_run_ids": [
                row["landscape_run_id"] for row in records
                if row["local_status"] == "completed"
            ],
        }
        _write_json(run_dir / "output" / "local_landscape_batch.json", output)
        _write_jsonl(run_dir / "output" / "local_landscape_batch.jsonl", records)
        _write_text(run_dir / "review" / "local_landscape_batch.md", _render_local_batch(output))
        manifest = {
            "schema_version": HIERARCHICAL_LOCAL_BATCH_RUN_SCHEMA_VERSION,
            "run_id": resolved,
            "status": status,
            "started_at": started_at,
            "finished_at": _now(),
            "plan_run_id": plan_run_id,
            "plan_sha256": plan_sha256,
            "resume_from_run_id": resume_from_run_id,
            "summary": summary,
            "failure": None,
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": status,
            "summary": summary,
            "report_path": str(run_dir / "review" / "local_landscape_batch.md"),
            "failure": None,
        }

    def _load_reusable(
        self,
        *,
        resume_from_run_id: str | None,
        plan_run_id: str,
        plan_sha256: str,
        clusters: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if resume_from_run_id is None:
            return {}
        _safe_segment(resume_from_run_id, "resume_from_run_id")
        old_dir = self.workspace / HIERARCHICAL_LANDSCAPE_ROOT / "local_runs" / resume_from_run_id
        old_manifest = _read_json(old_dir / "manifest.json", "旧局部批次manifest")
        old_output = _read_json(old_dir / "output" / "local_landscape_batch.json", "旧局部批次输出")
        if old_manifest.get("plan_run_id") != plan_run_id or old_manifest.get("plan_sha256") != plan_sha256:
            raise HierarchicalLandscapeError(
                "hierarchical_local.resume_mismatch", "续跑使用的层级计划已变化。"
            )
        known_clusters = {row["cluster_id"] for row in clusters}
        reused = {}
        for row in old_output["records"]:
            if row.get("local_status") != "completed" or row.get("cluster_id") not in known_clusters:
                continue
            landscape_run_id = str(row["landscape_run_id"])
            manifest = _read_json(
                self.workspace / "_research_landscapes" / "runs" / landscape_run_id / "manifest.json",
                "局部Landscape manifest",
            )
            output_path = self.workspace / "_research_landscapes" / "runs" / landscape_run_id / "output" / "research_landscape.json"
            if (
                manifest.get("schema_version") != RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION
                or manifest.get("run_id") != landscape_run_id
                or manifest.get("status") != "completed"
                or not output_path.exists()
            ):
                continue
            reused[str(row["cluster_id"])] = {
                "cluster_id": str(row["cluster_id"]),
                "cluster_title": str(row["cluster_title"]),
                "paper_count": int(row["paper_count"]),
                "run_id": landscape_run_id,
                "status": "completed",
                "landscape_id": row.get("landscape_id"),
                "coverage": row.get("coverage"),
                "failure": None,
                "reused_from_run_id": resume_from_run_id,
            }
        return reused


def build_hierarchical_plan(
    snapshot: Any,
    routing: object,
    *,
    max_papers_per_cluster: int,
    max_memberships_per_paper: int,
) -> dict[str, Any]:
    if not isinstance(routing, dict):
        raise HierarchicalLandscapeError(
            "hierarchical_plan.routing_invalid", "主题路由必须是JSON对象。"
        )
    expected_fields = {
        "schema_version",
        "topic",
        "review_goal",
        "clusters",
        "unassigned_papers",
    }
    if set(routing) != expected_fields:
        raise HierarchicalLandscapeError(
            "hierarchical_plan.routing_fields_invalid",
            "主题路由字段不匹配。",
        )
    if routing.get("schema_version") != HIERARCHICAL_ROUTING_SCHEMA_VERSION:
        raise HierarchicalLandscapeError(
            "hierarchical_plan.routing_schema_invalid", "主题路由版本不受支持。"
        )
    if routing.get("topic") != snapshot.topic or routing.get("review_goal") != snapshot.review_goal:
        raise HierarchicalLandscapeError(
            "hierarchical_plan.routing_scope_mismatch",
            "主题路由与来源collection的主题或综述目标不一致。",
        )
    sources_by_paper = {source.paper_id: source for source in snapshot.sources}
    if len(sources_by_paper) != len(snapshot.sources):
        raise HierarchicalLandscapeError(
            "hierarchical_plan.duplicate_paper", "来源Understanding包含重复paper_id。"
        )
    clusters = routing.get("clusters")
    if not isinstance(clusters, list) or not clusters:
        raise HierarchicalLandscapeError(
            "hierarchical_plan.clusters_invalid", "主题路由至少需要一个主题簇。"
        )
    cluster_ids: set[str] = set()
    memberships: dict[str, list[str]] = {paper_id: [] for paper_id in sources_by_paper}
    planned_clusters = []
    for index, raw in enumerate(clusters, start=1):
        cluster = _validate_cluster(raw, index=index)
        cluster_id = cluster["cluster_id"]
        if cluster_id in cluster_ids:
            raise HierarchicalLandscapeError(
                "hierarchical_plan.cluster_duplicate", f"主题簇ID重复：{cluster_id}"
            )
        cluster_ids.add(cluster_id)
        paper_ids = cluster["paper_ids"]
        if len(paper_ids) > max_papers_per_cluster:
            raise HierarchicalLandscapeError(
                "hierarchical_plan.cluster_overflow",
                f"主题簇{cluster_id}包含{len(paper_ids)}篇，超过上限{max_papers_per_cluster}。",
            )
        unknown = [paper_id for paper_id in paper_ids if paper_id not in sources_by_paper]
        if unknown:
            raise HierarchicalLandscapeError(
                "hierarchical_plan.paper_unknown",
                f"主题簇{cluster_id}引用未知论文：{unknown}",
            )
        for paper_id in paper_ids:
            memberships[paper_id].append(cluster_id)
        source_run_ids = [sources_by_paper[paper_id].run_id for paper_id in paper_ids]
        local_collection = {
            "schema_version": LOCAL_COLLECTION_SCHEMA_VERSION,
            "topic": snapshot.topic,
            "review_goal": f"{snapshot.review_goal}；局部主题：{cluster['title']}；问题：{cluster['question']}",
            "corpus_scope": snapshot.corpus_scope,
            "source_understanding_run_ids": source_run_ids,
        }
        planned_clusters.append({
            "cluster_index": index,
            "cluster_id": cluster_id,
            "title": cluster["title"],
            "question": cluster["question"],
            "paper_ids": paper_ids,
            "source_understanding_run_ids": source_run_ids,
            "local_collection_path": f"local_collections/{cluster_id}.json",
            "local_collection": local_collection,
        })
    unassigned = _validate_unassigned(
        routing.get("unassigned_papers"), known_papers=set(sources_by_paper)
    )
    unassigned_ids = {row["paper_id"] for row in unassigned}
    membership_errors = {
        paper_id: assigned
        for paper_id, assigned in memberships.items()
        if len(assigned) > max_memberships_per_paper
    }
    if membership_errors:
        raise HierarchicalLandscapeError(
            "hierarchical_plan.membership_overflow",
            f"论文主题归属超过上限：{membership_errors}",
        )
    overlap = sorted(set(paper_id for paper_id, assigned in memberships.items() if assigned) & unassigned_ids)
    if overlap:
        raise HierarchicalLandscapeError(
            "hierarchical_plan.assignment_conflict",
            f"论文不能同时已分配和未分配：{overlap}",
        )
    missing = sorted(
        paper_id
        for paper_id, assigned in memberships.items()
        if not assigned and paper_id not in unassigned_ids
    )
    if missing:
        raise HierarchicalLandscapeError(
            "hierarchical_plan.paper_silently_missing",
            f"论文既未进入主题簇也未显式记为未分配：{missing}",
        )
    paper_memberships = [
        {
            "paper_id": source.paper_id,
            "paper_title": source.paper_title,
            "understanding_run_id": source.run_id,
            "cluster_ids": memberships[source.paper_id],
            "unassigned_reason": next(
                (row["reason"] for row in unassigned if row["paper_id"] == source.paper_id),
                None,
            ),
        }
        for source in snapshot.sources
    ]
    assigned_count = sum(bool(row["cluster_ids"]) for row in paper_memberships)
    multi_count = sum(len(row["cluster_ids"]) > 1 for row in paper_memberships)
    coverage = {
        "source_paper_count": len(snapshot.sources),
        "cluster_count": len(planned_clusters),
        "assigned_paper_count": assigned_count,
        "multi_cluster_paper_count": multi_count,
        "unassigned_paper_count": len(unassigned),
        "accounted_paper_count": assigned_count + len(unassigned),
        "coverage_ratio": (
            (assigned_count + len(unassigned)) / len(snapshot.sources)
            if snapshot.sources else 0.0
        ),
    }
    return {
        "schema_version": HIERARCHICAL_PLAN_SCHEMA_VERSION,
        "topic": snapshot.topic,
        "review_goal": snapshot.review_goal,
        "corpus_scope": snapshot.corpus_scope,
        "source_input_sha256": snapshot.input_sha256,
        "max_papers_per_cluster": max_papers_per_cluster,
        "max_memberships_per_paper": max_memberships_per_paper,
        "clusters": planned_clusters,
        "unassigned_papers": unassigned,
        "paper_memberships": paper_memberships,
        "coverage": coverage,
    }


def _validate_cluster(raw: object, *, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"cluster_id", "title", "question", "paper_ids"}:
        raise HierarchicalLandscapeError(
            "hierarchical_plan.cluster_invalid", f"第{index}个主题簇字段无效。"
        )
    cluster_id = _safe_segment(str(raw.get("cluster_id") or ""), "cluster_id")
    title = _required_text(raw.get("title"), f"clusters[{index}].title")
    question = _required_text(raw.get("question"), f"clusters[{index}].question")
    paper_ids = raw.get("paper_ids")
    if not isinstance(paper_ids, list) or len(paper_ids) < 2 or not all(isinstance(value, str) and value for value in paper_ids):
        raise HierarchicalLandscapeError(
            "hierarchical_plan.cluster_papers_invalid",
            f"主题簇{cluster_id}至少需要2篇有效论文。",
        )
    if len(paper_ids) != len(set(paper_ids)):
        raise HierarchicalLandscapeError(
            "hierarchical_plan.cluster_paper_duplicate",
            f"主题簇{cluster_id}包含重复论文。",
        )
    return {"cluster_id": cluster_id, "title": title, "question": question, "paper_ids": paper_ids}


def _validate_unassigned(raw: object, *, known_papers: set[str]) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise HierarchicalLandscapeError(
            "hierarchical_plan.unassigned_invalid", "unassigned_papers必须是数组。"
        )
    result = []
    seen = set()
    for index, row in enumerate(raw):
        if not isinstance(row, dict) or set(row) != {"paper_id", "reason"}:
            raise HierarchicalLandscapeError(
                "hierarchical_plan.unassigned_invalid", f"第{index + 1}项未分配记录无效。"
            )
        paper_id = str(row.get("paper_id") or "")
        if paper_id not in known_papers or paper_id in seen:
            raise HierarchicalLandscapeError(
                "hierarchical_plan.unassigned_paper_invalid",
                f"未分配论文未知或重复：{paper_id}",
            )
        seen.add(paper_id)
        result.append({"paper_id": paper_id, "reason": _required_text(row.get("reason"), "reason")})
    return result


def _render_plan(plan: dict[str, Any]) -> str:
    coverage = plan["coverage"]
    lines = [
        "# 层级论文综合路由计划", "",
        f"- 综述主题：{plan['topic']}",
        f"- 来源论文：{coverage['source_paper_count']}",
        f"- 主题簇：{coverage['cluster_count']}",
        f"- 已分配：{coverage['assigned_paper_count']}",
        f"- 跨主题论文：{coverage['multi_cluster_paper_count']}",
        f"- 显式未分配：{coverage['unassigned_paper_count']}",
        f"- 账本覆盖率：{coverage['coverage_ratio']:.3f}", "",
        "## 主题簇", "",
    ]
    for cluster in plan["clusters"]:
        lines.extend([
            f"### {cluster['cluster_index']}. {cluster['title']}", "",
            f"- cluster_id：`{cluster['cluster_id']}`",
            f"- 核心问题：{cluster['question']}",
            f"- 论文数：{len(cluster['paper_ids'])}",
            f"- 局部collection：`{cluster['local_collection_path']}`", "",
        ])
    if plan["unassigned_papers"]:
        lines.extend(["## 未分配论文", ""])
        for row in plan["unassigned_papers"]:
            lines.append(f"- `{row['paper_id']}`：{row['reason']}")
        lines.append("")
    return "\n".join(lines)


def _render_local_batch(output: dict[str, Any]) -> str:
    summary = output["summary"]
    lines = [
        "# 层级论文综合局部运行账本", "",
        f"- 主题：{output['topic']}",
        f"- 主题簇：{summary['cluster_count']}",
        f"- 完成：{summary['completed_cluster_count']}",
        f"- 失败：{summary['failed_cluster_count']}",
        f"- 待运行：{summary['pending_cluster_count']}", "",
    ]
    for row in output["records"]:
        lines.append(
            f"- `{row['cluster_id']}` {row['cluster_title']}：{row['local_status']}，"
            f"论文{row['paper_count']}篇"
        )
    lines.append("")
    return "\n".join(lines)


def _load_plan(workspace: Path, run_id: str) -> tuple[Path, dict[str, Any], str]:
    run_dir = workspace / HIERARCHICAL_LANDSCAPE_ROOT / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json", "层级计划manifest")
    plan = _read_json(run_dir / "output" / "hierarchical_plan.json", "层级计划")
    plan_sha256 = _sha256_json(plan)
    if (
        manifest.get("schema_version") != HIERARCHICAL_PLAN_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "completed"
        or manifest.get("plan_sha256") != plan_sha256
        or plan.get("schema_version") != HIERARCHICAL_PLAN_SCHEMA_VERSION
    ):
        raise HierarchicalLandscapeError(
            "hierarchical_local.plan_replay_mismatch", "层级计划无法严格重放。"
        )
    return run_dir, plan, plan_sha256


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HierarchicalLandscapeError(
            "hierarchical_plan.text_invalid", f"{field}不能为空。"
        )
    return value.strip()


def _safe_segment(value: str, field: str) -> str:
    if not value or value in {".", ".."} or any(char in value for char in "/\\"):
        raise HierarchicalLandscapeError(
            "hierarchical_plan.path_invalid", f"{field}不是安全路径段。"
        )
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HierarchicalLandscapeError(
            "hierarchical_plan.json_invalid", f"无法读取{label}：{path}"
        ) from exc
    if not isinstance(value, dict):
        raise HierarchicalLandscapeError(
            "hierarchical_plan.json_invalid", f"{label}必须是JSON对象。"
        )
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
