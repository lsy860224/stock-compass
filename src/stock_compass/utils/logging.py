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

    rich_handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
        markup=True,
    )
    rich_handler.setLevel(level)

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

    root.handlers.clear()
    root.addHandler(rich_handler)
    root.addHandler(file_handler)
    root.setLevel(level)

    # 외부 라이브러리 노이즈 억제
    for noisy in ("urllib3", "yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    setattr(root, _SENTINEL_ATTR, True)
    return root


def get_logger(name: str) -> logging.Logger:
    """모듈용 로거. `setup_logging` 호출 전이어도 안전."""
    return logging.getLogger(name)
