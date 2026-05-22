"""ScoringEngine — 가중 평균, verdict 경계, 안전 호출."""

from __future__ import annotations

import pytest

from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.scoring.engine import ScoringEngine, _verdict


def _mk(name: str, score: float) -> FactorScore:
    return FactorScore(
        name=name,  # type: ignore[arg-type]
        score=score,
        weight=DEFAULT_WEIGHTS[name],  # type: ignore[index]
    )


class TestWeightedAverage:
    def test_all_same_score(self) -> None:
        factors = [_mk(n, 60.0) for n in DEFAULT_WEIGHTS]
        assert ScoringEngine._weighted_average(factors) == pytest.approx(60.0)

    def test_weighted_distribution(self) -> None:
        # valuation(30%)=100, 나머지=0 → 가중평균 = 30
        factors = [
            _mk("valuation", 100.0),
            _mk("fundamentals", 0.0),
            _mk("technical", 0.0),
            _mk("macro", 0.0),
            _mk("sentiment", 0.0),
        ]
        assert ScoringEngine._weighted_average(factors) == pytest.approx(30.0)

    def test_only_sentiment_max(self) -> None:
        # sentiment(10%)=100 → 가중평균 = 10
        factors = [
            _mk("valuation", 0.0),
            _mk("fundamentals", 0.0),
            _mk("technical", 0.0),
            _mk("macro", 0.0),
            _mk("sentiment", 100.0),
        ]
        assert ScoringEngine._weighted_average(factors) == pytest.approx(10.0)

    def test_empty_returns_neutral(self) -> None:
        assert ScoringEngine._weighted_average([]) == 50.0

    def test_default_weights_sum_to_one(self) -> None:
        assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)


class TestVerdict:
    @pytest.mark.parametrize(
        "total,expected",
        [
            (100.0, "관심권"),
            (70.0, "관심권"),  # 경계 포함
            (69.99, "중립"),
            (50.0, "중립"),  # 경계 포함
            (49.99, "주의"),
            (0.0, "주의"),
        ],
    )
    def test_boundaries(self, total: float, expected: str) -> None:
        assert _verdict(total) == expected
