-- preset: deep_value_kr
-- 한국 깊은 가치주: 저PER + 저PBR + 흑자 + 배당
-- ⚠️ 결과는 매수 권유 아님. 본인 추가 조사 필수.

SELECT
  code,
  name,
  sector,
  ROUND(price, 0) AS price_krw,
  ROUND(per, 2) AS per,
  ROUND(pbr, 2) AS pbr,
  ROUND(roe * 100, 1) AS roe_pct,
  ROUND(dividend_yield * 100, 2) AS dividend_pct,
  ROUND(composite_score, 1) AS score,
  verdict
FROM v_latest_scores
WHERE market = 'KR'
  AND universes LIKE '%KOSPI_200%' OR universes LIKE '%KOSDAQ_150%'
  AND per BETWEEN 3 AND 12
  AND pbr <= 1.2
  AND roe >= 0.05
  AND dividend_yield >= 0.02
  AND composite_score >= 50  -- 주의 영역 제외
ORDER BY (1.0/per + 1.0/pbr + dividend_yield) DESC
LIMIT 20;
