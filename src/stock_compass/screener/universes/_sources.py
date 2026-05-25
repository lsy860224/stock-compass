"""외부 universe 소스 fetcher — pykrx (KOSPI/KOSDAQ) + Wikipedia (US 인덱스).

레이어 분리 이유:
- 테스트에서 monkeypatch로 가짜 응답 주입 용이
- KRX API/Wikipedia 변경 시 이 모듈만 수정
- pykrx · pandas 등 무거운 의존성을 lazy import
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from stock_compass.config import settings
from stock_compass.utils.cache import load_json, save_json
from stock_compass.utils.logging import get_logger
from stock_compass.utils.retry import external_call_retry

_logger = get_logger(__name__)

# fallback 캐시 (네트워크/소스 장애 대비)
_CACHE_ROOT = settings.cache_dir / "universes"

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
WIKI_NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
WIKI_DOW30_URL = "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average"

KOSPI_200_INDEX_CODE = "1028"
KOSDAQ_150_INDEX_CODE = "2203"


# ──────────────────────── KR (pykrx) ────────────────────────


@external_call_retry
def fetch_kospi_200_constituents() -> list[tuple[str, str]]:
    """KOSPI 200 멤버 — [(code, name), ...]. pykrx KRX API.

    네트워크 실패 시 캐시 fallback. 응답 비어 있으면 빈 리스트.
    """
    return _fetch_kr_index(KOSPI_200_INDEX_CODE, cache_key="kospi_200")


@external_call_retry
def fetch_kosdaq_150_constituents() -> list[tuple[str, str]]:
    """KOSDAQ 150 멤버."""
    return _fetch_kr_index(KOSDAQ_150_INDEX_CODE, cache_key="kosdaq_150")


def _fetch_kr_index(index_code: str, *, cache_key: str) -> list[tuple[str, str]]:
    cached = _read_fallback(cache_key)
    try:
        from pykrx.stock import get_index_portfolio_deposit_file

        date_str = _kr_business_day()
        codes: list[str] = list(
            get_index_portfolio_deposit_file(date_str, index_code) or []
        )
    except (ConnectionError, TimeoutError, ValueError, IndexError, OSError) as e:
        _logger.warning(
            "KRX 지수 멤버 조회 실패 (%s) — 캐시 fallback: %s", index_code, e
        )
        return _to_pair_list(cached)

    if not codes:
        _logger.warning(
            "KRX 응답 비어 있음 (index=%s) — 캐시 fallback", index_code
        )
        return _to_pair_list(cached)

    pairs = [(code, _kr_name(code) or code) for code in codes]
    _write_fallback(cache_key, list(pairs))
    return pairs


@lru_cache(maxsize=512)
def _kr_name(code: str) -> str | None:
    try:
        from pykrx.stock import get_market_ticker_name

        name = get_market_ticker_name(code)
        return str(name) if name else None
    except (ConnectionError, TimeoutError, ValueError, KeyError, OSError) as e:
        _logger.debug("KRX 종목명 조회 실패 (%s): %s", code, e)
        return None


def _kr_business_day() -> str:
    """가장 가까운 영업일 (YYYYMMDD). 휴장 날짜에 호출되면 직전 영업일."""
    try:
        from pykrx.stock import get_nearest_business_day_in_a_week

        return str(get_nearest_business_day_in_a_week())
    except (ConnectionError, TimeoutError, ValueError, IndexError, OSError):
        # KRX 자체가 깨졌으면 오늘 날짜 추정 — caller가 빈 리스트 처리
        return datetime.utcnow().strftime("%Y%m%d")


# ──────────────────────── US (Wikipedia) ────────────────────────


@external_call_retry
def fetch_sp500_constituents() -> list[tuple[str, str, str]]:
    """S&P 500 — [(symbol, name, sector), ...]. Wikipedia 첫 테이블.

    실패 시 캐시 fallback.
    """
    return _fetch_wiki_us_index(
        WIKI_SP500_URL,
        table_index=0,
        symbol_col="Symbol",
        name_col="Security",
        sector_col="GICS Sector",
        cache_key="sp500",
    )


@external_call_retry
def fetch_nasdaq_100_constituents() -> list[tuple[str, str, str]]:
    """NASDAQ 100 — Wikipedia 'Components' 섹션. 테이블 인덱스 4 (변동 가능)."""
    # Wikipedia 'Nasdaq-100' 페이지의 컴포넌트 테이블은 위치가 자주 바뀜
    # → 컬럼명 기반으로 자동 탐지 (아래 _fetch_wiki_us_index가 처리)
    return _fetch_wiki_us_index(
        WIKI_NASDAQ100_URL,
        table_index=None,  # auto-detect
        symbol_col="Ticker",
        name_col="Company",
        sector_col="GICS Sector",
        cache_key="nasdaq_100",
    )


@external_call_retry
def fetch_dow30_constituents() -> list[tuple[str, str, str]]:
    """Dow Jones Industrial Average 30 종목."""
    return _fetch_wiki_us_index(
        WIKI_DOW30_URL,
        table_index=None,
        symbol_col="Symbol",
        name_col="Company",
        sector_col="Industry",
        cache_key="dow30",
    )


def _fetch_wiki_us_index(
    url: str,
    *,
    table_index: int | None,
    symbol_col: str,
    name_col: str,
    sector_col: str,
    cache_key: str,
) -> list[tuple[str, str, str]]:
    cached = _read_fallback(cache_key)
    try:
        import pandas as pd

        tables = pd.read_html(
            url,
            storage_options={"User-Agent": "stock-compass/0.1 (Personal use)"},
        )
    except (ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        _logger.warning("Wikipedia fetch 실패 (%s) — 캐시 fallback: %s", url, e)
        return _to_triple_list(cached)

    df = _select_wiki_table(tables, table_index, symbol_col=symbol_col)
    if df is None:
        _logger.warning(
            "Wikipedia에서 %s/%s/%s 컬럼 가진 테이블 미발견 — 캐시 fallback",
            symbol_col,
            name_col,
            sector_col,
        )
        return _to_triple_list(cached)

    pairs: list[tuple[str, str, str]] = []
    for _, row in df.iterrows():
        symbol = _clean_symbol(str(row.get(symbol_col, "")))
        name = str(row.get(name_col, "")).strip()
        sector = str(row.get(sector_col, "")).strip() if sector_col in row else ""
        if symbol and name:
            pairs.append((symbol, name, sector))

    if not pairs:
        return _to_triple_list(cached)
    _write_fallback(cache_key, list(pairs))
    return pairs


def _select_wiki_table(
    tables: list[Any], table_index: int | None, *, symbol_col: str
) -> Any | None:
    """`symbol_col`을 포함한 테이블 자동 탐지 (또는 명시 인덱스)."""
    if table_index is not None and 0 <= table_index < len(tables):
        df = tables[table_index]
        if symbol_col in df.columns:
            return df
    for df in tables:
        if hasattr(df, "columns") and symbol_col in df.columns:
            return df
    return None


def _clean_symbol(raw: str) -> str:
    """Wikipedia 심볼에 가끔 붙는 각주(`AAPL[a]`) 또는 BRK.B → BRK-B 같은 정규화."""
    s = raw.strip().split("[", 1)[0].strip()
    return s.replace(".", "-")  # yfinance 형식 (BRK.B → BRK-B)


# ──────────────────────── 캐시 fallback ────────────────────────


def _cache_path(name: str) -> Path:
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return _CACHE_ROOT / f"{name}.json"


def _read_fallback(name: str) -> list[list[str]] | None:
    """7일 TTL 캐시 — 외부 소스 장애 시 마지막 정전 데이터 사용."""
    from datetime import timedelta

    data = load_json(f"universes/{name}", timedelta(days=7))
    if isinstance(data, list):
        return data
    return None


def _write_fallback(name: str, pairs: list[Any]) -> None:
    save_json(f"universes/{name}", [list(p) for p in pairs])


def _to_pair_list(cached: list[list[str]] | None) -> list[tuple[str, str]]:
    if not cached:
        return []
    return [(item[0], item[1]) for item in cached if len(item) >= 2]


def _to_triple_list(cached: list[list[str]] | None) -> list[tuple[str, str, str]]:
    if not cached:
        return []
    return [
        (item[0], item[1], item[2] if len(item) > 2 else "")
        for item in cached
        if len(item) >= 2
    ]


# ──────────────────────── 메타 ────────────────────────


def now_date() -> date_cls:
    """테스트에서 패치 가능한 today wrapper."""
    from stock_compass.utils.dates import today_kst

    return today_kst()
