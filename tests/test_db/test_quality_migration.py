"""migration 008 — factor_scores CHECK +quality, 뷰 quality_score, 업그레이드 보존."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stock_compass.db.migrations import MIGRATIONS, migrate


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    migrate(p)
    return p


def test_quality_factor_name_allowed(db_path: Path) -> None:
    c = sqlite3.connect(db_path)
    c.execute(
        "INSERT INTO tickers (code,market,name,currency,yfinance_symbol) "
        "VALUES ('AAA','US','AAA','USD','AAA')"
    )
    tid = c.execute("SELECT id FROM tickers WHERE code='AAA'").fetchone()[0]
    c.execute(
        "INSERT INTO factor_scores (ticker_id,date,factor_name,score,weight) "
        "VALUES (?,?,?,?,?)",
        (tid, "2026-05-29", "quality", 70.0, 0.10),
    )
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO factor_scores (ticker_id,date,factor_name,score,weight) "
            "VALUES (?,?,?,?,?)",
            (tid, "2026-05-29", "bogus", 70.0, 0.10),
        )


def test_views_expose_quality_columns(db_path: Path) -> None:
    c = sqlite3.connect(db_path)
    for view in ("v_latest_scores", "v_score_history"):
        cols = [d[0] for d in c.execute(f"SELECT * FROM {view} LIMIT 0").description]
        assert "quality_score" in cols, f"{view} missing quality_score"
    # raw Quality 지표도 v_latest_scores 에 노출
    lcols = [d[0] for d in c.execute("SELECT * FROM v_latest_scores LIMIT 0").description]
    assert {"debt_to_equity", "current_ratio", "roa"} <= set(lcols)


def test_v_at_date_exposes_quality(db_path: Path) -> None:
    from datetime import date

    from stock_compass.screener.views import build_v_at_date_ctes

    c = sqlite3.connect(db_path)
    ctes = build_v_at_date_ctes(date(2026, 5, 29))
    cols = [
        d[0]
        for d in c.execute(f"WITH {ctes} SELECT * FROM _vad LIMIT 0").description
    ]
    assert "quality_score" in cols
    assert {"debt_to_equity", "current_ratio", "roa"} <= set(cols)


def test_upgrade_v7_to_v8_preserves_factor_scores(tmp_path: Path) -> None:
    """기존 v7 DB(quality 이전) → v8 재생성 시 factor_scores 데이터 보존."""
    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    for m in MIGRATIONS:
        if m.version <= 7:
            conn.executescript(m.sql)
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (m.version,)
            )
            conn.commit()
    conn.execute(
        "INSERT INTO tickers (code,market,name,currency,yfinance_symbol) "
        "VALUES ('OLD','US','Old','USD','OLD')"
    )
    tid = conn.execute("SELECT id FROM tickers WHERE code='OLD'").fetchone()[0]
    for fn in ("valuation", "fundamentals", "technical", "macro", "sentiment"):
        conn.execute(
            "INSERT INTO factor_scores (ticker_id,date,factor_name,score,weight) "
            "VALUES (?,?,?,?,?)",
            (tid, "2026-05-01", fn, 55.0, 0.2),
        )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM factor_scores").fetchone()[0]
    conn.close()

    # 정식 migrate 로 v8 적용
    assert migrate(p) == 8
    conn = sqlite3.connect(p)
    after = conn.execute("SELECT COUNT(*) FROM factor_scores").fetchone()[0]
    assert after == before == 5
    # quality 이제 삽입 가능
    conn.execute(
        "INSERT INTO factor_scores (ticker_id,date,factor_name,score,weight) "
        "VALUES (?,?,?,?,?)",
        (tid, "2026-05-01", "quality", 60.0, 0.10),
    )
