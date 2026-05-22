"""시장 어댑터 진입점."""

from __future__ import annotations

from stock_compass.markets.base import (
    Currency,
    Disclosure,
    Fundamentals,
    Market,
    MarketAdapter,
    News,
    PriceHistory,
)
from stock_compass.markets.kr import KrAdapter
from stock_compass.markets.us import UsAdapter

__all__ = [
    "Currency",
    "Disclosure",
    "Fundamentals",
    "KrAdapter",
    "Market",
    "MarketAdapter",
    "News",
    "PriceHistory",
    "UsAdapter",
    "detect_market",
    "get_adapter",
]


def detect_market(ticker: str) -> Market:
    """티커 패턴으로 KR/US 자동 감지.

    `005930`, `5930`, `005930.KS`, `091990.KQ` → KR
    `AAPL`, `MSFT` 등 알파벳 → US
    """
    head = ticker.split(".", maxsplit=1)[0]
    if head.isdigit():
        return "KR"
    if head.isalpha():
        return "US"
    raise ValueError(f"시장을 감지할 수 없는 티커: {ticker!r}")


def get_adapter(ticker: str, market: Market | None = None) -> MarketAdapter:
    """티커 + (선택)명시 시장 → 어댑터 인스턴스."""
    m = market or detect_market(ticker)
    if m == "KR":
        return KrAdapter()
    if m == "US":
        return UsAdapter()
    raise ValueError(f"지원하지 않는 시장: {m!r}")
