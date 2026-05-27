"""Phase 7-5 백테스트 엔진 — preset SQL x 리밸런싱 x forward return.

preset SQL이 `v_at_date(:as_of)`를 참조한다고 가정(plain `:as_of` placeholder를
매 라운드의 ISO date로 치환). ScreenerEngine으로 매 라운드 실행 →
각 종목의 N일 후 가격을 `composite_scores.price_at_score`에서 lookup하여
forward return 산출. 집계: 평균·중앙값·적중률(양수 비율)·최저 수익률.

한계 (사용자에게 항상 명시):
- survivorship: tickers.delisted_at IS NULL 만 조회 — 상장폐지 종목 시드 X
- 거래비용·세금·슬리피지·배당 미반영
- price_at_score 결측 라운드는 skip
- 과거 성과가 미래를 보장하지 않음
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path
from typing import Any

from stock_compass.markets.base import Market
from stock_compass.screener.engine import ScreenerEngine, ScreenerError
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

DEFAULT_FORWARD_PERIODS: tuple[str, ...] = ("1m", "3m")
_PERIOD_DAYS: dict[str, int] = {"1m": 30, "3m": 90, "6m": 180, "12m": 365}

DISCLAIMER = (
    "백테스트 결과는 과거 데이터 기반 통계입니다. **매수 권유 아님.** "
    "거래비용·세금·슬리피지·배당 미반영. survivorship bias: 상장폐지 종목 제외 "
    "→ 실제 시점에서는 선택 가능했던 종목이 누락될 수 있음. "
    "과거 성과가 미래를 보장하지 않습니다."
)


class BacktestError(RuntimeError):
    """백테스트 입력 또는 실행 오류."""


@dataclass(frozen=True, slots=True)
class BacktestRound:
    """한 리밸런싱 시점의 결과."""

    as_of: date_cls
    selected: list[dict[str, Any]]
    # ticker -> {period: forward_return | None}
    forward_returns: dict[str, dict[str, float | None]]


@dataclass(frozen=True, slots=True)
class BacktestStats:
    rounds_count: int
    total_picks: int
    avg_return: dict[str, float | None] = field(default_factory=dict)
    median_return: dict[str, float | None] = field(default_factory=dict)
    hit_rate: dict[str, float | None] = field(default_factory=dict)
    worst_return: dict[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    preset_sql: str
    preset_name: str | None
    start: date_cls
    end: date_cls
    rebalance: str
    forward_periods: tuple[str, ...]
    rounds: list[BacktestRound]
    stats: BacktestStats
    disclaimer: str = DISCLAIMER


# ──────────────────────── 공개 API ────────────────────────


def run_backtest(
    preset_sql: str,
    *,
    start: date_cls,
    end: date_cls,
    rebalance: str = "monthly",
    forward_periods: Sequence[str] = DEFAULT_FORWARD_PERIODS,
    limit: int = 15,
    preset_name: str | None = None,
    db_path: Path | None = None,
) -> BacktestResult:
    """`preset_sql` 안의 `:as_of` placeholder가 매 라운드 ISO date로 치환.

    `:as_of`가 없으면 `v_at_date(:as_of)` 사용을 안내. SQL은 단순 SELECT 권장
    (outer WITH 충돌은 v_at_date CTE 치환이 거부).
    """
    if ":as_of" not in preset_sql:
        raise BacktestError(
            "preset SQL이 ':as_of' placeholder를 포함해야 합니다 "
            "(예: SELECT ... FROM v_at_date(:as_of) WHERE ...)"
        )
    if start >= end:
        raise BacktestError(f"start({start}) >= end({end})")
    periods = tuple(forward_periods)
    for p in periods:
        if p not in _PERIOD_DAYS:
            raise BacktestError(
                f"미지원 forward period: {p} (지원: {list(_PERIOD_DAYS)})"
            )

    rebalance_dates = _build_rebalance_dates(start, end, rebalance)
    if not rebalance_dates:
        raise BacktestError(
            f"리밸런싱 날짜 0개 — {start}~{end} 사이 {rebalance} 주기 없음"
        )

    engine = ScreenerEngine(db_path=db_path)
    rounds: list[BacktestRound] = []
    for as_of in rebalance_dates:
        sql = preset_sql.replace(":as_of", f"'{as_of.isoformat()}'")
        try:
            r = engine.run_sql(
                sql,
                limit=limit,
                preset_name=f"backtest:{preset_name or 'inline'}:{as_of}",
            )
        except ScreenerError as e:
            _logger.warning("backtest round %s 실패 — 스킵: %s", as_of, e)
            continue
        if not r.rows:
            rounds.append(
                BacktestRound(as_of=as_of, selected=[], forward_returns={})
            )
            continue

        forward = _compute_round_forward_returns(
            r.rows, as_of=as_of, periods=periods, db_path=db_path
        )
        rounds.append(
            BacktestRound(as_of=as_of, selected=r.rows, forward_returns=forward)
        )

    stats = _aggregate_stats(rounds, periods)
    return BacktestResult(
        preset_sql=preset_sql,
        preset_name=preset_name,
        start=start,
        end=end,
        rebalance=rebalance,
        forward_periods=periods,
        rounds=rounds,
        stats=stats,
    )


# ──────────────────────── 리밸런싱 날짜 ────────────────────────


def _build_rebalance_dates(
    start: date_cls, end: date_cls, rebalance: str
) -> list[date_cls]:
    """monthly: 매월 1일, weekly: 매주 월요일, quarterly: 매 분기 첫날."""
    if rebalance == "monthly":
        return _monthly_first(start, end)
    if rebalance == "quarterly":
        return _quarterly_first(start, end)
    if rebalance == "weekly":
        return _weekly_mondays(start, end)
    raise BacktestError(
        f"미지원 rebalance: {rebalance} (monthly/quarterly/weekly)"
    )


def _monthly_first(start: date_cls, end: date_cls) -> list[date_cls]:
    out: list[date_cls] = []
    cur = date_cls(start.year, start.month, 1)
    if cur < start:
        # 다음 달 1일로
        if cur.month == 12:
            cur = date_cls(cur.year + 1, 1, 1)
        else:
            cur = date_cls(cur.year, cur.month + 1, 1)
    while cur <= end:
        out.append(cur)
        if cur.month == 12:
            cur = date_cls(cur.year + 1, 1, 1)
        else:
            cur = date_cls(cur.year, cur.month + 1, 1)
    return out


def _quarterly_first(start: date_cls, end: date_cls) -> list[date_cls]:
    """1/1, 4/1, 7/1, 10/1 중 [start, end] 안의 날짜."""
    out: list[date_cls] = []
    for year in range(start.year, end.year + 1):
        for month in (1, 4, 7, 10):
            d = date_cls(year, month, 1)
            if start <= d <= end:
                out.append(d)
    return out


def _weekly_mondays(start: date_cls, end: date_cls) -> list[date_cls]:
    out: list[date_cls] = []
    # 첫 월요일로 이동
    delta = (0 - start.weekday()) % 7  # 0=Monday
    cur = start + timedelta(days=delta)
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=7)
    return out


# ──────────────────────── forward return ────────────────────────


def _compute_round_forward_returns(
    rows: list[dict[str, Any]],
    *,
    as_of: date_cls,
    periods: tuple[str, ...],
    db_path: Path | None,
) -> dict[str, dict[str, float | None]]:
    from stock_compass.config import settings
    from stock_compass.db.tickers import get_ticker_id

    path = db_path or settings.db_path
    out: dict[str, dict[str, float | None]] = {}
    with sqlite3.connect(f"file:{path.absolute()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for row in rows:
            code = row.get("code") or row.get("ticker")
            market: Market | None = row.get("market") or None
            base_price = row.get("price") or row.get("price_at_score")
            if not code or market not in ("KR", "US") or not base_price:
                continue
            ticker_id = get_ticker_id(conn, str(code), market)
            if ticker_id is None:
                continue
            base = float(base_price)
            out[str(code)] = {
                p: _forward_return(conn, ticker_id, base, as_of, _PERIOD_DAYS[p])
                for p in periods
            }
    return out


def _forward_return(
    conn: sqlite3.Connection,
    ticker_id: int,
    base_price: float,
    base_date: date_cls,
    days: int,
) -> float | None:
    """as_of + N일 이후 가장 가까운 composite_scores.price_at_score로 수익률 계산.

    price_at_score 결측 행은 건너뜀. 미래 데이터 부족이면 None.
    """
    if base_price <= 0:
        return None
    target = base_date + timedelta(days=days)
    row = conn.execute(
        """
        SELECT price_at_score FROM composite_scores
        WHERE ticker_id = ? AND date >= ? AND price_at_score IS NOT NULL
        ORDER BY date ASC
        LIMIT 1
        """,
        (ticker_id, target.isoformat()),
    ).fetchone()
    if row is None or row["price_at_score"] is None:
        return None
    fwd = float(row["price_at_score"])
    return fwd / base_price - 1.0


# ──────────────────────── 집계 ────────────────────────


def _aggregate_stats(
    rounds: list[BacktestRound], periods: tuple[str, ...]
) -> BacktestStats:
    total_picks = sum(len(r.selected) for r in rounds)
    avg: dict[str, float | None] = {}
    median: dict[str, float | None] = {}
    hit: dict[str, float | None] = {}
    worst: dict[str, float | None] = {}
    for p in periods:
        values: list[float] = []
        for r in rounds:
            for ticker_returns in r.forward_returns.values():
                v = ticker_returns.get(p)
                if v is not None:
                    values.append(v)
        if not values:
            avg[p] = median[p] = hit[p] = worst[p] = None
            continue
        sorted_v = sorted(values)
        n = len(sorted_v)
        avg[p] = sum(sorted_v) / n
        median[p] = (
            sorted_v[n // 2]
            if n % 2
            else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
        )
        hit[p] = sum(1 for v in sorted_v if v > 0) / n
        worst[p] = sorted_v[0]
    return BacktestStats(
        rounds_count=len(rounds),
        total_picks=total_picks,
        avg_return=avg,
        median_return=median,
        hit_rate=hit,
        worst_return=worst,
    )
