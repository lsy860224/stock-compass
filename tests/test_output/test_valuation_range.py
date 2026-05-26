"""Valuation Range — Fundamentals → 가치 구간 계산."""

from __future__ import annotations

import pytest

from stock_compass.markets.base import Fundamentals
from stock_compass.output.valuation_range import (
    DEFAULT_DIVIDEND_YIELD_SCENARIOS,
    DEFAULT_PBR_SCENARIOS,
    DEFAULT_PER_SCENARIOS,
    ValuationRange,
    compute_valuation_range,
)


def _mk_fund(**overrides: object) -> Fundamentals:
    defaults: dict[str, object] = {
        "ticker": "TEST",
        "market": "US",
        "currency": "USD",
    }
    defaults.update(overrides)
    return Fundamentals(**defaults)  # type: ignore[arg-type]


class TestComputeValuationRange:
    def test_all_inputs_missing_returns_empty(self) -> None:
        vr = compute_valuation_range(_mk_fund())
        assert isinstance(vr, ValuationRange)
        assert vr.is_empty()
        assert "누락" in vr.note

    def test_eps_only_generates_per_points(self) -> None:
        vr = compute_valuation_range(_mk_fund(trailing_eps=10.0))
        assert len(vr.points) == 3
        assert all(p.method == "PER" for p in vr.points)
        # 10 * 15 = 150 (기준)
        base = next(p for p in vr.points if p.scenario == "기준")
        assert base.fair_price == 10.0 * DEFAULT_PER_SCENARIOS["기준"]

    def test_forward_eps_fallback_when_trailing_missing(self) -> None:
        vr = compute_valuation_range(_mk_fund(forward_eps=5.0))
        assert len(vr.points) == 3
        base = next(p for p in vr.points if p.scenario == "기준")
        assert base.fair_price == 5.0 * DEFAULT_PER_SCENARIOS["기준"]

    def test_negative_eps_skipped(self) -> None:
        vr = compute_valuation_range(_mk_fund(trailing_eps=-2.0))
        assert vr.is_empty()  # EPS<=0 → PER 방법 자동 제외

    def test_all_three_methods(self) -> None:
        vr = compute_valuation_range(
            _mk_fund(trailing_eps=10.0, book_value=50.0, dividend_per_share=2.0)
        )
        methods = {p.method for p in vr.points}
        assert methods == {"PER", "PBR", "DIVIDEND"}
        assert len(vr.points) == 9  # 3 methods x 3 scenarios

    def test_dividend_yield_inverse_relation(self) -> None:
        """yield 낮을수록 가격 높음 (낙관 > 보수)."""
        vr = compute_valuation_range(_mk_fund(dividend_per_share=2.0))
        conservative = next(
            p for p in vr.points if p.method == "DIVIDEND" and p.scenario == "보수"
        )
        optimistic = next(
            p for p in vr.points if p.method == "DIVIDEND" and p.scenario == "낙관"
        )
        # 2 / 0.05 = 40, 2 / 0.015 = 133.33
        assert conservative.fair_price == pytest.approx(
            2.0 / DEFAULT_DIVIDEND_YIELD_SCENARIOS["보수"]
        )
        assert optimistic.fair_price == pytest.approx(
            2.0 / DEFAULT_DIVIDEND_YIELD_SCENARIOS["낙관"]
        )
        assert conservative.fair_price < optimistic.fair_price

    def test_vs_current_pct_computed_when_price_given(self) -> None:
        vr = compute_valuation_range(
            _mk_fund(trailing_eps=10.0), current_price=100.0
        )
        base = next(p for p in vr.points if p.scenario == "기준")
        # fair=150, current=100 → +50%
        assert base.vs_current_pct == pytest.approx(50.0)

    def test_vs_current_pct_none_when_no_price(self) -> None:
        vr = compute_valuation_range(_mk_fund(trailing_eps=10.0))
        assert all(p.vs_current_pct is None for p in vr.points)

    def test_custom_scenarios_override(self) -> None:
        vr = compute_valuation_range(
            _mk_fund(trailing_eps=10.0),
            per_scenarios={"low": 5.0, "high": 20.0},
        )
        scenarios = {p.scenario for p in vr.points}
        assert scenarios == {"low", "high"}

    def test_zero_yield_scenario_skipped(self) -> None:
        vr = compute_valuation_range(
            _mk_fund(dividend_per_share=1.0),
            yield_scenarios={"bad": 0.0, "ok": 0.04},
        )
        dividend_points = [p for p in vr.points if p.method == "DIVIDEND"]
        assert len(dividend_points) == 1
        assert dividend_points[0].scenario == "ok"

    def test_pbr_only_three_points(self) -> None:
        vr = compute_valuation_range(_mk_fund(book_value=20.0))
        assert len(vr.points) == 3
        assert all(p.method == "PBR" for p in vr.points)
        base = next(p for p in vr.points if p.scenario == "기준")
        assert base.fair_price == 20.0 * DEFAULT_PBR_SCENARIOS["기준"]

    def test_by_method_groups(self) -> None:
        vr = compute_valuation_range(
            _mk_fund(trailing_eps=10.0, book_value=50.0)
        )
        grouped = vr.by_method()
        assert set(grouped) == {"PER", "PBR"}
        assert len(grouped["PER"]) == 3
        assert len(grouped["PBR"]) == 3
