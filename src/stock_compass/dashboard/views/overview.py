"""Overview 페이지 — 워치리스트 점수 순위 + sector 평균."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from stock_compass.dashboard._data import fetch_overview, fetch_sector_averages


def render_overview() -> None:
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
