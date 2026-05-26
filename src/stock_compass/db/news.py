"""news_summaries + daily_token_usage — LLM 요약 캐시 + 비용 추적."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date as date_cls

from stock_compass.utils.dates import today_kst


@dataclass(frozen=True, slots=True)
class NewsSummaryRow:
    ticker_id: int
    source_url: str
    source_type: str  # 'news' / 'disclosure'
    source: str  # 'api' / 'manual_prompt' / 'fallback'
    published_at: str  # ISO UTC
    summary: str
    tone_score: float
    keywords: list[str]
    model: str
    batch_id: str | None = None
    tokens_used: int | None = None
    concerns: list[str] = field(default_factory=list)


def upsert_news_summary(conn: sqlite3.Connection, row: NewsSummaryRow) -> None:
    """source_url UNIQUE per ticker — 중복 호출 자동 차단."""
    conn.execute(
        """
        INSERT INTO news_summaries
          (ticker_id, source_url, source_type, source, published_at,
           summary, tone_score, keywords, tokens_used, model, batch_id, concerns)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker_id, source_url) DO UPDATE SET
          summary = excluded.summary,
          tone_score = excluded.tone_score,
          keywords = excluded.keywords,
          tokens_used = COALESCE(excluded.tokens_used, news_summaries.tokens_used),
          model = excluded.model,
          source = excluded.source,
          batch_id = COALESCE(excluded.batch_id, news_summaries.batch_id),
          concerns = excluded.concerns
        """,
        (
            row.ticker_id,
            row.source_url,
            row.source_type,
            row.source,
            row.published_at,
            row.summary,
            row.tone_score,
            json.dumps(row.keywords, ensure_ascii=False),
            row.tokens_used,
            row.model,
            row.batch_id,
            json.dumps(row.concerns, ensure_ascii=False),
        ),
    )


def get_cached_news_summary(
    conn: sqlite3.Connection, ticker_id: int, source_url: str
) -> NewsSummaryRow | None:
    row = conn.execute(
        """
        SELECT ticker_id, source_url, source_type, source, published_at,
               summary, tone_score, keywords, tokens_used, model, batch_id, concerns
        FROM news_summaries
        WHERE ticker_id = ? AND source_url = ?
        """,
        (ticker_id, source_url),
    ).fetchone()
    return _row_to_news_summary(row) if row else None


def get_recent_news_summaries(
    conn: sqlite3.Connection, ticker_id: int, *, days: int = 30
) -> list[NewsSummaryRow]:
    rows = conn.execute(
        """
        SELECT ticker_id, source_url, source_type, source, published_at,
               summary, tone_score, keywords, tokens_used, model, batch_id, concerns
        FROM news_summaries
        WHERE ticker_id = ?
          AND published_at >= datetime('now', ?)
        ORDER BY published_at DESC
        """,
        (ticker_id, f"-{days} days"),
    ).fetchall()
    return [_row_to_news_summary(r) for r in rows]


def _row_to_news_summary(row: sqlite3.Row) -> NewsSummaryRow:
    keywords = json.loads(row["keywords"]) if row["keywords"] else []
    # SELECT가 항상 concerns 칼럼을 명시하므로 Row에 존재 — 빈 문자열/NULL은 빈 배열
    concerns = json.loads(row["concerns"]) if row["concerns"] else []
    return NewsSummaryRow(
        ticker_id=int(row["ticker_id"]),
        source_url=row["source_url"],
        source_type=row["source_type"],
        source=row["source"],
        published_at=row["published_at"],
        summary=row["summary"],
        tone_score=float(row["tone_score"]),
        keywords=keywords,
        model=row["model"],
        batch_id=row["batch_id"],
        tokens_used=int(row["tokens_used"]) if row["tokens_used"] is not None else None,
        concerns=concerns,
    )


# ──────────────────────── token usage ────────────────────────


def record_token_usage(
    conn: sqlite3.Connection,
    *,
    model: str,
    mode: str = "api",
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    on_date: date_cls | None = None,
) -> None:
    """daily_token_usage 누적 (date + model + mode UNIQUE)."""
    d = (on_date or today_kst()).isoformat()
    conn.execute(
        """
        INSERT INTO daily_token_usage
          (date, model, mode, input_tokens, output_tokens, call_count, estimated_cost_usd)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(date, model, mode) DO UPDATE SET
          input_tokens = input_tokens + excluded.input_tokens,
          output_tokens = output_tokens + excluded.output_tokens,
          call_count = call_count + 1,
          estimated_cost_usd = estimated_cost_usd + excluded.estimated_cost_usd,
          updated_at = datetime('now')
        """,
        (d, model, mode, input_tokens, output_tokens, cost_usd),
    )


def get_today_token_usage(
    conn: sqlite3.Connection, *, mode: str = "api", on_date: date_cls | None = None
) -> dict[str, int | float]:
    """오늘의 input/output 토큰 합계 + 호출 수 + 누적 비용."""
    d = (on_date or today_kst()).isoformat()
    row = conn.execute(
        """
        SELECT
          COALESCE(SUM(input_tokens), 0) AS input_tokens,
          COALESCE(SUM(output_tokens), 0) AS output_tokens,
          COALESCE(SUM(call_count), 0) AS call_count,
          COALESCE(SUM(estimated_cost_usd), 0) AS cost_usd
        FROM daily_token_usage
        WHERE date = ? AND mode = ?
        """,
        (d, mode),
    ).fetchone()
    return {
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "call_count": int(row["call_count"]),
        "cost_usd": float(row["cost_usd"]),
    }
