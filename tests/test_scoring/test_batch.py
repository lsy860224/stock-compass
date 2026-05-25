"""ScoringEngine.analyze_watchlist — 병렬 처리·입력 순서 보존·실패 격리."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.scoring.engine import CompositeScore, ScoringEngine


def _mk(ticker: str, score: float) -> CompositeScore:
    factors = [
        FactorScore(name=name, score=score, weight=DEFAULT_WEIGHTS[name])
        for name in DEFAULT_WEIGHTS
    ]
    return CompositeScore(
        ticker=ticker,
        market="US",
        total_score=score,
        verdict="중립",
        factors=factors,
        computed_at=datetime.now(UTC),
    )


class TestAnalyzeWatchlist:
    def test_empty_returns_empty(self) -> None:
        assert ScoringEngine().analyze_watchlist([], persist=False) == []

    def test_preserves_input_order(self) -> None:
        engine = ScoringEngine(max_workers=3)
        with patch.object(engine, "analyze", side_effect=lambda t, market=None: _mk(t, 50.0)):
            results = engine.analyze_watchlist(
                ["AAPL", "MSFT", "NVDA"], persist=False
            )
        assert [r.ticker for r in results] == ["AAPL", "MSFT", "NVDA"]

    def test_failed_ticker_skipped_not_crash(self) -> None:
        engine = ScoringEngine()

        def fake(ticker: str, market: str | None = None) -> CompositeScore:
            if ticker == "BAD":
                raise RuntimeError("simulated")
            return _mk(ticker, 50.0)

        with patch.object(engine, "analyze", side_effect=fake):
            results = engine.analyze_watchlist(
                ["AAPL", "BAD", "MSFT"], persist=False
            )
        assert [r.ticker for r in results] == ["AAPL", "MSFT"]
