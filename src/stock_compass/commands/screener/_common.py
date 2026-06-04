"""screener 명령군 공유 헬퍼 — screener row → (ticker, market) 변환."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stock_compass.markets.base import Market


def screener_rows_to_targets(
    rows: list[dict[str, Any]],
    *,
    default_market: Market | None,
) -> list[tuple[str, Market]]:
    """screener row → (ticker, market) 튜플. code/ticker + market 컬럼 자동 탐지."""
    out: list[tuple[str, Market]] = []
    for row in rows:
        ticker = row.get("code") or row.get("ticker")
        if not ticker:
            continue
        row_market = row.get("market") or default_market
        if not row_market or row_market not in ("KR", "US"):
            continue
        out.append((str(ticker), row_market))
    return out
