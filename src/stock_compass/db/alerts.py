"""alerts 테이블 — 3종 트리거 발화 이력 + 중복 방지."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Any

from stock_compass.utils.dates import today_kst


@dataclass(frozen=True, slots=True)
class AlertRow:
    ticker_id: int
    trigger_type: str
    score_before: float | None
    score_after: float
    message: str
    delivered_via: str
    fired_at: str  # ISO UTC


def has_recent_alert(
    conn: sqlite3.Connection,
    ticker_id: int,
    trigger_type: str,
    *,
    hours: int = 24,
) -> bool:
    """동일 종목·동일 trigger가 N시간 내 발화된 적 있는지."""
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE ticker_id = ? AND trigger_type = ?
          AND fired_at > datetime('now', ?)
        LIMIT 1
        """,
        (ticker_id, trigger_type, f"-{hours} hours"),
    ).fetchone()
    return row is not None


def has_daily_alert_today(
    conn: sqlite3.Connection, *, on_date: date_cls | None = None
) -> bool:
    """일일 리포트 알림이 오늘(KST 기준) 이미 발화됐는지."""
    d = (on_date or today_kst()).isoformat()
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE trigger_type = 'daily' AND DATE(fired_at, 'localtime') = ?
        LIMIT 1
        """,
        (d,),
    ).fetchone()
    return row is not None


def record_alert(conn: sqlite3.Connection, alert: AlertRow) -> int:
    """alerts 테이블에 한 행 추가. 발화 이력 영구 보존."""
    cur = conn.execute(
        """
        INSERT INTO alerts
          (ticker_id, trigger_type, score_before, score_after,
           message, delivered_via, fired_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert.ticker_id,
            alert.trigger_type,
            alert.score_before,
            alert.score_after,
            alert.message,
            alert.delivered_via,
            alert.fired_at,
        ),
    )
    return int(cur.lastrowid or 0)


def get_recent_alerts(
    conn: sqlite3.Connection, *, hours: int = 24
) -> list[dict[str, Any]]:
    """최근 N시간 발화된 알림 (목록·디버그용)."""
    rows = conn.execute(
        """
        SELECT a.id, a.trigger_type, a.score_before, a.score_after, a.message,
               a.delivered_via, a.fired_at,
               t.code, t.market, t.name
        FROM alerts a
        JOIN tickers t ON a.ticker_id = t.id
        WHERE a.fired_at > datetime('now', ?)
        ORDER BY a.fired_at DESC
        """,
        (f"-{hours} hours",),
    ).fetchall()
    return [dict(r) for r in rows]
