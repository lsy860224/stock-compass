"""LLM 도메인 모델 — Summary/AggregatedSentiment + 톤 변환 헬퍼."""

from __future__ import annotations

from dataclasses import dataclass


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


def clamp_tone(v: object) -> float:
    try:
        return max(-10.0, min(10.0, float(v)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def normalize_keywords(v: object) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()][:5]
