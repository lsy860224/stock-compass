"""섹터 상대 집계 — 같은 (market, sector) 내 순위·밸류에이션/펀더멘털 중앙값.

`factors/valuation`·`factors/fundamentals`가 절대 임계치 대신 sector-relative
점수화에 사용. composite/factor 영속화(scores.py)와는 별개의 읽기 전용 집계 관심사.
"""

from __future__ import annotations

import sqlite3

from stock_compass.markets.base import Market


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
