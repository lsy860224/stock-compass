"""tickers 테이블 — 종목 마스터 upsert + sentiment source 정규화."""

from __future__ import annotations

import sqlite3

from stock_compass.markets.base import Market

_ALLOWED_SENTIMENT_SOURCE = {"api", "manual_prompt", "fallback", "cache", "placeholder"}


def upsert_ticker(
    conn: sqlite3.Connection,
    *,
    code: str,
    market: Market,
    name: str | None,
    sector: str | None,
    currency: str,
    yfinance_symbol: str,
) -> int:
    """tickers 테이블에 upsert 후 id 반환.

    name=None이면 INSERT 시 code를 fallback으로 사용하되 기존 name은 덮어쓰지 않음
    (워치리스트 자동 갱신이 score/batch가 채워놓은 정식 종목명을 잃지 않도록).
    """
    if name is None:
        conn.execute(
            """
            INSERT INTO tickers (code, market, name, sector, currency, yfinance_symbol)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(market, code) DO UPDATE SET
              sector = COALESCE(excluded.sector, tickers.sector),
              currency = excluded.currency,
              yfinance_symbol = excluded.yfinance_symbol
            """,
            (code, market, code, sector, currency, yfinance_symbol),
        )
    else:
        conn.execute(
            """
            INSERT INTO tickers (code, market, name, sector, currency, yfinance_symbol)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(market, code) DO UPDATE SET
              name = excluded.name,
              sector = COALESCE(excluded.sector, tickers.sector),
              currency = excluded.currency,
              yfinance_symbol = excluded.yfinance_symbol
            """,
            (code, market, name, sector, currency, yfinance_symbol),
        )
    row = conn.execute(
        "SELECT id FROM tickers WHERE market = ? AND code = ?", (market, code)
    ).fetchone()
    return int(row["id"])


def get_ticker_id(conn: sqlite3.Connection, code: str, market: Market) -> int | None:
    row = conn.execute(
        "SELECT id FROM tickers WHERE market = ? AND code = ?", (market, code)
    ).fetchone()
    return int(row["id"]) if row else None


def normalize_sentiment_source(source: str) -> str:
    """sentiment 팩터의 source를 composite_scores CHECK 제약에 맞춰 변환."""
    if source in _ALLOWED_SENTIMENT_SOURCE:
        return source
    return "placeholder"
