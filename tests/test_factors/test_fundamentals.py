"""Fundamentals factor — 5 component (revenue/earnings/roe/op_margin/fcf_yield) + sector 보정."""

from __future__ import annotations

import pytest

from stock_compass.factors import fundamentals
from stock_compass.markets.base import Fundamentals


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    fundamentals.clear_sector_medians_cache()


def _patch_medians(
    monkeypatch: pytest.MonkeyPatch, medians: dict[str, float | None]
) -> None:
    monkeypatch.setattr(
        fundamentals,
        "_get_sector_fundamental_medians",
        lambda market, sector: medians,
    )


class _FakeAdapter:
    def __init__(self, fund: Fundamentals) -> None:
        self._fund = fund

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return self._fund


def _fund(**kw: object) -> Fundamentals:
    defaults: dict[str, object] = {
        "ticker": "AAPL",
        "market": "US",
        "currency": "USD",
    }
    defaults.update(kw)
    return Fundamentals(**defaults)  # type: ignore[arg-type]


class TestRevenueGrowthBuckets:
    @pytest.mark.parametrize(
        "g,expected",
        [(0.30, 90.0), (0.20, 75.0), (0.10, 60.0), (0.02, 45.0), (-0.05, 30.0), (-0.20, 15.0)],
    )
    def test_buckets(self, g: float, expected: float) -> None:
        assert fundamentals._score_revenue_growth(g) == expected

    def test_none(self) -> None:
        assert fundamentals._score_revenue_growth(None) is None


class TestEarningsGrowthBuckets:
    @pytest.mark.parametrize(
        "g,expected",
        [(0.35, 95.0), (0.20, 80.0), (0.08, 65.0), (0.02, 50.0), (-0.05, 30.0), (-0.20, 15.0)],
    )
    def test_buckets(self, g: float, expected: float) -> None:
        assert fundamentals._score_earnings_growth(g) == expected


class TestRoeBuckets:
    @pytest.mark.parametrize(
        "roe,expected",
        [(0.30, 95.0), (0.20, 80.0), (0.12, 65.0), (0.07, 50.0), (0.02, 30.0), (-0.05, 15.0)],
    )
    def test_buckets(self, roe: float, expected: float) -> None:
        assert fundamentals._score_roe(roe) == expected


class TestOpMarginBuckets:
    @pytest.mark.parametrize(
        "m,expected",
        [(0.30, 85.0), (0.15, 70.0), (0.07, 55.0), (0.02, 40.0), (-0.05, 20.0)],
    )
    def test_buckets(self, m: float, expected: float) -> None:
        assert fundamentals._score_op_margin(m) == expected


class TestFcfYield:
    @pytest.mark.parametrize(
        "fcf,mcap,expected",
        [
            (10, 100, 90.0),     # 10% yield
            (6, 100, 75.0),      # 6%
            (4, 100, 60.0),      # 4%
            (2, 100, 45.0),      # 2%
            (0.5, 100, 30.0),    # 0.5%
            (-1, 100, 15.0),     # 음수
        ],
    )
    def test_buckets(self, fcf: float, mcap: float, expected: float) -> None:
        assert fundamentals._score_fcf_yield(fcf, mcap) == expected

    def test_zero_market_cap(self) -> None:
        assert fundamentals._score_fcf_yield(10, 0) is None
        assert fundamentals._score_fcf_yield(10, -100) is None

    def test_none_inputs(self) -> None:
        assert fundamentals._score_fcf_yield(None, 100) is None
        assert fundamentals._score_fcf_yield(10, None) is None


class TestCalculateIntegration:
    def test_all_5_components_present(self) -> None:
        fs = fundamentals.calculate(
            _FakeAdapter(
                _fund(
                    revenue_growth_yoy=0.15,
                    earnings_growth_yoy=0.20,
                    roe=0.15,
                    operating_margin=0.10,
                    free_cash_flow=5_000_000,
                    market_cap=100_000_000,
                )
            ),
            "AAPL",
        )
        comps = fs.raw_values["component_scores"]
        assert set(comps.keys()) == {
            "revenue_growth_yoy",
            "earnings_growth_yoy",
            "roe",
            "operating_margin",
            "fcf_yield",
        }
        assert "5개" in fs.note

    def test_partial_missing_data_uses_available(self) -> None:
        fs = fundamentals.calculate(
            _FakeAdapter(_fund(roe=0.20, operating_margin=0.15)),
            "AAPL",
        )
        comps = fs.raw_values["component_scores"]
        assert set(comps.keys()) == {"roe", "operating_margin"}

    def test_all_missing_returns_neutral(self) -> None:
        fs = fundamentals.calculate(_FakeAdapter(_fund()), "AAPL")
        assert fs.score == 50.0  # neutral
        assert "누락" in fs.note

    def test_fcf_yield_only_when_both_present(self) -> None:
        # fcf만 있고 market_cap 없으면 fcf_yield 점수 제외
        fs = fundamentals.calculate(
            _FakeAdapter(_fund(roe=0.15, free_cash_flow=5_000_000)),
            "AAPL",
        )
        assert "fcf_yield" not in fs.raw_values["component_scores"]
        assert fs.raw_values["fcf_yield"] is None


class TestRatioHigherBetter:
    @pytest.mark.parametrize(
        "value,median,expected",
        [
            (0.20, 0.10, 90.0),  # ratio 2.0
            (0.13, 0.10, 78.0),  # ratio 1.3
            (0.105, 0.10, 65.0),  # ratio 1.05
            (0.08, 0.10, 50.0),  # ratio 0.8
            (0.05, 0.10, 35.0),  # ratio 0.5
            (0.02, 0.10, 20.0),  # ratio 0.2
        ],
    )
    def test_buckets(self, value: float, median: float, expected: float) -> None:
        assert fundamentals._score_ratio_higher_better(value, median) == expected

    def test_negative_or_zero_returns_none(self) -> None:
        # 음수 ROE는 ratio 무의미 — None (factor에서 absolute fallback)
        assert fundamentals._score_ratio_higher_better(-0.05, 0.10) is None
        assert fundamentals._score_ratio_higher_better(0.10, -0.05) is None
        assert fundamentals._score_ratio_higher_better(0.10, 0) is None


class TestSectorBoost:
    """AT3 — fundamentals factor가 sector median 활용."""

    def test_cold_start_uses_absolute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_medians(monkeypatch, {})
        fs = fundamentals.calculate(
            _FakeAdapter(_fund(roe=0.20, operating_margin=0.10)),
            "AAPL",
        )
        methods = fs.raw_values["scoring_method"]
        assert all(m == "absolute" for m in methods.values())
        assert "절대 임계치" in fs.note

    def test_sector_relative_when_medians_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ROE 20% vs sector median 10% → ratio 2.0 → 90점 (절대로는 80점)
        _patch_medians(monkeypatch, {"roe": 0.10})
        fs = fundamentals.calculate(
            _FakeAdapter(_fund(roe=0.20)),
            "AAPL",
        )
        assert fs.raw_values["scoring_method"]["roe"] == "sector"
        assert fs.raw_values["component_scores"]["roe"] == 90.0

    def test_negative_value_falls_back_to_absolute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 음수 ROE는 sector ratio 무의미 → 절대 임계치 fallback
        _patch_medians(monkeypatch, {"roe": 0.10})
        fs = fundamentals.calculate(
            _FakeAdapter(_fund(roe=-0.05)),
            "AAPL",
        )
        assert fs.raw_values["scoring_method"]["roe"] == "absolute"
        assert fs.raw_values["component_scores"]["roe"] == 15.0
