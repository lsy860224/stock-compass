-- preset: turnaround
-- 점수 회복 중인 종목 (30일 전 < 50, 현재 >= 65)
-- ⚠️ 단기 회복 ≠ 장기 추세 전환. 본인 추가 조사 필수.

WITH past_scores AS (
  SELECT
    ticker_id,
    total_score AS past_score   -- raw composite_scores 테이블 컬럼명은 total_score
                                 -- (v_latest_scores 뷰에서만 composite_score 로 노출)
  FROM composite_scores
  WHERE date = (
    SELECT MAX(date) FROM composite_scores WHERE date <= date('now', '-30 days')
  )
)
SELECT
  t.code,
  t.name,
  t.market,
  t.sector,
  ROUND(ps.past_score, 1) AS past_30d,
  ROUND(vls.composite_score, 1) AS current,
  ROUND(vls.composite_score - ps.past_score, 1) AS delta,
  ROUND(vls.rsi_14, 1) AS rsi,
  vls.verdict
FROM tickers t
JOIN v_latest_scores vls ON t.id = vls.ticker_id
JOIN past_scores ps ON t.id = ps.ticker_id
WHERE ps.past_score < 50
  AND vls.composite_score >= 65
  AND vls.rsi_14 < 75                    -- 과열 회복은 제외
ORDER BY delta DESC
LIMIT 15;
