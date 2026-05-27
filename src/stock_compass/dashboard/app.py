"""Streamlit 대시보드 entrypoint.

`uv run stock-compass dashboard` (subprocess streamlit run) 또는 직접
`streamlit run src/stock_compass/dashboard/app.py` 로 실행.

CLAUDE.md 1) 면책 자동 (모든 페이지 footer + 헤더 안내).
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from stock_compass.dashboard._data import (
    db_metadata,
    fetch_hindsight,
    fetch_history,
    fetch_overview,
    fetch_sector_averages,
    fetch_trades_recent,
    list_tickers_with_history,
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
        ("Overview", "History", "Trades"),
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


# ──────────────────────── pages ────────────────────────


def _render_overview() -> None:
    st.header("Overview — 워치리스트 점수 순위")
    rows = fetch_overview()
    if not rows:
        st.info("점수 데이터 없음 — `uv run stock-compass batch` 먼저 실행.")
        return

    df = pd.DataFrame(
        [
            {
                "code": r.code,
                "name": r.name or "—",
                "market": r.market,
                "sector": r.sector or "—",
                "score": round(r.total_score, 1),
                "Δ": round(r.delta, 1) if r.delta is not None else None,
                "verdict": r.verdict,
                "sector_rank": (
                    f"{r.sector_rank[0]}/{r.sector_rank[1]}"
                    if r.sector_rank
                    else "—"
                ),
                "price": r.price,
            }
            for r in rows
        ]
    ).sort_values("score", ascending=False)

    # KPI 카드
    cols = st.columns(4)
    n_interest = int((df["verdict"] == "관심권").sum())
    n_caution = int((df["verdict"] == "주의").sum())
    avg_score = float(df["score"].mean())
    cols[0].metric("종목 수", len(df))
    cols[1].metric("관심권 (≥70)", n_interest)
    cols[2].metric("주의 (<50)", n_caution)
    cols[3].metric("평균 점수", f"{avg_score:.1f}")

    st.subheader("점수 순위")
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "score": st.column_config.ProgressColumn(
                "score", min_value=0, max_value=100, format="%.1f"
            ),
            "Δ": st.column_config.NumberColumn(format="%+.1f"),
        },
    )

    # 변화 highlight (Δ ≥ 5 또는 verdict 변화)
    highlights = df[df["Δ"].abs() >= 5] if df["Δ"].notna().any() else df.iloc[:0]
    if not highlights.empty:
        st.subheader("변화 highlight (Δ ≥ 5)")
        st.dataframe(
            highlights[["code", "name", "score", "Δ", "verdict"]],
            use_container_width=True,
            hide_index=True,
        )

    # Sector 평균 차트
    st.subheader("Sector별 평균 점수")
    sector_rows = fetch_sector_averages()
    if sector_rows:
        sector_df = pd.DataFrame(sector_rows)
        chart = (
            alt.Chart(sector_df)
            .mark_bar()
            .encode(
                x=alt.X("avg_score:Q", title="평균 점수"),
                y=alt.Y("sector:N", sort="-x", title="섹터"),
                color=alt.Color("market:N"),
                tooltip=["sector", "market", "n", "avg_score"],
            )
            .properties(height=max(200, len(sector_df) * 22))
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.caption("sector 데이터 부족.")


def _render_history() -> None:
    st.header("History — 종목 점수 추이")
    items = list_tickers_with_history()
    if not items:
        st.info("점수 이력 없음 — batch 먼저 실행.")
        return

    label_to_pair = {
        f"{code} [{market}] · {name or '—'}": (code, market)
        for code, market, name in items
    }
    label = st.selectbox("종목", list(label_to_pair))
    days = st.slider("기간 (일)", min_value=14, max_value=365, value=90, step=7)

    code, market = label_to_pair[label]
    rows = fetch_history(code, market, days=days)
    if not rows:
        st.info("선택 종목의 점수 이력이 없습니다.")
        return

    df = pd.DataFrame(
        [
            {
                "date": r.date,
                "composite": r.total_score,
                "verdict": r.verdict,
                **r.factor_scores,
            }
            for r in rows
        ]
    )

    composite_chart = (
        alt.Chart(df)
        .mark_line(point=True)
        .encode(
            x=alt.X("date:T", title="일자"),
            y=alt.Y("composite:Q", title="종합", scale=alt.Scale(domain=[0, 100])),
            tooltip=["date", "composite", "verdict"],
        )
        .properties(title="종합 점수 추이", height=280)
    )
    threshold_lines = (
        alt.Chart(pd.DataFrame({"y": [70.0, 50.0]}))
        .mark_rule(strokeDash=[3, 3], color="#888")
        .encode(y="y:Q")
    )
    st.altair_chart(composite_chart + threshold_lines, use_container_width=True)

    factor_cols = [
        c
        for c in ("valuation", "fundamentals", "technical", "macro", "sentiment")
        if c in df.columns
    ]
    if factor_cols:
        factors_df = df.melt(
            id_vars="date",
            value_vars=factor_cols,
            var_name="factor",
            value_name="score",
        )
        fchart = (
            alt.Chart(factors_df)
            .mark_line(point=False)
            .encode(
                x=alt.X("date:T", title="일자"),
                y=alt.Y(
                    "score:Q", title="팩터", scale=alt.Scale(domain=[0, 100])
                ),
                color=alt.Color("factor:N", title="팩터"),
                tooltip=["date", "factor", "score"],
            )
            .properties(title="5팩터 분해", height=300)
        )
        st.altair_chart(fchart, use_container_width=True)


def _render_trades() -> None:
    st.header("Trades — 매매 일지 + hindsight")
    days = st.slider("lookback (일)", 30, 1500, 365, step=30)
    forward = st.multiselect(
        "forward 검증 일수",
        options=[30, 60, 90, 180, 365],
        default=[30, 90],
    )

    trades = fetch_trades_recent(days=days)
    if not trades:
        st.info(f"최근 {days}일 매매 없음.")
        return

    trade_df = pd.DataFrame(
        [
            {
                "일시": t.executed_at.split("T")[0],
                "종목": f"{t.ticker} [{t.market}]",
                "side": t.side.upper(),
                "price": t.price,
                "qty": t.qty,
                "score_at_trade": t.score_at_trade,
                "tag": t.tag or "—",
            }
            for t in trades
        ]
    )
    st.subheader(f"매매 일지 ({len(trades)}건)")
    st.dataframe(trade_df, use_container_width=True, hide_index=True)

    if not forward:
        return

    rows, summary = fetch_hindsight(
        days=days, forward_days=tuple(sorted(forward))
    )
    st.subheader("Hindsight forward return (composite_scores 기반)")

    # 방향별 통계
    side_data: list[dict[str, object]] = []
    for side in ("buy", "sell"):
        for k in summary["forward_keys"]:
            stat = summary["by_side"][side][k]
            if stat["count"] == 0:
                continue
            side_data.append(
                {
                    "side": side.upper(),
                    "period": k,
                    "count": stat["count"],
                    "avg_pct": round((stat["avg"] or 0) * 100, 2),
                    "hit_rate_pct": round((stat["hit_rate"] or 0) * 100, 1),
                }
            )
    if side_data:
        st.markdown("**방향별** (매수는 + 좋음, 매도는 - 좋음)")
        st.dataframe(
            pd.DataFrame(side_data), use_container_width=True, hide_index=True
        )

    # zone별 매수 통계 차트
    zone_data: list[dict[str, object]] = []
    zone_labels = {
        "high_70_plus": "관심권(≥70)",
        "mid_50_70": "중립(50~69)",
        "low_below_50": "주의(<50)",
        "no_score": "점수 없음",
    }
    for zone, label in zone_labels.items():
        for k in summary["forward_keys"]:
            stat = summary["by_zone"][zone][k]
            if stat["count"] == 0:
                continue
            zone_data.append(
                {
                    "zone": label,
                    "period": k,
                    "avg_pct": round((stat["avg"] or 0) * 100, 2),
                    "count": stat["count"],
                }
            )
    if zone_data:
        st.markdown("**진입 점수 zone별 매수 forward return**")
        zone_df = pd.DataFrame(zone_data)
        chart = (
            alt.Chart(zone_df)
            .mark_bar()
            .encode(
                x=alt.X("zone:N", title="진입 점수 zone"),
                y=alt.Y("avg_pct:Q", title="평균 forward return (%)"),
                color=alt.Color(
                    "period:N", scale=alt.Scale(scheme="tableau10")
                ),
                xOffset="period:N",
                tooltip=["zone", "period", "avg_pct", "count"],
            )
            .properties(height=320)
        )
        st.altair_chart(chart, use_container_width=True)

    # 개별 매매 forward return 표
    if rows:
        detail_data = []
        for r in rows:
            entry = {
                "일시": r.trade.executed_at.split("T")[0],
                "종목": f"{r.trade.ticker} [{r.trade.market}]",
                "side": r.trade.side.upper(),
                "score": r.trade.score_at_trade,
                "price": r.trade.price,
            }
            for k, v in r.forward_returns.items():
                entry[k] = round(v * 100, 1) if v is not None else None
            detail_data.append(entry)
        st.markdown("**개별 매매 forward return (%)**")
        st.dataframe(
            pd.DataFrame(detail_data), use_container_width=True, hide_index=True
        )


# ──────────────────────── dispatcher ────────────────────────


if page == "Overview":
    _render_overview()
elif page == "History":
    _render_history()
elif page == "Trades":
    _render_trades()

st.markdown("---")
st.caption(f"📋 {DISCLAIMER}")
