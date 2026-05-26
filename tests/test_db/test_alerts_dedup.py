"""alerts dedup 회귀 — Python cutoff binding이 ISO+TZ 정확히 비교하는지 검증.

이전 버그: `datetime('now', '-24 hours')`는 naive `'YYYY-MM-DD HH:MM:SS'` 반환.
fired_at은 `to_iso_utc(...)` = `'YYYY-MM-DDTHH:MM:SS+00:00'`. lexical 비교에서
'T'(84) > ' '(32)라 dedup 창이 ~1-2h 길어졌었다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_compass.db import migrate, upsert_ticker
from stock_compass.db.alerts import (
    AlertRow,
    has_daily_alert_today,
    has_recent_alert,
    record_alert,
)
from stock_compass.utils.dates import KST, to_iso_utc, today_kst


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "test.db"
    migrate(db)
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


@pytest.fixture
def ticker_id(conn: sqlite3.Connection) -> int:
    return upsert_ticker(
        conn,
        code="AAPL",
        market="US",
        name="Apple Inc.",
        sector=None,
        currency="USD",
        yfinance_symbol="AAPL",
    )


def _seed_alert(conn: sqlite3.Connection, ticker_id: int, fired_at: datetime) -> None:
    record_alert(
        conn,
        AlertRow(
            ticker_id=ticker_id,
            trigger_type="threshold_buy",
            score_before=60.0,
            score_after=80.0,
            message="test",
            delivered_via="notify",
            fired_at=to_iso_utc(fired_at),
        ),
    )


class TestHasRecentAlert:
    def test_inside_24h_detected(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        _seed_alert(conn, ticker_id, datetime.now(UTC) - timedelta(hours=23))
        assert has_recent_alert(conn, ticker_id, "threshold_buy", hours=24)

    def test_outside_24h_not_detected(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        _seed_alert(conn, ticker_id, datetime.now(UTC) - timedelta(hours=25))
        assert not has_recent_alert(conn, ticker_id, "threshold_buy", hours=24)

    def test_iso_tz_format_compared_correctly(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        # 정확히 24h 직전 + 1초 → dedup 안 됨
        _seed_alert(
            conn, ticker_id, datetime.now(UTC) - timedelta(hours=24, seconds=1)
        )
        assert not has_recent_alert(conn, ticker_id, "threshold_buy", hours=24)

    def test_other_trigger_ignored(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        _seed_alert(conn, ticker_id, datetime.now(UTC) - timedelta(hours=1))
        # 다른 trigger_type은 별도 dedup
        assert not has_recent_alert(conn, ticker_id, "delta_up", hours=24)


class TestHasDailyAlertToday:
    def test_kst_today_detected(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        # KST 오늘 00:30 발화 → UTC 환산 후 today_kst() 범위 안에 들어가야
        kst_today = today_kst()
        kst_dt = datetime.combine(
            kst_today, datetime.min.time().replace(hour=0, minute=30), tzinfo=KST
        )
        record_alert(
            conn,
            AlertRow(
                ticker_id=ticker_id,
                trigger_type="daily",
                score_before=None,
                score_after=70.0,
                message="daily",
                delivered_via="craft",
                fired_at=to_iso_utc(kst_dt),
            ),
        )
        assert has_daily_alert_today(conn)

    def test_kst_yesterday_not_detected(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        kst_yesterday = today_kst() - timedelta(days=1)
        kst_dt = datetime.combine(
            kst_yesterday,
            datetime.min.time().replace(hour=23, minute=59),
            tzinfo=KST,
        )
        record_alert(
            conn,
            AlertRow(
                ticker_id=ticker_id,
                trigger_type="daily",
                score_before=None,
                score_after=70.0,
                message="daily",
                delivered_via="craft",
                fired_at=to_iso_utc(kst_dt),
            ),
        )
        assert not has_daily_alert_today(conn)

    def test_no_alerts_empty_db(self, conn: sqlite3.Connection) -> None:
        assert not has_daily_alert_today(conn)
