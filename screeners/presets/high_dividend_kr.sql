-- preset: high_dividend_kr
-- 한국 고배당 + 재무 안정성
-- ⚠️ 배당락 + 배당세 + 환매조건부 거래 확인 필수.
-- ⚠️ 결과는 매수 권유 아님.
--
-- PER 은 보조 필터로만 사용(KRX 자격증명 없으면 NULL → 관대 통과). 과대평가는
-- valuation_score 로도 거른다. dividend_yield 는 fraction(0.04=4%).

SELECT
  code,
  name,
  sector,
  ROUND(price, 0) AS price_krw,
  ROUND(dividend_yield * 100, 2) AS dividend_pct,
  ROUND(per, 1) AS per,
  ROUND(valuation_score, 1) AS valuation,
  ROUND(roe * 100, 1) AS roe_pct,
  ROUND(composite_score, 1) AS score
FROM v_latest_scores
WHERE market = 'KR'
  AND dividend_yield >= 0.04               -- 배당수익률 4%+ (fraction scale)
  AND (per IS NULL OR per <= 25)           -- 보조: 극단적 고PER 제외
  AND valuation_score >= 40                -- 극단적 고평가 제외
  AND roe >= 0.08                          -- 흑자 + ROE 8%+
  AND composite_score >= 40                -- 주의 영역만 제외
ORDER BY dividend_yield DESC
LIMIT 20;
