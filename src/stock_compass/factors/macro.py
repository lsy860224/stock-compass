"""Macro 팩터 — FRED VIX·10Y·장단기 스프레드 (글로벌 risk-on/off)."""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from typing import Any

from stock_compass.config import settings
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore, neutral
from stock_compass.markets.base import MarketAdapter
from stock_compass.utils.cache import load_json, save_json
from stock_compass.utils.logging import get_logger
from stock_compass.utils.retry import external_call_retry

_logger = get_logger(__name__)
_FRED_TTL = timedelta(hours=6)


@lru_cache(maxsize=1)
def _fred_client() -> Any:
    from fredapi import Fred

    return Fred(api_key=settings.fred_api_key.get_secret_value())


@external_call_retry
def _fetch_latest_fred(series_id: str) -> float | None:
    try:
        series = _fred_client().get_series(series_id).dropna()
    except (ConnectionError, TimeoutError, ValueError, OSError) as e:
        _logger.warning("FRED 시리즈 호출 실패: %s (%s)", series_id, e)
        return None
    if series.empty:
        return None
    return float(series.iloc[-1])


def _latest(series_id: str) -> float | None:
    cache_name = f"fred-{series_id}"
    cached = load_json(cache_name, _FRED_TTL)
    if isinstance(cached, int | float):
        return float(cached)
    v = _fetch_latest_fred(series_id)
    if v is not None:
        save_json(cache_name, v)
    return v


def _score_vix(vix: float | None) -> float | None:
    if vix is None:
        return None
    if vix < 15:
        return 90.0
    if vix < 20:
        return 75.0
    if vix < 25:
        return 55.0
    if vix < 30:
        return 35.0
    if vix < 40:
        return 20.0
    return 10.0


def _score_spread(spread: float | None) -> float | None:
    """T10Y2Y (%). 음수 = 장단기 역전 = 경기침체 신호."""
    if spread is None:
        return None
    if spread >= 1.0:
        return 80.0
    if spread >= 0.5:
        return 70.0
    if spread >= 0.0:
        return 55.0
    if spread >= -0.5:
        return 35.0
    return 15.0


def _score_dgs10(y: float | None) -> float | None:
    """미국 10Y (%). 높을수록 할인율 부담."""
    if y is None:
        return None
    if y < 3.0:
        return 70.0
    if y < 4.0:
        return 60.0
    if y < 5.0:
        return 45.0
    return 30.0


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    _ = (adapter, ticker)  # macro는 글로벌 — 종목 무관
    vix = _latest("VIXCLS")
    spread = _latest("T10Y2Y")
    dgs10 = _latest("DGS10")

    s_vix = _score_vix(vix)
    s_spread = _score_spread(spread)
    s_dgs10 = _score_dgs10(dgs10)

    # 가중: VIX 0.45, T10Y2Y 0.35, DGS10 0.20 — 사용 가능한 것만으로 정규화
    weights = {"vix": (s_vix, 0.45), "spread": (s_spread, 0.35), "dgs10": (s_dgs10, 0.20)}
    valid = {k: (s, w) for k, (s, w) in weights.items() if s is not None}
    if not valid:
        return neutral(
            "macro",
            "FRED 데이터 모두 누락",
            raw={"vix": vix, "spread": spread, "dgs10": dgs10},
        )

    total_weight = sum(w for _, w in valid.values())
    score = sum(s * w for s, w in valid.values()) / total_weight

    return FactorScore(
        name="macro",
        score=round(score, 2),
        weight=DEFAULT_WEIGHTS["macro"],
        raw_values={
            "vix": vix,
            "t10y2y_spread": spread,
            "dgs10": dgs10,
            "component_scores": {k: s for k, (s, _) in valid.items()},
        },
        note=f"FRED {len(valid)}개 시리즈 (글로벌, 종목 무관)",
        source="fred",
    )
