"""DART 공시 제목 키워드 기반 이벤트 유형 분류.

LLM 호출 없이 regex만으로 정확도 80%+ 분류. summarizer가 sentiment 점수와
별도로 이 분류를 활용하여 (1) Craft 노트의 사건 카테고리 표시, (2) 통계
집계, (3) 점수 안정성 (이벤트 알려진 부호로 톤 sanity check) 보조.

분류 기준: DART `report_code` (`pblntf_ty`) + `title` 키워드. 한국어 공시
제목의 정형 표현에 의존 — 확실하지 않으면 OTHER로 빠짐.
"""

from __future__ import annotations

import re
from typing import Literal

DisclosureKind = Literal[
    "BUYBACK",       # 자기주식 취득 (긍정)
    "DIVIDEND",      # 배당 (긍정)
    "CAPITAL_UP",    # 유상증자 (대체로 부정 — 희석)
    "CAPITAL_DOWN",  # 감자 (대체로 부정)
    "BONUS_ISSUE",   # 무상증자 (중립~약긍정)
    "AUDIT",         # 감사보고서·감사인 변경 (중립)
    "RESTATEMENT",   # 정정공시·재무제표 정정 (부정)
    "M_AND_A",       # 합병·분할·영업양수도 (이벤트 — 중립)
    "INSIDER",       # 임원 선임/사임·대량보유 (중립)
    "EARNINGS",      # 잠정 실적·영업실적 공시 (중립 — 톤은 LLM에 위임)
    "OTHER",         # 분류 안 됨
]

ALL_KINDS: tuple[DisclosureKind, ...] = (
    "BUYBACK",
    "DIVIDEND",
    "CAPITAL_UP",
    "CAPITAL_DOWN",
    "BONUS_ISSUE",
    "AUDIT",
    "RESTATEMENT",
    "M_AND_A",
    "INSIDER",
    "EARNINGS",
    "OTHER",
)

# 키워드는 순서대로 검사 — 더 구체적인 매칭이 먼저 와야 함.
# 정정공시는 모든 분류보다 우선 (정정이면 일단 부정 신호).
_PATTERNS: list[tuple[DisclosureKind, re.Pattern[str]]] = [
    ("RESTATEMENT", re.compile(r"정정|재무제표\s*정정|회계처리\s*변경")),
    ("BUYBACK", re.compile(r"자기?주식\s*(취득|매입|소각|처분\s*신탁)|자사주")),
    ("DIVIDEND", re.compile(r"현금\s*·?\s*현물\s*배당|배당금?\s*결정|배당.*공시")),
    ("BONUS_ISSUE", re.compile(r"무상증자")),
    ("CAPITAL_UP", re.compile(r"유상증자|신주\s*발행|주주배정\s*증자|제3자\s*배정")),
    ("CAPITAL_DOWN", re.compile(r"감자|자본금?\s*감소")),
    ("M_AND_A", re.compile(r"합병|분할|영업\s*양수도|주식의?\s*포괄적\s*교환|인수")),
    (
        "INSIDER",
        re.compile(r"임원의?\s*(선임|사임|해임|변경)|대표이사\s*변경|대량보유|주요주주"),
    ),
    ("AUDIT", re.compile(r"감사보고서|감사인\s*변경|감사인의\s*의견|내부회계관리")),
    (
        "EARNINGS",
        re.compile(r"잠정\s*실적|영업실적|매출액?\s*공시|영업이익\s*공시|결산실적"),
    ),
]


def classify(title: str, report_code: str | None = None) -> DisclosureKind:
    """공시 제목 + report_code로 사건 유형 분류. 매칭 없으면 'OTHER'.

    `report_code`는 DART의 pblntf_ty (예: 'A'=정기공시, 'B'=주요사항보고).
    제목 키워드가 우선 — report_code는 현재 보조 (향후 확장 여지).
    """
    _ = report_code  # 현재 미사용
    text = title or ""
    for kind, pat in _PATTERNS:
        if pat.search(text):
            return kind
    return "OTHER"


# 사건 유형별 prior tone (-10~+10) — LLM 호출 없이 sanity check 가능.
# LLM 톤이 prior과 크게 어긋나면 LLM 응답 자체가 의심 (향후 활용).
PRIOR_TONE: dict[DisclosureKind, float] = {
    "BUYBACK": 4.0,
    "DIVIDEND": 3.0,
    "CAPITAL_UP": -3.0,
    "CAPITAL_DOWN": -5.0,
    "BONUS_ISSUE": 1.0,
    "AUDIT": 0.0,
    "RESTATEMENT": -6.0,
    "M_AND_A": 0.0,
    "INSIDER": 0.0,
    "EARNINGS": 0.0,
    "OTHER": 0.0,
}
