"""일일 리포트 트리거 — 정해진 시각 이후 워치리스트 요약을 한 번."""

from __future__ import annotations

from stock_compass.alerts.base import Alert
from stock_compass.scoring.engine import CompositeScore


class DailyTrigger:
    """워치리스트 점수 분포 + 상위/하위 3종목 요약.

    ticker 필드는 상위 종목으로 채워 DB 영속화 시 FK 충족. dedup은
    `has_daily_alert_today` 가 별도로 처리.
    """

    def __init__(self, *, top_n: int = 3) -> None:
        self.top_n = top_n

    def check(self, scores: list[CompositeScore]) -> Alert | None:
        if not scores:
            return None
        ordered = sorted(scores, key=lambda s: s.total_score, reverse=True)
        top = ordered[: self.top_n]
        bottom = ordered[-self.top_n :] if len(ordered) > self.top_n else []
        avg = sum(s.total_score for s in ordered) / len(ordered)

        top_str = " ".join(f"{s.ticker}({s.total_score:.0f})" for s in top)
        bottom_str = (
            " ".join(f"{s.ticker}({s.total_score:.0f})" for s in reversed(bottom))
            if bottom
            else "—"
        )

        head = ordered[0]
        return Alert(
            ticker=head.ticker,  # FK 충족용 — 실제 의미는 "전체 요약"
            market=head.market,
            trigger_type="daily",
            score_before=None,
            score_after=avg,
            title=f"일일 리포트 — 평균 {avg:.1f}",
            body=(
                f"{len(ordered)}종목 · 평균 {avg:.1f}\n"
                f"상위: {top_str}\n"
                f"하위: {bottom_str}"
            ),
        )
