"""Retry helpers for HTTP clients."""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable, TypeVar

from src.core.settings import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_retry(retry_on_exceptions: tuple[type[BaseException], ...]) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry the wrapped function on the given exception types."""
    settings = get_settings()

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception: BaseException | None = None
            for attempt in range(settings.retry_max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on_exceptions as exc:  # pragma: no cover - depends on network failures
                    last_exception = exc
                    if attempt == settings.retry_max_retries:
                        break
                    delay = min(settings.retry_base_delay * (2**attempt), settings.retry_max_delay)
                    logger.warning("请求失败，%.1f 秒后重试: %s", delay, exc)
                    time.sleep(delay)

            if last_exception is None:
                raise RuntimeError("重试失败但没有捕获到异常")
            raise last_exception

        return wrapper

    return decorator


def with_status_retry(func: Callable[..., T]) -> Callable[..., T]:
    """Retry when the wrapped request result has a retryable HTTP status."""
    settings = get_settings()

    @wraps(func)
    def wrapper(*args, **kwargs) -> T:
        last_response = None
        for attempt in range(settings.retry_max_retries + 1):
            response = func(*args, **kwargs)
            last_response = response
            status_code = getattr(response, "status_code", None)
            if status_code == 200:
                return response
            if status_code not in settings.retry_status_codes or attempt == settings.retry_max_retries:
                return response
            delay = min(settings.retry_base_delay * (2**attempt), settings.retry_max_delay)
            logger.warning("HTTP %s，%.1f 秒后重试", status_code, delay)
            time.sleep(delay)
        return last_response

    return wrapper
