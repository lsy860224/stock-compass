"""외부 API 재시도 데코레이터 (tenacity 래퍼).

사용:
    @external_call_retry
    def fetch_price(symbol: str) -> DataFrame: ...
"""

from __future__ import annotations

from collections.abc import Callable

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from stock_compass.config import settings
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)


def external_call_retry[F: Callable[..., object]](func: F) -> F:
    """3회까지 exponential backoff (1s, 2s, 4s). 네트워크·일시 오류만 재시도."""
    decorated = retry(
        stop=stop_after_attempt(settings.external_api_retry),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )(func)
    return decorated
