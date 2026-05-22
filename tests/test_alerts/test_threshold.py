"""ThresholdTrigger — 경계값 79/80/81, 30/29/31."""

from __future__ import annotations

import pytest

from stock_compass.alerts.threshold import ThresholdTrigger

from .conftest import make_score


@pytest.fixture
def trigger() -> ThresholdTrigger:
    return ThresholdTrigger()


class TestBuyZoneEntry:
    def test_below_threshold_no_alert(self, trigger: ThresholdTrigger) -> None:
        assert trigger.check(make_score(total=79.99), previous=None) is None

    def test_at_threshold_fires_with_no_prev(self, trigger: ThresholdTrigger) -> None:
        alert = trigger.check(make_score(total=80.0), previous=None)
        assert alert is not None
        assert alert.trigger_type == "threshold_buy"
        assert alert.score_after == 80.0
        assert alert.score_before is None

    def test_above_threshold_fires(self, trigger: ThresholdTrigger) -> None:
        alert = trigger.check(make_score(total=85.0), previous=make_score(total=70.0))
        assert alert is not None
        assert alert.trigger_type == "threshold_buy"
        assert alert.score_before == 70.0

    def test_prev_already_in_zone_no_re_fire(
        self, trigger: ThresholdTrigger
    ) -> None:
        # 어제 82, 오늘 85 → 이미 zone 안 → 발화 X
        assert (
            trigger.check(make_score(total=85.0), previous=make_score(total=82.0))
            is None
        )

    def test_prev_at_boundary_re_fire(self, trigger: ThresholdTrigger) -> None:
        # prev=79 (zone 밖), current=80 (zone 안) → 진입 발화
        alert = trigger.check(make_score(total=80.0), previous=make_score(total=79.0))
        assert alert is not None
        assert alert.trigger_type == "threshold_buy"


class TestCautionZoneEntry:
    def test_above_threshold_no_alert(self, trigger: ThresholdTrigger) -> None:
        assert trigger.check(make_score(total=31.0), previous=None) is None

    def test_at_threshold_fires_with_no_prev(self, trigger: ThresholdTrigger) -> None:
        alert = trigger.check(make_score(total=30.0), previous=None)
        assert alert is not None
        assert alert.trigger_type == "threshold_caution"

    def test_below_fires(self, trigger: ThresholdTrigger) -> None:
        alert = trigger.check(make_score(total=20.0), previous=make_score(total=45.0))
        assert alert is not None
        assert alert.trigger_type == "threshold_caution"
        assert alert.score_before == 45.0

    def test_prev_in_caution_no_re_fire(self, trigger: ThresholdTrigger) -> None:
        # prev=25, current=20 → 이미 caution → 발화 X
        assert (
            trigger.check(make_score(total=20.0), previous=make_score(total=25.0))
            is None
        )


class TestMessage:
    def test_buy_body_contains_score(self, trigger: ThresholdTrigger) -> None:
        a = trigger.check(make_score("NVDA", 88.0), previous=make_score(total=60.0))
        assert a is not None
        assert "NVDA" in a.title
        assert "88.0" in a.body
        assert "60.0" in a.body  # 직전 점수

    def test_caution_body(self, trigger: ThresholdTrigger) -> None:
        a = trigger.check(make_score("TSLA", 22.0), previous=None)
        assert a is not None
        assert "TSLA" in a.title
        assert "주의" in a.title or "caution" in a.trigger_type
