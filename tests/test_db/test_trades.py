"""trades CRUD + 편향 분석 (get_performance_summary)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_compass.db import (
    Trade,
    get_performance_summary,
    get_trades,
    insert_trade,
    migrate,
    upsert_ticker,
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "test.db"
    migrate(db)
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    seed: list[tuple[str, str, str]] = [
        ("AAPL", "US", "Apple"),
        ("005930", "KR", "삼성전자"),
        ("TSLA", "US", "Tesla"),
    ]
    for code, market, name in seed:
        upsert_ticker(
            c,
            code=code,
            market=market,  # type: ignore[arg-type]
            name=name,
            sector=None,
            currency="KRW" if market == "KR" else "USD",
            yfinance_symbol=code if market == "US" else f"{code}.KS",
        )
    return c


class TestInsertTrade:
    def test_basic(self, conn: sqlite3.Connection) -> None:
        t = insert_trade(
            conn,
            ticker="AAPL",
            market="US",
            side="buy",
            price=150.0,
            qty=10,
            reason="분할 1차",
            tag="planned",
            score_at_trade=65.0,
        )
        assert isinstance(t, Trade)
        assert t.id > 0
        assert t.score_at_trade == 65.0
        assert t.tag == "planned"

    def test_unknown_ticker_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="미등록"):
            insert_trade(
                conn,
                ticker="NOPE",
                market="US",
                side="buy",
                price=10.0,
                qty=1,
            )

    def test_score_auto_lookup(self, conn: sqlite3.Connection) -> None:
        # composite_scores 시드
        conn.execute(
            "INSERT INTO composite_scores (ticker_id, date, total_score, verdict)"
            " VALUES ((SELECT id FROM tickers WHERE code='AAPL' AND market='US'),"
            " '2026-05-21', 72.5, '관심권')"
        )
        t = insert_trade(
            conn, ticker="AAPL", market="US", side="buy", price=200, qty=1
        )
        assert t.score_at_trade == 72.5

    def test_score_none_when_no_composite(self, conn: sqlite3.Connection) -> None:
        t = insert_trade(
            conn, ticker="TSLA", market="US", side="sell", price=300, qty=2
        )
        assert t.score_at_trade is None


class TestGetTrades:
    def _seed(
        self, conn: sqlite3.Connection, ticker: str, market: str, days_ago: int
    ) -> Trade:
        ts = datetime.now(UTC) - timedelta(days=days_ago)
        return insert_trade(
            conn,
            ticker=ticker,
            market=market,  # type: ignore[arg-type]
            side="buy",
            price=100.0,
            qty=1,
            executed_at=ts,
            score_at_trade=50.0,
        )

    def test_orders_by_recent(self, conn: sqlite3.Connection) -> None:
        self._seed(conn, "AAPL", "US", days_ago=5)
        self._seed(conn, "TSLA", "US", days_ago=1)
        self._seed(conn, "005930", "KR", days_ago=3)
        trades = get_trades(conn)
        assert [t.ticker for t in trades] == ["TSLA", "005930", "AAPL"]

    def test_days_filter(self, conn: sqlite3.Connection) -> None:
        self._seed(conn, "AAPL", "US", days_ago=40)
        self._seed(conn, "TSLA", "US", days_ago=5)
        recent = get_trades(conn, days=10)
        assert [t.ticker for t in recent] == ["TSLA"]

    def test_ticker_filter(self, conn: sqlite3.Connection) -> None:
        self._seed(conn, "AAPL", "US", days_ago=1)
        self._seed(conn, "TSLA", "US", days_ago=1)
        only_aapl = get_trades(conn, ticker="AAPL")
        assert len(only_aapl) == 1
        assert only_aapl[0].ticker == "AAPL"

    def test_market_filter(self, conn: sqlite3.Connection) -> None:
        self._seed(conn, "AAPL", "US", days_ago=1)
        self._seed(conn, "005930", "KR", days_ago=1)
        kr = get_trades(conn, market="KR")
        assert [t.ticker for t in kr] == ["005930"]


class TestPerformanceSummary:
    def test_empty(self, conn: sqlite3.Connection) -> None:
        s = get_performance_summary(conn)
        assert s["total"] == 0

    def test_bias_metrics(self, conn: sqlite3.Connection) -> None:
        # 매수 5건: 점수 80/75/60/50/30 → 평균 59
        for sc in [80, 75, 60, 50, 30]:
            insert_trade(
                conn,
                ticker="AAPL",
                market="US",
                side="buy",
                price=100,
                qty=1,
                score_at_trade=float(sc),
                tag="planned" if sc >= 60 else "impulse",
            )
        # 매도 2건: 점수 40/20 → 평균 30
        for sc in [40, 20]:
            insert_trade(
                conn,
                ticker="AAPL",
                market="US",
                side="sell",
                price=200,
                qty=1,
                score_at_trade=float(sc),
            )

        s = get_performance_summary(conn)
        assert s["total"] == 7

        buy = s["by_side"]["buy"]
        assert buy["count"] == 5
        assert buy["avg_score"] == 59.0
        # 80, 75 → 2/5 = 40% high zone
        assert buy["high_zone_pct"] == 40.0
        # 30 → 1/5 = 20% low zone
        assert buy["low_zone_pct"] == 20.0

        sell = s["by_side"]["sell"]
        assert sell["count"] == 2
        assert sell["avg_score"] == 30.0

        assert "planned" in s["by_tag"]
        assert "impulse" in s["by_tag"]
        assert s["by_tag"]["planned"]["count"] == 3  # 80/75/60
        assert s["by_tag"]["impulse"]["count"] == 2  # 50/30

    def test_only_trades_with_score_counted(self, conn: sqlite3.Connection) -> None:
        insert_trade(
            conn, ticker="AAPL", market="US", side="buy",
            price=100, qty=1, score_at_trade=70.0,
        )
        insert_trade(
            conn, ticker="TSLA", market="US", side="buy",
            price=100, qty=1, score_at_trade=None,
        )
        s = get_performance_summary(conn)
        assert s["total"] == 1
        assert s["by_side"]["buy"]["count"] == 1
