"""Size·시가총액 메타데이터 도출 (Phase A a3).

팩터(가중 점수)가 아닌 **metadata** — 종합 점수에 영향 없음. 스크리너가
size_bucket·market_cap_krw 로 필터·정렬·cross-market 비교하는 용도.

- size_bucket: KRW 환산 시가총액 기준 단일 임계치 (mega~micro). 시장 무관하게
  같은 'large'가 같은 절대 규모를 의미하도록 통일.
- market_cap_krw: KR=현지값, US=현지값xUSD/KRW (FX 미가용 시 None → bucket None).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from stock_compass.markets.base import Market
from stock_compass.utils.logging import get_logger

if TYPE_CHECKING:
    from datetime import date as date_cls

_logger = get_logger(__name__)

type SizeBucket = Literal["mega", "large", "mid", "small", "micro"]

# KRW 임계치 — USD 표준 캡 티어(@~1350 KRW/USD)를 한국 단위로 라운딩.
# (threshold_krw, bucket) 내림차순. 첫 매치 적용.
_SIZE_TIERS_KRW: tuple[tuple[float, SizeBucket], ...] = (
    (200_000_000_000_000.0, "mega"),  # ≥ 200조 (≈ $148B)
    (10_000_000_000_000.0, "large"),  # ≥ 10조  (≈ $7.4B)
    (2_000_000_000_000.0, "mid"),  # ≥ 2조   (≈ $1.5B)
    (300_000_000_000.0, "small"),  # ≥ 3000억 (≈ $222M)
)


def classify_size_bucket(market_cap_krw: float | None) -> SizeBucket | None:
    """KRW 환산 시가총액 → size_bucket. None 또는 0 이하면 None."""
    if market_cap_krw is None or market_cap_krw <= 0:
        return None
    for threshold, bucket in _SIZE_TIERS_KRW:
        if market_cap_krw >= threshold:
            return bucket
    return "micro"


def to_krw(
    market_cap: float | None, market: Market, usdkrw: float | None
) -> float | None:
    """현지 통화 시가총액 → KRW 환산. KR=그대로, US=xUSD/KRW.

    US인데 usdkrw 미가용이면 None (cross-market 비교 불가 표시).
    """
    if market_cap is None or market_cap <= 0:
        return None
    if market == "KR":
        return market_cap
    if usdkrw is None or usdkrw <= 0:
        return None
    return market_cap * usdkrw


@dataclass(frozen=True, slots=True)
class TickerMeta:
    """단일 시점 size 메타데이터 — ticker_meta 테이블 1행에 대응."""

    market_cap: float | None
    market_cap_krw: float | None
    size_bucket: SizeBucket | None
    shares_outstanding: float | None


def build_ticker_meta(
    *,
    market: Market,
    market_cap: float | None,
    shares_outstanding: float | None = None,
    usdkrw: float | None = None,
) -> TickerMeta:
    """현지 시가총액 + FX → TickerMeta (KRW 환산 + bucket 도출)."""
    market_cap_krw = to_krw(market_cap, market, usdkrw)
    return TickerMeta(
        market_cap=market_cap,
        market_cap_krw=market_cap_krw,
        size_bucket=classify_size_bucket(market_cap_krw),
        shares_outstanding=shares_outstanding,
    )


def current_usdkrw() -> float | None:
    """현재 USD/KRW (FRED DEXKOUS 최신값). 실패 시 None — batch 의 US market_cap_krw 용."""
    try:
        from stock_compass.factors import macro

        series = macro.fetch_full_series("DEXKOUS")
        if series is None or len(series) == 0:
            return None
        return float(series.iloc[-1])
    except Exception as e:
        # FX 미가용은 비치명적 — market_cap_krw 를 None 으로 두고 진행.
        _logger.warning("USD/KRW 조회 실패 (market_cap_krw None 처리): %s", e)
        return None


def usdkrw_at(series: Any, as_of: date_cls) -> float | None:
    """백필용 시점별 USD/KRW — series 에서 as_of 이하 마지막 값 (look-ahead 차단)."""
    if series is None or len(series) == 0:
        return None
    import pandas as pd

    sliced = series.loc[: pd.Timestamp(as_of)]
    if sliced.empty:
        return None
    return float(sliced.iloc[-1])
