"""Dashboard _data 헬퍼 — streamlit-free, 순수 DB 조회 검증."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from stock_compass.dashboard import _data
from stock_compass.db import (
    insert_trade,
    migrate,
    upsert_composite_score,
    upsert_ticker,
)
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.scoring.engine import CompositeScore


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from stock_compass.config import settings

    p = tmp_path / "test.db"
    migrate(p)
    monkeypatch.setattr(settings, "db_path", p)
    return p


def _mk_score(
    ticker: str,
    total: float,
    *,
    market: str = "US",
    sector: str = "Tech",
    price: float = 100.0,
) -> CompositeScore:
    factors = [
        FactorScore(name=n, score=total, weight=DEFAULT_WEIGHTS[n])
        for n in DEFAULT_WEIGHTS
    ]
    return CompositeScore(
        ticker=ticker,
        market=market,  # type: ignore[arg-type]
        total_score=total,
        verdict="관심권" if total >= 70 else "중립" if total >= 50 else "주의",
        factors=factors,
        computed_at=datetime.now(UTC),
        price_at_score=price,
        currency="USD" if market == "US" else "KRW",
        name=f"{ticker} Inc.",
        sector=sector,
        yfinance_symbol=ticker,
    )


class TestOverview:
    def test_empty_db_returns_empty(self, db_path: Path) -> None:
        assert _data.fetch_overview() == []

    def test_returns_today_scores(self, db_path: Path) -> None:
        from stock_compass.utils.dates import today_kst

        with sqlite3.connect(db_path, isolation_level=None) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            upsert_composite_score(
                c, _mk_score("AAPL", 75.0), on_date=today_kst()
            )
        rows = _data.fetch_overview()
        assert len(rows) == 1
        assert rows[0].code == "AAPL"
        assert rows[0].total_score == 75.0
        assert rows[0].delta is None  # 어제 데이터 없음

    def test_delta_against_yesterday(self, db_path: Path) -> None:
        from stock_compass.utils.dates import today_kst

        today = today_kst()
        yesterday = today - timedelta(days=1)
        with sqlite3.connect(db_path, isolation_level=None) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            upsert_composite_score(c, _mk_score("AAPL", 60.0), on_date=yesterday)
            upsert_composite_score(c, _mk_score("AAPL", 75.0), on_date=today)
        rows = _data.fetch_overview()
        assert rows[0].delta == pytest.approx(15.0)

    def test_sector_rank(self, db_path: Path) -> None:
        from stock_compass.utils.dates import today_kst

        today = today_kst()
        with sqlite3.connect(db_path, isolation_level=None) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            upsert_composite_score(c, _mk_score("AAPL", 80.0, sector="Tech"), on_date=today)
            upsert_composite_score(c, _mk_score("MSFT", 60.0, sector="Tech"), on_date=today)
        rows = _data.fetch_overview()
        aapl = next(r for r in rows if r.code == "AAPL")
        assert aapl.sector_rank == (1, 2)


class TestSectorAverages:
    def test_returns_grouped_averages(self, db_path: Path) -> None:
        from stock_compass.utils.dates import today_kst

        today = today_kst()
        with sqlite3.connect(db_path, isolation_level=None) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            upsert_composite_score(c, _mk_score("AAPL", 80.0, sector="Tech"), on_date=today)
            upsert_composite_score(c, _mk_score("MSFT", 60.0, sector="Tech"), on_date=today)
            upsert_composite_score(c, _mk_score("JPM", 50.0, sector="Fin"), on_date=today)
        rows = _data.fetch_sector_averages()
        by_sector = {r["sector"]: r for r in rows}
        assert by_sector["Tech"]["n"] == 2
        assert by_sector["Tech"]["avg_score"] == pytest.approx(70.0)
        assert by_sector["Fin"]["n"] == 1


class TestListTickers:
    def test_returns_only_with_scores(self, db_path: Path) -> None:
        with sqlite3.connect(db_path, isolation_level=None) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            # 점수 있는 종목
            upsert_composite_score(c, _mk_score("AAPL", 60.0), on_date=date(2024, 1, 1))
            # 점수 없이 ticker 만 (혹시 universe 등록만 된 경우)
            upsert_ticker(
                c,
                code="ORPHAN",
                market="US",
                name=None,
                sector=None,
                currency="USD",
                yfinance_symbol="ORPHAN",
            )
        items = _data.list_tickers_with_history()
        codes = {code for code, _, _ in items}
        assert codes == {"AAPL"}  # ORPHAN 제외


class TestHindsightFetch:
    def test_passthrough(self, db_path: Path) -> None:
        with sqlite3.connect(db_path, isolation_level=None) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            upsert_composite_score(
                c,
                _mk_score("AAPL", 70.0, price=100.0),
                on_date=date(2024, 1, 1),
            )
            insert_trade(
                c,
                ticker="AAPL",
                market="US",
                side="buy",
                price=100.0,
                qty=10,
                executed_at=datetime(2024, 1, 1, tzinfo=UTC),
                score_at_trade=70.0,
            )
        rows, summary = _data.fetch_hindsight(days=3650, forward_days=(30,))
        assert len(rows) == 1
        assert summary["total"] == 1


class TestDbMetadata:
    def test_empty(self, db_path: Path) -> None:
        meta = _data.db_metadata()
        assert meta["score_count"] == 0
        assert meta["trade_count"] == 0
        assert meta["latest_score_date"] is None

    def test_populated(self, db_path: Path) -> None:
        with sqlite3.connect(db_path, isolation_level=None) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            upsert_composite_score(c, _mk_score("AAPL", 60.0), on_date=date(2024, 1, 5))
        meta = _data.db_metadata()
        assert meta["score_count"] == 1
        assert meta["latest_score_date"] == "2024-01-05"
