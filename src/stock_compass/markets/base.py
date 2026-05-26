"""시장 어댑터 추상 인터페이스 + 공통 데이터 모델."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, time
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

Market = Literal["KR", "US"]
Currency = Literal["KRW", "USD"]


class PriceHistory(BaseModel):
    """일별 OHLCV 시계열.

    df.index: DatetimeIndex (거래일 종가 기준).
    df.columns 최소: open, high, low, close, volume, adj_close.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    ticker: str
    market: Market
    currency: Currency
    source: str = Field(description="yfinance / pykrx / cache")
    df: pd.DataFrame

    def is_empty(self) -> bool:
        return bool(self.df.empty)


class Fundamentals(BaseModel):
    """단일 종목 펀더멘털 스냅샷. 누락 빈번 — 모두 Optional."""

    ticker: str
    market: Market
    currency: Currency
    name: str | None = None
    sector: str | None = None
    per: float | None = None
    forward_per: float | None = None
    pbr: float | None = None
    peg: float | None = None
    dividend_yield: float | None = None
    roe: float | None = Field(default=None, description="0~1 scale (yfinance 기본)")
    revenue_growth_yoy: float | None = Field(default=None, description="0.15 = +15%")
    earnings_growth_yoy: float | None = None
    operating_margin: float | None = None
    profit_margin: float | None = None
    free_cash_flow: float | None = None
    market_cap: float | None = None
    # Valuation Range용 (Phase A)
    trailing_eps: float | None = None
    forward_eps: float | None = None
    book_value: float | None = None  # BPS
    dividend_per_share: float | None = None
    source: str = "yfinance"


class News(BaseModel):
    title: str
    url: str
    published_at: datetime
    source_name: str | None = None
    summary: str | None = None


class Disclosure(BaseModel):
    """DART 한국 공시 (US는 사용 X)."""

    rcept_no: str
    title: str
    published_at: datetime
    report_code: str | None = None
    url: str | None = None


class MarketAdapter(ABC):
    """시장별 데이터 소스 어댑터.

    KR/US 모두 동일 인터페이스를 따라야 점수 엔진이 시장 무관하게 호출 가능.
    구현체는 retry·캐시·fallback을 내부에서 처리한다.
    """

    market: Market

    @abstractmethod
    def get_price_history(self, ticker: str, *, period: str = "1y") -> PriceHistory:
        """일봉 OHLCV. period는 yfinance 표기 ('1mo','6mo','1y','2y','5y','max')."""

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> Fundamentals:
        """PER/PBR/ROE 등. 누락 필드는 None — 호출자가 방어적으로 사용."""

    @abstractmethod
    def get_news(self, ticker: str, *, days: int = 30) -> list[News]:
        """최근 N일 뉴스. 실패 시 빈 리스트."""

    @abstractmethod
    def get_disclosures(self, ticker: str, *, days: int = 30) -> list[Disclosure]:
        """KR 공시 (US 구현체는 [] 반환)."""

    @abstractmethod
    def get_trading_hours(self) -> tuple[time, time]:
        """장 시작·종료 (해당 시장 현지 시각)."""

    @abstractmethod
    def get_currency(self) -> Currency: ...

    def to_yfinance_symbol(self, ticker: str) -> str:
        """기본은 그대로. KrAdapter에서 오버라이드 (.KS/.KQ 접미사)."""
        return ticker
