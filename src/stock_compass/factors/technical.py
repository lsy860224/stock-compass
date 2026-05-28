"""Technical 팩터 — RSI(14), 200MA, 거래량 z, MACD, ATR(14), 볼린저 %B (+ backfill)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore, neutral
from stock_compass.markets.base import MarketAdapter

if TYPE_CHECKING:
    from datetime import date as date_cls

    import pandas as pd


def rsi_14(close: pd.Series) -> float | None:
    """Wilder's RSI(14). 데이터 < 15 → None."""
    if len(close) < 15:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder's smoothing — first value: simple mean of first 14, then recursive
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    last_gain = float(avg_gain.iloc[-1])
    last_loss = float(avg_loss.iloc[-1])
    if last_loss == 0:
        return 100.0 if last_gain > 0 else 50.0
    rs = last_gain / last_loss
    return float(100 - 100 / (1 + rs))


def ma200_distance(close: pd.Series) -> float | None:
    """(현재가 - 200MA) / 200MA. 데이터 < 200 → None."""
    if len(close) < 200:
        return None
    ma = float(close.iloc[-200:].mean())
    if ma == 0:
        return None
    return (float(close.iloc[-1]) - ma) / ma


def volume_zscore(volume: pd.Series, window: int = 60) -> float | None:
    """최근 거래량 z-score (최근 window일 평균/표준편차 대비)."""
    if len(volume) < window + 1:
        return None
    recent = volume.iloc[-window - 1 : -1]
    mean = float(recent.mean())
    std = float(recent.std())
    if std == 0:
        return None
    return (float(volume.iloc[-1]) - mean) / std


def _score_rsi(rsi: float | None) -> float | None:
    if rsi is None:
        return None
    if rsi < 20:
        return 90.0
    if rsi < 30:
        return 80.0
    if rsi < 45:
        return 65.0
    if rsi < 55:
        return 50.0
    if rsi < 70:
        return 40.0
    if rsi < 80:
        return 25.0
    return 10.0


def _score_ma_distance(d: float | None) -> float | None:
    if d is None:
        return None
    if d < -0.20:
        return 25.0
    if d < -0.05:
        return 55.0
    if d < 0.10:
        return 75.0
    if d < 0.25:
        return 65.0
    if d < 0.50:
        return 45.0
    return 25.0


def _score_volume_z(
    z: float | None, return_5d: float | None = None
) -> float | None:
    """거래량 z-score 점수.

    가격 변화율과 결합하여 매집(z↑+price↑) vs 투매(z↑+price↓) 구분.
    return_5d 미지정(과거 호환)이면 기존 abs(z) 절대값 기반.
    """
    if z is None:
        return None
    abs_z = abs(z)
    if return_5d is None:
        # 호환 경로 — 방향성 정보 없음
        if abs_z > 3:
            return 70.0
        if abs_z > 1:
            return 60.0
        return 50.0
    # 방향성 결합
    if abs_z < 1.0:
        return 55.0  # 거래량 평범 — 중립
    # 거래량 폭증
    if return_5d > 0.02:  # 상승 + 거래량 폭증 = 매집
        return 80.0 if abs_z > 3 else 70.0
    if return_5d < -0.02:  # 하락 + 거래량 폭증 = 투매
        return 25.0 if abs_z > 3 else 35.0
    # 가격 횡보 + 거래량 폭증 — 의미 모호
    return 55.0


def return_n(close: pd.Series, n: int = 5) -> float | None:
    """직전 N 거래일 수익률 (close.iloc[-1] / close.iloc[-(n+1)] - 1)."""
    if len(close) < n + 1:
        return None
    base = float(close.iloc[-(n + 1)])
    if base == 0:
        return None
    return float(close.iloc[-1]) / base - 1


# ──────────────────────── 추가 지표 ────────────────────────


def macd(
    close: pd.Series,
) -> tuple[float | None, float | None, float | None]:
    """MACD(12,26,9). 반환: (macd_line, signal_line, histogram).

    EMA 정착에 최소 26 + 9 = 35 일 필요 → 부족 시 모두 None.
    """
    if len(close) < 35:
        return (None, None, None)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    return (
        float(macd_line.iloc[-1]),
        float(signal_line.iloc[-1]),
        float(hist.iloc[-1]),
    )


def atr_14(high: pd.Series, low: pd.Series, close: pd.Series) -> float | None:
    """ATR(14). Wilder smoothing. high/low/close 동일 index 가정.

    True Range = max(high-low, |high-prev_close|, |low-prev_close|).
    `Series.where` 로 element-wise max — pd.concat 회피.
    """
    if min(len(high), len(low), len(close)) < 15:
        return None
    prev_close = close.shift(1)
    tr = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = tr.where(tr >= tr2, tr2)
    tr = tr.where(tr >= tr3, tr3)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    last = float(atr.iloc[-1])
    if math.isnan(last):
        return None
    return last


def bollinger_percent_b(
    close: pd.Series, window: int = 20, n_std: float = 2.0
) -> float | None:
    """볼린저 %B = (close - lower) / (upper - lower). 데이터 < window → None.

    %B < 0: 하단 이탈 (과매도). %B > 1: 상단 이탈 (과매수).
    """
    if len(close) < window:
        return None
    recent = close.iloc[-window:]
    mean = float(recent.mean())
    std = float(recent.std())
    if std == 0:
        return None
    upper = mean + n_std * std
    lower = mean - n_std * std
    width = upper - lower
    if width == 0:
        return None
    return (float(close.iloc[-1]) - lower) / width


def _score_macd(
    macd_line: float | None,
    signal_line: float | None,
    histogram: float | None,
) -> float | None:
    """MACD 점수 — line vs signal 관계 + line 의 영선 위치."""
    if macd_line is None or signal_line is None or histogram is None:
        return None
    above_signal = macd_line > signal_line
    if above_signal and histogram > 0:
        # 상승 추세 + 강화. macd 가 영선 위면 더 강한 신호.
        return 80.0 if macd_line > 0 else 65.0
    if not above_signal and histogram < 0:
        return 20.0 if macd_line < 0 else 35.0
    # cross-over 직전·직후 — 모호
    return 50.0


def _score_atr(atr: float | None, last_close: float | None) -> float | None:
    """ATR/close 비율 — 일변동성. 낮을수록 안정 = 높은 점수."""
    if atr is None or last_close is None or last_close <= 0:
        return None
    ratio = atr / last_close
    if ratio < 0.015:
        return 70.0
    if ratio < 0.03:
        return 55.0
    if ratio < 0.05:
        return 40.0
    return 25.0


def _score_bollinger(percent_b: float | None) -> float | None:
    """볼린저 %B — 하단 이탈(과매도)→고점, 상단 이탈(과매수)→저점."""
    if percent_b is None:
        return None
    if percent_b < 0:
        return 85.0
    if percent_b < 0.2:
        return 75.0
    if percent_b < 0.5:
        return 60.0
    if percent_b < 0.8:
        return 50.0
    if percent_b < 1.0:
        return 35.0
    return 20.0


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    hist = adapter.get_price_history(ticker, period="2y")
    if hist.is_empty():
        return neutral("technical", f"가격 데이터 없음 ({ticker})")

    return _calculate_from_series(
        close=hist.df["close"],
        volume=hist.df["volume"],
        high=hist.df["high"],
        low=hist.df["low"],
        source=hist.source,
    )


def calculate_at_close(
    close: pd.Series,
    volume: pd.Series,
    *,
    as_of: date_cls,
    source: str = "backfill",
    high: pd.Series | None = None,
    low: pd.Series | None = None,
) -> FactorScore:
    """백필용 시점별 Technical 점수.

    close/volume/high/low 는 호출자가 미리 `index <= as_of` 로 slice. high/low 없으면
    ATR component 만 skip. `source="backfill"` 로 실시간 vs 백필 구분.
    """
    _ = as_of  # 호출자가 slice 책임 — 여기선 메타 표시 용도
    if close.empty:
        return neutral("technical", f"가격 데이터 없음 (as_of={as_of})")
    return _calculate_from_series(
        close=close, volume=volume, source=source, high=high, low=low
    )


def _calculate_from_series(
    close: pd.Series,
    volume: pd.Series,
    *,
    source: str,
    high: pd.Series | None = None,
    low: pd.Series | None = None,
) -> FactorScore:
    """OHLCV series → Technical 6 지표 점수. high/low None 이면 ATR 제외."""
    rsi = rsi_14(close)
    dist = ma200_distance(close)
    z = volume_zscore(volume)
    r5 = return_n(close, 5)
    macd_line, signal_line, histogram = macd(close)
    atr = (
        atr_14(high, low, close)
        if high is not None and low is not None
        else None
    )
    pct_b = bollinger_percent_b(close)
    last_close = float(close.iloc[-1])

    components: dict[str, float] = {}
    if (s := _score_rsi(rsi)) is not None:
        components["rsi_14"] = s
    if (s := _score_ma_distance(dist)) is not None:
        components["ma200_distance"] = s
    if (s := _score_volume_z(z, r5)) is not None:
        components["volume_zscore"] = s
    if (s := _score_macd(macd_line, signal_line, histogram)) is not None:
        components["macd"] = s
    if (s := _score_atr(atr, last_close)) is not None:
        components["atr_14"] = s
    if (s := _score_bollinger(pct_b)) is not None:
        components["bollinger_pct_b"] = s

    if not components:
        return neutral(
            "technical",
            f"기술 지표 산출 불가 (데이터 {len(close)}일)",
            raw={
                "rsi_14": rsi,
                "ma200_distance": dist,
                "volume_zscore": z,
                "macd_histogram": histogram,
                "atr_14": atr,
                "bollinger_pct_b": pct_b,
            },
        )

    score = sum(components.values()) / len(components)
    return FactorScore(
        name="technical",
        score=round(score, 2),
        weight=DEFAULT_WEIGHTS["technical"],
        raw_values={
            "rsi_14": rsi,
            "ma200_distance": dist,
            "volume_zscore": z,
            "return_5d": r5,
            "macd_line": macd_line,
            "macd_signal": signal_line,
            "macd_histogram": histogram,
            "atr_14": atr,
            "bollinger_pct_b": pct_b,
            "last_close": last_close,
            "data_points": len(close),
            "component_scores": components,
        },
        note=f"사용 지표 {len(components)}개: {', '.join(components)}",
        source=source,
    )
