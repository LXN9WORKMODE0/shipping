"""
搜索平台抽象基类
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Paper:
    """统一的文献数据结构"""
    title: str = ""
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: str = ""
    citation: int = 0
    doi: Optional[str] = None
    abstract: Optional[str] = None
    url: Optional[str] = None
    pdf_url: Optional[str] = None
    source: str = ""  # 来源平台: semantic_scholar, openalex, google_scholar


class SearchPlatform(ABC):
    """搜索平台抽象基类"""

    @abstractmethod
    def search(
        self,
        query: str,
        limit: int = 50,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None
    ) -> List[Paper]:
        """
        执行搜索

        Args:
            query: 搜索关键词
            limit: 最大结果数
            year_from: 起始年份（可选）
            year_to: 结束年份（可选）

        Returns:
            文献列表
        """
        pass

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台名称"""
        pass

    def _normalize_doi(self, doi: str) -> str:
        """标准化 DOI"""
        if not doi:
            return ""
        doi = doi.strip()
        # 移除常见前缀
        for prefix in ["https://doi.org/", "http://doi.org/", "doi:", "DOI:"]:
            if doi.lower().startswith(prefix.lower()):
                doi = doi[len(prefix):]
        return doi.strip()

    def _normalize_title(self, title: str) -> str:
        """标准化标题用于去重"""
        if not title:
            return ""
        # 转小写，移除特殊字符
        import re
        title = title.lower().strip()
        title = re.sub(r'[^\w\s]', '', title)
        title = re.sub(r'\s+', ' ', title)
        return title