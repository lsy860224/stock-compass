"""알림 시스템 — 3종 트리거 + AlertManager."""

from stock_compass.alerts.base import (
    Alert,
    AlertManager,
    FiredAlert,
    GlobalTrigger,
    PerTickerTrigger,
    TriggerType,
)
from stock_compass.alerts.daily import DailyTrigger
from stock_compass.alerts.delta import DeltaTrigger
from stock_compass.alerts.threshold import ThresholdTrigger


def default_manager() -> AlertManager:
    """기본 트리거 세트 (threshold + delta per-ticker, daily global)."""
    return AlertManager(
        per_ticker_triggers=[ThresholdTrigger(), DeltaTrigger()],
        global_triggers=[DailyTrigger()],
    )


__all__ = [
    "Alert",
    "AlertManager",
    "DailyTrigger",
    "DeltaTrigger",
    "FiredAlert",
    "GlobalTrigger",
    "PerTickerTrigger",
    "ThresholdTrigger",
    "TriggerType",
    "default_manager",
]
