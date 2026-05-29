"""Phase 7-5 백필 — 시점별 Technical+Macro 점수 + DB upsert + backtest 호환."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_compass.db import migrate
from stock_compass.factors import macro as macro_factor
from stock_compass.factors import technical as technical_factor
from stock_compass.scoring.backfill import _MIN_HISTORY_DAYS, run_backfill


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from stock_compass.config import settings

    p = tmp_path / "test.db"
    migrate(p)
    monkeypatch.setattr(settings, "db_path", p)
    return p


def _mk_ohlcv(days: int, *, start_price: float = 100.0, drift: float = 0.001) -> pd.DataFrame:
    """합성 OHLCV — drift 양수면 약한 상승 추세."""
    rng = np.random.default_rng(seed=42)
    dates = pd.bdate_range(end=datetime(2025, 12, 31), periods=days)
    returns = rng.normal(loc=drift, scale=0.015, size=days)
    closes = start_price * np.exp(np.cumsum(returns))
    volumes = rng.integers(1_000_000, 5_000_000, size=days).astype(float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": volumes,
            "adj_close": closes,
        },
        index=dates,
    )


class _FakeAdapter:
    market = "US"  # type: ignore[assignment]

    def __init__(self, ohlcv: pd.DataFrame) -> None:
        self._df = ohlcv

    def get_price_history(self, ticker: str, *, period: str = "5y"):  # type: ignore[no-untyped-def]
        from stock_compass.markets.base import PriceHistory

        return PriceHistory(
            ticker=ticker,
            market="US",
            currency="USD",
            source="yfinance",
            df=self._df,
        )

    def get_fundamentals(self, ticker: str):  # type: ignore[no-untyped-def]
        from stock_compass.markets.base import Fundamentals

        return Fundamentals(
            ticker=ticker,
            market="US",
            currency="USD",
            name="Test Inc.",
            sector="Tech",
        )

    def to_yfinance_symbol(self, ticker: str) -> str:
        return ticker


@pytest.fixture
def patch_external(monkeypatch: pytest.MonkeyPatch) -> None:
    """FRED 시계열·get_adapter·detect_market 외부 호출 mock."""
    # FRED: 빈 series → backfill 도 Macro None component
    monkeypatch.setattr(
        macro_factor, "fetch_full_series", lambda sid: pd.Series(dtype=float)
    )


@pytest.fixture
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> _FakeAdapter:
    # 500 거래일 ≈ 2년 — MA200 + 백필 범위 확보
    df = _mk_ohlcv(days=500)
    adapter = _FakeAdapter(df)
    monkeypatch.setattr(
        "stock_compass.scoring.backfill.get_adapter",
        lambda code, market=None: adapter,
    )
    monkeypatch.setattr(
        "stock_compass.scoring.backfill.detect_market", lambda code: "US"
    )
    return adapter


class TestTechnicalAtClose:
    def test_short_series_returns_neutral(self) -> None:
        close = pd.Series([float(i) for i in range(10)])
        volume = pd.Series([1000.0] * 10)
        fs = technical_factor.calculate_at_close(
            close, volume, as_of=date(2024, 1, 1)
        )
        assert fs.score == 50.0  # MA200 등 미산출 → neutral

    def test_returns_series_score(self) -> None:
        df = _mk_ohlcv(days=300)
        fs = technical_factor.calculate_at_close(
            df["close"], df["volume"], as_of=date(2025, 6, 1), source="backfill"
        )
        assert 0 <= fs.score <= 100
        assert fs.source == "backfill"
        assert fs.raw_values["data_points"] == 300


class TestMacroAtDate:
    def test_empty_series_returns_neutral(self) -> None:
        fs = macro_factor.calculate_at_date(
            "US",
            {
                "VIXCLS": pd.Series(dtype=float),
                "T10Y2Y": pd.Series(dtype=float),
                "DGS10": pd.Series(dtype=float),
            },
            as_of=date(2024, 6, 1),
        )
        assert fs.score == 50.0  # 모든 누락 → neutral

    def test_point_at_date_slices_correctly(self) -> None:
        dates = pd.date_range(start="2024-01-01", periods=100, freq="D")
        series = pd.Series(range(100), index=dates, dtype=float)
        # 30일자 시점 → 그 시점까지 slice → latest=29 (0-indexed)
        point = macro_factor._point_at_date(series, date(2024, 1, 30))
        assert point.latest == 29.0

    def test_point_at_date_excludes_future(self) -> None:
        dates = pd.date_range(start="2024-01-01", periods=100, freq="D")
        series = pd.Series(range(100), index=dates, dtype=float)
        # 6/1 시점 → 그 이후 데이터 제외
        point = macro_factor._point_at_date(series, date(2024, 1, 5))
        assert point.latest == 4.0  # 1/5 (idx 4)
        assert point.ma_90 == pytest.approx(2.0)  # 0,1,2,3,4 평균

    def test_calculate_at_date_with_series(self) -> None:
        dates = pd.date_range(start="2024-01-01", periods=200, freq="D")
        cache = {
            "VIXCLS": pd.Series(
                [15.0 + (i % 10) for i in range(200)], index=dates
            ),
            "T10Y2Y": pd.Series(
                [0.5 + (i * 0.01) for i in range(200)], index=dates
            ),
            "DGS10": pd.Series(
                [3.5 + (i * 0.005) for i in range(200)], index=dates
            ),
        }
        fs = macro_factor.calculate_at_date("US", cache, date(2024, 6, 1))
        assert 0 <= fs.score <= 100
        assert fs.source == "backfill"


class TestRunBackfill:
    def test_basic_backfill(
        self, db_path: Path, patch_external: None, fake_adapter: _FakeAdapter
    ) -> None:
        r = run_backfill(
            "TEST",
            "US",
            start=date(2025, 6, 1),
            end=date(2025, 12, 31),
        )
        assert r.days_processed > 0
        assert r.ticker == "TEST"
        assert r.market == "US"
        # composite_scores 에 행 생성됐는지 확인
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT COUNT(*) AS n FROM composite_scores"
            ).fetchone()
        assert row["n"] == r.days_processed

    def test_skip_insufficient_history(
        self, db_path: Path, patch_external: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 100 거래일만 — MA200 가능한 시점 없음 → 모두 skip
        df = _mk_ohlcv(days=100)
        adapter = _FakeAdapter(df)
        monkeypatch.setattr(
            "stock_compass.scoring.backfill.get_adapter",
            lambda code, market=None: adapter,
        )
        monkeypatch.setattr(
            "stock_compass.scoring.backfill.detect_market", lambda code: "US"
        )
        r = run_backfill(
            "SHORT", "US", start=date(2020, 1, 1), end=date(2025, 12, 31)
        )
        assert r.days_processed == 0
        assert r.days_skipped_insufficient_history > 0

    def test_skip_out_of_range(
        self, db_path: Path, patch_external: None, fake_adapter: _FakeAdapter
    ) -> None:
        # 데이터는 2024-2025년대인데 start/end가 2099 → 모두 OOR
        r = run_backfill(
            "TEST",
            "US",
            start=date(2099, 1, 1),
            end=date(2099, 12, 31),
        )
        assert r.days_processed == 0
        assert r.days_skipped_out_of_range > 0

    def test_start_after_end_raises(
        self, db_path: Path, patch_external: None, fake_adapter: _FakeAdapter
    ) -> None:
        with pytest.raises(ValueError, match=">"):
            run_backfill(
                "TEST",
                "US",
                start=date(2025, 12, 31),
                end=date(2025, 1, 1),
            )

    def test_backfill_makes_v_at_date_useful(
        self, db_path: Path, patch_external: None, fake_adapter: _FakeAdapter
    ) -> None:
        """백필 결과가 backtest 의 v_at_date(:as_of) 에서 즉시 인지되는지."""
        from stock_compass.screener.engine import ScreenerEngine

        run_backfill(
            "TEST", "US", start=date(2025, 6, 1), end=date(2025, 8, 1)
        )
        # 백필 중간 시점에 v_at_date 호출 — 그 시점 점수 반환
        result = ScreenerEngine().run_sql(
            "SELECT code, composite_score FROM v_at_date('2025-07-01')"
        )
        assert result.rows  # 그 시점에 데이터 있음
        assert result.rows[0]["code"] == "TEST"

    def test_backfill_writes_ticker_meta_when_shares_available(
        self, db_path: Path, patch_external: None, fake_adapter: _FakeAdapter
    ) -> None:
        """qf.shares_outstanding 있으면 시점별 ticker_meta(source='backfill') 적재."""
        from stock_compass.markets.base import (
            QuarterlyDatum,
            QuarterlyFinancials,
        )

        qf = QuarterlyFinancials(
            ticker="TEST",
            market="US",
            quarters=[
                QuarterlyDatum(
                    period_end=date(2025, 3, 31),
                    publish_after=date(2025, 5, 15),
                    revenue=1.0e9,
                    net_income=1.0e8,
                    equity=2.0e9,
                )
            ],
            shares_outstanding=1.0e8,
        )
        fake_adapter.get_quarterly_financials = lambda ticker: qf  # type: ignore[attr-defined]

        run_backfill("TEST", "US", start=date(2025, 6, 1), end=date(2025, 7, 1))
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT market_cap, size_bucket, source FROM ticker_meta"
            ).fetchall()
        assert rows, "ticker_meta 행이 생성돼야 함"
        assert all(r["source"] == "backfill" for r in rows)
        assert all(r["market_cap"] is not None for r in rows)

    def test_backfill_no_ticker_meta_without_shares(
        self, db_path: Path, patch_external: None, fake_adapter: _FakeAdapter
    ) -> None:
        """qf 없으면(=shares 없음) ticker_meta 미적재 — composite 만 기록."""
        run_backfill("TEST", "US", start=date(2025, 6, 1), end=date(2025, 7, 1))
        with sqlite3.connect(db_path) as c:
            n = c.execute("SELECT COUNT(*) FROM ticker_meta").fetchone()[0]
        assert n == 0

    def test_backfill_factor_sources_marked(
        self, db_path: Path, patch_external: None, fake_adapter: _FakeAdapter
    ) -> None:
        """factor_scores.raw_values 또는 note 에 backfill 표시 — 사용자가 인지 가능.

        composite_scores.sentiment_source 는 CHECK 제약 (api/manual_prompt/
        fallback/cache/placeholder) 에 맞춰 'placeholder' 로 정규화됨.
        factor_scores 의 note prefix '[backfill skip]' 으로 구분.
        """
        run_backfill(
            "TEST", "US", start=date(2025, 9, 1), end=date(2025, 9, 30)
        )
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            # composite_scores.sentiment_source — placeholder (normalize 결과)
            row = c.execute(
                "SELECT sentiment_source FROM composite_scores LIMIT 1"
            ).fetchone()
            assert row["sentiment_source"] == "placeholder"

            # factor_scores.note — backfill skip 명시 (valuation/fundamentals/sentiment)
            notes = [
                r["note"]
                for r in c.execute(
                    """
                    SELECT factor_name, note FROM factor_scores
                    WHERE factor_name IN ('valuation','fundamentals','sentiment')
                    LIMIT 3
                    """
                ).fetchall()
            ]
        assert all("backfill skip" in n for n in notes)


class TestCli:
    def test_backfill_help_lists_options(self) -> None:
        from typer.testing import CliRunner

        from stock_compass.commands._app import app

        runner = CliRunner()
        r = runner.invoke(app, ["backfill", "--help"])
        assert r.exit_code == 0
        for opt in ("--tickers", "--start", "--end", "--market"):
            assert opt in r.stdout

    def test_min_history_constant_aligned_with_ma200(self) -> None:
        # MA200 + 안전 마진이 200 일 — backtest 와 동일 가정
        assert _MIN_HISTORY_DAYS >= 200
