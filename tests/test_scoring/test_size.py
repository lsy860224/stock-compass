"""Size 메타데이터 도출 — bucket 분류·KRW 환산·FX 조회 (Phase A a3)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_compass.scoring import size


class TestClassifySizeBucket:
    @pytest.mark.parametrize(
        ("cap_krw", "expected"),
        [
            (250_000_000_000_000.0, "mega"),
            (200_000_000_000_000.0, "mega"),  # 경계 (≥)
            (199_999_000_000_000.0, "large"),
            (10_000_000_000_000.0, "large"),  # 경계
            (9_999_000_000_000.0, "mid"),
            (2_000_000_000_000.0, "mid"),  # 경계
            (1_999_000_000_000.0, "small"),
            (300_000_000_000.0, "small"),  # 경계
            (299_000_000_000.0, "micro"),
            (50_000_000_000.0, "micro"),
        ],
    )
    def test_boundaries(self, cap_krw: float, expected: str) -> None:
        assert size.classify_size_bucket(cap_krw) == expected

    @pytest.mark.parametrize("bad", [None, 0.0, -1.0])
    def test_missing_or_nonpositive_returns_none(self, bad: float | None) -> None:
        assert size.classify_size_bucket(bad) is None


class TestToKrw:
    def test_kr_passthrough_ignores_fx(self) -> None:
        assert size.to_krw(5_000_000_000_000.0, "KR", None) == 5_000_000_000_000.0
        assert size.to_krw(5_000_000_000_000.0, "KR", 1350.0) == 5_000_000_000_000.0

    def test_us_applies_fx(self) -> None:
        assert size.to_krw(10_000_000_000.0, "US", 1350.0) == 13_500_000_000_000.0

    def test_us_without_fx_returns_none(self) -> None:
        assert size.to_krw(10_000_000_000.0, "US", None) is None
        assert size.to_krw(10_000_000_000.0, "US", 0.0) is None

    @pytest.mark.parametrize("bad", [None, 0.0, -3.0])
    def test_missing_cap_returns_none(self, bad: float | None) -> None:
        assert size.to_krw(bad, "KR", None) is None


class TestBuildTickerMeta:
    def test_us_large_cap(self) -> None:
        meta = size.build_ticker_meta(
            market="US", market_cap=10_000_000_000.0, usdkrw=1350.0
        )
        assert meta.market_cap == 10_000_000_000.0
        assert meta.market_cap_krw == 13_500_000_000_000.0
        assert meta.size_bucket == "large"

    def test_kr_small_cap_no_fx_needed(self) -> None:
        meta = size.build_ticker_meta(
            market="KR", market_cap=500_000_000_000.0, shares_outstanding=1_000.0
        )
        assert meta.market_cap_krw == 500_000_000_000.0
        assert meta.size_bucket == "small"
        assert meta.shares_outstanding == 1_000.0

    def test_us_without_fx_bucket_none(self) -> None:
        meta = size.build_ticker_meta(
            market="US", market_cap=10_000_000_000.0, usdkrw=None
        )
        assert meta.market_cap_krw is None
        assert meta.size_bucket is None


class TestUsdkrwAt:
    def _series(self) -> pd.Series:
        idx = pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"])
        return pd.Series([1300.0, 1320.0, 1340.0], index=idx)

    def test_picks_last_on_or_before(self) -> None:
        assert size.usdkrw_at(self._series(), date(2026, 2, 15)) == 1320.0

    def test_exact_date_inclusive(self) -> None:
        assert size.usdkrw_at(self._series(), date(2026, 3, 1)) == 1340.0

    def test_before_range_returns_none(self) -> None:
        assert size.usdkrw_at(self._series(), date(2025, 12, 1)) is None

    def test_empty_or_none_returns_none(self) -> None:
        assert size.usdkrw_at(pd.Series(dtype=float), date(2026, 1, 1)) is None
        assert size.usdkrw_at(None, date(2026, 1, 1)) is None


class TestCurrentUsdkrw:
    def test_returns_latest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from stock_compass.factors import macro

        idx = pd.to_datetime(["2026-05-01", "2026-05-28"])
        monkeypatch.setattr(
            macro, "fetch_full_series", lambda sid: pd.Series([1330.0, 1370.0], index=idx)
        )
        assert size.current_usdkrw() == 1370.0

    def test_empty_series_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from stock_compass.factors import macro

        monkeypatch.setattr(macro, "fetch_full_series", lambda sid: pd.Series(dtype=float))
        assert size.current_usdkrw() is None

    def test_exception_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from stock_compass.factors import macro

        def _boom(sid: str) -> pd.Series:
            raise RuntimeError("FRED down")

        monkeypatch.setattr(macro, "fetch_full_series", _boom)
        assert size.current_usdkrw() is None
