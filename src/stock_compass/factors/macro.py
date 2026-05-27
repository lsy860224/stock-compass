"""Macro 팩터 — FRED VIX·10Y·장단기 스프레드 + KR 종목 시 USD/KRW.

CLAUDE.md 8) Macro 15%는 미국 거시(VIX/T10Y2Y/DGS10)가 베이스. KR 종목은
USD/KRW 환율(FRED DEXKOUS)을 추가 가중. **절대값 + 90일 평균 대비 추세를
블렌딩**하여 변별력 확보 (예: 현재 5% 금리도 90일 대비 하락 추세면 우호).

이전: 절대값 임계치만 — 현재 4-5% 금리 환경에서 _score_dgs10 30~45 평탄선.
지금: 절대값 30% + 추세 70% 블렌딩 (VIX는 절대 70% — 공포 자체가 정보).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Any

from stock_compass.config import settings
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore, neutral
from stock_compass.markets.base import Market, MarketAdapter
from stock_compass.utils.cache import load_json, save_json
from stock_compass.utils.logging import get_logger
from stock_compass.utils.retry import external_call_retry

_logger = get_logger(__name__)
_FRED_TTL = timedelta(hours=6)
_MA_WINDOW = 90  # 거래일 (≈ 4.5개월)


@dataclass(frozen=True, slots=True)
class FredSeriesPoint:
    """FRED 시리즈의 latest + 90일 평균 + 추세율."""

    latest: float | None
    ma_90: float | None
    trend_pct: float | None  # (latest - ma_90) / ma_90 * 100


_EMPTY_POINT = FredSeriesPoint(None, None, None)


@lru_cache(maxsize=1)
def _fred_client() -> Any:
    from fredapi import Fred

    return Fred(api_key=settings.fred_api_key.get_secret_value())


@external_call_retry
def _fetch_series_fred(series_id: str) -> FredSeriesPoint:
    """최신값 + 90일 평균 + 추세율. 실패 시 모든 필드 None."""
    try:
        series = _fred_client().get_series(series_id).dropna()
    except (ConnectionError, TimeoutError, ValueError, OSError) as e:
        _logger.warning("FRED 시리즈 호출 실패: %s (%s)", series_id, e)
        return _EMPTY_POINT
    if series.empty:
        return _EMPTY_POINT
    latest = float(series.iloc[-1])
    recent = series.iloc[-_MA_WINDOW:]
    ma_90: float | None = float(recent.mean()) if not recent.empty else None
    trend_pct: float | None = (
        (latest - ma_90) / ma_90 * 100
        if ma_90 is not None and ma_90 != 0
        else None
    )
    return FredSeriesPoint(latest=latest, ma_90=ma_90, trend_pct=trend_pct)


def _series(series_id: str) -> FredSeriesPoint:
    """6h 캐시 — `_fetch_series_fred` 래퍼."""
    cache_name = f"fred-series-{series_id}"
    cached = load_json(cache_name, _FRED_TTL)
    if isinstance(cached, dict):
        return FredSeriesPoint(
            latest=cached.get("latest"),
            ma_90=cached.get("ma_90"),
            trend_pct=cached.get("trend_pct"),
        )
    point = _fetch_series_fred(series_id)
    if point.latest is not None:
        save_json(cache_name, asdict(point))
    return point


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


def _score_usdkrw(rate: float | None) -> float | None:
    """USD/KRW. 낮을수록 (원화 강세) 외인 자금·수입 우호 → 가산. KR 종목 전용."""
    if rate is None or rate <= 0:
        return None
    if rate < 1100:
        return 75.0
    if rate < 1200:
        return 65.0
    if rate < 1300:
        return 55.0
    if rate < 1400:
        return 40.0
    return 25.0


# ──────────────────────── 추세 점수 ────────────────────────


def _score_trend_lower_better(
    latest: float | None, ma_90: float | None
) -> float | None:
    """현재 값이 90일 평균 대비 *낮을수록* 점수 ↑ (VIX/금리/USDKRW 용)."""
    if latest is None or ma_90 is None or ma_90 == 0:
        return None
    ratio = (latest - ma_90) / ma_90
    if ratio < -0.10:
        return 90.0  # 강한 하락 추세
    if ratio < -0.05:
        return 75.0
    if ratio < 0.02:
        return 55.0  # 거의 안정
    if ratio < 0.05:
        return 40.0
    if ratio < 0.10:
        return 25.0
    return 15.0  # 강한 상승 추세 (위험)


def _score_trend_higher_better(
    latest: float | None, ma_90: float | None
) -> float | None:
    """현재 값이 90일 평균 대비 *높을수록* 점수 ↑ (T10Y2Y spread 용 — 역전 해소)."""
    if latest is None or ma_90 is None or ma_90 == 0:
        return None
    ratio = (latest - ma_90) / ma_90
    if ratio > 0.10:
        return 90.0
    if ratio > 0.05:
        return 75.0
    if ratio > -0.02:
        return 55.0
    if ratio > -0.05:
        return 40.0
    if ratio > -0.10:
        return 25.0
    return 15.0


def _blend(
    abs_score: float | None,
    trend_score: float | None,
    *,
    abs_weight: float,
) -> float | None:
    """절대값 + 추세 점수 블렌딩. 둘 중 하나만 있으면 그것만 사용."""
    if abs_score is None and trend_score is None:
        return None
    if trend_score is None:
        return abs_score
    if abs_score is None:
        return trend_score
    trend_weight = 1.0 - abs_weight
    return abs_weight * abs_score + trend_weight * trend_score


def _score_vix_combined(point: FredSeriesPoint) -> float | None:
    """VIX — 절대값 우선(공포 자체 정보) + 추세 보조."""
    return _blend(
        _score_vix(point.latest),
        _score_trend_lower_better(point.latest, point.ma_90),
        abs_weight=0.70,
    )


def _score_dgs10_combined(point: FredSeriesPoint) -> float | None:
    """10Y 금리 — 추세 우선(절대값은 환경 의존). 변별력 회복."""
    return _blend(
        _score_dgs10(point.latest),
        _score_trend_lower_better(point.latest, point.ma_90),
        abs_weight=0.30,
    )


def _score_spread_combined(point: FredSeriesPoint) -> float | None:
    """T10Y2Y — 절대값(역전 자체 신호) + 추세(회복 방향) 균형."""
    return _blend(
        _score_spread(point.latest),
        _score_trend_higher_better(point.latest, point.ma_90),
        abs_weight=0.50,
    )


def _score_usdkrw_combined(point: FredSeriesPoint) -> float | None:
    """USD/KRW — 절대값(절대 레벨 의미) + 추세(자금 흐름 방향) 균형."""
    return _blend(
        _score_usdkrw(point.latest),
        _score_trend_lower_better(point.latest, point.ma_90),
        abs_weight=0.50,
    )


@external_call_retry
def fetch_full_series(series_id: str) -> Any:
    """FRED 시계열 전체 (pandas Series) — backfill 용. 실패 시 빈 Series.

    `_fetch_series_fred` 가 latest+ma_90 만 캐시하는 것과 달리, backfill 은
    매 시점 ma_90 계산을 위해 시계열 자체가 필요.
    """
    import pandas as pd

    try:
        return _fred_client().get_series(series_id).dropna()
    except (ConnectionError, TimeoutError, ValueError, OSError) as e:
        _logger.warning("FRED 전체 시계열 호출 실패: %s (%s)", series_id, e)
        return pd.Series(dtype=float)


def _point_at_date(series: Any, as_of: Any) -> FredSeriesPoint:
    """시계열에서 `as_of` 이하 마지막 값 + 그 시점까지의 90일 평균.

    `as_of` 다음날 데이터는 사용 안 함 (look-ahead 차단).
    series 빈 또는 as_of 이전 데이터 없으면 _EMPTY_POINT.
    """
    if series is None or len(series) == 0:
        return _EMPTY_POINT
    # pandas Series: index 가 datetime — as_of 이하 slice
    # Pandas 4.0+: date 객체 직접 슬라이싱 deprecated → Timestamp 변환
    import pandas as pd

    as_of_ts = pd.Timestamp(as_of)
    sliced = series.loc[:as_of_ts]
    if sliced.empty:
        return _EMPTY_POINT
    latest = float(sliced.iloc[-1])
    recent = sliced.iloc[-_MA_WINDOW:]
    ma_90: float | None = float(recent.mean()) if not recent.empty else None
    trend_pct: float | None = (
        (latest - ma_90) / ma_90 * 100
        if ma_90 is not None and ma_90 != 0
        else None
    )
    return FredSeriesPoint(latest=latest, ma_90=ma_90, trend_pct=trend_pct)


def calculate_at_date(
    market: Market,
    series_cache: dict[str, Any],
    as_of: Any,
) -> FactorScore:
    """백필용 시점별 Macro 점수 — 미리 fetch한 FRED 시계열에서 시점 slice.

    `series_cache`: {series_id: pandas.Series} — `fetch_full_series` 결과.
    백필 엔진이 모든 시리즈를 한 번만 fetch하고 모든 시점에 재사용 (효율적).
    """
    vix_p = _point_at_date(series_cache.get("VIXCLS"), as_of)
    spread_p = _point_at_date(series_cache.get("T10Y2Y"), as_of)
    dgs10_p = _point_at_date(series_cache.get("DGS10"), as_of)
    usdkrw_p = (
        _point_at_date(series_cache.get("DEXKOUS"), as_of)
        if market == "KR"
        else _EMPTY_POINT
    )
    return _aggregate_macro(
        market, vix_p, spread_p, dgs10_p, usdkrw_p, source="backfill"
    )


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    _ = ticker
    market = adapter.market
    vix_p = _series("VIXCLS")
    spread_p = _series("T10Y2Y")
    dgs10_p = _series("DGS10")
    usdkrw_p = _series("DEXKOUS") if market == "KR" else _EMPTY_POINT
    return _aggregate_macro(market, vix_p, spread_p, dgs10_p, usdkrw_p, source="fred")


def _aggregate_macro(
    market: Market,
    vix_p: FredSeriesPoint,
    spread_p: FredSeriesPoint,
    dgs10_p: FredSeriesPoint,
    usdkrw_p: FredSeriesPoint,
    *,
    source: str,
) -> FactorScore:
    s_vix = _score_vix_combined(vix_p)
    s_spread = _score_spread_combined(spread_p)
    s_dgs10 = _score_dgs10_combined(dgs10_p)
    s_usdkrw = _score_usdkrw_combined(usdkrw_p) if market == "KR" else None

    # 시장별 가중: KR은 USD/KRW 비중 0.30을 차지하고 나머지 축소
    if market == "KR":
        weights = {
            "vix": (s_vix, 0.30),
            "spread": (s_spread, 0.25),
            "dgs10": (s_dgs10, 0.15),
            "usdkrw": (s_usdkrw, 0.30),
        }
    else:
        weights = {
            "vix": (s_vix, 0.45),
            "spread": (s_spread, 0.35),
            "dgs10": (s_dgs10, 0.20),
        }
    valid = {k: (s, w) for k, (s, w) in weights.items() if s is not None}
    if not valid:
        return neutral(
            "macro",
            "FRED 데이터 모두 누락",
            raw={
                "vix": vix_p.latest,
                "spread": spread_p.latest,
                "dgs10": dgs10_p.latest,
                "usdkrw": usdkrw_p.latest,
            },
        )

    total_weight = sum(w for _, w in valid.values())
    score = sum(s * w for s, w in valid.values()) / total_weight

    return FactorScore(
        name="macro",
        score=round(score, 2),
        weight=DEFAULT_WEIGHTS["macro"],
        raw_values={
            "vix": vix_p.latest,
            "vix_ma90": vix_p.ma_90,
            "vix_trend_pct": vix_p.trend_pct,
            "t10y2y_spread": spread_p.latest,
            "spread_ma90": spread_p.ma_90,
            "spread_trend_pct": spread_p.trend_pct,
            "dgs10": dgs10_p.latest,
            "dgs10_ma90": dgs10_p.ma_90,
            "dgs10_trend_pct": dgs10_p.trend_pct,
            "usdkrw": usdkrw_p.latest,
            "usdkrw_ma90": usdkrw_p.ma_90,
            "usdkrw_trend_pct": usdkrw_p.trend_pct,
            "market_used": market,
            "component_scores": {k: s for k, (s, _) in valid.items()},
        },
        note=(
            f"FRED {len(valid)}개 시리즈 (시장={market}, "
            f"abs+trend 블렌딩 — 90일 평균 대비)"
        ),
        source=source,
    )
