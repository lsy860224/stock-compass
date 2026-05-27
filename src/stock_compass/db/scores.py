"""composite_scores + factor_scores — 점수 영속화·이력·조회."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from typing import Any

from stock_compass.db.tickers import get_ticker_id, normalize_sentiment_source, upsert_ticker
from stock_compass.factors.base import FactorName, FactorScore
from stock_compass.markets.base import Market
from stock_compass.scoring.engine import DISCLAIMER, CompositeScore, Verdict
from stock_compass.utils.dates import to_iso_utc, today_kst


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
    sentiment_source = normalize_sentiment_source(
        sentiment_factor.source if sentiment_factor else "placeholder"
    )

    # 호출자가 이미 트랜잭션 안에 있으면 nested 시작/커밋 회피 (batch persist 호환).
    own_transaction = not conn.in_transaction
    if own_transaction:
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
        if own_transaction:
            conn.execute("COMMIT")
    except sqlite3.DatabaseError:
        if own_transaction:
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
    history.reverse()
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


def get_previous_total_scores(
    conn: sqlite3.Connection,
    code_markets: list[tuple[str, Market]],
    *,
    before_date: date_cls,
) -> dict[tuple[str, Market], tuple[float, Verdict]]:
    """입력 종목들 각각의 `before_date` 이전 가장 최근 (total_score, verdict).

    워치리스트 batch 결과를 어제 대비 비교할 때 호출 (BT1 — Δ 표시).
    누락된 키는 dict에 포함 안 됨 (cold-start 종목).
    """
    if not code_markets:
        return {}
    result: dict[tuple[str, Market], tuple[float, Verdict]] = {}
    for code, market in code_markets:
        ticker_id = get_ticker_id(conn, code, market)
        if ticker_id is None:
            continue
        row = conn.execute(
            """
            SELECT total_score, verdict
            FROM composite_scores
            WHERE ticker_id = ? AND date < ?
            ORDER BY date DESC
            LIMIT 1
            """,
            (ticker_id, before_date.isoformat()),
        ).fetchone()
        if row is not None:
            result[(code, market)] = (float(row["total_score"]), row["verdict"])
    return result


def get_sector_score_rank(
    conn: sqlite3.Connection,
    market: Market,
    sector: str | None,
    ticker_id: int,
) -> tuple[int, int] | None:
    """같은 (market, sector) 내 최신 composite_score 기준 ticker_id의 (rank, total).

    표본 < 2 (peer 부재) 또는 sector 미상이면 None.
    """
    if not sector:
        return None
    rows = conn.execute(
        """
        WITH latest_per_ticker AS (
          SELECT cs.ticker_id, MAX(cs.date) AS d
          FROM composite_scores cs
          GROUP BY cs.ticker_id
        )
        SELECT cs.ticker_id, cs.total_score
        FROM composite_scores cs
        JOIN tickers t ON t.id = cs.ticker_id
        JOIN latest_per_ticker l
          ON cs.ticker_id = l.ticker_id AND cs.date = l.d
        WHERE t.market = ? AND t.sector = ?
        ORDER BY cs.total_score DESC, cs.ticker_id ASC
        """,
        (market, sector),
    ).fetchall()
    if len(rows) < 2:
        return None
    for i, r in enumerate(rows, start=1):
        if int(r["ticker_id"]) == ticker_id:
            return (i, len(rows))
    return None


def get_sector_valuation_medians(
    conn: sqlite3.Connection,
    market: Market,
    sector: str | None,
    *,
    lookback_days: int = 7,
    min_samples: int = 3,
) -> dict[str, float | None]:
    """같은 (market, sector) 종목들의 최근 PER/PBR/PEG 중앙값.

    `factors/valuation`이 절대 임계치 대신 sector-relative 점수화에 사용.
    표본 < `min_samples` (cold-start)면 빈 dict → factor가 절대 fallback.
    """
    if not sector:
        return {}
    rows = conn.execute(
        """
        SELECT
          json_extract(fs.raw_values, '$.per') AS per,
          json_extract(fs.raw_values, '$.pbr') AS pbr,
          json_extract(fs.raw_values, '$.peg') AS peg,
          json_extract(fs.raw_values, '$.psr') AS psr,
          json_extract(fs.raw_values, '$.ev_ebitda') AS ev_ebitda,
          json_extract(fs.raw_values, '$.p_fcf') AS p_fcf
        FROM factor_scores fs
        JOIN tickers t ON t.id = fs.ticker_id
        WHERE t.market = ? AND t.sector = ?
          AND fs.factor_name = 'valuation'
          AND fs.date >= date('now', ?)
        """,
        (market, sector, f"-{lookback_days} days"),
    ).fetchall()
    if len(rows) < min_samples:
        return {}

    def _median(values: list[float]) -> float | None:
        clean = sorted(v for v in values if v is not None and v > 0)
        if not clean:
            return None
        n = len(clean)
        if n % 2:
            return float(clean[n // 2])
        return float((clean[n // 2 - 1] + clean[n // 2]) / 2)

    return {
        "per": _median([float(r["per"]) for r in rows if r["per"] is not None]),
        "pbr": _median([float(r["pbr"]) for r in rows if r["pbr"] is not None]),
        "peg": _median([float(r["peg"]) for r in rows if r["peg"] is not None]),
        "psr": _median([float(r["psr"]) for r in rows if r["psr"] is not None]),
        "ev_ebitda": _median(
            [float(r["ev_ebitda"]) for r in rows if r["ev_ebitda"] is not None]
        ),
        "p_fcf": _median(
            [float(r["p_fcf"]) for r in rows if r["p_fcf"] is not None]
        ),
    }


def get_sector_fundamental_medians(
    conn: sqlite3.Connection,
    market: Market,
    sector: str | None,
    *,
    lookback_days: int = 7,
    min_samples: int = 3,
) -> dict[str, float | None]:
    """같은 (market, sector) 종목들의 최근 revenue/earnings/roe/op_margin/fcf_yield 중앙값.

    valuation 패턴과 동일. negative/zero 값은 제외 후 양수만 median 계산
    (음수 ROE를 양수 median과 비교하면 ratio가 무의미 — factor 측에서
    `value <= 0`일 때 절대 임계치 fallback).
    """
    if not sector:
        return {}
    rows = conn.execute(
        """
        SELECT
          json_extract(fs.raw_values, '$.revenue_growth_yoy') AS revenue_growth_yoy,
          json_extract(fs.raw_values, '$.earnings_growth_yoy') AS earnings_growth_yoy,
          json_extract(fs.raw_values, '$.roe') AS roe,
          json_extract(fs.raw_values, '$.operating_margin') AS operating_margin,
          json_extract(fs.raw_values, '$.fcf_yield') AS fcf_yield
        FROM factor_scores fs
        JOIN tickers t ON t.id = fs.ticker_id
        WHERE t.market = ? AND t.sector = ?
          AND fs.factor_name = 'fundamentals'
          AND fs.date >= date('now', ?)
        """,
        (market, sector, f"-{lookback_days} days"),
    ).fetchall()
    if len(rows) < min_samples:
        return {}

    def _median_positive(values: list[float]) -> float | None:
        clean = sorted(v for v in values if v is not None and v > 0)
        if not clean:
            return None
        n = len(clean)
        if n % 2:
            return float(clean[n // 2])
        return float((clean[n // 2 - 1] + clean[n // 2]) / 2)

    return {
        "revenue_growth_yoy": _median_positive(
            [float(r["revenue_growth_yoy"]) for r in rows if r["revenue_growth_yoy"] is not None]
        ),
        "earnings_growth_yoy": _median_positive(
            [float(r["earnings_growth_yoy"]) for r in rows if r["earnings_growth_yoy"] is not None]
        ),
        "roe": _median_positive(
            [float(r["roe"]) for r in rows if r["roe"] is not None]
        ),
        "operating_margin": _median_positive(
            [float(r["operating_margin"]) for r in rows if r["operating_margin"] is not None]
        ),
        "fcf_yield": _median_positive(
            [float(r["fcf_yield"]) for r in rows if r["fcf_yield"] is not None]
        ),
    }


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
