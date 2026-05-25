"""discover 명령 — screener row → batch 변환 + 빈 결과 처리."""

from __future__ import annotations

from typing import Any

import pytest

from stock_compass.cli import _screener_rows_to_targets


class TestScreenerRowsToTargets:
    def test_extracts_code_and_market(self) -> None:
        rows: list[dict[str, Any]] = [
            {"code": "AAPL", "market": "US", "composite_score": 70},
            {"code": "005930", "market": "KR", "composite_score": 65},
        ]
        out = _screener_rows_to_targets(rows, default_market=None)
        assert out == [("AAPL", "US"), ("005930", "KR")]

    def test_ticker_alias(self) -> None:
        rows: list[dict[str, Any]] = [
            {"ticker": "MSFT", "market": "US"},
        ]
        out = _screener_rows_to_targets(rows, default_market=None)
        assert out == [("MSFT", "US")]

    def test_default_market_fallback(self) -> None:
        rows: list[dict[str, Any]] = [
            {"code": "TSLA"},  # market 컬럼 없음
            {"code": "NVDA", "market": ""},
        ]
        out = _screener_rows_to_targets(rows, default_market="US")
        assert out == [("TSLA", "US"), ("NVDA", "US")]

    def test_invalid_market_skipped(self) -> None:
        rows: list[dict[str, Any]] = [
            {"code": "AAPL", "market": "US"},
            {"code": "X", "market": "JP"},  # 미지원
            {"code": "Y"},  # default 없음
        ]
        out = _screener_rows_to_targets(rows, default_market=None)
        assert out == [("AAPL", "US")]

    def test_empty_rows(self) -> None:
        assert _screener_rows_to_targets([], default_market="US") == []

    def test_missing_ticker_skipped(self) -> None:
        rows: list[dict[str, Any]] = [
            {"market": "US"},
            {"code": "", "market": "US"},
            {"code": "AAPL", "market": "US"},
        ]
        out = _screener_rows_to_targets(rows, default_market=None)
        assert out == [("AAPL", "US")]


@pytest.mark.parametrize(
    "code,market,expected_ok",
    [
        ("AAPL", "US", True),
        ("005930", "KR", True),
        ("X", "JP", False),
        ("", "US", False),
    ],
)
def test_parametrized(
    code: str, market: str, expected_ok: bool
) -> None:
    rows: list[dict[str, Any]] = [{"code": code, "market": market}]
    out = _screener_rows_to_targets(rows, default_market=None)
    assert (len(out) == 1) == expected_ok
