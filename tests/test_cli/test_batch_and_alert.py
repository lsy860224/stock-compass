"""batch-and-alert 명령의 시간대 분기 + --task 오버라이드."""

from __future__ import annotations

import pytest

from stock_compass.cli import _resolve_task


class TestExplicitTask:
    @pytest.mark.parametrize("task", ["us", "kr", "daily", "all"])
    def test_passthrough(self, task: str) -> None:
        # hour는 무관 — explicit task은 무조건 그대로
        assert _resolve_task(task, hour=0) == task
        assert _resolve_task(task.upper(), hour=12) == task

    def test_invalid_task_raises(self) -> None:
        import typer

        with pytest.raises(typer.BadParameter):
            _resolve_task("invalid", hour=10)


class TestAutoDispatch:
    @pytest.mark.parametrize("hour", [5, 6, 8])
    def test_morning_us_window(self, hour: int) -> None:
        # 05~06시(plist 06:30 직전), 08시(직후 관용)
        assert _resolve_task("auto", hour=hour) == "us"

    def test_07_is_daily(self) -> None:
        # plist 07:00 (daily 리포트)
        assert _resolve_task("auto", hour=7) == "daily"

    @pytest.mark.parametrize("hour", [15, 16, 17])
    def test_afternoon_kr_window(self, hour: int) -> None:
        # 15~17시 (plist 16:30 전후)
        assert _resolve_task("auto", hour=hour) == "kr"

    @pytest.mark.parametrize("hour", [0, 9, 12, 18, 22])
    def test_outside_window_falls_to_all(self, hour: int) -> None:
        assert _resolve_task("auto", hour=hour) == "all"
