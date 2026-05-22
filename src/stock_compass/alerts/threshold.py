"""임계치 진입 트리거 — ≥80 (관심권) 또는 ≤30 (주의) 신규 진입."""

from __future__ import annotations

from stock_compass.alerts.base import Alert
from stock_compass.config import settings
from stock_compass.scoring.engine import CompositeScore


class ThresholdTrigger:
    """이전 스냅샷이 zone 밖이고 현재가 zone 안일 때만 발화 (한 번)."""

    def check(
        self,
        current: CompositeScore,
        previous: CompositeScore | None,
    ) -> Alert | None:
        buy = settings.alert_threshold_buy
        caution = settings.alert_threshold_caution

        prev_score = previous.total_score if previous is not None else None

        if current.total_score >= buy and (prev_score is None or prev_score < buy):
            delta = (
                f"직전 {prev_score:.1f} → {current.total_score:.1f}"
                if prev_score is not None
                else f"신규 {current.total_score:.1f}"
            )
            return Alert(
                ticker=current.ticker,
                market=current.market,
                trigger_type="threshold_buy",
                score_before=prev_score,
                score_after=current.total_score,
                title=f"[관심권 진입] {current.ticker}",
                body=(
                    f"{_label(current)} 종합 {current.total_score:.1f} (≥{buy})"
                    f" — {delta}"
                ),
            )

        if (
            current.total_score <= caution
            and (prev_score is None or prev_score > caution)
        ):
            delta = (
                f"직전 {prev_score:.1f} → {current.total_score:.1f}"
                if prev_score is not None
                else f"신규 {current.total_score:.1f}"
            )
            return Alert(
                ticker=current.ticker,
                market=current.market,
                trigger_type="threshold_caution",
                score_before=prev_score,
                score_after=current.total_score,
                title=f"[주의 진입] {current.ticker}",
                body=(
                    f"{_label(current)} 종합 {current.total_score:.1f} (≤{caution})"
                    f" — {delta}"
                ),
            )

        return None


def _label(s: CompositeScore) -> str:
    return f"{s.name} [{s.market}]" if s.name else f"{s.ticker} [{s.market}]"
