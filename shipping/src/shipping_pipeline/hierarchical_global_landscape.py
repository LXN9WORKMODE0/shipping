from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .hierarchical_global_contracts import (
    HierarchicalGlobalConfig,
    build_hierarchical_global_schema,
    load_hierarchical_global_config,
    validate_hierarchical_global_landscape,
)
from .hierarchical_landscape import (
    HIERARCHICAL_LANDSCAPE_ROOT,
    HIERARCHICAL_LOCAL_BATCH_RUN_SCHEMA_VERSION,
)
from .llm_analysis import DEFAULT_MODEL_PROFILE, DEFAULT_TOKENIZER_CACHE, AnalysisInputError
from .llm_json_stage import execute_json_stage
from .llm_model_profile import ModelProfile, load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .research_landscape import (
    RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION,
    create_research_landscape_snapshot,
)
from .research_landscape_contracts import (
    ResearchLandscapeConfig,
    build_research_landscape_schema,
    validate_research_landscape,
)


HIERARCHICAL_GLOBAL_ROOT = "_hierarchical_global_landscapes"
HIERARCHICAL_GLOBAL_RUN_SCHEMA_VERSION = "llm.hierarchical_global_landscape_run.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HIERARCHICAL_GLOBAL_CONFIG = PROJECT_ROOT / "config" / "hierarchical-global-landscape-default.json"

GLOBAL_SYSTEM_PROMPT = """你是层级文献综合的全局归并器。输入只包含多个已严格验证的局部Research Landscape投影，不包含完整论文、Card或Paper Understanding。
只输出符合JSON Schema的对象，不输出Markdown或额外字段。你的任务是把局部维度归并为全局维度，描述局部主题簇之间由真实局部维度直接支持的关系，并把证据不足的问题记录为回看请求。
每个局部dimension必须且只能进入一个global_dimension，或进入unmapped_local_dimensions；禁止静默遗漏和重复归并。global_dimension只填写local_dimension_ids，cluster_ids和paper_ids由程序根据局部维度所有权生成，不得输出这两个字段。
必须在local_dimension_accounting中按输入完整性清单逐项列出每个局部dimension，并声明mapped及其global_dimension_index，或unmapped及原因；条目数必须与输入局部维度总数完全相同。
cross_cluster_relations先选择Schema提供的relation_pair_id，supporting_local_dimension_ids只能引用该簇对两侧的局部维度且两侧至少各一条；relation_pair_id随后由程序移除，from_cluster_id和to_cluster_id由程序生成。不得根据主题相邻自行补出因果、继承、整合成效或共同验证。局部gap只是当前语料缺口，不是已证实结论；需要补充论文时写入look_back_requests。
不得生成任何机器ID，ID由程序生成。"""


@dataclass(frozen=True)
class HierarchicalGlobalSnapshot:
    topic: str
    review_goal: str
    local_batch_run_id: str
    local_batch_sha256: str
    clusters: tuple[dict[str, Any], ...]
    input_sha256: str


class HierarchicalGlobalLandscapeRunner:
    def __init__(
        self,
        workspace: str | Path,
        *,
        analysis_client=None,
        token_counter=None,
        snapshot_loader=None,
    ) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter
        self.snapshot_loader = snapshot_loader or create_hierarchical_global_snapshot

    def run(
        self,
        *,
        local_batch_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path = DEFAULT_MODEL_PROFILE,
        global_config_path: str | Path = DEFAULT_HIERARCHICAL_GLOBAL_CONFIG,
        timeout: int = 900,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _safe_segment(resolved)
        run_dir = self.workspace / HIERARCHICAL_GLOBAL_ROOT / "runs" / resolved
        if run_dir.exists():
            raise AnalysisInputError(f"全局Landscape run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started_at = _now()
        snapshot = profile = config = stage_result = output = None
        try:
            snapshot = self.snapshot_loader(
                self.workspace, local_batch_run_id=local_batch_run_id
            )
            profile = load_model_profile(Path(model_profile_path))
            config = load_hierarchical_global_config(Path(global_config_path))
            if provider != profile.provider:
                raise AnalysisInputError("provider与model profile不一致。")
            schema = build_hierarchical_global_schema(
                topic=snapshot.topic,
                review_goal=snapshot.review_goal,
                clusters=list(snapshot.clusters),
                config=config,
            )
            prompt = build_hierarchical_global_prompt(snapshot, schema=schema)
            _write_json(run_dir / "input" / "local_projection.json", _projection(snapshot))
            _write_json(run_dir / "input" / "output_schema.json", schema)
            _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
            _write_json(run_dir / "input" / "global_config.json", config.to_dict())
            client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url, api_key_env=api_key_env,
                model=profile.request_model, timeout=timeout,
            )
            counter = self.token_counter or DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
            parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="global_landscape",
                task_name="hierarchical_global_landscape",
                directory_name="global_landscape",
                system_prompt=GLOBAL_SYSTEM_PROMPT,
                user_prompt=prompt,
                max_output_tokens=config.global_max_output_tokens,
                context={
                    "local_batch_run_id": local_batch_run_id,
                    "cluster_ids": [row["cluster_id"] for row in snapshot.clusters],
                    "local_dimension_ids": [
                        dimension["dimension_id"]
                        for cluster in snapshot.clusters
                        for dimension in cluster["landscape"]["dimensions"]
                    ],
                    "input_sha256": snapshot.input_sha256,
                },
                client=client, profile=profile, token_counter=counter,
            )
            output = validate_hierarchical_global_landscape(
                parsed, schema=schema, clusters=list(snapshot.clusters)
            )
            coverage = _coverage(snapshot, output)
            _write_json(run_dir / "global_landscape" / "validated_global_landscape.json", output)
            _write_json(run_dir / "audit" / "coverage.json", coverage)
            _write_jsonl(run_dir / "audit" / "look_back_requests.jsonl", output["look_back_requests"])
            _write_json(run_dir / "output" / "hierarchical_global_landscape.json", output)
            _write_text(run_dir / "review" / "hierarchical_global_landscape.md", _render(output, coverage))
            status = "completed"
            failure = None
        except Exception as exc:
            status = "failed"
            failure = {
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc),
                "stage": "global_landscape",
                "recorded_at": _now(),
            }
            _write_jsonl(run_dir / "audit" / "failures.jsonl", [failure])
            coverage = None
        manifest = {
            "schema_version": HIERARCHICAL_GLOBAL_RUN_SCHEMA_VERSION,
            "run_id": resolved,
            "status": status,
            "started_at": started_at,
            "finished_at": _now(),
            "local_batch_run_id": local_batch_run_id,
            "input_sha256": snapshot.input_sha256 if snapshot else None,
            "provider": profile.provider if profile else None,
            "model": profile.request_model if profile else None,
            "global_config": config.to_dict() if config else None,
            "stage_result": stage_result,
            "global_landscape_id": output.get("global_landscape_id") if output else None,
            "coverage": coverage,
            "failure": failure,
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {
            "run_id": resolved,
            "status": status,
            "global_landscape_id": manifest["global_landscape_id"],
            "coverage": coverage,
            "failure": failure,
            "run_dir": str(run_dir),
            "output_path": str(run_dir / "output" / "hierarchical_global_landscape.json") if output else None,
            "review_path": str(run_dir / "review" / "hierarchical_global_landscape.md") if output else None,
        }


def create_hierarchical_global_snapshot(
    workspace: str | Path, *, local_batch_run_id: str
) -> HierarchicalGlobalSnapshot:
    _safe_segment(local_batch_run_id)
    workspace = Path(workspace)
    batch_dir = workspace / HIERARCHICAL_LANDSCAPE_ROOT / "local_runs" / local_batch_run_id
    manifest = _read_json(batch_dir / "manifest.json")
    batch = _read_json(batch_dir / "output" / "local_landscape_batch.json")
    if (
        manifest.get("schema_version") != HIERARCHICAL_LOCAL_BATCH_RUN_SCHEMA_VERSION
        or manifest.get("run_id") != local_batch_run_id
        or manifest.get("status") != "completed"
        or batch.get("run_id") != local_batch_run_id
        or any(row.get("local_status") != "completed" for row in batch.get("records", []))
    ):
        raise AnalysisInputError("局部Landscape批次未完整完成或无法重放。")
    clusters = tuple(_load_local_cluster(workspace, row) for row in batch["records"])
    batch_sha256 = _sha256_json(batch)
    input_sha256 = _sha256_json({
        "batch_sha256": batch_sha256,
        "clusters": [
            {
                "cluster_id": row["cluster_id"],
                "landscape_run_id": row["landscape_run_id"],
                "landscape_sha256": row["landscape_sha256"],
            }
            for row in clusters
        ],
    })
    return HierarchicalGlobalSnapshot(
        topic=str(batch["topic"]), review_goal=str(batch["review_goal"]),
        local_batch_run_id=local_batch_run_id,
        local_batch_sha256=batch_sha256,
        clusters=clusters, input_sha256=input_sha256,
    )


def _load_local_cluster(workspace: Path, record: dict[str, Any]) -> dict[str, Any]:
    run_id = str(record["landscape_run_id"])
    run_dir = workspace / "_research_landscapes" / "runs" / run_id
    manifest = _read_json(run_dir / "manifest.json")
    raw = _read_json(run_dir / "output" / "research_landscape.json")
    if manifest.get("schema_version") != RESEARCH_LANDSCAPE_RUN_SCHEMA_VERSION or manifest.get("run_id") != run_id or manifest.get("status") != "completed":
        raise AnalysisInputError(f"局部Landscape运行不可用：{run_id}")
    snapshot = create_research_landscape_snapshot(
        workspace, collection_path=Path(str(manifest["collection_path"]))
    )
    config = ResearchLandscapeConfig(**manifest["landscape_config"])
    ownership = {
        contribution["contribution_id"]: source.paper_id
        for source in snapshot.sources
        for contribution in source.understanding["contributions"]
    }
    schema = build_research_landscape_schema(
        topic=snapshot.topic, review_goal=snapshot.review_goal,
        paper_ids=[source.paper_id for source in snapshot.sources],
        contribution_to_paper=ownership,
        corpus_scope=snapshot.corpus_scope, config=config,
    )
    replay_input = _strip_landscape_machine_ids(raw)
    landscape = validate_research_landscape(
        replay_input, schema=schema, contribution_to_paper=ownership
    )
    if landscape != raw:
        raise AnalysisInputError(f"局部Landscape无法由稳定ID规则重放：{run_id}")
    if landscape.get("landscape_id") != record.get("landscape_id"):
        raise AnalysisInputError(f"局部Landscape身份不匹配：{run_id}")
    return {
        "cluster_id": str(record["cluster_id"]),
        "cluster_title": str(record["cluster_title"]),
        "landscape_run_id": run_id,
        "landscape_sha256": _sha256_json(landscape),
        "landscape": landscape,
    }


def _strip_landscape_machine_ids(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value, ensure_ascii=False))
    result.pop("landscape_id", None)
    for collection, field in (
        ("dimensions", "dimension_id"),
        ("relations", "relation_id"),
        ("research_evolution", "evolution_id"),
        ("disagreements", "disagreement_id"),
        ("corpus_gaps", "corpus_gap_id"),
        ("field_gap_candidates", "field_gap_candidate_id"),
    ):
        for row in result.get(collection, []):
            row.pop(field, None)
    return result


def build_hierarchical_global_prompt(snapshot: HierarchicalGlobalSnapshot, *, schema: dict[str, Any]) -> str:
    payload = {
        "任务": "将局部Research Landscape归并为全局研究图谱并生成可审计回看请求",
        "综述主题": snapshot.topic,
        "综述目标": snapshot.review_goal,
        "局部主题图谱": _projection(snapshot)["clusters"],
        "局部维度完整性清单": [
            {
                "local_dimension_id": dimension["dimension_id"],
                "cluster_id": cluster["cluster_id"],
                "title": dimension["title"],
            }
            for cluster in snapshot.clusters
            for dimension in cluster["landscape"]["dimensions"]
        ],
        "输出前逐项核对": [
            "每个局部dimension只进入一个全局dimension或显式未映射",
            "local_dimension_accounting条目数与局部维度完整性清单完全相同且ID不重复",
            "跨簇关系引用两侧真实局部dimension",
            "局部gap只形成全局gap或look_back_request，不写成已有事实",
        ],
        "输出JSONSchema": schema,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _projection(snapshot: HierarchicalGlobalSnapshot) -> dict[str, Any]:
    return {
        "topic": snapshot.topic,
        "review_goal": snapshot.review_goal,
        "clusters": [
            {
                "cluster_id": row["cluster_id"],
                "cluster_title": row["cluster_title"],
                "landscape_id": row["landscape"]["landscape_id"],
                "central_problem": row["landscape"]["central_problem"],
                "dimensions": row["landscape"]["dimensions"],
                "relations": row["landscape"]["relations"],
                "research_evolution": row["landscape"]["research_evolution"],
                "disagreements": row["landscape"]["disagreements"],
                "corpus_gaps": row["landscape"]["corpus_gaps"],
                "unmapped_papers": row["landscape"]["unmapped_papers"],
            }
            for row in snapshot.clusters
        ],
    }


def _coverage(snapshot: HierarchicalGlobalSnapshot, output: dict[str, Any]) -> dict[str, Any]:
    total = sum(len(row["landscape"]["dimensions"]) for row in snapshot.clusters)
    mapped = sum(len(row["local_dimension_ids"]) for row in output["global_dimensions"])
    return {
        "cluster_count": len(snapshot.clusters),
        "local_dimension_count": total,
        "mapped_local_dimension_count": mapped,
        "unmapped_local_dimension_count": len(output["unmapped_local_dimensions"]),
        "accounted_local_dimension_count": mapped + len(output["unmapped_local_dimensions"]),
        "global_dimension_count": len(output["global_dimensions"]),
        "cross_cluster_relation_count": len(output["cross_cluster_relations"]),
        "look_back_request_count": len(output["look_back_requests"]),
        "coverage_ratio": (mapped + len(output["unmapped_local_dimensions"])) / total if total else 0.0,
    }


def _render(output: dict[str, Any], coverage: dict[str, Any]) -> str:
    lines = [
        "# 层级论文综合全局图谱", "",
        f"- 主题：{output['topic']}",
        f"- 全局维度：{coverage['global_dimension_count']}",
        f"- 局部维度覆盖率：{coverage['coverage_ratio']:.3f}",
        f"- 跨簇关系：{coverage['cross_cluster_relation_count']}",
        f"- 回看请求：{coverage['look_back_request_count']}", "",
        f"## 中心问题\n\n{output['central_problem']}\n", "## 全局维度", "",
    ]
    for row in output["global_dimensions"]:
        lines.extend([f"### {row['global_dimension_index']}. {row['title']}", "", row["question"], ""])
    if output["look_back_requests"]:
        lines.extend(["## 回看请求", ""])
        for row in output["look_back_requests"]:
            lines.append(f"- [{row['priority']}] {row['question']}：{row['reason']}")
        lines.append("")
    return "\n".join(lines)


def _safe_segment(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise AnalysisInputError("运行ID不是安全路径段。")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"无法读取JSON：{path}") from exc
    if not isinstance(value, dict):
        raise AnalysisInputError(f"JSON必须是对象：{path}")
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
