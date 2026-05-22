"""Valuation 팩터 — PER, PBR, PEG, dividend_yield 가산."""

from __future__ import annotations

from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore, neutral
from stock_compass.markets.base import MarketAdapter


def _score_per(per: float | None) -> float | None:
    if per is None:
        return None
    if per <= 0:
        return 30.0  # 적자 — 패널티
    if per < 8:
        return 90.0
    if per < 12:
        return 80.0
    if per < 18:
        return 65.0
    if per < 25:
        return 50.0
    if per < 35:
        return 35.0
    if per < 50:
        return 20.0
    return 10.0


def _score_pbr(pbr: float | None) -> float | None:
    if pbr is None or pbr <= 0:
        return None
    if pbr < 0.8:
        return 85.0
    if pbr < 1.2:
        return 75.0
    if pbr < 2.0:
        return 60.0
    if pbr < 3.0:
        return 45.0
    if pbr < 5.0:
        return 30.0
    return 15.0


def _score_peg(peg: float | None) -> float | None:
    if peg is None or peg <= 0:
        return None
    if peg < 0.8:
        return 90.0
    if peg < 1.2:
        return 75.0
    if peg < 2.0:
        return 55.0
    if peg < 3.0:
        return 35.0
    return 15.0


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    fund = adapter.get_fundamentals(ticker)
    components: dict[str, float] = {}
    if (s := _score_per(fund.per)) is not None:
        components["per"] = s
    if (s := _score_pbr(fund.pbr)) is not None:
        components["pbr"] = s
    if (s := _score_peg(fund.peg)) is not None:
        components["peg"] = s

    if not components:
        return neutral(
            "valuation",
            "PER/PBR/PEG 모두 누락",
            raw={"per": fund.per, "pbr": fund.pbr, "peg": fund.peg},
        )

    score = sum(components.values()) / len(components)
    return FactorScore(
        name="valuation",
        score=round(score, 2),
        weight=DEFAULT_WEIGHTS["valuation"],
        raw_values={
            "per": fund.per,
            "pbr": fund.pbr,
            "peg": fund.peg,
            "dividend_yield": fund.dividend_yield,
            "component_scores": components,
        },
        note=f"사용 지표 {len(components)}개: {', '.join(components)}",
        source=fund.source,
    )
