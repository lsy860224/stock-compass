"""get_trade_hindsight + summarize_hindsight — 매매 후 forward return."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from stock_compass.db import (
    get_trade_hindsight,
    insert_trade,
    migrate,
    summarize_hindsight,
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
    upsert_ticker(
        c,
        code="AAPL",
        market="US",
        name="Apple",
        sector="Tech",
        currency="USD",
        yfinance_symbol="AAPL",
    )
    return c


def _seed_forward_price(
    conn: sqlite3.Connection, ticker: str, on_date: date, price: float, score: float = 60.0
) -> None:
    """forward 가격 lookup용 composite_scores 시드."""
    factors = [
        FactorScore(name=n, score=score, weight=DEFAULT_WEIGHTS[n])
        for n in DEFAULT_WEIGHTS
    ]
    cs = CompositeScore(
        ticker=ticker,
        market="US",
        total_score=score,
        verdict="중립",
        factors=factors,
        computed_at=datetime.now(UTC),
        price_at_score=price,
        currency="USD",
        name="Apple",
        sector="Tech",
        yfinance_symbol=ticker,
    )
    upsert_composite_score(conn, cs, on_date=on_date)


class TestGetTradeHindsight:
    def test_no_trades_returns_empty(self, conn: sqlite3.Connection) -> None:
        assert get_trade_hindsight(conn) == []

    def test_forward_price_lookup(self, conn: sqlite3.Connection) -> None:
        # 매수 가격 100, 30일 후 가격 110 → +10% return
        executed = datetime(2024, 1, 1, tzinfo=UTC)
        insert_trade(
            conn,
            ticker="AAPL",
            market="US",
            side="buy",
            price=100.0,
            qty=10,
            executed_at=executed,
            score_at_trade=75.0,
        )
        _seed_forward_price(conn, "AAPL", date(2024, 1, 31), price=110.0)

        rows = get_trade_hindsight(conn, days=3650, forward_days=(30,))
        assert len(rows) == 1
        assert rows[0].forward_returns["30d"] == pytest.approx(0.10)
        assert rows[0].forward_prices["30d"] == 110.0

    def test_missing_forward_returns_none(self, conn: sqlite3.Connection) -> None:
        insert_trade(
            conn,
            ticker="AAPL",
            market="US",
            side="buy",
            price=100.0,
            qty=10,
            executed_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        # forward 가격 시드 없음
        rows = get_trade_hindsight(conn, days=3650, forward_days=(30, 90))
        assert rows[0].forward_returns == {"30d": None, "90d": None}

    def test_sell_forward_return_calculated(
        self, conn: sqlite3.Connection
    ) -> None:
        # 매도 가격 100, 30일 후 가격 95 → return -5% (매도 후 떨어짐 = 잘 판 것)
        insert_trade(
            conn,
            ticker="AAPL",
            market="US",
            side="sell",
            price=100.0,
            qty=10,
            executed_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        _seed_forward_price(conn, "AAPL", date(2024, 1, 31), price=95.0)
        rows = get_trade_hindsight(conn, days=3650, forward_days=(30,))
        assert rows[0].forward_returns["30d"] == pytest.approx(-0.05)

    def test_takes_nearest_forward_date(
        self, conn: sqlite3.Connection
    ) -> None:
        # target=1/31, 가격이 1/31엔 없고 2/2에 있으면 2/2 사용
        insert_trade(
            conn,
            ticker="AAPL",
            market="US",
            side="buy",
            price=100.0,
            qty=10,
            executed_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        _seed_forward_price(conn, "AAPL", date(2024, 2, 2), price=105.0)
        rows = get_trade_hindsight(conn, days=3650, forward_days=(30,))
        assert rows[0].forward_returns["30d"] == pytest.approx(0.05)


class TestSummarizeHindsight:
    def _mk_trades(
        self, conn: sqlite3.Connection
    ) -> None:
        """3 buys + 1 sell 시드. 각 forward 가격도 함께 시드."""
        scenarios = [
            # (side, score, entry, fwd_price)
            ("buy", 80.0, 100.0, 110.0),  # 관심권 매수 +10%
            ("buy", 75.0, 100.0, 105.0),  # 관심권 매수 +5%
            ("buy", 40.0, 100.0, 90.0),   # 주의 매수 -10%
            ("sell", 50.0, 100.0, 95.0),  # 매도 후 -5% (잘 판 것)
        ]
        for i, (side, score, entry, fwd) in enumerate(scenarios):
            executed = datetime(2024, 1, 1 + i, tzinfo=UTC)
            insert_trade(
                conn,
                ticker="AAPL",
                market="US",
                side=side,  # type: ignore[arg-type]
                price=entry,
                qty=10,
                executed_at=executed,
                score_at_trade=score,
            )
            _seed_forward_price(conn, "AAPL", executed.date() + timedelta(days=30), price=fwd)

    def test_by_side_buy_stats(self, conn: sqlite3.Connection) -> None:
        self._mk_trades(conn)
        rows = get_trade_hindsight(conn, days=3650, forward_days=(30,))
        s = summarize_hindsight(rows, forward_days=(30,))
        buy = s["by_side"]["buy"]["30d"]
        assert buy["count"] == 3
        # avg (+0.10 + 0.05 - 0.10) / 3 ≈ 0.0167
        assert buy["avg"] == pytest.approx((0.10 + 0.05 - 0.10) / 3, abs=1e-4)
        # 양수 2개 / 3 = 66.7%
        assert buy["hit_rate"] == pytest.approx(2 / 3, abs=1e-4)

    def test_by_side_sell_hit_means_price_dropped(
        self, conn: sqlite3.Connection
    ) -> None:
        self._mk_trades(conn)
        rows = get_trade_hindsight(conn, days=3650, forward_days=(30,))
        s = summarize_hindsight(rows, forward_days=(30,))
        sell = s["by_side"]["sell"]["30d"]
        # 1건 매도, 가격 -5% → hit (잘 판 것)
        assert sell["count"] == 1
        assert sell["hit_rate"] == 1.0

    def test_by_zone_separates_score(self, conn: sqlite3.Connection) -> None:
        self._mk_trades(conn)
        rows = get_trade_hindsight(conn, days=3650, forward_days=(30,))
        s = summarize_hindsight(rows, forward_days=(30,))
        # 관심권 2건 (80, 75 점수), 주의 1건 (40), 중립/no_score 0
        high = s["by_zone"]["high_70_plus"]["30d"]
        low = s["by_zone"]["low_below_50"]["30d"]
        assert high["count"] == 2
        assert low["count"] == 1
        # 관심권 평균 (+10 + 5) / 2 = +7.5%
        assert high["avg"] == pytest.approx(0.075, abs=1e-4)
        # 주의 zone: -10%
        assert low["avg"] == pytest.approx(-0.10, abs=1e-4)


class TestCli:
    def test_hindsight_help_lists_options(self) -> None:
        from typer.testing import CliRunner

        from stock_compass.commands._app import app

        runner = CliRunner()
        r = runner.invoke(app, ["trade", "hindsight", "--help"])
        assert r.exit_code == 0
        for opt in ("--days", "--forward", "--ticker"):
            assert opt in r.stdout
