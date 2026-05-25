"""PromptGenerator — 마크다운 템플릿 + batch_id 형식 + 분할 동작."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from stock_compass.markets.base import (
    Disclosure,
    Fundamentals,
    News,
    PriceHistory,
)
from stock_compass.markets.us import UsAdapter
from stock_compass.output.prompt_generator import (
    PromptGenerator,
    _build_batch_id,
)


class _FakeAdapter(UsAdapter):
    """adapter 호출을 결정적 데이터로 대체."""

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return Fundamentals(
            ticker=ticker,
            market="US",
            currency="USD",
            name=f"{ticker} Inc.",
            sector="Tech",
        )

    def get_news(self, ticker: str, *, days: int = 30) -> list[News]:
        return [
            News(
                title=f"{ticker} 호재 뉴스 #{i}",
                url=f"https://news.example.com/{ticker}/{i}",
                published_at=datetime(2026, 5, 20 + i, tzinfo=UTC),
                source_name="TestWire",
                summary=f"{ticker} 관련 짧은 본문 요약 #{i}.",
            )
            for i in range(2)
        ]

    def get_disclosures(self, ticker: str, *, days: int = 30) -> list[Disclosure]:
        return []

    def get_price_history(self, ticker: str, *, period: str = "1y") -> PriceHistory:
        import pandas as pd

        return PriceHistory(
            ticker=ticker, market="US", currency="USD", source="fake",
            df=pd.DataFrame(),
        )


@pytest.fixture
def generator(tmp_path: Path) -> PromptGenerator:
    return PromptGenerator(prompt_dir=tmp_path, tickers_per_file=3)


@pytest.fixture(autouse=True)
def _patch_adapter() -> Iterator[None]:
    """get_adapter가 _FakeAdapter를 돌려주도록 패치 — 실제 yfinance 호출 차단."""
    with patch(
        "stock_compass.output.prompt_generator.get_adapter",
        return_value=_FakeAdapter(),
    ):
        yield


class TestBatchId:
    def test_format(self) -> None:
        bid = _build_batch_id("sentiment")
        # YYYYMMDD-HHMMSS-sentiment-<6hex>
        assert re.match(r"^\d{8}-\d{6}-sentiment-[0-9a-f]{6}$", bid), bid

    def test_unique_across_calls(self) -> None:
        import time

        a = _build_batch_id("x")
        time.sleep(1.01)  # 초 단위 timestamp 보장 → 다른 batch_id
        b = _build_batch_id("x")
        assert a != b


class TestGenerate:
    def test_empty_tickers_raises(self, generator: PromptGenerator) -> None:
        with pytest.raises(ValueError, match="비어"):
            generator.generate_sentiment_prompt([])

    def test_single_file_when_small(self, generator: PromptGenerator) -> None:
        result = generator.generate_sentiment_prompt(
            [("AAPL", "US"), ("MSFT", "US")], days=7
        )
        assert result.file_count == 1
        assert result.ticker_count == 2
        content = result.path.read_text(encoding="utf-8")
        for required in (
            "# stock-compass — Sentiment 분석 요청",
            "Batch ID:",
            "## 작업 지시",
            "```json",
            "## 종목별 데이터",
            "AAPL",
            "MSFT",
            "면책",
        ):
            assert required in content, f"누락: {required}"

    def test_splits_when_over_limit(self, generator: PromptGenerator) -> None:
        # tickers_per_file=3 → 7개 → 3+3+1 = 3파일
        pairs = [(f"T{i}", "US") for i in range(7)]
        result = generator.generate_sentiment_prompt(pairs)  # type: ignore[arg-type]
        assert result.file_count == 3
        files = sorted(p for p in result.path.parent.iterdir())
        assert len(files) == 3
        for p in files:
            assert "part" in p.name

    def test_batch_id_in_response_template(self, generator: PromptGenerator) -> None:
        result = generator.generate_sentiment_prompt([("AAPL", "US")])
        content = result.path.read_text(encoding="utf-8")
        # 응답 형식에 동일 batch_id가 박혀 있어야 함 (사용자가 그대로 응답)
        assert f'"batch_id": "{result.batch_id}"' in content

    def test_per_ticker_news_section(self, generator: PromptGenerator) -> None:
        result = generator.generate_sentiment_prompt([("AAPL", "US")], days=7)
        content = result.path.read_text(encoding="utf-8")
        assert "최근 7일 뉴스 (2건)" in content
        assert "AAPL 호재 뉴스 #0" in content
        assert "https://news.example.com/AAPL/0" in content
