"""
文献检索下载工具配置文件
"""

import os
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.absolute()

# 下载目录
PAPERS_DIR = PROJECT_ROOT / "papers"
PAPERS_DIR.mkdir(exist_ok=True)

# Google Scholar 设置
SCHOLAR_CONFIG = {
    "max_results": 20,           # 默认最大结果数
    "delay_min": 3,              # 最小延迟（秒）
    "delay_max": 10,             # 最大延迟（秒）
    "max_retries": 3,            # 最大重试次数
    "timeout": 30,               # 请求超时（秒）
}

# 下载设置
DOWNLOAD_CONFIG = {
    "chunk_size": 8192,          # 下载块大小
    "max_retries": 3,            # 下载重试次数
    "retry_delay": 5,            # 重试延迟（秒）
    "timeout": 60,               # 下载超时（秒）
}

# 代理设置（可选）
PROXY_CONFIG = {
    "enabled": False,
    "http_proxy": "",
    "https_proxy": "",
}

# DOI 下载配置
DOI_CONFIG = {
    "use_scihub": True,          # 是否使用 Sci-Hub 备用
    "scihub_urls": [
        "https://sci-hub.se",
        "https://sci-hub.st",
        "https://sci-hub.pt",
    ]
}

# Semantic Scholar 配置
SEMANTIC_SCHOLAR_CONFIG = {
    "api_url": "https://api.semanticscholar.org/graph/v1/paper/search",
    "timeout": 30,
    "max_results_per_query": 100,
    "rate_limit_per_minute": 100,
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
}

# OpenAlex 配置
OPENALEX_CONFIG = {
    "api_url": "https://api.openalex.org/works",
    "timeout": 30,
    "max_results_per_query": 200,
    "rate_limit_per_minute": 3000,
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
}

# Crossref 配置
CROSSREF_CONFIG = {
    "api_url": "https://api.crossref.org/works",
    "timeout": 30,
    "max_results_per_query": 100,
    "rate_limit_per_second": 50,
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
}

# 统一搜索配置
# 顺序：优先使用更稳定的平台（OpenAlex），Semantic Scholar 放在后面
UNIFIED_SEARCH_CONFIG = {
    # 启用多个平台，按优先级排序（OpenAlex 最稳定，优先搜索）
    "enabled_platforms": ["openalex", "google_scholar", "semantic_scholar"],
    "max_results_per_platform": 50,
    "total_limit": 300,
    "deduplication_enabled": True,
}

# 日志配置
LOG_CONFIG = {
    "level": "INFO",
    "format": "%(asctime)s - %(levelname)s - %(message)s",
    "file": PROJECT_ROOT / "download.log",
}