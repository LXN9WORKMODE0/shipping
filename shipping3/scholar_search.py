"""
Google Scholar 搜索模块
使用 scholar 库进行文献检索
"""

import time
import random
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass

try:
    from scholarly import scholarly, ProxyGenerator
except ImportError:
    raise ImportError("请安装 scholarly 库: pip install scholarly")

from config import SCHOLAR_CONFIG, PROXY_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class Paper:
    """文献数据结构"""
    title: str
    authors: List[str]
    year: int
    venue: str
    citation: int
    doi: Optional[str]
    abstract: Optional[str]
    url: Optional[str]
    pdf_url: Optional[str] = None


class ScholarSearcher:
    """Google Scholar 搜索器"""

    def __init__(self):
        self.config = SCHOLAR_CONFIG.copy()
        self._setup_proxy()

    def _setup_proxy(self):
        """设置代理"""
        if PROXY_CONFIG.get("enabled") and PROXY_CONFIG.get("http_proxy"):
            try:
                pg = ProxyGenerator()
                pg.SingleProxy(
                    http=PROXY_CONFIG["http_proxy"],
                    https=PROXY_CONFIG["https_proxy"]
                )
                scholarly.use_proxy(pg)
                logger.info("代理已启用")
            except Exception as e:
                logger.warning(f"代理设置失败: {e}")

    def _random_delay(self):
        """随机延时，模拟人类行为"""
        delay = random.uniform(
            self.config["delay_min"],
            self.config["delay_max"]
        )
        logger.debug(f"等待 {delay:.1f} 秒...")
        time.sleep(delay)

    def search(
        self,
        query: str,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        max_results: Optional[int] = None,
        include_citations: bool = False
    ) -> List[Paper]:
        """
        搜索文献

        Args:
            query: 搜索关键词
            year_from: 起始年份
            year_to: 结束年份
            max_results: 最大结果数
            include_citations: 是否包含引用数

        Returns:
            文献列表
        """
        max_results = max_results or self.config["max_results"]
        results = []

        logger.info(f"开始搜索: {query}")

        try:
            # 执行搜索
            search_results = scholarly.search_pubs(query)

            for i, result in enumerate(search_results):
                if i >= max_results:
                    break

                # 提取文献信息
                paper = self._parse_result(result)
                if paper:
                    # 过滤年份
                    if year_from and paper.year < year_from:
                        continue
                    if year_to and paper.year > year_to:
                        continue

                    results.append(paper)
                    logger.info(f"找到: {paper.title[:50]}...")

                # 随机延时，避免被封
                self._random_delay()

        except Exception as e:
            logger.error(f"搜索出错: {e}")
            raise

        logger.info(f"搜索完成，找到 {len(results)} 篇文献")
        return results

    def search_by_author(
        self,
        author: str,
        max_results: int = 20
    ) -> List[Paper]:
        """
        按作者搜索

        Args:
            author: 作者姓名
            max_results: 最大结果数

        Returns:
            文献列表
        """
        logger.info(f"搜索作者: {author}")

        try:
            search_results = scholarly.search_author(author)
            author_info = next(search_results)
            filled_author = scholarly.fill(author_info)

            results = []
            for i, pub in enumerate(filled_author.get("publications", [])):
                if i >= max_results:
                    break
                paper = self._parse_result(pub)
                if paper:
                    results.append(paper)

        except Exception as e:
            logger.error(f"作者搜索出错: {e}")
            raise

        return results

    def _parse_result(self, result: Dict) -> Optional[Paper]:
        """解析搜索结果"""
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
                pdf_url=None
            )

        except Exception as e:
            logger.warning(f"解析结果失败: {e}")
            return None

    def get_citations(self, paper: Paper) -> int:
        """获取文献引用数"""
        try:
            self._random_delay()
            # 这里可以添加获取引用详情的逻辑
            return paper.citation
        except Exception as e:
            logger.warning(f"获取引用数失败: {e}")
            return 0