"""Sentiment 팩터 — SENTIMENT_MODE에 따라 API 자동 / Prompt 대기 분기.

- `api` 또는 `hybrid` + 워치리스트 scope → ClaudeSummarizer 자동 호출
- `prompt` → 50점 placeholder + 사용자 안내 (CLI `sentiment prompt`로 수동 처리)
- 모든 결과는 면책 "AI 생성 요약. 원문 확인 필수." 컨텍스트 하에 사용 가정.
"""

from __future__ import annotations

from stock_compass.config import settings
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore, neutral
from stock_compass.markets.base import MarketAdapter
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)


def calculate(adapter: MarketAdapter, ticker: str) -> FactorScore:
    mode = settings.sentiment_mode
    if mode == "prompt":
        return FactorScore(
            name="sentiment",
            score=50.0,
            weight=DEFAULT_WEIGHTS["sentiment"],
            raw_values={"mode": "prompt"},
            note="prompt 모드 — `stock-compass sentiment prompt` + `import` 대기",
            source="prompt_pending",
        )
    # api / hybrid (워치리스트 scope는 모두 API 자동)
    return _api_path(adapter, ticker)


def _api_path(adapter: MarketAdapter, ticker: str) -> FactorScore:
    # lazy import — Phase 0~3 단독 호출 시 LLM 의존성 회피
    from stock_compass.db import get_db_connection, upsert_ticker
    from stock_compass.llm.summarizer import ClaudeSummarizer

    name: str | None = None
    sector: str | None = None
    try:
        fund = adapter.get_fundamentals(ticker)
        name = fund.name
        sector = fund.sector
    except Exception as e:
        _logger.warning("sentiment: fundamentals 조회 실패 — %s (%s)", ticker, e)

    try:
        yf_sym = adapter.to_yfinance_symbol(ticker)
    except Exception:
        yf_sym = ticker

    try:
        news = adapter.get_news(ticker, days=30)
    except Exception as e:
        _logger.warning("sentiment: 뉴스 조회 실패 — %s (%s)", ticker, e)
        return neutral("sentiment", f"뉴스 조회 실패: {e}", raw={"ticker": ticker})

    if not news:
        return FactorScore(
            name="sentiment",
            score=50.0,
            weight=DEFAULT_WEIGHTS["sentiment"],
            raw_values={"news_count": 0},
            note="최근 30일 뉴스 없음",
            source="fallback",
        )

    summarizer = ClaudeSummarizer()
    with get_db_connection() as conn:
        ticker_id = upsert_ticker(
            conn,
            code=ticker,
            market=adapter.market,
            name=name,
            sector=sector,
            currency=adapter.get_currency(),
            yfinance_symbol=yf_sym,
        )
        result = summarizer.summarize_news_batch(
            conn, ticker_id=ticker_id, news=news[:10]
        )

    return FactorScore(
        name="sentiment",
        score=result.score,
        weight=DEFAULT_WEIGHTS["sentiment"],
        raw_values={
            "news_count": result.count,
            "avg_tone": result.avg_tone,
            "api_calls": result.api_count,
            "cached": result.cached_count,
        },
        note=result.note,
        source=result.source,
    )
