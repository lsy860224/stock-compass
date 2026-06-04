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


def resolve_default_targets(forced_market: Market | None) -> list[tuple[str, Market]]:
    """배치 기본 대상 = `.env` 워치리스트 + DB 추적 종목(watchlists). 중복 제거.

    .env(WATCHLIST_KR/US) 가 우선 순서, 그다음 DB 추적 종목. forced_market 지정 시
    해당 시장만. 추적 종목 등록 즉시 일일 배치가 자동 채점하도록 하는 진입점.
    """
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, get_tracked_targets

    seen: set[tuple[str, Market]] = set()
    out: list[tuple[str, Market]] = []
    for pair in resolve_targets(
        None, forced_market, settings.watchlist_kr, settings.watchlist_us
    ):
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    with get_db_connection() as conn:
        for code, market in get_tracked_targets(conn):
            if forced_market is not None and market != forced_market:
                continue
            pair = (code, market)
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
    return out


def resolve_universe_targets(universe_csv: str) -> list[tuple[str, Market]]:
    """`--universe` 콤마 구분 코드 → (ticker, market) 목록 (중복 제거, 입력 순서 보존).

    각 universe 의 최신 as_of 멤버를 사용. 같은 종목이 여러 universe 에 속하면
    1회만 포함. 미지원 코드는 즉시 종료.
    """
    from stock_compass.db import get_db_connection
    from stock_compass.screener.universes import (
        SUPPORTED_UNIVERSES,
        list_universe_members,
    )

    codes = [c.strip().upper() for c in universe_csv.split(",") if c.strip()]
    unknown = [c for c in codes if c not in SUPPORTED_UNIVERSES]
    if unknown:
        console.print(
            f"[red]지원하지 않는 universe: {', '.join(unknown)}[/red]\n"
            f"[dim]지원: {', '.join(SUPPORTED_UNIVERSES)}[/dim]"
        )
        raise typer.Exit(code=2)

    seen: set[tuple[str, Market]] = set()
    out: list[tuple[str, Market]] = []
    with get_db_connection() as conn:
        for code in codes:
            for r in list_universe_members(conn, universe_code=code):
                market = str(r["market"])
                if market not in _MARKET_VALUES:
                    continue
                pair: tuple[str, Market] = (str(r["code"]), market)  # type: ignore[assignment]
                if pair not in seen:
                    seen.add(pair)
                    out.append(pair)
    if not out:
        console.print(
            f"[yellow]universe {universe_csv} 멤버 없음 — `universe refresh` 먼저.[/yellow]"
        )
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

    rows: list[tuple[CompositeScore, float, float | None]] = []
    for s in results:
        fund = s.factor("fundamentals")
        mcap = fund.raw_values.get("market_cap") if fund else None
        if isinstance(mcap, int | float) and not isinstance(mcap, bool) and mcap > 0:
            shares = fund.raw_values.get("shares_outstanding") if fund else None
            shares_f = (
                float(shares)
                if isinstance(shares, int | float)
                and not isinstance(shares, bool)
                and shares > 0
                else None
            )
            rows.append((s, float(mcap), shares_f))
    if not rows:
        return

    usdkrw = current_usdkrw() if any(s.market == "US" for s, *_ in rows) else None
    on_date = today_kst()
    with get_db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for s, mcap, shares in rows:
                tid = get_ticker_id(conn, s.ticker, s.market)
                if tid is None:
                    continue
                meta = build_ticker_meta(
                    market=s.market,
                    market_cap=mcap,
                    shares_outstanding=shares,
                    usdkrw=usdkrw,
                )
                upsert_ticker_meta(
                    conn, ticker_id=tid, as_of=on_date, meta=meta, source="batch"
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
