"""KSIC (한국 표준 산업분류) 코드 → GICS sector 매핑.

DART `company` API 의 `induty_code` (KSIC 4-5자리) 활용. yfinance 가 KR 종목의
sector 를 자주 누락하거나 부정확 → DART 공식 분류로 교정.

KSIC 대분류 (2자리 prefix) → GICS 11 sector 매핑. 세부 분류는 추후 확장.
"""

from __future__ import annotations

from datetime import timedelta

from stock_compass.config import settings
from stock_compass.utils.cache import load_json, save_json
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)
_CACHE_TTL = timedelta(days=7)

# KSIC 2자리 prefix → GICS sector
# 참고: KSIC 9차 (통계청) — https://kssc.kostat.go.kr
_KSIC_TO_GICS: dict[str, str] = {
    # 농림어업 (A)
    "01": "Consumer Staples",
    "02": "Materials",
    "03": "Consumer Staples",
    # 광업 (B)
    "05": "Materials",
    "06": "Materials",
    "07": "Materials",
    "08": "Materials",
    # 제조업 (C) — KSIC 10~33, GICS 다양
    "10": "Consumer Staples",  # 식료품
    "11": "Consumer Staples",  # 음료
    "12": "Consumer Staples",  # 담배
    "13": "Consumer Discretionary",  # 섬유제품
    "14": "Consumer Discretionary",  # 의복·악세사리
    "15": "Consumer Discretionary",  # 가죽·가방·신발
    "16": "Materials",  # 목재
    "17": "Materials",  # 펄프·종이
    "18": "Communication Services",  # 인쇄·기록매체 복제
    "19": "Energy",  # 코크스·연탄·석유정제
    "20": "Materials",  # 화학물질·화학제품
    "21": "Health Care",  # 의료용 물질·의약품
    "22": "Materials",  # 고무·플라스틱
    "23": "Materials",  # 비금속 광물
    "24": "Materials",  # 1차 금속 (철강)
    "25": "Materials",  # 금속가공
    "26": "Information Technology",  # 전자부품·컴퓨터·통신장비
    "27": "Health Care",  # 의료·정밀·광학기기
    "28": "Industrials",  # 전기장비
    "29": "Industrials",  # 기타 기계·장비
    "30": "Consumer Discretionary",  # 자동차·트레일러
    "31": "Industrials",  # 기타 운송장비 (조선·항공·철도)
    "32": "Consumer Discretionary",  # 가구
    "33": "Consumer Discretionary",  # 기타 제품 (귀금속·악기 등)
    # 전기·가스·증기 (D), 수도·하수 (E)
    "35": "Utilities",
    "36": "Utilities",
    "37": "Industrials",
    "38": "Industrials",
    "39": "Industrials",
    # 건설업 (F)
    "41": "Industrials",
    "42": "Industrials",
    # 도소매 (G)
    "45": "Consumer Discretionary",  # 자동차 판매
    "46": "Consumer Discretionary",  # 도매
    "47": "Consumer Discretionary",  # 소매
    # 운수·창고 (H)
    "49": "Industrials",
    "50": "Industrials",
    "51": "Industrials",
    "52": "Industrials",
    # 숙박·음식점 (I)
    "55": "Consumer Discretionary",
    "56": "Consumer Discretionary",
    # 정보통신 (J)
    "58": "Communication Services",  # 출판
    "59": "Communication Services",  # 영상·오디오
    "60": "Communication Services",  # 방송
    "61": "Communication Services",  # 통신업
    "62": "Information Technology",  # 컴퓨터 프로그래밍·시스템 통합
    "63": "Communication Services",  # 정보 서비스 (포털·뉴스)
    # 금융·보험 (K)
    "64": "Financials",
    "65": "Financials",  # 보험
    "66": "Financials",
    # 부동산 (L)
    "68": "Real Estate",
    # 전문·과학·기술 (M)
    "70": "Industrials",
    "71": "Industrials",
    "72": "Health Care",  # 자연·인문 연구개발
    "73": "Industrials",
    # 사업시설 (N)
    "74": "Industrials",
    "75": "Industrials",
    "76": "Industrials",
    # 교육 (P)
    "85": "Consumer Discretionary",
    # 보건·사회복지 (Q)
    "86": "Health Care",
    "87": "Health Care",
    # 예술·스포츠·여가 (R)
    "90": "Communication Services",
    "91": "Consumer Discretionary",
    # 협회·단체·수리 (S)
    "94": "Industrials",
    "95": "Industrials",
    "96": "Consumer Discretionary",
}

# 11 GICS sectors (정전 리스트)
ALL_GICS_SECTORS: tuple[str, ...] = (
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
)


def classify_kr_sector_by_ksic(ksic_code: str | None) -> str | None:
    """KSIC 코드 (3-5자리) 의 2자리 prefix → GICS sector. 매칭 없으면 None."""
    if not ksic_code:
        return None
    s = str(ksic_code).strip()
    if not s.isdigit() and not all(c.isdigit() for c in s):
        # 영숫자 prefix 가능성 (희귀) — 첫 2글자만
        prefix = s[:2]
    else:
        # 1자리면 0 padding (예: "1" → "01")
        prefix = s[:2] if len(s) >= 2 else s.zfill(2)
    return _KSIC_TO_GICS.get(prefix)


def get_kr_sector(code: str) -> str | None:
    """DART company API → KSIC induty_code → GICS sector. 종목별 7d 캐시.

    DART 키 없거나 호출 실패 시 None — 호출자가 yfinance sector fallback.
    """
    cache_name = f"kr-sector-{code}"
    cached = load_json(cache_name, _CACHE_TTL)
    if isinstance(cached, dict) and "sector" in cached:
        sector = cached.get("sector")
        return sector if isinstance(sector, str) else None

    api_key = settings.dart_api_key
    if api_key is None:
        return None
    try:
        import OpenDartReader

        dart = OpenDartReader(api_key.get_secret_value())
        info = dart.company(code)
    except Exception as e:
        _logger.warning("DART company 조회 실패: %s (%s)", code, e)
        return None
    if not isinstance(info, dict):
        return None

    ksic = info.get("induty_code")
    sector = classify_kr_sector_by_ksic(str(ksic) if ksic else None)
    save_json(cache_name, {"sector": sector, "induty_code": ksic})
    return sector
