"""매매 hindsight — 진입 시점 결정의 사후 검증 (N일 후 forward return + 집계).

trades.py(CRUD·편향 분석)에 단방향 의존. composite_scores.price_at_score 를 기준
forward 가격으로 사용.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from stock_compass.db.tickers import get_ticker_id
from stock_compass.db.trades import Trade, get_trades
from stock_compass.markets.base import Market

DEFAULT_HINDSIGHT_DAYS: tuple[int, ...] = (30, 90)


@dataclass(frozen=True, slots=True)
class TradeWithHindsight:
    """매매 한 건 + N일 후 가격 변화율 — 진입 시점 결정의 사후 검증."""

    trade: Trade
    forward_returns: dict[str, float | None] = field(default_factory=dict)
    forward_prices: dict[str, float | None] = field(default_factory=dict)


def get_trade_hindsight(
    conn: sqlite3.Connection,
    *,
    days: int = 365,
    forward_days: tuple[int, ...] = DEFAULT_HINDSIGHT_DAYS,
    ticker: str | None = None,
    market: Market | None = None,
) -> list[TradeWithHindsight]:
    """매매 일지 + 각 거래의 N일 후 forward 가격 (composite_scores.price_at_score 기반).

    매수: forward_return = (forward_price / entry_price) - 1. +면 잘 산 것.
    매도: forward_return = (forward_price / exit_price) - 1.
        +면 너무 일찍 팔음 (missed gain), -면 잘 팔음.
    forward 데이터 부족이면 None.
    """
    trades = get_trades(conn, days=days, ticker=ticker, market=market)
    out: list[TradeWithHindsight] = []
    for t in trades:
        tid = get_ticker_id(conn, t.ticker, t.market)
        if tid is None:
            out.append(TradeWithHindsight(trade=t))
            continue
        base = datetime.fromisoformat(t.executed_at).date()
        returns: dict[str, float | None] = {}
        prices: dict[str, float | None] = {}
        for d in forward_days:
            fwd_price = _forward_price_at(conn, tid, base + timedelta(days=d))
            prices[f"{d}d"] = fwd_price
            if fwd_price is None or t.price <= 0:
                returns[f"{d}d"] = None
            else:
                returns[f"{d}d"] = fwd_price / t.price - 1.0
        out.append(
            TradeWithHindsight(
                trade=t, forward_returns=returns, forward_prices=prices
            )
        )
    return out


def _forward_price_at(
    conn: sqlite3.Connection, ticker_id: int, target_date: object
) -> float | None:
    """`target_date` 이후 가장 가까운 `composite_scores.price_at_score`."""
    row = conn.execute(
        """
        SELECT price_at_score FROM composite_scores
        WHERE ticker_id = ?
          AND date >= ?
          AND price_at_score IS NOT NULL
        ORDER BY date ASC
        LIMIT 1
        """,
        (ticker_id, str(target_date)),
    ).fetchone()
    return float(row["price_at_score"]) if row else None


def summarize_hindsight(
    rows: list[TradeWithHindsight], *, forward_days: tuple[int, ...]
) -> dict[str, Any]:
    """hindsight 결과 집계.

    매수와 매도는 방향이 다르므로 분리 통계.
    - buy: forward_return > 0 → 잘 산 것 (hit)
    - sell: forward_return < 0 → 잘 판 것 (가격이 내려감 = missed loss 회피, hit)
    `score_zone`: 진입 점수 70+ vs 50- vs 중간 분포.
    """
    by_side: dict[str, dict[str, Any]] = {"buy": {}, "sell": {}}
    by_zone: dict[str, dict[str, Any]] = {
        "high_70_plus": {},
        "mid_50_70": {},
        "low_below_50": {},
        "no_score": {},
    }
    keys = [f"{d}d" for d in forward_days]

    def _stats(values: list[float], *, hit_positive: bool) -> dict[str, float | int | None]:
        if not values:
            return {
                "count": 0,
                "avg": None,
                "median": None,
                "hit_rate": None,
                "best": None,
                "worst": None,
            }
        sorted_v = sorted(values)
        n = len(sorted_v)
        avg = sum(sorted_v) / n
        median = (
            sorted_v[n // 2]
            if n % 2
            else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
        )
        if hit_positive:
            hits = sum(1 for v in sorted_v if v > 0)
        else:
            hits = sum(1 for v in sorted_v if v < 0)
        return {
            "count": n,
            "avg": avg,
            "median": median,
            "hit_rate": hits / n,
            "best": sorted_v[-1],
            "worst": sorted_v[0],
        }

    for side in ("buy", "sell"):
        by_side[side] = {
            k: _stats(
                [
                    v
                    for r in rows
                    if r.trade.side == side
                    and (v := r.forward_returns.get(k)) is not None
                ],
                hit_positive=(side == "buy"),
            )
            for k in keys
        }

    def _zone(score: float | None) -> str:
        if score is None:
            return "no_score"
        if score >= 70:
            return "high_70_plus"
        if score >= 50:
            return "mid_50_70"
        return "low_below_50"

    for zone in ("high_70_plus", "mid_50_70", "low_below_50", "no_score"):
        by_zone[zone] = {
            k: _stats(
                [
                    v
                    for r in rows
                    if _zone(r.trade.score_at_trade) == zone
                    and r.trade.side == "buy"  # zone 비교는 매수만 의미
                    and (v := r.forward_returns.get(k)) is not None
                ],
                hit_positive=True,
            )
            for k in keys
        }

    return {
        "total": len(rows),
        "by_side": by_side,
        "by_zone": by_zone,
        "forward_keys": keys,
    }
