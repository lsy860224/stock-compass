"""Macro factor — VIX/spread/dgs10 글로벌 + KR USD/KRW 시장별 가중."""

from __future__ import annotations

import pytest

from stock_compass.factors import macro
from stock_compass.markets.base import Currency, Market


class _FakeAdapter:
    def __init__(self, market: Market) -> None:
        self.market = market

    def get_currency(self) -> Currency:
        return "KRW" if self.market == "KR" else "USD"


class TestVixBuckets:
    @pytest.mark.parametrize(
        "v,expected",
        [(10, 90.0), (17, 75.0), (22, 55.0), (28, 35.0), (35, 20.0), (50, 10.0)],
    )
    def test_buckets(self, v: float, expected: float) -> None:
        assert macro._score_vix(v) == expected


class TestSpreadBuckets:
    @pytest.mark.parametrize(
        "s,expected",
        [(1.5, 80.0), (0.7, 70.0), (0.2, 55.0), (-0.3, 35.0), (-1.0, 15.0)],
    )
    def test_buckets(self, s: float, expected: float) -> None:
        assert macro._score_spread(s) == expected


class TestDgs10Buckets:
    @pytest.mark.parametrize(
        "y,expected",
        [(2.5, 70.0), (3.5, 60.0), (4.5, 45.0), (6.0, 30.0)],
    )
    def test_buckets(self, y: float, expected: float) -> None:
        assert macro._score_dgs10(y) == expected


class TestUsdKrwBuckets:
    @pytest.mark.parametrize(
        "rate,expected",
        [(1050, 75.0), (1150, 65.0), (1250, 55.0), (1350, 40.0), (1500, 25.0)],
    )
    def test_buckets(self, rate: float, expected: float) -> None:
        assert macro._score_usdkrw(rate) == expected

    def test_invalid_inputs(self) -> None:
        assert macro._score_usdkrw(None) is None
        assert macro._score_usdkrw(0) is None
        assert macro._score_usdkrw(-100) is None


class TestMarketSpecificWeights:
    """KR은 USD/KRW 추가 + 가중 재분배, US는 기존 3 시리즈."""

    def test_us_uses_3_series(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _l(sid: str) -> float | None:
            return {"VIXCLS": 15.0, "T10Y2Y": 0.8, "DGS10": 3.5}.get(sid)

        monkeypatch.setattr(macro, "_latest", _l)
        fs = macro.calculate(_FakeAdapter("US"), "AAPL")
        comps = fs.raw_values["component_scores"]
        assert set(comps.keys()) == {"vix", "spread", "dgs10"}
        assert fs.raw_values["usdkrw"] is None
        assert fs.raw_values["market_used"] == "US"

    def test_kr_adds_usdkrw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _l(sid: str) -> float | None:
            return {
                "VIXCLS": 15.0,
                "T10Y2Y": 0.8,
                "DGS10": 3.5,
                "DEXKOUS": 1150.0,
            }.get(sid)

        monkeypatch.setattr(macro, "_latest", _l)
        fs = macro.calculate(_FakeAdapter("KR"), "005930")
        comps = fs.raw_values["component_scores"]
        assert set(comps.keys()) == {"vix", "spread", "dgs10", "usdkrw"}
        assert fs.raw_values["usdkrw"] == 1150.0
        assert fs.raw_values["market_used"] == "KR"

    def test_kr_without_usdkrw_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # DEXKOUS 미수신 — usdkrw 점수 누락이지만 다른 3 시리즈로 정규화
        def _l(sid: str) -> float | None:
            return {"VIXCLS": 15.0, "T10Y2Y": 0.8, "DGS10": 3.5}.get(sid)

        monkeypatch.setattr(macro, "_latest", _l)
        fs = macro.calculate(_FakeAdapter("KR"), "005930")
        comps = fs.raw_values["component_scores"]
        assert "usdkrw" not in comps
        assert {"vix", "spread", "dgs10"}.issubset(set(comps.keys()))

    def test_all_missing_returns_neutral(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(macro, "_latest", lambda sid: None)
        fs = macro.calculate(_FakeAdapter("US"), "AAPL")
        assert fs.score == 50.0
        assert "누락" in fs.note
