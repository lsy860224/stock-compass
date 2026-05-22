"""Alert 도메인 + AlertManager — 트리거 순회·dedup·발화 조율.

3종 트리거 (CLAUDE.md 9):
- 임계치 (threshold_buy / threshold_caution) — ≥80 진입 또는 ≤30 진입
- 급변 (delta_up / delta_down) — 24h 내 ±15 변화
- 일일 (daily) — 매일 정해진 시각, 워치리스트 요약

중복 방지: 동일 종목·동일 trigger_type 24h 내 재발화 X (CLAUDE.md 9).
일일은 calendar date(KST) 기준 중복 차단.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Literal, Protocol

from stock_compass.db import (
    AlertRow,
    get_previous_composite_score,
    get_scores_on_date,
    get_ticker_id,
    has_daily_alert_today,
    has_recent_alert,
    record_alert,
)
from stock_compass.markets.base import Market
from stock_compass.scoring.engine import CompositeScore
from stock_compass.utils.dates import now_kst, now_utc, to_iso_utc, today_kst
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

TriggerType = Literal[
    "threshold_buy", "threshold_caution", "delta_up", "delta_down", "daily"
]


@dataclass(frozen=True, slots=True)
class Alert:
    """발화 의사결정 단위. ticker_id는 dedup·DB 저장 시 해결."""

    ticker: str
    market: Market
    trigger_type: TriggerType
    score_before: float | None
    score_after: float
    title: str
    body: str

    @property
    def short_label(self) -> str:
        return f"{self.ticker} {self.trigger_type}"


class PerTickerTrigger(Protocol):
    """단일 종목 (current vs previous) 비교."""

    def check(
        self, current: CompositeScore, previous: CompositeScore | None
    ) -> Alert | None: ...


class GlobalTrigger(Protocol):
    """전체 스냅샷 입력 (워치리스트 요약 등)."""

    def check(self, scores: list[CompositeScore]) -> Alert | None: ...


@dataclass(frozen=True, slots=True)
class FiredAlert:
    """발화 완료 결과 — UI 요약 + 디버그용."""

    alert: Alert
    delivered: bool
    delivered_via: str
    deduplicated: bool
    reason: str


class AlertManager:
    """모든 트리거 호출 + 중복 방지 + 발화 조율 + DB 영속화."""

    def __init__(
        self,
        per_ticker_triggers: list[PerTickerTrigger],
        global_triggers: list[GlobalTrigger],
        *,
        notify_fn: object | None = None,
    ) -> None:
        self.per_ticker_triggers = per_ticker_triggers
        self.global_triggers = global_triggers
        # 순환 import 회피용 lazy default
        self._notify_fn = notify_fn

    def run(
        self,
        conn: sqlite3.Connection,
        *,
        on_date: date_cls | None = None,
        dry_run: bool = False,
    ) -> list[FiredAlert]:
        """지정일(기본: 오늘 KST) 스냅샷으로 모든 트리거 평가."""
        on_date = on_date or today_kst()
        scores = get_scores_on_date(conn, on_date)
        if not scores:
            _logger.warning(
                "%s 스냅샷 없음 — 먼저 `stock-compass batch` 실행 필요", on_date
            )
            return []

        fired: list[FiredAlert] = []
        for score in scores:
            ticker_id = get_ticker_id(conn, score.ticker, score.market)
            if ticker_id is None:
                continue
            previous = get_previous_composite_score(
                conn, ticker_id, before_date=on_date
            )
            for trigger in self.per_ticker_triggers:
                alert = trigger.check(score, previous)
                if alert is None:
                    continue
                fired.append(
                    self._fire(conn, ticker_id, alert, dry_run=dry_run)
                )

        for global_trigger in self.global_triggers:
            alert = global_trigger.check(scores)
            if alert is None:
                continue
            top_ticker_id = get_ticker_id(conn, alert.ticker, alert.market)
            if top_ticker_id is None:
                continue
            fired.append(
                self._fire(
                    conn, top_ticker_id, alert, dry_run=dry_run, is_daily=True
                )
            )

        return fired

    # ─── 내부 ───

    def _fire(
        self,
        conn: sqlite3.Connection,
        ticker_id: int,
        alert: Alert,
        *,
        dry_run: bool,
        is_daily: bool = False,
    ) -> FiredAlert:
        # dedup
        if is_daily:
            if has_daily_alert_today(conn):
                return FiredAlert(
                    alert=alert,
                    delivered=False,
                    delivered_via="",
                    deduplicated=True,
                    reason="오늘 daily 이미 발화",
                )
        elif has_recent_alert(conn, ticker_id, alert.trigger_type, hours=24):
            return FiredAlert(
                alert=alert,
                delivered=False,
                delivered_via="",
                deduplicated=True,
                reason="24h 내 동일 trigger 발화 이력",
            )

        if dry_run:
            return FiredAlert(
                alert=alert,
                delivered=False,
                delivered_via="dry_run",
                deduplicated=False,
                reason="dry-run",
            )

        delivered_via = self._notify(alert)
        record_alert(
            conn,
            AlertRow(
                ticker_id=ticker_id,
                trigger_type=alert.trigger_type,
                score_before=alert.score_before,
                score_after=alert.score_after,
                message=alert.body,
                delivered_via=delivered_via,
                fired_at=to_iso_utc(now_utc()),
            ),
        )
        return FiredAlert(
            alert=alert,
            delivered=True,
            delivered_via=delivered_via,
            deduplicated=False,
            reason="발화 완료",
        )

    def _notify(self, alert: Alert) -> str:
        if self._notify_fn is None:
            # lazy import 회피 — output.notify 의존성 분리
            from stock_compass.output.notify import macos_notify

            self._notify_fn = macos_notify
        # title + subtitle + body
        subtitle = f"stock-compass · {now_kst().strftime('%Y-%m-%d %H:%M')}"
        self._notify_fn(  # type: ignore[operator]
            title=alert.title, body=alert.body, subtitle=subtitle
        )
        return "macos_notify"
