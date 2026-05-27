"""Valuation 팩터 — PER, PBR, PEG.

CLAUDE.md 8) "PER, PBR, PEG (업종 중앙값 대비)" — sector median이 있으면
ratio 기반 상대 점수화, cold-start(< 3 종목)면 절대 임계치 fallback.
같은 (market, sector) 중앙값은 process-level dict 캐시 (배치 안에서 재사용).

`calculate_at_date(qf, ...)`: 백필용 시점별 PER/PBR 재구성 (TTM EPS·BPS 기반).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date as date_cls

from stock_compass.factors._fundamentals_history import metrics_at
from stock_compass.factors.base import (
    DEFAULT_WEIGHTS,
    FactorScore,
    backfill_skip,
    neutral,
)
from stock_compass.markets.base import Market, MarketAdapter, QuarterlyFinancials

_AbsoluteScorer = Callable[[float | None], float | None]


# ──────────────────────── 절대 임계치 (cold-start fallback) ────────────────────────


def _score_per_absolute(per: float | None) -> float | None:
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


def _score_pbr_absolute(pbr: float | None) -> float | None:
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


def _score_peg_absolute(peg: float | None) -> float | None:
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


# ──────────────────────── 상대 점수 (sector median ratio) ────────────────────────


def _score_ratio(value: float | None, median: float | None) -> float | None:
    """value/median 비율. 낮을수록 가치주(고점수). 두 값 모두 양수 필요."""
    if value is None or value <= 0 or median is None or median <= 0:
        return None
    ratio = value / median
    if ratio < 0.6:
        return 90.0
    if ratio < 0.8:
        return 78.0
    if ratio < 1.0:
        return 65.0
    if ratio < 1.3:
        return 50.0
    if ratio < 1.8:
        return 35.0
    if ratio < 2.5:
        return 22.0
    return 10.0


def _score_or_fallback(
    value: float | None,
    median: float | None,
    absolute_fn: _AbsoluteScorer,
) -> tuple[float | None, str]:
    """median 있으면 상대 점수, 없으면 절대 점수. 어느 경로 사용했는지 반환."""
    if median is not None and median > 0 and value is not None and value > 0:
        s = _score_ratio(value, median)
        if s is not None:
            return s, "sector"
    return absolute_fn(value), "absolute"


# ──────────────────────── sector medians 캐시 ────────────────────────


_MEDIANS_CACHE: dict[tuple[Market, str], dict[str, float | None]] = {}


def _get_sector_medians(market: Market, sector: str | None) -> dict[str, float | None]:
    if not sector:
        return {}
    key: tuple[Market, str] = (market, sector)
    if key in _MEDIANS_CACHE:
        return _MEDIANS_CACHE[key]
    from stock_compass.db import get_db_connection, get_sector_valuation_medians

    with get_db_connection() as conn:
        medians = get_sector_valuation_medians(conn, market, sector)
    _MEDIANS_CACHE[key] = medians
    return medians


def clear_sector_medians_cache() -> None:
    """테스트 또는 batch 재실행 시 stale 데이터 제거."""
    _MEDIANS_CACHE.clear()


def calculate_at_date(
    qf: QuarterlyFinancials | None,
    *,
    as_of: date_cls,
    close_at_date: float,
) -> FactorScore:
    """백필용 시점별 Valuation — TTM EPS·BPS 기반 PER/PBR.

    `qf.shares_outstanding` 은 현재 시점 값 (시점별 데이터 부재) — 시점별
    shares 변동이 큰 종목은 정밀도 ↓. PEG 는 미산출 (forward EPS 부재).
    데이터 부족 시 backfill_skip ('valuation').
    """
    if qf is None or qf.is_empty():
        return backfill_skip("valuation")
    shares = qf.shares_outstanding
    if shares is None or shares <= 0:
        return backfill_skip("valuation")

    m = metrics_at(qf, as_of)
    if not m:
        return backfill_skip("valuation")

    ni_ttm = m.get("ni_ttm")
    equity_avg = m.get("equity_avg")
    eps_ttm = ni_ttm / shares if ni_ttm is not None else None
    bps = equity_avg / shares if equity_avg is not None and equity_avg > 0 else None

    per = (
        close_at_date / eps_ttm
        if eps_ttm is not None and eps_ttm > 0 and close_at_date > 0
        else None
    )
    pbr = (
        close_at_date / bps
        if bps is not None and bps > 0 and close_at_date > 0
        else None
    )

    components: dict[str, float] = {}
    if (s := _score_per_absolute(per)) is not None:
        components["per"] = s
    if (s := _score_pbr_absolute(pbr)) is not None:
        components["pbr"] = s

    if not components:
        return backfill_skip("valuation")

    score = sum(components.values()) / len(components)
    return FactorScore(
        name="valuation",
        score=round(score, 2),
        weight=DEFAULT_WEIGHTS["valuation"],
        raw_values={
            "per": per,
            "pbr": pbr,
            "eps_ttm": eps_ttm,
            "bps": bps,
            "shares_outstanding": shares,
            "ni_ttm": ni_ttm,
            "equity_avg": equity_avg,
            "component_scores": components,
        },
        note=(
            f"[backfill 시점별] TTM 기반 ({len(components)} 지표) — "
            "shares는 현재값 가정"
        ),
        source="backfill_reconstructed",
    )


# ──────────────────────── 진입점 ────────────────────────


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    fund = adapter.get_fundamentals(ticker)
    medians = _get_sector_medians(fund.market, fund.sector)

    components: dict[str, float] = {}
    methods: dict[str, str] = {}
    pairs: list[tuple[str, float | None, float | None, _AbsoluteScorer]] = [
        ("per", fund.per, medians.get("per"), _score_per_absolute),
        ("pbr", fund.pbr, medians.get("pbr"), _score_pbr_absolute),
        ("peg", fund.peg, medians.get("peg"), _score_peg_absolute),
    ]
    for name, value, median, absolute_fn in pairs:
        s, method = _score_or_fallback(value, median, absolute_fn)
        if s is not None:
            components[name] = s
            methods[name] = method

    if not components:
        return neutral(
            "valuation",
            "PER/PBR/PEG 모두 누락",
            raw={"per": fund.per, "pbr": fund.pbr, "peg": fund.peg},
        )

    sector_methods = [n for n, m in methods.items() if m == "sector"]
    note_method = (
        f"섹터 중앙값 비교: {','.join(sector_methods)}"
        if sector_methods
        else "절대 임계치 (sector cold-start)"
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
            "sector": fund.sector,
            "sector_medians": medians,
            "component_scores": components,
            "scoring_method": methods,
        },
        note=f"사용 지표 {len(components)}개 ({note_method})",
        source=fund.source,
    )
