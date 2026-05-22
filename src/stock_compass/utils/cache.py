"""디스크 캐시 — DataFrame은 parquet, 작은 dict는 JSON.

TTL 기반 무효화. 단일 사용자 가정으로 락은 없음.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from stock_compass.config import settings
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)
_CACHE_ROOT = settings.cache_dir


def _path(name: str, suffix: str) -> Path:
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    safe = name.replace("/", "_").replace(":", "_")
    return _CACHE_ROOT / f"{safe}.{suffix}"


def _is_fresh(path: Path, ttl: timedelta) -> bool:
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return datetime.now(UTC) - mtime <= ttl


def load_dataframe(name: str, ttl: timedelta) -> pd.DataFrame | None:
    """parquet 캐시 로드. 만료·없음·캐시 비활성 시 None."""
    if not settings.enable_cache:
        return None
    p = _path(name, "parquet")
    if not _is_fresh(p, ttl):
        return None
    try:
        return pd.read_parquet(p)
    except (OSError, ValueError) as e:
        _logger.warning("parquet 캐시 손상, 무시: %s (%s)", p, e)
        return None


def save_dataframe(name: str, df: pd.DataFrame) -> None:
    if not settings.enable_cache or df.empty:
        return
    p = _path(name, "parquet")
    try:
        df.to_parquet(p, compression="snappy")
    except (OSError, ValueError) as e:
        _logger.warning("parquet 캐시 저장 실패: %s (%s)", p, e)


def load_json(name: str, ttl: timedelta) -> Any | None:
    if not settings.enable_cache:
        return None
    p = _path(name, "json")
    if not _is_fresh(p, ttl):
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _logger.warning("JSON 캐시 손상, 무시: %s (%s)", p, e)
        return None


def save_json(name: str, data: Any) -> None:
    if not settings.enable_cache:
        return
    p = _path(name, "json")
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    except OSError as e:
        _logger.warning("JSON 캐시 저장 실패: %s (%s)", p, e)


def cache_key_for_today(symbol: str) -> str:
    """`AAPL` → `AAPL-20260522` (UTC 기준 날짜)."""
    return f"{symbol}-{time.strftime('%Y%m%d', time.gmtime())}"
