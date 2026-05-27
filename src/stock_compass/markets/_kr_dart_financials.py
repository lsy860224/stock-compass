"""DART OpenDartReader → KR 분기 재무 → QuarterlyFinancials.

yfinance가 .KS 종목의 분기 financials를 거의 안 줘서 KR V/F 시점별 재구성 0%.
DART 정기보고서 (1Q/반기/3Q/사업) 4종 x `finstate_all`(전체 계정과목) 호출 →
손익(IS) + 자본(BS) + 현금흐름(CF) 통합 추출.

핵심 (DART `thstrm_amount` 의미):
- 11013/11012/11014 (1Q/반기/3Q 보고서): 분기 *단독* 데이터 (DART의 IFRS 표시)
- 11011 (사업보고서): *연간 누적* (12개월 합) — Q4 단독 = 연간 - (Q1+Q2+Q3)
- BS의 자본총계는 시점값 — 그대로 사용
- CF의 영업활동현금흐름은 손익과 같이 분기 단독(or Q4 차감) 처리
- account_id (IFRS XBRL 표준) 우선 매핑 → account_nm fallback
- FCF는 영업CF 만 사용 (CapEx 차감 X — proxy, raw_values 에 명시)
- publish_after: 분기 보고서 +45일, 사업보고서 다음해 3/31
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import timedelta
from typing import Any

from stock_compass.config import settings
from stock_compass.markets.base import QuarterlyDatum, QuarterlyFinancials
from stock_compass.utils.cache import load_json, save_json
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)

_CACHE_TTL = timedelta(hours=24)

# 정기보고서 reprt_code
_QUARTER_CODES: tuple[str, ...] = ("11013", "11012", "11014", "11011")

# IFRS XBRL account_id 우선 매핑 (안정적)
_ACCOUNT_ID_MAP: dict[str, str] = {
    "ifrs-full_Revenue": "revenue",
    "dart_OperatingIncomeLoss": "operating_income",
    "ifrs-full_ProfitLoss": "net_income",
    "ifrs-full_Equity": "equity",
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "operating_cashflow",
}

# account_nm fallback (account_id 가 표준이 아닐 때) — (sj_div, account_nm) → 키
_ACCOUNT_NM_MAP: dict[tuple[str, str], str] = {
    ("BS", "자본총계"): "equity",
    ("IS", "매출액"): "revenue",
    ("IS", "수익(매출액)"): "revenue",
    ("IS", "영업수익"): "revenue",
    ("IS", "영업이익"): "operating_income",
    ("IS", "영업이익(손실)"): "operating_income",
    ("IS", "당기순이익"): "net_income",
    ("IS", "당기순이익(손실)"): "net_income",
    ("CIS", "당기순이익"): "net_income",
    ("CIS", "당기순이익(손실)"): "net_income",
    ("CF", "영업활동현금흐름"): "operating_cashflow",
    ("CF", "영업활동으로인한현금흐름"): "operating_cashflow",
}


@dataclass(frozen=True, slots=True)
class _ReprtData:
    """단일 보고서의 손익/자본/현금흐름."""

    revenue: float | None
    operating_income: float | None
    net_income: float | None
    equity: float | None
    operating_cashflow: float | None = None


def fetch_kr_quarterly_via_dart(
    code: str, *, lookback_years: int = 4
) -> QuarterlyFinancials:
    """DART finstate 로 KR 분기 재무 → QuarterlyFinancials. 24h 캐시.

    DART 키 없거나 호출 실패면 빈 결과 (호출자가 yfinance fallback).
    """
    api_key = settings.dart_api_key
    if api_key is None:
        return QuarterlyFinancials(ticker=code, market="KR")

    cache_name = f"dart-qfin-{code}"
    cached = load_json(cache_name, _CACHE_TTL)
    if isinstance(cached, dict) and cached.get("quarters") is not None:
        try:
            return QuarterlyFinancials.model_validate(cached)
        except Exception as e:
            _logger.debug("DART 캐시 검증 실패 — 재 fetch: %s (%s)", code, e)

    try:
        import OpenDartReader

        dart = OpenDartReader(api_key.get_secret_value())
    except (ImportError, Exception) as e:
        _logger.warning("OpenDartReader 초기화 실패: %s", e)
        return QuarterlyFinancials(ticker=code, market="KR")

    from stock_compass.utils.dates import today_kst

    current_year = today_kst().year
    raw: dict[tuple[int, str], _ReprtData] = {}
    years = range(current_year - lookback_years, current_year + 1)

    for year in years:
        for reprt in _QUARTER_CODES:
            data = _fetch_single_report(dart, code, year, reprt)
            if data is not None:
                raw[(year, reprt)] = data

    quarters = _build_quarters(raw)
    if not quarters:
        return QuarterlyFinancials(ticker=code, market="KR")

    shares = _kr_shares_outstanding(code)
    qf = QuarterlyFinancials(
        ticker=code,
        market="KR",
        quarters=quarters,
        shares_outstanding=shares,
    )
    save_json(cache_name, qf.model_dump(mode="json"))
    return qf


def _fetch_single_report(
    dart: Any, code: str, year: int, reprt_code: str
) -> _ReprtData | None:
    """단일 (year, reprt) 보고서 fetch → _ReprtData. 실패 시 None.

    finstate_all (전체 계정과목) 1회 호출로 손익+자본+현금흐름 모두 추출.
    """
    try:
        df = dart.finstate_all(code, year, reprt_code)
    except Exception as e:
        _logger.debug(
            "DART finstate_all %s/%d/%s 실패 (계속): %s",
            code,
            year,
            reprt_code,
            e,
        )
        return None
    if df is None or len(df) == 0:
        return None
    return _parse_finstate_all_df(df)


def _parse_finstate_all_df(df: Any) -> _ReprtData:
    """finstate_all df → _ReprtData.

    account_id (IFRS XBRL 표준) 우선 매핑 — `ifrs-full_Revenue` 같은 안정적
    식별자. fallback 으로 (sj_div, account_nm) 매핑. 같은 키에 여러 row 있으면
    첫 번째 값 사용.
    """
    out: dict[str, float | None] = {
        "revenue": None,
        "operating_income": None,
        "net_income": None,
        "equity": None,
        "operating_cashflow": None,
    }
    # 1차: account_id 매핑 (안정적)
    for _, row in df.iterrows():
        account_id = row.get("account_id")
        if account_id is None:
            continue
        mapped = _ACCOUNT_ID_MAP.get(account_id)
        if mapped is None or out[mapped] is not None:
            continue
        value = _parse_amount(row.get("thstrm_amount"))
        if value is not None:
            out[mapped] = value

    # 2차: account_nm fallback (1차에서 못 채운 항목만)
    if any(v is None for v in out.values()):
        for _, row in df.iterrows():
            key = (row.get("sj_div"), row.get("account_nm"))
            mapped = _ACCOUNT_NM_MAP.get(key)
            if mapped is None or out[mapped] is not None:
                continue
            value = _parse_amount(row.get("thstrm_amount"))
            if value is not None:
                out[mapped] = value

    return _ReprtData(
        revenue=out["revenue"],
        operating_income=out["operating_income"],
        net_income=out["net_income"],
        equity=out["equity"],
        operating_cashflow=out["operating_cashflow"],
    )


def _parse_amount(s: Any) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _build_quarters(
    raw: dict[tuple[int, str], _ReprtData],
) -> list[QuarterlyDatum]:
    """DART 보고서 → 분기 단독 QuarterlyDatum.

    11013/11012/11014: 이미 분기 단독 → 그대로 사용
    11011 (사업): 연간 누적 → Q4 = 연간 - (Q1+Q2+Q3)
    자본총계는 모든 보고서에서 시점값.
    """
    quarters: list[QuarterlyDatum] = []
    for (year, reprt), data in raw.items():
        period_end, publish_after = _quarter_meta(year, reprt)
        if period_end is None or publish_after is None:
            continue

        if reprt == "11011":
            # Q4 단독 = 사업(연간) - Q1 - Q2 - Q3 (flow 항목 모두)
            q1 = raw.get((year, "11013"))
            q2 = raw.get((year, "11012"))
            q3 = raw.get((year, "11014"))
            rev_q4 = _annual_minus_quarters(data.revenue, q1, q2, q3, "revenue")
            op_q4 = _annual_minus_quarters(
                data.operating_income, q1, q2, q3, "operating_income"
            )
            ni_q4 = _annual_minus_quarters(data.net_income, q1, q2, q3, "net_income")
            ocf_q4 = _annual_minus_quarters(
                data.operating_cashflow, q1, q2, q3, "operating_cashflow"
            )
            quarters.append(
                QuarterlyDatum(
                    period_end=period_end,
                    publish_after=publish_after,
                    revenue=rev_q4,
                    operating_income=op_q4,
                    net_income=ni_q4,
                    free_cash_flow=ocf_q4,  # FCF proxy = operating CF (CapEx 차감 X)
                    equity=data.equity,
                )
            )
        else:
            # 분기 단독 그대로
            quarters.append(
                QuarterlyDatum(
                    period_end=period_end,
                    publish_after=publish_after,
                    revenue=data.revenue,
                    operating_income=data.operating_income,
                    net_income=data.net_income,
                    free_cash_flow=data.operating_cashflow,  # FCF proxy
                    equity=data.equity,
                )
            )
    return sorted(quarters, key=lambda q: q.period_end, reverse=True)


def _quarter_meta(
    year: int, reprt: str
) -> tuple[date_cls | None, date_cls | None]:
    """(period_end, publish_after)."""
    if reprt == "11013":  # 1분기
        return date_cls(year, 3, 31), date_cls(year, 5, 15)
    if reprt == "11012":  # 반기
        return date_cls(year, 6, 30), date_cls(year, 8, 14)
    if reprt == "11014":  # 3분기
        return date_cls(year, 9, 30), date_cls(year, 11, 14)
    if reprt == "11011":  # 사업
        return date_cls(year, 12, 31), date_cls(year + 1, 3, 31)
    return None, None


def _annual_minus_quarters(
    annual: float | None,
    q1: _ReprtData | None,
    q2: _ReprtData | None,
    q3: _ReprtData | None,
    attr: str,
) -> float | None:
    """사업보고서(연간) 에서 Q1+Q2+Q3 합 차감 → Q4 단독. 한 분기라도 누락이면 None."""
    if annual is None:
        return None
    parts: list[float] = []
    for q in (q1, q2, q3):
        v = getattr(q, attr) if q else None
        if v is None:
            return None
        parts.append(float(v))
    return annual - sum(parts)


def _kr_shares_outstanding(code: str) -> float | None:
    """현재 상장주식수. 시점별 미지원 — 현재 값만.

    1순위: pykrx 시가총액 데이터 (상장주식수 컬럼)
    2순위: yfinance .KS 심볼 .info.sharesOutstanding
    둘 다 실패 시 None → Valuation backfill_skip.
    """
    # 1) pykrx 시도
    try:
        from pykrx.stock import (
            get_market_cap_by_ticker,
            get_nearest_business_day_in_a_week,
        )

        day = get_nearest_business_day_in_a_week()
        df = get_market_cap_by_ticker(day)
        if code in df.index:
            return float(df.loc[code, "상장주식수"])
    except (IndexError, KeyError, ValueError, OSError) as e:
        _logger.debug("pykrx 상장주식수 조회 실패 — yfinance 시도: %s (%s)", code, e)

    # 2) yfinance fallback (.KS 심볼)
    try:
        from stock_compass.markets.us import UsAdapter

        info = UsAdapter()._fetch_info(f"{code}.KS")
        shares = info.get("sharesOutstanding")
        if shares:
            return float(shares)
    except Exception as e:
        _logger.debug("yfinance shares 조회 실패: %s (%s)", code, e)

    return None


__all__: Sequence[str] = ("fetch_kr_quarterly_via_dart",)
