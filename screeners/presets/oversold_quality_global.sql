-- preset: oversold_quality_global
-- 우량주 단기 과매도 (반등 후보, 한국+미국 통합)
-- ⚠️ 결과는 매수 권유 아님. 과매도 = 더 빠질 수 있음.

SELECT
  code,
  name,
  market,
  sector,
  ROUND(rsi_14, 1) AS rsi,
  ROUND(roe * 100, 1) AS roe_pct,
  ROUND(operating_margin * 100, 1) AS op_margin_pct,
  ROUND(composite_score, 1) AS score,
  verdict
FROM v_latest_scores
WHERE rsi_14 <= 35                          -- 과매도 진입
  AND roe >= 0.15                            -- 우량 (ROE 15% 이상)
  AND operating_margin >= 0.10               -- 영업이익률 10% 이상
  AND composite_score >= 45                  -- 너무 망가진 종목 제외
ORDER BY rsi_14 ASC, composite_score DESC
LIMIT 15;
