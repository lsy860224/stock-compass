"""테스트용 CompositeScore 빌더."""

from __future__ import annotations

from datetime import UTC, datetime

from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.scoring.engine import CompositeScore


def make_score(
    ticker: str = "AAPL",
    total: float = 50.0,
    *,
    market: str = "US",
    name: str | None = "Apple Inc.",
) -> CompositeScore:
    factors = [
        FactorScore(
            name=n,  # type: ignore[arg-type]
            score=total,
            weight=DEFAULT_WEIGHTS[n],  # type: ignore[index]
        )
        for n in DEFAULT_WEIGHTS
    ]
    return CompositeScore(
        ticker=ticker,
        market=market,  # type: ignore[arg-type]
        total_score=total,
        verdict="관심권" if total >= 70 else "중립" if total >= 50 else "주의",
        factors=factors,
        computed_at=datetime.now(UTC),
        name=name,
        currency="USD" if market == "US" else "KRW",
    )
