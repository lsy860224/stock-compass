"""AlertManager — dedup 동작 + end-to-end (DB + 가짜 notify)."""

from __future__ import annotations

import sqlite3
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path

import pytest

from stock_compass.alerts import (
    AlertManager,
    DailyTrigger,
    DeltaTrigger,
    ThresholdTrigger,
)
from stock_compass.db import (
    AlertRow,
    get_recent_alerts,
    migrate,
    record_alert,
    upsert_composite_score,
)

from .conftest import make_score


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "test.db"
    migrate(db)
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


@pytest.fixture
def mock_notify() -> list[dict[str, str]]:
    """`AlertManager._notify_fn` 자리에 주입할 호출 기록기."""
    return []


def make_manager(notify_log: list[dict[str, str]]) -> AlertManager:
    def _stub(*, title: str, body: str, subtitle: str | None = None) -> bool:
        notify_log.append({"title": title, "body": body, "subtitle": subtitle or ""})
        return True

    return AlertManager(
        per_ticker_triggers=[ThresholdTrigger(), DeltaTrigger()],
        global_triggers=[DailyTrigger()],
        notify_fn=_stub,
    )


def _seed(conn: sqlite3.Connection, ticker: str, total: float, on_date: date_cls) -> None:
    upsert_composite_score(conn, make_score(ticker, total=total), on_date=on_date)


class TestEndToEnd:
    def test_no_snapshots_returns_empty(
        self, conn: sqlite3.Connection, mock_notify: list[dict[str, str]]
    ) -> None:
        result = make_manager(mock_notify).run(conn, on_date=date_cls(2026, 5, 22))
        assert result == []
        assert mock_notify == []

    def test_threshold_buy_entry_fires(
        self, conn: sqlite3.Connection, mock_notify: list[dict[str, str]]
    ) -> None:
        d = date_cls(2026, 5, 22)
        prev = d - timedelta(days=1)
        _seed(conn, "AAPL", 65, prev)  # 어제: 65
        _seed(conn, "AAPL", 82, d)  # 오늘: 82 → 진입

        result = make_manager(mock_notify).run(conn, on_date=d)
        triggers_fired = {f.alert.trigger_type for f in result if f.delivered}
        assert "threshold_buy" in triggers_fired
        # daily도 함께 발화
        assert "daily" in triggers_fired
        assert len(mock_notify) >= 1
        rows = get_recent_alerts(conn)
        assert any(r["trigger_type"] == "threshold_buy" for r in rows)

    def test_dedup_blocks_repeat(
        self, conn: sqlite3.Connection, mock_notify: list[dict[str, str]]
    ) -> None:
        d = date_cls(2026, 5, 22)
        prev = d - timedelta(days=1)
        _seed(conn, "AAPL", 65, prev)
        _seed(conn, "AAPL", 82, d)

        m = make_manager(mock_notify)
        first = m.run(conn, on_date=d)
        second = m.run(conn, on_date=d)

        assert any(f.delivered for f in first)
        # 두 번째는 모두 dedup 처리되어 delivered=False
        assert any(f.deduplicated for f in second)
        assert not any(f.delivered for f in second)

    def test_dry_run_no_notify_no_record(
        self, conn: sqlite3.Connection, mock_notify: list[dict[str, str]]
    ) -> None:
        d = date_cls(2026, 5, 22)
        _seed(conn, "AAPL", 90, d)

        result = make_manager(mock_notify).run(conn, on_date=d, dry_run=True)
        assert any(f.alert.trigger_type == "threshold_buy" for f in result)
        assert mock_notify == []
        assert get_recent_alerts(conn) == []

    def test_delta_alert(
        self, conn: sqlite3.Connection, mock_notify: list[dict[str, str]]
    ) -> None:
        d = date_cls(2026, 5, 22)
        prev = d - timedelta(days=1)
        _seed(conn, "NVDA", 55, prev)
        _seed(conn, "NVDA", 75, d)  # +20

        result = make_manager(mock_notify).run(conn, on_date=d)
        assert any(
            f.alert.trigger_type == "delta_up" and f.delivered for f in result
        )

    def test_daily_dedup_separate_from_per_ticker(
        self, conn: sqlite3.Connection, mock_notify: list[dict[str, str]]
    ) -> None:
        d = date_cls(2026, 5, 22)
        _seed(conn, "AAPL", 70, d)

        m = make_manager(mock_notify)
        first = m.run(conn, on_date=d)
        assert any(f.alert.trigger_type == "daily" and f.delivered for f in first)

        # daily는 calendar day 기준 dedup이라 같은 날 재실행 시 차단
        second = m.run(conn, on_date=d)
        daily_second = [f for f in second if f.alert.trigger_type == "daily"]
        assert daily_second and all(f.deduplicated for f in daily_second)


class TestRepositoryDedupHelpers:
    def test_has_recent_alert(self, conn: sqlite3.Connection) -> None:
        from stock_compass.db import has_recent_alert, upsert_ticker
        from stock_compass.utils.dates import now_utc, to_iso_utc

        tid = upsert_ticker(
            conn,
            code="AAPL",
            market="US",
            name="Apple",
            sector=None,
            currency="USD",
            yfinance_symbol="AAPL",
        )
        assert has_recent_alert(conn, tid, "threshold_buy") is False
        record_alert(
            conn,
            AlertRow(
                ticker_id=tid,
                trigger_type="threshold_buy",
                score_before=70.0,
                score_after=82.0,
                message="x",
                delivered_via="macos_notify",
                fired_at=to_iso_utc(now_utc()),
            ),
        )
        assert has_recent_alert(conn, tid, "threshold_buy") is True
        # 다른 trigger는 별도 카운트
        assert has_recent_alert(conn, tid, "delta_up") is False
