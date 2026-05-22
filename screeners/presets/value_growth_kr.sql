-- preset: value_growth_kr
-- 가치+성장 콤보 (GARP — Growth at Reasonable Price)
-- ⚠️ 결과는 매수 권유 아님. 본인 추가 조사 필수.

SELECT
  code,
  name,
  sector,
  ROUND(per, 1) AS per,
  ROUND(peg, 2) AS peg,
  ROUND(roe * 100, 1) AS roe_pct,
  ROUND(revenue_growth_yoy * 100, 1) AS revenue_growth_pct,
  ROUND(rsi_14, 1) AS rsi,
  ROUND(composite_score, 1) AS score
FROM v_latest_scores
WHERE market = 'KR'
  AND per BETWEEN 5 AND 20
  AND peg IS NOT NULL AND peg <= 1.5
  AND roe >= 0.15
  AND revenue_growth_yoy >= 0.10
  AND rsi_14 < 70                    -- 과매수 제외
  AND composite_score >= 60
ORDER BY composite_score DESC, peg ASC
LIMIT 20;
