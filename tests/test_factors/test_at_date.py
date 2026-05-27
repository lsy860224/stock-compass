"""Valuation/Fundamentals calculate_at_date — 시점별 TTM 기반 점수."""

from __future__ import annotations

from datetime import date, timedelta

from stock_compass.factors import fundamentals as fundamentals_factor
from stock_compass.factors import valuation as valuation_factor
from stock_compass.markets.base import QuarterlyDatum, QuarterlyFinancials


def _qf(
    *,
    n_quarters: int = 8,
    revenue: float = 1000.0,
    op_income: float = 200.0,
    net_income: float = 150.0,
    fcf: float = 100.0,
    equity: float = 5000.0,
    shares: float = 1_000_000.0,
) -> QuarterlyFinancials:
    quarters = []
    for i in range(n_quarters):
        # 2024-Q4, Q3, Q2, Q1, 2023-Q4 ...
        year = 2024 - (i // 4)
        month = (4 - (i % 4)) * 3
        # period_end month = 12/9/6/3
        if month == 12:
            pe = date(year, 12, 31)
        elif month == 9:
            pe = date(year, 9, 30)
        elif month == 6:
            pe = date(year, 6, 30)
        else:
            pe = date(year, 3, 31)
        quarters.append(
            QuarterlyDatum(
                period_end=pe,
                publish_after=pe + timedelta(days=45),
                revenue=revenue,
                operating_income=op_income,
                net_income=net_income,
                free_cash_flow=fcf,
                equity=equity,
            )
        )
    return QuarterlyFinancials(
        ticker="TEST", market="US", quarters=quarters, shares_outstanding=shares
    )


class TestValuationAtDate:
    def test_returns_skip_when_qf_empty(self) -> None:
        empty_qf = QuarterlyFinancials(ticker="X", market="US")
        fs = valuation_factor.calculate_at_date(
            empty_qf, as_of=date(2025, 6, 1), close_at_date=100.0
        )
        assert fs.source == "backfill_skip"

    def test_returns_skip_without_shares(self) -> None:
        qf = _qf(shares=0.0)
        fs = valuation_factor.calculate_at_date(
            qf, as_of=date(2025, 6, 1), close_at_date=100.0
        )
        assert fs.source == "backfill_skip"

    def test_per_pbr_calculated(self) -> None:
        # 4 분기 NI 150 x 4 = TTM NI 600. shares 1,000,000 → EPS = 0.0006
        # close 0.006 → PER = 10
        # equity 5000 x 1 (avg) → BPS = 5000/1M = 0.005, close 0.006 → PBR=1.2
        qf = _qf()
        fs = valuation_factor.calculate_at_date(
            qf, as_of=date(2025, 6, 1), close_at_date=0.006
        )
        assert fs.source == "backfill_reconstructed"
        assert fs.raw_values["per"] is not None
        assert fs.raw_values["pbr"] is not None
        assert "[backfill 시점별]" in fs.note


class TestFundamentalsAtDate:
    def test_returns_skip_when_qf_empty(self) -> None:
        empty_qf = QuarterlyFinancials(ticker="X", market="US")
        fs = fundamentals_factor.calculate_at_date(
            empty_qf, as_of=date(2025, 6, 1)
        )
        assert fs.source == "backfill_skip"

    def test_ttm_metrics_in_raw(self) -> None:
        qf = _qf()
        fs = fundamentals_factor.calculate_at_date(qf, as_of=date(2025, 6, 1))
        assert fs.source == "backfill_reconstructed"
        # revenue_ttm = 4 x 1000 = 4000
        assert fs.raw_values["revenue_ttm"] == 4000.0
        # op_margin = 800 / 4000 = 0.2
        assert fs.raw_values["operating_margin"] == 0.2
        # roe = 600 / 5000 = 0.12
        assert fs.raw_values["roe"] == 0.12

    def test_fcf_yield_with_market_cap(self) -> None:
        qf = _qf()
        fs = fundamentals_factor.calculate_at_date(
            qf, as_of=date(2025, 6, 1), market_cap_at_date=10_000.0
        )
        # fcf_ttm = 4 x 100 = 400, market_cap 10000 → yield 0.04
        assert fs.raw_values["fcf_yield"] == 0.04

    def test_no_yoy_with_only_4_quarters(self) -> None:
        qf = _qf(n_quarters=4)
        fs = fundamentals_factor.calculate_at_date(qf, as_of=date(2025, 6, 1))
        # YoY 없음 — component score 에서 제외
        assert fs.raw_values["revenue_growth_yoy"] is None
        assert "revenue_growth_yoy" not in fs.raw_values["component_scores"]
