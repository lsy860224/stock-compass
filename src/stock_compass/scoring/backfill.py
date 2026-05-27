"""Phase 7-5 보조 — 과거 데이터로 composite_scores 백필.

목표: backtest 가 의미 있게 동작하도록 과거 N 년의 시점별 점수를 시드.

설계:
- **Technical + Macro**: 시점별 실제 데이터 (yfinance OHLCV + FRED 시계열) 로 재계산
- **Valuation / Fundamentals / Sentiment**: 시점별 재구성 불가 → `backfill_skip` (50 neutral)
- 결과: 종합 점수는 Technical+Macro 신호 위주, 나머지는 baseline 50

source 표시:
- factor_scores 의 FactorScore.source = `backfill` (technical/macro) 또는
  `backfill_skip` (valuation/fundamentals/sentiment) — 사용자가 실시간 batch
  결과와 구분 가능. composite_scores.sentiment_source 도 `backfill_skip`.

한계 (CLAUDE.md 1) 출력 면책에 명시):
- Valuation/Fundamentals/Sentiment 시점별 정보 없음 — 백필 점수는 부분 신호
- composite_scores 에 이미 같은 (ticker, date) 가 있으면 덮어씀
- yfinance 가 그 종목의 그 기간 OHLCV 를 줘야 작동 (상장폐지 종목은 부분만)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path
from typing import Any

from stock_compass.factors import macro as macro_factor
from stock_compass.factors import technical as technical_factor
from stock_compass.factors.base import FactorScore, backfill_skip
from stock_compass.markets import detect_market, get_adapter
from stock_compass.markets.base import Market
from stock_compass.utils.dates import now_utc, today_kst
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

# MA200 + 안전 마진 — 이 일수 누적 후부터 Technical 산출 가능
_MIN_HISTORY_DAYS = 200


@dataclass(frozen=True, slots=True)
class BackfillResult:
    ticker: str
    market: Market
    start: date_cls
    end: date_cls
    days_processed: int
    days_skipped_insufficient_history: int
    days_skipped_out_of_range: int


def run_backfill(
    ticker: str,
    market: Market | None = None,
    *,
    start: date_cls,
    end: date_cls | None = None,
    db_path: Path | None = None,
) -> BackfillResult:
    """ticker 1종목 백필 — [start, end] 거래일별 점수 + DB upsert.

    price_at_score = 그 시점 종가. backtest 가 forward return lookup 시 자연스럽게
    호환. Technical+Macro 만 시점별 정확, 나머지는 baseline 50.
    """
    market = market or detect_market(ticker)
    end = end or today_kst()
    if start > end:
        raise ValueError(f"start({start}) > end({end})")

    adapter = get_adapter(ticker, market)

    # 1) OHLCV — 최대 5년 (yfinance 한계). start 가 더 옛날이면 데이터 부족
    hist = adapter.get_price_history(ticker, period="5y")
    if hist.is_empty():
        raise ValueError(f"가격 데이터 없음: {ticker} [{market}]")

    # 2) 종목 메타 (현재 시점 — name/sector 만 사용)
    name: str | None = None
    sector: str | None = None
    yf_symbol: str | None = None
    try:
        fund = adapter.get_fundamentals(ticker)
        name = fund.name
        sector = fund.sector
    except Exception as e:
        _logger.warning("backfill 메타데이터 조회 실패 (계속): %s — %s", ticker, e)
    try:
        yf_symbol = adapter.to_yfinance_symbol(ticker)
    except Exception:
        yf_symbol = ticker

    # 3) FRED 시계열 전체 — 모든 시점 재사용
    series_cache: dict[str, Any] = {
        "VIXCLS": macro_factor.fetch_full_series("VIXCLS"),
        "T10Y2Y": macro_factor.fetch_full_series("T10Y2Y"),
        "DGS10": macro_factor.fetch_full_series("DGS10"),
    }
    if market == "KR":
        series_cache["DEXKOUS"] = macro_factor.fetch_full_series("DEXKOUS")

    close = hist.df["close"]
    volume = hist.df["volume"]
    currency = "KRW" if market == "KR" else "USD"

    processed = 0
    skipped_history = 0
    skipped_oor = 0

    from stock_compass.db import get_db_connection, upsert_composite_score

    path = db_path or None
    conn_ctx = (
        sqlite3.connect(str(path), isolation_level=None)
        if path is not None
        else get_db_connection()
    )
    with conn_ctx as conn:
        if path is not None:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for ts in close.index:
                as_of = ts.date() if hasattr(ts, "date") else ts
                if not isinstance(as_of, date_cls):
                    continue
                if as_of < start or as_of > end:
                    skipped_oor += 1
                    continue
                close_slice = close.loc[:ts]
                volume_slice = volume.loc[:ts]
                if len(close_slice) < _MIN_HISTORY_DAYS:
                    skipped_history += 1
                    continue

                composite = _build_composite_at(
                    ticker=ticker,
                    market=market,
                    name=name,
                    sector=sector,
                    yf_symbol=yf_symbol,
                    currency=currency,
                    close_slice=close_slice,
                    volume_slice=volume_slice,
                    series_cache=series_cache,
                    as_of=as_of,
                )
                upsert_composite_score(conn, composite, on_date=as_of)
                processed += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return BackfillResult(
        ticker=ticker,
        market=market,
        start=start,
        end=end,
        days_processed=processed,
        days_skipped_insufficient_history=skipped_history,
        days_skipped_out_of_range=skipped_oor,
    )


def _build_composite_at(
    *,
    ticker: str,
    market: Market,
    name: str | None,
    sector: str | None,
    yf_symbol: str | None,
    currency: str,
    close_slice: Any,
    volume_slice: Any,
    series_cache: dict[str, Any],
    as_of: date_cls,
) -> Any:
    """시점별 CompositeScore 생성. ScoringEngine 의 _weighted_average 와 동일 로직."""
    from stock_compass.scoring.engine import (
        DISCLAIMER,
        CompositeScore,
        _verdict,
    )

    v = backfill_skip("valuation")
    f = backfill_skip("fundamentals")
    t = technical_factor.calculate_at_close(
        close_slice, volume_slice, as_of=as_of, source="backfill"
    )
    m = macro_factor.calculate_at_date(market, series_cache, as_of)
    s = backfill_skip("sentiment")

    factors: list[FactorScore] = [v, f, t, m, s]
    total_w = sum(fs.weight for fs in factors)
    total = (
        sum(fs.score * fs.weight for fs in factors) / total_w
        if total_w > 0
        else 50.0
    )
    return CompositeScore(
        ticker=ticker,
        market=market,
        total_score=round(total, 2),
        verdict=_verdict(total),
        factors=factors,
        computed_at=now_utc(),
        price_at_score=float(close_slice.iloc[-1]),
        currency=currency,
        name=name,
        sector=sector,
        yfinance_symbol=yf_symbol,
        disclaimer=DISCLAIMER,
    )
