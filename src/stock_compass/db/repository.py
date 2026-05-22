"""SQLite repository — 종목 마스터·점수 영속화·이력 조회.

모든 시각은 UTC ISO 8601, 일자는 YYYY-MM-DD (KST 기준). DB는 단일 사용자 가정,
sqlite3 + raw SQL (의존성 최소화).
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_compass.config import settings
from stock_compass.db.migrations import migrate
from stock_compass.factors.base import FactorName, FactorScore
from stock_compass.markets.base import Market
from stock_compass.scoring.engine import DISCLAIMER, CompositeScore, Verdict
from stock_compass.utils.dates import to_iso_utc, today_kst
from stock_compass.utils.logging import get_logger

_logger = get_logger(__name__)


@contextmanager
def get_db_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """SQLite 연결. PRAGMA foreign_keys ON + Row factory. 처음 호출 시 자동 마이그레이션."""
    path = db_path or settings.db_path
    migrate(path)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; 명시적 BEGIN 가능
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


# ──────────────────────── tickers ────────────────────────


def upsert_ticker(
    conn: sqlite3.Connection,
    *,
    code: str,
    market: Market,
    name: str | None,
    sector: str | None,
    currency: str,
    yfinance_symbol: str,
) -> int:
    """tickers 테이블에 upsert 후 id 반환. name 누락 시 code로 대체."""
    conn.execute(
        """
        INSERT INTO tickers (code, market, name, sector, currency, yfinance_symbol)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(market, code) DO UPDATE SET
          name = COALESCE(excluded.name, tickers.name),
          sector = COALESCE(excluded.sector, tickers.sector),
          currency = excluded.currency,
          yfinance_symbol = excluded.yfinance_symbol
        """,
        (code, market, name or code, sector, currency, yfinance_symbol),
    )
    row = conn.execute(
        "SELECT id FROM tickers WHERE market = ? AND code = ?", (market, code)
    ).fetchone()
    return int(row["id"])


def get_ticker_id(conn: sqlite3.Connection, code: str, market: Market) -> int | None:
    row = conn.execute(
        "SELECT id FROM tickers WHERE market = ? AND code = ?", (market, code)
    ).fetchone()
    return int(row["id"]) if row else None


_ALLOWED_SENTIMENT_SOURCE = {"api", "manual_prompt", "fallback", "cache", "placeholder"}


def _normalize_sentiment_source(source: str) -> str:
    """sentiment 팩터의 source를 composite_scores CHECK 제약에 맞춰 변환."""
    if source in _ALLOWED_SENTIMENT_SOURCE:
        return source
    # 'computed' (테스트 기본값), 'prompt_pending', 미지정 → placeholder로 통합
    return "placeholder"


# ──────────────────────── composite & factor scores ────────────────────────


def upsert_composite_score(
    conn: sqlite3.Connection,
    score: CompositeScore,
    *,
    on_date: date_cls | None = None,
) -> int:
    """composite_scores + factor_scores를 동일 트랜잭션으로 upsert. ticker_id 반환."""
    on_date = on_date or today_kst()
    date_str = on_date.isoformat()

    ticker_id = upsert_ticker(
        conn,
        code=score.ticker,
        market=score.market,
        name=score.name,
        sector=score.sector,
        currency=score.currency or ("KRW" if score.market == "KR" else "USD"),
        yfinance_symbol=score.yfinance_symbol or score.ticker,
    )

    sentiment_factor = next((f for f in score.factors if f.name == "sentiment"), None)
    sentiment_source = _normalize_sentiment_source(
        sentiment_factor.source if sentiment_factor else "placeholder"
    )

    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT INTO composite_scores
              (ticker_id, date, total_score, verdict, price_at_score, computed_at,
               sentiment_source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker_id, date) DO UPDATE SET
              total_score = excluded.total_score,
              verdict = excluded.verdict,
              price_at_score = excluded.price_at_score,
              computed_at = excluded.computed_at,
              sentiment_source = excluded.sentiment_source
            """,
            (
                ticker_id,
                date_str,
                score.total_score,
                score.verdict,
                score.price_at_score,
                to_iso_utc(score.computed_at),
                sentiment_source,
            ),
        )
        for f in score.factors:
            conn.execute(
                """
                INSERT INTO factor_scores
                  (ticker_id, date, factor_name, score, weight, raw_values, note, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker_id, date, factor_name) DO UPDATE SET
                  score = excluded.score,
                  weight = excluded.weight,
                  raw_values = excluded.raw_values,
                  note = excluded.note,
                  computed_at = excluded.computed_at
                """,
                (
                    ticker_id,
                    date_str,
                    f.name,
                    f.score,
                    f.weight,
                    json.dumps(f.raw_values, ensure_ascii=False, default=str),
                    f.note,
                    to_iso_utc(score.computed_at),
                ),
            )
        conn.execute("COMMIT")
    except sqlite3.DatabaseError:
        conn.execute("ROLLBACK")
        raise
    return ticker_id


def get_previous_composite_score(
    conn: sqlite3.Connection,
    ticker_id: int,
    *,
    before_date: date_cls,
) -> CompositeScore | None:
    """`before_date` 이전(미포함)에서 가장 최근 composite + 5팩터.

    threshold·delta 트리거가 "직전 점수" 비교용으로 사용.
    """
    row = conn.execute(
        """
        SELECT cs.date, cs.total_score, cs.verdict, cs.price_at_score, cs.computed_at,
               t.code, t.market, t.name, t.sector, t.currency, t.yfinance_symbol
        FROM composite_scores cs
        JOIN tickers t ON cs.ticker_id = t.id
        WHERE cs.ticker_id = ? AND cs.date < ?
        ORDER BY cs.date DESC
        LIMIT 1
        """,
        (ticker_id, before_date.isoformat()),
    ).fetchone()
    if row is None:
        return None
    factor_rows = conn.execute(
        """
        SELECT factor_name, score, weight, raw_values, note
        FROM factor_scores
        WHERE ticker_id = ? AND date = ?
        """,
        (ticker_id, row["date"]),
    ).fetchall()
    return _row_to_composite(row, [_row_to_factor(r) for r in factor_rows])


def get_last_score(
    conn: sqlite3.Connection, code: str, market: Market
) -> CompositeScore | None:
    """가장 최근 composite_score 1건 + 해당 일자 factor_scores 전부."""
    ticker_id = get_ticker_id(conn, code, market)
    if ticker_id is None:
        return None
    composite = conn.execute(
        """
        SELECT cs.date, cs.total_score, cs.verdict, cs.price_at_score, cs.computed_at,
               t.code, t.market, t.name, t.sector, t.currency, t.yfinance_symbol
        FROM composite_scores cs
        JOIN tickers t ON cs.ticker_id = t.id
        WHERE cs.ticker_id = ?
        ORDER BY cs.date DESC
        LIMIT 1
        """,
        (ticker_id,),
    ).fetchone()
    if composite is None:
        return None
    factor_rows = conn.execute(
        """
        SELECT factor_name, score, weight, raw_values, note
        FROM factor_scores
        WHERE ticker_id = ? AND date = ?
        """,
        (ticker_id, composite["date"]),
    ).fetchall()
    factors = [_row_to_factor(r) for r in factor_rows]
    return _row_to_composite(composite, factors)


@dataclass(frozen=True, slots=True)
class HistoryRow:
    date: str
    total_score: float
    verdict: Verdict
    price_at_score: float | None
    factor_scores: dict[FactorName, float]


def get_score_history(
    conn: sqlite3.Connection, code: str, market: Market, *, days: int = 30
) -> list[HistoryRow]:
    """최근 N영업일(기록 기준) 점수 + 팩터별 점수 추이. 오래된 → 최신 순."""
    ticker_id = get_ticker_id(conn, code, market)
    if ticker_id is None:
        return []
    rows = conn.execute(
        """
        SELECT
          cs.date, cs.total_score, cs.verdict, cs.price_at_score,
          MAX(CASE WHEN fs.factor_name='valuation' THEN fs.score END) AS valuation,
          MAX(CASE WHEN fs.factor_name='fundamentals' THEN fs.score END) AS fundamentals,
          MAX(CASE WHEN fs.factor_name='technical' THEN fs.score END) AS technical,
          MAX(CASE WHEN fs.factor_name='macro' THEN fs.score END) AS macro,
          MAX(CASE WHEN fs.factor_name='sentiment' THEN fs.score END) AS sentiment
        FROM composite_scores cs
        LEFT JOIN factor_scores fs ON cs.ticker_id = fs.ticker_id AND cs.date = fs.date
        WHERE cs.ticker_id = ?
        GROUP BY cs.date
        ORDER BY cs.date DESC
        LIMIT ?
        """,
        (ticker_id, days),
    ).fetchall()
    history: list[HistoryRow] = []
    for r in rows:
        scores: dict[FactorName, float] = {}
        for name in ("valuation", "fundamentals", "technical", "macro", "sentiment"):
            v = r[name]
            if v is not None:
                scores[name] = float(v)
        history.append(
            HistoryRow(
                date=r["date"],
                total_score=float(r["total_score"]),
                verdict=r["verdict"],
                price_at_score=(
                    float(r["price_at_score"]) if r["price_at_score"] is not None else None
                ),
                factor_scores=scores,
            )
        )
    history.reverse()  # 오래된 → 최신
    return history


def get_latest_scores(
    conn: sqlite3.Connection, *, market: Market | None = None
) -> list[CompositeScore]:
    """모든 종목의 가장 최근 composite_score. 점수 내림차순. (batch 결과 요약용)"""
    where = "WHERE t.market = ?" if market else ""
    params: tuple[Any, ...] = (market,) if market else ()
    rows = conn.execute(
        f"""
        WITH latest AS (
          SELECT ticker_id, MAX(date) AS latest_date
          FROM composite_scores
          GROUP BY ticker_id
        )
        SELECT cs.date, cs.total_score, cs.verdict, cs.price_at_score, cs.computed_at,
               t.code, t.market, t.name, t.sector, t.currency, t.yfinance_symbol
        FROM composite_scores cs
        JOIN latest l ON cs.ticker_id = l.ticker_id AND cs.date = l.latest_date
        JOIN tickers t ON cs.ticker_id = t.id
        {where}
        ORDER BY cs.total_score DESC
        """,
        params,
    ).fetchall()
    return [_row_to_composite(r, factors=[]) for r in rows]


def get_scores_on_date(
    conn: sqlite3.Connection,
    on_date: date_cls,
    *,
    market: Market | None = None,
) -> list[CompositeScore]:
    """특정 일자의 모든 종목 composite + 5팩터. 점수 내림차순. (report 명령용)

    2쿼리(composite + factor)로 N+1 회피.
    """
    date_str = on_date.isoformat()
    where_market = "AND t.market = ?" if market else ""
    composite_params: tuple[Any, ...] = (date_str, market) if market else (date_str,)
    composites = conn.execute(
        f"""
        SELECT cs.ticker_id, cs.date, cs.total_score, cs.verdict, cs.price_at_score,
               cs.computed_at,
               t.code, t.market, t.name, t.sector, t.currency, t.yfinance_symbol
        FROM composite_scores cs
        JOIN tickers t ON cs.ticker_id = t.id
        WHERE cs.date = ? {where_market}
        ORDER BY cs.total_score DESC
        """,
        composite_params,
    ).fetchall()
    if not composites:
        return []

    factor_rows = conn.execute(
        """
        SELECT ticker_id, factor_name, score, weight, raw_values, note
        FROM factor_scores
        WHERE date = ?
        """,
        (date_str,),
    ).fetchall()
    factors_by_ticker: dict[int, list[FactorScore]] = defaultdict(list)
    for fr in factor_rows:
        factors_by_ticker[int(fr["ticker_id"])].append(_row_to_factor(fr))

    return [
        _row_to_composite(c, factors_by_ticker.get(int(c["ticker_id"]), []))
        for c in composites
    ]


# ──────────────────────── news summaries (Phase 4) ────────────────────────


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


def upsert_news_summary(conn: sqlite3.Connection, row: NewsSummaryRow) -> None:
    """source_url UNIQUE per ticker — 중복 호출 자동 차단."""
    conn.execute(
        """
        INSERT INTO news_summaries
          (ticker_id, source_url, source_type, source, published_at,
           summary, tone_score, keywords, tokens_used, model, batch_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker_id, source_url) DO UPDATE SET
          summary = excluded.summary,
          tone_score = excluded.tone_score,
          keywords = excluded.keywords,
          tokens_used = COALESCE(excluded.tokens_used, news_summaries.tokens_used),
          model = excluded.model,
          source = excluded.source,
          batch_id = COALESCE(excluded.batch_id, news_summaries.batch_id)
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
        ),
    )


def get_cached_news_summary(
    conn: sqlite3.Connection, ticker_id: int, source_url: str
) -> NewsSummaryRow | None:
    row = conn.execute(
        """
        SELECT ticker_id, source_url, source_type, source, published_at,
               summary, tone_score, keywords, tokens_used, model, batch_id
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
               summary, tone_score, keywords, tokens_used, model, batch_id
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
    )


# ──────────────────────── token usage (Phase 4) ────────────────────────


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


# ──────────────────────── alerts (Phase 5) ────────────────────────


@dataclass(frozen=True, slots=True)
class AlertRow:
    ticker_id: int
    trigger_type: str
    score_before: float | None
    score_after: float
    message: str
    delivered_via: str
    fired_at: str  # ISO UTC


def has_recent_alert(
    conn: sqlite3.Connection,
    ticker_id: int,
    trigger_type: str,
    *,
    hours: int = 24,
) -> bool:
    """동일 종목·동일 trigger가 N시간 내 발화된 적 있는지."""
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE ticker_id = ? AND trigger_type = ?
          AND fired_at > datetime('now', ?)
        LIMIT 1
        """,
        (ticker_id, trigger_type, f"-{hours} hours"),
    ).fetchone()
    return row is not None


def has_daily_alert_today(
    conn: sqlite3.Connection, *, on_date: date_cls | None = None
) -> bool:
    """일일 리포트 알림이 오늘(KST 기준) 이미 발화됐는지."""
    d = (on_date or today_kst()).isoformat()
    row = conn.execute(
        """
        SELECT 1 FROM alerts
        WHERE trigger_type = 'daily' AND DATE(fired_at, 'localtime') = ?
        LIMIT 1
        """,
        (d,),
    ).fetchone()
    return row is not None


def record_alert(conn: sqlite3.Connection, alert: AlertRow) -> int:
    """alerts 테이블에 한 행 추가. 발화 이력 영구 보존."""
    cur = conn.execute(
        """
        INSERT INTO alerts
          (ticker_id, trigger_type, score_before, score_after,
           message, delivered_via, fired_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert.ticker_id,
            alert.trigger_type,
            alert.score_before,
            alert.score_after,
            alert.message,
            alert.delivered_via,
            alert.fired_at,
        ),
    )
    return int(cur.lastrowid or 0)


def get_recent_alerts(
    conn: sqlite3.Connection, *, hours: int = 24
) -> list[dict[str, Any]]:
    """최근 N시간 발화된 알림 (목록·디버그용)."""
    rows = conn.execute(
        """
        SELECT a.id, a.trigger_type, a.score_before, a.score_after, a.message,
               a.delivered_via, a.fired_at,
               t.code, t.market, t.name
        FROM alerts a
        JOIN tickers t ON a.ticker_id = t.id
        WHERE a.fired_at > datetime('now', ?)
        ORDER BY a.fired_at DESC
        """,
        (f"-{hours} hours",),
    ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────── helpers ────────────────────────


def _row_to_factor(row: sqlite3.Row) -> FactorScore:
    raw = json.loads(row["raw_values"]) if row["raw_values"] else {}
    return FactorScore(
        name=row["factor_name"],
        score=float(row["score"]),
        weight=float(row["weight"]),
        raw_values=raw,
        note=row["note"] or "",
    )


def _row_to_composite(row: sqlite3.Row, factors: list[FactorScore]) -> CompositeScore:
    return CompositeScore(
        ticker=row["code"],
        market=row["market"],
        total_score=float(row["total_score"]),
        verdict=row["verdict"],
        factors=factors,
        computed_at=datetime.fromisoformat(row["computed_at"]),
        price_at_score=float(row["price_at_score"]) if row["price_at_score"] is not None else None,
        currency=row["currency"],
        name=row["name"],
        sector=row["sector"],
        yfinance_symbol=row["yfinance_symbol"],
        disclaimer=DISCLAIMER,
    )
