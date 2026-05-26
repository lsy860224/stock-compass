"""SQLite 연결 컨텍스트 — 마이그레이션 자동 적용 + Row factory."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from stock_compass.config import settings
from stock_compass.db.migrations import migrate


@contextmanager
def get_db_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """SQLite 연결. PRAGMA foreign_keys ON + Row factory. 처음 호출 시 자동 마이그레이션."""
    path = db_path or settings.db_path
    migrate(path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
