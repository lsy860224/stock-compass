"""Backtest History 페이지 — 저장된 backtest 실행 목록·상세·비교."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from stock_compass.dashboard._data import fetch_backtest_history


def render_backtest_history() -> None:
    st.header("Backtest History — 저장된 실행 목록")

    rows = fetch_backtest_history(limit=200)
    if not rows:
        st.info(
            "저장된 backtest 없음. `stock-compass backtest --save ...` 실행 "
            "(--save 기본 ON) 후 다시 방문."
        )
        return

    presets_all = sorted({r.preset_name or "(inline)" for r in rows})
    selected_presets = st.multiselect(
        "preset 필터", ["(전체)", *presets_all], default=["(전체)"]
    )
    if "(전체)" in selected_presets or not selected_presets:
        filtered = rows
    else:
        filtered = [
            r for r in rows if (r.preset_name or "(inline)") in selected_presets
        ]

    # KPI
    cols = st.columns(4)
    cols[0].metric("총 실행", len(filtered))
    cols[1].metric("총 picks", sum(r.total_picks for r in filtered))
    hits_3m = [
        h
        for r in filtered
        if (h := r.stats.get("3m", {}).get("hit_rate")) is not None
    ]
    if hits_3m:
        cols[2].metric(
            "평균 3m 적중률",
            f"{sum(hits_3m) / len(hits_3m) * 100:.1f}%",
        )
    avgs_3m = [
        a
        for r in filtered
        if (a := r.stats.get("3m", {}).get("avg")) is not None
    ]
    if avgs_3m:
        cols[3].metric(
            "평균 3m 수익률",
            f"{sum(avgs_3m) / len(avgs_3m) * 100:+.1f}%",
        )

    # 목록 테이블
    summary_rows = []
    for r in filtered:
        summary_rows.append(
            {
                "id": r.id,
                "run_at": r.run_at,
                "preset": r.preset_name or "(inline)",
                "기간": f"{r.start_date} ~ {r.end_date}",
                "rebalance": r.rebalance,
                "라운드": r.rounds_count,
                "picks": r.total_picks,
                "1m 적중률 (%)": _stat_pct(r.stats, "1m", "hit_rate"),
                "3m 적중률 (%)": _stat_pct(r.stats, "3m", "hit_rate"),
                "1m 평균 (%)": _stat_pct(r.stats, "1m", "avg"),
                "3m 평균 (%)": _stat_pct(r.stats, "3m", "avg"),
            }
        )
    st.dataframe(
        pd.DataFrame(summary_rows), use_container_width=True, hide_index=True
    )

    # 상세 보기
    st.subheader("상세 보기")
    ids = ["(선택)", *[str(r.id) for r in filtered]]
    chosen_id_str = st.selectbox("실행 id", ids)
    if chosen_id_str != "(선택)":
        chosen = next(r for r in filtered if r.id == int(chosen_id_str))
        st.markdown(
            f"**#{chosen.id}** · `{chosen.preset_name or '(inline)'}` · "
            f"{chosen.start_date} ~ {chosen.end_date} · {chosen.rebalance} · "
            f"forward {','.join(chosen.forward_periods)}"
        )
        # 통계 표
        stats_table = []
        for p in chosen.forward_periods:
            s = chosen.stats.get(p, {})
            stats_table.append(
                {
                    "Forward": p,
                    "평균 (%)": _fmt_stat(s.get("avg")),
                    "중앙값 (%)": _fmt_stat(s.get("median")),
                    "적중률 (%)": _fmt_stat(s.get("hit_rate"), rate=True),
                    "최저 (%)": _fmt_stat(s.get("worst")),
                }
            )
        st.dataframe(
            pd.DataFrame(stats_table), use_container_width=True, hide_index=True
        )

        # 라운드별 line chart
        if chosen.rounds_summary:
            data: list[dict[str, object]] = []
            for round_info in chosen.rounds_summary:
                for p in chosen.forward_periods:
                    v = (round_info.get("avg_returns_by_period") or {}).get(p)
                    if v is None:
                        continue
                    data.append(
                        {
                            "as_of": round_info["as_of"],
                            "period": p,
                            "avg_return_pct": round(v * 100, 2),
                            "picks": round_info.get("picks", 0),
                        }
                    )
            if data:
                df_rounds = pd.DataFrame(data)
                chart = (
                    alt.Chart(df_rounds)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("as_of:T", title="rebalance"),
                        y=alt.Y(
                            "avg_return_pct:Q",
                            title="평균 forward return (%)",
                        ),
                        color="period:N",
                        tooltip=["as_of", "period", "avg_return_pct", "picks"],
                    )
                    .properties(height=300)
                )
                zero = (
                    alt.Chart(pd.DataFrame({"y": [0.0]}))
                    .mark_rule(strokeDash=[3, 3], color="#888")
                    .encode(y="y:Q")
                )
                st.altair_chart(chart + zero, use_container_width=True)

        with st.expander("SQL"):
            st.code(chosen.sql_text, language="sql")

    # 비교
    st.subheader("실행 비교 (2~4 개)")
    compare_ids = st.multiselect(
        "비교 대상", [str(r.id) for r in filtered], max_selections=4
    )
    if len(compare_ids) >= 2:
        compare_rows = [r for r in filtered if str(r.id) in compare_ids]
        comp_data: list[dict[str, object]] = []
        for r in compare_rows:
            for p in r.forward_periods:
                s = r.stats.get(p, {})
                comp_data.append(
                    {
                        "label": f"#{r.id} {r.preset_name or 'inline'}",
                        "period": p,
                        "avg_pct": (s.get("avg") or 0) * 100,
                        "hit_pct": (s.get("hit_rate") or 0) * 100,
                    }
                )
        if comp_data:
            df_comp = pd.DataFrame(comp_data)
            chart = (
                alt.Chart(df_comp)
                .mark_bar()
                .encode(
                    x=alt.X("period:N"),
                    y=alt.Y("avg_pct:Q", title="평균 forward return (%)"),
                    color="label:N",
                    xOffset="label:N",
                    tooltip=["label", "period", "avg_pct", "hit_pct"],
                )
                .properties(height=320)
            )
            st.altair_chart(chart, use_container_width=True)


def _stat_pct(stats, period, key):
    s = stats.get(period, {})
    v = s.get(key)
    if v is None:
        return None
    return round(v * 100, 2)


def _fmt_stat(v, rate=False):
    if v is None:
        return None
    return round(v * 100, 2 if not rate else 1)
