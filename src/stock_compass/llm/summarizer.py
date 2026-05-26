"""ClaudeSummarizer — 뉴스·공시 JSON 요약 (API 경로).

비용 안전장치: 일일 input 토큰 한도 초과 시 자동 fallback (50점 sentiment).
캐시: news_summaries 테이블 source_url UNIQUE — 동일 URL 재요약 차단.
면책: 모든 호출자가 "AI 생성 요약. 원문 확인 필수." footer를 노출하도록 강제.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from stock_compass.config import settings
from stock_compass.db import (
    NewsSummaryRow,
    get_cached_news_summary,
    get_today_token_usage,
    record_token_usage,
    upsert_news_summary,
)
from stock_compass.llm.models import (
    AggregatedSentiment,
    SummaryLimitError,
    SummaryResult,
    clamp_tone,
    normalize_keywords,
    tone_to_score,
)
from stock_compass.llm.pricing import estimate_cost, split_tokens
from stock_compass.llm.prompts import (
    DISCLOSURE_FOOTER,
    SYSTEM_PROMPT_DISCLOSURE,
    SYSTEM_PROMPT_NEWS,
)
from stock_compass.markets.base import Disclosure, News
from stock_compass.utils.dates import to_iso_utc
from stock_compass.utils.logging import get_logger

if TYPE_CHECKING:
    import sqlite3

_logger = get_logger(__name__)

# 하위 호환 re-export — 외부에서 summarizer.X로 접근하던 코드 보호.
__all__ = [
    "DISCLOSURE_FOOTER",
    "SYSTEM_PROMPT_DISCLOSURE",
    "SYSTEM_PROMPT_NEWS",
    "AggregatedSentiment",
    "ClaudeSummarizer",
    "SummaryLimitError",
    "SummaryResult",
    "tone_to_score",
]


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

    # ─── 단일 요약 (뉴스 / 공시 공용) ───

    def summarize_article(
        self, article_text: str, *, system_prompt: str = SYSTEM_PROMPT_NEWS
    ) -> SummaryResult:
        """원문 텍스트 → 요약. system_prompt로 뉴스/공시 분기."""
        truncated = article_text[:8000]
        response = self.client.messages.create(
            model=self.model,
            max_tokens=400,
            system=system_prompt,
            messages=[{"role": "user", "content": truncated}],
        )
        text = self._extract_text(response)
        parsed = self._parse_json(text)
        usage = getattr(response, "usage", None)
        in_tokens = int(getattr(usage, "input_tokens", 0))
        out_tokens = int(getattr(usage, "output_tokens", 0))
        return SummaryResult(
            summary=str(parsed.get("summary", "")).strip(),
            tone_score=clamp_tone(parsed.get("tone_score", 0)),
            keywords=normalize_keywords(parsed.get("keywords", [])),
            tokens_used=in_tokens + out_tokens,
            model=self.model,
            source="api",
        )

    # ─── 워치리스트용 배치 (뉴스만) ───

    def summarize_news_batch(
        self,
        conn: sqlite3.Connection,
        *,
        ticker_id: int,
        news: list[News],
    ) -> AggregatedSentiment:
        """캐시 → API → 집계. DB 저장 포함. 한도 초과 시 source='fallback'."""
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

    # ─── 통합 batch (뉴스 + 공시) ───

    def summarize_events_batch(
        self,
        conn: sqlite3.Connection,
        *,
        ticker_id: int,
        news: list[News],
        disclosures: list[Disclosure],
    ) -> AggregatedSentiment:
        """뉴스 + 공시 통합 sentiment.

        공시는 이벤트성·확정성이 강하므로 가중치 2배. 캐시는 source_url 기준.
        """
        if not news and not disclosures:
            return AggregatedSentiment(
                score=50.0,
                avg_tone=0.0,
                count=0,
                cached_count=0,
                api_count=0,
                fallback=True,
                note="최근 30일 뉴스·공시 없음",
                source="fallback",
            )

        weighted: list[tuple[float, float]] = []  # (tone, weight)
        cached_count = 0
        api_count = 0
        disclosure_count = 0
        budget_blown = False
        limit_remaining = self._remaining_budget(conn)

        # 뉴스 (weight 1.0)
        for item in news[:10]:
            tone, was_cached, used = self._process_event(
                conn,
                ticker_id=ticker_id,
                source_url=item.url,
                source_type="news",
                published_at_iso=to_iso_utc(item.published_at),
                build_text=lambda i=item: (
                    f"제목: {i.title}\n출처: {i.source_name or '?'}\n"
                    f"발행: {i.published_at}\n"
                    f"요약(원문): {i.summary or i.title}"
                ),
                system_prompt=SYSTEM_PROMPT_NEWS,
                budget_blown=budget_blown,
                limit_remaining=limit_remaining,
            )
            if tone is None:
                if not was_cached:
                    budget_blown = True
                continue
            weighted.append((tone, 1.0))
            if was_cached:
                cached_count += 1
            else:
                api_count += 1
                limit_remaining -= used

        # 공시 (weight 2.0 — 이벤트 확정성)
        for d in disclosures[:5]:
            url = d.url or f"dart-rcept://{d.rcept_no}"
            tone, was_cached, used = self._process_event(
                conn,
                ticker_id=ticker_id,
                source_url=url,
                source_type="disclosure",
                published_at_iso=to_iso_utc(d.published_at),
                build_text=lambda dd=d: (
                    f"공시 제목: {dd.title}\n보고 유형: {dd.report_code or '?'}\n"
                    f"공시번호: {dd.rcept_no}\n발행: {dd.published_at}"
                ),
                system_prompt=SYSTEM_PROMPT_DISCLOSURE,
                budget_blown=budget_blown,
                limit_remaining=limit_remaining,
            )
            if tone is None:
                if not was_cached:
                    budget_blown = True
                continue
            weighted.append((tone, 2.0))
            disclosure_count += 1
            if was_cached:
                cached_count += 1
            else:
                api_count += 1
                limit_remaining -= used

        if not weighted:
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

        total_w = sum(w for _, w in weighted)
        avg = sum(t * w for t, w in weighted) / total_w

        if budget_blown and api_count == 0 and cached_count == 0:
            src = "fallback"
        elif api_count > 0:
            src = "api"
        else:
            src = "cache"

        return AggregatedSentiment(
            score=round(tone_to_score(avg), 2),
            avg_tone=round(avg, 2),
            count=len(weighted),
            cached_count=cached_count,
            api_count=api_count,
            fallback=budget_blown and api_count == 0,
            note=(
                f"뉴스+공시 {len(weighted)}건 (공시 {disclosure_count}건, w=2.0) "
                f"가중평균 톤 {avg:+.1f} (API {api_count}/캐시 {cached_count})"
            ),
            source=src,
        )

    # ─── 내부 ───

    def _process_event(
        self,
        conn: sqlite3.Connection,
        *,
        ticker_id: int,
        source_url: str,
        source_type: str,  # 'news' / 'disclosure'
        published_at_iso: str,
        build_text: Any,  # () -> str (lazy — 캐시 hit 시 호출 회피)
        system_prompt: str,
        budget_blown: bool,
        limit_remaining: int,
    ) -> tuple[float | None, bool, int]:
        """단일 이벤트 처리 → (tone, was_cached, tokens_used).

        tone=None은 스킵 (한도 초과·실패). was_cached는 budget 소모 여부 판단용.
        """
        cached = get_cached_news_summary(conn, ticker_id, source_url)
        if cached is not None:
            return (cached.tone_score, True, 0)

        if budget_blown or limit_remaining <= 0:
            return (None, False, 0)

        try:
            result = self.summarize_article(build_text(), system_prompt=system_prompt)
        except (json.JSONDecodeError, ValueError) as e:
            _logger.warning("요약 파싱 실패: %s — %s", source_url, e)
            return (None, True, 0)
        except Exception as e:
            _logger.warning("Claude API 호출 실패: %s — %s", source_url, e)
            return (None, False, 0)

        self._persist_summary(
            conn,
            ticker_id=ticker_id,
            source_url=source_url,
            source_type=source_type,
            published_at_iso=published_at_iso,
            result=result,
        )
        return (result.tone_score, False, result.tokens_used)

    def _remaining_budget(self, conn: sqlite3.Connection) -> int:
        usage = get_today_token_usage(conn, mode="api")
        used = int(usage["input_tokens"])
        return max(0, self.daily_input_limit - used)

    def _persist_summary(
        self,
        conn: sqlite3.Connection,
        *,
        ticker_id: int,
        source_url: str,
        source_type: str,
        published_at_iso: str,
        result: SummaryResult,
    ) -> None:
        upsert_news_summary(
            conn,
            NewsSummaryRow(
                ticker_id=ticker_id,
                source_url=source_url,
                source_type=source_type,
                source="api",
                published_at=published_at_iso,
                summary=result.summary,
                tone_score=result.tone_score,
                keywords=result.keywords,
                model=result.model,
                tokens_used=result.tokens_used,
            ),
        )
        in_t, out_t = split_tokens(result.tokens_used)
        record_token_usage(
            conn,
            model=result.model,
            mode="api",
            input_tokens=in_t,
            output_tokens=out_t,
            cost_usd=estimate_cost(result.model, in_t, out_t),
        )

    def _persist(
        self,
        conn: sqlite3.Connection,
        ticker_id: int,
        news: News,
        result: SummaryResult,
    ) -> None:
        """legacy — summarize_news_batch 호환용."""
        self._persist_summary(
            conn,
            ticker_id=ticker_id,
            source_url=news.url,
            source_type="news",
            published_at_iso=to_iso_utc(news.published_at),
            result=result,
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
