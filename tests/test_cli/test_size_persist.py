"""batch 경로의 ticker_meta 적재 — persist_size_metadata (Phase A a3)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_compass.commands._helpers import persist_size_metadata
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.scoring.engine import CompositeScore


def _score(
    ticker: str, market: str, market_cap: float | None, shares: float | None = None
) -> CompositeScore:
    factors = [
        FactorScore(
            name=n,  # type: ignore[arg-type]
            score=60.0,
            weight=DEFAULT_WEIGHTS[n],  # type: ignore[index]
            raw_values=(
                {"market_cap": market_cap, "shares_outstanding": shares}
                if n == "fundamentals"
                else {}
            ),
        )
        for n in DEFAULT_WEIGHTS
    ]
    return CompositeScore(
        ticker=ticker,
        market=market,  # type: ignore[arg-type]
        total_score=60.0,
        verdict="중립",
        factors=factors,
        computed_at=datetime.now(UTC),
        price_at_score=100.0,
        currency="USD" if market == "US" else "KRW",
        name=f"{ticker} Inc.",
        sector="Tech",
        yfinance_symbol=ticker,
    )


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, migrate, upsert_ticker

    p = tmp_path / "test.db"
    migrate(p)
    monkeypatch.setattr(settings, "db_path", p)
    # tickers 선등록 (persist_size_metadata 는 get_ticker_id 로 조회)
    with get_db_connection() as conn:
        upsert_ticker(
            conn, code="AAPL", market="US", name="Apple", sector="Tech",
            currency="USD", yfinance_symbol="AAPL",
        )
        upsert_ticker(
            conn, code="005930", market="KR", name="삼성전자", sector="Tech",
            currency="KRW", yfinance_symbol="005930.KS",
        )
    return p


def test_persists_us_and_kr_with_buckets(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stock_compass.scoring import size

    monkeypatch.setattr(size, "current_usdkrw", lambda: 1350.0)
    results = [
        _score("AAPL", "US", 10_000_000_000.0),  # $10B x1350 = 13.5조 → large
        _score("005930", "KR", 400_000_000_000_000.0),  # 400조 → mega
    ]
    persist_size_metadata(results)

    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        rows = {
            r["code"]: r
            for r in c.execute(
                """
                SELECT t.code, m.size_bucket, m.market_cap_krw, m.source
                FROM ticker_meta m JOIN tickers t ON m.ticker_id = t.id
                """
            ).fetchall()
        }
    assert rows["AAPL"]["size_bucket"] == "large"
    assert rows["AAPL"]["market_cap_krw"] == 13_500_000_000_000.0
    assert rows["AAPL"]["source"] == "batch"
    assert rows["005930"]["size_bucket"] == "mega"
    assert rows["005930"]["market_cap_krw"] == 400_000_000_000_000.0


def test_persists_shares_outstanding(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fundamentals raw_values 의 shares_outstanding 이 ticker_meta 에 적재되는지."""
    from stock_compass.scoring import size

    monkeypatch.setattr(size, "current_usdkrw", lambda: 1350.0)
    persist_size_metadata(
        [_score("005930", "KR", 400_000_000_000_000.0, shares=5_969_782_550.0)]
    )

    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT m.shares_outstanding FROM ticker_meta m").fetchone()
    assert row is not None
    assert row["shares_outstanding"] == 5_969_782_550.0


def test_skips_when_market_cap_missing(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stock_compass.scoring import size

    # market_cap 없으면 FX 조회조차 하지 않아야 함 — 호출 시 실패 유도
    def _boom() -> float:
        raise AssertionError("FX 조회가 호출되면 안 됨")

    monkeypatch.setattr(size, "current_usdkrw", _boom)
    persist_size_metadata([_score("AAPL", "US", None)])

    with sqlite3.connect(db) as c:
        n = c.execute("SELECT COUNT(*) FROM ticker_meta").fetchone()[0]
    assert n == 0


def test_kr_only_skips_fx_call(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stock_compass.scoring import size

    def _boom() -> float:
        raise AssertionError("KR-only batch 는 FX 조회 불필요")

    monkeypatch.setattr(size, "current_usdkrw", _boom)
    persist_size_metadata([_score("005930", "KR", 2_000_000_000_000.0)])  # 2조 → mid

    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT m.size_bucket FROM ticker_meta m"
        ).fetchone()
    assert row is not None and row["size_bucket"] == "mid"
