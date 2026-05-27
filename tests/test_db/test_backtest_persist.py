"""backtest_results CRUD — round-trip + Markdown 렌더 + CLI 옵션."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from stock_compass.db import (
    get_backtest_result,
    list_backtest_results,
    migrate,
    save_backtest_result,
)
from stock_compass.output.backtest_md import render_backtest_note
from stock_compass.screener.backtest import (
    BacktestResult,
    BacktestRound,
    BacktestStats,
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "test.db"
    migrate(db)
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _mk_result(
    *,
    preset_name: str = "test_preset",
    forward_periods: tuple[str, ...] = ("1m", "3m"),
) -> BacktestResult:
    rounds = [
        BacktestRound(
            as_of=date(2024, 1, 1),
            selected=[{"code": "AAPL", "market": "US"}],
            forward_returns={"AAPL": {"1m": 0.05, "3m": 0.12}},
        ),
        BacktestRound(
            as_of=date(2024, 2, 1),
            selected=[{"code": "MSFT", "market": "US"}],
            forward_returns={"MSFT": {"1m": -0.03, "3m": 0.08}},
        ),
    ]
    stats = BacktestStats(
        rounds_count=2,
        total_picks=2,
        avg_return={"1m": 0.01, "3m": 0.10},
        median_return={"1m": 0.01, "3m": 0.10},
        hit_rate={"1m": 0.5, "3m": 1.0},
        worst_return={"1m": -0.03, "3m": 0.08},
    )
    return BacktestResult(
        preset_sql="SELECT * FROM v_at_date(:as_of) WHERE composite_score >= 60",
        preset_name=preset_name,
        start=date(2024, 1, 1),
        end=date(2024, 3, 1),
        rebalance="monthly",
        forward_periods=forward_periods,
        rounds=rounds,
        stats=stats,
    )


class TestSaveAndRetrieve:
    def test_round_trip(self, conn: sqlite3.Connection) -> None:
        result = _mk_result()
        row_id = save_backtest_result(conn, result, limit_per_round=15)
        assert row_id > 0

        retrieved = get_backtest_result(conn, row_id)
        assert retrieved is not None
        assert retrieved.preset_name == "test_preset"
        assert retrieved.rebalance == "monthly"
        assert retrieved.forward_periods == ["1m", "3m"]
        assert retrieved.rounds_count == 2
        assert retrieved.total_picks == 2
        assert retrieved.stats["1m"]["avg"] == 0.01
        assert retrieved.stats["3m"]["hit_rate"] == 1.0

    def test_rounds_summary_persisted(self, conn: sqlite3.Connection) -> None:
        row_id = save_backtest_result(
            conn, _mk_result(), limit_per_round=15
        )
        retrieved = get_backtest_result(conn, row_id)
        assert retrieved is not None
        assert retrieved.rounds_summary is not None
        assert len(retrieved.rounds_summary) == 2
        # 첫 라운드 평균 1m = 0.05 (단일 종목)
        assert (
            retrieved.rounds_summary[0]["avg_returns_by_period"]["1m"] == 0.05
        )

    def test_list_recent(self, conn: sqlite3.Connection) -> None:
        for name in ("preset_a", "preset_b", "preset_a"):
            save_backtest_result(
                conn, _mk_result(preset_name=name), limit_per_round=15
            )
        all_rows = list_backtest_results(conn)
        assert len(all_rows) == 3
        # preset_a 만 필터
        filtered = list_backtest_results(conn, preset_name="preset_a")
        assert len(filtered) == 2
        assert all(r.preset_name == "preset_a" for r in filtered)


class TestMarkdownRender:
    def test_contains_stats_table(self) -> None:
        md = render_backtest_note(_mk_result())
        assert "## 전체 통계" in md
        assert "Forward" in md
        assert "+1.0%" in md or "+1.00%" in md  # avg 1m = 1%
        assert "100.0%" in md  # hit rate 3m

    def test_contains_disclaimer(self) -> None:
        md = render_backtest_note(_mk_result())
        assert "매수 권유 아님" in md
        assert "survivorship" in md.lower() or "거래비용" in md

    def test_empty_rounds_handled(self) -> None:
        result = BacktestResult(
            preset_sql="SELECT 1",
            preset_name="empty",
            start=date(2024, 1, 1),
            end=date(2024, 12, 31),
            rebalance="monthly",
            forward_periods=("1m",),
            rounds=[],
            stats=BacktestStats(
                rounds_count=0, total_picks=0,
                avg_return={}, median_return={}, hit_rate={}, worst_return={},
            ),
        )
        md = render_backtest_note(result)
        assert "라운드 0개" in md
        assert "## 결과" in md


class TestCli:
    def test_backtest_help_lists_save_options(self) -> None:
        from typer.testing import CliRunner

        from stock_compass.commands._app import app

        runner = CliRunner()
        r = runner.invoke(app, ["backtest", "--help"])
        assert r.exit_code == 0
        assert "--save" in r.stdout
        assert "--publish-craft" in r.stdout
