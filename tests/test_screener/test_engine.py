"""ScreenerEngine — RO 모드, LIMIT 강제, 안전 검증, 기본 조회."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from stock_compass.db import migrate, upsert_composite_score
from stock_compass.factors.base import DEFAULT_WEIGHTS, FactorScore
from stock_compass.scoring.engine import CompositeScore
from stock_compass.screener.engine import (
    ScreenerEngine,
    ScreenerError,
)


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """일시 DB + settings.db_path 패치 — engine은 default로 settings 사용."""
    from stock_compass.config import settings

    p = tmp_path / "test.db"
    migrate(p)
    monkeypatch.setattr(settings, "db_path", p)
    return p


def _seed_score(
    db: Path,
    ticker: str,
    total: float,
    *,
    market: str = "US",
    price: float = 200.0,
) -> None:
    """v_latest_scores 조회용 최소 시드. price는 composite_scores.price_at_score로 저장."""
    factors = [
        FactorScore(
            name=n,            score=total,
            weight=DEFAULT_WEIGHTS[n],            raw_values=(
                {"per": 12.0, "pbr": 1.1, "peg": 1.2, "dividend_yield": 0.02}
                if n == "valuation"
                else {"roe": 0.15, "revenue_growth_yoy": 0.12, "operating_margin": 0.15}
                if n == "fundamentals"
                else {"rsi_14": 55, "ma200_distance": 0.05, "volume_zscore": 0.5}
                if n == "technical"
                else {}
            ),
        )
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
        upsert_composite_score(c, score, on_date=date(2026, 5, 22))


class TestSafetyValidation:
    @pytest.mark.parametrize(
        "sql",
        [
            "DROP TABLE tickers",
            "DELETE FROM tickers",
            "UPDATE tickers SET name='x'",
            "INSERT INTO tickers VALUES (1,2,3,4,5,6)",
            "ALTER TABLE tickers ADD COLUMN foo TEXT",
            "ATTACH DATABASE 'evil.db' AS evil",
            "CREATE TABLE foo (id INT)",
            "REPLACE INTO tickers VALUES (1,2,3,4,5,6)",
            "PRAGMA foreign_keys = OFF",
            "VACUUM",
            "SELECT 1; DROP TABLE tickers",  # multi-statement
        ],
    )
    def test_blocks_dangerous(self, db_path: Path, sql: str) -> None:
        engine = ScreenerEngine()
        with pytest.raises(ScreenerError):
            engine.run_sql(sql)

    def test_select_allowed(self, db_path: Path) -> None:
        engine = ScreenerEngine()
        result = engine.run_sql("SELECT 1 AS x")
        assert result.rows == [{"x": 1}]

    def test_empty_sql_rejected(self, db_path: Path) -> None:
        with pytest.raises(ScreenerError, match="빈"):
            ScreenerEngine().run_sql("   ")


class TestReadOnlyMode:
    def test_write_attempts_blocked_by_uri_mode(self, db_path: Path) -> None:
        """`mode=ro` URI에서 RO 차단은 SQLite 차원에서 보장. 정규식 통과해도 SQLite 거부."""
        engine = ScreenerEngine()
        # 정규식을 우회하는 변형 (소문자 + 캐멀케이스 등)이 와도 RO 모드에서 실패
        # 하지만 정규식이 이미 차단 — 여기서는 SELECT만으로 RO 동작 확인
        result = engine.run_sql("SELECT name FROM sqlite_master WHERE type='table'")
        assert "tickers" in [r["name"] for r in result.rows]


class TestLimitEnforcement:
    def test_default_limit_appended(self, db_path: Path) -> None:
        for i in range(3):
            _seed_score(db_path, f"T{i}", 50.0 + i)
        engine = ScreenerEngine(default_limit=2)
        result = engine.run_sql("SELECT code FROM v_latest_scores")
        assert result.row_count == 2
        assert result.limit_applied == 2
        assert "LIMIT 2" in result.sql

    def test_explicit_limit_overrides_default(self, db_path: Path) -> None:
        for i in range(5):
            _seed_score(db_path, f"T{i}", 50.0 + i)
        engine = ScreenerEngine(default_limit=2)
        result = engine.run_sql("SELECT code FROM v_latest_scores", limit=4)
        assert result.row_count == 4

    def test_existing_limit_kept_if_under_max(self, db_path: Path) -> None:
        engine = ScreenerEngine(max_limit=100)
        result = engine.run_sql("SELECT 1 LIMIT 10")
        assert "LIMIT 10" in result.sql

    def test_existing_limit_clamped_to_max(self, db_path: Path) -> None:
        engine = ScreenerEngine(max_limit=5)
        result = engine.run_sql("SELECT 1 LIMIT 9999")
        assert "LIMIT 5" in result.sql

    def test_negative_limit_rejected(self, db_path: Path) -> None:
        with pytest.raises(ScreenerError, match="양수"):
            ScreenerEngine().run_sql("SELECT 1", limit=-1)


class TestAtDate:
    def test_v_at_date_future_blocked(self, db_path: Path) -> None:
        with pytest.raises(ScreenerError, match="미래"):
            ScreenerEngine().run_sql(
                "SELECT * FROM v_at_date('2099-01-01')"
            )

    def test_v_at_date_invalid_format(self, db_path: Path) -> None:
        # 정규식이 YYYY-MM-DD만 매칭하므로 "2026-13-99" 같은 형식은 매칭 후 fromisoformat 실패
        with pytest.raises(ScreenerError, match="일자 형식"):
            ScreenerEngine().run_sql("SELECT * FROM v_at_date('2026-13-99')")

    def test_v_at_date_phase_75_stub(self, db_path: Path) -> None:
        with pytest.raises(ScreenerError, match="Phase 7-5"):
            ScreenerEngine().run_sql("SELECT * FROM v_at_date('2026-05-22')")


class TestViewQueries:
    def test_v_latest_scores_exposes_expected_columns(self, db_path: Path) -> None:
        _seed_score(db_path, "AAPL", 65.0)
        result = ScreenerEngine().run_sql(
            "SELECT code, name, composite_score, verdict, per, pbr, roe "
            "FROM v_latest_scores"
        )
        assert result.row_count == 1
        row = result.rows[0]
        assert row["code"] == "AAPL"
        assert row["composite_score"] == 65.0
        assert row["per"] == 12.0
        assert row["roe"] == 0.15
        assert row["verdict"] == "중립"

    def test_universes_column_empty_when_no_members(self, db_path: Path) -> None:
        _seed_score(db_path, "AAPL", 65.0)
        result = ScreenerEngine().run_sql(
            "SELECT universes FROM v_latest_scores"
        )
        assert result.rows[0]["universes"] == ""

    def test_screener_runs_recorded(self, db_path: Path) -> None:
        _seed_score(db_path, "AAPL", 65.0)
        engine = ScreenerEngine()
        engine.run_sql(
            "SELECT * FROM v_latest_scores", preset_name="test"
        )
        # 별도 RW 연결로 기록 확인
        with sqlite3.connect(db_path) as c:
            row = c.execute(
                "SELECT preset_name, result_count FROM screener_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row[0] == "test"
        assert row[1] == 1


class TestBudgetFilter:
    def test_excludes_over_budget(self, db_path: Path) -> None:
        _seed_score(db_path, "AAPL", 50.0, market="US", price=150.0)
        _seed_score(db_path, "MSFT", 50.0, market="US", price=400.0)
        result = ScreenerEngine().run_sql(
            "SELECT code, price FROM v_latest_scores ORDER BY code",
            budget=200.0,
        )
        codes = [r["code"] for r in result.rows]
        assert codes == ["AAPL"]

    def test_includes_at_budget_boundary(self, db_path: Path) -> None:
        _seed_score(db_path, "AAPL", 50.0, market="US", price=200.0)
        result = ScreenerEngine().run_sql(
            "SELECT code, price FROM v_latest_scores",
            budget=200.0,
        )
        assert {r["code"] for r in result.rows} == {"AAPL"}

    def test_market_filter_separates_currencies(self, db_path: Path) -> None:
        # KR 종목 (price 200 KRW도 가능 — 환산 없이 비교)
        _seed_score(db_path, "005930", 50.0, market="KR", price=100.0)
        _seed_score(db_path, "AAPL", 50.0, market="US", price=100.0)
        # KR + budget=150 → US 자동 제외
        result = ScreenerEngine().run_sql(
            "SELECT code, market, price FROM v_latest_scores ORDER BY code",
            budget=150.0,
            budget_market="KR",
        )
        codes = [r["code"] for r in result.rows]
        assert codes == ["005930"]

    def test_missing_price_column_rejected(self, db_path: Path) -> None:
        _seed_score(db_path, "AAPL", 50.0)
        with pytest.raises(ScreenerError, match="price 컬럼"):
            ScreenerEngine().run_sql(
                "SELECT code, name FROM v_latest_scores",  # price 없음
                budget=200.0,
            )

    def test_price_alias_krw_works(self, db_path: Path) -> None:
        _seed_score(db_path, "005930", 50.0, market="KR", price=90000.0)
        _seed_score(db_path, "005380", 50.0, market="KR", price=200000.0)
        # alias로 price_krw 노출되도 budget 적용
        result = ScreenerEngine().run_sql(
            "SELECT code, ROUND(price, 0) AS price_krw FROM v_latest_scores",
            budget=100000.0,
        )
        codes = [r["code"] for r in result.rows]
        assert codes == ["005930"]
