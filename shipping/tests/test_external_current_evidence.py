from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.external_current_evidence import (  # noqa: E402
    ExternalCurrentEvidenceError,
    _render_evidence,
    _responses_endpoint,
    _validate_result,
)


class ExternalCurrentEvidenceTests(unittest.TestCase):
    def test_chat_completion_url_is_mapped_to_responses_endpoint(self) -> None:
        self.assertEqual(
            _responses_endpoint("https://api.deepseek.com/v1/chat/completions"),
            "https://api.deepseek.com/v1/responses",
        )

    def test_claim_url_must_have_been_opened_by_web_search(self) -> None:
        result = _result("https://agency.gov.cn/report")
        with self.assertRaisesRegex(ExternalCurrentEvidenceError, "未在搜索动作中打开"):
            _validate_result(result, set(), _temporal())

    def test_old_web_statistic_is_rejected(self) -> None:
        result = _result("https://agency.gov.cn/report", statistic_year=2022)
        with self.assertRaisesRegex(ExternalCurrentEvidenceError, "统计年份过旧"):
            _validate_result(result, {"https://agency.gov.cn/report"}, _temporal())

    def test_render_uses_explicit_non_paper_label(self) -> None:
        evidence = _validate_result(
            _result("https://agency.gov.cn/report"),
            {"https://agency.gov.cn/report"},
            _temporal(),
        )
        rendered = _render_evidence(evidence)
        self.assertIn("<u><em>【联网补充｜非论文证据｜W001｜统计年度2025】", rendered)
        self.assertIn("https://agency.gov.cn/report", rendered)

    def test_partial_or_forecast_statistic_is_rejected(self) -> None:
        result = _result("https://agency.gov.cn/report")
        result["claims"][0]["period_status"] = "forecast"
        with self.assertRaisesRegex(ExternalCurrentEvidenceError, "不是最终统计"):
            _validate_result(result, {"https://agency.gov.cn/report"}, _temporal())

    def test_same_metric_and_year_is_rejected(self) -> None:
        result = _result("https://agency.gov.cn/report")
        duplicate = dict(result["claims"][0])
        duplicate["statement"] = "该机构另一页面公布同一年度运行量达到一百七十四百万吨。"
        duplicate["source"] = dict(duplicate["source"], url="https://agency.gov.cn/other")
        result["claims"].append(duplicate)
        with self.assertRaisesRegex(ExternalCurrentEvidenceError, "同一指标同一统计年度"):
            _validate_result(
                result,
                {"https://agency.gov.cn/report", "https://agency.gov.cn/other"},
                _temporal(),
            )


def _result(url: str, *, statistic_year: int = 2025) -> dict:
    return {"claims": [{
        "statement": "该机构公布的年度运行量达到一百七十三百万吨。",
        "metric_key": "annual_cargo_throughput",
        "period_status": "final",
        "statistic_year": statistic_year,
        "source": {
            "publisher": "测试管理机构",
            "page_title": "年度运行数据公告",
            "published_at": "2026-01-04",
            "url": url,
            "source_class": "primary_official",
        },
    }]}


def _temporal() -> dict:
    return {
        "review_as_of_year": 2026,
        "latest_system_observation_year": 2022,
        "freshness_min_year": 2024,
    }


if __name__ == "__main__":
    unittest.main()
