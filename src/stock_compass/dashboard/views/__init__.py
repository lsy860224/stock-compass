"""대시보드 페이지 렌더러 모음 — app.py 디스패처가 호출.

⚠️ 이 패키지를 `pages`로 이름 짓지 말 것: Streamlit이 entrypoint 옆 `pages/`
디렉터리를 멀티페이지 앱으로 자동 인식해 커스텀 사이드바 네비게이션이 깨진다.
"""

from __future__ import annotations

from stock_compass.dashboard.views.backtest import render_backtest
from stock_compass.dashboard.views.backtest_history import render_backtest_history
from stock_compass.dashboard.views.history import render_history
from stock_compass.dashboard.views.overview import render_overview
from stock_compass.dashboard.views.reports import render_reports
from stock_compass.dashboard.views.tracked import render_tracked
from stock_compass.dashboard.views.trades import render_trades

__all__ = [
    "render_backtest",
    "render_backtest_history",
    "render_history",
    "render_overview",
    "render_reports",
    "render_tracked",
    "render_trades",
]
