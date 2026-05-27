"""backtest_results 테이블 CRUD — Phase 7-5 백테스트 결과 영구 저장.

dashboard history / 비교 / audit 용도. 매 backtest 실행마다 1 row 추가.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stock_compass.screener.backtest import BacktestResult


@dataclass(frozen=True, slots=True)
class BacktestRow:
    id: int
    run_at: str  # ISO
    preset_name: str | None
    sql_text: str
    start_date: str
    end_date: str
    rebalance: str
    forward_periods: list[str]
    limit_per_round: int
    rounds_count: int
    total_picks: int
    stats: dict[str, dict[str, float | None]]
    rounds_summary: list[dict[str, Any]] | None


def save_backtest_result(
    conn: sqlite3.Connection,
    result: BacktestResult,
    *,
    limit_per_round: int,
) -> int:
    """BacktestResult를 backtest_results에 1 row insert. 새 id 반환."""
    stats_dict = {
        period: {
            "avg": result.stats.avg_return.get(period),
            "median": result.stats.median_return.get(period),
            "hit_rate": result.stats.hit_rate.get(period),
            "worst": result.stats.worst_return.get(period),
        }
        for period in result.forward_periods
    }
    rounds_summary = [
        {
            "as_of": r.as_of.isoformat(),
            "picks": len(r.selected),
            "avg_returns_by_period": {
                period: _avg_for_period(r.forward_returns, period)
                for period in result.forward_periods
            },
        }
        for r in result.rounds
    ]
    cur = conn.execute(
        """
        INSERT INTO backtest_results
          (preset_name, sql_text, start_date, end_date, rebalance,
           forward_periods, limit_per_round, rounds_count, total_picks,
           stats, rounds_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.preset_name,
            result.preset_sql,
            result.start.isoformat(),
            result.end.isoformat(),
            result.rebalance,
            json.dumps(list(result.forward_periods)),
            limit_per_round,
            result.stats.rounds_count,
            result.stats.total_picks,
            json.dumps(stats_dict, default=str),
            json.dumps(rounds_summary, default=str),
        ),
    )
    return int(cur.lastrowid or 0)


def list_backtest_results(
    conn: sqlite3.Connection,
    *,
    preset_name: str | None = None,
    limit: int = 50,
) -> list[BacktestRow]:
    """최근 backtest 실행 목록 — dashboard history."""
    if preset_name:
        rows = conn.execute(
            """
            SELECT * FROM backtest_results
            WHERE preset_name = ?
            ORDER BY run_at DESC LIMIT ?
            """,
            (preset_name, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM backtest_results ORDER BY run_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_backtest(r) for r in rows]


def get_backtest_result(
    conn: sqlite3.Connection, backtest_id: int
) -> BacktestRow | None:
    row = conn.execute(
        "SELECT * FROM backtest_results WHERE id = ?", (backtest_id,)
    ).fetchone()
    return _row_to_backtest(row) if row else None


def _row_to_backtest(row: sqlite3.Row) -> BacktestRow:
    return BacktestRow(
        id=int(row["id"]),
        run_at=row["run_at"],
        preset_name=row["preset_name"],
        sql_text=row["sql_text"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        rebalance=row["rebalance"],
        forward_periods=json.loads(row["forward_periods"]),
        limit_per_round=int(row["limit_per_round"]),
        rounds_count=int(row["rounds_count"]),
        total_picks=int(row["total_picks"]),
        stats=json.loads(row["stats"]),
        rounds_summary=(
            json.loads(row["rounds_summary"]) if row["rounds_summary"] else None
        ),
    )


def _avg_for_period(
    forward_returns: dict[str, dict[str, float | None]], period: str
) -> float | None:
    vals = [
        v for tr in forward_returns.values() if (v := tr.get(period)) is not None
    ]
    return sum(vals) / len(vals) if vals else None
