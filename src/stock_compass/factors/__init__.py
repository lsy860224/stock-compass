"""5대 팩터 — 동일한 calculate(adapter, ticker) 시그니처."""

from __future__ import annotations

from collections.abc import Callable

from stock_compass.factors import (
    fundamentals,
    macro,
    quality,
    sentiment,
    technical,
    valuation,
)
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorName, FactorScore, neutral
from stock_compass.markets.base import MarketAdapter

FactorCalc = Callable[[MarketAdapter, str], FactorScore]

REGISTRY: dict[FactorName, FactorCalc] = {
    "valuation": valuation.calculate,
    "fundamentals": fundamentals.calculate,
    "quality": quality.calculate,
    "technical": technical.calculate,
    "macro": macro.calculate,
    "sentiment": sentiment.calculate,
}

__all__ = [
    "DEFAULT_WEIGHTS",
    "REGISTRY",
    "FactorCalc",
    "FactorName",
    "FactorScore",
    "neutral",
]
