"""점수 급변 트리거 — 직전 스냅샷 대비 ±15 이상 변화."""

from __future__ import annotations

from stock_compass.alerts.base import Alert
from stock_compass.config import settings
from stock_compass.scoring.engine import CompositeScore


class DeltaTrigger:
    """방향에 따라 delta_up / delta_down 두 trigger_type 발화."""

    def check(
        self,
        current: CompositeScore,
        previous: CompositeScore | None,
    ) -> Alert | None:
        if previous is None:
            return None  # 비교할 직전 점수 없음

        delta = current.total_score - previous.total_score
        threshold = settings.alert_delta_min

        if delta >= threshold:
            return Alert(
                ticker=current.ticker,
                market=current.market,
                trigger_type="delta_up",
                score_before=previous.total_score,
                score_after=current.total_score,
                title=f"[점수 급등] {current.ticker}",
                body=(
                    f"{_label(current)} {previous.total_score:.1f}"
                    f" → {current.total_score:.1f} (↑{delta:.1f},"
                    f" 임계 {threshold})"
                ),
            )

        if -delta >= threshold:
            return Alert(
                ticker=current.ticker,
                market=current.market,
                trigger_type="delta_down",
                score_before=previous.total_score,
                score_after=current.total_score,
                title=f"[점수 급락] {current.ticker}",
                body=(
                    f"{_label(current)} {previous.total_score:.1f}"
                    f" → {current.total_score:.1f} (↓{-delta:.1f},"
                    f" 임계 {threshold})"
                ),
            )

        return None


def _label(s: CompositeScore) -> str:
    return f"{s.name} [{s.market}]" if s.name else f"{s.ticker} [{s.market}]"
