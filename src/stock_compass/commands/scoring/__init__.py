"""score / batch / history / batch-and-alert — 점수화·이력·스케줄 통합.

typer 명령은 `_commands`에, launchd 자동화 오케스트레이션은 `scheduler`·`rescore`에,
결과 후처리(보고·헬스체크·차트)는 `reporting`에 있다. `_commands` import 시 모든
명령이 app에 등록된다. `resolve_task`는 하위 호환을 위해 패키지 레벨에서 re-export.
"""

from __future__ import annotations

from stock_compass.commands.scoring import _commands  # noqa: F401 — 등록 트리거
from stock_compass.commands.scoring.scheduler import resolve_task

__all__ = ["resolve_task"]
