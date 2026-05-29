"""외부 universe 소스 fetcher — pykrx (KOSPI/KOSDAQ) + Wikipedia (US 인덱스).

레이어 분리 이유:
- 테스트에서 monkeypatch로 가짜 응답 주입 용이
- KRX API/Wikipedia 변경 시 이 모듈만 수정
- pykrx · pandas 등 무거운 의존성을 lazy import
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from datetime import date as date_cls
from functools import lru_cache
from pathlib import Path
from typing import Any

from stock_compass.config import settings
from stock_compass.utils.cache import load_json, save_json
from stock_compass.utils.logging import get_logger
from stock_compass.utils.retry import external_call_retry

_logger = get_logger(__name__)

# fallback 캐시 (네트워크/소스 장애 대비)
_CACHE_ROOT = settings.cache_dir / "universes"

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
WIKI_NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
WIKI_DOW30_URL = "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average"

KOSPI_200_INDEX_CODE = "1028"
KOSDAQ_150_INDEX_CODE = "2203"

# KRX 자격증명 없을 때 FinanceDataReader 시총상위 프록시 한도.
# FDR StockListing 은 시총 내림차순 정렬 → head(N) ≈ 지수 구성 근사.
KOSPI_200_PROXY_LIMIT = 200
KOSDAQ_150_PROXY_LIMIT = 150

# FDR Dept(소속부) 중 투자 부적합 — 프록시에서 제외.
_KR_EXCLUDE_DEPT = ("스팩", "SPAC", "관리종목", "투자주의", "환기", "외국기업")


# ──────────────────────── KR (pykrx 정확 지수 → FDR 프록시) ────────────────────────
#
# 반환 형식: [(code, name, market_sub), ...]  market_sub ∈ {"KOSPI", "KOSDAQ"}
# (market_sub 로 yfinance 심볼 .KS/.KQ 정확화)
#
# 소스 체인:
#   1) KRX 자격증명(KRX_ID/KRX_PW) 있으면 pykrx 정확 지수 구성종목
#   2) 없으면 FinanceDataReader 시총상위 N 프록시 (KOSPI200≈top200, KOSDAQ150≈top150)
#   3) 둘 다 실패하면 7일 캐시 fallback
# KRX 가 지수 구성 데이터를 로그인 뒤로 이전 → 자격증명 없으면 (1)은 빈 응답.


@external_call_retry
def fetch_kospi_200_constituents() -> list[tuple[str, str, str]]:
    """KOSPI 200 멤버 — [(code, name, "KOSPI"), ...].

    KRX 자격증명 있으면 정확 지수, 없으면 FDR KOSPI 시총상위 200 프록시.
    """
    return _fetch_kr_universe(
        market_sub="KOSPI",
        index_code=KOSPI_200_INDEX_CODE,
        cache_key="kospi_200",
        proxy_limit=KOSPI_200_PROXY_LIMIT,
    )


@external_call_retry
def fetch_kosdaq_150_constituents() -> list[tuple[str, str, str]]:
    """KOSDAQ 150 멤버 — [(code, name, "KOSDAQ"), ...]."""
    return _fetch_kr_universe(
        market_sub="KOSDAQ",
        index_code=KOSDAQ_150_INDEX_CODE,
        cache_key="kosdaq_150",
        proxy_limit=KOSDAQ_150_PROXY_LIMIT,
    )


def fetch_all_kr_constituents() -> list[tuple[str, str, str]]:
    """KOSPI 200 + KOSDAQ 150 합집합. 코드 기준 중복 제거.

    docs/SCREENER_SPEC.md의 ALL_KR. 전체 KRX 종목(~2,500)은 부담이라 벤치마크
    지수(또는 시총상위 프록시) 합집합(~350)으로 시작. 양쪽 모두 실패하면 빈 리스트.
    """
    seen: dict[str, tuple[str, str, str]] = {}
    for fetcher in (fetch_kospi_200_constituents, fetch_kosdaq_150_constituents):
        try:
            for code, name, market_sub in fetcher():
                seen.setdefault(code, (code, name, market_sub))
        except (ConnectionError, TimeoutError, OSError) as e:
            _logger.warning("ALL_KR 일부 소스 실패 — 다른 소스 계속: %s", e)
    return list(seen.values())


def _fetch_kr_universe(
    *, market_sub: str, index_code: str, cache_key: str, proxy_limit: int
) -> list[tuple[str, str, str]]:
    """KR 유니버스 소스 체인: pykrx(정확) → FDR(프록시) → 캐시."""
    exact = _fetch_kr_index_pykrx(index_code, market_sub)
    if exact:
        _write_fallback(cache_key, [list(t) for t in exact])
        return exact

    proxy = _fetch_kr_listing_fdr(market_sub, limit=proxy_limit)
    if proxy:
        _logger.info(
            "%s: FDR 시총상위 %d 프록시 (정확 지수는 KRX 자격증명 필요)",
            cache_key,
            len(proxy),
        )
        _write_fallback(cache_key, [list(t) for t in proxy])
        return proxy

    return _to_kr_list(_read_fallback(cache_key))


def _fetch_kr_index_pykrx(index_code: str, market_sub: str) -> list[tuple[str, str, str]]:
    """pykrx 정확 지수 구성종목. 자격증명 없으면 빈 리스트(→ caller FDR 폴백)."""
    from stock_compass.utils.krx_auth import apply_krx_credentials, krx_quiet

    if not apply_krx_credentials():
        return []  # 자격증명 없음 — pykrx 지수 엔드포인트 로그인 필요
    try:
        with krx_quiet():
            from pykrx.stock import get_index_portfolio_deposit_file

            # pykrx 1.2.x 시그니처: (ticker=지수코드, date) — 1.0.x 의 (date, ticker)에서 변경
            raw = get_index_portfolio_deposit_file(index_code, _kr_business_day())
    except (ConnectionError, TimeoutError, ValueError, IndexError, KeyError, OSError) as e:
        _logger.warning("KRX 지수 멤버 조회 실패 (%s): %s", index_code, e)
        return []
    codes = _coerce_code_list(raw)
    if not codes:
        _logger.info("KRX 지수 %s 빈 응답 (휴장/자격증명?) — FDR 폴백", index_code)
        return []
    return [(code, _kr_name(code) or code, market_sub) for code in codes]


def _fetch_kr_listing_fdr(market_sub: str, *, limit: int) -> list[tuple[str, str, str]]:
    """FinanceDataReader 상장목록 → 시총상위 `limit` 프록시. 보통주만, 부적합 제외.

    FDR StockListing("KOSPI"/"KOSDAQ") 은 시총 내림차순 → 순서대로 필터 후 head.
    (Marcap 칼럼 값 자체는 신뢰 불가한 환경 있어 정렬 대신 기본 순서 사용.)
    """
    try:
        import FinanceDataReader as fdr  # noqa: N813 — FDR 공식 관용 alias

        df = fdr.StockListing(market_sub)
    except (ImportError, ConnectionError, TimeoutError, ValueError, KeyError, OSError) as e:
        _logger.warning("FDR %s 상장목록 실패: %s", market_sub, e)
        return []
    if df is None or getattr(df, "empty", True) or "Code" not in df.columns:
        return []

    out: list[tuple[str, str, str]] = []
    for _, row in df.iterrows():
        code = str(row.get("Code", "")).strip()
        name = str(row.get("Name", "")).strip()
        if _is_listable_kr(code, name, str(row.get("Dept", "") or "")):
            out.append((code, name, market_sub))
            if len(out) >= limit:
                break
    return out


def _is_listable_kr(code: str, name: str, dept: str) -> bool:
    """보통주 + 투자 적합 종목만. 우선주·스팩·관리종목·외국기업 제외."""
    if len(code) != 6 or not code.isdigit():
        return False
    if not code.endswith("0"):  # 보통주 코드는 끝자리 0 — 우선주/신주인수권 제외
        return False
    if not name or name.endswith("우") or "스팩" in name:
        return False
    return not any(token in dept for token in _KR_EXCLUDE_DEPT)


def _coerce_code_list(raw: object) -> list[str]:
    """pykrx get_index_portfolio_deposit_file 반환 정규화.

    정상은 list[str] 이나 버전/응답에 따라 None·DataFrame·Series 가 올 수 있어
    방어적으로 처리 (기존 `... or []` 는 DataFrame 에서 truth-value 예외 발생).
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        import pandas as pd

        if isinstance(raw, pd.DataFrame):
            return [] if raw.empty else [str(x) for x in raw.index.tolist()]
        if isinstance(raw, pd.Series | pd.Index):
            return [str(x) for x in raw.tolist()]
    except ImportError:
        pass
    if isinstance(raw, Iterable):
        try:
            return [str(x) for x in raw]
        except TypeError:
            return []
    return []


@lru_cache(maxsize=512)
def _kr_name(code: str) -> str | None:
    """pykrx 종목명 (정확 지수 경로 전용 — FDR 경로는 이름 직접 제공)."""
    from stock_compass.utils.krx_auth import krx_quiet

    try:
        with krx_quiet():
            from pykrx.stock import get_market_ticker_name

            name = get_market_ticker_name(code)
        return str(name) if name else None
    except (ConnectionError, TimeoutError, ValueError, KeyError, OSError) as e:
        _logger.debug("KRX 종목명 조회 실패 (%s): %s", code, e)
        return None


def _kr_business_day() -> str:
    """가장 가까운 영업일 (YYYYMMDD). 휴장 날짜에 호출되면 직전 영업일."""
    from stock_compass.utils.krx_auth import krx_quiet

    try:
        with krx_quiet():
            from pykrx.stock import get_nearest_business_day_in_a_week

            return str(get_nearest_business_day_in_a_week())
    except (ConnectionError, TimeoutError, ValueError, IndexError, OSError):
        # KRX 자체가 깨졌으면 오늘 날짜 추정 — caller가 빈 리스트 처리
        return datetime.now(UTC).strftime("%Y%m%d")


# ──────────────────────── US (Wikipedia) ────────────────────────


@external_call_retry
def fetch_sp500_constituents() -> list[tuple[str, str, str]]:
    """S&P 500 — [(symbol, name, sector), ...]. Wikipedia 첫 테이블.

    실패 시 캐시 fallback.
    """
    return _fetch_wiki_us_index(
        WIKI_SP500_URL,
        table_index=0,
        symbol_col="Symbol",
        name_col="Security",
        sector_col="GICS Sector",
        cache_key="sp500",
    )


@external_call_retry
def fetch_nasdaq_100_constituents() -> list[tuple[str, str, str]]:
    """NASDAQ 100 — Wikipedia 'Components' 섹션. 테이블 인덱스 4 (변동 가능)."""
    # Wikipedia 'Nasdaq-100' 페이지의 컴포넌트 테이블은 위치가 자주 바뀜
    # → 컬럼명 기반으로 자동 탐지 (아래 _fetch_wiki_us_index가 처리)
    return _fetch_wiki_us_index(
        WIKI_NASDAQ100_URL,
        table_index=None,  # auto-detect
        symbol_col="Ticker",
        name_col="Company",
        sector_col="GICS Sector",
        cache_key="nasdaq_100",
    )


@external_call_retry
def fetch_dow30_constituents() -> list[tuple[str, str, str]]:
    """Dow Jones Industrial Average 30 종목."""
    return _fetch_wiki_us_index(
        WIKI_DOW30_URL,
        table_index=None,
        symbol_col="Symbol",
        name_col="Company",
        sector_col="Industry",
        cache_key="dow30",
    )


def _fetch_wiki_us_index(
    url: str,
    *,
    table_index: int | None,
    symbol_col: str,
    name_col: str,
    sector_col: str,
    cache_key: str,
) -> list[tuple[str, str, str]]:
    cached = _read_fallback(cache_key)
    try:
        import pandas as pd

        tables = pd.read_html(
            url,
            storage_options={"User-Agent": "stock-compass/0.1 (Personal use)"},
        )
    except (ConnectionError, TimeoutError, ValueError, OSError, ImportError) as e:
        _logger.warning("Wikipedia fetch 실패 (%s) — 캐시 fallback: %s", url, e)
        return _to_triple_list(cached)

    df = _select_wiki_table(tables, table_index, symbol_col=symbol_col)
    if df is None:
        _logger.warning(
            "Wikipedia에서 %s/%s/%s 컬럼 가진 테이블 미발견 — 캐시 fallback",
            symbol_col,
            name_col,
            sector_col,
        )
        return _to_triple_list(cached)

    pairs: list[tuple[str, str, str]] = []
    for _, row in df.iterrows():
        symbol = _clean_symbol(str(row.get(symbol_col, "")))
        name = str(row.get(name_col, "")).strip()
        sector = str(row.get(sector_col, "")).strip() if sector_col in row else ""
        if symbol and name:
            pairs.append((symbol, name, sector))

    if not pairs:
        return _to_triple_list(cached)
    _write_fallback(cache_key, list(pairs))
    return pairs


def _select_wiki_table(
    tables: list[Any], table_index: int | None, *, symbol_col: str
) -> Any | None:
    """`symbol_col`을 포함한 테이블 자동 탐지 (또는 명시 인덱스)."""
    if table_index is not None and 0 <= table_index < len(tables):
        df = tables[table_index]
        if symbol_col in df.columns:
            return df
    for df in tables:
        if hasattr(df, "columns") and symbol_col in df.columns:
            return df
    return None


def _clean_symbol(raw: str) -> str:
    """Wikipedia 심볼에 가끔 붙는 각주(`AAPL[a]`) 또는 BRK.B → BRK-B 같은 정규화."""
    s = raw.strip().split("[", 1)[0].strip()
    return s.replace(".", "-")  # yfinance 형식 (BRK.B → BRK-B)


# ──────────────────────── 캐시 fallback ────────────────────────


def _cache_path(name: str) -> Path:
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return _CACHE_ROOT / f"{name}.json"


def _read_fallback(name: str) -> list[list[str]] | None:
    """7일 TTL 캐시 — 외부 소스 장애 시 마지막 정전 데이터 사용."""
    from datetime import timedelta

    data = load_json(f"universes/{name}", timedelta(days=7))
    if isinstance(data, list):
        return data
    return None


def _write_fallback(name: str, pairs: list[Any]) -> None:
    save_json(f"universes/{name}", [list(p) for p in pairs])


def _to_kr_list(cached: list[list[str]] | None) -> list[tuple[str, str, str]]:
    """KR 캐시 → [(code, name, market_sub), ...]. 구 2-항목 캐시는 KOSPI 로 간주."""
    if not cached:
        return []
    return [
        (item[0], item[1], item[2] if len(item) > 2 else "KOSPI")
        for item in cached
        if len(item) >= 2
    ]


def _to_triple_list(cached: list[list[str]] | None) -> list[tuple[str, str, str]]:
    if not cached:
        return []
    return [
        (item[0], item[1], item[2] if len(item) > 2 else "")
        for item in cached
        if len(item) >= 2
    ]


# ──────────────────────── 메타 ────────────────────────


def now_date() -> date_cls:
    """테스트에서 패치 가능한 today wrapper."""
    from stock_compass.utils.dates import today_kst

    return today_kst()
