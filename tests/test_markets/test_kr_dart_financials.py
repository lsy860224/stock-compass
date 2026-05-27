"""DART finstate → QuarterlyFinancials 변환 — 분기 단독 + Q4 누적 차감 + CFS 우선."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_compass.markets._kr_dart_financials import (
    _annual_minus_quarters,
    _build_quarters,
    _parse_finstate_df,
    _ReprtData,
    fetch_kr_quarterly_via_dart,
)


def _mk_df(rows: list[dict[str, str | float]]) -> pd.DataFrame:
    """합성 DART finstate dataframe."""
    return pd.DataFrame(rows)


class TestParseFinstateDf:
    def test_cfs_preferred_over_ofs(self) -> None:
        df = _mk_df(
            [
                {
                    "fs_div": "OFS",
                    "sj_div": "IS",
                    "account_nm": "매출액",
                    "thstrm_amount": "50,000,000,000",
                },
                {
                    "fs_div": "CFS",
                    "sj_div": "IS",
                    "account_nm": "매출액",
                    "thstrm_amount": "100,000,000,000",
                },
            ]
        )
        d = _parse_finstate_df(df)
        # CFS 연결 우선
        assert d.revenue == 100_000_000_000

    def test_all_four_accounts_parsed(self) -> None:
        df = _mk_df(
            [
                {"fs_div": "CFS", "sj_div": "BS", "account_nm": "자본총계",
                 "thstrm_amount": "300,000,000,000"},
                {"fs_div": "CFS", "sj_div": "IS", "account_nm": "매출액",
                 "thstrm_amount": "100,000,000,000"},
                {"fs_div": "CFS", "sj_div": "IS", "account_nm": "영업이익",
                 "thstrm_amount": "20,000,000,000"},
                {"fs_div": "CFS", "sj_div": "IS", "account_nm": "당기순이익(손실)",
                 "thstrm_amount": "15,000,000,000"},
            ]
        )
        d = _parse_finstate_df(df)
        assert d.revenue == 100_000_000_000
        assert d.operating_income == 20_000_000_000
        assert d.net_income == 15_000_000_000
        assert d.equity == 300_000_000_000

    def test_alternative_account_names(self) -> None:
        # "수익(매출액)" / "영업이익(손실)" 별칭
        df = _mk_df(
            [
                {"fs_div": "CFS", "sj_div": "IS", "account_nm": "수익(매출액)",
                 "thstrm_amount": "100,000,000,000"},
                {"fs_div": "CFS", "sj_div": "IS", "account_nm": "영업이익(손실)",
                 "thstrm_amount": "20,000,000,000"},
            ]
        )
        d = _parse_finstate_df(df)
        assert d.revenue == 100_000_000_000
        assert d.operating_income == 20_000_000_000

    def test_duplicate_rows_first_wins(self) -> None:
        # 같은 account_nm 두 row → 첫 번째만 (CFS 우선 + 첫 매칭)
        df = _mk_df(
            [
                {"fs_div": "CFS", "sj_div": "IS", "account_nm": "당기순이익(손실)",
                 "thstrm_amount": "1,000,000"},
                {"fs_div": "CFS", "sj_div": "IS", "account_nm": "당기순이익(손실)",
                 "thstrm_amount": "2,000,000"},
            ]
        )
        assert _parse_finstate_df(df).net_income == 1_000_000

    def test_falls_back_to_ofs_when_cfs_missing(self) -> None:
        # CFS 없고 OFS만
        df = _mk_df(
            [
                {"fs_div": "OFS", "sj_div": "IS", "account_nm": "매출액",
                 "thstrm_amount": "50,000,000,000"},
            ]
        )
        assert _parse_finstate_df(df).revenue == 50_000_000_000


class TestBuildQuarters:
    def _mk_year(self, year: int) -> dict[tuple[int, str], _ReprtData]:
        # 1Q=100, Q2 단독=110, Q3 단독=120, 연간=500 → Q4 단독 = 500-100-110-120 = 170
        return {
            (year, "11013"): _ReprtData(100.0, 20.0, 15.0, 1000.0),
            (year, "11012"): _ReprtData(110.0, 22.0, 17.0, 1100.0),
            (year, "11014"): _ReprtData(120.0, 25.0, 18.0, 1200.0),
            (year, "11011"): _ReprtData(500.0, 100.0, 75.0, 1300.0),
        }

    def test_q1_q2_q3_use_single_amount(self) -> None:
        raw = self._mk_year(2024)
        qs = _build_quarters(raw)
        by_month = {q.period_end.month: q for q in qs}
        # 1Q
        assert by_month[3].revenue == 100.0
        assert by_month[3].period_end == date(2024, 3, 31)
        assert by_month[3].publish_after == date(2024, 5, 15)
        # 2Q 단독 (DART에서 이미 단독)
        assert by_month[6].revenue == 110.0
        assert by_month[6].publish_after == date(2024, 8, 14)
        # 3Q 단독
        assert by_month[9].revenue == 120.0

    def test_q4_is_annual_minus_q1_q2_q3(self) -> None:
        raw = self._mk_year(2024)
        qs = _build_quarters(raw)
        by_month = {q.period_end.month: q for q in qs}
        # Q4 = 500 - 100 - 110 - 120 = 170
        assert by_month[12].revenue == 170.0
        # Q4 영업이익 = 100 - 20 - 22 - 25 = 33
        assert by_month[12].operating_income == 33.0
        # Q4 자본총계 = 시점값 (사업보고서 자본총계 1300 그대로)
        assert by_month[12].equity == 1300.0
        # publish_after = 다음해 3/31
        assert by_month[12].publish_after == date(2025, 3, 31)

    def test_q4_none_if_quarter_missing(self) -> None:
        # Q3 보고서 누락 → Q4 못 계산
        raw = self._mk_year(2024)
        del raw[(2024, "11014")]
        qs = _build_quarters(raw)
        by_month = {q.period_end.month: q for q in qs}
        assert by_month[12].revenue is None  # Q4 계산 불가

    def test_returns_sorted_desc(self) -> None:
        raw = {**self._mk_year(2024), **self._mk_year(2023)}
        qs = _build_quarters(raw)
        ends = [q.period_end for q in qs]
        assert ends == sorted(ends, reverse=True)


class TestAnnualMinusQuarters:
    def test_calculates_q4(self) -> None:
        q1 = _ReprtData(100, None, None, None)
        q2 = _ReprtData(110, None, None, None)
        q3 = _ReprtData(120, None, None, None)
        assert _annual_minus_quarters(500, q1, q2, q3, "revenue") == 170.0

    def test_returns_none_when_quarter_missing(self) -> None:
        q1 = _ReprtData(100, None, None, None)
        q3 = _ReprtData(120, None, None, None)
        assert _annual_minus_quarters(500, q1, None, q3, "revenue") is None

    def test_returns_none_when_attr_missing(self) -> None:
        q1 = _ReprtData(100, None, None, None)
        q2 = _ReprtData(None, None, None, None)  # revenue 누락
        q3 = _ReprtData(120, None, None, None)
        assert _annual_minus_quarters(500, q1, q2, q3, "revenue") is None


class TestFetchEndToEnd:
    def test_no_dart_key_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stock_compass.config import settings

        monkeypatch.setattr(settings, "dart_api_key", None)
        qf = fetch_kr_quarterly_via_dart("005930")
        assert qf.is_empty()
