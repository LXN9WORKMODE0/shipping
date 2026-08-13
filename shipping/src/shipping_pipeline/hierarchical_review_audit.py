from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .hierarchical_review_writing import _paragraphs, _read_json, _write_json, _write_text
from .llm_analysis import DEFAULT_TOKENIZER_CACHE
from .llm_json_stage import execute_json_stage
from .llm_model_profile import load_model_profile
from .llm_provider import OpenAICompatibleAnalysisClient
from .llm_tokenizer import DeepSeekV4TokenCounter
from .reference_catalog import extract_local_reference, format_gbt_reference


ROOT = "_hierarchical_review_audits"
NUMERIC_RE = re.compile(r"(?<![A-Za-z])(?:\d+(?:\.\d+)?)(?:\s*[%％]|\s*[万亿千百]?[吨艘次小时分钟米m²³kW]*)?")
COMPARISON_RE = re.compile(r"增加|减少|提高|降低|超过|低于|高于|优于|大于|小于|最多|最少|最高|最低|达到|仅|约")
ISSN_JOURNALS = {
    "1000-3428": ("计算机工程", "https://openalex.org/S2764825856"),
    "0258-2724": ("西南交通大学学报", "https://openalex.org/S4306535130"),
    "1000-6788": ("系统工程理论与实践", "https://portal.issn.org/resource/ISSN/1000-6788"),
    "1006-7973": ("中国水运", "https://xueshu.baidu.com/usercenter/journal/baseinfo?entity_id=4c31370154d5a8bed6064fbb300e533e"),
    "1002-4972": ("水运工程", "https://g-city.sass.org.cn/_upload/article/files/83/a0/dbfe35554811b50d2a9092e71ee7/827c2008-db36-432a-9542-83c6589fc5af.pdf"),
    "1671-7953": ("船海工程", "https://www.chuanhaigongcheng.cn/"),
    "1672-9846": ("武汉交通职业学院学报", "https://www.newcnki.net/info/6054.html"),
    "1006-6349": ("中国三峡", "https://www.ctgne.com/sxjt/attachDir/2026/05/2026052217295054086.pdf"),
    "1000-8152": ("控制理论与应用", "http://jcta.alljournals.ac.cn/cta_cn/ch/index.aspx"),
    "1000-1638": ("内蒙古大学学报（自然科学版）", "https://ndxb.imu.edu.cn/"),
    "1001-5485": ("长江科学院院报", "https://openalex.org/S2764996046"),
    "1001-4179": ("人民长江", "https://opaj.napstic.cn/periodical/1162"),
}
VERIFIED_REFERENCE_OVERRIDES = {
    "Best fit算法在三峡船闸调度中的应用": ({"journal": "华中科技大学学报（自然科学版）", "volume": "33"}, "https://xb.dlmu.edu.cn/CN/10.16411/j.cnki.issn1006-7736.2017.02.005"),
    "“碳减排”视域下内河流域梯级枢纽联合通航调度优化": ({"authors": ["高攀", "方志伟", "赵旭"]}, "local_markdown:first_page"),
    "三峡-葛洲坝枢纽引航道和船闸群协同调度的多目标混合启发式算法": ({"year": 2025, "journal": "同济大学学报（自然科学版）", "volume": "53", "issue": "9", "pages": "1444-1456"}, "https://tjxb.ijournals.cn/jtuns/article/abstract/202509013"),
    "三峡-葛洲坝枢纽通航作业的多目标调度优化": ({"entry_type": "doctoral_thesis", "authors": ["郑倩倩"], "year": 2024, "institution": "武汉理工大学", "degree": "博士学位论文"}, "https://cnki.istiz.org.cn/kcms/detail/search.aspx?dbcode=CDFD&sfield=kw&skey=hub"),
    "三峡-葛洲坝梯级枢纽通航二十年创新发展与实践": ({"year": 2023, "journal": "中国工程科学", "volume": "25", "issue": "1", "pages": "155-166", "doi": "10.15302/J-SSCAE-2023.07.006"}, "https://www.engineering.org.cn/sscae/CN/1160094161867170039"),
    "三峡-葛洲坝联合调度系统闸室编排快速算法": ({"authors": ["孙波", "齐欢", "张晓盼", "蔡霄"], "journal": "计算机技术与发展", "volume": "16"}, "https://sdnye.cbpt.cnki.net/portal/journal/portal/client/paper/622d349a3a05f8cc2202ebf2f0d02931"),
    "三峡临时船闸引航道外河段通航条件试验研究": ({"authors": ["舒荣龙", "杜宗伟"], "year": 1999, "journal": "人民长江", "volume": "30", "issue": "5"}, "https://www.rmcjzz.cjw.cn/cn/article/pdf/preview/rmcj_5636.pdf"),
    "三峡新通道研究进展及主要技术问题": ({"year": 2016, "journal": "重庆交通大学学报（自然科学版）", "volume": "35", "issue": "增刊1", "pages": "33-40"}, "https://www.sciengine.com/doi/pdf/319CE3BE20E041FBB2B37861BEAC6439"),
    "检修期三峡-葛洲坝枢纽船舶过闸调度建模与优化": ({"journal": "工业工程", "volume": "27", "pages": "43-53,86"}, "https://iej.gdut.edu.cn/article/doi/10.3969/j.issn.1007-7375.220257"),
}

SYSTEM_PROMPT = """你是中文学术综述的主张审计员。只依据输入段落及其所列论文理解审计，不使用外部知识，只输出JSON。
逐条检查：数字、单位、年份、大小关系、增减方向、比较范围、因果强度、验证类型和限定条件。一个数字出现在某篇论文中，不代表段落对它的关系描述必然正确。
verdict只能是supported、needs_revision或conflict。存在明确算术/比较矛盾、来源间年份或口径冲突时用conflict；仅证据不足或措辞过强用needs_revision。
不得编造修订事实。suggested_revision只能删除不受支持内容、降低强度、显化冲突或沿用输入论文理解中的表述。"""


class HierarchicalReviewAuditError(ValueError):
    pass


class HierarchicalReviewAuditRunner:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(
        self,
        *,
        writing_run_id: str,
        run_id: str | None = None,
        model_profile_path: str | Path,
        api_url: str | None = None,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        reuse_audit_from_run_id: str | None = None,
        timeout: int = 1800,
    ) -> dict[str, Any]:
        resolved = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = self.workspace / ROOT / "runs" / resolved
        if run_dir.exists():
            raise HierarchicalReviewAuditError(f"run_id已存在：{resolved}")
        run_dir.mkdir(parents=True)
        source_dir = self.workspace / "_hierarchical_review_writing" / "runs" / writing_run_id
        manifest = _read_json(source_dir / "manifest.json")
        if manifest.get("status") != "completed":
            raise HierarchicalReviewAuditError("层级综述运行不可用。")
        review = _read_json(source_dir / "output" / "hierarchical_review.json")
        writing_input = _read_json(source_dir / "input" / "writing_input.json")
        claims = _claim_catalogue(review)
        papers = {row["citation_key"]: row for row in writing_input["papers"]}
        deterministic = [_deterministic_audit(row, papers) for row in claims]
        candidates = claims
        _write_json(run_dir / "input" / "claims.json", {"claims": claims})
        _write_json(run_dir / "audit" / "deterministic_numeric_support.json", {"claims": deterministic})

        llm_results: list[dict[str, Any]] = []
        stage_results: list[dict[str, Any]] = []
        if reuse_audit_from_run_id:
            prior = _read_json(self.workspace / ROOT / "runs" / reuse_audit_from_run_id / "output" / "audit.json")
            if prior.get("writing_run_id") != writing_run_id:
                raise HierarchicalReviewAuditError("复用审计与目标写作运行不一致。")
            llm_results = prior["audits"]
            stage_results = [{"status": "reused", "run_id": reuse_audit_from_run_id}]
        else:
            profile = load_model_profile(Path(model_profile_path))
            client = OpenAICompatibleAnalysisClient.from_env(
                api_url=api_url, api_key_env=api_key_env, model=profile.request_model, timeout=timeout
            )
            counter = DeepSeekV4TokenCounter(profile, DEFAULT_TOKENIZER_CACHE)
            for chapter, rows in _group_by_section(candidates).items():
                projected = [{**row, "cited_papers": [_audit_paper_projection(papers[key]) for key in row["citation_keys"]]} for row in rows]
                schema = _audit_schema([row["claim_id"] for row in rows])
                parsed, stage = execute_json_stage(
                    run_dir=run_dir, stage=f"claim_audit_{chapter}", task_name="hierarchical_review_claim_audit",
                    directory_name=f"generation/{chapter}", system_prompt=SYSTEM_PROMPT,
                    user_prompt=json.dumps({"待审计段落": projected, "输出Schema": schema}, ensure_ascii=False),
                    max_output_tokens=min(12000, profile.model_max_output_tokens),
                    context={"writing_run_id": writing_run_id, "section": chapter, "claim_count": len(rows)},
                    client=client, profile=profile, token_counter=counter,
                )
                Draft202012Validator(schema).validate(parsed)
                llm_results.extend(parsed["audits"])
                stage_results.append(stage)

        references = _build_references(self.workspace, writing_input)
        audit_by_id = {row["claim_id"]: row for row in llm_results}
        issue_count = sum(row["verdict"] != "supported" for row in llm_results)
        output = {
            "schema_version": "hierarchical_review.audit.v1",
            "run_id": resolved,
            "writing_run_id": writing_run_id,
            "claim_count": len(claims),
            "audited_claim_count": len(llm_results),
            "issue_count": issue_count,
            "audits": llm_results,
            "references": references,
        }
        _write_json(run_dir / "output" / "audit.json", output)
        _write_json(run_dir / "output" / "references.json", {"references": references})
        _write_text(run_dir / "review" / "claim_audit.md", _render_audit(output, claims))
        _write_text(run_dir / "review" / "audited_review.md", _render_review(review, writing_input, references, audit_by_id))
        completed = sum(row["status"] == "complete" for row in references)
        result_manifest = {
            "schema_version": "hierarchical_review.audit_run.v1",
            "run_id": resolved,
            "status": "completed" if issue_count == 0 and completed == len(references) else "completed_with_findings",
            "writing_run_id": writing_run_id,
            "claim_count": len(claims),
            "audited_claim_count": len(llm_results),
            "issue_count": issue_count,
            "reference_count": len(references),
            "complete_reference_count": completed,
            "partial_reference_count": len(references) - completed,
            "stage_results": stage_results,
        }
        _write_json(run_dir / "manifest.json", result_manifest)
        return {**result_manifest, "run_dir": str(run_dir), "review_path": str(run_dir / "review" / "audited_review.md")}


def _claim_catalogue(review: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sections = [("引言", review["introduction"])]
    sections.extend((chapter["title"], chapter["paragraphs"]) for chapter in review["chapters"])
    sections.extend((("讨论与研究展望", review["discussion"]), ("结论", review["conclusion"])))
    index = 0
    for section, paragraphs in sections:
        for paragraph_index, paragraph in enumerate(paragraphs, 1):
            index += 1
            text = paragraph["text"]
            rows.append({
                "claim_id": f"claim_{index:03d}", "section": section, "paragraph_index": paragraph_index,
                "text": text, "citation_keys": paragraph["citation_keys"],
                "numeric_facts": [m.group(0).strip() for m in NUMERIC_RE.finditer(text)],
                "has_comparison": bool(COMPARISON_RE.search(text)),
            })
    return rows


def _deterministic_audit(claim: dict[str, Any], papers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    support = "\n".join(json.dumps(papers[key], ensure_ascii=False) for key in claim["citation_keys"])
    unsupported = [fact for fact in claim["numeric_facts"] if _normalize_number(fact) not in _normalize_number(support)]
    return {"claim_id": claim["claim_id"], "numeric_facts": claim["numeric_facts"], "unsupported_numeric_facts": unsupported}


def _normalize_number(value: str) -> str:
    return re.sub(r"[\s,，]", "", value).replace("％", "%")


def _audit_paper_projection(paper: dict[str, Any]) -> dict[str, Any]:
    return {key: paper.get(key) for key in ("citation_key", "paper_title", "study_context", "contributions", "limitations", "unresolved_questions")}


def _group_by_section(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        existing = next((name for name, values in result.items() if values[0]["section"] == row["section"]), None)
        key = existing or f"section_{len(result) + 1:02d}"
        result.setdefault(key, []).append(row)
    return result


def _audit_schema(claim_ids: list[str]) -> dict[str, Any]:
    item = {"type": "object", "additionalProperties": False, "required": ["claim_id", "verdict", "issues", "suggested_revision"], "properties": {
        "claim_id": {"type": "string", "enum": claim_ids},
        "verdict": {"type": "string", "enum": ["supported", "needs_revision", "conflict"]},
        "issues": {"type": "array", "maxItems": 8, "items": {"type": "object", "additionalProperties": False, "required": ["issue_type", "problematic_text", "explanation", "citation_keys"], "properties": {
            "issue_type": {"type": "string", "enum": ["numeric_support", "numeric_relation", "source_conflict", "causal_overreach", "scope_overreach", "validation_mismatch"]},
            "problematic_text": {"type": "string"}, "explanation": {"type": "string"},
            "citation_keys": {"type": "array", "items": {"type": "string"}},
        }}},
        "suggested_revision": {"type": ["string", "null"]},
    }}
    return {"type": "object", "additionalProperties": False, "required": ["audits"], "properties": {"audits": {"type": "array", "minItems": len(claim_ids), "maxItems": len(claim_ids), "items": item}}}


def _build_references(workspace: Path, writing_input: dict[str, Any]) -> list[dict[str, Any]]:
    verified: dict[str, dict[str, Any]] = {}
    for path in (workspace / "_reference_catalogs" / "runs").glob("*/output/references.json"):
        try:
            for row in _read_json(path).get("references", []):
                if row.get("status") == "complete":
                    verified[str(row["paper_id"])] = row
        except (OSError, ValueError, KeyError):
            continue
    markdown_index = _markdown_index(workspace)
    references = []
    for number, mapping in enumerate(writing_input["citation_map"], 1):
        paper_id = str(mapping["paper_id"])
        if paper_id in verified:
            row = dict(verified[paper_id])
            row["number"] = number
            row["catalog_source"] = "verified_catalog"
        else:
            match = markdown_index.get(paper_id)
            if match is None:
                row = {"paper_id": paper_id, "title": mapping["paper_title"], "authors": [], "year": None, "journal": None, "pages": None, "entry_type": "article", "status": "partial", "missing_fields": ["authors", "year", "journal", "pages"], "warnings": ["未定位规范化Markdown"]}
            else:
                workspace_id, markdown_path, markdown = match
                row = extract_local_reference(markdown, paper_id=paper_id, paper_title=mapping["paper_title"], workspace_paper_id=workspace_id, markdown_path=markdown_path, source_run_id="hierarchical-review-audit")
                _apply_issn_journal(row)
                _apply_verified_override(row)
                _finalize_local_reference(row)
            row["number"] = number
            row["catalog_source"] = "local_markdown"
        references.append(row)
    return references


def _markdown_index(workspace: Path) -> dict[str, tuple[str, Path, str]]:
    index = {}
    screening_root = workspace / "_paper_pool_screenings" / "runs"
    for ledger in screening_root.glob("*/runs/child_runs.jsonl"):
        try:
            records = [json.loads(line) for line in ledger.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        except (OSError, UnicodeError, ValueError):
            continue
        for record in records:
            paper_id = str(record.get("paper_id", ""))
            workspace_id = str(record.get("workspace_paper_id", ""))
            path = workspace / workspace_id / "normalized" / "document.md"
            if paper_id and workspace_id and paper_id not in index and path.is_file():
                try:
                    index[paper_id] = (workspace_id, path, path.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError):
                    pass
    for path in workspace.glob("*/normalized/document.md"):
        try:
            markdown = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        first = "\n".join(markdown.splitlines()[:30])
        for title in re.findall(r"^#\s+(.+)$", first, flags=re.MULTILINE):
            clean = re.sub(r"<[^>]+>", "", title).strip()
            index.setdefault(clean, (path.parent.parent.name, path, markdown))
        for directory_title in (path.parent.parent.name,):
            index.setdefault(directory_title, (path.parent.parent.name, path, markdown))
    return index


def _apply_issn_journal(row: dict[str, Any]) -> None:
    if row.get("journal"):
        return
    match = re.match(r"(\d{4}-\d{3}[\dXx])", str(row.get("article_number", "")))
    if not match or match.group(1) not in ISSN_JOURNALS:
        return
    issn = match.group(1)
    journal, url = ISSN_JOURNALS[issn]
    row["journal"] = journal
    row.setdefault("provenance", {}).setdefault("journal", []).append({
        "source_type": "external_bibliographic_record", "source_id": f"issn-{issn}",
        "url": url, "reason": "verified_issn_mapping",
    })


def _apply_verified_override(row: dict[str, Any]) -> None:
    override = VERIFIED_REFERENCE_OVERRIDES.get(str(row.get("paper_id", "")))
    if override is None:
        return
    fields, source = override
    for field, value in fields.items():
        row[field] = value
        row.setdefault("provenance", {}).setdefault(field, []).append({
            "source_type": "verified_bibliographic_record",
            "url": source if source.startswith("http") else None,
            "path": source if not source.startswith("http") else None,
            "reason": "verified_override",
        })


def _finalize_local_reference(row: dict[str, Any]) -> None:
    required = ("title", "authors", "year", "journal", "pages") if row.get("entry_type") == "article" else ("title", "authors", "year", "institution", "degree")
    missing = [field for field in required if not row.get(field)]
    row["missing_fields"] = missing
    row["warnings"] = row.get("warnings", [])
    row["status"] = "complete" if not missing else "partial"


def _render_audit(output: dict[str, Any], claims: list[dict[str, Any]]) -> str:
    by_id = {row["claim_id"]: row for row in claims}
    lines = ["# 层级综述数值与主张审计", "", f"- 审计段落：{output['audited_claim_count']}", f"- 发现问题：{output['issue_count']}", ""]
    for audit in output["audits"]:
        if audit["verdict"] == "supported":
            continue
        claim = by_id[audit["claim_id"]]
        lines.extend([f"## {audit['claim_id']}｜{claim['section']}｜{audit['verdict']}", "", claim["text"], ""])
        for issue in audit["issues"]:
            lines.append(f"- `{issue['issue_type']}`：{issue['problematic_text']}；{issue['explanation']}")
        if audit["suggested_revision"]:
            lines.extend(["", "建议改写：", "", audit["suggested_revision"], ""])
    return "\n".join(lines).rstrip() + "\n"


def _render_review(review: dict[str, Any], writing_input: dict[str, Any], references: list[dict[str, Any]], audits: dict[str, dict[str, Any]]) -> str:
    lines = [f"# {review['title']}", "", "## 摘要", "", review["abstract"], "", "**关键词：**" + "；".join(review["keywords"]), ""]
    index = 0
    sections = [("引言", review["introduction"])] + [(c["title"], c["paragraphs"]) for c in review["chapters"]] + [("讨论与研究展望", review["discussion"]), ("结论", review["conclusion"])]
    number = {row["citation_key"]: i for i, row in enumerate(writing_input["citation_map"], 1)}
    for section, paragraphs in sections:
        lines.extend([f"## {section}", ""])
        for paragraph in paragraphs:
            index += 1
            audit = audits.get(f"claim_{index:03d}")
            text = paragraph["text"]
            cites = ",".join(str(number[k]) for k in paragraph["citation_keys"])
            lines.extend([text + (f"[{cites}]" if cites else ""), ""])
            if audit and audit["verdict"] != "supported":
                lines.extend([f"> 审计状态：{audit['verdict']}。建议改写仅见独立审计报告，未自动覆盖正文。", ""])
    lines.extend(["## 参考文献", ""])
    lines.extend(format_gbt_reference(row) if row["status"] == "complete" else f"[{row['number']}] {row['title']}（题录缺失：{'、'.join(row['missing_fields'])}）" for row in references)
    return "\n".join(lines).rstrip() + "\n"
