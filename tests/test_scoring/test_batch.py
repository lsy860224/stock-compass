"""ScoringEngine.analyze_watchlist — 병렬 처리·입력 순서 보존·실패 격리."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

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


class TestPersistTransaction:
    """batch persist가 트랜잭션으로 묶여 부분 실패 시 전체 롤백 (P3-1)."""

    def test_persist_all_success(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from stock_compass.config import settings
        from stock_compass.db import (
            get_db_connection,
            get_last_score,
            migrate,
        )

        db = tmp_path / "test.db"
        migrate(db)
        original = settings.db_path
        settings.db_path = db
        try:
            engine = ScoringEngine()
            with patch.object(
                engine,
                "analyze",
                side_effect=lambda t, market=None: _mk(t, 50.0),
            ):
                engine.analyze_watchlist(["AAPL", "MSFT"], persist=True)
            with get_db_connection() as conn:
                assert get_last_score(conn, "AAPL", "US") is not None
                assert get_last_score(conn, "MSFT", "US") is not None
        finally:
            settings.db_path = original

    def test_partial_persist_failure_rolls_back(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from stock_compass.config import settings
        from stock_compass.db import get_db_connection, get_last_score, migrate
        from stock_compass.db import scores as scores_mod

        db = tmp_path / "test.db"
        migrate(db)
        original = settings.db_path
        settings.db_path = db

        original_upsert = scores_mod.upsert_composite_score
        call_count = {"n": 0}

        def fake_upsert(conn, score, on_date=None):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise sqlite3.DatabaseError("simulated 2nd ticker failure")
            return original_upsert(conn, score, on_date=on_date)

        try:
            engine = ScoringEngine()
            with (
                patch.object(
                    engine,
                    "analyze",
                    side_effect=lambda t, market=None: _mk(t, 50.0),
                ),
                patch(
                    "stock_compass.db.upsert_composite_score",
                    side_effect=fake_upsert,
                ),
                pytest.raises(sqlite3.DatabaseError),
            ):
                engine.analyze_watchlist(
                    ["AAPL", "MSFT", "NVDA"], persist=True
                )
            # 부분 실패 → 전체 롤백
            with get_db_connection() as conn:
                assert get_last_score(conn, "AAPL", "US") is None
                assert get_last_score(conn, "MSFT", "US") is None
                assert get_last_score(conn, "NVDA", "US") is None
        finally:
            settings.db_path = original
