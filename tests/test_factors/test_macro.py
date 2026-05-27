"""Macro factor — 절대값 + 90일 추세 블렌딩 + 시장별 가중 + 죽은 컴포넌트 복구."""

from __future__ import annotations

import pytest

from stock_compass.factors import macro
from stock_compass.factors.macro import FredSeriesPoint
from stock_compass.markets.base import Currency, Market


class _FakeAdapter:
    def __init__(self, market: Market) -> None:
        self.market = market

    def get_currency(self) -> Currency:
        return "KRW" if self.market == "KR" else "USD"


def _point(latest: float | None, ma: float | None = None) -> FredSeriesPoint:
    if ma is None:
        ma = latest
    trend_pct = (
        (latest - ma) / ma * 100
        if latest is not None and ma is not None and ma != 0
        else None
    )
    return FredSeriesPoint(latest=latest, ma_90=ma, trend_pct=trend_pct)


# ──────────────────────── 절대값 임계치 (legacy) ────────────────────────


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


# ──────────────────────── 추세 점수 (MA-2) ────────────────────────


class TestTrendLowerBetter:
    @pytest.mark.parametrize(
        "latest,ma,expected",
        [
            (89, 100, 90.0),   # ratio -0.11 (강한 하락 추세)
            (94, 100, 75.0),   # ratio -0.06
            (100, 100, 55.0),  # 안정
            (103, 100, 40.0),  # +3%
            (107, 100, 25.0),  # +7%
            (115, 100, 15.0),  # 강한 상승 (위험)
        ],
    )
    def test_buckets(self, latest: float, ma: float, expected: float) -> None:
        assert macro._score_trend_lower_better(latest, ma) == expected

    def test_invalid_inputs(self) -> None:
        assert macro._score_trend_lower_better(None, 100) is None
        assert macro._score_trend_lower_better(100, None) is None
        assert macro._score_trend_lower_better(100, 0) is None


class TestTrendHigherBetter:
    @pytest.mark.parametrize(
        "latest,ma,expected",
        [
            (1.11, 1.0, 90.0),  # +11% (강한 상승 — 역전 해소 등)
            (1.07, 1.0, 75.0),  # +7%
            (1.0, 1.0, 55.0),   # 안정
            (0.97, 1.0, 40.0),  # -3%
            (0.93, 1.0, 25.0),  # -7%
            (0.85, 1.0, 15.0),  # 강한 하락
        ],
    )
    def test_buckets(self, latest: float, ma: float, expected: float) -> None:
        assert macro._score_trend_higher_better(latest, ma) == expected


class TestBlend:
    def test_both_present(self) -> None:
        # 절대값 80 + 추세 40, abs_weight 0.7 → 0.7*80 + 0.3*40 = 68
        assert macro._blend(80, 40, abs_weight=0.7) == pytest.approx(68.0)

    def test_only_absolute(self) -> None:
        assert macro._blend(80, None, abs_weight=0.7) == 80.0

    def test_only_trend(self) -> None:
        assert macro._blend(None, 40, abs_weight=0.7) == 40.0

    def test_both_none(self) -> None:
        assert macro._blend(None, None, abs_weight=0.7) is None


class TestDeadComponentRecovery:
    """MA-2 — _score_dgs10 평탄선(30~45) 변별력 복구."""

    def test_dgs10_4pct_with_falling_trend(self) -> None:
        # 4% 절대 (45점) + 추세 -8% (75점) → 0.3*45 + 0.7*75 = 66 (≫ 45)
        s = macro._score_dgs10_combined(_point(4.0, ma=4.35))
        assert s is not None
        assert s == pytest.approx(0.3 * 45 + 0.7 * 75)

    def test_dgs10_4pct_with_rising_trend(self) -> None:
        # 4% 절대 (45점) + 추세 +8% (25점) → 0.3*45 + 0.7*25 = 31 (≪ 45)
        s = macro._score_dgs10_combined(_point(4.0, ma=3.70))
        assert s is not None
        assert s == pytest.approx(0.3 * 45 + 0.7 * 25)


# ──────────────────────── calculate integration ────────────────────────


def _patch_series(
    monkeypatch: pytest.MonkeyPatch, data: dict[str, FredSeriesPoint]
) -> None:
    monkeypatch.setattr(
        macro,
        "_series",
        lambda sid: data.get(sid, FredSeriesPoint(None, None, None)),
    )


class TestMarketSpecificWeights:
    """KR은 USD/KRW 추가 + 가중 재분배, US는 기존 3 시리즈."""

    def test_us_uses_3_series(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_series(
            monkeypatch,
            {
                "VIXCLS": _point(15.0, 16.0),
                "T10Y2Y": _point(0.8, 0.5),
                "DGS10": _point(3.5, 3.8),
            },
        )
        fs = macro.calculate(_FakeAdapter("US"), "AAPL")
        comps = fs.raw_values["component_scores"]
        assert set(comps.keys()) == {"vix", "spread", "dgs10"}
        assert fs.raw_values["usdkrw"] is None
        assert fs.raw_values["market_used"] == "US"

    def test_kr_adds_usdkrw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_series(
            monkeypatch,
            {
                "VIXCLS": _point(15.0, 16.0),
                "T10Y2Y": _point(0.8, 0.5),
                "DGS10": _point(3.5, 3.8),
                "DEXKOUS": _point(1150.0, 1200.0),
            },
        )
        fs = macro.calculate(_FakeAdapter("KR"), "005930")
        comps = fs.raw_values["component_scores"]
        assert set(comps.keys()) == {"vix", "spread", "dgs10", "usdkrw"}
        assert fs.raw_values["usdkrw"] == 1150.0
        assert fs.raw_values["usdkrw_ma90"] == 1200.0
        assert fs.raw_values["market_used"] == "KR"

    def test_kr_without_usdkrw_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # DEXKOUS 미수신 — usdkrw 점수 누락이지만 다른 3 시리즈로 정규화
        _patch_series(
            monkeypatch,
            {
                "VIXCLS": _point(15.0, 16.0),
                "T10Y2Y": _point(0.8, 0.5),
                "DGS10": _point(3.5, 3.8),
            },
        )
        fs = macro.calculate(_FakeAdapter("KR"), "005930")
        comps = fs.raw_values["component_scores"]
        assert "usdkrw" not in comps
        assert {"vix", "spread", "dgs10"}.issubset(set(comps.keys()))

    def test_all_missing_returns_neutral(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_series(monkeypatch, {})
        fs = macro.calculate(_FakeAdapter("US"), "AAPL")
        assert fs.score == 50.0
        assert "누락" in fs.note

    def test_raw_values_expose_trend_pct(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_series(
            monkeypatch,
            {
                "VIXCLS": _point(15.0, 18.0),  # 추세 약 -16.7%
                "T10Y2Y": _point(0.5, 0.5),
                "DGS10": _point(3.5, 3.8),
            },
        )
        fs = macro.calculate(_FakeAdapter("US"), "AAPL")
        assert fs.raw_values["vix_trend_pct"] == pytest.approx(
            (15.0 - 18.0) / 18.0 * 100, rel=1e-3
        )
        assert fs.raw_values["dgs10_ma90"] == 3.8

    def test_only_latest_no_ma_uses_absolute_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ma_90 None → blend가 abs_score만 사용 (이전 동작 호환)
        _patch_series(
            monkeypatch,
            {
                "VIXCLS": FredSeriesPoint(15.0, None, None),
                "T10Y2Y": FredSeriesPoint(0.5, None, None),
                "DGS10": FredSeriesPoint(3.5, None, None),
            },
        )
        fs = macro.calculate(_FakeAdapter("US"), "AAPL")
        # absolute-only — _score_vix(15) == 75
        assert fs.raw_values["component_scores"]["vix"] == 75.0
