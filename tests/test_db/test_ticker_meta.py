"""ticker_meta upsert·조회 + 스크리너 뷰 size 칼럼 노출 (Phase A a3)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from stock_compass.db import (
    get_latest_ticker_meta,
    migrate,
    upsert_composite_score,
    upsert_ticker,
    upsert_ticker_meta,
)
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.scoring.engine import CompositeScore
from stock_compass.scoring.size import TickerMeta


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "test.db"
    migrate(p)
    c = sqlite3.connect(p, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _ticker_id(conn: sqlite3.Connection, code: str = "AAPL", market: str = "US") -> int:
    return upsert_ticker(
        conn,
        code=code,
        market=market,  # type: ignore[arg-type]
        name=f"{code} Inc.",
        sector="Tech",
        currency="USD" if market == "US" else "KRW",
        yfinance_symbol=code,
    )


def _meta(bucket: str = "large", cap: float = 1.0e10) -> TickerMeta:
    return TickerMeta(
        market_cap=cap,
        market_cap_krw=cap * 1350.0,
        size_bucket=bucket,  # type: ignore[arg-type]
        shares_outstanding=1.0e8,
    )


class TestUpsertTickerMeta:
    def test_insert_and_get_latest(self, conn: sqlite3.Connection) -> None:
        tid = _ticker_id(conn)
        upsert_ticker_meta(conn, ticker_id=tid, as_of=date(2026, 5, 28), meta=_meta())
        got = get_latest_ticker_meta(conn, tid)
        assert got is not None
        assert got.size_bucket == "large"
        assert got.market_cap == 1.0e10
        assert got.market_cap_krw == 1.0e10 * 1350.0

    def test_conflict_updates_same_date(self, conn: sqlite3.Connection) -> None:
        tid = _ticker_id(conn)
        d = date(2026, 5, 28)
        upsert_ticker_meta(conn, ticker_id=tid, as_of=d, meta=_meta("mid"))
        upsert_ticker_meta(conn, ticker_id=tid, as_of=d, meta=_meta("mega"))
        got = get_latest_ticker_meta(conn, tid)
        assert got is not None and got.size_bucket == "mega"
        n = conn.execute(
            "SELECT COUNT(*) FROM ticker_meta WHERE ticker_id = ?", (tid,)
        ).fetchone()[0]
        assert n == 1

    def test_get_latest_picks_most_recent(self, conn: sqlite3.Connection) -> None:
        tid = _ticker_id(conn)
        upsert_ticker_meta(conn, ticker_id=tid, as_of=date(2026, 1, 2), meta=_meta("small"))
        upsert_ticker_meta(conn, ticker_id=tid, as_of=date(2026, 5, 1), meta=_meta("mega"))
        got = get_latest_ticker_meta(conn, tid)
        assert got is not None and got.size_bucket == "mega"

    def test_source_check_constraint(self, conn: sqlite3.Connection) -> None:
        tid = _ticker_id(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ticker_meta (ticker_id, as_of_date, source) VALUES (?,?,?)",
                (tid, "2026-05-28", "manual"),
            )

    def test_missing_returns_none(self, conn: sqlite3.Connection) -> None:
        tid = _ticker_id(conn)
        assert get_latest_ticker_meta(conn, tid) is None


def _seed_composite(conn: sqlite3.Connection, ticker: str, on_date: date) -> int:
    factors = [
        FactorScore(name=n, score=60.0, weight=DEFAULT_WEIGHTS[n])  # type: ignore[arg-type]
        for n in DEFAULT_WEIGHTS
    ]
    score = CompositeScore(
        ticker=ticker,
        market="US",
        total_score=60.0,
        verdict="중립",
        factors=factors,
        computed_at=datetime.now(UTC),
        price_at_score=200.0,
        currency="USD",
        name=f"{ticker} Inc.",
        sector="Tech",
        yfinance_symbol=ticker,
    )
    return upsert_composite_score(conn, score, on_date=on_date)


class TestViewExposure:
    def test_v_latest_scores_exposes_size(self, conn: sqlite3.Connection) -> None:
        d = date(2026, 5, 28)
        tid = _seed_composite(conn, "AAPL", d)
        upsert_ticker_meta(conn, ticker_id=tid, as_of=d, meta=_meta("mega", 3.0e12))
        row = conn.execute(
            "SELECT size_bucket, market_cap_krw FROM v_latest_scores WHERE code = 'AAPL'"
        ).fetchone()
        assert row is not None
        assert row["size_bucket"] == "mega"
        assert row["market_cap_krw"] == 3.0e12 * 1350.0

    def test_v_at_date_point_in_time_size(self, conn: sqlite3.Connection) -> None:
        from stock_compass.screener.views import build_v_at_date_ctes

        tid = _seed_composite(conn, "AAPL", date(2026, 3, 1))
        # 과거(작은 캡) → 이후(큰 캡) 두 시점
        upsert_ticker_meta(conn, ticker_id=tid, as_of=date(2026, 3, 1), meta=_meta("small", 1.0e9))
        upsert_ticker_meta(conn, ticker_id=tid, as_of=date(2026, 5, 1), meta=_meta("mega", 3.0e12))

        ctes = build_v_at_date_ctes(date(2026, 3, 15))
        row = conn.execute(
            f"WITH {ctes} SELECT size_bucket FROM _vad WHERE code = 'AAPL'"
        ).fetchone()
        # 2026-03-15 시점엔 3/1 메타(small)만 보여야 함 (5/1 look-ahead 차단)
        assert row is not None and row["size_bucket"] == "small"
