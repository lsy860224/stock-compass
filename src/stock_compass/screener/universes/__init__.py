"""유니버스 자동 갱신 — Phase 7-2 본격 구현.

지원 universe:
- WATCHLIST: .env의 워치리스트 (즉시 가동)
- KOSPI_200, KOSDAQ_150: pykrx KRX API
- SP500, NASDAQ_100, DOW30: Wikipedia 스크래핑

외부 소스 fetcher는 `_sources.py`로 격리 — 변경/장애 격리.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date as date_cls

from stock_compass.config import settings
from stock_compass.db import upsert_ticker
from stock_compass.markets.base import Market
from stock_compass.screener.universes import _sources
from stock_compass.utils.dates import today_kst
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

# 지원 universe 코드
UNIVERSE_WATCHLIST = "WATCHLIST"
UNIVERSE_KOSPI_200 = "KOSPI_200"
UNIVERSE_KOSDAQ_150 = "KOSDAQ_150"
UNIVERSE_ALL_KR = "ALL_KR"
UNIVERSE_SP500 = "SP500"
UNIVERSE_NASDAQ_100 = "NASDAQ_100"
UNIVERSE_DOW30 = "DOW30"

SUPPORTED_UNIVERSES = (
    UNIVERSE_WATCHLIST,
    UNIVERSE_KOSPI_200,
    UNIVERSE_KOSDAQ_150,
    UNIVERSE_ALL_KR,
    UNIVERSE_SP500,
    UNIVERSE_NASDAQ_100,
    UNIVERSE_DOW30,
)


@dataclass(frozen=True, slots=True)
class UniverseRefreshResult:
    universe_code: str
    as_of_date: str
    members: int


class UniverseFetchError(RuntimeError):
    """소스에서 0개 멤버 fetch — DB 작성 거부 (기존 정전 데이터 보존)."""


# ──────────────────────── 공개 API ────────────────────────


def refresh(
    conn: sqlite3.Connection,
    universe_code: str,
    *,
    on_date: date_cls | None = None,
) -> UniverseRefreshResult:
    """universe_code별 dispatcher. 호출자가 분기 안 해도 되도록."""
    code = universe_code.upper()
    if code == UNIVERSE_WATCHLIST:
        return refresh_watchlist(conn, on_date=on_date)
    if code == UNIVERSE_KOSPI_200:
        return refresh_kospi_200(conn, on_date=on_date)
    if code == UNIVERSE_KOSDAQ_150:
        return refresh_kosdaq_150(conn, on_date=on_date)
    if code == UNIVERSE_ALL_KR:
        return refresh_all_kr(conn, on_date=on_date)
    if code == UNIVERSE_SP500:
        return refresh_sp500(conn, on_date=on_date)
    if code == UNIVERSE_NASDAQ_100:
        return refresh_nasdaq_100(conn, on_date=on_date)
    if code == UNIVERSE_DOW30:
        return refresh_dow30(conn, on_date=on_date)
    raise ValueError(
        f"지원하지 않는 universe: {universe_code!r} (지원: {', '.join(SUPPORTED_UNIVERSES)})"
    )


def refresh_watchlist(
    conn: sqlite3.Connection,
    *,
    on_date: date_cls | None = None,
) -> UniverseRefreshResult:
    """`.env` WATCHLIST_KR/US를 WATCHLIST universe 멤버로 등록.

    종목 메타가 DB에 없으면 placeholder로 upsert — 본격 메타데이터는
    이후 score/batch 실행 시 채워짐.
    """
    pairs: list[tuple[str, Market, str | None, str | None]] = []
    for code in settings.watchlist_kr:
        pairs.append((code, "KR", None, None))
    for code in settings.watchlist_us:
        pairs.append((code, "US", None, None))
    return _bulk_register(conn, UNIVERSE_WATCHLIST, pairs, on_date=on_date)


def refresh_kospi_200(
    conn: sqlite3.Connection, *, on_date: date_cls | None = None
) -> UniverseRefreshResult:
    """pykrx로 KOSPI 200 멤버 갱신. 휴장일·KRX 장애 시 캐시 fallback."""
    return _refresh_kr(
        conn,
        UNIVERSE_KOSPI_200,
        _sources.fetch_kospi_200_constituents,
        on_date=on_date,
    )


def refresh_kosdaq_150(
    conn: sqlite3.Connection, *, on_date: date_cls | None = None
) -> UniverseRefreshResult:
    """pykrx로 KOSDAQ 150 멤버 갱신."""
    return _refresh_kr(
        conn,
        UNIVERSE_KOSDAQ_150,
        _sources.fetch_kosdaq_150_constituents,
        on_date=on_date,
    )


def refresh_all_kr(
    conn: sqlite3.Connection, *, on_date: date_cls | None = None
) -> UniverseRefreshResult:
    """KOSPI 200 + KOSDAQ 150 합집합 — 한국 시장 광역 발굴용.

    KOSPI/KOSDAQ 시장 구분은 _guess_symbol/markets.kr이 ticker 코드로 처리.
    """
    return _refresh_kr(
        conn,
        UNIVERSE_ALL_KR,
        _sources.fetch_all_kr_constituents,
        on_date=on_date,
    )


def add_to_watchlist_group(
    conn: sqlite3.Connection,
    group: str,
    pairs: Iterable[tuple[str, Market, str | None, str | None]],
    *,
    on_date: date_cls | None = None,
) -> UniverseRefreshResult:
    """발굴 종목을 `WATCHLIST_<GROUP>` universe로 등록 — `screen --add-to-watchlist` 진입점.

    .env의 WATCHLIST_KR/US와 분리된 사용자 그룹. batch 자동 추적은 .env 갱신
    필요(이 함수는 후속 안내를 호출자가 출력하도록 universe 등록만 수행).
    """
    if not group or not group.replace("_", "").isalnum():
        raise ValueError(
            f"group은 영숫자+언더바만 허용 (받음: {group!r})"
        )
    universe_code = f"WATCHLIST_{group.upper()}"
    return _bulk_register(conn, universe_code, list(pairs), on_date=on_date)


def refresh_sp500(
    conn: sqlite3.Connection, *, on_date: date_cls | None = None
) -> UniverseRefreshResult:
    """Wikipedia 스크래핑으로 S&P 500 갱신. 7일 fallback 캐시."""
    return _refresh_us(
        conn,
        UNIVERSE_SP500,
        _sources.fetch_sp500_constituents,
        on_date=on_date,
    )


def refresh_nasdaq_100(
    conn: sqlite3.Connection, *, on_date: date_cls | None = None
) -> UniverseRefreshResult:
    return _refresh_us(
        conn,
        UNIVERSE_NASDAQ_100,
        _sources.fetch_nasdaq_100_constituents,
        on_date=on_date,
    )


def refresh_dow30(
    conn: sqlite3.Connection, *, on_date: date_cls | None = None
) -> UniverseRefreshResult:
    return _refresh_us(
        conn,
        UNIVERSE_DOW30,
        _sources.fetch_dow30_constituents,
        on_date=on_date,
    )


def list_universe_members(
    conn: sqlite3.Connection,
    *,
    universe_code: str | None = None,
) -> list[dict[str, object]]:
    """universe별 카운트 또는 특정 universe의 멤버 목록."""
    if universe_code:
        rows = conn.execute(
            """
            SELECT um.universe_code, t.code, t.name, t.market,
                   MAX(um.as_of_date) AS as_of_date
            FROM universe_members um
            JOIN tickers t ON t.id = um.ticker_id
            WHERE um.universe_code = ?
            GROUP BY um.universe_code, t.code, t.name, t.market
            ORDER BY t.market, t.code
            """,
            (universe_code,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT um.universe_code,
                   COUNT(DISTINCT um.ticker_id) AS member_count,
                   MAX(um.as_of_date) AS latest_date
            FROM universe_members um
            GROUP BY um.universe_code
            ORDER BY um.universe_code
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────── 내부 ────────────────────────


def _refresh_kr(
    conn: sqlite3.Connection,
    universe_code: str,
    fetcher: Callable[[], list[tuple[str, str, str]]],
    *,
    on_date: date_cls | None,
) -> UniverseRefreshResult:
    raw = fetcher()
    if not raw:
        raise UniverseFetchError(
            f"{universe_code}: 멤버 0개 — KRX 자격증명/FDR/캐시 모두 무 (기존 데이터 보존)"
        )
    pairs: list[tuple[str, Market, str | None, str | None]] = [
        (code, "KR", name, None) for code, name, _ in raw
    ]
    kr_market_sub = {code: market_sub for code, _, market_sub in raw}
    return _bulk_register(
        conn, universe_code, pairs, on_date=on_date, kr_market_sub=kr_market_sub
    )


def _refresh_us(
    conn: sqlite3.Connection,
    universe_code: str,
    fetcher: Callable[[], list[tuple[str, str, str]]],
    *,
    on_date: date_cls | None,
) -> UniverseRefreshResult:
    raw = fetcher()
    if not raw:
        raise UniverseFetchError(
            f"{universe_code}: 멤버 0개 — Wikipedia 장애 또는 캐시 무 (기존 데이터 보존)"
        )
    pairs: list[tuple[str, Market, str | None, str | None]] = [
        (symbol, "US", name, sector or None) for symbol, name, sector in raw
    ]
    return _bulk_register(conn, universe_code, pairs, on_date=on_date)


def _bulk_register(
    conn: sqlite3.Connection,
    universe_code: str,
    pairs: Iterable[tuple[str, Market, str | None, str | None]],
    *,
    on_date: date_cls | None,
    kr_market_sub: dict[str, str] | None = None,
) -> UniverseRefreshResult:
    """공통 등록 — ticker upsert + universe_members INSERT OR IGNORE.

    같은 (universe_code, ticker_id, as_of_date) 충돌은 멤버 카운트에서 제외.
    `kr_market_sub`: KR 종목 code → "KOSPI"/"KOSDAQ" (yfinance .KS/.KQ 정확화).
    """
    d = (on_date or today_kst()).isoformat()
    subs = kr_market_sub or {}
    inserted = 0
    for code, market, name, sector in pairs:
        ticker_id = upsert_ticker(
            conn,
            code=code,
            market=market,
            name=name,
            sector=sector,
            currency="KRW" if market == "KR" else "USD",
            yfinance_symbol=_guess_symbol(code, market, subs.get(code)),
        )
        cur = conn.execute(
            """
            INSERT INTO universe_members (universe_code, ticker_id, as_of_date)
            VALUES (?, ?, ?)
            ON CONFLICT(universe_code, ticker_id, as_of_date) DO NOTHING
            """,
            (universe_code, ticker_id, d),
        )
        if cur.rowcount:
            inserted += 1

    _logger.info(
        "universe '%s' 갱신: %d 신규 (as_of=%s)", universe_code, inserted, d
    )
    return UniverseRefreshResult(
        universe_code=universe_code, as_of_date=d, members=inserted
    )


def _guess_symbol(code: str, market: Market, market_sub: str | None = None) -> str:
    if market == "US":
        return code.upper()
    if market_sub == "KOSDAQ":
        return f"{code}.KQ"
    return f"{code}.KS"  # KOSPI 또는 미상 (미상은 KrAdapter가 런타임 .KQ 재시도)
