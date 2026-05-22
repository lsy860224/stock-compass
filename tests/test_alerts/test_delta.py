"""DeltaTrigger — 경계값 14/15, up/down 방향, prev 없음."""

from __future__ import annotations

import pytest

from stock_compass.alerts.delta import DeltaTrigger

from .conftest import make_score


@pytest.fixture
def trigger() -> DeltaTrigger:
    return DeltaTrigger()


class TestNoPrev:
    def test_no_previous_no_alert(self, trigger: DeltaTrigger) -> None:
        assert trigger.check(make_score(total=80.0), previous=None) is None


class TestDeltaUp:
    def test_below_threshold_no_alert(self, trigger: DeltaTrigger) -> None:
        # 50 → 64 (delta=14) → 발화 X
        assert (
            trigger.check(make_score(total=64.0), previous=make_score(total=50.0))
            is None
        )

    def test_at_threshold_fires(self, trigger: DeltaTrigger) -> None:
        # 50 → 65 (delta=15) → 발화
        a = trigger.check(make_score(total=65.0), previous=make_score(total=50.0))
        assert a is not None
        assert a.trigger_type == "delta_up"
        assert a.score_before == 50.0
        assert a.score_after == 65.0

    def test_above_threshold_fires(self, trigger: DeltaTrigger) -> None:
        a = trigger.check(make_score(total=75.0), previous=make_score(total=50.0))
        assert a is not None
        assert a.trigger_type == "delta_up"


class TestDeltaDown:
    def test_below_threshold_no_alert(self, trigger: DeltaTrigger) -> None:
        # 70 → 56 (delta=-14) → 발화 X
        assert (
            trigger.check(make_score(total=56.0), previous=make_score(total=70.0))
            is None
        )

    def test_at_threshold_fires(self, trigger: DeltaTrigger) -> None:
        a = trigger.check(make_score(total=55.0), previous=make_score(total=70.0))
        assert a is not None
        assert a.trigger_type == "delta_down"

    def test_large_drop(self, trigger: DeltaTrigger) -> None:
        a = trigger.check(make_score(total=20.0), previous=make_score(total=80.0))
        assert a is not None
        assert a.trigger_type == "delta_down"
        assert "60" in a.body  # ↓60


class TestMessage:
    def test_up_arrow(self, trigger: DeltaTrigger) -> None:
        a = trigger.check(make_score("NVDA", 80), previous=make_score(total=60))
        assert a is not None
        assert "↑" in a.body
        assert "급등" in a.title

    def test_down_arrow(self, trigger: DeltaTrigger) -> None:
        a = trigger.check(make_score("TSLA", 30), previous=make_score(total=60))
        assert a is not None
        assert "↓" in a.body
        assert "급락" in a.title
