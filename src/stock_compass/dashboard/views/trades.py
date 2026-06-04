"""Trades 페이지 — 매매 일지 + hindsight forward return."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from stock_compass.dashboard._data import fetch_hindsight, fetch_trades_recent


def render_trades() -> None:
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
