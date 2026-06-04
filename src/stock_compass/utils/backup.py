"""SQLite DB 자동 백업 — `daily` 잡 직후 호출.

CLAUDE.md 6) Phase 5 "매일 자동 `data/backups/stock_compass-YYYYMMDD.db`" 약속.
SQLite의 online backup API(`conn.backup`) 사용 — 운영 중 안전.
"""

from __future__ import annotations

from datetime import date as date_cls
from pathlib import Path

from stock_compass.config import settings
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)


def backup_database(
    *,
    on_date: date_cls | None = None,
    retention_count: int | None = None,
) -> Path | None:
    """DB를 `data/backups/stock_compass-YYYYMMDD.db`로 복사 + 오래된 백업 정리.

    DB 파일 없으면 None 반환 (cold-start). 같은 날짜 백업이 있으면 덮어씀.
    DB가 700MB+ 라 시간이 아닌 **개수 기반** 보존 (기본 backup_retention_count).
    """
    import sqlite3

    keep = (
        retention_count
        if retention_count is not None
        else settings.backup_retention_count
    )

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
    _logger.info(
        "DB 백업 완료: %s (%.1f MB)", dest.name, dest.stat().st_size / 1024 / 1024
    )

    _prune_backups_by_count(backup_dir, keep=keep)
    return dest


def _prune_backups_by_count(backup_dir: Path, *, keep: int) -> int:
    """파일명(날짜) 기준 최신 `keep`개만 유지하고 나머지 삭제. 삭제 개수 반환."""
    if keep < 1:
        return 0
    backups = sorted(
        backup_dir.glob("stock_compass-*.db"), key=lambda p: p.name, reverse=True
    )
    deleted = 0
    for old in backups[keep:]:
        try:
            old.unlink()
            deleted += 1
            _logger.info("오래된 백업 삭제: %s", old.name)
        except OSError as e:
            _logger.warning("백업 삭제 실패: %s (%s)", old.name, e)
    return deleted
