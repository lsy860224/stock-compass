"""Claude.ai 응답 파일 → DB 저장 (수동 sentiment 경로).

흐름:
    1. 응답 텍스트에서 JSON 코드블록 추출 (정규식)
    2. Pydantic 검증 (batch_id 일치, results 형식)
    3. 30단어 이상 인용 감지 (50단어 이상 → 차단, 30~49 → 경고)
    4. news_summaries.source='manual_prompt' 저장
    5. 응답 파일 자동 보관 (`data/prompts/archive/`)
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError, field_validator

from stock_compass.config import settings
from stock_compass.db import NewsSummaryRow, get_ticker_id, upsert_news_summary
from stock_compass.markets import detect_market
from stock_compass.utils.dates import to_iso_utc
from stock_compass.utils.logging import get_logger

if TYPE_CHECKING:
    import sqlite3

_logger = get_logger(__name__)

# 30단어 = 경고, 50단어 = 차단 (CLAUDE.md 12) 저작권 원칙).
QUOTE_WARN_WORDS = 30
QUOTE_BLOCK_WORDS = 50


class PromptImportError(RuntimeError):
    """import 실패 (포맷 오류·batch_id 불일치·정책 위반)."""


# ──────────────────────── 응답 스키마 ────────────────────────


class _ResultItem(BaseModel):
    ticker: str
    summary: str
    tone_score: float = Field(ge=-10.0, le=10.0)
    keywords: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)

    @field_validator("ticker")
    @classmethod
    def _strip_ticker(cls, v: str) -> str:
        return v.strip()

    @field_validator("keywords", "concerns", mode="before")
    @classmethod
    def _normalize_str_list(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [str(v).strip()] if str(v).strip() else []


class _ResponsePayload(BaseModel):
    batch_id: str
    results: list[_ResultItem]


# ──────────────────────── 결과 ────────────────────────


@dataclass(slots=True)
class ImportResult:
    batch_id: str
    saved: int = 0
    warnings: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    archived_to: Path | None = None


# ──────────────────────── public ────────────────────────


def import_response(
    conn: sqlite3.Connection,
    file_path: Path,
    *,
    batch_id: str | None = None,
    archive: bool = True,
) -> ImportResult:
    """응답 파일 → DB. batch_id 미지정 시 응답 내부의 값을 사용."""
    text = file_path.read_text(encoding="utf-8")
    payload = _parse_payload(text)

    if batch_id is not None and payload.batch_id != batch_id:
        raise PromptImportError(
            f"batch_id 불일치: 응답='{payload.batch_id}' 기대='{batch_id}'"
        )

    result = ImportResult(batch_id=payload.batch_id)
    for item in payload.results:
        max_words = _max_words_per_sentence(item.summary)
        if max_words >= QUOTE_BLOCK_WORDS:
            msg = f"{item.ticker}: 문장 {max_words}단어 — 인용 의심으로 차단"
            _logger.error(msg)
            result.blocked.append(msg)
            continue
        if max_words >= QUOTE_WARN_WORDS:
            msg = f"{item.ticker}: 문장 {max_words}단어 — 인용 가능성 (검토 권장)"
            _logger.warning(msg)
            result.warnings.append(msg)

        try:
            market = detect_market(item.ticker)
        except ValueError as e:
            result.warnings.append(f"{item.ticker}: 시장 감지 실패 — 스킵 ({e})")
            continue

        ticker_id = get_ticker_id(conn, item.ticker, market)
        if ticker_id is None:
            result.warnings.append(
                f"{item.ticker}: DB 미등록 — 먼저 `batch` 또는 `score` 실행 필요"
            )
            continue

        # manual_prompt는 단일 종목 단위 — 가짜 source_url로 캐시 (batch_id 결합)
        source_url = f"manual_prompt://{payload.batch_id}/{item.ticker}"
        upsert_news_summary(
            conn,
            NewsSummaryRow(
                ticker_id=ticker_id,
                source_url=source_url,
                source_type="news",
                source="manual_prompt",
                published_at=to_iso_utc(datetime.now(UTC)),
                summary=item.summary,
                tone_score=item.tone_score,
                keywords=item.keywords,
                model="manual_claude_ai",
                batch_id=payload.batch_id,
                concerns=item.concerns,
            ),
        )
        result.saved += 1

    if archive:
        result.archived_to = _archive(file_path)

    return result


# ──────────────────────── 내부 ────────────────────────


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)


def _parse_payload(text: str) -> _ResponsePayload:
    raw = _extract_json(text)
    try:
        return _ResponsePayload.model_validate_json(raw)
    except ValidationError as e:
        raise PromptImportError(f"응답 JSON 검증 실패: {e}") from e


def _extract_json(text: str) -> str:
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1)
    m = _BARE_JSON_RE.search(text)
    if m:
        return m.group(1)
    raise PromptImportError("응답에서 JSON 코드블록을 찾지 못했습니다.")


def _max_words_per_sentence(text: str) -> int:
    """가장 긴 문장의 단어 수. 한·영 공백 기준 단순 분할."""
    if not text:
        return 0
    # 한·영 문장 종결자 — 전각 마침표/물음표/느낌표 포함 (한국어 응답 대비)
    sentences = re.split(r"[.!?。！？]\s*", text)  # noqa: RUF001
    return max((len(s.split()) for s in sentences if s.strip()), default=0)


def _archive(file_path: Path) -> Path:
    archive_dir = settings.prompt_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    dest = archive_dir / f"{file_path.stem}-{stamp}{file_path.suffix}"
    shutil.copy2(file_path, dest)
    return dest
