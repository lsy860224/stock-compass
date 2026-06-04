"""History 페이지 — 종목 점수 추이 + 5팩터 분해."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from stock_compass.dashboard._data import fetch_history, list_tickers_with_history


def render_history() -> None:
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
        for c in (
            "valuation",
            "fundamentals",
            "quality",
            "technical",
            "macro",
            "sentiment",
        )
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
