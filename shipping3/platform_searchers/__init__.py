"""
多平台学术搜索适配器
"""

from .base import SearchPlatform, Paper
from .semantic_searcher import SemanticScholarSearcher
from .openalex_searcher import OpenAlexSearcher
from .google_scholar_searcher import GoogleScholarSearcher
from .unified_searcher import UnifiedSearcher

__all__ = [
    "SearchPlatform",
    "Paper",
    "SemanticScholarSearcher",
    "OpenAlexSearcher",
    "GoogleScholarSearcher",
    "UnifiedSearcher",
]