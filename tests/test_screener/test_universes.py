"""유니버스 — refresh_watchlist + 외부 소스 stub."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stock_compass.db import migrate
from stock_compass.screener.universes import (
    UNIVERSE_WATCHLIST,
    list_universe_members,
    refresh_kospi_200,
    refresh_sp500,
    refresh_watchlist,
)


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    from stock_compass.config import settings

    db = tmp_path / "test.db"
    migrate(db)
    monkeypatch.setattr(settings, "db_path", db)
    monkeypatch.setattr(settings, "watchlist_kr", ["005930", "035720"])
    monkeypatch.setattr(settings, "watchlist_us", ["AAPL", "MSFT", "NVDA"])
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


class TestRefreshWatchlist:
    def test_populates_members(self, conn: sqlite3.Connection) -> None:
        r = refresh_watchlist(conn)
        assert r.universe_code == UNIVERSE_WATCHLIST
        assert r.members == 5  # 2 KR + 3 US

        rows = conn.execute(
            """
            SELECT t.code FROM universe_members um
            JOIN tickers t ON t.id = um.ticker_id
            WHERE um.universe_code = ?
            ORDER BY t.code
            """,
            (UNIVERSE_WATCHLIST,),
        ).fetchall()
        codes = {r["code"] for r in rows}
        assert codes == {"005930", "035720", "AAPL", "MSFT", "NVDA"}

    def test_idempotent(self, conn: sqlite3.Connection) -> None:
        refresh_watchlist(conn)
        r2 = refresh_watchlist(conn)
        # 같은 as_of_date에 재실행해도 PK 충돌 → DO NOTHING
        assert r2.members == 5

    def test_listing(self, conn: sqlite3.Connection) -> None:
        refresh_watchlist(conn)
        members = list_universe_members(conn, universe_code=UNIVERSE_WATCHLIST)
        assert len(members) == 5
        markets = {m["market"] for m in members}
        assert markets == {"KR", "US"}

    def test_summary_listing(self, conn: sqlite3.Connection) -> None:
        refresh_watchlist(conn)
        summary = list_universe_members(conn)
        assert len(summary) == 1
        assert summary[0]["universe_code"] == UNIVERSE_WATCHLIST
        assert summary[0]["member_count"] == 5


class TestExternalStubs:
    def test_kospi_200_not_implemented(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(NotImplementedError, match="Phase 7-2"):
            refresh_kospi_200(conn)

    def test_sp500_not_implemented(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(NotImplementedError, match="Phase 7-2"):
            refresh_sp500(conn)
