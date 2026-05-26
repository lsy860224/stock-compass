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
from stock_compass.markets.base import Disclosure, News
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


SYSTEM_PROMPT_DISCLOSURE = """당신은 한국 DART 공시 분석가다.
공시 제목과 보고 유형으로 시장 임팩트(tone_score: -10 ~ +10)를 정량화하라.

이벤트 유형별 기본 가중치 (참고 기준 — 규모·맥락에 따라 조정):

[긍정]
- 자사주 매입 결정: +3 ~ +5
- 자사주 소각: +5 ~ +7
- 어닝 서프라이즈 / 잠정실적 양호: +4 ~ +8
- 대규모 신규 계약 / 수주 공시: +3 ~ +6
- 무상증자: +2 ~ +4
- 배당 인상: +2 ~ +4
- M&A / 전략적 제휴 (주주가치 증대): +2 ~ +5
- 흑자 전환: +4 ~ +7

[부정]
- 회계 정정 (정정 보고서 포함): -5 ~ -8
- 실적 어닝 미스 / 적자 확대: -4 ~ -7
- 유상증자 (대규모 희석): -3 ~ -6
- 무상감자: -6 ~ -9
- CB·BW 발행: -2 ~ -4
- 임원·대주주 자사주 매도: -2 ~ -4
- 소송 패소 / 과징금: -3 ~ -6
- 횡령·배임 사실 공시: -8 ~ -10
- 거래정지 / 상장적격성 사유: -8 ~ -10

[중립]
- 분기·사업·반기보고서 정기 제출: 0
- 정기주총 결의: 0
- 단순 정정 (오기 정정): -1 ~ +1

원칙:
1. 제목+보고유형으로 이벤트 분류 → 위 범위 안에서 tone_score 결정
2. 분류 불명·일반 공시는 0 (중립)
3. summary는 한국어 3줄 이내. 원문 인용 금지
4. JSON 한 객체만 출력. ``` 감싸기·인사말·주석 절대 금지

출력 형식 (정확히 이 키만):
{
  "summary": "한국어 3줄 요약",
  "event_type": "자사주매입 / 회계정정 / 실적공시 / 유상증자 / 기타",
  "tone_score": 0.0,
  "keywords": ["키워드1", "키워드2", "키워드3"]
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
            return (None, True, 0)  # 파싱 실패는 budget 무관
        except Exception as e:
            _logger.warning("Claude API 호출 실패: %s — %s", source_url, e)
            return (None, False, 0)  # API 실패는 budget 소모로 간주

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
        in_t, out_t = _split_tokens(result.tokens_used)
        record_token_usage(
            conn,
            model=result.model,
            mode="api",
            input_tokens=in_t,
            output_tokens=out_t,
            cost_usd=_estimate_cost(result.model, in_t, out_t),
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
