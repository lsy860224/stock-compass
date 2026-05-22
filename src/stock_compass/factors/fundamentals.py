"""Fundamentals 팩터 — 매출 성장, ROE, 영업이익률."""

from __future__ import annotations

from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore, neutral
from stock_compass.markets.base import MarketAdapter


def _score_revenue_growth(g: float | None) -> float | None:
    """yfinance revenueGrowth: 0.15 == +15% YoY."""
    if g is None:
        return None
    if g >= 0.25:
        return 90.0
    if g >= 0.15:
        return 75.0
    if g >= 0.05:
        return 60.0
    if g >= 0.0:
        return 45.0
    if g >= -0.10:
        return 30.0
    return 15.0


def _score_roe(roe: float | None) -> float | None:
    """yfinance returnOnEquity: 0.20 == 20%."""
    if roe is None:
        return None
    if roe >= 0.25:
        return 95.0
    if roe >= 0.15:
        return 80.0
    if roe >= 0.10:
        return 65.0
    if roe >= 0.05:
        return 50.0
    if roe >= 0.0:
        return 30.0
    return 15.0


def _score_op_margin(m: float | None) -> float | None:
    if m is None:
        return None
    if m >= 0.20:
        return 85.0
    if m >= 0.10:
        return 70.0
    if m >= 0.05:
        return 55.0
    if m >= 0.0:
        return 40.0
    return 20.0


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    fund = adapter.get_fundamentals(ticker)
    components: dict[str, float] = {}
    if (s := _score_revenue_growth(fund.revenue_growth_yoy)) is not None:
        components["revenue_growth_yoy"] = s
    if (s := _score_roe(fund.roe)) is not None:
        components["roe"] = s
    if (s := _score_op_margin(fund.operating_margin)) is not None:
        components["operating_margin"] = s

    if not components:
        return neutral(
            "fundamentals",
            "매출성장·ROE·영업이익률 모두 누락",
            raw={
                "revenue_growth_yoy": fund.revenue_growth_yoy,
                "roe": fund.roe,
                "operating_margin": fund.operating_margin,
            },
        )

    score = sum(components.values()) / len(components)
    return FactorScore(
        name="fundamentals",
        score=round(score, 2),
        weight=DEFAULT_WEIGHTS["fundamentals"],
        raw_values={
            "revenue_growth_yoy": fund.revenue_growth_yoy,
            "earnings_growth_yoy": fund.earnings_growth_yoy,
            "roe": fund.roe,
            "operating_margin": fund.operating_margin,
            "profit_margin": fund.profit_margin,
            "component_scores": components,
        },
        note=f"사용 지표 {len(components)}개: {', '.join(components)}",
        source=fund.source,
    )
