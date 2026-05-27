"""Phase 7-5 백테스트 — 리밸런싱 날짜 생성 + forward return + 통계."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from stock_compass.db import migrate, upsert_composite_score
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.scoring.engine import CompositeScore
from stock_compass.screener.backtest import (
    BacktestError,
    _monthly_first,
    _quarterly_first,
    _weekly_mondays,
    run_backtest,
)


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from stock_compass.config import settings

    p = tmp_path / "test.db"
    migrate(p)
    monkeypatch.setattr(settings, "db_path", p)
    return p


def _seed(
    db: Path,
    ticker: str,
    total: float,
    *,
    on_date: date,
    price: float,
    market: str = "US",
) -> None:
    factors = [
        FactorScore(name=n, score=total, weight=DEFAULT_WEIGHTS[n])
        for n in DEFAULT_WEIGHTS
    ]
    score = CompositeScore(
        ticker=ticker,
        market=market,  # type: ignore[arg-type]
        total_score=total,
        verdict="관심권" if total >= 70 else "중립" if total >= 50 else "주의",
        factors=factors,
        computed_at=datetime.now(UTC),
        price_at_score=price,
        currency="USD" if market == "US" else "KRW",
        name=f"{ticker} Inc.",
        sector="Tech",
        yfinance_symbol=ticker,
    )
    with sqlite3.connect(db, isolation_level=None) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        upsert_composite_score(c, score, on_date=on_date)


class TestRebalanceDates:
    def test_monthly_first(self) -> None:
        # 1/15 ~ 4/5 → 2/1, 3/1, 4/1
        out = _monthly_first(date(2024, 1, 15), date(2024, 4, 5))
        assert out == [date(2024, 2, 1), date(2024, 3, 1), date(2024, 4, 1)]

    def test_monthly_first_starts_on_first(self) -> None:
        # 시작이 이미 1일이면 그대로 포함
        out = _monthly_first(date(2024, 1, 1), date(2024, 3, 31))
        assert out == [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)]

    def test_monthly_first_year_boundary(self) -> None:
        out = _monthly_first(date(2024, 12, 1), date(2025, 2, 28))
        assert out == [date(2024, 12, 1), date(2025, 1, 1), date(2025, 2, 1)]

    def test_quarterly_first(self) -> None:
        out = _quarterly_first(date(2024, 2, 1), date(2024, 12, 31))
        # 4/1, 7/1, 10/1 (1/1은 start 이전)
        assert out == [date(2024, 4, 1), date(2024, 7, 1), date(2024, 10, 1)]

    def test_weekly_mondays(self) -> None:
        # 2024-01-01은 월요일
        out = _weekly_mondays(date(2024, 1, 1), date(2024, 1, 21))
        assert out == [date(2024, 1, 1), date(2024, 1, 8), date(2024, 1, 15)]


class TestBacktestEnd2End:
    def test_simple_round(self, db_path: Path) -> None:
        # 2024-01-01: AAPL 80점 / 가격 100
        # 2024-02-01: AAPL 가격 110 (+10%)
        _seed(db_path, "AAPL", 80.0, on_date=date(2024, 1, 1), price=100.0)
        _seed(db_path, "AAPL", 82.0, on_date=date(2024, 2, 1), price=110.0)
        result = run_backtest(
            "SELECT code, market, price, composite_score FROM v_at_date(:as_of) "
            "WHERE composite_score >= 70",
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            rebalance="monthly",
            forward_periods=("1m",),
        )
        assert result.stats.rounds_count == 1
        assert result.stats.total_picks == 1
        # AAPL: 100 → 110 = +10%
        assert result.stats.avg_return["1m"] == pytest.approx(0.10)
        assert result.stats.hit_rate["1m"] == 1.0

    def test_excludes_low_scores(self, db_path: Path) -> None:
        _seed(db_path, "LOW", 45.0, on_date=date(2024, 1, 1), price=50.0)
        _seed(db_path, "HIGH", 75.0, on_date=date(2024, 1, 1), price=100.0)
        _seed(db_path, "HIGH", 75.0, on_date=date(2024, 2, 1), price=120.0)
        result = run_backtest(
            "SELECT code, market, price, composite_score FROM v_at_date(:as_of) "
            "WHERE composite_score >= 70",
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            rebalance="monthly",
            forward_periods=("1m",),
        )
        # LOW는 점수 미달 → 선정 X, HIGH만
        assert result.stats.total_picks == 1
        assert list(result.rounds[0].forward_returns) == ["HIGH"]

    def test_forward_return_missing_when_no_future(
        self, db_path: Path
    ) -> None:
        # base 시드만 있고 future 데이터 없음 → forward None
        _seed(db_path, "AAPL", 80.0, on_date=date(2024, 1, 1), price=100.0)
        result = run_backtest(
            "SELECT code, market, price, composite_score FROM v_at_date(:as_of) "
            "WHERE composite_score >= 70",
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            rebalance="monthly",
            forward_periods=("1m",),
        )
        # 종목은 선정됐지만 forward None → stats avg None
        assert result.stats.total_picks == 1
        assert result.stats.avg_return["1m"] is None
        assert result.rounds[0].forward_returns["AAPL"]["1m"] is None

    def test_negative_return_and_hit_rate(self, db_path: Path) -> None:
        # 2종목: WINNER 100→120, LOSER 100→80 → 평균 +0%, 적중률 50%
        for ticker, fwd_price in [("WINNER", 120.0), ("LOSER", 80.0)]:
            _seed(db_path, ticker, 75.0, on_date=date(2024, 1, 1), price=100.0)
            _seed(db_path, ticker, 75.0, on_date=date(2024, 2, 1), price=fwd_price)
        result = run_backtest(
            "SELECT code, market, price, composite_score FROM v_at_date(:as_of) "
            "WHERE composite_score >= 70",
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            rebalance="monthly",
            forward_periods=("1m",),
        )
        assert result.stats.avg_return["1m"] == pytest.approx(0.0)
        assert result.stats.hit_rate["1m"] == 0.5
        assert result.stats.worst_return["1m"] == pytest.approx(-0.20)


class TestInputValidation:
    def test_missing_as_of_placeholder(self, db_path: Path) -> None:
        with pytest.raises(BacktestError, match=":as_of"):
            run_backtest(
                "SELECT * FROM v_latest_scores",  # :as_of 없음
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
            )

    def test_start_after_end(self, db_path: Path) -> None:
        with pytest.raises(BacktestError, match=">="):
            run_backtest(
                "SELECT * FROM v_at_date(:as_of)",
                start=date(2024, 6, 1),
                end=date(2024, 1, 1),
            )

    def test_unsupported_forward_period(self, db_path: Path) -> None:
        with pytest.raises(BacktestError, match="미지원 forward"):
            run_backtest(
                "SELECT * FROM v_at_date(:as_of)",
                start=date(2024, 1, 1),
                end=date(2024, 6, 1),
                forward_periods=("99y",),
            )

    def test_unsupported_rebalance(self, db_path: Path) -> None:
        with pytest.raises(BacktestError, match="미지원 rebalance"):
            run_backtest(
                "SELECT * FROM v_at_date(:as_of)",
                start=date(2024, 1, 1),
                end=date(2024, 6, 1),
                rebalance="daily",
            )

    def test_empty_rebalance_dates(self, db_path: Path) -> None:
        # 너무 짧은 기간 (monthly인데 1/15 ~ 1/30 → 다음 달 1일 없음 → 0개)
        with pytest.raises(BacktestError, match="0개"):
            run_backtest(
                "SELECT * FROM v_at_date(:as_of)",
                start=date(2024, 1, 15),
                end=date(2024, 1, 30),
                rebalance="monthly",
            )


class TestCli:
    def test_backtest_help_lists_options(self) -> None:
        from typer.testing import CliRunner

        from stock_compass.commands._app import app

        runner = CliRunner()
        r = runner.invoke(app, ["backtest", "--help"])
        assert r.exit_code == 0
        for opt in ("--preset", "--start", "--end", "--rebalance", "--forward"):
            assert opt in r.stdout
