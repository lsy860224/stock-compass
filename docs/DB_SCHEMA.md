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
  ├── 1:N → news_summaries     (Claude 요약)
  ├── 1:N → alerts             (발화 이력)
  └── 1:N → trades             (본인 매매 일지)

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
| published_at | TEXT | NOT NULL | 원문 발행 ISO 8601 |
| summary | TEXT | NOT NULL | Claude 요약 (3줄, 자체 표현) |
| tone_score | REAL | NOT NULL CHECK(tone_score BETWEEN -10 AND 10) | 부정~긍정 |
| keywords | TEXT | NULL | JSON array |
| tokens_used | INTEGER | NULL | 비용 추적 |
| model | TEXT | NOT NULL | 'claude-sonnet-4-6' 등 |
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
