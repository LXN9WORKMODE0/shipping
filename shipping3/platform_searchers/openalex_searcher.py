"""
OpenAlex 搜索适配器
API 文档: https://api.openalex.org/api-docs/
"""

import logging
import requests
from typing import List, Optional

from .base import SearchPlatform, Paper
from config import OPENALEX_CONFIG

logger = logging.getLogger(__name__)


class OpenAlexSearcher(SearchPlatform):
    """OpenAlex 搜索器"""

    def __init__(self):
        self.config = OPENALEX_CONFIG
        self.session = requests.Session()
        self.session.headers.update(self.config.get("headers", {}))
        self.base_url = self.config["api_url"]

    @property
    def platform_name(self) -> str:
        return "openalex"

    def search(
        self,
        query: str,
        limit: int = 50,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None
    ) -> List[Paper]:
        """
        搜索 OpenAlex

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

        logger.info(f"[OpenAlex] 搜索: {query}")

        try:
            params = {
                "search": query,
                "per-page": max_limit,
                "sort": "relevance_score:desc"
            }

            # 添加年份过滤（使用正确的filter语法）
            filter_parts = []
            if year_from:
                filter_parts.append(f"from_publication_date:{year_from}")
            if year_to:
                filter_parts.append(f"to_publication_date:{year_to}")

            if filter_parts:
                params["filter"] = ",".join(filter_parts)

            response = self.session.get(
                self.base_url,
                params=params,
                timeout=self.config.get("timeout", 30)
            )
            response.raise_for_status()

            data = response.json()
            papers_data = data.get("results", [])

            for item in papers_data:
                paper = self._parse_paper(item)
                if paper:
                    results.append(paper)

            logger.info(f"[OpenAlex] 找到 {len(results)} 篇文献")

        except requests.exceptions.RequestException as e:
            logger.error(f"[OpenAlex] 请求错误: {e}")
        except Exception as e:
            logger.error(f"[OpenAlex] 搜索失败: {e}")

        return results

    def _parse_paper(self, item: dict) -> Optional[Paper]:
        """解析论文数据"""
        try:
            # 解析作者
            authors = []
            authorships = item.get("authorships", [])
            for auth in authorships:
                if isinstance(auth, dict):
                    author = auth.get("author", {})
                    if isinstance(author, dict):
                        name = author.get("display_name", "")
                        if name:
                            authors.append(name)
                elif isinstance(auth, str):
                    authors.append(auth)

            # 解析 DOI
            doi = item.get("doi", "")
            if doi:
                doi = self._normalize_doi(doi)

            # 解析年份
            year = item.get("publication_year")
            if year is None:
                year = item.get("publicationDate")
                if year:
                    try:
                        year = int(year[:4])
                    except (ValueError, TypeError):
                        year = None

            # 解析期刊/会议
            venue = ""
            host_venue = item.get("host_venue")
            if host_venue and isinstance(host_venue, dict):
                venue = host_venue.get("display_name", "")

            # 解析引用数
            citation = 0
            counts = item.get("counts_by_year", [])
            if counts:
                # 汇总所有年份的引用数
                citation = sum(c.get("cited_by_count", 0) for c in counts)

            # 解析 Open Access PDF
            pdf_url = ""
            oa = item.get("open_access")
            if oa and isinstance(oa, dict):
                pdf_url = oa.get("pdf_url", "")

            # 解析 URL
            url = ""
            primary_location = item.get("primary_location", {})
            if primary_location:
                source = primary_location.get("source", {})
                if source:
                    url = source.get("id", "")

            return Paper(
                title=item.get("title", "Unknown"),
                authors=authors,
                year=year,
                venue=venue,
                citation=citation,
                doi=doi,
                abstract=item.get("abstract", ""),
                url=url,
                pdf_url=pdf_url,
                source=self.platform_name
            )

        except Exception as e:
            logger.warning(f"[OpenAlex] 解析论文失败: {e}")
            return None