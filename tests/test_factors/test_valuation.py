"""Valuation factor — sector-relative scoring + cold-start fallback."""

from __future__ import annotations

import pytest

from stock_compass.factors import valuation
from stock_compass.markets.base import Fundamentals


class _FakeAdapter:
    """get_fundamentals만 사용되는 minimal stub."""

    def __init__(self, fund: Fundamentals) -> None:
        self._fund = fund

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return self._fund


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    valuation.clear_sector_medians_cache()


def _patch_medians(
    monkeypatch: pytest.MonkeyPatch, medians: dict[str, float | None]
) -> None:
    monkeypatch.setattr(
        valuation,
        "_get_sector_medians",
        lambda market, sector: medians,
    )


class TestAbsoluteThresholds:
    @pytest.mark.parametrize(
        "per,expected",
        [(5, 90.0), (10, 80.0), (15, 65.0), (20, 50.0), (30, 35.0), (40, 20.0), (60, 10.0)],
    )
    def test_per_buckets(self, per: float, expected: float) -> None:
        assert valuation._score_per_absolute(per) == expected

    def test_negative_per_penalty(self) -> None:
        assert valuation._score_per_absolute(-5) == 30.0

    def test_none_returns_none(self) -> None:
        assert valuation._score_per_absolute(None) is None
        assert valuation._score_pbr_absolute(None) is None
        assert valuation._score_peg_absolute(None) is None


class TestRatioScoring:
    @pytest.mark.parametrize(
        "value,median,expected",
        [
            (5, 10, 90.0),   # ratio 0.5 (저평가)
            (7, 10, 78.0),   # ratio 0.7
            (9, 10, 65.0),   # ratio 0.9
            (12, 10, 50.0),  # ratio 1.2
            (15, 10, 35.0),  # ratio 1.5
            (22, 10, 22.0),  # ratio 2.2
            (30, 10, 10.0),  # ratio 3.0+
        ],
    )
    def test_ratio_buckets(
        self, value: float, median: float, expected: float
    ) -> None:
        assert valuation._score_ratio(value, median) == expected

    def test_none_value(self) -> None:
        assert valuation._score_ratio(None, 10) is None
        assert valuation._score_ratio(10, None) is None
        assert valuation._score_ratio(0, 10) is None  # 음수/0 무효
        assert valuation._score_ratio(10, 0) is None


class TestCalculate:
    def _fund(self, **kw: object) -> Fundamentals:
        defaults: dict[str, object] = {
            "ticker": "AAPL",
            "market": "US",
            "currency": "USD",
            "sector": "Technology",
            "per": 20.0,
            "pbr": 5.0,
            "peg": 1.5,
        }
        defaults.update(kw)
        return Fundamentals(**defaults)  # type: ignore[arg-type]

    def test_cold_start_uses_absolute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_medians(monkeypatch, {})
        fs = valuation.calculate(_FakeAdapter(self._fund()), "AAPL")
        assert fs.raw_values["scoring_method"] == {
            "per": "absolute",
            "pbr": "absolute",
            "peg": "absolute",
        }
        assert "절대 임계치" in fs.note

    def test_sector_relative_when_medians_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # sector median 30 → PER 20 ratio 0.667 → 78점 (절대 임계치 PER 20 → 50점)
        _patch_medians(monkeypatch, {"per": 30.0, "pbr": 8.0, "peg": 3.0})
        fs = valuation.calculate(_FakeAdapter(self._fund()), "AAPL")
        assert fs.raw_values["scoring_method"]["per"] == "sector"
        assert fs.raw_values["component_scores"]["per"] == 78.0
        assert "섹터 중앙값" in fs.note

    def test_mixed_when_only_some_medians(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_medians(monkeypatch, {"per": 25.0, "pbr": None, "peg": None})
        fs = valuation.calculate(_FakeAdapter(self._fund()), "AAPL")
        assert fs.raw_values["scoring_method"]["per"] == "sector"
        assert fs.raw_values["scoring_method"]["pbr"] == "absolute"
        assert fs.raw_values["scoring_method"]["peg"] == "absolute"

    def test_neutral_when_all_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_medians(monkeypatch, {})
        fs = valuation.calculate(
            _FakeAdapter(self._fund(per=None, pbr=None, peg=None)),
            "AAPL",
        )
        assert fs.score == 50.0  # neutral
