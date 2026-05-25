"""유니버스 — refresh_watchlist + 외부 소스 (pykrx/Wikipedia mock)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stock_compass.db import migrate
from stock_compass.screener.universes import (
    SUPPORTED_UNIVERSES,
    UNIVERSE_KOSPI_200,
    UNIVERSE_SP500,
    UNIVERSE_WATCHLIST,
    UniverseFetchError,
    list_universe_members,
    refresh,
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
        r1 = refresh_watchlist(conn)
        r2 = refresh_watchlist(conn)
        # 같은 as_of_date에 재실행 → 신규 등록 0
        assert r1.members == 5
        assert r2.members == 0
        # 실제 멤버 수는 그대로 유지
        members = list_universe_members(conn, universe_code=UNIVERSE_WATCHLIST)
        assert len(members) == 5

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
    def test_kospi_200_with_mocked_source(
        self, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "stock_compass.screener.universes._sources.fetch_kospi_200_constituents",
            lambda: [("005930", "삼성전자"), ("000660", "SK하이닉스")],
        )
        r = refresh_kospi_200(conn)
        assert r.universe_code == UNIVERSE_KOSPI_200
        assert r.members == 2
        # 종목명 정확히 보강됐는지
        row = conn.execute(
            "SELECT name FROM tickers WHERE code = '005930'"
        ).fetchone()
        assert row["name"] == "삼성전자"

    def test_sp500_with_mocked_source(
        self, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "stock_compass.screener.universes._sources.fetch_sp500_constituents",
            lambda: [
                ("AAPL", "Apple Inc.", "Information Technology"),
                ("MSFT", "Microsoft Corporation", "Information Technology"),
                ("BRK-B", "Berkshire Hathaway", "Financials"),
            ],
        )
        r = refresh_sp500(conn)
        assert r.universe_code == UNIVERSE_SP500
        assert r.members == 3
        sectors = {
            r["sector"]
            for r in conn.execute("SELECT sector FROM tickers WHERE market = 'US'")
        }
        assert "Information Technology" in sectors

    def test_empty_source_raises(
        self, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "stock_compass.screener.universes._sources.fetch_kospi_200_constituents",
            lambda: [],
        )
        with pytest.raises(UniverseFetchError, match="멤버 0개"):
            refresh_kospi_200(conn)


class TestDispatcher:
    def test_unknown_universe_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="지원하지 않는"):
            refresh(conn, "NIKKEI_225")

    def test_supported_list(self) -> None:
        # 정전 리스트 확인 — 후속 universe 추가 시 갱신 필요
        assert "WATCHLIST" in SUPPORTED_UNIVERSES
        assert "KOSPI_200" in SUPPORTED_UNIVERSES
        assert "SP500" in SUPPORTED_UNIVERSES
