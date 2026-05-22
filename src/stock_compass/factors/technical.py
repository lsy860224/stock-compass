"""Technical 팩터 — RSI(14), 200MA 이격률, 거래량 z-score."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore, neutral
from stock_compass.markets.base import MarketAdapter

if TYPE_CHECKING:
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


def _score_volume_z(z: float | None) -> float | None:
    if z is None:
        return None
    abs_z = abs(z)
    if abs_z > 3:
        return 70.0
    if abs_z > 1:
        return 60.0
    return 50.0


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    hist = adapter.get_price_history(ticker, period="2y")
    if hist.is_empty():
        return neutral("technical", f"가격 데이터 없음 ({ticker})")

    close = hist.df["close"]
    volume = hist.df["volume"]

    rsi = rsi_14(close)
    dist = ma200_distance(close)
    z = volume_zscore(volume)

    components: dict[str, float] = {}
    if (s := _score_rsi(rsi)) is not None:
        components["rsi_14"] = s
    if (s := _score_ma_distance(dist)) is not None:
        components["ma200_distance"] = s
    if (s := _score_volume_z(z)) is not None:
        components["volume_zscore"] = s

    if not components:
        return neutral(
            "technical",
            f"기술 지표 산출 불가 (데이터 {len(close)}일)",
            raw={"rsi_14": rsi, "ma200_distance": dist, "volume_zscore": z},
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
            "last_close": float(close.iloc[-1]),
            "data_points": len(close),
            "component_scores": components,
        },
        note=f"사용 지표 {len(components)}개: {', '.join(components)}",
        source=hist.source,
    )
