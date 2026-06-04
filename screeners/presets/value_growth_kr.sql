-- preset: value_growth_kr
-- 가치+성장 콤보 (GARP — Growth at Reasonable Price)
-- ⚠️ 결과는 매수 권유 아님. 본인 추가 조사 필수.
--
-- valuation_score(섹터상대)로 "reasonable price"를 1차 판정하고, PER/PEG 를 보조로
-- 쓴다 (KRX 자격증명 없으면 NULL → 관대 통과). dividend/per 등은 데이터 의존.

SELECT
  code,
  name,
  sector,
  ROUND(valuation_score, 1) AS valuation,
  ROUND(per, 1) AS per,
  ROUND(peg, 2) AS peg,
  ROUND(roe * 100, 1) AS roe_pct,
  ROUND(revenue_growth_yoy * 100, 1) AS revenue_growth_pct,
  ROUND(rsi_14, 1) AS rsi,
  ROUND(composite_score, 1) AS score
FROM v_latest_scores
WHERE market = 'KR'
  AND valuation_score >= 50               -- 1차: 섹터 대비 합리적 밸류
  AND (per IS NULL OR per <= 30)           -- 보조: GARP 는 저평가까진 아니어도 과대평가 제외
  AND (peg IS NULL OR peg <= 1.5)          -- 보조: 성장 대비 합리적 (데이터 없으면 통과)
  AND roe >= 0.15                          -- 고ROE 우량
  AND revenue_growth_yoy >= 0.10           -- 매출 성장 10%+
  AND rsi_14 < 70                          -- 과매수 제외
  AND composite_score >= 60
ORDER BY composite_score DESC, valuation_score DESC
LIMIT 20;
