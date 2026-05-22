"""SQLite 마이그레이션 SQL 상수.

docs/DB_SCHEMA.md의 정의를 그대로 옮겨옴. Phase 4 (news.source/batch_id) 와 Phase 7
(universe_members, watchlists, screener_runs, views)는 향후 마이그레이션으로 추가.
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
