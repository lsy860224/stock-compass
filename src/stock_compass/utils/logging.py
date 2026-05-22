"""로깅 설정 — rich 콘솔 출력 + 파일 로테이션 (10MB)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

_DEFAULT_LOG_NAME = "stock-compass.log"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_SENTINEL_ATTR = "_stock_compass_configured"


class _ValidMessageFilter(logging.Filter):
    """비정상 형식의 LogRecord(예: pykrx의 `logging.info(tuple, dict)`)를 차단.

    RichHandler.emit은 format 예외를 catch하지 않아서 호출자까지 전파됨 — 사전 차단.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.getMessage()
        except (TypeError, ValueError):
            return False
        return True


def setup_logging(
    log_dir: Path,
    *,
    level: int = logging.INFO,
    filename: str = _DEFAULT_LOG_NAME,
) -> logging.Logger:
    """루트 로거를 rich + 파일 핸들러로 설정. 멱등."""
    root = logging.getLogger()
    if getattr(root, _SENTINEL_ATTR, False):
        return root

    log_dir.mkdir(parents=True, exist_ok=True)

    valid_filter = _ValidMessageFilter()

    rich_handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
        markup=True,
    )
    rich_handler.setLevel(level)
    rich_handler.addFilter(valid_filter)

    file_handler = RotatingFileHandler(
        log_dir / filename,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    file_handler.addFilter(valid_filter)

    root.handlers.clear()
    root.addHandler(rich_handler)
    root.addHandler(file_handler)
    root.setLevel(level)

    # 외부 라이브러리 노이즈 억제
    for noisy in ("urllib3", "yfinance", "peewee", "pykrx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    setattr(root, _SENTINEL_ATTR, True)
    return root


def get_logger(name: str) -> logging.Logger:
    """모듈용 로거. `setup_logging` 호출 전이어도 안전."""
    return logging.getLogger(name)
