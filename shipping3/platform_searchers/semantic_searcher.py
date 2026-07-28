"""
Semantic Scholar 搜索适配器
API 文档: https://api.semanticscholar.org/api-docs/
"""

import logging
import time
import random
import requests
from typing import List, Optional

from .base import SearchPlatform, Paper
from config import SEMANTIC_SCHOLAR_CONFIG

logger = logging.getLogger(__name__)


class SemanticScholarSearcher(SearchPlatform):
    """Semantic Scholar 搜索器"""

    def __init__(self):
        self.config = SEMANTIC_SCHOLAR_CONFIG
        self.session = requests.Session()
        self.session.headers.update(self.config.get("headers", {}))
        self.base_url = self.config["api_url"]
        self.request_delay = 2.0  # 请求间隔（秒），避免触发速率限制
        self.max_retries = 3     # 最大重试次数
        self.retry_delay = 10    # 遇到 429 时的等待时间（秒）

    @property
    def platform_name(self) -> str:
        return "semantic_scholar"

    def search(
        self,
        query: str,
        limit: int = 50,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None
    ) -> List[Paper]:
        """
        搜索 Semantic Scholar

        Args:
            query: 搜索关键词
            limit: 最大结果数
            year_from: 起始年份（可选）
            year_to: 结束年份（可选）

        Returns:
            文献列表
        """
        results = []
        max_limit = min(limit, self.config["max_results_per_query"])

        logger.info(f"[Semantic Scholar] 搜索: {query}")

        for attempt in range(self.max_retries):
            try:
                params = {
                    "query": query,
                    "limit": max_limit,
                    "offset": 0,
                    "fields": "title,authors,year,abstract,doi,url,citationCount,openAccessPdf,venue"
                }

                # 添加年份过滤
                if year_from or year_to:
                    year_filter = []
                    if year_from:
                        year_filter.append(f"year:>={year_from}")
                    if year_to:
                        year_filter.append(f"year:<={year_to}")
                    params["query"] = f"{query} {' '.join(year_filter)}"

                response = self.session.get(
                    self.base_url,
                    params=params,
                    timeout=self.config.get("timeout", 30)
                )

                # 处理速率限制 (429)
                if response.status_code == 429:
                    wait_time = self.retry_delay + random.uniform(0, 5)
                    logger.warning(f"[Semantic Scholar] 速率限制触发，等待 {wait_time:.1f} 秒后重试...")
                    time.sleep(wait_time)
                    continue

                response.raise_for_status()

                data = response.json()
                papers_data = data.get("data", [])

                for item in papers_data:
                    paper = self._parse_paper(item)
                    if paper:
                        results.append(paper)

                logger.info(f"[Semantic Scholar] 找到 {len(results)} 篇文献")
                break  # 成功获取数据，退出重试循环

            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.request_delay * (attempt + 1)
                    logger.warning(f"[Semantic Scholar] 请求错误: {e}，{wait_time}秒后重试...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"[Semantic Scholar] 请求错误: {e}")
            except Exception as e:
                logger.error(f"[Semantic Scholar] 搜索失败: {e}")
                break

        # 请求间隔，避免影响后续请求
        time.sleep(self.request_delay + random.uniform(0, 1))

        return results

    def _parse_paper(self, item: dict) -> Optional[Paper]:
        """解析论文数据"""
        try:
            # 解析作者
            authors = []
            for author in item.get("authors", []):
                if isinstance(author, dict):
                    name = author.get("name", "")
                    if name:
                        authors.append(name)
                elif isinstance(author, str):
                    authors.append(author)

            # 解析 DOI
            doi = item.get("doi", "")
            if doi:
                doi = self._normalize_doi(doi)

            # 解析年份
            year = item.get("year")
            if year is None:
                # 尝试从其他字段获取
                year = item.get("publicationDate")
                if year:
                    try:
                        year = int(year[:4])
                    except (ValueError, TypeError):
                        year = None

            # 解析 Open Access PDF
            pdf_url = ""
            oa_pdf = item.get("openAccessPdf")
            if oa_pdf and isinstance(oa_pdf, dict):
                pdf_url = oa_pdf.get("url", "")

            return Paper(
                title=item.get("title", "Unknown"),
                authors=authors,
                year=year,
                venue=item.get("venue", ""),
                citation=item.get("citationCount", 0) or 0,
                doi=doi,
                abstract=item.get("abstract", ""),
                url=item.get("url", ""),
                pdf_url=pdf_url,
                source=self.platform_name
            )

        except Exception as e:
            logger.warning(f"[Semantic Scholar] 解析论文失败: {e}")
            return None