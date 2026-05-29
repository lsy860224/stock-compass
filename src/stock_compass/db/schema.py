"""SQLite 마이그레이션 SQL 상수.

docs/DB_SCHEMA.md의 정의를 그대로 옮겨옴. Phase 7 (universe_members, watchlists,
screener_runs, views)는 향후 마이그레이션으로 추가.
"""

from __future__ import annotations

MIGRATION_001_INITIAL = """
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
  factor_name TEXT NOT NULL
    CHECK(factor_name IN ('valuation','fundamentals','technical','macro','sentiment')),
  score REAL NOT NULL CHECK(score BETWEEN 0 AND 100),
  weight REAL NOT NULL CHECK(weight BETWEEN 0 AND 1),
  raw_values TEXT,
  note TEXT,
  computed_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(ticker_id, date, factor_name)
);
CREATE INDEX IF NOT EXISTS idx_factor_scores_ticker_date
  ON factor_scores(ticker_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_factor_scores_factor
  ON factor_scores(factor_name, date DESC);

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
CREATE INDEX IF NOT EXISTS idx_composite_ticker_date
  ON composite_scores(ticker_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_composite_date_score
  ON composite_scores(date DESC, total_score DESC);

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
CREATE INDEX IF NOT EXISTS idx_news_ticker_pub
  ON news_summaries(ticker_id, published_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  trigger_type TEXT NOT NULL
    CHECK(trigger_type IN ('threshold_buy','threshold_caution','delta_up','delta_down','daily')),
  score_before REAL,
  score_after REAL NOT NULL,
  message TEXT NOT NULL,
  delivered_via TEXT NOT NULL,
  fired_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alerts_ticker_trigger_time
  ON alerts(ticker_id, trigger_type, fired_at DESC);

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
CREATE INDEX IF NOT EXISTS idx_trades_ticker_time
  ON trades(ticker_id, executed_at DESC);

CREATE TRIGGER IF NOT EXISTS tickers_updated_at
AFTER UPDATE ON tickers
BEGIN
  UPDATE tickers SET updated_at = datetime('now') WHERE id = NEW.id;
END;
"""


MIGRATION_002_HYBRID_SENTIMENT = """
ALTER TABLE news_summaries ADD COLUMN source TEXT
  NOT NULL DEFAULT 'api'
  CHECK(source IN ('api','manual_prompt','fallback'));
ALTER TABLE news_summaries ADD COLUMN batch_id TEXT;

ALTER TABLE composite_scores ADD COLUMN sentiment_source TEXT
  CHECK(sentiment_source IN ('api','manual_prompt','fallback','cache','placeholder'))
  DEFAULT 'placeholder';

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
CREATE INDEX IF NOT EXISTS idx_daily_token_usage_date ON daily_token_usage(date DESC);
CREATE INDEX IF NOT EXISTS idx_news_summaries_source ON news_summaries(ticker_id, source);
CREATE INDEX IF NOT EXISTS idx_news_summaries_batch ON news_summaries(batch_id);
"""


MIGRATION_003_SCREENER = """
-- 상장폐지 추적 (백테스트 survivorship bias 회피)
ALTER TABLE tickers ADD COLUMN delisted_at TEXT;

-- 유니버스 멤버십 (날짜별 이력 보존)
CREATE TABLE IF NOT EXISTS universe_members (
  universe_code TEXT NOT NULL,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  as_of_date TEXT NOT NULL,
  weight REAL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (universe_code, ticker_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_universe_code_date
  ON universe_members(universe_code, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_universe_ticker
  ON universe_members(ticker_id);

-- 그룹별 워치리스트 (.env 단일 리스트의 확장)
CREATE TABLE IF NOT EXISTS watchlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  group_name TEXT NOT NULL DEFAULT 'core',
  added_at TEXT NOT NULL DEFAULT (datetime('now')),
  added_by TEXT NOT NULL DEFAULT 'manual',
  notes TEXT,
  UNIQUE(ticker_id, group_name)
);

-- 스크리너 실행 감사 로그
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
CREATE INDEX IF NOT EXISTS idx_screener_runs_time
  ON screener_runs(run_at DESC);
"""


# Phase 7 뷰 — 마이그레이션 003 이후 별도 실행. 뷰는 idempotent하게 DROP+CREATE.
SCREENER_VIEWS_DDL = """
DROP VIEW IF EXISTS v_latest_scores;
CREATE VIEW v_latest_scores AS
WITH latest AS (
  SELECT ticker_id, MAX(date) AS d
  FROM composite_scores
  GROUP BY ticker_id
),
lc AS (
  SELECT cs.ticker_id, cs.date, cs.total_score, cs.verdict,
         cs.price_at_score, cs.sentiment_source
  FROM composite_scores cs
  JOIN latest l ON cs.ticker_id = l.ticker_id AND cs.date = l.d
),
lf AS (
  SELECT
    fs.ticker_id,
    MAX(CASE WHEN factor_name='valuation' THEN score END) AS valuation_score,
    MAX(CASE WHEN factor_name='fundamentals' THEN score END) AS fundamentals_score,
    MAX(CASE WHEN factor_name='quality' THEN score END) AS quality_score,
    MAX(CASE WHEN factor_name='technical' THEN score END) AS technical_score,
    MAX(CASE WHEN factor_name='macro' THEN score END) AS macro_score,
    MAX(CASE WHEN factor_name='sentiment' THEN score END) AS sentiment_score,
    MAX(CASE WHEN factor_name='quality'
             THEN json_extract(raw_values, '$.debt_to_equity') END) AS debt_to_equity,
    MAX(CASE WHEN factor_name='quality'
             THEN json_extract(raw_values, '$.current_ratio') END) AS current_ratio,
    MAX(CASE WHEN factor_name='quality'
             THEN json_extract(raw_values, '$.roa') END) AS roa,
    MAX(CASE WHEN factor_name='valuation'
             THEN json_extract(raw_values, '$.per') END) AS per,
    MAX(CASE WHEN factor_name='valuation'
             THEN json_extract(raw_values, '$.pbr') END) AS pbr,
    MAX(CASE WHEN factor_name='valuation'
             THEN json_extract(raw_values, '$.peg') END) AS peg,
    MAX(CASE WHEN factor_name='valuation'
             THEN json_extract(raw_values, '$.dividend_yield') END) AS dividend_yield,
    MAX(CASE WHEN factor_name='fundamentals'
             THEN json_extract(raw_values, '$.roe') END) AS roe,
    MAX(CASE WHEN factor_name='fundamentals'
             THEN json_extract(raw_values, '$.revenue_growth_yoy') END) AS revenue_growth_yoy,
    MAX(CASE WHEN factor_name='fundamentals'
             THEN json_extract(raw_values, '$.operating_margin') END) AS operating_margin,
    MAX(CASE WHEN factor_name='fundamentals'
             THEN json_extract(raw_values, '$.market_cap') END) AS market_cap,
    MAX(CASE WHEN factor_name='technical'
             THEN json_extract(raw_values, '$.rsi_14') END) AS rsi_14,
    MAX(CASE WHEN factor_name='technical'
             THEN json_extract(raw_values, '$.ma200_distance') END) AS ma200_distance,
    MAX(CASE WHEN factor_name='technical'
             THEN json_extract(raw_values, '$.volume_zscore') END) AS volume_zscore
  FROM factor_scores fs
  JOIN latest l ON fs.ticker_id = l.ticker_id AND fs.date = l.d
  GROUP BY fs.ticker_id
),
tm AS (
  SELECT m.ticker_id, m.market_cap_krw, m.size_bucket
  FROM ticker_meta m
  JOIN (
    SELECT ticker_id, MAX(as_of_date) AS d
    FROM ticker_meta GROUP BY ticker_id
  ) mx ON m.ticker_id = mx.ticker_id AND m.as_of_date = mx.d
)
SELECT
  t.id AS ticker_id, t.code, t.name, t.market, t.sector,
  lc.price_at_score AS price,
  lc.total_score AS composite_score,
  lc.verdict, lc.sentiment_source,
  lf.valuation_score, lf.fundamentals_score, lf.quality_score, lf.technical_score,
  lf.macro_score, lf.sentiment_score,
  lf.per, lf.pbr, lf.peg, lf.dividend_yield,
  lf.roe, lf.revenue_growth_yoy, lf.operating_margin, lf.market_cap,
  lf.debt_to_equity, lf.current_ratio, lf.roa,
  tm.market_cap_krw, tm.size_bucket,
  lf.rsi_14, lf.ma200_distance, lf.volume_zscore,
  lc.date AS as_of_date,
  COALESCE(
    (SELECT GROUP_CONCAT(universe_code) FROM universe_members um
      WHERE um.ticker_id = t.id),
    ''
  ) AS universes
FROM tickers t
JOIN lc ON lc.ticker_id = t.id
LEFT JOIN lf ON lf.ticker_id = t.id
LEFT JOIN tm ON tm.ticker_id = t.id
WHERE t.delisted_at IS NULL OR t.delisted_at > lc.date;


DROP VIEW IF EXISTS v_score_history;
CREATE VIEW v_score_history AS
SELECT
  t.id AS ticker_id, t.code, t.name, t.market,
  cs.date, cs.total_score AS composite_score, cs.verdict,
  MAX(CASE WHEN fs.factor_name='valuation' THEN fs.score END) AS valuation_score,
  MAX(CASE WHEN fs.factor_name='fundamentals' THEN fs.score END) AS fundamentals_score,
  MAX(CASE WHEN fs.factor_name='quality' THEN fs.score END) AS quality_score,
  MAX(CASE WHEN fs.factor_name='technical' THEN fs.score END) AS technical_score,
  MAX(CASE WHEN fs.factor_name='macro' THEN fs.score END) AS macro_score,
  MAX(CASE WHEN fs.factor_name='sentiment' THEN fs.score END) AS sentiment_score
FROM tickers t
JOIN composite_scores cs ON t.id = cs.ticker_id
LEFT JOIN factor_scores fs ON t.id = fs.ticker_id AND cs.date = fs.date
GROUP BY t.id, cs.date;


DROP VIEW IF EXISTS v_universe;
CREATE VIEW v_universe AS
SELECT
  um.universe_code, um.ticker_id, t.code, t.name, t.market,
  um.as_of_date, um.weight
FROM universe_members um
JOIN tickers t ON um.ticker_id = t.id;
"""


MIGRATION_004_CRAFT_PUBLICATIONS = """
CREATE TABLE IF NOT EXISTS craft_publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_kind TEXT NOT NULL,
  on_date TEXT NOT NULL,
  note_id TEXT NOT NULL,
  folder_id TEXT NOT NULL,
  url TEXT,
  published_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(note_kind, on_date)
);
CREATE INDEX IF NOT EXISTS idx_craft_publications_kind_date
  ON craft_publications(note_kind, on_date DESC);
"""


# concerns 칼럼 — Claude.ai prompt 응답이 명시한 주의사항을 보존 (이전엔 폐기됐음).
# JSON 배열로 저장. API 경로는 빈 배열.
MIGRATION_005_NEWS_CONCERNS = """
ALTER TABLE news_summaries ADD COLUMN concerns TEXT NOT NULL DEFAULT '[]';
"""


# 백테스트 결과 영구 저장 — dashboard history, 비교, audit 용도
MIGRATION_006_BACKTEST_RESULTS = """
CREATE TABLE IF NOT EXISTS backtest_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at TEXT NOT NULL DEFAULT (datetime('now')),
  preset_name TEXT,
  sql_text TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  rebalance TEXT NOT NULL,
  forward_periods TEXT NOT NULL,  -- JSON 배열 (예: ["1m","3m"])
  limit_per_round INTEGER NOT NULL,
  rounds_count INTEGER NOT NULL,
  total_picks INTEGER NOT NULL,
  stats TEXT NOT NULL,  -- JSON {period: {avg, median, hit_rate, worst}}
  rounds_summary TEXT  -- JSON [{as_of, picks, avg_returns_by_period}]
);
CREATE INDEX IF NOT EXISTS idx_backtest_results_run_at
  ON backtest_results(run_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_results_preset
  ON backtest_results(preset_name, run_at DESC);
"""


# Size·시가총액 메타데이터 시계열 (Phase A a3). 팩터 점수와 분리된 metadata —
# 가중치에 영향 없음. backfill 은 closexshares_outstanding 으로 과거 시가총액 복원,
# batch 는 yfinance 현재값. market_cap_krw 는 cross-market 비교용 KRW 환산값.
MIGRATION_007_TICKER_META = """
CREATE TABLE IF NOT EXISTS ticker_meta (
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  as_of_date TEXT NOT NULL,
  market_cap REAL,
  market_cap_krw REAL,
  size_bucket TEXT
    CHECK(size_bucket IN ('mega','large','mid','small','micro')),
  shares_outstanding REAL,
  source TEXT NOT NULL DEFAULT 'batch'
    CHECK(source IN ('batch','backfill')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (ticker_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_ticker_meta_date_cap
  ON ticker_meta(as_of_date DESC, market_cap_krw DESC);
CREATE INDEX IF NOT EXISTS idx_ticker_meta_bucket
  ON ticker_meta(size_bucket, as_of_date DESC);
"""


# Quality 팩터(Phase A a2) 추가 — factor_scores.factor_name CHECK 에 'quality' 허용.
# SQLite 는 CHECK 제약 ALTER 불가 → 테이블 재생성. factor_scores 를 참조하는
# inbound FK 가 없어 foreign_keys 토글 없이 안전 (outbound FK 는 데이터 그대로 보존).
MIGRATION_008_QUALITY_FACTOR = """
-- factor_scores 를 참조하는 뷰를 먼저 제거 (table swap 중 dangling 참조 방지).
-- 아래 SCREENER_VIEWS_DDL 이 재생성.
DROP VIEW IF EXISTS v_latest_scores;
DROP VIEW IF EXISTS v_score_history;

CREATE TABLE factor_scores_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  factor_name TEXT NOT NULL
    CHECK(factor_name IN
      ('valuation','fundamentals','quality','technical','macro','sentiment')),
  score REAL NOT NULL CHECK(score BETWEEN 0 AND 100),
  weight REAL NOT NULL CHECK(weight BETWEEN 0 AND 1),
  raw_values TEXT,
  note TEXT,
  computed_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(ticker_id, date, factor_name)
);
INSERT INTO factor_scores_new
  (id, ticker_id, date, factor_name, score, weight, raw_values, note, computed_at)
  SELECT id, ticker_id, date, factor_name, score, weight, raw_values, note, computed_at
  FROM factor_scores;
DROP TABLE factor_scores;
ALTER TABLE factor_scores_new RENAME TO factor_scores;
CREATE INDEX IF NOT EXISTS idx_factor_scores_ticker_date
  ON factor_scores(ticker_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_factor_scores_factor
  ON factor_scores(factor_name, date DESC);
"""


