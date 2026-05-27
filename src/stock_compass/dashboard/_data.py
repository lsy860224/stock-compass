"""대시보드용 DB 조회 헬퍼 — streamlit-free. CLI/테스트에서도 재사용 가능.

streamlit/altair 의존성 없이 순수 sqlite + dataclass. pandas 변환은 app.py에서.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Any

from stock_compass.config import settings
from stock_compass.db import (
    HistoryRow,
    Trade,
    TradeWithHindsight,
    get_db_connection,
    get_previous_total_scores,
    get_score_history,
    get_scores_on_date,
    get_sector_score_rank,
    get_ticker_id,
    get_trade_hindsight,
    get_trades,
    summarize_hindsight,
)
from stock_compass.markets.base import Market
from stock_compass.scoring.engine import CompositeScore
from stock_compass.utils.dates import today_kst


@dataclass(frozen=True, slots=True)
class OverviewRow:
    code: str
    market: Market
    name: str | None
    sector: str | None
    total_score: float
    verdict: str
    delta: float | None  # 어제 대비 변화
    sector_rank: tuple[int, int] | None  # (rank, total)
    price: float | None
    currency: str | None


def fetch_overview(
    *, on_date: date_cls | None = None
) -> list[OverviewRow]:
    """오늘(또는 지정일) composite_scores + 어제 대비 Δ + sector rank."""
    target = on_date or today_kst()
    with get_db_connection() as conn:
        scores = get_scores_on_date(conn, target)
        if not scores:
            # 오늘 데이터 없으면 가장 최근 날짜로 fallback
            scores = _fallback_latest(conn)
        if not scores:
            return []

        code_markets = [(s.ticker, s.market) for s in scores]
        previous = get_previous_total_scores(
            conn, code_markets, before_date=target
        )

        out: list[OverviewRow] = []
        for s in scores:
            prev = previous.get((s.ticker, s.market))
            delta = (s.total_score - prev[0]) if prev else None
            rank = None
            if s.sector:
                tid = get_ticker_id(conn, s.ticker, s.market)
                if tid is not None:
                    rank = get_sector_score_rank(
                        conn, s.market, s.sector, tid
                    )
            out.append(
                OverviewRow(
                    code=s.ticker,
                    market=s.market,
                    name=s.name,
                    sector=s.sector,
                    total_score=s.total_score,
                    verdict=s.verdict,
                    delta=delta,
                    sector_rank=rank,
                    price=s.price_at_score,
                    currency=s.currency,
                )
            )
        return out


def _fallback_latest(conn: sqlite3.Connection) -> list[CompositeScore]:
    row = conn.execute(
        "SELECT MAX(date) AS d FROM composite_scores"
    ).fetchone()
    if not row or not row["d"]:
        return []
    return get_scores_on_date(conn, date_cls.fromisoformat(row["d"]))


def fetch_history(code: str, market: Market, *, days: int = 90) -> list[HistoryRow]:
    with get_db_connection() as conn:
        return get_score_history(conn, code, market, days=days)


def fetch_sector_averages(
    *, on_date: date_cls | None = None
) -> list[dict[str, Any]]:
    """sector별 평균 점수 + 종목 수 — 차트용."""
    target = on_date or today_kst()
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            WITH latest AS (
              SELECT ticker_id, MAX(date) AS d FROM composite_scores
              WHERE date <= ?
              GROUP BY ticker_id
            )
            SELECT t.sector, t.market,
                   COUNT(*) AS n,
                   AVG(cs.total_score) AS avg_score
            FROM composite_scores cs
            JOIN latest l ON cs.ticker_id = l.ticker_id AND cs.date = l.d
            JOIN tickers t ON t.id = cs.ticker_id
            WHERE t.sector IS NOT NULL
            GROUP BY t.sector, t.market
            ORDER BY avg_score DESC
            """,
            (target.isoformat(),),
        ).fetchall()
        return [dict(r) for r in rows]


def fetch_trades_recent(*, days: int = 90) -> list[Trade]:
    with get_db_connection() as conn:
        return get_trades(conn, days=days)


def fetch_hindsight(
    *,
    days: int = 365,
    forward_days: tuple[int, ...] = (30, 90),
) -> tuple[list[TradeWithHindsight], dict[str, Any]]:
    with get_db_connection() as conn:
        rows = get_trade_hindsight(conn, days=days, forward_days=forward_days)
    summary = summarize_hindsight(rows, forward_days=forward_days)
    return rows, summary


def list_tickers_with_history() -> list[tuple[str, Market, str | None]]:
    """History 페이지 selectbox용 — 점수 이력 있는 종목만."""
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT t.code, t.market, t.name
            FROM tickers t
            JOIN composite_scores cs ON cs.ticker_id = t.id
            ORDER BY t.market, t.code
            """
        ).fetchall()
        return [(r["code"], r["market"], r["name"]) for r in rows]


def list_backtestable_presets() -> list[tuple[str, str]]:
    """backtest 가능 (`v_at_date(:as_of)` 또는 `v_latest_scores` 자동 변환 대상) preset 목록.

    `v_latest_scores` 사용 preset은 `v_at_date(:as_of)` 로 자동 치환 가능 — backtest
    UI 가 이 변환을 시도. 사용자는 인라인 SQL 도 직접 입력 가능.
    """
    from stock_compass.screener import list_presets, load_preset

    out: list[tuple[str, str]] = []
    for p in list_presets():
        try:
            sql = load_preset(p.name)
        except Exception:
            continue
        out.append((p.name, sql))
    return out


def adapt_preset_for_backtest(sql: str) -> str:
    """preset SQL 의 `v_latest_scores` 를 `v_at_date(:as_of)` 로 치환.

    이미 `v_at_date(:as_of)` 를 사용 중이면 그대로 반환. 추가 발견되는 패턴은
    `v_at_date('YYYY-MM-DD')` 같이 hard-coded 일 수 있음 — 그건 그대로 (사용자
    의도가 명확).
    """
    import re

    if ":as_of" in sql:
        return sql
    if re.search(r"v_at_date\(", sql, re.IGNORECASE):
        return sql
    # v_latest_scores → v_at_date(:as_of)
    return re.sub(r"\bv_latest_scores\b", "v_at_date(:as_of)", sql, flags=re.IGNORECASE)


def db_metadata() -> dict[str, Any]:
    """대시보드 헤더용 — DB 경로 + 점수 / 매매 건수 + 최신 날짜."""
    with get_db_connection() as conn:
        score_count = conn.execute(
            "SELECT COUNT(*) AS n FROM composite_scores"
        ).fetchone()["n"]
        trade_count = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()[
            "n"
        ]
        latest_score = conn.execute(
            "SELECT MAX(date) AS d FROM composite_scores"
        ).fetchone()["d"]
    return {
        "db_path": str(settings.db_path),
        "score_count": score_count,
        "trade_count": trade_count,
        "latest_score_date": latest_score,
    }
