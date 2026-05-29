# 종목 스크리너 (Screener) 스펙

> Phase 7 산출물. SQL DSL 기반. SQLite 직접 쿼리 + 사전 정의 뷰로 진입장벽 완화.

---

## 1) 설계 원칙

| 원칙 | 적용 |
|---|---|
| **SQL이 원천 언어** | 학습 곡선 인정, 대신 사전 뷰·프리셋·치트시트로 완화 |
| **읽기 전용** | SQLite을 `mode=ro` URI로 열어 DDL/DML 차단 (안전장치) |
| **Point-in-time 정확성** | 모든 뷰가 `as_of` 파라미터 지원 → look-ahead 차단 |
| **유니버스 관리 자동화** | KOSPI 200, KOSDAQ 150, S&P 500, NASDAQ 100 등 일 1회 자동 갱신 |
| **결과는 항상 워치리스트 후보** | 스크리너 결과 → 검토 → 워치리스트 추가 (자동 X) |
| **백테스트 안전장치** | survivorship bias, look-ahead 명시적 처리 |

---

## 2) 사용 흐름 (3가지 진입점)

### 진입점 A: 프리셋 (가장 쉬움)

```bash
uv run stock-compass screen --preset deep_value_kr
uv run stock-compass screen --preset momentum_us --limit 10
uv run stock-compass screen --list-presets
```

### 진입점 B: 저장된 SQL 파일

```bash
uv run stock-compass screen --file screeners/my_strategy.sql
```

`screeners/*.sql` 파일은 `.gitignore` 대상 (개인 전략 보호).

### 진입점 C: 인라인 SQL

```bash
uv run stock-compass screen --sql "
  SELECT * FROM v_latest_scores
  WHERE universe = 'KOSPI_200'
    AND composite_score >= 70
    AND per BETWEEN 5 AND 15
    AND roe >= 0.15
  ORDER BY composite_score DESC
  LIMIT 20
"
```

### 진입점 D: 인터랙티브 REPL

```bash
uv run stock-compass screen --interactive
# > .views          # 뷰 목록
# > .fields v_latest_scores
# > .schema universe_members
# > SELECT * FROM v_latest_scores LIMIT 5;
# > .preset deep_value_kr    # 프리셋 SQL 출력 (수정 후 실행 가능)
```

---

## 3) 사전 정의 뷰 (View 카탈로그)

스크리너는 raw 테이블이 아닌 **뷰**에 쿼리. 뷰는 점수·펀더멘털·테크니컬 데이터를 디노멀라이즈하여 조인 부담 제거.

### 3.1 `v_latest_scores`

각 종목의 **최신 거래일** 점수 + 핵심 지표 (스크리너 99% 케이스에 이거면 충분).

| 칼럼 | 타입 | 출처 |
|---|---|---|
| ticker_id | INTEGER | tickers.id |
| code | TEXT | 종목 코드 |
| name | TEXT | 종목명 |
| market | TEXT | KR / US |
| sector | TEXT | |
| price | REAL | snapshots.close (최신) |
| market_cap | REAL | yfinance/pykrx (현지 통화) |
| market_cap_krw | REAL | ticker_meta (KRW 환산, cross-market 비교) |
| size_bucket | TEXT | ticker_meta (mega/large/mid/small/micro) |
| composite_score | REAL | composite_scores.total_score (최신) |
| verdict | TEXT | 관심권/중립/주의 |
| valuation_score | REAL | factor_scores |
| fundamentals_score | REAL | factor_scores |
| technical_score | REAL | factor_scores |
| macro_score | REAL | factor_scores |
| sentiment_score | REAL | factor_scores |
| **per** | REAL | raw_values JSON에서 추출 |
| **pbr** | REAL | |
| **peg** | REAL | |
| **roe** | REAL | |
| **revenue_growth_yoy** | REAL | |
| **operating_margin** | REAL | |
| **rsi_14** | REAL | |
| **ma200_distance** | REAL | 200일선 대비 이격률 |
| **volume_zscore** | REAL | 20일 거래량 z-score |
| **dividend_yield** | REAL | |
| as_of_date | TEXT | 데이터 기준일 |

### 3.2 `v_score_history`

종목별 일자별 점수 추이 (히스토리 백테스트용).

```sql
ticker_id, code, name, date, composite_score, verdict,
valuation_score, fundamentals_score, technical_score, macro_score, sentiment_score
```

### 3.3 `v_universe`

유니버스(지수 구성) 멤버십 + 최신 데이터.

| 칼럼 | |
|---|---|
| universe_code | KOSPI_200, KOSDAQ_150, SP500, NASDAQ_100, ALL_KR, ALL_US |
| ticker_id, code, name | |
| as_of_date | 멤버십 기준일 |
| weight | 지수 내 비중 (있는 경우) |

### 3.4 `v_at_date(:date)` (테이블 함수 패턴)

특정 시점의 모든 종목 스냅샷. 백테스트 핵심.

```sql
-- "2026-04-22 시점에서 어떤 종목이 점수 70 이상이었는가?"
SELECT * FROM v_at_date('2026-04-22')
WHERE composite_score >= 70 AND market = 'KR';
```

SQLite는 진짜 테이블 함수가 없으므로 실제로는:
```python
# Python layer에서 :date 파라미터를 inject한 CTE로 변환
```

### 3.5 `v_forward_return`

백테스트용 — 특정 시점 이후 수익률.

```sql
ticker_id, code, base_date, base_price,
return_1m, return_3m, return_6m, return_12m
```

---

## 4) 칼럼 치트시트 (`screen --list-fields`)

```
[Valuation]
  per                  PER (배)
  pbr                  PBR (배)
  peg                  PEG (배)
  ev_ebitda           EV/EBITDA
  dividend_yield       배당수익률 (소수, 0.03 = 3%)
  market_cap           시가총액 (현지 통화)
  market_cap_krw       KRW 환산 시가총액 (cross-market 비교)
  size_bucket          규모 등급 (mega/large/mid/small/micro)

[Fundamentals]
  revenue_growth_yoy   매출 성장률 YoY (소수, 0.10 = 10%)
  operating_margin     영업이익률 (소수)
  net_margin           순이익률
  roe                  자기자본수익률 (소수)
  roa                  총자산수익률
  debt_to_equity       부채비율

[Technical]
  rsi_14               RSI(14)
  ma50_distance        50일선 이격률 (소수, +0.05 = 5% 위)
  ma200_distance       200일선 이격률
  volume_zscore        20일 거래량 z-score
  beta_1y              1년 베타 (시장 대비)
  volatility_90d       90일 변동성 (연환산)

[Sentiment]
  sentiment_score      0~100 (높을수록 긍정)
  news_count_7d        최근 7일 뉴스 건수
  tone_avg_30d         최근 30일 평균 톤 (-10 ~ +10)
  disclosure_count_30d 최근 30일 공시 건수 (KR)

[Macro/시장]
  is_risk_on           1 / 0 (VIX < 25)
  vix                  현재 VIX
  us_10y_yield         미국 10년 금리

[메타]
  market               KR / US
  sector               섹터
  universe             지수 멤버십
  days_since_listing   상장일 경과 일수
```

---

## 5) 프리셋 카탈로그 (`screeners/presets/*.sql`)

### 5.1 `deep_value_kr.sql`

```sql
-- 한국 깊은 가치주: 저PER + 저PBR + 흑자 + 배당
SELECT code, name, per, pbr, roe, dividend_yield, composite_score
FROM v_latest_scores
WHERE market = 'KR'
  AND universe IN ('KOSPI_200', 'KOSDAQ_150')
  AND per BETWEEN 3 AND 12
  AND pbr <= 1.2
  AND roe >= 0.05
  AND dividend_yield >= 0.02
  AND composite_score >= 50  -- 주의 영역 제외
ORDER BY (1.0/per + 1.0/pbr + dividend_yield) DESC
LIMIT 20;
```

### 5.2 `value_growth_kr.sql`

```sql
-- 가치+성장 콤보 (GARP)
SELECT code, name, per, peg, roe, revenue_growth_yoy, composite_score
FROM v_latest_scores
WHERE market = 'KR'
  AND per BETWEEN 5 AND 20
  AND peg <= 1.5
  AND roe >= 0.15
  AND revenue_growth_yoy >= 0.10
  AND rsi_14 < 70
  AND composite_score >= 60
ORDER BY composite_score DESC, peg ASC
LIMIT 20;
```

### 5.3 `momentum_us.sql`

```sql
-- 미국 모멘텀: 200MA 위 + 거래량 증가 + RSI 정상
SELECT code, name, ma200_distance, volume_zscore, rsi_14, composite_score
FROM v_latest_scores
WHERE market = 'US'
  AND universe IN ('SP500', 'NASDAQ_100')
  AND ma200_distance BETWEEN 0.05 AND 0.30  -- 5~30% 위
  AND volume_zscore >= 1.0                    -- 평균 이상 거래량
  AND rsi_14 BETWEEN 50 AND 70                -- 과매수 제외
  AND market_cap >= 10000000000               -- $10B+
ORDER BY ma200_distance DESC
LIMIT 15;
```

### 5.4 `oversold_quality_global.sql`

```sql
-- 우량주 단기 과매도 (반등 후보)
SELECT code, name, market, rsi_14, roe, composite_score,
       (composite_score - LAG(composite_score, 14) OVER (PARTITION BY ticker_id ORDER BY as_of_date)) AS score_change_14d
FROM v_latest_scores
WHERE rsi_14 <= 30
  AND roe >= 0.15
  AND operating_margin >= 0.10
  AND market_cap >= 1000000000
ORDER BY rsi_14 ASC
LIMIT 15;
```

### 5.5 `turnaround.sql`

```sql
-- 점수 회복 중인 종목 (30일 전 < 50, 현재 >= 65)
WITH latest AS (
  SELECT ticker_id, composite_score AS now_score
  FROM v_latest_scores
),
past AS (
  SELECT ticker_id, composite_score AS past_score
  FROM v_score_history
  WHERE date = date('now', '-30 days')
)
SELECT t.code, t.name, p.past_score, l.now_score, (l.now_score - p.past_score) AS delta
FROM tickers t
JOIN latest l ON t.id = l.ticker_id
JOIN past p ON t.id = p.ticker_id
WHERE p.past_score < 50
  AND l.now_score >= 65
ORDER BY delta DESC
LIMIT 15;
```

### 5.6 `high_dividend_kr.sql`

```sql
-- 한국 고배당 + 안정성
SELECT code, name, dividend_yield, debt_to_equity, roe, sector
FROM v_latest_scores
WHERE market = 'KR'
  AND dividend_yield >= 0.04          -- 4%+
  AND debt_to_equity <= 1.0
  AND roe >= 0.08
  AND composite_score >= 40
ORDER BY dividend_yield DESC
LIMIT 20;
```

---

## 6) 백테스트 모드

```bash
uv run stock-compass screen --preset value_growth_kr \
  --backtest --start 2025-01-01 --end 2026-05-22 \
  --rebalance monthly \
  --forward-period 3m
```

### 동작

1. `--start` ~ `--end` 사이의 매 `--rebalance` 시점에 스크리너 실행 → 종목 선정
2. 선정 후 `--forward-period` (1m/3m/6m/12m) 후 가격 변화 측정
3. 결과: 시점별 종목 + 평균 수익률 + 적중률 + 벤치마크 대비

### 출력

```
백테스트 기간: 2025-01-01 ~ 2026-05-22 (17개월)
리밸런싱: 매월
포워드: 3개월

월별 결과:
  2025-01-01: 18종목 선정, 평균 +4.2% (3m)
  2025-02-01: 22종목 선정, 평균 -1.5% (3m)
  ...

전체 통계:
  총 선정: 312건
  평균 수익률 (3m): +3.1%
  중앙값: +1.8%
  적중률 (양수): 58%
  벤치마크 (KOSPI): +1.7%
  알파: +1.4%pt
  최대 드로다운: -22.5%

⚠️ 주의: 거래비용·세금·슬리피지 미반영. survivorship bias 처리됨 (상장폐지 종목 포함).
⚠️ 과거 성과가 미래를 보장하지 않음.
```

### Look-ahead bias 방지

- `v_at_date(:date)` 뷰는 해당 날짜까지의 데이터만 노출
- `revenue_growth_yoy` 등은 **공시일 기준**으로 사용 (분기 종료일 X)
- DART/SEC 공시일을 별도 테이블에 저장: `disclosure_filings(ticker_id, filing_date, fiscal_period)`

### Survivorship bias 처리

- 상장폐지·합병 종목은 `tickers.delisted_at` 칼럼으로 표시
- 백테스트 시 해당 시점 살아있던 종목만 유니버스로
- pykrx로 KOSPI 상장폐지 이력 1회 수집 (`scripts/seed_delisted.sh`)

---

## 7) 결과 → 다음 액션

### 7.1 워치리스트 추가

```bash
uv run stock-compass screen --preset deep_value_kr --add-to-watchlist --group screening
# 결과 종목들이 새 그룹 'screening'으로 워치리스트에 추가됨
# 다음 batch부터 일일 점수 추적 시작
```

### 7.2 Craft 노트로 발행

```bash
uv run stock-compass screen --preset value_growth_kr --to-craft
# data/craft_export/screening-YYYYMMDD-value_growth_kr.md 생성
```

### 7.3 CSV/JSON 내보내기

```bash
uv run stock-compass screen --preset momentum_us --format csv > momentum.csv
uv run stock-compass screen --preset momentum_us --format json
```

### 7.4 하이브리드 Sentiment — 결과 종목 심층 분석 프롬프트 생성

```bash
# 스크리너 결과 10종목에 대해 Claude.ai에 붙여넣을 프롬프트 생성
uv run stock-compass screen --preset value_growth_kr --prompt-deepdive
# data/prompts/deepdive-YYYYMMDD.md 생성

# 이 파일을 Claude.ai (Pro 구독)에 복붙 → 응답 받아서:
uv run stock-compass sentiment import data/prompts/deepdive-YYYYMMDD-response.txt
```

자세한 흐름은 `docs/HYBRID_SENTIMENT.md` 참조.

---

## 8) 유니버스 자동 갱신

### KR (`screener/universes/kr.py`)

| 유니버스 | 출처 | 갱신 주기 |
|---|---|---|
| KOSPI_200 | `pykrx.stock.get_index_portfolio_deposit_file("1028")` | 매일 |
| KOSDAQ_150 | `pykrx.stock.get_index_portfolio_deposit_file("2203")` | 매일 |
| ALL_KR | `pykrx.stock.get_market_ticker_list` (KOSPI+KOSDAQ) | 매일 |

### US (`screener/universes/us.py`)

| 유니버스 | 출처 | 갱신 주기 |
|---|---|---|
| SP500 | Wikipedia 스크래핑 (List of S&P 500 companies) | 매주 |
| NASDAQ_100 | Wikipedia 스크래핑 (NASDAQ-100 components) | 매주 |
| DOW30 | Wikipedia 스크래핑 | 매주 |
| ALL_US | (대용량 — 일단 미지원) | — |

### 저장

```sql
universe_members (
  universe_code TEXT,
  ticker_id INTEGER,
  as_of_date TEXT,
  weight REAL,
  PRIMARY KEY (universe_code, ticker_id, as_of_date)
)
```

이력 보존 → 백테스트 시 "그 시점의 KOSPI 200" 정확히 재현 가능.

---

## 9) 인터랙티브 REPL 명령

```
.help              # 도움말
.views             # 모든 뷰 목록
.tables            # raw 테이블 목록 (직접 쿼리 가능)
.fields <view>     # 뷰의 칼럼 목록 + 설명
.schema <table>    # 테이블 스키마
.preset <name>     # 프리셋 SQL 출력 (편집 후 실행 가능)
.universes         # 사용 가능한 유니버스 목록
.last              # 마지막 결과 다시 보기
.export csv|json   # 마지막 결과 내보내기
.save <name>       # 마지막 SQL을 screeners/<name>.sql 로 저장
.quit              # 종료
```

---

## 10) 안전장치

### SQL Injection / DDL 차단

```python
def open_screener_db(db_path: str) -> sqlite3.Connection:
    """읽기 전용 모드로 열기 — DDL/DML 차단."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
```

→ `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER` 모두 자동 거부됨.

### 쿼리 타임아웃

```python
conn.execute("PRAGMA busy_timeout = 5000;")  # 5초
# 추가로 Python signal로 30초 hard timeout
```

### 결과 행 수 제한

- 기본 `LIMIT` 자동 적용 (없으면 50)
- `--limit 500` 명시 가능, 최대 5000 강제

### 백테스트 비용 안전장치

- 백테스트 1회 실행 시 예상 토큰 사용량 사전 계산
- $0.10 초과 시 사용자 확인 프롬프트

---

## 11) 한계 명시 (CLAUDE.md 1) 원칙 준수)

스크리너 결과 화면 하단 자동 출력:

```
⚠️ 스크리너 결과는 통계적 후보 종목이며, 매수 권유가 아닙니다.

알려진 한계:
- 과거 데이터 기반: 시장 레짐 변화 시 무효화
- 거래비용·세금·슬리피지 미반영
- 펀더멘털 데이터 공시 시차 존재
- 백테스트의 과거 성과 ≠ 미래 보장

다음 액션은 본인 판단으로:
- 개별 종목 심층 조사 (사업·재무·뉴스)
- 분할 매수 / 위험 관리 계획
- 본인 포트폴리오 비중 검토
```

---

## 12) Phase 7 구현 순서 (총 2일)

| 단계 | 소요 | 산출물 |
|---|---|---|
| 7-1. 뷰 + universe_members 테이블 | 4h | DB 마이그레이션 + 시드 |
| 7-2. 유니버스 자동 갱신 (KR + US) | 4h | `screener/universes/*.py` |
| 7-3. SQL 실행 엔진 (RO 모드) | 2h | `screener/engine.py` |
| 7-4. CLI (`screen` 명령 + 5개 진입점) | 4h | `cli.py` 확장 |
| 7-5. 프리셋 6개 작성 | 2h | `screeners/presets/*.sql` |
| 7-6. 백테스트 엔진 (기본만) | 6h | `screener/backtest.py` |
| 7-7. 출력 (terminal/CSV/Craft/prompt-deepdive) | 4h | `output/` 확장 |
| 7-8. 인터랙티브 REPL | 2h | (선택) |
| 7-9. 테스트 + 문서 | 4h | tests, README 업데이트 |
| **합계** | **약 32h** | (3~4일 풀타임 / 1.5주 야간) |
