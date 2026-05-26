"""get_previous_total_scores + get_sector_score_rank — Δ 표시·sector rank 인프라."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from stock_compass.db import (
    get_previous_total_scores,
    get_sector_score_rank,
    get_ticker_id,
    migrate,
    upsert_composite_score,
    upsert_ticker,
)
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.scoring.engine import CompositeScore


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "test.db"
    migrate(db)
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _mk_score(ticker: str, total: float, *, market="US", sector="Technology"):  # type: ignore[no-untyped-def]
    factors = [
        FactorScore(name=n, score=total, weight=DEFAULT_WEIGHTS[n])
        for n in DEFAULT_WEIGHTS
    ]
    return CompositeScore(
        ticker=ticker,
        market=market,
        total_score=total,
        verdict="관심권" if total >= 70 else "중립" if total >= 50 else "주의",
        factors=factors,
        computed_at=datetime.now(UTC),
        currency="USD" if market == "US" else "KRW",
        sector=sector,
    )


class TestPreviousTotalScores:
    def test_returns_yesterday_score(self, conn: sqlite3.Connection) -> None:
        yesterday = date(2026, 5, 26)
        today = date(2026, 5, 27)
        upsert_composite_score(conn, _mk_score("AAPL", 60.0), on_date=yesterday)

        prev = get_previous_total_scores(conn, [("AAPL", "US")], before_date=today)
        assert prev == {("AAPL", "US"): (60.0, "중립")}

    def test_returns_most_recent_before_date(
        self, conn: sqlite3.Connection
    ) -> None:
        upsert_composite_score(conn, _mk_score("AAPL", 50.0), on_date=date(2026, 5, 20))
        upsert_composite_score(conn, _mk_score("AAPL", 55.0), on_date=date(2026, 5, 25))
        upsert_composite_score(conn, _mk_score("AAPL", 60.0), on_date=date(2026, 5, 26))
        prev = get_previous_total_scores(
            conn, [("AAPL", "US")], before_date=date(2026, 5, 27)
        )
        # 5/26 (가장 최근) 선택
        assert prev[("AAPL", "US")][0] == 60.0

    def test_excludes_today(self, conn: sqlite3.Connection) -> None:
        today = date(2026, 5, 27)
        upsert_composite_score(conn, _mk_score("AAPL", 60.0), on_date=today)
        # before_date=today → today 이전만 → 빈 결과
        prev = get_previous_total_scores(conn, [("AAPL", "US")], before_date=today)
        assert prev == {}

    def test_unknown_ticker_excluded(self, conn: sqlite3.Connection) -> None:
        prev = get_previous_total_scores(
            conn, [("NOPE", "US")], before_date=date(2026, 5, 27)
        )
        assert prev == {}

    def test_empty_input_returns_empty(self, conn: sqlite3.Connection) -> None:
        assert get_previous_total_scores(conn, [], before_date=date.today()) == {}


class TestSectorScoreRank:
    def test_returns_rank_among_sector_peers(
        self, conn: sqlite3.Connection
    ) -> None:
        # 같은 sector 3종목 — 점수 80, 70, 60
        for code, score in [("AAPL", 80), ("MSFT", 70), ("NVDA", 60)]:
            upsert_composite_score(
                conn, _mk_score(code, score, sector="Technology")
            )
        nvda_id = get_ticker_id(conn, "NVDA", "US")
        assert nvda_id is not None
        rank = get_sector_score_rank(conn, "US", "Technology", nvda_id)
        # NVDA 60점 → 3 종목 중 3위
        assert rank == (3, 3)

    def test_top_rank(self, conn: sqlite3.Connection) -> None:
        for code, score in [("AAPL", 80), ("MSFT", 70)]:
            upsert_composite_score(conn, _mk_score(code, score, sector="Technology"))
        aapl_id = get_ticker_id(conn, "AAPL", "US")
        assert aapl_id is not None
        assert get_sector_score_rank(conn, "US", "Technology", aapl_id) == (1, 2)

    def test_returns_none_when_sector_missing(
        self, conn: sqlite3.Connection
    ) -> None:
        tid = upsert_ticker(
            conn,
            code="UNKNOWN",
            market="US",
            name=None,
            sector=None,
            currency="USD",
            yfinance_symbol="UNKNOWN",
        )
        assert get_sector_score_rank(conn, "US", None, tid) is None

    def test_returns_none_when_no_peers(
        self, conn: sqlite3.Connection
    ) -> None:
        # 단일 종목만 — peer 없음
        upsert_composite_score(conn, _mk_score("AAPL", 80, sector="Solo"))
        tid = get_ticker_id(conn, "AAPL", "US")
        assert tid is not None
        assert get_sector_score_rank(conn, "US", "Solo", tid) is None

    def test_different_sector_excluded(
        self, conn: sqlite3.Connection
    ) -> None:
        upsert_composite_score(conn, _mk_score("AAPL", 80, sector="Technology"))
        upsert_composite_score(conn, _mk_score("JPM", 50, sector="Financials"))
        upsert_composite_score(conn, _mk_score("MSFT", 70, sector="Technology"))
        aapl_id = get_ticker_id(conn, "AAPL", "US")
        assert aapl_id is not None
        # Technology에 AAPL, MSFT 두 종목만
        assert get_sector_score_rank(conn, "US", "Technology", aapl_id) == (1, 2)
