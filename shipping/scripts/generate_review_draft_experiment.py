from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.env import load_env_file
from shipping_pipeline.llm_analysis import DEFAULT_TOKENIZER_CACHE
from shipping_pipeline.llm_json_stage import execute_json_stage
from shipping_pipeline.llm_model_profile import load_model_profile
from shipping_pipeline.llm_provider import OpenAICompatibleAnalysisClient
from shipping_pipeline.llm_tokenizer import DeepSeekV4TokenCounter


SCHEMA_VERSION = "llm.review_draft_experiment.v1"
SYSTEM_PROMPT = """你是学术综述正文撰写器。
输入仅包含已经通过程序合同验证的综述提纲、Evidence 观点和语料缺口。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。
正文应围绕问题和方法形成跨论文综合，不得逐篇罗列。
不得增加输入中不存在的事实、数字、作者、年份或因果关系。
章节、段落类型、evidence_unit_ids 和 gap_ids 已由程序绑定，必须原样返回。
正文文字中不要写机器 ID，程序会根据 ID 渲染可读来源。
对仿真结果、历史数据、建议性结论和单一来源判断必须保留其适用边界。
不要把语料缺口写成已被证实的事实，也不要把研究建议写成已经实施的措施。
不要使用“学界共识”“主流观点”“实践表明”“全面梳理”等超出本次定向样本文献范围的表述。
算法运行效率、作业时间、闸室利用率和实际通过能力是不同指标，不得互相替换。"""

MIN_BODY_CHARS = 5000
MAX_BODY_CHARS = 8000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于已验证综合结果生成实验性综述正文。")
    parser.add_argument("--synthesis-run-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument(
        "--model-profile",
        type=Path,
        default=PROJECT_ROOT / "config" / "models" / "deepseek-v4-pro-official.json",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--timeout", type=int, default=900)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)
    synthesis_dir = args.synthesis_run_dir.resolve()
    synthesis = _read_json(synthesis_dir / "output" / "topic_synthesis.json")
    manifest = _read_json(synthesis_dir / "manifest.json")
    if manifest.get("status") != "completed":
        raise ValueError("只能从已完成的跨论文综合运行生成正文。")

    evidence_by_id, paper_by_id = _load_evidence(manifest, synthesis_dir)
    outline = synthesis["outline"]
    source_paper_count = len(manifest["source_runs"])
    used_ids = {
        str(evidence_id)
        for section in outline["sections"]
        for paragraph in section["paragraphs"]
        for evidence_id in paragraph["evidence_unit_ids"]
    }
    projected_evidence = [
        {
            "evidence_unit_id": evidence_id,
            "paper_title": paper_by_id[evidence_id],
            "claim": evidence_by_id[evidence_id]["claim"],
            "evidence_type": evidence_by_id[evidence_id]["evidence_type"],
            "confidence": evidence_by_id[evidence_id]["confidence"],
            "caveats": evidence_by_id[evidence_id].get("caveats", []),
        }
        for evidence_id in sorted(used_ids)
    ]
    used_paper_count = len({paper_by_id[evidence_id] for evidence_id in used_ids})
    partial_source_count = len(synthesis["coverage"]["partial_source_run_ids"])
    gap_rows = list(synthesis["evidence_map"]["corpus_gaps"])
    gap_ids = [str(row["gap_id"]) for row in gap_rows]
    review_title = (
        f"{synthesis['topic']}：基于{source_paper_count}篇样本文献的主题综述"
    )
    schema = _build_schema(
        outline=outline,
        evidence_ids=sorted(used_ids),
        gap_ids=gap_ids,
        review_title=review_title,
    )
    prompt = {
        "任务": "根据已验证提纲撰写一版可审计的中文综述正文",
        "综述主题": synthesis["topic"],
        "指定标题": review_title,
        "中心问题": outline["central_question"],
        "资料范围": {
            "定向选择论文数": source_paper_count,
            "进入正文Evidence的论文数": used_paper_count,
            "单篇分析部分完成的论文数": partial_source_count,
            "说明": "本次是工程验证样本，不是系统检索所得的完整文献集合。",
        },
        "输出JSONSchema": schema,
        "章节提纲": outline["sections"],
        "已验证Evidence": projected_evidence,
        "语料缺口": gap_rows,
        "写作要求": {
            "篇幅": "正文约5000至8000汉字",
            "风格": "学术综述，强调方法之间的关系、适用条件和证据边界",
            "禁止": [
                "不得把单篇仿真结果推广为普遍规律",
                "不得把论文建议写成已经实施或得到实践验证的事实",
                "不得宣称本次定向样本代表学界共识或完整研究版图",
                "不得把算法运算效率改写为闸室利用率、人工时间或实际通过量",
            ],
        },
    }

    profile = load_model_profile(args.model_profile)
    client = OpenAICompatibleAnalysisClient.from_env(
        model=profile.request_model,
        timeout=args.timeout,
    )
    counter = DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
    run_id = args.run_id or datetime.now(UTC).strftime("draft-%Y%m%dT%H%M%SZ")
    run_dir = (
        synthesis_dir.parents[2]
        / "_review_drafts"
        / "runs"
        / run_id
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    _write_json(
        run_dir / "input" / "source.json",
        {
            "synthesis_run_id": manifest["run_id"],
            "synthesis_input_sha256": manifest["input_sha256"],
            "used_evidence_count": len(used_ids),
            "gap_count": len(gap_ids),
        },
    )
    _write_json(run_dir / "input" / "writing_payload.json", prompt)

    parsed, result = execute_json_stage(
        run_dir=run_dir,
        stage="draft",
        task_name="topic_review_draft_experiment",
        directory_name=None,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=json.dumps(prompt, ensure_ascii=False, separators=(",", ":")),
        max_output_tokens=16000,
        context={
            "topic": synthesis["topic"],
            "synthesis_run_id": manifest["run_id"],
            "evidence_unit_ids": sorted(used_ids),
        },
        client=client,
        profile=profile,
        token_counter=counter,
    )
    try:
        _validate(parsed, schema)
        body_char_count = _validate_draft_contract(parsed)
    except Exception as exc:
        _write_json(
            run_dir / "manifest.json",
            {
                "schema_version": "llm.review_draft_experiment_run.v1",
                "run_id": run_id,
                "status": "failed",
                "source_synthesis_run_id": manifest["run_id"],
                "model_profile_id": profile.profile_id,
                "request": result,
                "failure_code": "review_draft.contract_invalid",
                "failure_message": str(exc),
            },
        )
        raise
    scope = {
        "source_paper_count": source_paper_count,
        "used_paper_count": used_paper_count,
        "partial_source_count": partial_source_count,
        "source_evidence_count": synthesis["coverage"]["source_evidence_count"],
        "used_evidence_count": len(used_ids),
    }
    markdown = _render(parsed, evidence_by_id, paper_by_id, scope)
    _write_json(run_dir / "output" / "review_draft.json", parsed)
    (run_dir / "review").mkdir(parents=True, exist_ok=True)
    (run_dir / "review" / "review_draft.md").write_text(
        markdown,
        encoding="utf-8",
    )
    _write_json(
        run_dir / "manifest.json",
        {
            "schema_version": "llm.review_draft_experiment_run.v1",
            "run_id": run_id,
            "status": "completed",
            "source_synthesis_run_id": manifest["run_id"],
            "model_profile_id": profile.profile_id,
            "request": result,
            "used_evidence_count": len(used_ids),
            "used_paper_count": used_paper_count,
            "section_count": len(parsed["sections"]),
            "body_char_count": body_char_count,
            "review_path": str(run_dir / "review" / "review_draft.md"),
        },
    )
    print(
        json.dumps(
            {
                "run_id": run_id,
                "status": "completed",
                "used_evidence_count": len(used_ids),
                "used_paper_count": used_paper_count,
                "section_count": len(parsed["sections"]),
                "body_char_count": body_char_count,
                "usage": result["usage"],
                "run_dir": str(run_dir),
                "review_path": str(run_dir / "review" / "review_draft.md"),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _load_evidence(
    manifest: dict[str, Any],
    synthesis_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    evidence_by_id: dict[str, dict[str, Any]] = {}
    paper_by_id: dict[str, str] = {}
    review_runs_root = synthesis_dir.parents[2] / "_topic_reviews" / "runs"
    for source in manifest["source_runs"]:
        run_dir = review_runs_root / str(source["run_id"])
        for row in _read_jsonl(run_dir / "evidence" / "evidence_units.jsonl"):
            evidence_id = str(row["evidence_unit_id"])
            evidence_by_id[evidence_id] = row
            paper_by_id[evidence_id] = str(source["paper_title"])
    return evidence_by_id, paper_by_id


def _build_paragraph_schema(
    *,
    paragraph_type: str,
    evidence_ids: list[str],
    gap_ids: list[str],
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "paragraph_type": {"const": paragraph_type},
            "text": {"type": "string", "minLength": 200, "maxLength": 3000},
            "evidence_unit_ids": {"const": evidence_ids},
            "gap_ids": {"const": gap_ids},
        },
        "required": ["paragraph_type", "text", "evidence_unit_ids", "gap_ids"],
    }


def _build_schema(
    *,
    outline: dict[str, Any],
    evidence_ids: list[str],
    gap_ids: list[str],
    review_title: str,
) -> dict[str, Any]:
    evidence_set = set(evidence_ids)
    gap_set = set(gap_ids)
    section_schemas: list[dict[str, Any]] = []
    for section in outline["sections"]:
        paragraph_schemas: list[dict[str, Any]] = []
        for paragraph in section["paragraphs"]:
            bound_evidence = [str(row) for row in paragraph["evidence_unit_ids"]]
            bound_gaps = [str(row) for row in paragraph["gap_ids"]]
            if not set(bound_evidence).issubset(evidence_set):
                raise ValueError("提纲段落引用了正文输入中不存在的 Evidence。")
            if not set(bound_gaps).issubset(gap_set):
                raise ValueError("提纲段落引用了正文输入中不存在的语料缺口。")
            paragraph_schemas.append(
                _build_paragraph_schema(
                    paragraph_type=str(paragraph["paragraph_role"]),
                    evidence_ids=bound_evidence,
                    gap_ids=bound_gaps,
                )
            )
        section_schemas.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "heading": {"const": str(section["title"])},
                    "paragraphs": {
                        "type": "array",
                        "prefixItems": paragraph_schemas,
                        "items": False,
                        "minItems": len(paragraph_schemas),
                        "maxItems": len(paragraph_schemas),
                    },
                },
                "required": ["heading", "paragraphs"],
            }
        )
    conclusion = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "paragraph_type": {"const": "evidence_synthesis"},
            "text": {"type": "string", "minLength": 200, "maxLength": 2000},
            "evidence_unit_ids": {
                "type": "array",
                "items": {"type": "string", "enum": evidence_ids},
                "minItems": 1,
                "maxItems": min(20, len(evidence_ids)),
                "uniqueItems": True,
            },
            "gap_ids": {"const": []},
        },
        "required": ["paragraph_type", "text", "evidence_unit_ids", "gap_ids"],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "title": {"const": review_title},
            "abstract": {"type": "string", "minLength": 150, "maxLength": 1200},
            "keywords": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 40},
                "minItems": 3,
                "maxItems": 8,
                "uniqueItems": True,
            },
            "sections": {
                "type": "array",
                "prefixItems": section_schemas,
                "items": False,
                "minItems": len(section_schemas),
                "maxItems": len(section_schemas),
            },
            "conclusion": conclusion,
        },
        "required": [
            "schema_version",
            "title",
            "abstract",
            "keywords",
            "sections",
            "conclusion",
        ],
    }


def _validate(payload: object, schema: dict[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        raise ValueError(f"review_draft.schema_invalid at {path}: {error.message}")


def _validate_draft_contract(payload: dict[str, Any]) -> int:
    body_char_count = sum(
        len(str(paragraph["text"]))
        for section in payload["sections"]
        for paragraph in section["paragraphs"]
    )
    if not MIN_BODY_CHARS <= body_char_count <= MAX_BODY_CHARS:
        raise ValueError(
            "review_draft.body_length_invalid: "
            f"{body_char_count} not in [{MIN_BODY_CHARS}, {MAX_BODY_CHARS}]"
        )
    return body_char_count


def _render(
    payload: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    paper_by_id: dict[str, str],
    scope: dict[str, int],
) -> str:
    lines = [
        f"# {payload['title']}",
        "",
        "## 摘要",
        "",
        payload["abstract"],
        "",
        f"**关键词：** {'；'.join(payload['keywords'])}",
        "",
        "## 资料范围说明",
        "",
        (
            f"本文基于本次工程验证定向选择的 {scope['source_paper_count']} 篇论文，"
            f"其中 {scope['used_paper_count']} 篇的 Evidence 进入正文。"
            f"单篇分析共形成 {scope['source_evidence_count']} 条 Evidence，"
            f"正文采用 {scope['used_evidence_count']} 条；"
            f"{scope['partial_source_count']} 篇单篇分析存在已记录的局部失败。"
            "该样本未经过系统检索和文献质量分级，因此本文只能视为主题综述草稿，"
            "不能据此推断完整学界共识。"
        ),
        "",
    ]
    cited_ids: list[str] = []
    for section in payload["sections"]:
        lines.extend([f"## {section['heading']}", ""])
        for paragraph in section["paragraphs"]:
            ids = [str(row) for row in paragraph["evidence_unit_ids"]]
            cited_ids.extend(ids)
            papers = list(dict.fromkeys(paper_by_id[row] for row in ids))
            suffix = f"（证据来源：{'；'.join(papers)}）" if papers else ""
            lines.extend([str(paragraph["text"]) + suffix, ""])
    conclusion = payload["conclusion"]
    conclusion_ids = [str(row) for row in conclusion["evidence_unit_ids"]]
    cited_ids.extend(conclusion_ids)
    conclusion_papers = list(
        dict.fromkeys(paper_by_id[row] for row in conclusion_ids)
    )
    lines.extend(
        [
            "## 结论",
            "",
            str(conclusion["text"])
            + (
                f"（证据来源：{'；'.join(conclusion_papers)}）"
                if conclusion_papers
                else ""
            ),
            "",
            "## 证据索引",
            "",
        ]
    )
    for evidence_id in dict.fromkeys(cited_ids):
        lines.extend(
            [
                f"- `{evidence_id}`｜{paper_by_id[evidence_id]}："
                f"{evidence_by_id[evidence_id]['claim']}"
            ]
        )
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON必须是对象：{path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
