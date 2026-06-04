"""DB 백업 — online backup + 30일 보존 정책."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from stock_compass.db import migrate, upsert_ticker
from stock_compass.utils import backup as backup_mod


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "stock_compass.db"
    migrate(db)
    # 데이터 한 줄 — backup 검증용
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    upsert_ticker(
        c,
        code="AAPL",
        market="US",
        name="Apple",
        sector="Tech",
        currency="USD",
        yfinance_symbol="AAPL",
    )
    c.close()
    from stock_compass.config import settings

    monkeypatch.setattr(settings, "db_path", db)
    return db


class TestBackup:
    def test_creates_backup_with_data(self, db_path: Path) -> None:
        on_date = date(2026, 5, 27)
        backup = backup_mod.backup_database(on_date=on_date)
        assert backup is not None
        assert backup.name == "stock_compass-20260527.db"
        assert backup.exists()
        # 백업본에도 데이터가 있어야 (단순 cp가 아니라 sqlite3.backup 검증)
        with sqlite3.connect(backup) as c:
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT code FROM tickers WHERE code = 'AAPL'").fetchone()
            assert row is not None
            assert row["code"] == "AAPL"

    def test_returns_none_when_no_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.config import settings

        monkeypatch.setattr(settings, "db_path", tmp_path / "nope.db")
        assert backup_mod.backup_database() is None

    def test_prunes_beyond_retention_count(self, db_path: Path) -> None:
        """개수 기반 보존 — 파일명(날짜) 최신 N개만 유지, 오래된 백업 삭제.

        DB가 700MB+ 라 시간이 아닌 개수 기반 보존 (backup.py 설계).
        """
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        # 기존 백업 3개 (날짜 오름차순 이름)
        for stamp in ("20260101", "20260102", "20260103"):
            (backup_dir / f"stock_compass-{stamp}.db").write_bytes(b"old")

        # 새 백업 실행 (오늘 날짜) + 최신 2개만 유지 → 신규 백업도 count에 포함
        backup_mod.backup_database(on_date=date(2026, 5, 27), retention_count=2)

        remaining = sorted(p.name for p in backup_dir.glob("stock_compass-*.db"))
        # 신규(20260527) + 직전 최신(20260103)만 남고 나머지 삭제
        assert remaining == [
            "stock_compass-20260103.db",
            "stock_compass-20260527.db",
        ]

    def test_retention_preserves_within_count(self, db_path: Path) -> None:
        """보존 개수 이내 백업은 유지."""
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        recent = backup_dir / "stock_compass-20260520.db"
        recent.write_bytes(b"recent")

        # 새 백업 + 5개 보존 → 총 2개(20260520, 20260527)는 count 이내라 보존
        backup_mod.backup_database(on_date=date(2026, 5, 27), retention_count=5)
        assert recent.exists()

    def test_same_day_overwrites(self, db_path: Path) -> None:
        on_date = date(2026, 5, 27)
        backup_mod.backup_database(on_date=on_date)
        size1 = (db_path.parent / "backups" / "stock_compass-20260527.db").stat().st_size
        # 같은 날짜 재실행 — 덮어쓰기, 에러 X
        backup_mod.backup_database(on_date=on_date)
        size2 = (db_path.parent / "backups" / "stock_compass-20260527.db").stat().st_size
        assert size1 == size2  # 동일 내용
