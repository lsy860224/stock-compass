"""Streamlit 대시보드 entrypoint.

`uv run stock-compass dashboard` (subprocess streamlit run) 또는 직접
`streamlit run src/stock_compass/dashboard/app.py` 로 실행.

페이지 렌더러는 `dashboard.views.*` 모듈에 있고, 이 파일은 page_config·사이드바·
디스패처·면책 footer만 담당한다. (Streamlit은 이 스크립트를 top-to-bottom 실행.)

CLAUDE.md 1) 면책 자동 (모든 페이지 footer + 헤더 안내).
"""

from __future__ import annotations

import streamlit as st

from stock_compass.dashboard._data import db_metadata
from stock_compass.dashboard.views import (
    render_backtest,
    render_backtest_history,
    render_history,
    render_overview,
    render_reports,
    render_tracked,
    render_trades,
)
from stock_compass.scoring.engine import DISCLAIMER

st.set_page_config(
    page_title="stock-compass",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ──────────────────────── sidebar ────────────────────────


with st.sidebar:
    st.title("🧭 stock-compass")
    page = st.radio(
        "페이지",
        (
            "Overview",
            "Tracked",
            "History",
            "Reports",
            "Trades",
            "Backtest",
            "Backtest History",
        ),
        label_visibility="collapsed",
    )
    meta = db_metadata()
    st.caption(
        f"점수 {meta['score_count']:,} / 매매 {meta['trade_count']:,}"
        + (
            f"\n\n최신: {meta['latest_score_date']}"
            if meta["latest_score_date"]
            else "\n\n점수 없음"
        )
    )
    st.markdown("---")
    st.caption("⚠️ **매수 권유 아님.** 본인 판단의 보조 자료.")


# ──────────────────────── dispatcher ────────────────────────


_PAGES = {
    "Overview": render_overview,
    "Tracked": render_tracked,
    "History": render_history,
    "Reports": render_reports,
    "Trades": render_trades,
    "Backtest": render_backtest,
    "Backtest History": render_backtest_history,
}

_render = _PAGES.get(page)
if _render is not None:
    _render()

st.markdown("---")
st.caption(f"📋 {DISCLAIMER}")
