"""미국 시장 어댑터 (yfinance 단일 소스)."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from stock_compass.markets.base import (
    Currency,
    Disclosure,
    Fundamentals,
    MarketAdapter,
    News,
    PriceHistory,
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
            dividend_yield=_get(info, "dividendYield", as_=float),
            roe=_get(info, "returnOnEquity", as_=float),
            revenue_growth_yoy=_get(info, "revenueGrowth", as_=float),
            earnings_growth_yoy=_get(info, "earningsGrowth", as_=float),
            operating_margin=_get(info, "operatingMargins", as_=float),
            profit_margin=_get(info, "profitMargins", as_=float),
            free_cash_flow=_get(info, "freeCashflow", as_=float),
            market_cap=_get(info, "marketCap", as_=float),
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
