"""마이그레이션 실행기 — schema_version 추적 + 순차 적용."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from stock_compass.db.schema import (
    MIGRATION_001_INITIAL,
    MIGRATION_002_HYBRID_SENTIMENT,
)
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS: Sequence[Migration] = (
    Migration(version=1, name="initial", sql=MIGRATION_001_INITIAL),
    Migration(version=2, name="hybrid_sentiment", sql=MIGRATION_002_HYBRID_SENTIMENT),
)


def _current_version(conn: sqlite3.Connection) -> int:
    """schema_version 테이블이 없으면 0. 있으면 MAX(version)."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0)


def migrate(db_path: Path) -> int:
    """누락된 마이그레이션 순차 적용. 적용된 마지막 버전 반환."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        current = _current_version(conn)
        target = MIGRATIONS[-1].version
        if current >= target:
            return current
        for m in MIGRATIONS:
            if m.version <= current:
                continue
            _logger.info("DB 마이그레이션 적용: v%d (%s)", m.version, m.name)
            conn.executescript(m.sql)
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (m.version,)
            )
            conn.commit()
        return target
