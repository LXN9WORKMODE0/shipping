"""Environment-backed runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

from src.core.paths import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    mineru_api_key: str
    mineru_secret_key: str
    mineru_base_url: str
    mineru_timeout: int
    mineru_model_version: str
    mineru_poll_interval: int
    mineru_max_wait_time: int
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_timeout: int
    log_level: str
    retry_max_retries: int
    retry_base_delay: float
    retry_max_delay: float
    retry_status_codes: tuple[int, ...]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings(
        mineru_api_key=os.getenv("MINERU_API_KEY", ""),
        mineru_secret_key=os.getenv("MINERU_SECRET_KEY", ""),
        mineru_base_url=os.getenv("MINERU_BASE_URL", "https://mineru.net/api/v4"),
        mineru_timeout=int(os.getenv("MINERU_TIMEOUT", "120")),
        mineru_model_version=os.getenv("MINERU_MODEL_VERSION", "vlm"),
        mineru_poll_interval=int(os.getenv("MINERU_POLL_INTERVAL", "2")),
        mineru_max_wait_time=int(os.getenv("MINERU_MAX_WAIT_TIME", "300")),
        llm_api_key=os.getenv("SILICONFLOW_API_KEY", ""),
        llm_base_url=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1/chat/completions"),
        llm_model=os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen3-8B"),
        llm_timeout=int(os.getenv("SILICONFLOW_TIMEOUT", "60")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        retry_max_retries=int(os.getenv("RETRY_MAX_RETRIES", "3")),
        retry_base_delay=float(os.getenv("RETRY_BASE_DELAY", "1")),
        retry_max_delay=float(os.getenv("RETRY_MAX_DELAY", "30")),
        retry_status_codes=tuple(
            int(item.strip()) for item in os.getenv("RETRY_STATUS_CODES", "429,500,502,503,504").split(",") if item.strip()
        ),
    )
