"""DailyTrigger — 상위/하위 집계, 빈 입력."""

from __future__ import annotations

from stock_compass.alerts.daily import DailyTrigger

from .conftest import make_score


class TestDaily:
    def test_empty_no_alert(self) -> None:
        assert DailyTrigger().check([]) is None

    def test_single_ticker(self) -> None:
        a = DailyTrigger().check([make_score("AAPL", 60)])
        assert a is not None
        assert a.trigger_type == "daily"
        assert "AAPL(60)" in a.body
        assert a.score_after == 60.0  # 평균

    def test_ranks_top_and_bottom(self) -> None:
        scores = [
            make_score("AAPL", 80),
            make_score("MSFT", 60),
            make_score("NVDA", 75),
            make_score("TSLA", 30),
            make_score("META", 40),
        ]
        a = DailyTrigger(top_n=3).check(scores)
        assert a is not None
        body = a.body
        # 상위: 80, 75, 60 / 하위: 30, 40, 60 (정렬상 일부 겹칠 수 있음)
        assert "AAPL(80)" in body
        assert "NVDA(75)" in body
        assert "TSLA(30)" in body
        # ticker 필드는 최상위로 채워져 DB FK 충족
        assert a.ticker == "AAPL"

    def test_average_in_title(self) -> None:
        a = DailyTrigger().check(
            [make_score("A", 100), make_score("B", 0)]
        )
        assert a is not None
        assert "50.0" in a.title
