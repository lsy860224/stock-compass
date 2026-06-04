-- preset: value_growth_kr
-- 가치+성장 콤보 (GARP — Growth at Reasonable Price)
-- ⚠️ 결과는 매수 권유 아님. 본인 추가 조사 필수.
--
-- NOTE: KR PER/PEG 는 데이터 소스 한계로 누락 빈번 → "reasonable price" 는
--   valuation_score(섹터 상대 저평가, 항상 산출)로 대체 판정한다.

SELECT
  code,
  name,
  sector,
  ROUND(valuation_score, 1) AS valuation,
  ROUND(roe * 100, 1) AS roe_pct,
  ROUND(revenue_growth_yoy * 100, 1) AS revenue_growth_pct,
  ROUND(rsi_14, 1) AS rsi,
  ROUND(composite_score, 1) AS score
FROM v_latest_scores
WHERE market = 'KR'
  AND valuation_score >= 50          -- 섹터 대비 합리적 밸류 (저평가까진 아니어도)
  AND roe >= 0.15                    -- 고ROE 우량
  AND revenue_growth_yoy >= 0.10     -- 매출 성장 10%+
  AND rsi_14 < 70                    -- 과매수 제외
  AND composite_score >= 60
ORDER BY composite_score DESC, valuation_score DESC
LIMIT 20;
