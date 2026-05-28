"""Technical 팩터 — RSI(14), MA200, 거래량 z-score 단위 테스트."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_compass.factors.technical import (
    _calculate_from_series,
    _score_atr,
    _score_bollinger,
    _score_ma_distance,
    _score_macd,
    _score_rsi,
    _score_volume_z,
    atr_14,
    bollinger_percent_b,
    ma200_distance,
    macd,
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


# ──────────────────────── MACD ────────────────────────


class TestMACD:
    def test_short_series_returns_none(self) -> None:
        s = pd.Series([100.0] * 30)
        assert macd(s) == (None, None, None)

    def test_monotonic_increase_positive_histogram(self) -> None:
        s = pd.Series([100.0 + i for i in range(60)])
        line, signal, hist = macd(s)
        assert line is not None and signal is not None and hist is not None
        # 단조 증가 → MACD 양수 + signal 위
        assert line > 0
        assert line > signal

    def test_monotonic_decrease_negative_histogram(self) -> None:
        s = pd.Series([200.0 - i for i in range(60)])
        line, signal, hist = macd(s)
        assert line is not None and signal is not None and hist is not None
        assert line < 0
        assert line < signal


class TestMACDScore:
    def test_strong_uptrend(self) -> None:
        # macd 양수 + signal 위 + histogram 양수 = 강한 상승
        assert _score_macd(1.5, 1.0, 0.5) == 80.0

    def test_weak_uptrend_below_zero(self) -> None:
        # macd 음수지만 signal 위 = 반등 시작
        assert _score_macd(-0.5, -1.0, 0.5) == 65.0

    def test_strong_downtrend(self) -> None:
        assert _score_macd(-1.5, -1.0, -0.5) == 20.0

    def test_weak_downtrend_above_zero(self) -> None:
        # macd 양수지만 signal 아래 = 반락 시작
        assert _score_macd(0.5, 1.0, -0.5) == 35.0

    def test_ambiguous_when_signs_disagree(self) -> None:
        # signal 위인데 histogram 음수 (cross-over 직후) — 모호
        assert _score_macd(1.5, 1.0, -0.1) == 50.0

    def test_none_returns_none(self) -> None:
        assert _score_macd(None, 1.0, 0.5) is None
        assert _score_macd(1.0, None, 0.5) is None
        assert _score_macd(1.0, 0.5, None) is None


# ──────────────────────── ATR ────────────────────────


class TestATR14:
    def test_short_series_returns_none(self) -> None:
        high = pd.Series([float(i) + 1 for i in range(10)])
        low = pd.Series([float(i) for i in range(10)])
        close = pd.Series([float(i) + 0.5 for i in range(10)])
        assert atr_14(high, low, close) is None

    def test_flat_market_low_atr(self) -> None:
        # 변동성 거의 0 — high=low=close
        high = pd.Series([100.0] * 30)
        low = pd.Series([100.0] * 30)
        close = pd.Series([100.0] * 30)
        atr = atr_14(high, low, close)
        assert atr == 0.0

    def test_constant_range_steady(self) -> None:
        # high-low = 2 일정
        high = pd.Series([102.0] * 30)
        low = pd.Series([100.0] * 30)
        close = pd.Series([101.0] * 30)
        atr = atr_14(high, low, close)
        assert atr is not None
        assert 1.0 < atr <= 2.0  # TR=2 이지만 시작값 0 영향으로 1~2 사이


class TestATRScore:
    def test_very_low_volatility(self) -> None:
        # ATR/close = 0.01 → 70점
        assert _score_atr(1.0, 100.0) == 70.0

    def test_moderate_volatility(self) -> None:
        # ATR/close = 0.025 → 55점
        assert _score_atr(2.5, 100.0) == 55.0

    def test_high_volatility(self) -> None:
        # ATR/close = 0.04 → 40점
        assert _score_atr(4.0, 100.0) == 40.0

    def test_extreme_volatility(self) -> None:
        # ATR/close = 0.08 → 25점
        assert _score_atr(8.0, 100.0) == 25.0

    def test_none_returns_none(self) -> None:
        assert _score_atr(None, 100.0) is None
        assert _score_atr(1.0, None) is None
        assert _score_atr(1.0, 0) is None


# ──────────────────────── Bollinger %B ────────────────────────


class TestBollingerPercentB:
    def test_short_series_returns_none(self) -> None:
        s = pd.Series([100.0] * 10)
        assert bollinger_percent_b(s) is None

    def test_flat_zero_std_returns_none(self) -> None:
        s = pd.Series([100.0] * 25)
        assert bollinger_percent_b(s) is None

    def test_close_at_middle(self) -> None:
        # 정규 분포 → 마지막 값이 평균이면 %B ≈ 0.5
        rng = np.random.default_rng(seed=7)
        vals = rng.normal(loc=100, scale=2, size=24).tolist()
        vals.append(100.0)  # 마지막 = 평균
        s = pd.Series(vals)
        pct = bollinger_percent_b(s)
        assert pct is not None
        assert 0.3 < pct < 0.7  # 중간 영역


class TestBollingerScore:
    @pytest.mark.parametrize(
        "pct,expected",
        [
            (-0.1, 85.0),  # 하단 이탈 (과매도)
            (0.1, 75.0),   # 하단 근처
            (0.4, 60.0),   # 중간 하단
            (0.7, 50.0),   # 중간 상단
            (0.9, 35.0),   # 상단 근처
            (1.1, 20.0),   # 상단 이탈 (과매수)
        ],
    )
    def test_buckets(self, pct: float, expected: float) -> None:
        assert _score_bollinger(pct) == expected

    def test_none(self) -> None:
        assert _score_bollinger(None) is None


# ──────────────────────── 통합 6 components ────────────────────────


class TestFromSeriesAllComponents:
    def test_includes_macd_atr_bollinger_when_data_sufficient(self) -> None:
        rng = np.random.default_rng(seed=42)
        n = 250
        closes = 100.0 + np.cumsum(rng.normal(0, 1, n))
        highs = closes + rng.uniform(0.5, 1.5, n)
        lows = closes - rng.uniform(0.5, 1.5, n)
        volumes = rng.uniform(900, 1100, n)

        fs = _calculate_from_series(
            close=pd.Series(closes),
            volume=pd.Series(volumes),
            source="test",
            high=pd.Series(highs),
            low=pd.Series(lows),
        )
        cs = fs.raw_values["component_scores"]
        # 6 components 모두 산출
        assert set(cs.keys()) == {
            "rsi_14",
            "ma200_distance",
            "volume_zscore",
            "macd",
            "atr_14",
            "bollinger_pct_b",
        }
        assert fs.raw_values["macd_line"] is not None
        assert fs.raw_values["atr_14"] is not None
        assert fs.raw_values["bollinger_pct_b"] is not None

    def test_atr_skipped_when_high_low_missing(self) -> None:
        rng = np.random.default_rng(seed=42)
        n = 250
        closes = 100.0 + np.cumsum(rng.normal(0, 1, n))
        volumes = rng.uniform(900, 1100, n)

        fs = _calculate_from_series(
            close=pd.Series(closes),
            volume=pd.Series(volumes),
            source="test",
            # high/low 미전달 → ATR skip
        )
        cs = fs.raw_values["component_scores"]
        assert "atr_14" not in cs
        # 나머지 5 components 는 산출
        assert "macd" in cs
        assert "bollinger_pct_b" in cs
