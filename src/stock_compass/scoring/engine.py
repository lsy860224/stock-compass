"""ScoringEngine — 5팩터 병렬 호출 → 가중 평균 → CompositeScore."""

from __future__ import annotations

import concurrent.futures
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from stock_compass.factors import REGISTRY, FactorName, FactorScore, neutral
from stock_compass.markets import get_adapter
from stock_compass.markets.base import Market
from stock_compass.utils.dates import now_utc
from stock_compass.utils.logging import get_logger

if TYPE_CHECKING:
    from stock_compass.markets.base import MarketAdapter

_logger = get_logger(__name__)

Verdict = Literal["관심권", "중립", "주의"]

# CLAUDE.md 1) 절대 원칙 — 모든 출력에 면책 자동 삽입
DISCLAIMER = (
    "투자 자문이 아닙니다. 본인 판단의 보조 자료입니다. "
    "BUY/SELL 신호 아님 — 정보 요약 + 정량 점수."
)

# CLAUDE.md 8) 등급 컷
_THRESH_INTEREST = 70.0
_THRESH_NEUTRAL = 50.0


def _verdict(total: float) -> Verdict:
    if total >= _THRESH_INTEREST:
        return "관심권"
    if total >= _THRESH_NEUTRAL:
        return "중립"
    return "주의"


class CompositeScore(BaseModel):
    """단일 종목 종합 점수 + 5팩터 분해."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    market: Market
    total_score: float = Field(ge=0.0, le=100.0)
    verdict: Verdict
    factors: list[FactorScore]
    computed_at: datetime
    price_at_score: float | None = None
    currency: str | None = None
    disclaimer: str = DISCLAIMER

    def factor(self, name: FactorName) -> FactorScore | None:
        return next((f for f in self.factors if f.name == name), None)


class ScoringEngine:
    """팩터 병렬 호출 + 가중 평균 계산."""

    def __init__(self, max_workers: int = 5) -> None:
        self.max_workers = max_workers

    def analyze(self, ticker: str, market: Market | None = None) -> CompositeScore:
        adapter = get_adapter(ticker, market)
        factors = self._run_factors(adapter, ticker)
        total = self._weighted_average(factors)
        technical = next((f for f in factors if f.name == "technical"), None)
        price = technical.raw_values.get("last_close") if technical else None
        return CompositeScore(
            ticker=ticker,
            market=adapter.market,
            total_score=round(total, 2),
            verdict=_verdict(total),
            factors=factors,
            computed_at=now_utc(),
            price_at_score=float(price) if isinstance(price, int | float) else None,
            currency=adapter.get_currency(),
        )

    # ─── 내부 ───

    def _run_factors(self, adapter: MarketAdapter, ticker: str) -> list[FactorScore]:
        results: dict[FactorName, FactorScore] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            future_to_name = {
                ex.submit(self._safe_calc, name, adapter, ticker): name
                for name in REGISTRY
            }
            for fut in concurrent.futures.as_completed(future_to_name):
                name = future_to_name[fut]
                results[name] = fut.result()
        # 일관된 순서로 반환
        return [results[name] for name in REGISTRY]

    @staticmethod
    def _safe_calc(name: FactorName, adapter: MarketAdapter, ticker: str) -> FactorScore:
        try:
            return REGISTRY[name](adapter, ticker)
        except Exception as e:
            _logger.exception("팩터 계산 실패: %s / %s — %s", name, ticker, e)
            return neutral(name, f"계산 실패: {type(e).__name__}: {e}")

    @staticmethod
    def _weighted_average(factors: list[FactorScore]) -> float:
        total_w = sum(f.weight for f in factors)
        if total_w == 0:
            return 50.0
        return sum(f.score * f.weight for f in factors) / total_w
