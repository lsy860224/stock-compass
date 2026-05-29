"""스크리너용 동적 뷰 — 백테스트의 `v_at_date(:as_of)` CTE 생성.

`v_latest_scores`는 마이그레이션 003의 정적 뷰지만, 백테스트는 시점별 스냅샷이
필요해서 SQLite의 진짜 테이블 함수가 없는 대신 `v_at_date('YYYY-MM-DD')` 를
SQL 안에 표시하면 ScreenerEngine 이 CTE로 inline 치환.

look-ahead 차단: `composite_scores.date <= :as_of` + `tickers.delisted_at`
처리 + universe_members 도 `as_of_date <= :as_of` 기준.
"""

from __future__ import annotations

from datetime import date as date_cls


def build_v_at_date_ctes(as_of: date_cls) -> str:
    """`as_of` 기준 v_at_date 를 4 단계 CTE로 정의 (WITH 절 본문 부분 — `WITH ` 키워드 미포함).

    사용 예:
        WITH {build_v_at_date_ctes(date)}
        SELECT * FROM _vad WHERE composite_score >= 70
    """
    d = as_of.isoformat()
    return f"""_vad_latest AS (
  SELECT ticker_id, MAX(date) AS d
  FROM composite_scores
  WHERE date <= '{d}'
  GROUP BY ticker_id
),
_vad_lc AS (
  SELECT cs.ticker_id, cs.date, cs.total_score, cs.verdict,
         cs.price_at_score, cs.sentiment_source
  FROM composite_scores cs
  JOIN _vad_latest l ON cs.ticker_id = l.ticker_id AND cs.date = l.d
),
_vad_lf AS (
  SELECT
    fs.ticker_id,
    MAX(CASE WHEN factor_name='valuation' THEN score END) AS valuation_score,
    MAX(CASE WHEN factor_name='fundamentals' THEN score END) AS fundamentals_score,
    MAX(CASE WHEN factor_name='technical' THEN score END) AS technical_score,
    MAX(CASE WHEN factor_name='macro' THEN score END) AS macro_score,
    MAX(CASE WHEN factor_name='sentiment' THEN score END) AS sentiment_score,
    MAX(CASE WHEN factor_name='valuation'
             THEN json_extract(raw_values, '$.per') END) AS per,
    MAX(CASE WHEN factor_name='valuation'
             THEN json_extract(raw_values, '$.pbr') END) AS pbr,
    MAX(CASE WHEN factor_name='valuation'
             THEN json_extract(raw_values, '$.peg') END) AS peg,
    MAX(CASE WHEN factor_name='valuation'
             THEN json_extract(raw_values, '$.dividend_yield') END) AS dividend_yield,
    MAX(CASE WHEN factor_name='fundamentals'
             THEN json_extract(raw_values, '$.roe') END) AS roe,
    MAX(CASE WHEN factor_name='fundamentals'
             THEN json_extract(raw_values, '$.revenue_growth_yoy') END) AS revenue_growth_yoy,
    MAX(CASE WHEN factor_name='fundamentals'
             THEN json_extract(raw_values, '$.operating_margin') END) AS operating_margin,
    MAX(CASE WHEN factor_name='fundamentals'
             THEN json_extract(raw_values, '$.market_cap') END) AS market_cap,
    MAX(CASE WHEN factor_name='technical'
             THEN json_extract(raw_values, '$.rsi_14') END) AS rsi_14,
    MAX(CASE WHEN factor_name='technical'
             THEN json_extract(raw_values, '$.ma200_distance') END) AS ma200_distance,
    MAX(CASE WHEN factor_name='technical'
             THEN json_extract(raw_values, '$.volume_zscore') END) AS volume_zscore
  FROM factor_scores fs
  JOIN _vad_latest l ON fs.ticker_id = l.ticker_id AND fs.date = l.d
  GROUP BY fs.ticker_id
),
_vad_tm AS (
  SELECT m.ticker_id, m.market_cap_krw, m.size_bucket
  FROM ticker_meta m
  WHERE m.as_of_date <= '{d}'
    AND m.as_of_date = (
      SELECT MAX(m2.as_of_date) FROM ticker_meta m2
      WHERE m2.ticker_id = m.ticker_id AND m2.as_of_date <= '{d}'
    )
),
_vad AS (
  SELECT
    t.id AS ticker_id, t.code, t.name, t.market, t.sector,
    _vad_lc.price_at_score AS price,
    _vad_lc.total_score AS composite_score,
    _vad_lc.verdict, _vad_lc.sentiment_source,
    _vad_lf.valuation_score, _vad_lf.fundamentals_score, _vad_lf.technical_score,
    _vad_lf.macro_score, _vad_lf.sentiment_score,
    _vad_lf.per, _vad_lf.pbr, _vad_lf.peg, _vad_lf.dividend_yield,
    _vad_lf.roe, _vad_lf.revenue_growth_yoy, _vad_lf.operating_margin,
    _vad_lf.market_cap,
    _vad_tm.market_cap_krw, _vad_tm.size_bucket,
    _vad_lf.rsi_14, _vad_lf.ma200_distance, _vad_lf.volume_zscore,
    _vad_lc.date AS as_of_date
  FROM tickers t
  JOIN _vad_lc ON _vad_lc.ticker_id = t.id
  LEFT JOIN _vad_lf ON _vad_lf.ticker_id = t.id
  LEFT JOIN _vad_tm ON _vad_tm.ticker_id = t.id
  WHERE t.delisted_at IS NULL OR t.delisted_at > '{d}'
)"""
