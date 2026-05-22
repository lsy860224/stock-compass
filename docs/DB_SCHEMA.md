# 데이터베이스 스키마: stock-compass

> SQLite 단일 파일 (`data/stock_compass.db`). 단일 사용자 가정.
> 모든 시각은 UTC ISO 8601 문자열로 저장.

---

## ERD 요약

```
tickers (마스터)
  ├── 1:N → snapshots          (일별 OHLCV)
  ├── 1:N → factor_scores      (일별 팩터별 점수)
  ├── 1:N → composite_scores   (일별 종합 점수)
  ├── 1:N → news_summaries     (Claude 요약 — api/manual_prompt/fallback)
  ├── 1:N → alerts             (발화 이력)
  ├── 1:N → trades             (본인 매매 일지)
  ├── 1:N → universe_members   (지수 멤버십, Phase 7)
  └── 1:N → watchlists         (워치리스트 그룹, Phase 7)

daily_token_usage (Anthropic 비용 추적, Phase 4)
screener_runs (스크리너 실행 이력, Phase 7)
schema_version (마이그레이션 추적)
```

---

## 테이블 정의

### `schema_version`

마이그레이션 추적용.

| 칼럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| version | INTEGER | PRIMARY KEY | 현재 스키마 버전 |
| applied_at | TEXT | NOT NULL | ISO 8601 UTC |

---

### `tickers`

종목 마스터.

| 칼럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| code | TEXT | NOT NULL | 정규화 코드 (KR: 005930, US: AAPL) |
| market | TEXT | NOT NULL CHECK(market IN ('KR','US')) | |
| name | TEXT | NOT NULL | 종목명 |
| sector | TEXT | NULL | 섹터 분류 |
| currency | TEXT | NOT NULL CHECK(currency IN ('KRW','USD')) | |
| yfinance_symbol | TEXT | NOT NULL | yfinance 호출용 (005930.KS, AAPL) |
| created_at | TEXT | NOT NULL DEFAULT (datetime('now')) | |
| updated_at | TEXT | NOT NULL DEFAULT (datetime('now')) | |

**제약**: `UNIQUE(market, code)`

---

### `snapshots`

일별 OHLCV 캐시 (yfinance 응답 저장).

| 칼럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | INTEGER | PK AUTOINCREMENT | |
| ticker_id | INTEGER | NOT NULL FK→tickers.id ON DELETE CASCADE | |
| date | TEXT | NOT NULL | YYYY-MM-DD (거래일, 현지 시장 기준) |
| open | REAL | NULL | |
| high | REAL | NULL | |
| low | REAL | NULL | |
| close | REAL | NULL | |
| volume | INTEGER | NULL | |
| adj_close | REAL | NULL | 배당·분할 조정 |
| source | TEXT | NOT NULL | 'yfinance' / 'pykrx' / 'manual' |
| created_at | TEXT | NOT NULL DEFAULT (datetime('now')) | |

**제약**: `UNIQUE(ticker_id, date)`

**인덱스**:
```sql
CREATE INDEX idx_snapshots_ticker_date ON snapshots(ticker_id, date DESC);
```

---

### `factor_scores`

일별 팩터별 점수 (5개 팩터 × N종목 × N일).

| 칼럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | INTEGER | PK AUTOINCREMENT | |
| ticker_id | INTEGER | NOT NULL FK→tickers.id | |
| date | TEXT | NOT NULL | YYYY-MM-DD |
| factor_name | TEXT | NOT NULL CHECK(factor_name IN ('valuation','fundamentals','technical','macro','sentiment')) | |
| score | REAL | NOT NULL CHECK(score BETWEEN 0 AND 100) | |
| weight | REAL | NOT NULL CHECK(weight BETWEEN 0 AND 1) | 계산 시점 가중치 |
| raw_values | TEXT | NULL | JSON dict (디버그용 원본 지표) |
| note | TEXT | NULL | 사람이 읽는 설명 |
| computed_at | TEXT | NOT NULL DEFAULT (datetime('now')) | |

**제약**: `UNIQUE(ticker_id, date, factor_name)`

**인덱스**:
```sql
CREATE INDEX idx_factor_scores_ticker_date ON factor_scores(ticker_id, date DESC);
CREATE INDEX idx_factor_scores_factor ON factor_scores(factor_name, date DESC);
```

---

### `composite_scores`

일별 종합 점수 (의사결정 보조의 최종 산출물).

| 칼럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | INTEGER | PK AUTOINCREMENT | |
| ticker_id | INTEGER | NOT NULL FK→tickers.id | |
| date | TEXT | NOT NULL | YYYY-MM-DD |
| total_score | REAL | NOT NULL CHECK(total_score BETWEEN 0 AND 100) | 가중 평균 |
| verdict | TEXT | NOT NULL CHECK(verdict IN ('관심권','중립','주의')) | |
| price_at_score | REAL | NULL | 점수 산출 시점 종가 |
| computed_at | TEXT | NOT NULL DEFAULT (datetime('now')) | |

**제약**: `UNIQUE(ticker_id, date)`

**⚠️ verdict는 BUY/SELL 같은 명령형 X. "관심권/중립/주의"만 허용 (CLAUDE.md 1) 원칙).**

**인덱스**:
```sql
CREATE INDEX idx_composite_ticker_date ON composite_scores(ticker_id, date DESC);
CREATE INDEX idx_composite_date_score ON composite_scores(date DESC, total_score DESC);
```

---

### `news_summaries`

Claude 요약 결과 캐시.

| 칼럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | INTEGER | PK AUTOINCREMENT | |
| ticker_id | INTEGER | NOT NULL FK→tickers.id | |
| source_url | TEXT | NOT NULL | 원문 URL (중복 방지 키) |
| source_type | TEXT | NOT NULL CHECK(source_type IN ('news','disclosure')) | |
| source | TEXT | NOT NULL CHECK(source IN ('api','manual_prompt','fallback')) DEFAULT 'api' | 어느 경로로 생성됐는가 |
| published_at | TEXT | NOT NULL | 원문 발행 ISO 8601 |
| summary | TEXT | NOT NULL | Claude 요약 (3줄, 자체 표현) |
| tone_score | REAL | NOT NULL CHECK(tone_score BETWEEN -10 AND 10) | 부정~긍정 |
| keywords | TEXT | NULL | JSON array |
| tokens_used | INTEGER | NULL | 비용 추적 (api만) |
| model | TEXT | NOT NULL | 'claude-haiku-4-5-...' 또는 'manual_claude_ai' |
| batch_id | TEXT | NULL | 하이브리드 import 시 batch 추적 |
| created_at | TEXT | NOT NULL DEFAULT (datetime('now')) | |

**제약**: `UNIQUE(ticker_id, source_url)`

**인덱스**:
```sql
CREATE INDEX idx_news_ticker_pub ON news_summaries(ticker_id, published_at DESC);
```

---

### `alerts`

발화된 알림 이력 + 중복 방지.

| 칼럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | INTEGER | PK AUTOINCREMENT | |
| ticker_id | INTEGER | NOT NULL FK→tickers.id | |
| trigger_type | TEXT | NOT NULL CHECK(trigger_type IN ('threshold_buy','threshold_caution','delta_up','delta_down','daily')) | |
| score_before | REAL | NULL | |
| score_after | REAL | NOT NULL | |
| message | TEXT | NOT NULL | 사용자에게 표시된 메시지 |
| delivered_via | TEXT | NOT NULL | 'macos_notify' / 'craft' / 'terminal' (콤마 구분) |
| fired_at | TEXT | NOT NULL DEFAULT (datetime('now')) | UTC ISO 8601 |

**중복 방지 쿼리** (alerts 발화 전 체크):
```sql
SELECT COUNT(*) FROM alerts
WHERE ticker_id = ?
  AND trigger_type = ?
  AND fired_at > datetime('now', '-24 hours');
```

**인덱스**:
```sql
CREATE INDEX idx_alerts_ticker_trigger_time ON alerts(ticker_id, trigger_type, fired_at DESC);
```

---

### `trades`

본인 매매 일지 (편향 분석 핵심 자산).

| 칼럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | INTEGER | PK AUTOINCREMENT | |
| ticker_id | INTEGER | NOT NULL FK→tickers.id | |
| side | TEXT | NOT NULL CHECK(side IN ('buy','sell')) | |
| price | REAL | NOT NULL CHECK(price > 0) | |
| qty | REAL | NOT NULL CHECK(qty > 0) | 분할 매매 고려 소수 허용 |
| executed_at | TEXT | NOT NULL DEFAULT (datetime('now')) | 매매 시각 |
| score_at_trade | REAL | NULL | 매매 시점 종합 점수 (편향 분석용) |
| reason | TEXT | NULL | 본인 메모 (왜 샀는가/팔았는가) |
| tag | TEXT | NULL | 'planned' / 'impulse' / 'rebalance' 등 |
| created_at | TEXT | NOT NULL DEFAULT (datetime('now')) | |

**인덱스**:
```sql
CREATE INDEX idx_trades_ticker_time ON trades(ticker_id, executed_at DESC);
```

**편향 분석 쿼리 예시** (충동 매수 점수 분포):
```sql
SELECT AVG(score_at_trade) AS avg_score, COUNT(*) AS n
FROM trades
WHERE tag = 'impulse' AND side = 'buy';
```

---

## 마이그레이션 SQL (전체 초기 생성)

```sql
-- 001_initial.sql

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tickers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL,
  market TEXT NOT NULL CHECK(market IN ('KR','US')),
  name TEXT NOT NULL,
  sector TEXT,
  currency TEXT NOT NULL CHECK(currency IN ('KRW','USD')),
  yfinance_symbol TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(market, code)
);

CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  volume INTEGER,
  adj_close REAL,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(ticker_id, date)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_date ON snapshots(ticker_id, date DESC);

CREATE TABLE IF NOT EXISTS factor_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  factor_name TEXT NOT NULL CHECK(factor_name IN ('valuation','fundamentals','technical','macro','sentiment')),
  score REAL NOT NULL CHECK(score BETWEEN 0 AND 100),
  weight REAL NOT NULL CHECK(weight BETWEEN 0 AND 1),
  raw_values TEXT,
  note TEXT,
  computed_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(ticker_id, date, factor_name)
);
CREATE INDEX IF NOT EXISTS idx_factor_scores_ticker_date ON factor_scores(ticker_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_factor_scores_factor ON factor_scores(factor_name, date DESC);

CREATE TABLE IF NOT EXISTS composite_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  total_score REAL NOT NULL CHECK(total_score BETWEEN 0 AND 100),
  verdict TEXT NOT NULL CHECK(verdict IN ('관심권','중립','주의')),
  price_at_score REAL,
  computed_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(ticker_id, date)
);
CREATE INDEX IF NOT EXISTS idx_composite_ticker_date ON composite_scores(ticker_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_composite_date_score ON composite_scores(date DESC, total_score DESC);

CREATE TABLE IF NOT EXISTS news_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  source_url TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK(source_type IN ('news','disclosure')),
  published_at TEXT NOT NULL,
  summary TEXT NOT NULL,
  tone_score REAL NOT NULL CHECK(tone_score BETWEEN -10 AND 10),
  keywords TEXT,
  tokens_used INTEGER,
  model TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(ticker_id, source_url)
);
CREATE INDEX IF NOT EXISTS idx_news_ticker_pub ON news_summaries(ticker_id, published_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  trigger_type TEXT NOT NULL CHECK(trigger_type IN ('threshold_buy','threshold_caution','delta_up','delta_down','daily')),
  score_before REAL,
  score_after REAL NOT NULL,
  message TEXT NOT NULL,
  delivered_via TEXT NOT NULL,
  fired_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alerts_ticker_trigger_time ON alerts(ticker_id, trigger_type, fired_at DESC);

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  side TEXT NOT NULL CHECK(side IN ('buy','sell')),
  price REAL NOT NULL CHECK(price > 0),
  qty REAL NOT NULL CHECK(qty > 0),
  executed_at TEXT NOT NULL DEFAULT (datetime('now')),
  score_at_trade REAL,
  reason TEXT,
  tag TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trades_ticker_time ON trades(ticker_id, executed_at DESC);

-- 트리거: updated_at 자동 갱신
CREATE TRIGGER IF NOT EXISTS tickers_updated_at
AFTER UPDATE ON tickers
BEGIN
  UPDATE tickers SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- 마이그레이션 완료 기록
INSERT INTO schema_version (version) VALUES (1);
```

---

## 백업 정책

- 매일 06:00 KST (launchd 별도 잡):
  - `cp data/stock_compass.db data/backups/stock_compass-YYYYMMDD.db`
  - 30일 초과 백업 자동 삭제
- 외장 디스크 백업은 사용자 수동 (Time Machine으로 충분)

---

## 자주 쓰는 쿼리

### 1. 워치리스트 오늘 점수 순위

```sql
SELECT t.code, t.name, c.total_score, c.verdict
FROM composite_scores c
JOIN tickers t ON c.ticker_id = t.id
WHERE c.date = date('now', 'localtime')
ORDER BY c.total_score DESC;
```

### 2. 특정 종목 30일 점수 추이

```sql
SELECT date, total_score, verdict
FROM composite_scores c
JOIN tickers t ON c.ticker_id = t.id
WHERE t.code = '005930' AND t.market = 'KR'
  AND date >= date('now', '-30 days')
ORDER BY date ASC;
```

### 3. 본인 매매 시점 평균 점수 (편향 분석)

```sql
SELECT
  t.code, t.name,
  tr.side,
  COUNT(*) AS trade_count,
  ROUND(AVG(tr.score_at_trade), 1) AS avg_score
FROM trades tr
JOIN tickers t ON tr.ticker_id = t.id
GROUP BY t.id, tr.side;
```

### 4. 24시간 내 동일 알림 발화 여부 (중복 방지)

```sql
SELECT 1 FROM alerts
WHERE ticker_id = :ticker_id
  AND trigger_type = :trigger_type
  AND fired_at > datetime('now', '-24 hours')
LIMIT 1;
```

---

## Phase 4 신규 테이블 — 하이브리드 Sentiment

### `daily_token_usage`

Anthropic API 일일 사용량 추적 (비용 안전장치).

| 칼럼 | 타입 | 설명 |
|---|---|---|
| id | INTEGER | PK AUTOINCREMENT |
| date | TEXT | YYYY-MM-DD (KST 기준) |
| model | TEXT | claude-haiku-4-5 / sonnet-4-6 등 |
| mode | TEXT | api / manual_prompt |
| input_tokens | INTEGER | 누적 입력 토큰 |
| output_tokens | INTEGER | 누적 출력 토큰 |
| call_count | INTEGER | 호출 횟수 |
| estimated_cost_usd | REAL | 예상 비용 (모델별 가격 적용) |
| updated_at | TEXT | DEFAULT (datetime('now')) |

**제약**: `UNIQUE(date, model, mode)`

**일일 한도 체크 쿼리** (API 호출 전):
```sql
SELECT
  COALESCE(SUM(input_tokens), 0) AS used_input,
  COALESCE(SUM(output_tokens), 0) AS used_output
FROM daily_token_usage
WHERE date = date('now', 'localtime')
  AND mode = 'api';
```

### `composite_scores` 칼럼 추가

```sql
ALTER TABLE composite_scores ADD COLUMN sentiment_source TEXT
  CHECK(sentiment_source IN ('api','manual_prompt','fallback','cache')) DEFAULT 'api';
```

---

## Phase 7 신규 테이블·뷰 — 종목 스크리너

### `universe_members`

지수 멤버십 (날짜별 이력 보존 → 백테스트 정확성).

| 칼럼 | 타입 | 설명 |
|---|---|---|
| universe_code | TEXT | KOSPI_200, KOSDAQ_150, SP500, NASDAQ_100, DOW30, ALL_KR |
| ticker_id | INTEGER | FK → tickers.id |
| as_of_date | TEXT | 멤버십 기준일 YYYY-MM-DD |
| weight | REAL | 지수 내 비중 (있는 경우, 0~1) |
| created_at | TEXT | DEFAULT (datetime('now')) |

**제약**: `PRIMARY KEY (universe_code, ticker_id, as_of_date)`

**인덱스**:
```sql
CREATE INDEX idx_universe_code_date ON universe_members(universe_code, as_of_date DESC);
CREATE INDEX idx_universe_ticker ON universe_members(ticker_id);
```

### `watchlists`

워치리스트 그룹 (env의 단일 리스트를 확장).

| 칼럼 | 타입 | 설명 |
|---|---|---|
| id | INTEGER | PK AUTOINCREMENT |
| ticker_id | INTEGER | FK → tickers.id |
| group_name | TEXT | 'core' / 'screening' / 'core_kr' / 본인 정의 |
| added_at | TEXT | DEFAULT (datetime('now')) |
| added_by | TEXT | 'env' / 'manual' / 'screener:<preset>' |
| notes | TEXT | NULL |

**제약**: `UNIQUE(ticker_id, group_name)`

### `screener_runs`

스크리너 실행 이력 (감사 + 재실행).

| 칼럼 | 타입 | 설명 |
|---|---|---|
| id | INTEGER | PK AUTOINCREMENT |
| run_at | TEXT | DEFAULT (datetime('now')) |
| preset_name | TEXT | NULL (preset 사용 시) |
| sql_text | TEXT | 실행된 최종 SQL |
| result_count | INTEGER | 결과 행 수 |
| result_tickers | TEXT | JSON array (ticker_id들) |
| elapsed_ms | INTEGER | 실행 시간 |
| is_backtest | INTEGER | 0 / 1 |
| backtest_period | TEXT | NULL or "2025-01-01_2026-05-22" |

### `tickers` 칼럼 추가

```sql
ALTER TABLE tickers ADD COLUMN delisted_at TEXT;  -- ISO date, NULL이면 활성
```

---

## Phase 7 뷰 (View) DDL

> 모든 뷰는 SQL 직접 정의. SQLite는 materialized view 미지원이므로 실시간 계산.
> 성능 이슈 시 → 일일 batch 끝에 별도 `cache_*` 테이블로 덤프.

### `v_latest_scores`

```sql
CREATE VIEW v_latest_scores AS
WITH latest_dates AS (
  SELECT ticker_id, MAX(date) AS latest_date
  FROM composite_scores
  GROUP BY ticker_id
),
latest_composite AS (
  SELECT cs.*
  FROM composite_scores cs
  JOIN latest_dates ld
    ON cs.ticker_id = ld.ticker_id AND cs.date = ld.latest_date
),
latest_factors AS (
  SELECT
    fs.ticker_id,
    MAX(CASE WHEN factor_name='valuation' THEN score END) AS valuation_score,
    MAX(CASE WHEN factor_name='fundamentals' THEN score END) AS fundamentals_score,
    MAX(CASE WHEN factor_name='technical' THEN score END) AS technical_score,
    MAX(CASE WHEN factor_name='macro' THEN score END) AS macro_score,
    MAX(CASE WHEN factor_name='sentiment' THEN score END) AS sentiment_score,
    -- raw_values JSON에서 핵심 지표 추출 (SQLite JSON1 확장)
    MAX(CASE WHEN factor_name='valuation' THEN json_extract(raw_values, '$.per') END) AS per,
    MAX(CASE WHEN factor_name='valuation' THEN json_extract(raw_values, '$.pbr') END) AS pbr,
    MAX(CASE WHEN factor_name='valuation' THEN json_extract(raw_values, '$.peg') END) AS peg,
    MAX(CASE WHEN factor_name='valuation' THEN json_extract(raw_values, '$.dividend_yield') END) AS dividend_yield,
    MAX(CASE WHEN factor_name='fundamentals' THEN json_extract(raw_values, '$.roe') END) AS roe,
    MAX(CASE WHEN factor_name='fundamentals' THEN json_extract(raw_values, '$.revenue_growth_yoy') END) AS revenue_growth_yoy,
    MAX(CASE WHEN factor_name='fundamentals' THEN json_extract(raw_values, '$.operating_margin') END) AS operating_margin,
    MAX(CASE WHEN factor_name='technical' THEN json_extract(raw_values, '$.rsi_14') END) AS rsi_14,
    MAX(CASE WHEN factor_name='technical' THEN json_extract(raw_values, '$.ma200_distance') END) AS ma200_distance,
    MAX(CASE WHEN factor_name='technical' THEN json_extract(raw_values, '$.volume_zscore') END) AS volume_zscore
  FROM factor_scores fs
  JOIN latest_dates ld
    ON fs.ticker_id = ld.ticker_id AND fs.date = ld.latest_date
  GROUP BY fs.ticker_id
)
SELECT
  t.id AS ticker_id,
  t.code, t.name, t.market, t.sector,
  lc.price_at_score AS price,
  lc.total_score AS composite_score,
  lc.verdict,
  lf.valuation_score, lf.fundamentals_score, lf.technical_score,
  lf.macro_score, lf.sentiment_score,
  lf.per, lf.pbr, lf.peg, lf.dividend_yield,
  lf.roe, lf.revenue_growth_yoy, lf.operating_margin,
  lf.rsi_14, lf.ma200_distance, lf.volume_zscore,
  lc.date AS as_of_date,
  (SELECT GROUP_CONCAT(universe_code) FROM universe_members um
    WHERE um.ticker_id = t.id AND um.as_of_date = lc.date) AS universes
FROM tickers t
JOIN latest_composite lc ON t.id = lc.ticker_id
LEFT JOIN latest_factors lf ON t.id = lf.ticker_id
WHERE t.delisted_at IS NULL OR t.delisted_at > lc.date;
```

### `v_score_history`

```sql
CREATE VIEW v_score_history AS
SELECT
  t.id AS ticker_id, t.code, t.name, t.market,
  cs.date, cs.total_score AS composite_score, cs.verdict,
  MAX(CASE WHEN fs.factor_name='valuation' THEN fs.score END) AS valuation_score,
  MAX(CASE WHEN fs.factor_name='fundamentals' THEN fs.score END) AS fundamentals_score,
  MAX(CASE WHEN fs.factor_name='technical' THEN fs.score END) AS technical_score,
  MAX(CASE WHEN fs.factor_name='macro' THEN fs.score END) AS macro_score,
  MAX(CASE WHEN fs.factor_name='sentiment' THEN fs.score END) AS sentiment_score
FROM tickers t
JOIN composite_scores cs ON t.id = cs.ticker_id
LEFT JOIN factor_scores fs ON t.id = fs.ticker_id AND cs.date = fs.date
GROUP BY t.id, cs.date;
```

### `v_universe`

```sql
CREATE VIEW v_universe AS
SELECT
  um.universe_code, um.ticker_id, t.code, t.name, t.market,
  um.as_of_date, um.weight
FROM universe_members um
JOIN tickers t ON um.ticker_id = t.id;
```

### `v_at_date(:date)` — 동적 처리

SQLite는 인자 받는 뷰가 없으므로 Python 측에서 CTE로 동적 생성:

```python
def query_at_date(sql_template: str, target_date: str) -> str:
    """v_at_date(:date)를 실제 CTE로 치환."""
    cte = f"""
    WITH v_at_date AS (
      SELECT ... (v_latest_scores와 동일하되 latest_dates를 :date 기준으로)
    )
    """
    return cte + sql_template.replace("v_at_date(:date)", "v_at_date")
```

---

## 통합 마이그레이션 SQL (Phase 4 + 7)

```sql
-- 002_hybrid_sentiment.sql

ALTER TABLE news_summaries ADD COLUMN source TEXT
  NOT NULL DEFAULT 'api'
  CHECK(source IN ('api','manual_prompt','fallback'));

ALTER TABLE news_summaries ADD COLUMN batch_id TEXT;

ALTER TABLE composite_scores ADD COLUMN sentiment_source TEXT
  CHECK(sentiment_source IN ('api','manual_prompt','fallback','cache')) DEFAULT 'api';

CREATE TABLE IF NOT EXISTS daily_token_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  model TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('api','manual_prompt')),
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  call_count INTEGER NOT NULL DEFAULT 0,
  estimated_cost_usd REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(date, model, mode)
);

INSERT INTO schema_version (version) VALUES (2);
```

```sql
-- 003_screener.sql

ALTER TABLE tickers ADD COLUMN delisted_at TEXT;

CREATE TABLE IF NOT EXISTS universe_members (
  universe_code TEXT NOT NULL,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  as_of_date TEXT NOT NULL,
  weight REAL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (universe_code, ticker_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_universe_code_date ON universe_members(universe_code, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_universe_ticker ON universe_members(ticker_id);

CREATE TABLE IF NOT EXISTS watchlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  group_name TEXT NOT NULL DEFAULT 'core',
  added_at TEXT NOT NULL DEFAULT (datetime('now')),
  added_by TEXT NOT NULL DEFAULT 'manual',
  notes TEXT,
  UNIQUE(ticker_id, group_name)
);

CREATE TABLE IF NOT EXISTS screener_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at TEXT NOT NULL DEFAULT (datetime('now')),
  preset_name TEXT,
  sql_text TEXT NOT NULL,
  result_count INTEGER NOT NULL,
  result_tickers TEXT NOT NULL,
  elapsed_ms INTEGER NOT NULL,
  is_backtest INTEGER NOT NULL DEFAULT 0,
  backtest_period TEXT
);

-- 뷰는 별도 파일 또는 views.py에서 생성

INSERT INTO schema_version (version) VALUES (3);
```
