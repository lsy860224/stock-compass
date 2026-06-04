"""DB 기반 추적 종목 (watchlists 테이블) — .env 워치리스트의 영속 확장.

`.env` WATCHLIST_KR/US 는 "core" 기본 추적이고, 이 테이블은 그 외 종목을
group 별로 영속 추적한다(수동 추가·discover 발굴 등). 일일 배치가 .env + 이
테이블을 합쳐 채점하므로, 등록 즉시 점수 히스토리가 쌓인다.

group_name: 'discover' / 'manual' / 사용자 정의. added_by: provenance.
"""

from __future__ import annotations

import sqlite3

from stock_compass.markets.base import Market


def track_ticker(
    conn: sqlite3.Connection,
    *,
    code: str,
    market: Market,
    group: str,
    added_by: str,
    notes: str | None = None,
) -> bool:
    """종목을 tickers에 upsert(placeholder) 후 추적 그룹에 등록. 신규면 True.

    name/sector는 None placeholder — 첫 배치 채점이 정식 메타로 채움. KR yfinance
    심볼은 .KS 기본(KrAdapter가 런타임 .KQ 재시도). 추적·discover·screen 공용 진입점.
    """
    from stock_compass.db.tickers import upsert_ticker

    ticker_id = upsert_ticker(
        conn,
        code=code,
        market=market,
        name=None,
        sector=None,
        currency="KRW" if market == "KR" else "USD",
        yfinance_symbol=code.upper() if market == "US" else f"{code}.KS",
    )
    return add_tracked(
        conn, ticker_id=ticker_id, group=group, added_by=added_by, notes=notes
    )


def add_tracked(
    conn: sqlite3.Connection,
    *,
    ticker_id: int,
    group: str,
    added_by: str,
    notes: str | None = None,
) -> bool:
    """추적 등록. 이미 (ticker, group) 있으면 notes/added_by 갱신. 신규면 True."""
    cur = conn.execute(
        """
        INSERT INTO watchlists (ticker_id, group_name, added_by, notes)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(ticker_id, group_name) DO UPDATE SET
          added_by = excluded.added_by,
          notes = COALESCE(excluded.notes, watchlists.notes)
        """,
        (ticker_id, group, added_by, notes),
    )
    return cur.rowcount > 0 and cur.lastrowid is not None


def remove_tracked(
    conn: sqlite3.Connection,
    *,
    ticker_id: int,
    group: str | None = None,
) -> int:
    """추적 해제. group 지정 시 해당 그룹만, 미지정 시 전 그룹. 삭제 행수 반환."""
    if group is None:
        cur = conn.execute(
            "DELETE FROM watchlists WHERE ticker_id = ?", (ticker_id,)
        )
    else:
        cur = conn.execute(
            "DELETE FROM watchlists WHERE ticker_id = ? AND group_name = ?",
            (ticker_id, group),
        )
    return cur.rowcount


def list_tracked(
    conn: sqlite3.Connection, *, group: str | None = None
) -> list[dict[str, object]]:
    """추적 종목 목록 (tickers JOIN) — code/market/name/group/added_by/notes/added_at."""
    sql = """
        SELECT t.code, t.market, t.name, t.sector,
               w.group_name, w.added_by, w.notes, w.added_at
        FROM watchlists w
        JOIN tickers t ON t.id = w.ticker_id
        {where}
        ORDER BY w.group_name, t.market, t.code
    """
    if group:
        rows = conn.execute(
            sql.format(where="WHERE w.group_name = ?"), (group,)
        ).fetchall()
    else:
        rows = conn.execute(sql.format(where="")).fetchall()
    return [dict(r) for r in rows]


def get_tracked_targets(
    conn: sqlite3.Connection, *, groups: list[str] | None = None
) -> list[tuple[str, Market]]:
    """추적 종목의 distinct (code, market) — 배치 채점 대상. groups 미지정 시 전체."""
    if groups:
        placeholders = ",".join("?" * len(groups))
        rows = conn.execute(
            f"""
            SELECT DISTINCT t.code, t.market
            FROM watchlists w JOIN tickers t ON t.id = w.ticker_id
            WHERE w.group_name IN ({placeholders})
            ORDER BY t.market, t.code
            """,
            tuple(groups),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT t.code, t.market
            FROM watchlists w JOIN tickers t ON t.id = w.ticker_id
            ORDER BY t.market, t.code
            """
        ).fetchall()
    return [(str(r["code"]), str(r["market"])) for r in rows]  # type: ignore[misc]


def count_tracked_by_group(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """group 별 추적 종목 수 (요약용)."""
    rows = conn.execute(
        """
        SELECT group_name, COUNT(*) AS n
        FROM watchlists GROUP BY group_name ORDER BY group_name
        """
    ).fetchall()
    return [(str(r["group_name"]), int(r["n"])) for r in rows]
