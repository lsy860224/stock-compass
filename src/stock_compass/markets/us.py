"""미국 시장 어댑터 (yfinance 단일 소스)."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from stock_compass.markets.base import (
    Currency,
    Disclosure,
    Fundamentals,
    MarketAdapter,
    News,
    PriceHistory,
    QuarterlyDatum,
    QuarterlyFinancials,
)
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


class UsAdapter(MarketAdapter):
    """yfinance를 1차 소스로 쓰는 미국 시장 어댑터."""

    market = "US"

    def to_yfinance_symbol(self, ticker: str) -> str:
        return ticker.upper()

    def get_currency(self) -> Currency:
        return "USD"

    def get_trading_hours(self) -> tuple[time, time]:
        return (time(9, 30), time(16, 0))

    # ─── price history ───

    def get_price_history(self, ticker: str, *, period: str = "1y") -> PriceHistory:
        symbol = self.to_yfinance_symbol(ticker)
        cache_name = f"{cache_key_for_today(symbol)}-{period}"
        df = load_dataframe(cache_name, _HISTORY_TTL)
        source = "cache"
        if df is None:
            df = self._fetch_history(symbol, period)
            source = "yfinance"
            if not df.empty:
                save_dataframe(cache_name, df)
        if df.empty:
            _logger.warning("US OHLCV 비어 있음: %s (period=%s)", symbol, period)
        return PriceHistory(
            ticker=ticker, market="US", currency="USD", source=source, df=df
        )

    @external_call_retry
    def _fetch_history(self, symbol: str, period: str) -> pd.DataFrame:
        import yfinance as yf

        t = yf.Ticker(symbol)
        df = t.history(period=period, auto_adjust=False)
        if df.empty:
            return df
        # 칼럼 정규화
        rename = {
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "Adj Close": "adj_close",
        }
        df = df.rename(columns=rename)
        # adj_close 없으면 close 복제
        if "adj_close" not in df.columns:
            df["adj_close"] = df["close"]
        return df[["open", "high", "low", "close", "volume", "adj_close"]]

    # ─── fundamentals ───

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        symbol = self.to_yfinance_symbol(ticker)
        cache_name = f"info-{cache_key_for_today(symbol)}"
        info: dict[str, Any] | None = load_json(cache_name, _INFO_TTL)
        if info is None:
            info = self._fetch_info(symbol)
            if info:
                save_json(cache_name, info)
        info = info or {}
        return Fundamentals(
            ticker=ticker,
            market="US",
            currency="USD",
            name=_get(info, "longName", "shortName", as_=str),
            sector=_get(info, "sector", as_=str),
            per=_get(info, "trailingPE", as_=float),
            forward_per=_get(info, "forwardPE", as_=float),
            pbr=_get(info, "priceToBook", as_=float),
            peg=_get(info, "pegRatio", "trailingPegRatio", as_=float),
            psr=_get(info, "priceToSalesTrailing12Months", as_=float),
            ev_ebitda=_get(info, "enterpriseToEbitda", as_=float),
            dividend_yield=_get(info, "dividendYield", as_=float),
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
            source="yfinance",
        )

    @external_call_retry
    def _fetch_info(self, symbol: str) -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(symbol)
        try:
            return dict(t.info or {})
        except (KeyError, ValueError, AttributeError) as e:
            _logger.warning("yfinance info 실패: %s (%s)", symbol, e)
            return {}

    # ─── news ───

    def get_news(self, ticker: str, *, days: int = 30) -> list[News]:
        symbol = self.to_yfinance_symbol(ticker)
        try:
            raw = self._fetch_news(symbol)
        except (ConnectionError, TimeoutError, OSError) as e:
            _logger.warning("US news 실패: %s (%s)", symbol, e)
            return []
        cutoff = datetime.now(UTC) - timedelta(days=days)
        out: list[News] = []
        for item in raw:
            content: dict[str, Any] = item.get("content", item) if isinstance(item, dict) else {}
            if not content:
                continue
            ts = content.get("pubDate") or content.get("providerPublishTime")
            if ts is None:
                continue
            published = _parse_news_time(ts)
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
            provider = (content.get("provider") or {}).get("displayName")
            out.append(
                News(
                    title=title,
                    url=url,
                    published_at=published,
                    source_name=str(provider) if provider else None,
                )
            )
        return out

    @external_call_retry
    def _fetch_news(self, symbol: str) -> list[dict[str, Any]]:
        import yfinance as yf

        return list(yf.Ticker(symbol).news or [])

    # ─── disclosures (US는 없음) ───

    def get_disclosures(self, ticker: str, *, days: int = 30) -> list[Disclosure]:
        _ = (ticker, days)
        return []

    # ─── 분기 재무 (백필용) ───

    def get_quarterly_financials(self, ticker: str) -> QuarterlyFinancials:
        """yfinance 분기 손익+대차+현금 → QuarterlyFinancials. 24h 캐시.

        실패 시 빈 객체. 공시 lag 45일 추정 (period_end + 45d → publish_after).
        """
        symbol = self.to_yfinance_symbol(ticker)
        cache_name = f"qfin-{cache_key_for_today(symbol)}"
        cached = load_json(cache_name, _HISTORY_TTL)
        if isinstance(cached, dict) and cached.get("quarters") is not None:
            try:
                return QuarterlyFinancials.model_validate(cached)
            except Exception:
                pass

        qf = self._fetch_quarterly(symbol, ticker)
        if not qf.is_empty():
            save_json(cache_name, qf.model_dump(mode="json"))
        return qf

    @external_call_retry
    def _fetch_quarterly(self, symbol: str, ticker: str) -> QuarterlyFinancials:
        import yfinance as yf

        try:
            t = yf.Ticker(symbol)
            income = _safe_df(t, "quarterly_income_stmt", "quarterly_financials")
            balance = _safe_df(t, "quarterly_balance_sheet")
            cashflow = _safe_df(t, "quarterly_cashflow")
            info = t.info or {}
        except (KeyError, ValueError, AttributeError, OSError) as e:
            _logger.warning("yfinance 분기 fetch 실패: %s (%s)", symbol, e)
            return QuarterlyFinancials(ticker=ticker, market=self.market)

        # 분기 종료일 = 컬럼 (income/balance/cashflow 공통 — 일부 불일치 있을 수 있음)
        period_ends: set[Any] = set()
        for df in (income, balance, cashflow):
            if df is not None and not df.empty:
                period_ends.update(df.columns.tolist())
        if not period_ends:
            return QuarterlyFinancials(ticker=ticker, market=self.market)
        sorted_ends = sorted(period_ends, reverse=True)

        quarters: list[QuarterlyDatum] = []
        for col in sorted_ends:
            period_end = _to_date(col)
            if period_end is None:
                continue
            quarters.append(
                QuarterlyDatum(
                    period_end=period_end,
                    publish_after=period_end + timedelta(days=45),
                    revenue=_row_value(income, "Total Revenue", col),
                    operating_income=_row_value(income, "Operating Income", col),
                    net_income=_row_value(income, "Net Income", col),
                    free_cash_flow=_row_value(cashflow, "Free Cash Flow", col),
                    equity=_row_value(balance, "Stockholders Equity", col),
                )
            )

        shares = _get(info, "sharesOutstanding", "impliedSharesOutstanding", as_=float)
        return QuarterlyFinancials(
            ticker=ticker,
            market=self.market,
            quarters=quarters,
            shares_outstanding=shares,
        )


def _safe_df(ticker: Any, *attrs: str) -> Any:
    """yfinance Ticker 의 분기 dataframe 속성 — 첫 비어있지 않은 결과. 없으면 None."""
    for attr in attrs:
        try:
            df = getattr(ticker, attr, None)
        except (KeyError, ValueError, AttributeError, OSError):
            continue
        if df is not None and not df.empty:
            return df
    return None


def _to_date(col: Any) -> date | None:
    """pandas Timestamp 또는 str 을 date 로."""
    try:
        if hasattr(col, "date"):
            return col.date()  # type: ignore[no-any-return]
        if isinstance(col, str):
            return datetime.fromisoformat(col.split(" ")[0]).date()
    except (ValueError, AttributeError):
        return None
    return None


def _row_value(df: Any, row_name: str, col: Any) -> float | None:
    """yfinance 분기 df 에서 [row_name, col] 값. 누락·NaN 시 None.

    income/balance/cashflow 가 서로 다른 분기 종료일을 보일 수 있어 합집합
    col 이 특정 df 에 없을 수 있음 → 그 경우도 None (KeyError 방지).
    row_name 변종 (예: 'Total Revenue' vs 'TotalRevenue' vs 'Revenue') 도 시도.
    """
    if df is None or df.empty or col not in df.columns:
        return None
    variants = [row_name, row_name.replace(" ", "")]
    aliases = {
        "Total Revenue": ["TotalRevenue", "Revenue"],
        "Operating Income": ["OperatingIncome"],
        "Net Income": ["NetIncome"],
        "Free Cash Flow": ["FreeCashFlow"],
        "Stockholders Equity": [
            "StockholdersEquity",
            "Total Stockholder Equity",
            "TotalStockholderEquity",
            "Common Stock Equity",
        ],
    }
    variants.extend(aliases.get(row_name, []))
    for name in variants:
        if name in df.index:
            try:
                v = df.loc[name, col]
            except KeyError:
                return None
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def _get[T](d: Mapping[str, Any], *keys: str, as_: Callable[[Any], T]) -> T | None:
    """dict에서 첫 번째로 발견되는 비-None·비-NaN 값을 타입 캐스팅."""
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        try:
            return as_(v)
        except (TypeError, ValueError):
            continue
    return None


def _parse_news_time(ts: object) -> datetime | None:
    if isinstance(ts, int | float):
        try:
            return datetime.fromtimestamp(float(ts), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
