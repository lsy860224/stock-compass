"""Quality 팩터 (Phase A a2) — 재무 건전성: 부채비율·유동비율·ROA.

Fundamentals(성장·수익성)와 분리된 **대차대조표 건전성** 축. 모두 yfinance
.info 단일 호출에서 취득 (debtToEquity·currentRatio·returnOnAssets) — 추가
네트워크 호출 없음. 절대 임계치 기반 (섹터 비교는 향후 확장).

가중치 10% — 기존 Fundamentals 25% 를 F15 + Q10 으로 분할 (CLAUDE.md 8).
"""

from __future__ import annotations

from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore, neutral
from stock_compass.markets.base import MarketAdapter


def _score_debt_to_equity(de: float | None) -> float | None:
    """부채/자본 (yfinance % 스케일). 낮을수록 안전 = 높은 점수. 음수 = 자본잠식 패널티."""
    if de is None:
        return None
    if de < 0:
        return 15.0
    if de < 30:
        return 90.0
    if de < 60:
        return 78.0
    if de < 100:
        return 62.0
    if de < 150:
        return 48.0
    if de < 250:
        return 32.0
    return 18.0


def _score_current_ratio(cr: float | None) -> float | None:
    """유동비율 (유동자산/유동부채). 높을수록 단기 지급여력 양호."""
    if cr is None:
        return None
    if cr >= 2.5:
        return 85.0
    if cr >= 1.5:
        return 75.0
    if cr >= 1.2:
        return 62.0
    if cr >= 1.0:
        return 50.0
    if cr >= 0.8:
        return 38.0
    return 22.0


def _score_roa(roa: float | None) -> float | None:
    """ROA (총자산이익률, 소수). 자산 대비 이익 창출력 = 자본 효율성."""
    if roa is None:
        return None
    if roa >= 0.15:
        return 90.0
    if roa >= 0.10:
        return 78.0
    if roa >= 0.05:
        return 64.0
    if roa >= 0.02:
        return 50.0
    if roa > 0:
        return 40.0
    return 22.0


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    fund = adapter.get_fundamentals(ticker)

    components: dict[str, float] = {}
    if (s := _score_debt_to_equity(fund.debt_to_equity)) is not None:
        components["debt_to_equity"] = s
    if (s := _score_current_ratio(fund.current_ratio)) is not None:
        components["current_ratio"] = s
    if (s := _score_roa(fund.roa)) is not None:
        components["roa"] = s

    if not components:
        return neutral(
            "quality",
            "부채비율·유동비율·ROA 모두 누락",
            raw={
                "debt_to_equity": fund.debt_to_equity,
                "current_ratio": fund.current_ratio,
                "roa": fund.roa,
            },
        )

    score = sum(components.values()) / len(components)
    return FactorScore(
        name="quality",
        score=round(score, 2),
        weight=DEFAULT_WEIGHTS["quality"],
        raw_values={
            "debt_to_equity": fund.debt_to_equity,
            "current_ratio": fund.current_ratio,
            "roa": fund.roa,
            "component_scores": components,
        },
        note=f"사용 지표 {len(components)}개: {', '.join(components)}",
        source=fund.source,
    )
