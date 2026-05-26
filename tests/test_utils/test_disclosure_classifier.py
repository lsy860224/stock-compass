"""DART 공시 제목 keyword 기반 분류 — 실제 DART 공시 제목 패턴 검증."""

from __future__ import annotations

import pytest

from stock_compass.utils.disclosure_classifier import (
    PRIOR_TONE,
    classify,
)


class TestClassify:
    @pytest.mark.parametrize(
        "title,expected",
        [
            # BUYBACK
            ("자기주식 취득 결정", "BUYBACK"),
            ("자기주식 매입 신탁계약 체결", "BUYBACK"),
            ("자기주식 처분 신탁계약 체결", "BUYBACK"),
            ("자기주식 소각 결정", "BUYBACK"),
            ("자사주 매입 결정", "BUYBACK"),
            # DIVIDEND
            ("현금ㆍ현물배당 결정", "DIVIDEND"),
            ("배당금 결정", "DIVIDEND"),
            ("주식배당 결정 공시", "DIVIDEND"),
            # CAPITAL_UP
            ("유상증자 결정", "CAPITAL_UP"),
            ("주주배정 증자 공시", "CAPITAL_UP"),
            ("제3자 배정 신주 발행", "CAPITAL_UP"),
            # CAPITAL_DOWN
            ("감자 결정", "CAPITAL_DOWN"),
            ("자본금 감소 결정", "CAPITAL_DOWN"),
            # BONUS_ISSUE
            ("무상증자 결정", "BONUS_ISSUE"),
            # AUDIT
            ("감사보고서 제출", "AUDIT"),
            ("감사인 변경 공시", "AUDIT"),
            ("내부회계관리제도 운영 보고", "AUDIT"),
            # RESTATEMENT (RESTATEMENT는 다른 키워드보다 우선)
            ("[기재정정] 자기주식 취득 결정", "RESTATEMENT"),
            ("재무제표 정정", "RESTATEMENT"),
            ("회계처리 변경 공시", "RESTATEMENT"),
            # M_AND_A
            ("합병 결정", "M_AND_A"),
            ("분할 합병 결정", "M_AND_A"),
            ("영업양수도 결정", "M_AND_A"),
            ("주식의 포괄적 교환·이전 결정", "M_AND_A"),
            # INSIDER
            ("임원의 선임 공시", "INSIDER"),
            ("대표이사 변경", "INSIDER"),
            ("주식등의 대량보유 상황보고서", "INSIDER"),
            # EARNINGS
            ("잠정 실적 공시", "EARNINGS"),
            ("영업실적 공시 (4분기)", "EARNINGS"),
            ("결산실적 공시", "EARNINGS"),
            # OTHER
            ("기타 공시 사항", "OTHER"),
            ("주요사항보고서 (소송)", "OTHER"),
            ("", "OTHER"),
        ],
    )
    def test_classification(self, title: str, expected: str) -> None:
        assert classify(title) == expected

    def test_report_code_does_not_affect(self) -> None:
        # 현재 report_code는 보조 — 키워드 매칭이 우선
        assert classify("자기주식 취득 결정", report_code="A") == "BUYBACK"
        assert classify("자기주식 취득 결정", report_code=None) == "BUYBACK"

    def test_priority_restatement_first(self) -> None:
        # "[기재정정] 배당 결정" — 정정이 우선이라 RESTATEMENT
        assert classify("[기재정정] 배당 결정") == "RESTATEMENT"


class TestPriorTone:
    def test_all_kinds_have_prior(self) -> None:
        # 모든 분류에 prior tone이 정의됐는지
        from stock_compass.utils.disclosure_classifier import ALL_KINDS

        for k in ALL_KINDS:
            assert k in PRIOR_TONE

    def test_buyback_positive(self) -> None:
        assert PRIOR_TONE["BUYBACK"] > 0

    def test_restatement_negative(self) -> None:
        assert PRIOR_TONE["RESTATEMENT"] < 0

    def test_capital_down_more_negative_than_up(self) -> None:
        assert PRIOR_TONE["CAPITAL_DOWN"] < PRIOR_TONE["CAPITAL_UP"]

    def test_audit_neutral(self) -> None:
        assert PRIOR_TONE["AUDIT"] == 0.0
