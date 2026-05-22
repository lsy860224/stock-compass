"""ClaudeSummarizer — 뉴스·공시 JSON 요약 (API 경로).

비용 안전장치: 일일 input 토큰 한도 초과 시 자동 fallback (50점 sentiment).
캐시: news_summaries 테이블 source_url UNIQUE — 동일 URL 재요약 차단.
면책: 모든 호출자가 "AI 생성 요약. 원문 확인 필수." footer를 노출하도록 강제.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from stock_compass.config import settings
from stock_compass.db import (
    NewsSummaryRow,
    get_cached_news_summary,
    get_today_token_usage,
    record_token_usage,
    upsert_news_summary,
)
from stock_compass.markets.base import News
from stock_compass.utils.dates import to_iso_utc
from stock_compass.utils.logging import get_logger

if TYPE_CHECKING:
    import sqlite3

_logger = get_logger(__name__)

# Anthropic 모델별 가격 ($/1M tokens). 누락 모델은 보수적 기본값.
# Phase 4 시점 기준 — 변동 시 갱신 필요.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.25, 1.25),
    "claude-haiku-4-5": (0.25, 1.25),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
}
_DEFAULT_PRICING = (1.0, 5.0)

DISCLOSURE_FOOTER = "AI 생성 요약. 원문 확인 필수."

SYSTEM_PROMPT_NEWS = """당신은 한국·미국 주식 시장의 금융 뉴스 분석가다.
다음 원칙 엄수:

1. 객관적 사실만 추출. 추측·예측 금지.
2. 원문 30단어 이상 그대로 인용 금지 (저작권).
3. 자체 표현으로 3줄 요약 (한국어).
4. 톤 점수 -10(매우 부정) ~ +10(매우 긍정) 부여:
   - +10: 어닝 서프라이즈·대형 호재
   - 0: 중립적 사실 보고
   - -10: 부도·회계 부정·상장폐지 위기
5. 핵심 키워드 5개 추출.
6. JSON 한 객체만 출력. 다른 텍스트(```, 인사말, 주석) 절대 금지.

출력 형식 (정확히 이 키만):
{
  "summary": "한국어 3줄 요약",
  "tone_score": 0.0,
  "keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"]
}"""


class SummaryLimitError(RuntimeError):
    """일일 토큰 한도 초과 — 호출자는 fallback 처리."""


@dataclass(frozen=True, slots=True)
class SummaryResult:
    """단일 뉴스 요약 결과 (DB 직렬화 전 도메인 객체)."""

    summary: str
    tone_score: float  # -10 ~ +10
    keywords: list[str]
    tokens_used: int
    model: str
    source: str = "api"  # 'api' / 'fallback' / 'cache'


@dataclass(frozen=True, slots=True)
class AggregatedSentiment:
    """다건 뉴스 평균 톤 — 0~100 점수 + 메타."""

    score: float  # 0~100
    avg_tone: float  # -10 ~ +10
    count: int
    cached_count: int
    api_count: int
    fallback: bool
    note: str
    source: str  # 'api' / 'cache' / 'fallback' (factor_scores.source용)


def tone_to_score(tone: float) -> float:
    """(-10 ~ +10) → (0 ~ 100). 선형."""
    return max(0.0, min(100.0, (tone + 10.0) / 20.0 * 100.0))


class ClaudeSummarizer:
    """Anthropic API 호출 + 비용 통제. 클라이언트는 lazy 초기화."""

    def __init__(
        self,
        *,
        model: str | None = None,
        client: Any | None = None,
        daily_input_limit: int | None = None,
    ) -> None:
        self.model = model or settings.anthropic_model_default
        self._client = client  # 테스트에서 주입
        self.daily_input_limit = (
            daily_input_limit
            if daily_input_limit is not None
            else settings.anthropic_daily_input_limit
        )

    @property
    def client(self) -> Any:
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic(
                api_key=settings.anthropic_api_key.get_secret_value()
            )
        return self._client

    # ─── 단일 뉴스 요약 ───

    def summarize_article(self, article_text: str) -> SummaryResult:
        """원문 텍스트 → 요약. 한도 초과 시 SummaryLimitExceeded."""
        truncated = article_text[:8000]  # 한 번에 처리할 안전 길이
        response = self.client.messages.create(
            model=self.model,
            max_tokens=400,
            system=SYSTEM_PROMPT_NEWS,
            messages=[{"role": "user", "content": truncated}],
        )
        text = self._extract_text(response)
        parsed = self._parse_json(text)
        usage = getattr(response, "usage", None)
        in_tokens = int(getattr(usage, "input_tokens", 0))
        out_tokens = int(getattr(usage, "output_tokens", 0))
        return SummaryResult(
            summary=str(parsed.get("summary", "")).strip(),
            tone_score=_clamp_tone(parsed.get("tone_score", 0)),
            keywords=_normalize_keywords(parsed.get("keywords", [])),
            tokens_used=in_tokens + out_tokens,
            model=self.model,
            source="api",
        )

    # ─── 워치리스트용 배치 ───

    def summarize_news_batch(
        self,
        conn: sqlite3.Connection,
        *,
        ticker_id: int,
        news: list[News],
    ) -> AggregatedSentiment:
        """캐시 → API → 집계. DB 저장 포함.

        한도 초과 또는 뉴스 없을 시 source='fallback'/'cache' 명시.
        """
        if not news:
            return AggregatedSentiment(
                score=50.0,
                avg_tone=0.0,
                count=0,
                cached_count=0,
                api_count=0,
                fallback=True,
                note="최근 뉴스 없음",
                source="fallback",
            )

        tones: list[float] = []
        cached_count = 0
        api_count = 0
        budget_blown = False
        limit_remaining = self._remaining_budget(conn)

        for item in news:
            cached = get_cached_news_summary(conn, ticker_id, item.url)
            if cached is not None:
                tones.append(cached.tone_score)
                cached_count += 1
                continue

            if budget_blown or limit_remaining <= 0:
                budget_blown = True
                continue

            try:
                result = self.summarize_article(
                    f"제목: {item.title}\n출처: {item.source_name or '?'}\n"
                    f"발행: {item.published_at}\n"
                    f"요약(원문): {item.summary or item.title}"
                )
            except (json.JSONDecodeError, ValueError) as e:
                _logger.warning("요약 파싱 실패: %s — %s", item.url, e)
                continue
            except Exception as e:
                _logger.warning("Claude API 호출 실패: %s — %s", item.url, e)
                budget_blown = True
                continue

            self._persist(conn, ticker_id, item, result)
            tones.append(result.tone_score)
            api_count += 1
            limit_remaining -= result.tokens_used

        if not tones:
            return AggregatedSentiment(
                score=50.0,
                avg_tone=0.0,
                count=0,
                cached_count=cached_count,
                api_count=api_count,
                fallback=True,
                note="요약 실패 또는 한도 초과",
                source="fallback",
            )

        avg = sum(tones) / len(tones)
        # source 결정: 신규 API 호출이 있으면 'api', 캐시만이면 'cache'
        if budget_blown and api_count == 0 and cached_count == 0:
            src = "fallback"
        elif api_count > 0:
            src = "api"
        else:
            src = "cache"

        return AggregatedSentiment(
            score=round(tone_to_score(avg), 2),
            avg_tone=round(avg, 2),
            count=len(tones),
            cached_count=cached_count,
            api_count=api_count,
            fallback=budget_blown and api_count == 0,
            note=f"뉴스 {len(tones)}건 평균 톤 {avg:+.1f} (API {api_count}/캐시 {cached_count})",
            source=src,
        )

    # ─── 내부 ───

    def _remaining_budget(self, conn: sqlite3.Connection) -> int:
        usage = get_today_token_usage(conn, mode="api")
        used = int(usage["input_tokens"])
        return max(0, self.daily_input_limit - used)

    def _persist(
        self,
        conn: sqlite3.Connection,
        ticker_id: int,
        news: News,
        result: SummaryResult,
    ) -> None:
        upsert_news_summary(
            conn,
            NewsSummaryRow(
                ticker_id=ticker_id,
                source_url=news.url,
                source_type="news",
                source="api",
                published_at=to_iso_utc(news.published_at),
                summary=result.summary,
                tone_score=result.tone_score,
                keywords=result.keywords,
                model=result.model,
                tokens_used=result.tokens_used,
            ),
        )
        in_t, out_t = _split_tokens(result.tokens_used)
        record_token_usage(
            conn,
            model=result.model,
            mode="api",
            input_tokens=in_t,
            output_tokens=out_t,
            cost_usd=_estimate_cost(result.model, in_t, out_t),
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        content = getattr(response, "content", [])
        if not content:
            return ""
        first = content[0]
        return str(getattr(first, "text", first) or "")

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        # 모델이 ```json ... ``` 감싸는 경우 대비
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise json.JSONDecodeError("JSON 객체 미발견", text, 0)
        return dict(json.loads(match.group(0)))


def _split_tokens(total: int) -> tuple[int, int]:
    """SDK가 usage 분리 못 주면 7:3 추정 (요약 input >> output)."""
    if total <= 0:
        return (0, 0)
    in_t = int(total * 0.7)
    return (in_t, total - in_t)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _PRICING.get(model, _DEFAULT_PRICING)
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def _clamp_tone(v: object) -> float:
    try:
        return max(-10.0, min(10.0, float(v)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _normalize_keywords(v: object) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()][:5]
