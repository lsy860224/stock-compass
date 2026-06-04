"""한국 시장 어댑터 (yfinance + pykrx + DART)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from stock_compass.config import settings
from stock_compass.markets.base import (
    Currency,
    Disclosure,
    Fundamentals,
    MarketAdapter,
    News,
    PriceHistory,
    QuarterlyFinancials,
    pct_to_fraction,
)
from stock_compass.markets.us import UsAdapter, _get, _parse_news_time
from stock_compass.utils.cache import (
    cache_key_for_today,
    load_dataframe,
    load_json,
    save_dataframe,
    save_json,
)
from stock_compass.utils.logging import get_logger
from stock_compass.utils.retry import external_call_retry

if TYPE_CHECKING:
    import pandas as pd

_logger = get_logger(__name__)
_HISTORY_TTL = timedelta(hours=24)
_INFO_TTL = timedelta(hours=6)
_DISCLOSURE_TTL = timedelta(hours=6)
_KR_CODE_RE = re.compile(r"^\d{6}$")


@lru_cache(maxsize=1)
def _kospi_codes() -> frozenset[str]:
    return _kr_market_codes("KOSPI")


@lru_cache(maxsize=1)
def _kosdaq_codes() -> frozenset[str]:
    return _kr_market_codes("KOSDAQ")


def _kr_market_codes(market: str) -> frozenset[str]:
    """시장별(KOSPI/KOSDAQ) 종목코드 집합 — yfinance .KS/.KQ 분류용.

    pykrx get_market_ticker_list 가 KRX 로그인 뒤로 이동해 무인증 시 빈 응답 →
    FinanceDataReader 상장목록을 1차 소스로 (.KQ 분류 복구). 실패 시 pykrx 폴백.
    """
    try:
        import FinanceDataReader as fdr  # noqa: N813 — FDR 공식 관용 alias

        df = fdr.StockListing(market)
        if df is not None and not df.empty and "Code" in df.columns:
            return frozenset(str(c).strip() for c in df["Code"] if str(c).strip())
    except (ImportError, ConnectionError, TimeoutError, ValueError, KeyError, OSError) as e:
        _logger.warning("FDR %s 상장목록 실패 (%s) — pykrx 폴백", market, e)

    from stock_compass.utils.krx_auth import krx_quiet

    try:
        with krx_quiet():
            from pykrx.stock import get_market_ticker_list

            return frozenset(get_market_ticker_list(market=market))
    except (ConnectionError, TimeoutError, ValueError, IndexError, KeyError, OSError) as e:
        _logger.warning("%s 종목 목록 조회 실패 (pykrx): %s — 심볼 추정 사용", market, e)
        return frozenset()


def is_kospi(code: str) -> bool:
    return code in _kospi_codes()


def is_kosdaq(code: str) -> bool:
    return code in _kosdaq_codes()


def _kr_sector_from_dart(code: str) -> str | None:
    """DART KSIC → GICS 매핑 lazy wrapper. _kr_sector_classifier import 격리."""
    try:
        from stock_compass.markets._kr_sector_classifier import get_kr_sector

        return get_kr_sector(code)
    except Exception as e:
        _logger.debug("KR sector DART 조회 실패: %s (%s)", code, e)
        return None


@lru_cache(maxsize=512)
def _kr_name(code: str) -> str | None:
    """pykrx로 KR 종목명 조회. Naver 검색·로깅에 사용. 실패 시 None."""
    try:
        from pykrx.stock import get_market_ticker_name

        name = get_market_ticker_name(code)
        return str(name) if name else None
    except (ConnectionError, TimeoutError, ValueError, KeyError, OSError) as e:
        _logger.debug("KR 종목명 조회 실패: %s (%s)", code, e)
        return None


def _fetch_news_naver(code: str, *, days: int) -> list[News]:
    """Naver Search API로 KR 뉴스 가져옴. client id/secret 없으면 빈 리스트.

    종목명 기준 검색 (pykrx 이름 lookup). 이름 미상이면 Naver 경로 생략.
    """
    if not settings.naver_client_id or not settings.naver_client_secret:
        return []
    name = _kr_name(code)
    if not name:
        _logger.debug("Naver: %s 종목명 미상 — 검색 생략", code)
        return []

    secret = settings.naver_client_secret.get_secret_value()
    try:
        import html
        import re as _re

        import httpx

        r = httpx.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={
                "X-Naver-Client-Id": settings.naver_client_id,
                "X-Naver-Client-Secret": secret,
            },
            params={"query": name, "display": 10, "sort": "date"},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        _logger.warning("Naver news fetch 실패: %s (%s)", code, e)
        return []

    cutoff = datetime.now(UTC) - timedelta(days=days)
    out: list[News] = []
    tag_re = _re.compile(r"<[^>]+>")
    for item in data.get("items", []):
        try:
            published = datetime.strptime(
                item["pubDate"], "%a, %d %b %Y %H:%M:%S %z"
            )
        except (ValueError, KeyError):
            continue
        if published < cutoff:
            continue
        title = html.unescape(tag_re.sub("", str(item.get("title", "")))).strip()
        article_url = str(item.get("originallink") or item.get("link") or "")
        desc = html.unescape(tag_re.sub("", str(item.get("description", "")))).strip()
        if not title or not article_url:
            continue
        out.append(
            News(
                title=title,
                url=article_url,
                published_at=published,
                source_name="naver",
                summary=desc or None,
            )
        )
    return out


class KrAdapter(MarketAdapter):
    """KR. yfinance 1차 + pykrx 보조 + DART 공시."""

    market = "KR"

    # ─── 심볼 변환 ───

    def to_yfinance_symbol(self, ticker: str) -> str:
        code = self._normalize_code(ticker)
        if is_kospi(code):
            return f"{code}.KS"
        if is_kosdaq(code):
            return f"{code}.KQ"
        # 미상 — 일단 KOSPI 추정 (대부분 시총 상위 KOSPI), 호출자가 로그로 확인
        _logger.warning("KR 시장 미상 종목, KOSPI(.KS)로 추정: %s", code)
        return f"{code}.KS"

    @staticmethod
    def _normalize_code(ticker: str) -> str:
        """`005930.KS` / `005930` / `5930` → `005930`."""
        t = ticker.split(".", maxsplit=1)[0]
        if not t.isdigit():
            raise ValueError(f"KR 종목 코드는 6자리 숫자여야 합니다: {ticker!r}")
        code = t.zfill(6)
        if not _KR_CODE_RE.match(code):
            raise ValueError(f"KR 종목 코드 형식 오류: {ticker!r}")
        return code

    def get_currency(self) -> Currency:
        return "KRW"

    def get_trading_hours(self) -> tuple[time, time]:
        return (time(9, 0), time(15, 30))

    # ─── price history ───

    def get_price_history(self, ticker: str, *, period: str = "1y") -> PriceHistory:
        code = self._normalize_code(ticker)
        symbol = self.to_yfinance_symbol(code)
        cache_name = f"{cache_key_for_today(symbol)}-{period}"
        df = load_dataframe(cache_name, _HISTORY_TTL)
        source = "cache"
        if df is None:
            df = self._fetch_history_yf(symbol, period)
            source = "yfinance"
            # KOSPI 명시 아니고 .KS 빈 결과면 KOSDAQ(.KQ) 시도 — pykrx 정전 시 fallback
            if df.empty and symbol.endswith(".KS") and not is_kospi(code):
                alt = f"{code}.KQ"
                _logger.info("yfinance %s 빈 결과 → %s 시도", symbol, alt)
                df = self._fetch_history_yf(alt, period)
                if not df.empty:
                    source = "yfinance(.KQ)"
            if df.empty:
                _logger.info("yfinance KR 빈 결과 → pykrx fallback: %s", code)
                df = self._fetch_history_pykrx(code, period)
                source = "pykrx"
            if not df.empty:
                save_dataframe(cache_name, df)
        if df.empty:
            _logger.warning("KR OHLCV 비어 있음: %s (period=%s)", code, period)
        return PriceHistory(
            ticker=code, market="KR", currency="KRW", source=source, df=df
        )

    @external_call_retry
    def _fetch_history_yf(self, symbol: str, period: str) -> pd.DataFrame:
        # UsAdapter의 _fetch_history와 동일 로직 — 위임
        return UsAdapter()._fetch_history(symbol, period)

    def _fetch_history_pykrx(self, code: str, period: str) -> pd.DataFrame:
        import pandas as pd
        from pykrx.stock import get_market_ohlcv_by_date

        end = datetime.now(UTC).date()
        days_map = {"1mo": 31, "3mo": 95, "6mo": 190, "1y": 380, "2y": 760, "5y": 1900}
        days = days_map.get(period, 380)
        start = end - timedelta(days=days)
        try:
            df = get_market_ohlcv_by_date(
                start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code
            )
        except (ConnectionError, TimeoutError, ValueError, IndexError, OSError) as e:
            _logger.warning("pykrx OHLCV 실패: %s (%s)", code, e)
            return pd.DataFrame()
        if df.empty:
            return df
        df = df.rename(
            columns={
                "시가": "open",
                "고가": "high",
                "저가": "low",
                "종가": "close",
                "거래량": "volume",
            }
        )
        df["adj_close"] = df["close"]
        return df[["open", "high", "low", "close", "volume", "adj_close"]]

    # ─── fundamentals ───

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        code = self._normalize_code(ticker)
        symbol = self.to_yfinance_symbol(code)
        cache_name = f"info-{cache_key_for_today(symbol)}"
        info: dict[str, Any] | None = load_json(cache_name, _INFO_TTL)
        if info is None:
            info = UsAdapter()._fetch_info(symbol)
            # KOSPI 명시 아니고 빈 info면 KOSDAQ(.KQ) 시도
            if not info and symbol.endswith(".KS") and not is_kospi(code):
                alt = f"{code}.KQ"
                _logger.info("yfinance info %s 빈 → %s 시도", symbol, alt)
                info = UsAdapter()._fetch_info(alt)
            if info:
                save_json(cache_name, info)
        info = info or {}

        # sector — KR 종목은 DART KSIC → GICS 우선 (yfinance label "Technology"/
        # "Consumer Cyclical" 같이 GICS 표준 아님). DART 실패 시 yfinance 폴백.
        sector = _kr_sector_from_dart(code) or _get(info, "sector", as_=str)

        # pykrx로 PER/PBR 보강 (yfinance KR 누락 빈번)
        per = _get(info, "trailingPE", as_=float)
        pbr = _get(info, "priceToBook", as_=float)
        if per is None or pbr is None:
            pykrx_fund = self._fetch_fundamentals_pykrx(code)
            per = per or pykrx_fund.get("per")
            pbr = pbr or pykrx_fund.get("pbr")

        return Fundamentals(
            ticker=code,
            market="KR",
            currency="KRW",
            name=_get(info, "longName", "shortName", as_=str),
            sector=sector,
            per=per,
            forward_per=_get(info, "forwardPE", as_=float),
            pbr=pbr,
            peg=_get(info, "pegRatio", "trailingPegRatio", as_=float),
            psr=_get(info, "priceToSalesTrailing12Months", as_=float),
            ev_ebitda=_get(info, "enterpriseToEbitda", as_=float),
            dividend_yield=pct_to_fraction(_get(info, "dividendYield", as_=float)),
            roe=_get(info, "returnOnEquity", as_=float),
            revenue_growth_yoy=_get(info, "revenueGrowth", as_=float),
            earnings_growth_yoy=_get(info, "earningsGrowth", as_=float),
            operating_margin=_get(info, "operatingMargins", as_=float),
            profit_margin=_get(info, "profitMargins", as_=float),
            free_cash_flow=_get(info, "freeCashflow", as_=float),
            market_cap=_get(info, "marketCap", as_=float),
            shares_outstanding=_get(
                info, "sharesOutstanding", "impliedSharesOutstanding", as_=float
            ),
            debt_to_equity=_get(info, "debtToEquity", as_=float),
            current_ratio=_get(info, "currentRatio", as_=float),
            roa=_get(info, "returnOnAssets", as_=float),
            trailing_eps=_get(info, "trailingEps", as_=float),
            forward_eps=_get(info, "forwardEps", as_=float),
            book_value=_get(info, "bookValue", as_=float),
            dividend_per_share=_get(info, "dividendRate", as_=float),
            source="yfinance+pykrx" if (per or pbr) else "yfinance",
        )

    def _fetch_fundamentals_pykrx(self, code: str) -> dict[str, float | None]:
        """KR PER/PBR 보강 — pykrx `get_market_fundamental` 사용.

        pykrx 1.2.x 는 이 엔드포인트에 KRX 로그인(`KRX_ID`/`KRX_PW`)을 요구한다.
        자격증명이 없으면 즉시 빈 dict 반환 — 종목마다 로그인 실패 배너·경고를
        뿜지 않도록 단축한다 (yfinance KR 은 PER/PBR 미제공 → per/pbr 은 None 유지,
        valuation 팩터는 peg/psr/ev_ebitda sector-relative 로 폴백).
        """
        from stock_compass.utils.krx_auth import apply_krx_credentials, krx_quiet

        if not apply_krx_credentials():
            return {}  # KRX 자격증명 없음 — 정확 PER/PBR 조회 불가, 조용히 skip

        try:
            with krx_quiet():
                from pykrx.stock import (
                    get_market_fundamental,
                    get_nearest_business_day_in_a_week,
                )

                day = get_nearest_business_day_in_a_week()
                df = get_market_fundamental(day, day, code)
            if df.empty:
                return {}
            row = df.iloc[-1]
            return {
                "per": float(row["PER"]) if row.get("PER") and row["PER"] != 0 else None,
                "pbr": float(row["PBR"]) if row.get("PBR") and row["PBR"] != 0 else None,
            }
        except (ConnectionError, TimeoutError, ValueError, KeyError, IndexError, OSError) as e:
            _logger.warning("pykrx fundamental 실패: %s (%s)", code, e)
            return {}

    # ─── news ───

    def get_news(self, ticker: str, *, days: int = 30) -> list[News]:
        """KR 뉴스. Naver Open API (있으면) → yfinance (KR 종목엔 거의 없음) fallback.

        Naver client id/secret 셋업 시 KR sentiment 신호 부활. 종목명 기준
        검색이라 `_kr_name(code)` 가 None 이면 Naver 경로 생략.
        """
        code = self._normalize_code(ticker)
        # 1) Naver 시도 (CLAUDE.md 정상 흐름)
        naver_items = _fetch_news_naver(code, days=days)
        if naver_items:
            return naver_items

        # 2) yfinance fallback — KR 종목엔 응답 거의 없으나 미국 상장 KR ADR 등
        symbol = self.to_yfinance_symbol(code)
        try:
            raw = UsAdapter()._fetch_news(symbol)
        except (ConnectionError, TimeoutError, OSError) as e:
            _logger.warning("KR news 실패: %s (%s)", code, e)
            return []
        cutoff = datetime.now(UTC) - timedelta(days=days)
        out: list[News] = []
        for item in raw:
            content: dict[str, Any] = item.get("content", item) if isinstance(item, dict) else {}
            if not content:
                continue
            ts = content.get("pubDate") or content.get("providerPublishTime")
            published = _parse_news_time(ts) if ts is not None else None
            if published is None or published < cutoff:
                continue
            title = str(content.get("title", "")).strip()
            url = str(
                (content.get("canonicalUrl") or {}).get("url")
                or (content.get("clickThroughUrl") or {}).get("url")
                or content.get("link", "")
            )
            if not title or not url:
                continue
            out.append(News(title=title, url=url, published_at=published))
        return out

    # ─── Naver 뉴스 ───
    # 정의는 클래스 밖 헬퍼로 — _kr_name(code) lookup 의존성 분리

    # ─── 분기 재무 (백필용) — DART 우선 → yfinance fallback ───

    def get_quarterly_financials(self, ticker: str) -> QuarterlyFinancials:
        """KR 종목 분기 재무.

        1순위: DART OpenAPI (finstate) — 정기보고서 4종 fetch + 누적 차감 (Q4)
        2순위: yfinance .KS — KR 분기 데이터는 가용성 낮음
        둘 다 빈 결과면 빈 객체 → V/F backfill_skip 폴백.
        """
        code = self._normalize_code(ticker)
        from stock_compass.markets._kr_dart_financials import (
            fetch_kr_quarterly_via_dart,
        )

        qf = fetch_kr_quarterly_via_dart(code)
        if not qf.is_empty():
            return qf

        # DART 빈 결과 → yfinance fallback
        return UsAdapter().get_quarterly_financials(code).model_copy(
            update={"ticker": code, "market": "KR"}
        )

    # ─── disclosures (DART) ───

    def get_disclosures(self, ticker: str, *, days: int = 30) -> list[Disclosure]:
        if settings.dart_api_key is None:
            return []
        code = self._normalize_code(ticker)
        end = datetime.now(UTC).date()
        start = end - timedelta(days=days)
        cache_name = f"dart-{code}-{end.strftime('%Y%m%d')}-{days}"
        cached = load_json(cache_name, _DISCLOSURE_TTL)
        if cached is not None:
            return [Disclosure(**d) for d in cached]
        items = self._fetch_disclosures_dart(code, start, end)
        save_json(cache_name, [d.model_dump(mode="json") for d in items])
        return items

    def _fetch_disclosures_dart(
        self, code: str, start: object, end: object
    ) -> list[Disclosure]:
        try:
            import OpenDartReader
        except ImportError:
            _logger.warning("OpenDartReader 미설치 — 공시 생략")
            return []

        api_key = settings.dart_api_key
        if api_key is None:
            return []
        try:
            dart = OpenDartReader(api_key.get_secret_value())
            df = dart.list(code, start=str(start), end=str(end))
        except (ConnectionError, TimeoutError, ValueError, OSError) as e:
            _logger.warning("DART 호출 실패: %s (%s)", code, e)
            return []
        if df is None or df.empty:
            return []
        from stock_compass.utils.disclosure_classifier import classify

        out: list[Disclosure] = []
        for _, row in df.iterrows():
            try:
                published = datetime.strptime(str(row.get("rcept_dt", "")), "%Y%m%d").replace(
                    tzinfo=UTC
                )
            except ValueError:
                continue
            title = str(row.get("report_nm", ""))
            report_code = str(row.get("pblntf_ty", "")) or None
            out.append(
                Disclosure(
                    rcept_no=str(row.get("rcept_no", "")),
                    title=title,
                    published_at=published,
                    report_code=report_code,
                    url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row.get('rcept_no')}",
                    kind=classify(title, report_code),
                )
            )
        return out
