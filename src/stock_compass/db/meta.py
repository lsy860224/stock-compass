"""ticker_meta 테이블 — Size·시가총액 메타데이터 시계열 upsert·조회 (Phase A a3)."""

from __future__ import annotations

import sqlite3
from datetime import date as date_cls

from stock_compass.scoring.size import SizeBucket, TickerMeta


def upsert_ticker_meta(
    conn: sqlite3.Connection,
    *,
    ticker_id: int,
    as_of: date_cls,
    meta: TickerMeta,
    source: str = "batch",
) -> None:
    """(ticker_id, as_of_date) 단위 upsert. 호출자 트랜잭션 컨텍스트 그대로 사용."""
    conn.execute(
        """
        INSERT INTO ticker_meta
          (ticker_id, as_of_date, market_cap, market_cap_krw, size_bucket,
           shares_outstanding, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker_id, as_of_date) DO UPDATE SET
          market_cap = excluded.market_cap,
          market_cap_krw = excluded.market_cap_krw,
          size_bucket = excluded.size_bucket,
          shares_outstanding = excluded.shares_outstanding,
          source = excluded.source
        """,
        (
            ticker_id,
            as_of.isoformat(),
            meta.market_cap,
            meta.market_cap_krw,
            meta.size_bucket,
            meta.shares_outstanding,
            source,
        ),
    )


def get_latest_ticker_meta(
    conn: sqlite3.Connection, ticker_id: int
) -> TickerMeta | None:
    """해당 종목의 가장 최근 ticker_meta 1건 (없으면 None)."""
    row = conn.execute(
        """
        SELECT market_cap, market_cap_krw, size_bucket, shares_outstanding
        FROM ticker_meta
        WHERE ticker_id = ?
        ORDER BY as_of_date DESC
        LIMIT 1
        """,
        (ticker_id,),
    ).fetchone()
    if row is None:
        return None
    bucket: SizeBucket | None = row["size_bucket"]
    return TickerMeta(
        market_cap=row["market_cap"],
        market_cap_krw=row["market_cap_krw"],
        size_bucket=bucket,
        shares_outstanding=row["shares_outstanding"],
    )
