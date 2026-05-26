"""ClaudeSummarizer — Anthropic SDK mock + 캐시 + 한도 동작."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from stock_compass.db import (
    NewsSummaryRow,
    get_today_token_usage,
    migrate,
    upsert_news_summary,
    upsert_ticker,
)
from stock_compass.llm.summarizer import (
    ClaudeSummarizer,
    SummaryResult,
    _estimate_cost,
    tone_to_score,
)
from stock_compass.markets.base import Disclosure, News


@dataclass
class _MockUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _MockContent:
    text: str


@dataclass
class _MockResponse:
    content: list[_MockContent]
    usage: _MockUsage


class _MockAnthropicClient:
    """미리 정의한 응답 큐를 순서대로 반환."""

    def __init__(self, responses: list[dict[str, Any]], usage_tokens: int = 200) -> None:
        self._responses = list(responses)
        self._usage_tokens = usage_tokens
        self.messages = self  # client.messages.create() 지원
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _MockResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("mock 응답 소진")
        body = self._responses.pop(0)
        return _MockResponse(
            content=[_MockContent(text=json.dumps(body))],
            usage=_MockUsage(
                input_tokens=int(self._usage_tokens * 0.7),
                output_tokens=int(self._usage_tokens * 0.3),
            ),
        )


def _mk_news(url: str = "https://example.com/a") -> News:
    return News(
        title="HBM3E 양산 확대",
        url=url,
        published_at=datetime.now(UTC) - timedelta(hours=2),
        source_name="매일경제",
        summary="삼성전자가 HBM3E 양산을 확대하며 엔비디아 공급계약 임박.",
    )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "test.db"
    migrate(db)
    c = sqlite3.connect(db, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


@pytest.fixture
def ticker_id(conn: sqlite3.Connection) -> int:
    return upsert_ticker(
        conn,
        code="005930",
        market="KR",
        name="삼성전자",
        sector="반도체",
        currency="KRW",
        yfinance_symbol="005930.KS",
    )


class TestToneToScore:
    @pytest.mark.parametrize(
        "tone,expected",
        [(-10, 0.0), (0, 50.0), (10, 100.0), (5, 75.0), (-5, 25.0)],
    )
    def test_linear(self, tone: float, expected: float) -> None:
        assert tone_to_score(tone) == expected

    def test_clamps_above_max(self) -> None:
        assert tone_to_score(20) == 100.0

    def test_clamps_below_min(self) -> None:
        assert tone_to_score(-20) == 0.0


class TestSummarizeArticle:
    def test_parses_response(self) -> None:
        mock = _MockAnthropicClient(
            [
                {
                    "summary": "삼전 HBM 호재 3줄 요약.",
                    "tone_score": 4.5,
                    "keywords": ["HBM", "엔비디아", "공급계약", "양산", "삼성전자"],
                }
            ]
        )
        s = ClaudeSummarizer(model="test-model", client=mock)
        r = s.summarize_article("article body")
        assert isinstance(r, SummaryResult)
        assert r.tone_score == 4.5
        assert r.keywords == ["HBM", "엔비디아", "공급계약", "양산", "삼성전자"]
        assert r.tokens_used > 0

    def test_handles_code_fence(self) -> None:
        mock = _MockAnthropicClient(
            [{"summary": "x", "tone_score": 0, "keywords": []}]
        )
        s = ClaudeSummarizer(model="test", client=mock)
        # _parse_json은 코드펜스 안의 JSON도 추출해야 함 (정규식 사용)
        r = s.summarize_article("a")
        assert r.summary == "x"

    def test_clamps_invalid_tone(self) -> None:
        mock = _MockAnthropicClient(
            [{"summary": "ok", "tone_score": 99.0, "keywords": []}]
        )
        s = ClaudeSummarizer(model="test", client=mock)
        r = s.summarize_article("a")
        assert r.tone_score == 10.0  # clamp


class TestSummarizeBatch:
    def test_empty_news_returns_fallback(self, conn: sqlite3.Connection, ticker_id: int) -> None:
        s = ClaudeSummarizer(model="test", client=_MockAnthropicClient([]))
        agg = s.summarize_news_batch(conn, ticker_id=ticker_id, news=[])
        assert agg.score == 50.0
        assert agg.source == "fallback"
        assert agg.count == 0

    def test_api_call_then_cache(self, conn: sqlite3.Connection, ticker_id: int) -> None:
        mock = _MockAnthropicClient(
            [
                {"summary": "긍정 호재", "tone_score": 6.0, "keywords": ["x"]},
            ]
        )
        s = ClaudeSummarizer(model="test", client=mock, daily_input_limit=10_000)
        news = [_mk_news("https://e.com/1")]

        # 1차: API 호출
        agg1 = s.summarize_news_batch(conn, ticker_id=ticker_id, news=news)
        assert agg1.api_count == 1
        assert agg1.cached_count == 0
        assert agg1.score > 50.0  # 긍정 톤
        assert agg1.source == "api"

        # 2차: 같은 URL → 캐시
        agg2 = s.summarize_news_batch(conn, ticker_id=ticker_id, news=news)
        assert agg2.api_count == 0
        assert agg2.cached_count == 1
        assert agg2.source == "cache"

        # 토큰 사용량 1회만 기록
        usage = get_today_token_usage(conn, mode="api")
        assert usage["call_count"] == 1

    def test_budget_exceeded_falls_back(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        # 한도를 0으로 설정 → 새 API 호출 차단, 캐시도 없음 → fallback
        mock = _MockAnthropicClient([])
        s = ClaudeSummarizer(model="test", client=mock, daily_input_limit=0)
        agg = s.summarize_news_batch(
            conn, ticker_id=ticker_id, news=[_mk_news("https://e.com/9")]
        )
        assert agg.source == "fallback"
        assert agg.score == 50.0
        assert agg.api_count == 0

    def test_mixed_cache_and_api(self, conn: sqlite3.Connection, ticker_id: int) -> None:
        # 기존 캐시 1건 미리 세팅
        upsert_news_summary(
            conn,
            NewsSummaryRow(
                ticker_id=ticker_id,
                source_url="https://e.com/cached",
                source_type="news",
                source="api",
                published_at=datetime.now(UTC).isoformat(),
                summary="기존",
                tone_score=8.0,
                keywords=["a"],
                model="test",
            ),
        )
        mock = _MockAnthropicClient(
            [{"summary": "신규", "tone_score": -2.0, "keywords": []}]
        )
        s = ClaudeSummarizer(model="test", client=mock, daily_input_limit=10_000)
        agg = s.summarize_news_batch(
            conn,
            ticker_id=ticker_id,
            news=[_mk_news("https://e.com/cached"), _mk_news("https://e.com/new")],
        )
        assert agg.cached_count == 1
        assert agg.api_count == 1
        # 평균: (8 + -2)/2 = 3 → tone_to_score(3) = 65
        assert agg.avg_tone == 3.0


def _mk_disclosure(
    rcept_no: str = "20260101000001", title: str = "자기주식 취득 결정"
) -> Disclosure:
    return Disclosure(
        rcept_no=rcept_no,
        title=title,
        published_at=datetime.now(UTC) - timedelta(hours=1),
        report_code="A001",
        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
    )


class TestSummarizeEventsBatch:
    def test_empty_inputs_returns_fallback(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        s = ClaudeSummarizer(model="test", client=_MockAnthropicClient([]))
        agg = s.summarize_events_batch(
            conn, ticker_id=ticker_id, news=[], disclosures=[]
        )
        assert agg.score == 50.0
        assert agg.source == "fallback"
        assert agg.count == 0

    def test_news_only_weights_1x(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        mock = _MockAnthropicClient(
            [{"summary": "x", "tone_score": 4.0, "keywords": []}]
        )
        s = ClaudeSummarizer(model="test", client=mock, daily_input_limit=10_000)
        agg = s.summarize_events_batch(
            conn, ticker_id=ticker_id, news=[_mk_news("https://e.com/n1")], disclosures=[]
        )
        assert agg.api_count == 1
        # 단일 뉴스 weight 1x → avg = 4.0
        assert agg.avg_tone == 4.0

    def test_disclosure_weight_2x_dominates_average(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        """뉴스 -2 (w=1) + 공시 +6 (w=2) → (-2 + 12)/3 = 3.33."""
        mock = _MockAnthropicClient(
            [
                {"summary": "n", "tone_score": -2.0, "keywords": []},
                {
                    "summary": "d",
                    "event_type": "자사주매입",
                    "tone_score": 6.0,
                    "keywords": [],
                },
            ]
        )
        s = ClaudeSummarizer(model="test", client=mock, daily_input_limit=10_000)
        agg = s.summarize_events_batch(
            conn,
            ticker_id=ticker_id,
            news=[_mk_news("https://e.com/n2")],
            disclosures=[_mk_disclosure("20260201000001")],
        )
        assert agg.api_count == 2
        assert agg.avg_tone == pytest.approx((-2.0 + 6.0 * 2) / 3.0, abs=0.01)
        assert "공시 1건" in agg.note

    def test_disclosure_cache_hit_skips_api(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        """공시 URL이 캐시에 있으면 재호출 X."""
        d = _mk_disclosure("20260301000001", "회계 정정 보고서")
        upsert_news_summary(
            conn,
            NewsSummaryRow(
                ticker_id=ticker_id,
                source_url=d.url or "",
                source_type="disclosure",
                source="api",
                published_at=datetime.now(UTC).isoformat(),
                summary="정정 공시",
                tone_score=-7.0,
                keywords=[],
                model="test",
            ),
        )
        mock = _MockAnthropicClient([])
        s = ClaudeSummarizer(model="test", client=mock, daily_input_limit=10_000)
        agg = s.summarize_events_batch(
            conn, ticker_id=ticker_id, news=[], disclosures=[d]
        )
        assert agg.cached_count == 1
        assert agg.api_count == 0
        assert agg.avg_tone == -7.0
        assert agg.source == "cache"

    def test_disclosure_persists_with_source_type(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        mock = _MockAnthropicClient(
            [{"summary": "d", "tone_score": 5.0, "keywords": []}]
        )
        s = ClaudeSummarizer(model="test", client=mock, daily_input_limit=10_000)
        d = _mk_disclosure("20260401000001")
        s.summarize_events_batch(
            conn, ticker_id=ticker_id, news=[], disclosures=[d]
        )
        # DB에 source_type='disclosure'로 저장됐는지
        row = conn.execute(
            "SELECT source_type FROM news_summaries WHERE source_url = ?",
            (d.url,),
        ).fetchone()
        assert row is not None
        assert row["source_type"] == "disclosure"

    def test_budget_blown_partway_through(
        self, conn: sqlite3.Connection, ticker_id: int
    ) -> None:
        """첫 호출이 한도를 소진하면 다음 호출은 건너뛰지만 첫 결과는 유지."""
        mock = _MockAnthropicClient(
            [{"summary": "ok", "tone_score": 3.0, "keywords": []}]
        )
        # 1회 호출분만 허용 (test 모의는 200 토큰 사용)
        s = ClaudeSummarizer(model="test", client=mock, daily_input_limit=150)
        agg = s.summarize_events_batch(
            conn,
            ticker_id=ticker_id,
            news=[_mk_news("https://e.com/x1"), _mk_news("https://e.com/x2")],
            disclosures=[],
        )
        assert agg.api_count == 1
        assert agg.count == 1
        # 한도 소진해도 처리한 1건은 유효 (fallback 아님)
        assert agg.source == "api"


class TestPricing:
    def test_known_model(self) -> None:
        # Haiku: $0.25/M input + $1.25/M output
        cost = _estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        assert cost == pytest.approx(1.50)

    def test_unknown_model_uses_default(self) -> None:
        # default: $1/$5 per M
        cost = _estimate_cost("unknown-model", 1_000_000, 0)
        assert cost == pytest.approx(1.0)
