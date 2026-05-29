"""CLI 공용 유틸 — 시장 파싱, 타겟 결정, 마켓 혼합 batch."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, get_args

import typer

from stock_compass.commands._app import console
from stock_compass.markets.base import Market

if TYPE_CHECKING:
    from rich.progress import Progress

    from stock_compass.scoring import CompositeScore, ScoringEngine

_MARKET_VALUES = set(get_args(Market))


def parse_market(raw: str | None) -> Market | None:
    """--market 옵션 → 'KR'/'US' 또는 None. 잘못된 값은 즉시 종료."""
    if raw is None:
        return None
    m = raw.upper()
    if m not in _MARKET_VALUES:
        console.print(f"[red]지원하지 않는 시장: {raw!r} (kr / us 만 허용)[/red]")
        raise typer.Exit(code=2)
    return m  # type: ignore[return-value]


def resolve_targets(
    tickers_csv: str | None,
    forced_market: Market | None,
    wl_kr: list[str],
    wl_us: list[str],
) -> list[tuple[str, Market]]:
    """(ticker, market) 튜플 리스트 생성. 명시 우선, 그다음 env 워치리스트."""
    from stock_compass.markets import detect_market

    if tickers_csv:
        tokens = [t.strip() for t in tickers_csv.split(",") if t.strip()]
        return [(t, forced_market or detect_market(t)) for t in tokens]

    out: list[tuple[str, Market]] = []
    if forced_market in (None, "KR"):
        out.extend((t, "KR") for t in wl_kr)
    if forced_market in (None, "US"):
        out.extend((t, "US") for t in wl_us)
    return out


def run_mixed(
    engine: ScoringEngine,
    tickers: list[str],
    market_for: dict[str, Market],
    progress: Progress,
    *,
    persist: bool,
) -> list[CompositeScore]:
    """KR/US가 섞인 목록을 시장별 그룹 분리해 analyze_watchlist 호출."""
    groups: dict[Market, list[str]] = defaultdict(list)
    for t in tickers:
        groups[market_for[t]].append(t)

    all_results: list[CompositeScore] = []
    for market, ts in groups.items():
        results = engine.analyze_watchlist(
            ts, market=market, persist=persist, progress=progress
        )
        all_results.extend(results)
    if persist and all_results:
        persist_size_metadata(all_results)
    return all_results


def persist_size_metadata(results: list[CompositeScore]) -> None:
    """batch 결과의 시가총액(fundamentals raw)을 ticker_meta 에 적재 (오늘 시점).

    Size 는 metadata — 점수 영향 없음. market_cap 미가용 종목은 skip.
    US 종목이 있을 때만 USD/KRW FX 1회 조회 (KR-only batch 는 네트워크 호출 없음).
    """
    from stock_compass.db import (
        get_db_connection,
        get_ticker_id,
        upsert_ticker_meta,
    )
    from stock_compass.scoring.size import build_ticker_meta, current_usdkrw
    from stock_compass.utils.dates import today_kst

    rows: list[tuple[CompositeScore, float]] = []
    for s in results:
        fund = s.factor("fundamentals")
        mcap = fund.raw_values.get("market_cap") if fund else None
        if isinstance(mcap, int | float) and not isinstance(mcap, bool) and mcap > 0:
            rows.append((s, float(mcap)))
    if not rows:
        return

    usdkrw = current_usdkrw() if any(s.market == "US" for s, _ in rows) else None
    on_date = today_kst()
    with get_db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for s, mcap in rows:
                tid = get_ticker_id(conn, s.ticker, s.market)
                if tid is None:
                    continue
                meta = build_ticker_meta(
                    market=s.market, market_cap=mcap, usdkrw=usdkrw
                )
                upsert_ticker_meta(
                    conn, ticker_id=tid, as_of=on_date, meta=meta, source="batch"
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
