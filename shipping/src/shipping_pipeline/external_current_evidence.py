from __future__ import annotations

import hashlib
import json
import os
import urllib.request
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse


ROOT = "_external_current_evidence"


class ExternalCurrentEvidenceError(ValueError):
    pass


class ExternalCurrentEvidenceRunner:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(
        self,
        *,
        writing_run_id: str,
        run_id: str,
        api_url: str,
        api_key_env: str = "LLM_ANALYSIS_API_KEY",
        model: str = "deepseek-v4-pro",
        timeout: int = 300,
    ) -> dict[str, Any]:
        run_dir = self.workspace / ROOT / "runs" / run_id
        if run_dir.exists():
            raise ExternalCurrentEvidenceError(f"run_id已存在：{run_id}")
        run_dir.mkdir(parents=True)
        writing_dir = self.workspace / "_hierarchical_review_writing" / "runs" / writing_run_id
        manifest = _read_json(writing_dir / "manifest.json")
        if manifest.get("status") != "completed":
            raise ExternalCurrentEvidenceError("层级综述写作运行不可用。")
        temporal = _read_json(writing_dir / "audit" / "temporal_adequacy.json")
        if not temporal.get("external_current_evidence_needed"):
            raise ExternalCurrentEvidenceError("论文证据当前性充分，不需要联网补充。")
        writing_input = _read_json(writing_dir / "input" / "writing_input.json")
        topic = str(writing_input["global_landscape"].get("topic") or "")
        schema = _schema()
        request_payload = {
            "model": model,
            "input": _prompt(topic, temporal),
            "tools": [{"type": "web_search"}],
            "tool_choice": {"type": "web_search"},
            "text": {"format": {"type": "json_schema", "name": "external_current_evidence", "strict": True, "schema": schema}},
            "max_output_tokens": 8192,
            "max_tool_calls": 12,
        }
        _write_json(run_dir / "input" / "request.json", request_payload)
        key = os.environ.get(api_key_env, "").strip()
        if not key:
            raise ExternalCurrentEvidenceError(f"缺少环境变量：{api_key_env}")
        endpoint = _responses_endpoint(api_url)
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        started_at = _now()
        try:
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read()
            except urllib.error.HTTPError as exc:
                error_body = exc.read()
                (run_dir / "provider_error_response.json").write_bytes(error_body)
                message = error_body.decode("utf-8", errors="replace")
                raise ExternalCurrentEvidenceError(f"Responses API返回HTTP {exc.code}：{message}") from exc
            (run_dir / "raw_response.json").write_bytes(raw)
            response_payload = json.loads(raw)
            opened_urls = _opened_urls(response_payload)
            result = json.loads(_output_text(response_payload))
            evidence = _validate_result(result, opened_urls, temporal)
            _write_json(run_dir / "output" / "external_current_evidence.json", evidence)
            _write_text(run_dir / "review" / "external_current_evidence.md", _render_evidence(evidence))
            review = (writing_dir / "review" / "hierarchical_review.md").read_text(encoding="utf-8")
            _write_text(run_dir / "review" / "hierarchical_review_with_web_evidence.md", _merge_review(review, evidence))
            status = "completed"
            failure = None
        except Exception as exc:
            status = "failed"
            failure = {"error_code": type(exc).__name__, "error_message": str(exc), "recorded_at": _now()}
            _write_json(run_dir / "audit" / "failure.json", failure)
        run_manifest = {
            "schema_version": "external_current_evidence.run.v1",
            "run_id": run_id,
            "status": status,
            "started_at": started_at,
            "finished_at": _now(),
            "writing_run_id": writing_run_id,
            "review_as_of_year": temporal["review_as_of_year"],
            "model": model,
            "failure": failure,
        }
        _write_json(run_dir / "manifest.json", run_manifest)
        return {"run_id": run_id, "status": status, "run_dir": str(run_dir), "failure": failure}


def _schema() -> dict[str, Any]:
    source = {
        "type": "object",
        "additionalProperties": False,
        "required": ["publisher", "page_title", "published_at", "url", "source_class"],
        "properties": {
            "publisher": {"type": "string", "minLength": 2, "maxLength": 200},
            "page_title": {"type": "string", "minLength": 4, "maxLength": 300},
            "published_at": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
            "url": {"type": "string", "pattern": "^https?://"},
            "source_class": {"type": "string", "enum": ["primary_official", "secondary_authoritative"]},
        },
    }
    claim = {
        "type": "object",
        "additionalProperties": False,
        "required": ["statement", "metric_key", "period_status", "statistic_year", "source"],
        "properties": {
            "statement": {"type": "string", "minLength": 20, "maxLength": 600},
            "metric_key": {"type": "string", "pattern": "^[a-z0-9_]{3,80}$"},
            "period_status": {"type": "string", "enum": ["final", "partial", "forecast", "event"]},
            "statistic_year": {"type": "integer", "minimum": 2000, "maximum": 2999},
            "source": source,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["claims"],
        "properties": {"claims": {"type": "array", "minItems": 1, "maxItems": 5, "items": claim}},
    }


def _prompt(topic: str, temporal: dict[str, Any]) -> str:
    return (
        f"围绕综述主题“{topic}”搜索能够补足系统运行当前基线的公开事实。"
        f"综述年份为{temporal['review_as_of_year']}年，论文证据最新仅到{temporal['latest_system_observation_year']}年，"
        f"只保留统计年份不早于{temporal['freshness_min_year']}年的结果。"
        "优先打开政府部门、法定管理机构和项目运营机构的原始发布页面；只有原始页面不存在时才用权威二手来源。"
        "只选3至5条对当前判断最有用的原子事实，一条只表达一个指标或一个已经发生的事件。"
        "为每条事实给出稳定的英文metric_key；同一指标同一统计年度只能保留一条。"
        "年度最终统计优先于阶段统计和预计值；存在最终统计时不得输出阶段值或预计值。"
        "最多进行12次搜索或页面操作，完成必要核验后必须输出JSON结果。"
        "每条事实必须来自你本次实际打开的页面，并给出发布机构、页面标题、明确发布日期、URL和统计年份。"
        "不得用搜索摘要、论文旧数据或常识推断当前状态。只输出符合JSON Schema的对象。"
    )


def _responses_endpoint(api_url: str) -> str:
    parsed = urlparse(api_url)
    if not parsed.scheme or not parsed.netloc:
        raise ExternalCurrentEvidenceError("API URL无效。")
    return f"{parsed.scheme}://{parsed.netloc}/v1/responses"


def _opened_urls(payload: dict[str, Any]) -> set[str]:
    urls = set()
    for item in payload.get("output") or []:
        action = item.get("action") or {}
        if item.get("type") == "web_search_call" and action.get("type") == "open_page" and action.get("url"):
            urls.add(_normalize_url(str(action["url"])))
    return urls


def _output_text(payload: dict[str, Any]) -> str:
    texts = [
        part["text"]
        for item in payload.get("output") or []
        if item.get("type") == "message"
        for part in item.get("content") or []
        if part.get("type") == "output_text"
    ]
    if len(texts) != 1:
        raise ExternalCurrentEvidenceError("Responses API没有返回唯一结构化文本。")
    return texts[0]


def _validate_result(result: dict[str, Any], opened_urls: set[str], temporal: dict[str, Any]) -> dict[str, Any]:
    from jsonschema import Draft202012Validator

    Draft202012Validator(_schema()).validate(result)
    claims = []
    seen = set()
    seen_metric_periods = set()
    for row in result["claims"]:
        url = _normalize_url(row["source"]["url"])
        if url not in opened_urls:
            raise ExternalCurrentEvidenceError(f"来源URL未在搜索动作中打开：{url}")
        if row["statistic_year"] < temporal["freshness_min_year"]:
            raise ExternalCurrentEvidenceError(f"联网事实统计年份过旧：{row['statistic_year']}")
        if row["period_status"] not in {"final", "event"}:
            raise ExternalCurrentEvidenceError(
                f"联网事实不是最终统计或已发生事件：{row['metric_key']}={row['period_status']}"
            )
        metric_period = (row["metric_key"], row["statistic_year"])
        if metric_period in seen_metric_periods:
            raise ExternalCurrentEvidenceError(
                f"同一指标同一统计年度存在多条联网事实：{row['metric_key']}@{row['statistic_year']}"
            )
        seen_metric_periods.add(metric_period)
        fingerprint = hashlib.sha256((row["statement"] + "\n" + url).encode("utf-8")).hexdigest()[:20]
        if fingerprint in seen:
            raise ExternalCurrentEvidenceError("联网事实重复。")
        seen.add(fingerprint)
        source = dict(row["source"])
        source["url"] = url
        claims.append({"web_evidence_id": f"W{len(claims) + 1:03d}", **row, "source": source})
    return {
        "schema_version": "external_current_evidence.v1",
        "review_as_of_year": temporal["review_as_of_year"],
        "paper_evidence_cutoff_year": temporal["latest_system_observation_year"],
        "label": "联网补充｜非论文证据",
        "claims": claims,
    }


def _render_evidence(evidence: dict[str, Any]) -> str:
    lines = ["# 联网补充的当前状态证据", "", "> 本节不是论文证据，不能替代论文方法与机制分析。", ""]
    for row in evidence["claims"]:
        source = row["source"]
        lines.extend([
            f"<u><em>【联网补充｜非论文证据｜{row['web_evidence_id']}｜统计年度{row['statistic_year']}】{row['statement']}</em></u>",
            "",
            f"来源：[{source['publisher']}：《{source['page_title']}》]({source['url']})，{source['published_at']}，`{source['source_class']}`。",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _merge_review(review: str, evidence: dict[str, Any]) -> str:
    marker = "## 摘要"
    if marker not in review:
        raise ExternalCurrentEvidenceError("综述Markdown缺少摘要标题。")
    section = _render_evidence(evidence).replace("# 联网补充的当前状态证据", "## 联网补充的当前状态证据", 1)
    return review.replace(marker, section + "\n" + marker, 1)


def _normalize_url(value: str) -> str:
    clean, _ = urldefrag(value)
    return clean.rstrip("/")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()
