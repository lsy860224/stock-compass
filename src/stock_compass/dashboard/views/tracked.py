"""Tracked 페이지 — .env 외 + discover 영속 추적 종목."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stock_compass.dashboard._data import fetch_tracked


def render_tracked() -> None:
    st.header("Tracked — 추적 종목 (.env 외 + discover)")
    st.caption(
        "DB로 영속 추적되는 종목. 일일 배치가 .env 워치리스트와 함께 자동 채점합니다. "
        "추가/해제: `track add/remove`, discover 발굴분은 'discover' 그룹 자동 등록."
    )
    rows = fetch_tracked()
    if not rows:
        st.info(
            "추적 종목이 없습니다. `stock-compass track add <코드>` 또는 "
            "weekly-discover 발굴(--track)로 등록됩니다."
        )
        return

    df = pd.DataFrame(rows)
    groups = sorted(df["group_name"].unique())
    chosen = st.multiselect("그룹", groups, default=groups)
    view = df[df["group_name"].isin(chosen)] if chosen else df

    c1, c2, c3 = st.columns(3)
    c1.metric("추적 종목", len(view))
    scored = view["total_score"].notna().sum()
    c2.metric("채점됨", int(scored))
    c3.metric("관심권(≥70)", int((view["total_score"] >= 70).sum()))

    show = view.rename(
        columns={
            "code": "종목",
            "name": "이름",
            "market": "시장",
            "group_name": "그룹",
            "total_score": "점수",
            "verdict": "등급",
            "added_by": "출처",
            "score_date": "채점일",
            "notes": "메모",
        }
    )[
        ["종목", "이름", "시장", "그룹", "점수", "등급", "출처", "채점일", "메모"]
    ]
    st.dataframe(show, use_container_width=True, hide_index=True)
