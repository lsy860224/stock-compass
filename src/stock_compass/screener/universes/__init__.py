"""유니버스 자동 갱신 — Phase 7-1 최소판.

WATCHLIST (.env 기반)만 즉시 가동. KOSPI 200·KOSDAQ 150·S&P 500·NASDAQ 100은
스크래핑/외부 API 의존성이 있어 사용자 환경에서 별도 활성화 (TODO 표기).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls

from stock_compass.config import settings
from stock_compass.db import upsert_ticker
from stock_compass.markets import detect_market
from stock_compass.utils.dates import today_kst
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

# 지원 유니버스 코드 (CHECK constraint 없음 — 자유 확장 가능)
UNIVERSE_WATCHLIST = "WATCHLIST"
UNIVERSE_KOSPI_200 = "KOSPI_200"
UNIVERSE_KOSDAQ_150 = "KOSDAQ_150"
UNIVERSE_ALL_KR = "ALL_KR"
UNIVERSE_SP500 = "SP500"
UNIVERSE_NASDAQ_100 = "NASDAQ_100"
UNIVERSE_DOW30 = "DOW30"


@dataclass(frozen=True, slots=True)
class UniverseRefreshResult:
    universe_code: str
    as_of_date: str
    members: int


def refresh_watchlist(
    conn: sqlite3.Connection,
    *,
    on_date: date_cls | None = None,
) -> UniverseRefreshResult:
    """`.env` WATCHLIST_KR/US를 `WATCHLIST` universe 멤버로 등록.

    종목 메타가 DB에 없으면 placeholder로 upsert — 본격 메타데이터는
    이후 score/batch 실행 시 채워짐.
    """
    d = (on_date or today_kst()).isoformat()
    pairs: list[tuple[str, str]] = []
    pairs.extend((code, "KR") for code in settings.watchlist_kr)
    pairs.extend((code, "US") for code in settings.watchlist_us)

    inserted = 0
    for code, market in pairs:
        ticker_id = upsert_ticker(
            conn,
            code=code,
            market=market,  # type: ignore[arg-type]
            name=None,  # COALESCE로 기존 이름 보존
            sector=None,
            currency="KRW" if market == "KR" else "USD",
            yfinance_symbol=_guess_symbol(code, market),
        )
        conn.execute(
            """
            INSERT INTO universe_members (universe_code, ticker_id, as_of_date)
            VALUES (?, ?, ?)
            ON CONFLICT(universe_code, ticker_id, as_of_date) DO NOTHING
            """,
            (UNIVERSE_WATCHLIST, ticker_id, d),
        )
        inserted += 1

    _logger.info(
        "universe '%s' 갱신: %d 종목 (as_of=%s)", UNIVERSE_WATCHLIST, inserted, d
    )
    return UniverseRefreshResult(
        universe_code=UNIVERSE_WATCHLIST, as_of_date=d, members=inserted
    )


def list_universe_members(
    conn: sqlite3.Connection,
    *,
    universe_code: str | None = None,
) -> list[dict[str, object]]:
    """v_universe 조회 (최신 as_of_date 기준)."""
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


# ──────────────────────── 외부 소스 (TODO — 사용자 환경 가변) ────────────────────────


def refresh_kospi_200(conn: sqlite3.Connection) -> UniverseRefreshResult:
    """pykrx로 KOSPI 200 멤버 갱신. Phase 7-2 후속.

    Note: 현재는 stub. 실제 구현 시 `pykrx.stock.get_index_portfolio_deposit_file('1028')`
    호출하되, KRX API 가용성/스로틀 고려 필요.
    """
    raise NotImplementedError(
        "KOSPI 200 자동 갱신은 Phase 7-2 후속 작업 — 현재 WATCHLIST만 가동"
    )


def refresh_sp500(conn: sqlite3.Connection) -> UniverseRefreshResult:
    """Wikipedia 스크래핑으로 S&P 500 멤버 갱신. Phase 7-2 후속."""
    raise NotImplementedError(
        "S&P 500 자동 갱신은 Phase 7-2 후속 작업 — 현재 WATCHLIST만 가동"
    )


# ──────────────────────── 헬퍼 ────────────────────────


def _guess_symbol(code: str, market: str) -> str:
    if market == "US":
        return code.upper()
    # KR: .KS 추정 (KOSDAQ은 KrAdapter가 score 시점에 .KQ로 정정)
    _ = detect_market  # 향후 detect_market 활용 여지
    return f"{code}.KS"
