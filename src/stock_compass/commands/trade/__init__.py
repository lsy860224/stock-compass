"""trade 서브그룹 — add / list / analyze / hindsight.

각 명령은 전용 모듈에 있고, import 시 trade_app에 데코레이터가 등록된다.
"""

from __future__ import annotations

from stock_compass.commands.trade import (  # noqa: F401 — 등록 트리거
    analyze,
    entry,
    hindsight,
)
