"""
Google Scholar 搜索适配器
"""

import logging
import time
import random
from typing import List, Optional

from .base import SearchPlatform, Paper
from config import SCHOLAR_CONFIG

logger = logging.getLogger(__name__)

# 尝试导入 scholarly
try:
    from scholarly import scholarly
    SCHOLAR_AVAILABLE = True
except ImportError:
    SCHOLAR_AVAILABLE = False
    logger.warning("scholary 库未安装，Google Scholar 搜索不可用")


class GoogleScholarSearcher(SearchPlatform):
    """Google Scholar 搜索器"""

    def __init__(self):
        self.config = SCHOLAR_CONFIG.copy()
        self._random_delay = lambda: time.sleep(random.uniform(
            self.config.get("delay_min", 3),
            self.config.get("delay_max", 10)
        ))

    @property
    def platform_name(self) -> str:
        return "google_scholar"

    def search(
        self,
        query: str,
        limit: int = 50,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None
    ) -> List[Paper]:
        """
        搜索 Google Scholar

        Args:
            query: 搜索关键词
            limit: 最大结果数
            year_from: 起始年份（可选）
            year_to: 结束年份（可选）

        Returns:
            文献列表
        """
        if not SCHOLAR_AVAILABLE:
            logger.warning("scholary 库未安装，跳过 Google Scholar 搜索")
            return []

        results = []
        logger.info(f"[Google Scholar] 搜索: {query}")

        try:
            # 随机延迟
            self._random_delay()

            # 执行搜索
            search_results = scholarly.search_pubs(query)

            for i, result in enumerate(search_results):
                if i >= limit:
                    break

                paper = self._parse_paper(result)
                if paper:
                    # 年份过滤（如果指定）
                    if year_from and (paper.year is None or paper.year < year_from):
                        continue
                    if year_to and (paper.year is not None and paper.year > year_to):
                        continue

                    results.append(paper)
                    logger.debug(f"找到: {paper.title[:50]}...")

                # 随机延时，避免被封
                self._random_delay()

            logger.info(f"[Google Scholar] 找到 {len(results)} 篇文献")

        except Exception as e:
            logger.error(f"[Google Scholar] 搜索出错: {e}")

        return results

    def _parse_paper(self, result: dict) -> Optional[Paper]:
        """解析论文数据"""
        try:
            # 提取基本信息
            title = result.get("bib", {}).get("title", "Unknown")
            authors = result.get("bib", {}).get("author", [])

            # 尝试将年份转换为整数
            year_raw = result.get("bib", {}).get("pub_year", 0)
            try:
                year = int(year_raw) if year_raw else 0
            except (ValueError, TypeError):
                year = 0

            venue = result.get("bib", {}).get("venue", "")
            citation = result.get("num_citations", 0) or 0

            # 提取 DOI
            doi = None
            if "eprint_url" in result:
                url = result.get("eprint_url", "")
            elif "pub_url" in result:
                url = result.get("pub_url", "")
            else:
                url = ""

            # 尝试从 URL 中提取 DOI
            if "doi.org" in url:
                doi = url.split("doi.org/")[-1]
                doi = self._normalize_doi(doi)

            # 提取摘要
            abstract = result.get("bib", {}).get("abstract", "")

            return Paper(
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                citation=citation,
                doi=doi,
                abstract=abstract,
                url=url,
                pdf_url=None,
                source=self.platform_name
            )

        except Exception as e:
            logger.warning(f"[Google Scholar] 解析结果失败: {e}")
            return None