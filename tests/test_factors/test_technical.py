"""Technical 팩터 — RSI(14), MA200, 거래량 z-score 단위 테스트."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_compass.factors.technical import (
    _score_ma_distance,
    _score_rsi,
    ma200_distance,
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
