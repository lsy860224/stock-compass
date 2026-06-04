"""screen / discover / weekly-discover / backtest — SQL 스크리너 + 발굴 + 백테스트.

각 명령은 전용 모듈에 있고, import 시 typer 데코레이터가 app에 등록된다.
`screener_rows_to_targets`는 하위 호환을 위해 패키지 레벨에서 re-export한다.
"""

from __future__ import annotations

from stock_compass.commands.screener import (  # noqa: F401 — 등록 트리거
    backtest,
    discover,
    screen,
    weekly,
)
from stock_compass.commands.screener._common import screener_rows_to_targets

__all__ = ["screener_rows_to_targets"]
