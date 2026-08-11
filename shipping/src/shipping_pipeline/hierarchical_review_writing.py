from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .llm_analysis import DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_model_profile import load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter


ROOT = "_hierarchical_review_writing"
MIN_BODY_CHARS = 8000
MAX_BODY_CHARS = 24000
SYSTEM_PROMPT = """你是中文学术综述写作者。只输出符合JSON Schema的对象。
输入包含全局研究景观、各主题局部景观和逐篇Paper Understanding。全局景观决定章节结构，局部景观用于跨论文比较，Paper Understanding用于具体事实、方法、结果、验证水平和局限。
不得逐篇罗列摘要；每段必须围绕一个比较性判断组织多篇文献。必须区分现场观察、统计分析、数值仿真、物理模型试验、算法测试、工程实践与方案建议，不得把仿真或建议写成已实施效果。
每段citation_keys必须列出直接支持该段的论文短键，禁止使用未知短键。不得在text中写机器ID或引用编号。语料未回答的问题只能表述为“本次纳入文献尚未充分回答”。跨主题关系仅作为组织线索，具体陈述仍须由Paper Understanding支持。"""
SYSTEM_PROMPT += """
正文text绝对禁止出现P001这类论文短键，论文身份只写入citation_keys。不得使用“证明某类方法普遍优越”“尚未有研究”“普遍认为”“根本原因”“根本性方案”等超出定向语料范围的概括。短期测试只能写成特定数据和设定下的结果；仿真、模型试验、现场观测和工程实施必须严格区分。摘要不得把多项措施与总通过量构造为未经论文直接支持的因果关系。"""
SYSTEM_PROMPT += """
最终中文正文总长度必须为8000至24000个字符。每个主题章节应充分展开研究对象、方法和证据类型、主要共识与分歧、验证边界及其对综述主题的意义，不能只写概括性摘要；扩写必须来自输入材料，不得用重复句或无来源常识填充篇幅。"""
SYSTEM_PROMPT += """
输入文献对同一年份、指标或结论存在冲突时，必须明确写成来源间不一致或口径差异，不得自行选择其中一个作为确定事实。正文禁用“证明”“普遍”“根本原因”“根本途径”四种表述，分别改用与证据强度相符的“表明”“部分文献认为”“重要因素”或“候选路径”。"""


class HierarchicalReviewWritingError(ValueError):
    pass


class HierarchicalReviewWritingRunner:
    def __init__(self, workspace: str | Path, *, analysis_client=None, token_counter=None) -> None:
        self.workspace = Path(workspace)
        self.analysis_client = analysis_client
        self.token_counter = token_counter

    def run(
        self,
        *,
        global_run_id: str,
        run_id: str | None = None,
        provider: str = "openai-compatible",
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model_profile_path: str | Path,
        reuse_draft_from_run_id: str | None = None,
        timeout: int = 1800,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = self.workspace / ROOT / "runs" / resolved
        if run_dir.exists():
            raise HierarchicalReviewWritingError(f"run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        started = _now()
        failure = None
        output = None
        stage_result = None
        try:
            source = self._load_source(global_run_id)
            schema = _schema(source["global_landscape"])
            _write_json(run_dir / "input" / "writing_input.json", source)
            _write_json(run_dir / "input" / "output_schema.json", schema)
            profile = load_model_profile(Path(model_profile_path))
            if profile.provider != provider:
                raise HierarchicalReviewWritingError("模型provider与命令不一致。")
            _write_json(run_dir / "input" / "model_profile.json", profile.to_dict())
            if reuse_draft_from_run_id:
                prior_dir = self.workspace / ROOT / "runs" / reuse_draft_from_run_id
                prior_input = _read_json(prior_dir / "input" / "writing_input.json")
                if prior_input["input_sha256"] != source["input_sha256"]:
                    raise HierarchicalReviewWritingError("复用草稿的冻结输入与当前输入不一致。")
                parsed = _read_json(prior_dir / "generation" / "parsed_response.json")
                stage_result = {
                    "status": "reused",
                    "reused_from_run_id": reuse_draft_from_run_id,
                    "input_sha256": source["input_sha256"],
                }
            else:
                client = self.analysis_client or OpenAICompatibleAnalysisClient.from_env(
                    api_url=api_url,
                    api_key_env=api_key_env,
                    model=profile.request_model,
                    timeout=timeout,
                )
                counter = self.token_counter or DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
                parsed, stage_result = execute_json_stage(
                run_dir=run_dir,
                stage="hierarchical_review_generation",
                task_name="hierarchical_review_writing",
                directory_name="generation",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=json.dumps({
                    "任务": "依据层级研究景观和49篇论文理解撰写完整中文综述",
                    "写作要求": {
                        "正文目标字符数": f"{MIN_BODY_CHARS}-{MAX_BODY_CHARS}",
                        "每个主题章节": "至少2段，比较方法、结果、验证水平和适用边界",
                        "引用规则": "每段至少1个citation_key；具体结果必须直接引用支持论文",
                    },
                    "输入": source,
                    "输出JSONSchema": schema,
                }, ensure_ascii=False, separators=(",", ":")),
                max_output_tokens=min(32768, profile.model_max_output_tokens),
                context={
                    "global_run_id": global_run_id,
                    "paper_count": len(source["papers"]),
                    "chapter_count": len(source["global_landscape"]["global_dimensions"]),
                },
                client=client,
                profile=profile,
                    token_counter=counter,
                )
            restored_citation_mentions = _restore_citation_names(parsed, source)
            evidence_language_calibrations = _calibrate_evidence_language(parsed)
            output = _validate(parsed, source, schema)
            _write_json(run_dir / "generation" / "validated_review.json", output)
            _write_json(run_dir / "output" / "hierarchical_review.json", output)
            _write_text(run_dir / "review" / "hierarchical_review.md", _render(output, source))
            citation_audit = _citation_audit(output, source)
            citation_audit["restored_citation_mention_count"] = restored_citation_mentions
            citation_audit["evidence_language_calibrations"] = evidence_language_calibrations
            _write_json(run_dir / "audit" / "citation_coverage.json", citation_audit)
            status = "completed"
        except Exception as exc:
            status = "failed"
            failure = {"error_code": type(exc).__name__, "error_message": str(exc), "recorded_at": _now()}
            _write_json(run_dir / "audit" / "failure.json", failure)
        manifest = {
            "schema_version": "llm.hierarchical_review_writing_run.v1",
            "run_id": resolved,
            "status": status,
            "started_at": started,
            "finished_at": _now(),
            "global_run_id": global_run_id,
            "reuse_draft_from_run_id": reuse_draft_from_run_id,
            "paper_count": len(source["papers"]) if "source" in locals() else 0,
            "chapter_count": len(output["chapters"]) if output else 0,
            "body_char_count": _body_chars(output) if output else 0,
            "stage_result": stage_result,
            "failure": failure,
        }
        _write_json(run_dir / "manifest.json", manifest)
        return {"run_id": resolved, "status": status, "run_dir": str(run_dir), "review_path": str(run_dir / "review" / "hierarchical_review.md") if output else None, "body_char_count": manifest["body_char_count"], "failure": failure}

    def _load_source(self, global_run_id: str) -> dict[str, Any]:
        global_dir = self.workspace / "_hierarchical_global_landscapes" / "runs" / global_run_id
        manifest = _read_json(global_dir / "manifest.json")
        if manifest.get("status") != "completed":
            raise HierarchicalReviewWritingError("全局研究景观运行不可用。")
        global_landscape = _read_json(global_dir / "output" / "hierarchical_global_landscape.json")
        local_batch_id = str(manifest["local_batch_run_id"])
        local_batch_dir = self.workspace / "_hierarchical_landscapes" / "local_runs" / local_batch_id
        local_batch = _read_json(local_batch_dir / "output" / "local_landscape_batch.json")
        local_landscapes: list[dict[str, Any]] = []
        papers: dict[str, dict[str, Any]] = {}
        for record in local_batch["records"]:
            if record["local_status"] != "completed":
                raise HierarchicalReviewWritingError(f"局部主题未完成：{record['cluster_id']}")
            run = str(record["landscape_run_id"])
            local_dir = self.workspace / "_research_landscapes" / "runs" / run
            landscape = _read_json(local_dir / "output" / "research_landscape.json")
            local_landscapes.append({"cluster_id": record["cluster_id"], "cluster_title": record["cluster_title"], "landscape": landscape})
            for row in _read_jsonl(local_dir / "input" / "understandings.jsonl"):
                understanding = row["understanding"]
                papers[str(understanding["paper_id"])] = understanding
        aliases = {paper_id: f"P{index:03d}" for index, paper_id in enumerate(sorted(papers), 1)}
        projected = []
        for paper_id in sorted(papers):
            row = dict(papers[paper_id])
            row.pop("understanding_id", None)
            projected.append({"citation_key": aliases[paper_id], **row})
        return {
            "schema_version": "llm.hierarchical_review_writing_input.v1",
            "global_run_id": global_run_id,
            "local_batch_run_id": local_batch_id,
            "global_landscape": global_landscape,
            "local_landscapes": local_landscapes,
            "papers": projected,
            "citation_map": [{"citation_key": aliases[k], "paper_id": k, "paper_title": papers[k].get("paper_title", k)} for k in sorted(papers)],
            "input_sha256": _sha256({"global": global_landscape, "locals": local_landscapes, "papers": projected}),
        }


def _schema(global_landscape: dict[str, Any]) -> dict[str, Any]:
    titles = [row["title"] for row in global_landscape["global_dimensions"]]
    paragraph = {"type": "object", "additionalProperties": False, "required": ["text", "citation_keys"], "properties": {"text": {"type": "string", "minLength": 80, "maxLength": 2600, "pattern": "^(?![\\s\\S]*(?:P[0-9]{3}|dimension_|cluster_|global_|证明|普遍|根本原因|根本途径))[\\s\\S]+$"}, "citation_keys": {"type": "array", "minItems": 1, "maxItems": 24, "uniqueItems": True, "items": {"type": "string", "pattern": "^P[0-9]{3}$"}}}}
    conclusion_paragraph = json.loads(json.dumps(paragraph))
    conclusion_paragraph["properties"]["citation_keys"]["minItems"] = 0
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False, "required": ["title", "abstract", "keywords", "introduction", "chapters", "discussion", "conclusion"], "properties": {
        "title": {"type": "string", "minLength": 8, "maxLength": 80},
        "abstract": {"type": "string", "minLength": 300, "maxLength": 1200, "pattern": "^(?![\\s\\S]*(?:P[0-9]{3}|dimension_|cluster_|global_|证明|普遍|根本原因|根本途径))[\\s\\S]+$"},
        "keywords": {"type": "array", "minItems": 4, "maxItems": 8, "uniqueItems": True, "items": {"type": "string"}},
        "introduction": {"type": "array", "minItems": 2, "maxItems": 4, "items": paragraph},
        "chapters": {"type": "array", "minItems": len(titles), "maxItems": len(titles), "prefixItems": [{"type": "object", "additionalProperties": False, "required": ["chapter_index", "title", "paragraphs"], "properties": {"chapter_index": {"const": i}, "title": {"const": title}, "paragraphs": {"type": "array", "minItems": 2, "maxItems": 6, "items": paragraph}}} for i, title in enumerate(titles, 1)]},
        "discussion": {"type": "array", "minItems": 2, "maxItems": 5, "items": paragraph},
        "conclusion": {"type": "array", "minItems": 1, "maxItems": 3, "items": conclusion_paragraph},
    }}


def _calibrate_evidence_language(value: dict[str, Any]) -> dict[str, int]:
    replacements = (
        ("被证明", "在所述研究中表明"),
        ("根本原因", "重要原因"),
        ("根本途径", "重要候选途径"),
        ("证明", "表明"),
        ("普遍", "较多"),
    )
    counts: dict[str, int] = {}
    texts = [value["abstract"], *[row["text"] for row in _paragraphs(value)]]
    calibrated: list[str] = []
    for text in texts:
        for source, target in replacements:
            count = text.count(source)
            if count:
                counts[source] = counts.get(source, 0) + count
                text = text.replace(source, target)
        calibrated.append(text)
    value["abstract"] = calibrated[0]
    for paragraph, text in zip(_paragraphs(value), calibrated[1:]):
        paragraph["text"] = text
    return counts


def _validate(value: dict[str, Any], source: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    Draft202012Validator(schema).validate(value)
    forbidden_abstract = [token for token in ("证明", "普遍", "根本原因", "根本途径") if token in value["abstract"]]
    if forbidden_abstract:
        raise HierarchicalReviewWritingError(f"摘要包含证据强度过高的措辞：{forbidden_abstract}")
    allowed = {row["citation_key"] for row in source["citation_map"]}
    for paragraph in _paragraphs(value):
        unknown = set(paragraph["citation_keys"]) - allowed
        if unknown:
            raise HierarchicalReviewWritingError(f"出现未知引用：{sorted(unknown)}")
        if any(token in paragraph["text"] for token in ("dimension_", "cluster_", "global_")) or re.search(r"(?<![A-Za-z0-9_])P\d{3}(?![A-Za-z0-9_])", paragraph["text"]):
            raise HierarchicalReviewWritingError("正文泄露机器标识。")
        forbidden = [token for token in ("证明", "普遍", "根本原因", "根本途径") if token in paragraph["text"]]
        if forbidden:
            raise HierarchicalReviewWritingError(f"正文包含证据强度过高的措辞：{forbidden}")
    chars = _body_chars(value)
    if not MIN_BODY_CHARS <= chars <= MAX_BODY_CHARS:
        raise HierarchicalReviewWritingError(f"正文字符数不合格：{chars}")
    value["body_char_count"] = chars
    return value


def _restore_citation_names(value: dict[str, Any], source: dict[str, Any]) -> int:
    titles = {
        row["citation_key"]: f"《{row['paper_title']}》"
        for row in source["citation_map"]
    }
    pattern = re.compile(r"(?<![A-Za-z0-9_])P\d{3}(?![A-Za-z0-9_])")
    count = 0
    for paragraph in _paragraphs(value):
        def replace(match: re.Match[str]) -> str:
            nonlocal count
            key = match.group(0)
            title = titles.get(key)
            if title is None:
                return key
            count += 1
            return title
        paragraph["text"] = pattern.sub(replace, paragraph["text"])
    return count


def _paragraphs(value: dict[str, Any]):
    yield from value["introduction"]
    for chapter in value["chapters"]:
        yield from chapter["paragraphs"]
    yield from value["discussion"]
    yield from value["conclusion"]


def _body_chars(value: dict[str, Any]) -> int:
    return len(value["abstract"]) + sum(len(row["text"]) for row in _paragraphs(value))


def _citation_audit(value: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    used = {key for row in _paragraphs(value) for key in row["citation_keys"]}
    all_keys = {row["citation_key"] for row in source["citation_map"]}
    return {"paper_count": len(all_keys), "cited_paper_count": len(used), "uncited_paper_count": len(all_keys - used), "uncited_citation_keys": sorted(all_keys - used), "citation_coverage_ratio": len(used) / len(all_keys)}


def _render(value: dict[str, Any], source: dict[str, Any]) -> str:
    numbers = {row["citation_key"]: i for i, row in enumerate(source["citation_map"], 1)}
    def paragraph(row: dict[str, Any]) -> str:
        refs = ",".join(str(numbers[key]) for key in row["citation_keys"])
        return f"{row['text']}[{refs}]"
    lines = [f"# {value['title']}", "", "## 摘要", "", value["abstract"], "", "**关键词：**" + "；".join(value["keywords"]), "", "## 引言", ""]
    lines.extend(sum(([paragraph(row), ""] for row in value["introduction"]), []))
    for chapter in value["chapters"]:
        lines.extend([f"## {chapter['chapter_index']} {chapter['title']}", ""])
        lines.extend(sum(([paragraph(row), ""] for row in chapter["paragraphs"]), []))
    lines.extend(["## 讨论与研究展望", ""])
    lines.extend(sum(([paragraph(row), ""] for row in value["discussion"]), []))
    lines.extend(["## 结论", ""])
    lines.extend(sum(([paragraph(row), ""] for row in value["conclusion"]), []))
    lines.extend(["## 参考文献", ""])
    for i, row in enumerate(source["citation_map"], 1):
        lines.append(f"[{i}] {row['paper_title']}（本地题录元数据待核验）")
    return "\n".join(lines).rstrip() + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
