"""분기 재무 → TTM + YoY — look-ahead 차단 + 데이터 부족 처리."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stock_compass.factors._fundamentals_history import (
    available_quarters_at,
    metrics_at,
)
from stock_compass.markets.base import QuarterlyDatum, QuarterlyFinancials


def _quarter(
    year: int,
    month: int,
    *,
    revenue: float | None = 100.0,
    op_income: float | None = 20.0,
    net_income: float | None = 15.0,
    fcf: float | None = 10.0,
    equity: float | None = 500.0,
) -> QuarterlyDatum:
    """3/6/9/12월말 분기. publish_after = period_end + 45일."""
    pe = date(year, month, 28)  # 안전한 분기말 근사
    return QuarterlyDatum(
        period_end=pe,
        publish_after=pe + timedelta(days=45),
        revenue=revenue,
        operating_income=op_income,
        net_income=net_income,
        free_cash_flow=fcf,
        equity=equity,
    )


def _qf(quarters: list[QuarterlyDatum]) -> QuarterlyFinancials:
    return QuarterlyFinancials(
        ticker="TEST", market="US", quarters=quarters, shares_outstanding=1000.0
    )


class TestAvailableQuartersAt:
    def test_publish_after_blocks_future_quarter(self) -> None:
        # 2024 Q4 (period_end 12/28, publish 2025-02-11). as_of=2025-01-01 → skip
        qf = _qf([_quarter(2024, 12)])
        assert available_quarters_at(qf, date(2025, 1, 1)) == []
        # as_of=2025-03-01 → 포함
        assert len(available_quarters_at(qf, date(2025, 3, 1))) == 1

    def test_returns_in_input_order_latest_first(self) -> None:
        qf = _qf(
            [
                _quarter(2024, 12),
                _quarter(2024, 9),
                _quarter(2024, 6),
                _quarter(2024, 3),
            ]
        )
        out = available_quarters_at(qf, date(2025, 3, 1))
        assert [q.period_end.month for q in out] == [12, 9, 6, 3]


class TestMetricsAt:
    def test_insufficient_quarters_returns_empty(self) -> None:
        # 3분기만 → TTM 불가
        qf = _qf(
            [_quarter(2024, 12), _quarter(2024, 9), _quarter(2024, 6)]
        )
        assert metrics_at(qf, date(2025, 4, 1)) == {}

    def test_ttm_4_quarters_sum(self) -> None:
        # 4분기 각 revenue=100 → TTM=400, op=80, ni=60
        qf = _qf(
            [
                _quarter(2024, 12),
                _quarter(2024, 9),
                _quarter(2024, 6),
                _quarter(2024, 3),
            ]
        )
        m = metrics_at(qf, date(2025, 4, 1))
        assert m["revenue_ttm"] == 400.0
        assert m["op_income_ttm"] == 80.0
        assert m["ni_ttm"] == 60.0
        assert m["operating_margin"] == pytest.approx(0.20)  # 80 / 400
        assert m["roe"] == pytest.approx(60 / 500)  # ni / avg_equity

    def test_yoy_growth_with_8_quarters(self) -> None:
        # 최근 4분기 rev=100, prior 4분기 rev=80 → growth +25%
        qf = _qf(
            [
                _quarter(2024, 12, revenue=100),
                _quarter(2024, 9, revenue=100),
                _quarter(2024, 6, revenue=100),
                _quarter(2024, 3, revenue=100),
                _quarter(2023, 12, revenue=80),
                _quarter(2023, 9, revenue=80),
                _quarter(2023, 6, revenue=80),
                _quarter(2023, 3, revenue=80),
            ]
        )
        m = metrics_at(qf, date(2025, 4, 1))
        assert m["revenue_growth_yoy"] == pytest.approx(0.25)

    def test_yoy_growth_none_without_prior(self) -> None:
        # 4분기만 — YoY 불가
        qf = _qf(
            [
                _quarter(2024, 12),
                _quarter(2024, 9),
                _quarter(2024, 6),
                _quarter(2024, 3),
            ]
        )
        m = metrics_at(qf, date(2025, 4, 1))
        assert m["revenue_growth_yoy"] is None

    def test_missing_field_breaks_ttm(self) -> None:
        # 1개 분기에 revenue None → TTM revenue None
        qf = _qf(
            [
                _quarter(2024, 12, revenue=None),
                _quarter(2024, 9),
                _quarter(2024, 6),
                _quarter(2024, 3),
            ]
        )
        m = metrics_at(qf, date(2025, 4, 1))
        assert m["revenue_ttm"] is None
        # 다른 지표는 영향 X (net_income 4개 모두 있음)
        assert m["ni_ttm"] == 60.0

    def test_point_in_time_excludes_future(self) -> None:
        # 5분기 중 가장 최근 (2025 Q1, period_end 2025-03-28) publish 2025-05-12
        # as_of=2025-05-01 → 그 분기 제외, 4분기 (2024 전체) 사용
        qf = _qf(
            [
                _quarter(2025, 3, revenue=200),  # 새 분기
                _quarter(2024, 12),
                _quarter(2024, 9),
                _quarter(2024, 6),
                _quarter(2024, 3),
            ]
        )
        m = metrics_at(qf, date(2025, 5, 1))
        # 2025-Q1 제외, 2024 4분기 = revenue 400
        assert m["revenue_ttm"] == 400.0
