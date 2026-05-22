"""Sentiment 팩터 — Phase 4까지 placeholder (50점 중립)."""

from __future__ import annotations

from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.markets.base import MarketAdapter


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    _ = (adapter, ticker)
    return FactorScore(
        name="sentiment",
        score=50.0,
        weight=DEFAULT_WEIGHTS["sentiment"],
        raw_values={},
        note="Phase 4까지 placeholder (뉴스·공시 Claude 요약 미구현)",
        source="placeholder",
    )
