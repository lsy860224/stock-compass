"""SQLite DB 자동 백업 — `daily` 잡 직후 호출.

CLAUDE.md 6) Phase 5 "매일 자동 `data/backups/stock_compass-YYYYMMDD.db`" 약속.
SQLite의 online backup API(`conn.backup`) 사용 — 운영 중 안전.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path

from stock_compass.config import settings
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

# 기본 보존 30일 — config 노출은 추후
DEFAULT_RETENTION_DAYS = 30


def backup_database(
    *,
    on_date: date_cls | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> Path | None:
    """DB를 `data/backups/stock_compass-YYYYMMDD.db`로 복사 + 오래된 백업 정리.

    DB 파일 없으면 None 반환 (cold-start). 같은 날짜 백업이 있으면 덮어씀.
    """
    import sqlite3

    db_path = settings.db_path
    if not db_path.exists():
        _logger.info("DB 없음 — 백업 생략: %s", db_path)
        return None

    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (on_date or date_cls.today()).strftime("%Y%m%d")
    dest = backup_dir / f"stock_compass-{stamp}.db"

    # SQLite online backup — write lock 잠깐 잡지만 안전
    with sqlite3.connect(db_path) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
    _logger.info("DB 백업 완료: %s (%.1f KB)", dest.name, dest.stat().st_size / 1024)

    _prune_old_backups(backup_dir, retention_days=retention_days)
    return dest


def _prune_old_backups(backup_dir: Path, *, retention_days: int) -> int:
    """`retention_days` 이전 백업 자동 삭제. 삭제 개수 반환."""
    cutoff = datetime.now() - timedelta(days=retention_days)
    deleted = 0
    for old in backup_dir.glob("stock_compass-*.db"):
        try:
            mtime = datetime.fromtimestamp(old.stat().st_mtime)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                old.unlink()
                deleted += 1
                _logger.info("오래된 백업 삭제: %s", old.name)
            except OSError as e:
                _logger.warning("백업 삭제 실패: %s (%s)", old.name, e)
    return deleted
