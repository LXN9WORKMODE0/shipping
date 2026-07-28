"""
统一搜索引擎 - 整合多个平台的搜索结果
"""

import logging
from typing import List, Optional
from collections import defaultdict

from .base import SearchPlatform, Paper
from .semantic_searcher import SemanticScholarSearcher
from .openalex_searcher import OpenAlexSearcher
from .google_scholar_searcher import GoogleScholarSearcher
from config import UNIFIED_SEARCH_CONFIG

logger = logging.getLogger(__name__)


class UnifiedSearcher:
    """统一搜索引擎 - 整合多个学术搜索平台"""

    def __init__(self):
        self.config = UNIFIED_SEARCH_CONFIG
        self.platforms: List[SearchPlatform] = []
        self._init_platforms()

    def _init_platforms(self):
        """初始化启用的搜索平台"""
        enabled = self.config.get("enabled_platforms", [])

        platform_map = {
            "semantic_scholar": SemanticScholarSearcher,
            "openalex": OpenAlexSearcher,
            "google_scholar": GoogleScholarSearcher,
        }

        for name in enabled:
            if name in platform_map:
                try:
                    self.platforms.append(platform_map[name]())
                    logger.info(f"已加载搜索平台: {name}")
                except Exception as e:
                    logger.warning(f"加载平台 {name} 失败: {e}")

    @property
    def available_platforms(self) -> List[str]:
        """获取可用平台列表"""
        return [p.platform_name for p in self.platforms]

    def search(
        self,
        query: str,
        limit: int = 50,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        platforms: Optional[List[str]] = None
    ) -> List[Paper]:
        """
        多平台统一搜索

        Args:
            query: 搜索关键词
            limit: 最大结果数（总结果）
            year_from: 起始年份（可选）
            year_to: 结束年份（可选）
            platforms: 指定平台列表（可选，默认全部）

        Returns:
            去重后的文献列表
        """
        all_papers = []
        platforms_to_use = self.platforms

        # 筛选指定平台
        if platforms:
            platforms_to_use = [p for p in self.platforms if p.platform_name in platforms]

        if not platforms_to_use:
            logger.warning("没有可用的搜索平台")
            return []

        logger.info(f"统一搜索: {query}, 平台: {[p.platform_name for p in platforms_to_use]}")

        # 每个平台搜索
        per_platform_limit = self.config.get("max_results_per_platform", 50)

        for platform in platforms_to_use:
            try:
                papers = platform.search(
                    query=query,
                    limit=per_platform_limit,
                    year_from=year_from,
                    year_to=year_to
                )
                all_papers.extend(papers)
                logger.info(f"[{platform.platform_name}] 返回 {len(papers)} 篇")
            except Exception as e:
                logger.error(f"[{platform.platform_name}] 搜索异常: {e}")

        # 合并去重
        if self.config.get("deduplication_enabled", True):
            results = self._deduplicate(all_papers)
        else:
            results = all_papers

        # 排序（按引用量）
        results.sort(key=lambda p: p.citation, reverse=True)

        # 限制总数
        total_limit = self.config.get("total_limit", 300)
        return results[:min(len(results), total_limit)]

    def _deduplicate(self, papers: List[Paper]) -> List[Paper]:
        """结果去重"""
        # 来源优先级（数字越大优先级越高）
        priority = {
            "semantic_scholar": 3,
            "openalex": 2,
            "google_scholar": 1,
        }

        # 用于去重的数据结构
        doi_map = {}      # DOI -> Paper
        title_map = {}    # 标准化标题 -> (Paper, priority)

        for paper in papers:
            # 1. 首先用 DOI 去重
            if paper.doi:
                key = paper.doi.lower().strip()
                if key not in doi_map:
                    doi_map[key] = paper
                continue

            # 2. 用标题去重
            norm_title = self._normalize_title(paper.title)
            if norm_title:
                if norm_title not in title_map:
                    title_map[norm_title] = (paper, priority.get(paper.source, 0))
                else:
                    # 保留优先级更高的
                    existing, existing_priority = title_map[norm_title]
                    current_priority = priority.get(paper.source, 0)
                    if current_priority > existing_priority:
                        title_map[norm_title] = (paper, current_priority)

        # 合并结果
        results = list(doi_map.values())
        for paper, _ in title_map.values():
            if paper not in results:
                results.append(paper)

        logger.info(f"去重: {len(papers)} -> {len(results)}")
        return results

    def _normalize_title(self, title: str) -> str:
        """标准化标题"""
        if not title:
            return ""
        import re
        title = title.lower().strip()
        title = re.sub(r'[^\w\s]', '', title)
        title = re.sub(r'\s+', ' ', title)
        return title