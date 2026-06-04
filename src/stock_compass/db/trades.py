"""trades 테이블 — 본인 매매 일지 + 진입 점수 추적 + 편향 분석.

hindsight(forward return 사후 검증)는 trade_hindsight.py 로 분리.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from stock_compass.db.tickers import get_ticker_id
from stock_compass.markets.base import Market
from stock_compass.utils.dates import now_utc, to_iso_utc

Side = Literal["buy", "sell"]


@dataclass(frozen=True, slots=True)
class Trade:
    """매매 일지 한 행."""

    id: int
    ticker: str
    market: Market
    name: str | None
    side: Side
    price: float
    qty: float
    executed_at: str  # ISO UTC
    score_at_trade: float | None
    reason: str | None
    tag: str | None


def insert_trade(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    market: Market,
    side: Side,
    price: float,
    qty: float,
    reason: str | None = None,
    tag: str | None = None,
    score_at_trade: float | None = None,
    executed_at: datetime | None = None,
) -> Trade:
    """매매 한 건 기록. score_at_trade 미지정 시 최신 composite_score 자동 lookup.

    ticker는 사전 등록 (batch 또는 score 실행) 필요 — 없으면 ValueError.
    """
    ticker_id = get_ticker_id(conn, ticker, market)
    if ticker_id is None:
        raise ValueError(
            f"DB에 미등록 종목: {ticker} [{market}] — 먼저 `score`/`batch` 실행 필요"
        )

    if score_at_trade is None:
        row = conn.execute(
            """
            SELECT total_score FROM composite_scores
            WHERE ticker_id = ?
            ORDER BY date DESC LIMIT 1
            """,
            (ticker_id,),
        ).fetchone()
        score_at_trade = float(row["total_score"]) if row else None

    iso = to_iso_utc(executed_at) if executed_at else to_iso_utc(now_utc())
    cur = conn.execute(
        """
        INSERT INTO trades
          (ticker_id, side, price, qty, executed_at, score_at_trade, reason, tag)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ticker_id, side, price, qty, iso, score_at_trade, reason, tag),
    )
    trade_id = int(cur.lastrowid or 0)
    return _fetch_trade_row(conn, trade_id)


def get_trades(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    ticker: str | None = None,
    market: Market | None = None,
) -> list[Trade]:
    """최근 N일 매매 일지. ticker/market 필터 옵션."""
    where = ["t.id = tr.ticker_id", "tr.executed_at >= datetime('now', ?)"]
    params: list[Any] = [f"-{days} days"]
    if ticker:
        where.append("t.code = ?")
        params.append(ticker)
    if market:
        where.append("t.market = ?")
        params.append(market)
    sql = f"""
        SELECT tr.id, tr.side, tr.price, tr.qty, tr.executed_at,
               tr.score_at_trade, tr.reason, tr.tag,
               t.code, t.market, t.name
        FROM trades tr, tickers t
        WHERE {" AND ".join(where)}
        ORDER BY tr.executed_at DESC
    """
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_trade(r) for r in rows]


def get_performance_summary(
    conn: sqlite3.Connection, *, days: int = 90
) -> dict[str, Any]:
    """본인 매매 편향 분석 — 진입 시점 점수 분포.

    핵심 지표:
    - buy_avg_score / sell_avg_score: 매수·매도 평균 점수
    - buy_high_pct: 70점 이상에서 매수한 비율 (FOMO/추격 매수 경향)
    - buy_low_pct: 30점 이하에서 매수한 비율 (역추세/저점 매수 경향)
    - by_tag: 태그별 평균 점수 (impulse vs planned 비교)
    """
    rows = conn.execute(
        """
        SELECT tr.side, tr.score_at_trade, tr.tag
        FROM trades tr
        WHERE tr.executed_at >= datetime('now', ?)
          AND tr.score_at_trade IS NOT NULL
        """,
        (f"-{days} days",),
    ).fetchall()

    if not rows:
        return {"days": days, "total": 0, "by_side": {}, "by_tag": {}}

    buys = [float(r["score_at_trade"]) for r in rows if r["side"] == "buy"]
    sells = [float(r["score_at_trade"]) for r in rows if r["side"] == "sell"]

    by_tag: dict[str, dict[str, float | int]] = {}
    tag_groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        tag = r["tag"] or "(untagged)"
        tag_groups[tag].append(float(r["score_at_trade"]))
    for tag, scores in tag_groups.items():
        by_tag[tag] = {
            "count": len(scores),
            "avg_score": round(sum(scores) / len(scores), 2),
        }

    def _pct(seq: list[float], pred: Callable[[float], bool]) -> float:
        if not seq:
            return 0.0
        matching = sum(1 for x in seq if pred(x))
        return round(matching / len(seq) * 100, 1)

    return {
        "days": days,
        "total": len(rows),
        "by_side": {
            "buy": {
                "count": len(buys),
                "avg_score": round(sum(buys) / len(buys), 2) if buys else None,
                "high_zone_pct": _pct(buys, lambda x: x >= 70),
                "low_zone_pct": _pct(buys, lambda x: x <= 30),
            },
            "sell": {
                "count": len(sells),
                "avg_score": round(sum(sells) / len(sells), 2) if sells else None,
                "high_zone_pct": _pct(sells, lambda x: x >= 70),
                "low_zone_pct": _pct(sells, lambda x: x <= 30),
            },
        },
        "by_tag": by_tag,
    }


def _fetch_trade_row(conn: sqlite3.Connection, trade_id: int) -> Trade:
    row = conn.execute(
        """
        SELECT tr.id, tr.side, tr.price, tr.qty, tr.executed_at,
               tr.score_at_trade, tr.reason, tr.tag,
               t.code, t.market, t.name
        FROM trades tr
        JOIN tickers t ON tr.ticker_id = t.id
        WHERE tr.id = ?
        """,
        (trade_id,),
    ).fetchone()
    return _row_to_trade(row)


def _row_to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=int(row["id"]),
        ticker=row["code"],
        market=row["market"],
        name=row["name"],
        side=row["side"],
        price=float(row["price"]),
        qty=float(row["qty"]),
        executed_at=row["executed_at"],
        score_at_trade=(
            float(row["score_at_trade"]) if row["score_at_trade"] is not None else None
        ),
        reason=row["reason"],
        tag=row["tag"],
    )
