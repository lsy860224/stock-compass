"""분기 재무 시계열 → TTM(Trailing Twelve Months) + YoY 지표.

`QuarterlyFinancials` 입력 → `as_of` 시점에 공시된 분기만 사용해서 시점별
PER/PBR/ROE/op_margin/revenue_growth 재구성. look-ahead 차단의 핵심:
`publish_after <= as_of` 조건.

호출자: `factors/valuation.calculate_at_date`, `factors/fundamentals.calculate_at_date`,
백필 엔진.
"""

from __future__ import annotations

from datetime import date as date_cls
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stock_compass.markets.base import QuarterlyDatum, QuarterlyFinancials


def available_quarters_at(
    qf: QuarterlyFinancials, as_of: date_cls
) -> list[QuarterlyDatum]:
    """`as_of` 시점에 이미 공시된 분기만 (publish_after <= as_of), 최신 → 과거 순."""
    return [q for q in qf.quarters if q.publish_after <= as_of]


def metrics_at(qf: QuarterlyFinancials, as_of: date_cls) -> dict[str, float | None]:
    """4분기 TTM + YoY (전년 동기 TTM). 데이터 부족 시 빈 dict.

    반환 키:
    - revenue_ttm / op_income_ttm / ni_ttm / fcf_ttm
    - equity_avg (4분기 평균 자본총계 — ROE 분모)
    - revenue_growth_yoy / earnings_growth_yoy
    - operating_margin (op_ttm / rev_ttm)
    - roe (ni_ttm / equity_avg)
    """
    available = available_quarters_at(qf, as_of)
    if len(available) < 4:
        return {}

    ttm = available[:4]
    prior = available[4:8] if len(available) >= 8 else []

    rev_ttm = _sum(ttm, "revenue")
    op_ttm = _sum(ttm, "operating_income")
    ni_ttm = _sum(ttm, "net_income")
    fcf_ttm = _sum(ttm, "free_cash_flow")
    equity_avg = _avg(ttm, "equity")

    rev_prior = _sum(prior, "revenue") if prior else None
    ni_prior = _sum(prior, "net_income") if prior else None

    return {
        "revenue_ttm": rev_ttm,
        "op_income_ttm": op_ttm,
        "ni_ttm": ni_ttm,
        "fcf_ttm": fcf_ttm,
        "equity_avg": equity_avg,
        "revenue_growth_yoy": _ratio_change(rev_ttm, rev_prior),
        "earnings_growth_yoy": _ratio_change(ni_ttm, ni_prior),
        "operating_margin": _safe_divide(op_ttm, rev_ttm),
        "roe": _safe_divide(ni_ttm, equity_avg),
    }


def _sum(quarters: list[QuarterlyDatum], attr: str) -> float | None:
    """4분기 모두 값 있어야 TTM 의미 — 하나라도 None 이면 None 반환."""
    vals = [getattr(q, attr) for q in quarters]
    if any(v is None for v in vals):
        return None
    return float(sum(vals))


def _avg(quarters: list[QuarterlyDatum], attr: str) -> float | None:
    vals = [getattr(q, attr) for q in quarters if getattr(q, attr) is not None]
    if not vals:
        return None
    return float(sum(vals)) / len(vals)


def _ratio_change(now: float | None, prior: float | None) -> float | None:
    if now is None or prior is None or prior == 0:
        return None
    # 음수 prior 는 ratio 무의미 (음↔양 전환은 별도 표현 필요)
    if prior < 0:
        return None
    return (now - prior) / prior


def _safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator
