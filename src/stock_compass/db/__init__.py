"""DB 패키지 — 도메인별 모듈 통합 진입점.

모듈 구조:
- _connection: SQLite 연결 컨텍스트
- tickers:     종목 마스터
- scores:      composite + factor 점수
- news:        LLM 요약 캐시 + 토큰 사용량
- alerts:      알림 발화 이력
- trades:      매매 일지 + 편향 분석
- migrations:  스키마 마이그레이션
"""

from stock_compass.db._connection import get_db_connection
from stock_compass.db.alerts import (
    AlertRow,
    get_recent_alerts,
    has_daily_alert_today,
    has_recent_alert,
    record_alert,
)
from stock_compass.db.backtest import (
    BacktestRow,
    get_backtest_result,
    list_backtest_results,
    save_backtest_result,
)
from stock_compass.db.meta import get_latest_ticker_meta, upsert_ticker_meta
from stock_compass.db.migrations import MIGRATIONS, migrate
from stock_compass.db.news import (
    NewsSummaryRow,
    get_cached_news_summary,
    get_recent_news_summaries,
    get_today_token_usage,
    record_token_usage,
    upsert_news_summary,
)
from stock_compass.db.scores import (
    HistoryRow,
    get_last_score,
    get_latest_scores,
    get_previous_composite_score,
    get_previous_total_scores,
    get_score_history,
    get_scores_on_date,
    upsert_composite_score,
)
from stock_compass.db.sectors import (
    get_sector_fundamental_medians,
    get_sector_score_rank,
    get_sector_valuation_medians,
)
from stock_compass.db.tickers import (
    get_ticker_id,
    upsert_ticker,
)
from stock_compass.db.trade_hindsight import (
    DEFAULT_HINDSIGHT_DAYS,
    TradeWithHindsight,
    get_trade_hindsight,
    summarize_hindsight,
)
from stock_compass.db.trades import (
    Trade,
    get_performance_summary,
    get_trades,
    insert_trade,
)
from stock_compass.db.watchlist import (
    add_tracked,
    count_tracked_by_group,
    get_tracked_targets,
    list_tracked,
    remove_tracked,
    track_ticker,
)

__all__ = [
    "DEFAULT_HINDSIGHT_DAYS",
    "MIGRATIONS",
    "AlertRow",
    "BacktestRow",
    "HistoryRow",
    "NewsSummaryRow",
    "Trade",
    "TradeWithHindsight",
    "add_tracked",
    "count_tracked_by_group",
    "get_backtest_result",
    "get_cached_news_summary",
    "get_db_connection",
    "get_last_score",
    "get_latest_scores",
    "get_latest_ticker_meta",
    "get_performance_summary",
    "get_previous_composite_score",
    "get_previous_total_scores",
    "get_recent_alerts",
    "get_recent_news_summaries",
    "get_score_history",
    "get_scores_on_date",
    "get_sector_fundamental_medians",
    "get_sector_score_rank",
    "get_sector_valuation_medians",
    "get_ticker_id",
    "get_today_token_usage",
    "get_tracked_targets",
    "get_trade_hindsight",
    "get_trades",
    "has_daily_alert_today",
    "has_recent_alert",
    "insert_trade",
    "list_backtest_results",
    "list_tracked",
    "migrate",
    "record_alert",
    "record_token_usage",
    "remove_tracked",
    "save_backtest_result",
    "summarize_hindsight",
    "track_ticker",
    "upsert_composite_score",
    "upsert_news_summary",
    "upsert_ticker",
    "upsert_ticker_meta",
]
