"""Repository — 스키마 생성·티커 upsert·점수 round-trip·이력 조회."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from stock_compass.db import (
    get_last_score,
    get_latest_scores,
    get_score_history,
    get_ticker_id,
    migrate,
    upsert_composite_score,
    upsert_ticker,
)
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.scoring.engine import CompositeScore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """일시 DB 파일 경로 + 마이그레이션."""
    p = tmp_path / "test.db"
    migrate(p)
    return p


@pytest.fixture
def conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(db_path, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _mk_factor(name: str, score: float, raw: dict[str, float] | None = None) -> FactorScore:
    return FactorScore(
        name=name,  # type: ignore[arg-type]
        score=score,
        weight=DEFAULT_WEIGHTS[name],  # type: ignore[index]
        raw_values=raw or {},
        note=f"test {name}",
    )


def _mk_composite(
    ticker: str = "AAPL",
    market: str = "US",
    total: float = 65.0,
    name: str | None = "Apple Inc.",
    when: datetime | None = None,
) -> CompositeScore:
    return CompositeScore(
        ticker=ticker,
        market=market,  # type: ignore[arg-type]
        total_score=total,
        verdict="중립",
        factors=[
            _mk_factor("valuation", 60.0, {"per": 25.0}),
            _mk_factor("fundamentals", 70.0),
            _mk_factor("technical", 55.0),
            _mk_factor("macro", 50.0),
            _mk_factor("sentiment", 50.0),
        ],
        computed_at=when or datetime.now(UTC),
        price_at_score=150.0,
        currency="USD" if market == "US" else "KRW",
        name=name,
        sector="Technology",
        yfinance_symbol=ticker,
    )


class TestMigrate:
    def test_creates_all_phase2_tables(self, db_path: Path) -> None:
        c = sqlite3.connect(db_path)
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r[0] for r in rows}
        for required in (
            "schema_version",
            "tickers",
            "snapshots",
            "factor_scores",
            "composite_scores",
            "news_summaries",
            "alerts",
            "trades",
        ):
            assert required in names, f"테이블 누락: {required}"

    def test_idempotent(self, db_path: Path) -> None:
        # 두 번째 migrate 호출은 no-op이어야 함
        v = migrate(db_path)
        assert v == 1

    def test_records_version(self, db_path: Path) -> None:
        c = sqlite3.connect(db_path)
        row = c.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == 1


class TestTickerUpsert:
    def test_inserts_new(self, conn: sqlite3.Connection) -> None:
        tid = upsert_ticker(
            conn,
            code="AAPL",
            market="US",
            name="Apple",
            sector="Tech",
            currency="USD",
            yfinance_symbol="AAPL",
        )
        assert tid > 0
        assert get_ticker_id(conn, "AAPL", "US") == tid

    def test_upsert_keeps_id(self, conn: sqlite3.Connection) -> None:
        a = upsert_ticker(
            conn,
            code="005930",
            market="KR",
            name="삼성전자",
            sector=None,
            currency="KRW",
            yfinance_symbol="005930.KS",
        )
        b = upsert_ticker(
            conn,
            code="005930",
            market="KR",
            name="삼성전자",
            sector="반도체",
            currency="KRW",
            yfinance_symbol="005930.KS",
        )
        assert a == b
        sector = conn.execute(
            "SELECT sector FROM tickers WHERE id = ?", (a,)
        ).fetchone()["sector"]
        assert sector == "반도체"

    def test_name_fallback_to_code(self, conn: sqlite3.Connection) -> None:
        tid = upsert_ticker(
            conn,
            code="NONAME",
            market="US",
            name=None,
            sector=None,
            currency="USD",
            yfinance_symbol="NONAME",
        )
        name = conn.execute(
            "SELECT name FROM tickers WHERE id = ?", (tid,)
        ).fetchone()["name"]
        assert name == "NONAME"


class TestCompositeRoundTrip:
    def test_upsert_persists_composite_and_factors(self, conn: sqlite3.Connection) -> None:
        score = _mk_composite()
        upsert_composite_score(conn, score, on_date=date(2026, 5, 22))

        comp = conn.execute(
            "SELECT * FROM composite_scores WHERE date = ?", ("2026-05-22",)
        ).fetchone()
        assert comp["total_score"] == 65.0
        assert comp["verdict"] == "중립"

        factor_rows = conn.execute(
            "SELECT factor_name, score, raw_values FROM factor_scores WHERE date = ?",
            ("2026-05-22",),
        ).fetchall()
        assert len(factor_rows) == 5
        names = {r["factor_name"] for r in factor_rows}
        assert names == {"valuation", "fundamentals", "technical", "macro", "sentiment"}
        val_raw = next(r["raw_values"] for r in factor_rows if r["factor_name"] == "valuation")
        assert "per" in val_raw  # JSON 보존

    def test_upsert_replaces_same_day(self, conn: sqlite3.Connection) -> None:
        s1 = _mk_composite(total=60.0)
        s2 = _mk_composite(total=75.0)
        upsert_composite_score(conn, s1, on_date=date(2026, 5, 22))
        upsert_composite_score(conn, s2, on_date=date(2026, 5, 22))
        comp = conn.execute(
            "SELECT total_score FROM composite_scores WHERE date = ?", ("2026-05-22",)
        ).fetchone()
        assert comp["total_score"] == 75.0


class TestQueries:
    def test_get_last_score_none_when_empty(self, conn: sqlite3.Connection) -> None:
        assert get_last_score(conn, "AAPL", "US") is None

    def test_get_last_score_returns_latest(self, conn: sqlite3.Connection) -> None:
        upsert_composite_score(conn, _mk_composite(total=60.0), on_date=date(2026, 5, 20))
        upsert_composite_score(conn, _mk_composite(total=70.0), on_date=date(2026, 5, 22))
        last = get_last_score(conn, "AAPL", "US")
        assert last is not None
        assert last.total_score == 70.0
        assert len(last.factors) == 5

    def test_history_ordering(self, conn: sqlite3.Connection) -> None:
        base = date(2026, 5, 1)
        for i in range(5):
            upsert_composite_score(
                conn,
                _mk_composite(total=50.0 + i),
                on_date=base + timedelta(days=i),
            )
        rows = get_score_history(conn, "AAPL", "US", days=10)
        assert len(rows) == 5
        assert rows[0].date == "2026-05-01"  # 오래된 → 최신
        assert rows[-1].date == "2026-05-05"
        assert rows[-1].factor_scores["valuation"] == 60.0

    def test_history_empty_when_no_ticker(self, conn: sqlite3.Connection) -> None:
        assert get_score_history(conn, "NOPE", "US") == []

    def test_latest_scores_ranks_descending(self, conn: sqlite3.Connection) -> None:
        d = date(2026, 5, 22)
        upsert_composite_score(conn, _mk_composite("AAPL", "US", total=60), on_date=d)
        upsert_composite_score(conn, _mk_composite("MSFT", "US", total=80), on_date=d)
        upsert_composite_score(
            conn, _mk_composite("005930", "KR", total=70), on_date=d
        )
        rows = get_latest_scores(conn)
        codes = [r.ticker for r in rows]
        assert codes == ["MSFT", "005930", "AAPL"]

    def test_latest_scores_market_filter(self, conn: sqlite3.Connection) -> None:
        d = date(2026, 5, 22)
        upsert_composite_score(conn, _mk_composite("AAPL", "US"), on_date=d)
        upsert_composite_score(conn, _mk_composite("005930", "KR"), on_date=d)
        us = get_latest_scores(conn, market="US")
        kr = get_latest_scores(conn, market="KR")
        assert [r.ticker for r in us] == ["AAPL"]
        assert [r.ticker for r in kr] == ["005930"]
