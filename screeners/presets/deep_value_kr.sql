-- preset: deep_value_kr
-- 한국 저평가 가치주: 높은 Valuation 팩터점수(섹터 상대 저평가) + 흑자 + 배당
-- ⚠️ 결과는 매수 권유 아님. 본인 추가 조사 필수.
--
-- NOTE: KR 종목은 yfinance/pykrx 가 PER/PBR 을 제공하지 않을 때가 많다(KRX 로그인 필요).
--   그래서 raw PER/PBR 대신 valuation_score(peg·psr·ev_ebitda·p_fcf 섹터중앙값 대비
--   점수, 항상 산출됨)로 "저평가"를 판정한다. dividend_yield 는 fraction(0.015=1.5%).

SELECT
  code,
  name,
  sector,
  ROUND(price, 0) AS price_krw,
  ROUND(valuation_score, 1) AS valuation,
  ROUND(roe * 100, 1) AS roe_pct,
  ROUND(dividend_yield * 100, 2) AS dividend_pct,
  ROUND(composite_score, 1) AS score,
  verdict
FROM v_latest_scores
WHERE market = 'KR'
  AND (universes LIKE '%KOSPI_200%' OR universes LIKE '%KOSDAQ_150%')
  AND valuation_score >= 65         -- 섹터 대비 저평가 (Valuation 팩터 상위)
  AND roe >= 0.05                    -- 흑자 + ROE 5%+
  AND dividend_yield >= 0.015        -- 배당 1.5%+ (fraction scale)
  AND composite_score >= 50          -- 주의 영역 제외
ORDER BY valuation_score DESC, dividend_yield DESC
LIMIT 20;
