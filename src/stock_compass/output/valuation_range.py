"""Valuation Range — 현재 펀더멘털 기반 가치 구간 계산.

⚠️ 예측이 아님. 현재 EPS/BPS/배당 x 시나리오 multiple → 가격 환산.
사후 EPS 변동·일회성 손익·업종 평균 차이 미반영.

CLAUDE.md 1절 원칙 준수 — "AI 예측" 표현 X, "현재 정보 x 시나리오" 표현 사용.
"""

from __future__ import annotations

from dataclasses import dataclass

from stock_compass.markets.base import Fundamentals

# 시나리오 PER 배수 (시장 평균 컨센서스 기반).
# 업종별 조정은 PR-2에서. MVP는 단일 매트릭스.
DEFAULT_PER_SCENARIOS: dict[str, float] = {
    "보수": 10.0,
    "기준": 15.0,
    "낙관": 25.0,
}
DEFAULT_PBR_SCENARIOS: dict[str, float] = {
    "보수": 0.8,
    "기준": 1.5,
    "낙관": 3.0,
}
DEFAULT_DIVIDEND_YIELD_SCENARIOS: dict[str, float] = {
    # "보수"는 높은 yield 요구 → 낮은 가격. "낙관"은 낮은 yield 수용 → 높은 가격.
    "보수": 0.05,  # 5% 배당수익률 요구
    "기준": 0.03,
    "낙관": 0.015,
}


@dataclass(frozen=True, slots=True)
class ValuationPoint:
    """한 시나리오의 valuation 추정."""

    scenario: str  # 보수 / 기준 / 낙관
    method: str  # PER / PBR / DIVIDEND
    multiple: float  # 15.0 (PER 15x) 또는 0.03 (yield 3%)
    fair_price: float  # 추정 가격 (현재 통화)
    vs_current_pct: float | None  # 현재가 대비 (+/-%, 현재가 모르면 None)


@dataclass(frozen=True, slots=True)
class ValuationRange:
    """종목 valuation band — 여러 방법론·시나리오 통합."""

    ticker: str
    currency: str
    current_price: float | None
    points: list[ValuationPoint]
    note: str  # 사용된 방법론·근거 요약

    def is_empty(self) -> bool:
        return not self.points

    def by_method(self) -> dict[str, list[ValuationPoint]]:
        out: dict[str, list[ValuationPoint]] = {}
        for p in self.points:
            out.setdefault(p.method, []).append(p)
        return out


def compute_valuation_range(
    fund: Fundamentals,
    *,
    current_price: float | None = None,
    per_scenarios: dict[str, float] | None = None,
    pbr_scenarios: dict[str, float] | None = None,
    yield_scenarios: dict[str, float] | None = None,
) -> ValuationRange:
    """Fundamentals → ValuationRange. EPS/BPS/DPS 누락 시 해당 방법 자동 스킵.

    사용 가능 방법:
    - PER x EPS  (trailing EPS 우선, 없으면 forward EPS)
    - PBR x BPS  (book_value)
    - DPS / yield  (dividend_per_share / scenario yield)
    """
    points: list[ValuationPoint] = []
    methods_used: list[str] = []

    eps = fund.trailing_eps if fund.trailing_eps not in (None, 0) else fund.forward_eps
    bps = fund.book_value
    dps = fund.dividend_per_share

    if eps and eps > 0:
        methods_used.append(f"PERxEPS (EPS={eps:.2f})")
        for scenario, mul in (per_scenarios or DEFAULT_PER_SCENARIOS).items():
            price = eps * mul
            points.append(_make_point(scenario, "PER", mul, price, current_price))

    if bps and bps > 0:
        methods_used.append(f"PBRxBPS (BPS={bps:.2f})")
        for scenario, mul in (pbr_scenarios or DEFAULT_PBR_SCENARIOS).items():
            price = bps * mul
            points.append(_make_point(scenario, "PBR", mul, price, current_price))

    if dps and dps > 0:
        methods_used.append(f"DPS/yield (DPS={dps:.2f})")
        for scenario, target_yield in (
            yield_scenarios or DEFAULT_DIVIDEND_YIELD_SCENARIOS
        ).items():
            if target_yield <= 0:
                continue
            price = dps / target_yield
            points.append(
                _make_point(scenario, "DIVIDEND", target_yield, price, current_price)
            )

    if not methods_used:
        note = "EPS/BPS/DPS 모두 누락 — valuation 추정 불가"
    else:
        note = " · ".join(methods_used)

    return ValuationRange(
        ticker=fund.ticker,
        currency=fund.currency,
        current_price=current_price,
        points=points,
        note=note,
    )


def _make_point(
    scenario: str,
    method: str,
    multiple: float,
    fair_price: float,
    current_price: float | None,
) -> ValuationPoint:
    vs_pct: float | None = None
    if current_price and current_price > 0:
        vs_pct = (fair_price - current_price) / current_price * 100
    return ValuationPoint(
        scenario=scenario,
        method=method,
        multiple=multiple,
        fair_price=fair_price,
        vs_current_pct=vs_pct,
    )


# ──────────────────────── 면책 ────────────────────────

DISCLAIMER = (
    "Valuation Range는 **예측이 아닙니다.** 현재 펀더멘털(TTM EPS·BPS·DPS) x "
    "시나리오 multiple로 환산한 가치 구간일 뿐, 사후 변동·업종 평균·일회성 손익 미반영. "
    "참고용 — 본인 판단 필수."
)
