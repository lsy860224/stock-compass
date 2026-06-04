-- preset: momentum_us
-- 미국 모멘텀: 200MA 위 + 거래량 증가 + RSI 정상범위
-- ⚠️ 결과는 매수 권유 아님. 본인 추가 조사 필수.

SELECT
  code AS ticker,
  name,
  sector,
  ROUND(price, 2) AS price_usd,
  ROUND(ma200_distance * 100, 1) AS above_200ma_pct,
  ROUND(volume_zscore, 2) AS volume_z,
  ROUND(rsi_14, 1) AS rsi,
  ROUND(composite_score, 1) AS score
FROM v_latest_scores
WHERE market = 'US'
  AND (universes LIKE '%SP500%' OR universes LIKE '%NASDAQ_100%')
  AND ma200_distance BETWEEN 0.05 AND 0.30      -- 5~30% 위
  AND volume_zscore >= 0.5                       -- 평균 이상 거래량 (모멘텀 초입 포함)
  AND rsi_14 BETWEEN 50 AND 70                   -- 과매수 제외
  AND composite_score >= 50                      -- 모멘텀주는 valuation에 눌려 composite가 낮음
                                                 -- → 절대 고점수(>=65) 요구는 부적절, '주의'(<50)만 제외
ORDER BY (ma200_distance + volume_zscore / 10) DESC
LIMIT 15;
