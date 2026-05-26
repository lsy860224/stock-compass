"""Technical 팩터 — RSI(14), MA200, 거래량 z-score 단위 테스트."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_compass.factors.technical import (
    _score_ma_distance,
    _score_rsi,
    _score_volume_z,
    ma200_distance,
    return_n,
    rsi_14,
    volume_zscore,
)


class TestRSI14:
    def test_short_series_returns_none(self) -> None:
        s = pd.Series(range(10))
        assert rsi_14(s) is None

    def test_monotonic_increase_is_max(self) -> None:
        s = pd.Series([float(i) for i in range(50)])
        rsi = rsi_14(s)
        assert rsi is not None
        assert rsi >= 99.0  # 손실 0 → RSI 100 (또는 매우 근접)

    def test_monotonic_decrease_is_min(self) -> None:
        s = pd.Series([float(50 - i) for i in range(50)])
        rsi = rsi_14(s)
        assert rsi is not None
        assert rsi <= 1.0  # 이익 0 → RSI 0

    def test_flat_series_returns_neutral(self) -> None:
        s = pd.Series([100.0] * 50)
        rsi = rsi_14(s)
        assert rsi == 50.0

    def test_value_in_bounds(self) -> None:
        prices = [10, 11, 12, 11.5, 13, 14, 13.5, 12, 11, 12.5, 13, 14, 15, 16, 15.5, 14, 13]
        rsi = rsi_14(pd.Series([float(x) for x in prices]))
        assert rsi is not None
        assert 0 <= rsi <= 100


class TestRSIScore:
    @pytest.mark.parametrize(
        "rsi,expected",
        [
            (15, 90),
            (25, 80),
            (40, 65),
            (50, 50),
            (60, 40),
            (75, 25),
            (85, 10),
        ],
    )
    def test_buckets(self, rsi: float, expected: float) -> None:
        assert _score_rsi(rsi) == expected

    def test_none(self) -> None:
        assert _score_rsi(None) is None


class TestMA200Distance:
    def test_short_series(self) -> None:
        s = pd.Series([100.0] * 199)
        assert ma200_distance(s) is None

    def test_flat_is_zero(self) -> None:
        s = pd.Series([100.0] * 200)
        d = ma200_distance(s)
        assert d == 0.0

    def test_above_ma(self) -> None:
        s = pd.Series([100.0] * 199 + [110.0])
        d = ma200_distance(s)
        assert d is not None and d > 0


class TestMADistanceScore:
    def test_sweet_spot(self) -> None:
        assert _score_ma_distance(0.0) == 75.0

    def test_overextended(self) -> None:
        assert _score_ma_distance(0.60) == 25.0

    def test_deep_below(self) -> None:
        assert _score_ma_distance(-0.30) == 25.0


class TestVolumeZScore:
    def test_short_series(self) -> None:
        s = pd.Series([100] * 30)
        assert volume_zscore(s) is None

    def test_normal(self) -> None:
        rng = np.random.default_rng(seed=42)
        s = pd.Series(rng.normal(loc=1000, scale=100, size=100))
        z = volume_zscore(s)
        assert z is not None
        assert -5 < z < 5  # 정상 분포에서 합리적 범위


class TestReturnN:
    def test_short_series(self) -> None:
        assert return_n(pd.Series([1.0, 2.0, 3.0]), n=5) is None

    def test_5d_positive(self) -> None:
        # 100 → 110 = +10%
        s = pd.Series([100, 105, 108, 102, 106, 110])
        assert return_n(s, n=5) == pytest.approx(0.10)

    def test_zero_base_safe(self) -> None:
        s = pd.Series([0, 1, 2, 3, 4, 5])
        assert return_n(s, n=5) is None  # base 0 → None


class TestVolumeZScoreDirectional:
    """AT1 — volume z-score가 price return과 결합되어 매집/투매 구분."""

    def test_none_z_returns_none(self) -> None:
        assert _score_volume_z(None) is None
        assert _score_volume_z(None, return_5d=0.10) is None

    def test_backward_compat_without_return(self) -> None:
        # return_5d 없으면 기존 abs(z) 절대값 경로
        assert _score_volume_z(2.0) == 60.0
        assert _score_volume_z(0.5) == 50.0
        assert _score_volume_z(4.0) == 70.0

    def test_buying_pressure_high_z(self) -> None:
        # 거래량 폭증 + 가격 상승 = 매집 (80점)
        assert _score_volume_z(3.5, return_5d=0.05) == 80.0

    def test_buying_pressure_moderate_z(self) -> None:
        # 거래량 증가 + 가격 상승 (70점)
        assert _score_volume_z(1.5, return_5d=0.03) == 70.0

    def test_selling_pressure_high_z(self) -> None:
        # 거래량 폭증 + 가격 하락 = 투매 (25점)
        assert _score_volume_z(3.5, return_5d=-0.05) == 25.0

    def test_selling_pressure_moderate_z(self) -> None:
        assert _score_volume_z(1.5, return_5d=-0.03) == 35.0

    def test_low_z_neutral(self) -> None:
        # 거래량 평범 (|z|<1) — 중립
        assert _score_volume_z(0.5, return_5d=0.10) == 55.0
        assert _score_volume_z(-0.5, return_5d=-0.10) == 55.0

    def test_high_z_flat_price_neutral(self) -> None:
        # 거래량 폭증 + 가격 횡보 — 신호 모호
        assert _score_volume_z(3.0, return_5d=0.005) == 55.0
        assert _score_volume_z(3.0, return_5d=-0.005) == 55.0
