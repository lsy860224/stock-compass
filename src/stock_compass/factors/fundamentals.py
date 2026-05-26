"""Fundamentals 팩터 — 매출 성장, 영업이익 YoY, ROE, 영업이익률, FCF yield.

CLAUDE.md 8) 명시 5개 component. 가중치 25% 대비 component 빈약하지 않도록
영업이익 YoY (yfinance `earningsGrowth`)와 FCF yield (free_cash_flow / market_cap)
점수화 추가. revenue가 Fundamentals에 없어 FCF "마진" 대신 yield 사용
(시가총액 대비 FCF 비율 — 가치 평가에 동등하게 유효).
"""

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


def _score_earnings_growth(g: float | None) -> float | None:
    """yfinance earningsGrowth (≒ 영업이익/순이익 YoY): 0.20 == +20%."""
    if g is None:
        return None
    if g >= 0.30:
        return 95.0
    if g >= 0.15:
        return 80.0
    if g >= 0.05:
        return 65.0
    if g >= 0.0:
        return 50.0
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


def _score_fcf_yield(
    fcf: float | None, market_cap: float | None
) -> float | None:
    """FCF / 시가총액 (FCF yield). market_cap 0 또는 음의 FCF 처리."""
    if fcf is None or market_cap is None or market_cap <= 0:
        return None
    y = fcf / market_cap
    if y >= 0.08:
        return 90.0
    if y >= 0.05:
        return 75.0
    if y >= 0.03:
        return 60.0
    if y >= 0.01:
        return 45.0
    if y >= 0.0:
        return 30.0
    return 15.0


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    fund = adapter.get_fundamentals(ticker)
    components: dict[str, float] = {}
    if (s := _score_revenue_growth(fund.revenue_growth_yoy)) is not None:
        components["revenue_growth_yoy"] = s
    if (s := _score_earnings_growth(fund.earnings_growth_yoy)) is not None:
        components["earnings_growth_yoy"] = s
    if (s := _score_roe(fund.roe)) is not None:
        components["roe"] = s
    if (s := _score_op_margin(fund.operating_margin)) is not None:
        components["operating_margin"] = s
    if (s := _score_fcf_yield(fund.free_cash_flow, fund.market_cap)) is not None:
        components["fcf_yield"] = s

    if not components:
        return neutral(
            "fundamentals",
            "매출성장·영업이익YoY·ROE·영업이익률·FCF 모두 누락",
            raw={
                "revenue_growth_yoy": fund.revenue_growth_yoy,
                "earnings_growth_yoy": fund.earnings_growth_yoy,
                "roe": fund.roe,
                "operating_margin": fund.operating_margin,
                "free_cash_flow": fund.free_cash_flow,
            },
        )

    fcf_yield = (
        fund.free_cash_flow / fund.market_cap
        if fund.free_cash_flow is not None
        and fund.market_cap is not None
        and fund.market_cap > 0
        else None
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
            "free_cash_flow": fund.free_cash_flow,
            "fcf_yield": fcf_yield,
            "market_cap": fund.market_cap,
            "component_scores": components,
        },
        note=f"사용 지표 {len(components)}개: {', '.join(components)}",
        source=fund.source,
    )
