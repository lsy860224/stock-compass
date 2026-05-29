"""Fundamentals 팩터 — 매출 성장, 영업이익 YoY, ROE, 영업이익률, FCF yield.

CLAUDE.md 8) 명시 5개 component. valuation 패턴과 동일하게 sector median
대비 ratio 점수화 도입 — cold-start(< 3 종목)이거나 음수값이면 절대 임계치
fallback. 같은 (market, sector) median은 process-level dict 캐시.

각 component는 *높을수록 좋은* 방향 → ratio는 valuation 의 반대 (큰 ratio
가 높은 점수). negative value는 항상 absolute fallback (sector ratio 무의미).

`calculate_at_date(qf, ...)`: 백필용 시점별 ROE/op_margin/revenue_growth 재구성
(TTM 4분기 합산).
"""

from __future__ import annotations

from datetime import date as date_cls

from stock_compass.factors._fundamentals_history import metrics_at
from stock_compass.factors.base import (
    DEFAULT_WEIGHTS,
    FactorScore,
    backfill_skip,
    neutral,
)
from stock_compass.markets.base import Market, MarketAdapter, QuarterlyFinancials


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


# ──────────────────────── 상대 점수 (sector median) ────────────────────────


def _score_ratio_higher_better(
    value: float | None, median: float | None
) -> float | None:
    """value/median 비율 — 클수록 높은 점수 (ROE/margin/growth 등). 둘 다 양수 필요."""
    if value is None or value <= 0 or median is None or median <= 0:
        return None
    ratio = value / median
    if ratio > 1.6:
        return 90.0
    if ratio > 1.2:
        return 78.0
    if ratio > 1.0:
        return 65.0
    if ratio > 0.7:
        return 50.0
    if ratio > 0.4:
        return 35.0
    return 20.0


# ──────────────────────── sector medians 캐시 ────────────────────────


_FUND_MEDIANS_CACHE: dict[tuple[Market, str], dict[str, float | None]] = {}


def _get_sector_fundamental_medians(
    market: Market, sector: str | None
) -> dict[str, float | None]:
    if not sector:
        return {}
    key: tuple[Market, str] = (market, sector)
    if key in _FUND_MEDIANS_CACHE:
        return _FUND_MEDIANS_CACHE[key]
    from stock_compass.db import get_db_connection, get_sector_fundamental_medians

    with get_db_connection() as conn:
        medians = get_sector_fundamental_medians(conn, market, sector)
    _FUND_MEDIANS_CACHE[key] = medians
    return medians


def clear_sector_medians_cache() -> None:
    """테스트 또는 batch 재실행 시 stale 데이터 제거."""
    _FUND_MEDIANS_CACHE.clear()


def calculate_at_date(
    qf: QuarterlyFinancials | None,
    *,
    as_of: date_cls,
    market_cap_at_date: float | None = None,
) -> FactorScore:
    """백필용 시점별 Fundamentals — TTM 기반 revenue_growth/op_margin/ROE/earnings_growth.

    FCF yield 는 시점별 market_cap 이 필요 (close x shares_outstanding) — 호출자가
    `market_cap_at_date` 주입 시 산출, 아니면 component 제외.
    데이터 부족 시 backfill_skip ('fundamentals').
    """
    if qf is None or qf.is_empty():
        return backfill_skip("fundamentals")

    m = metrics_at(qf, as_of)
    if not m:
        return backfill_skip("fundamentals")

    revenue_growth = m.get("revenue_growth_yoy")
    earnings_growth = m.get("earnings_growth_yoy")
    operating_margin = m.get("operating_margin")
    roe = m.get("roe")
    fcf_ttm = m.get("fcf_ttm")
    fcf_yield = (
        fcf_ttm / market_cap_at_date
        if fcf_ttm is not None
        and market_cap_at_date is not None
        and market_cap_at_date > 0
        else None
    )

    components: dict[str, float] = {}
    if (s := _score_revenue_growth(revenue_growth)) is not None:
        components["revenue_growth_yoy"] = s
    if (s := _score_earnings_growth(earnings_growth)) is not None:
        components["earnings_growth_yoy"] = s
    if (s := _score_roe(roe)) is not None:
        components["roe"] = s
    if (s := _score_op_margin(operating_margin)) is not None:
        components["operating_margin"] = s
    if fcf_yield is not None and (
        s := _score_fcf_yield(fcf_ttm, market_cap_at_date)
    ) is not None:
        components["fcf_yield"] = s

    if not components:
        return backfill_skip("fundamentals")

    score = sum(components.values()) / len(components)
    return FactorScore(
        name="fundamentals",
        score=round(score, 2),
        weight=DEFAULT_WEIGHTS["fundamentals"],
        raw_values={
            "revenue_growth_yoy": revenue_growth,
            "earnings_growth_yoy": earnings_growth,
            "roe": roe,
            "operating_margin": operating_margin,
            "fcf_yield": fcf_yield,
            "revenue_ttm": m.get("revenue_ttm"),
            "ni_ttm": m.get("ni_ttm"),
            "equity_avg": m.get("equity_avg"),
            "component_scores": components,
        },
        note=f"[backfill 시점별] TTM 기반 ({len(components)} 지표)",
        source="backfill_reconstructed",
    )


# ──────────────────────── 진입점 ────────────────────────


def _score_with_sector(
    value: float | None,
    median: float | None,
    absolute_score: float | None,
) -> tuple[float | None, str]:
    """median 있고 value 양수면 ratio 점수, 아니면 절대 점수. 사용 method 표시."""
    if value is not None and value > 0 and median is not None and median > 0:
        s = _score_ratio_higher_better(value, median)
        if s is not None:
            return s, "sector"
    return absolute_score, "absolute" if absolute_score is not None else "none"


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    fund = adapter.get_fundamentals(ticker)
    medians = _get_sector_fundamental_medians(fund.market, fund.sector)

    fcf_yield = (
        fund.free_cash_flow / fund.market_cap
        if fund.free_cash_flow is not None
        and fund.market_cap is not None
        and fund.market_cap > 0
        else None
    )

    # (name, value, median, absolute_score)
    pairs: list[tuple[str, float | None, float | None, float | None]] = [
        (
            "revenue_growth_yoy",
            fund.revenue_growth_yoy,
            medians.get("revenue_growth_yoy"),
            _score_revenue_growth(fund.revenue_growth_yoy),
        ),
        (
            "earnings_growth_yoy",
            fund.earnings_growth_yoy,
            medians.get("earnings_growth_yoy"),
            _score_earnings_growth(fund.earnings_growth_yoy),
        ),
        (
            "roe",
            fund.roe,
            medians.get("roe"),
            _score_roe(fund.roe),
        ),
        (
            "operating_margin",
            fund.operating_margin,
            medians.get("operating_margin"),
            _score_op_margin(fund.operating_margin),
        ),
        (
            "fcf_yield",
            fcf_yield,
            medians.get("fcf_yield"),
            _score_fcf_yield(fund.free_cash_flow, fund.market_cap),
        ),
    ]

    components: dict[str, float] = {}
    methods: dict[str, str] = {}
    for name, value, median, absolute in pairs:
        s, method = _score_with_sector(value, median, absolute)
        if s is not None:
            components[name] = s
            methods[name] = method

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

    sector_methods = [n for n, m in methods.items() if m == "sector"]
    note_method = (
        f"섹터 중앙값 비교: {','.join(sector_methods)}"
        if sector_methods
        else "절대 임계치 (sector cold-start)"
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
            "shares_outstanding": fund.shares_outstanding,
            "sector": fund.sector,
            "sector_medians": medians,
            "component_scores": components,
            "scoring_method": methods,
        },
        note=f"사용 지표 {len(components)}개 ({note_method})",
        source=fund.source,
    )
