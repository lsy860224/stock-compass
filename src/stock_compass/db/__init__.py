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
    get_sector_score_rank,
    get_sector_valuation_medians,
    upsert_composite_score,
)
from stock_compass.db.tickers import (
    get_ticker_id,
    upsert_ticker,
)
from stock_compass.db.trades import (
    Trade,
    get_performance_summary,
    get_trades,
    insert_trade,
)

__all__ = [
    "MIGRATIONS",
    "AlertRow",
    "HistoryRow",
    "NewsSummaryRow",
    "Trade",
    "get_cached_news_summary",
    "get_db_connection",
    "get_last_score",
    "get_latest_scores",
    "get_performance_summary",
    "get_previous_composite_score",
    "get_previous_total_scores",
    "get_recent_alerts",
    "get_recent_news_summaries",
    "get_score_history",
    "get_scores_on_date",
    "get_sector_score_rank",
    "get_sector_valuation_medians",
    "get_ticker_id",
    "get_today_token_usage",
    "get_trades",
    "has_daily_alert_today",
    "has_recent_alert",
    "insert_trade",
    "migrate",
    "record_alert",
    "record_token_usage",
    "upsert_composite_score",
    "upsert_news_summary",
    "upsert_ticker",
]
