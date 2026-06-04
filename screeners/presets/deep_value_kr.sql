-- preset: deep_value_kr
-- 한국 저평가 가치주: 섹터상대 저평가(valuation_score) + 저PER·저PBR + 흑자 + 배당
-- ⚠️ 결과는 매수 권유 아님. 본인 추가 조사 필수.
--
-- valuation_score(섹터상대, 항상 산출)를 1차 필터로, PER/PBR 을 보조 필터로 쓴다.
-- PER/PBR 은 KRX 자격증명 있을 때만 채워지므로 `IS NULL OR ...` 로 관대 처리
-- (데이터 없을 때 0건 되지 않도록). dividend_yield 는 fraction(0.015=1.5%).

SELECT
  code,
  name,
  sector,
  ROUND(price, 0) AS price_krw,
  ROUND(valuation_score, 1) AS valuation,
  ROUND(per, 1) AS per,
  ROUND(pbr, 2) AS pbr,
  ROUND(roe * 100, 1) AS roe_pct,
  ROUND(dividend_yield * 100, 2) AS dividend_pct,
  ROUND(composite_score, 1) AS score,
  verdict
FROM v_latest_scores
WHERE market = 'KR'
  AND (universes LIKE '%KOSPI_200%' OR universes LIKE '%KOSDAQ_150%')
  AND valuation_score >= 65               -- 1차: 섹터 대비 저평가
  AND (per IS NULL OR per <= 15)           -- 보조: 저PER (데이터 없으면 통과)
  AND (pbr IS NULL OR pbr <= 2.0)          -- 보조: 저PBR
  AND roe >= 0.05                          -- 흑자 + ROE 5%+
  AND dividend_yield >= 0.015              -- 배당 1.5%+
  AND composite_score >= 50                -- 주의 영역 제외
ORDER BY valuation_score DESC, COALESCE(per, 999) ASC
LIMIT 20;
