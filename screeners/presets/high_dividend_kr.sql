-- preset: high_dividend_kr
-- 한국 고배당 + 재무 안정성
-- ⚠️ 배당락 + 배당세 + 환매조건부 거래 확인 필수.
-- ⚠️ 결과는 매수 권유 아님.

SELECT
  code,
  name,
  sector,
  ROUND(price, 0) AS price_krw,
  ROUND(dividend_yield * 100, 2) AS dividend_pct,
  ROUND(per, 1) AS per,
  ROUND(roe * 100, 1) AS roe_pct,
  ROUND(composite_score, 1) AS score
FROM v_latest_scores
WHERE market = 'KR'
  AND dividend_yield >= 0.04           -- 배당수익률 4%+
  AND per IS NOT NULL AND per <= 20   -- 너무 고평가 제외
  AND roe >= 0.08                      -- 흑자 + ROE 8%+
  AND composite_score >= 40            -- 주의 영역만 제외
ORDER BY dividend_yield DESC
LIMIT 20;
