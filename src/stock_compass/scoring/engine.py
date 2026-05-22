"""ScoringEngine — 5팩터 병렬 호출 → 가중 평균 → CompositeScore."""

from __future__ import annotations

import concurrent.futures
import time
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from stock_compass.config import settings
from stock_compass.factors import REGISTRY, FactorName, FactorScore, neutral
from stock_compass.markets import get_adapter
from stock_compass.markets.base import Market
from stock_compass.utils.dates import now_utc
from stock_compass.utils.logging import get_logger

if TYPE_CHECKING:
    from rich.progress import Progress

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
    name: str | None = None
    sector: str | None = None
    yfinance_symbol: str | None = None
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
        name, sector = _safe_metadata(adapter, ticker)
        yfinance_symbol = _safe_symbol(adapter, ticker)
        return CompositeScore(
            ticker=ticker,
            market=adapter.market,
            total_score=round(total, 2),
            verdict=_verdict(total),
            factors=factors,
            computed_at=now_utc(),
            price_at_score=float(price) if isinstance(price, int | float) else None,
            currency=adapter.get_currency(),
            name=name,
            sector=sector,
            yfinance_symbol=yfinance_symbol,
        )

    def analyze_watchlist(
        self,
        tickers: list[str],
        *,
        market: Market | None = None,
        persist: bool = True,
        progress: Progress | None = None,
    ) -> list[CompositeScore]:
        """다종목 병렬 분석. 결과는 입력 순서 유지. persist=True 면 SQLite 자동 저장."""
        if not tickers:
            return []
        results: dict[str, CompositeScore] = {}
        task_id = (
            progress.add_task("[cyan]점수 계산", total=len(tickers))
            if progress is not None
            else None
        )

        def _job(t: str) -> tuple[str, CompositeScore | Exception]:
            try:
                return t, self.analyze(t, market)
            except Exception as e:
                return t, e
            finally:
                if settings.yfinance_throttle_sec > 0:
                    time.sleep(settings.yfinance_throttle_sec)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as ex:
            futures = [ex.submit(_job, t) for t in tickers]
            for fut in concurrent.futures.as_completed(futures):
                t, outcome = fut.result()
                if isinstance(outcome, Exception):
                    _logger.exception(
                        "워치리스트 종목 실패: %s — %s", t, outcome
                    )
                else:
                    results[t] = outcome
                if progress is not None and task_id is not None:
                    progress.update(task_id, advance=1)

        ordered = [results[t] for t in tickers if t in results]

        if persist and ordered:
            from stock_compass.db import get_db_connection, upsert_composite_score

            with get_db_connection() as conn:
                for s in ordered:
                    upsert_composite_score(conn, s)

        return ordered

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


def _safe_metadata(adapter: MarketAdapter, ticker: str) -> tuple[str | None, str | None]:
    """fundamentals에서 name/sector 추출. 캐시 적중 시 추가 비용 거의 없음."""
    try:
        fund = adapter.get_fundamentals(ticker)
    except Exception as e:
        _logger.warning("메타데이터 조회 실패: %s — %s", ticker, e)
        return None, None
    return fund.name, fund.sector


def _safe_symbol(adapter: MarketAdapter, ticker: str) -> str | None:
    try:
        return adapter.to_yfinance_symbol(ticker)
    except Exception as e:
        _logger.warning("yfinance 심볼 변환 실패: %s — %s", ticker, e)
        return None
