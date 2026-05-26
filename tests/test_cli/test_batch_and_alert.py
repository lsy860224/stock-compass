"""batch-and-alert 명령의 시간대 + 요일 분기 + --task 오버라이드."""

from __future__ import annotations

import pytest

from stock_compass.commands.scoring import resolve_task as _resolve_task

# Mon=0 ... Fri=4, Sat=5, Sun=6
MON = 0
FRI = 4
SAT = 5
SUN = 6


class TestExplicitTask:
    @pytest.mark.parametrize("task", ["us", "kr", "daily", "all", "weekly"])
    def test_passthrough(self, task: str) -> None:
        # hour/weekday 무관 — explicit task은 무조건 그대로
        assert _resolve_task(task, hour=0, weekday=MON) == task
        assert _resolve_task(task.upper(), hour=12, weekday=SAT) == task

    def test_invalid_task_raises(self) -> None:
        import typer

        with pytest.raises(typer.BadParameter):
            _resolve_task("invalid", hour=10, weekday=MON)


class TestAutoDispatchWeekday:
    """평일 기준 — 시각만으로 결정."""

    @pytest.mark.parametrize("hour", [5, 6, 8])
    def test_morning_us_window(self, hour: int) -> None:
        # 05~06시(plist 06:30 직전), 08시(직후 관용)
        assert _resolve_task("auto", hour=hour, weekday=MON) == "us"

    def test_07_is_daily(self) -> None:
        assert _resolve_task("auto", hour=7, weekday=MON) == "daily"

    @pytest.mark.parametrize("hour", [15, 16, 17])
    def test_afternoon_kr_window(self, hour: int) -> None:
        assert _resolve_task("auto", hour=hour, weekday=FRI) == "kr"

    @pytest.mark.parametrize("hour", [0, 9, 12, 18, 22])
    def test_outside_window_falls_to_all(self, hour: int) -> None:
        assert _resolve_task("auto", hour=hour, weekday=MON) == "all"


class TestAutoDispatchWeekly:
    """토요일 08:00 → weekly. 다른 요일/시간 영향 X."""

    def test_saturday_08_is_weekly(self) -> None:
        assert _resolve_task("auto", hour=8, weekday=SAT) == "weekly"

    def test_saturday_other_hour_normal(self) -> None:
        # 토요일이지만 08시 아니면 일반 분기
        assert _resolve_task("auto", hour=7, weekday=SAT) == "daily"
        assert _resolve_task("auto", hour=15, weekday=SAT) == "kr"
        assert _resolve_task("auto", hour=12, weekday=SAT) == "all"

    def test_sunday_08_is_us_not_weekly(self) -> None:
        # 일요일 08시는 weekly 아님 (US 윈도우)
        assert _resolve_task("auto", hour=8, weekday=SUN) == "us"

    def test_weekday_08_is_us_not_weekly(self) -> None:
        assert _resolve_task("auto", hour=8, weekday=MON) == "us"
