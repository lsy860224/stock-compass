"""Quality factor — 부채비율·유동비율·ROA 스코어 + calculate (Phase A a2)."""

from __future__ import annotations

import pytest

from stock_compass.factors import quality
from stock_compass.factors.base import DEFAULT_WEIGHTS
from stock_compass.markets.base import Fundamentals


class _FakeAdapter:
    def __init__(self, fund: Fundamentals) -> None:
        self._fund = fund

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return self._fund


def _fund(**kw: object) -> Fundamentals:
    defaults: dict[str, object] = {"ticker": "AAPL", "market": "US", "currency": "USD"}
    defaults.update(kw)
    return Fundamentals(**defaults)  # type: ignore[arg-type]


class TestScoreDebtToEquity:
    @pytest.mark.parametrize(
        ("de", "expected"),
        [
            (-10.0, 15.0),  # 자본잠식
            (10.0, 90.0),
            (45.0, 78.0),
            (80.0, 62.0),
            (120.0, 48.0),
            (200.0, 32.0),
            (400.0, 18.0),
        ],
    )
    def test_buckets(self, de: float, expected: float) -> None:
        assert quality._score_debt_to_equity(de) == expected

    def test_none(self) -> None:
        assert quality._score_debt_to_equity(None) is None


class TestScoreCurrentRatio:
    @pytest.mark.parametrize(
        ("cr", "expected"),
        [
            (3.0, 85.0),
            (2.5, 85.0),  # 경계
            (1.8, 75.0),
            (1.3, 62.0),
            (1.0, 50.0),  # 경계
            (0.9, 38.0),
            (0.5, 22.0),
        ],
    )
    def test_buckets(self, cr: float, expected: float) -> None:
        assert quality._score_current_ratio(cr) == expected

    def test_none(self) -> None:
        assert quality._score_current_ratio(None) is None


class TestScoreRoa:
    @pytest.mark.parametrize(
        ("roa", "expected"),
        [
            (0.20, 90.0),
            (0.15, 90.0),  # 경계
            (0.11, 78.0),
            (0.06, 64.0),
            (0.03, 50.0),
            (0.01, 40.0),
            (0.0, 22.0),
            (-0.05, 22.0),
        ],
    )
    def test_buckets(self, roa: float, expected: float) -> None:
        assert quality._score_roa(roa) == expected

    def test_none(self) -> None:
        assert quality._score_roa(None) is None


class TestCalculate:
    def test_all_three_components(self) -> None:
        fs = quality.calculate(
            _FakeAdapter(_fund(debt_to_equity=45.0, current_ratio=1.8, roa=0.11)),
            "AAPL",
        )
        assert fs.name == "quality"
        assert fs.weight == DEFAULT_WEIGHTS["quality"]
        comps = fs.raw_values["component_scores"]
        assert set(comps) == {"debt_to_equity", "current_ratio", "roa"}
        # (78 + 75 + 78) / 3 = 77.0
        assert fs.score == pytest.approx(77.0)
        assert "3개" in fs.note

    def test_partial_components(self) -> None:
        fs = quality.calculate(
            _FakeAdapter(_fund(debt_to_equity=10.0)), "AAPL"
        )
        assert set(fs.raw_values["component_scores"]) == {"debt_to_equity"}
        assert fs.score == 90.0

    def test_all_missing_returns_neutral(self) -> None:
        fs = quality.calculate(_FakeAdapter(_fund()), "AAPL")
        assert fs.score == 50.0
        assert fs.source == "fallback"
        assert "누락" in fs.note

    def test_raw_values_preserved(self) -> None:
        fs = quality.calculate(
            _FakeAdapter(_fund(debt_to_equity=45.0, current_ratio=1.8, roa=0.11)),
            "AAPL",
        )
        assert fs.raw_values["debt_to_equity"] == 45.0
        assert fs.raw_values["current_ratio"] == 1.8
        assert fs.raw_values["roa"] == 0.11


class TestWeights:
    def test_quality_in_default_weights(self) -> None:
        assert DEFAULT_WEIGHTS["quality"] == 0.10

    def test_weights_sum_to_one(self) -> None:
        assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)

    def test_six_factors(self) -> None:
        assert len(DEFAULT_WEIGHTS) == 6
