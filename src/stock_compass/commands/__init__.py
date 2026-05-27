"""CLI 명령군 패키지 — 각 모듈 import 시 typer 데코레이터가 app에 등록됨.

`cli.py`에서 이 패키지를 import하기만 하면 모든 명령이 활성화된다.
"""

from stock_compass.commands import (  # noqa: F401 — 등록 트리거
    alerts,
    backfill,
    dashboard,
    news,
    report,
    scoring,
    screener,
    sentiment,
    trade,
    universe,
)
from stock_compass.commands._app import app

__all__ = ["app"]
