"""Backtest 페이지 — preset x 시점별 forward return (인터랙티브 폼)."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from stock_compass.dashboard._data import (
    adapt_preset_for_backtest,
    list_backtestable_presets,
)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_backtest(
    sql_text,
    start_iso,
    end_iso,
    rebalance,
    forward_tuple,
    limit,
    preset_name,
):
    """5분 TTL caching — 동일 입력 재실행 방지."""
    from datetime import date as date_cls

    from stock_compass.screener.backtest import run_backtest

    return run_backtest(
        sql_text,
        start=date_cls.fromisoformat(start_iso),
        end=date_cls.fromisoformat(end_iso),
        rebalance=rebalance,
        forward_periods=forward_tuple,
        limit=limit,
        preset_name=preset_name,
    )


def _round_pct(forward_returns_for_ticker, period):
    if forward_returns_for_ticker is None:
        return None
    v = forward_returns_for_ticker.get(period)
    if v is None:
        return None
    return round(v * 100, 2)


def render_backtest() -> None:
    from datetime import date as date_cls

    from stock_compass.screener.backtest import BacktestError
    from stock_compass.screener.engine import ScreenerError

    st.header("Backtest — preset x 시점별 forward return")

    presets = list_backtestable_presets()
    preset_names = ["(인라인 SQL 직접 입력)", *(name for name, _ in presets)]
    preset_sql_map = dict(presets)

    with st.form("backtest_form"):
        col1, col2 = st.columns([3, 2])
        with col1:
            chosen = st.selectbox(
                "preset (또는 인라인)", preset_names, index=0
            )
            if chosen == "(인라인 SQL 직접 입력)":
                default_sql = (
                    "SELECT code, market, price, composite_score\n"
                    "FROM v_at_date(:as_of)\n"
                    "WHERE composite_score >= 70\n"
                )
                sql_text = st.text_area(
                    "SQL (`:as_of` 또는 `v_at_date(:as_of)` 필수)",
                    value=default_sql,
                    height=180,
                )
                preset_for_run: str | None = None
            else:
                raw = preset_sql_map.get(chosen, "")
                sql_text = adapt_preset_for_backtest(raw)
                st.code(sql_text, language="sql")
                if sql_text == raw and ":as_of" not in raw:
                    st.warning(
                        "이 preset은 `:as_of` placeholder 가 없습니다 — "
                        "수동으로 `v_at_date(:as_of)` 로 수정 필요."
                    )
                preset_for_run = chosen
        with col2:
            today_d = date_cls.today()
            start_d = st.date_input("start", value=date_cls(today_d.year - 1, 1, 1))
            end_d = st.date_input("end", value=today_d)
            rebalance = st.selectbox(
                "rebalance", ["monthly", "quarterly", "weekly"], index=0
            )
            limit = st.slider("limit / round", 5, 50, 15)
            forward = st.multiselect(
                "forward periods",
                options=["1m", "3m", "6m", "12m"],
                default=["1m", "3m"],
            )
        submitted = st.form_submit_button("백테스트 실행", type="primary")

    if not submitted:
        st.info("Preset/기간 선택 후 [백테스트 실행] 클릭")
        return

    if not sql_text.strip():
        st.error("SQL 비어 있음.")
        return
    if ":as_of" not in sql_text:
        st.error(
            "SQL 에 `:as_of` placeholder 가 필요합니다 — "
            "`FROM v_at_date(:as_of)` 형태 사용."
        )
        return
    if not forward:
        st.error("forward periods 최소 1개 선택.")
        return

    try:
        with st.spinner("백테스트 실행 중 — preset/SQL 복잡도에 따라 5~30초"):
            result = _cached_backtest(
                sql_text,
                start_d.isoformat(),
                end_d.isoformat(),
                rebalance,
                tuple(forward),
                limit,
                preset_for_run,
            )
    except (BacktestError, ScreenerError) as e:
        st.error(f"실행 실패: {e}")
        return

    if not result.rounds:
        st.warning("라운드 0개 — 데이터 또는 기간 부족. backfill 또는 batch 누적 후 재시도.")
        return

    # KPI 카드
    n_cards = len(result.forward_periods)
    cols = st.columns(n_cards)
    for i, p in enumerate(result.forward_periods):
        avg = result.stats.avg_return.get(p)
        hit = result.stats.hit_rate.get(p)
        cols[i].metric(
            f"평균 {p}",
            f"{avg * 100:+.1f}%" if avg is not None else "—",
            f"적중률 {hit * 100:.1f}%" if hit is not None else None,
        )
    st.caption(
        f"총 선정 {result.stats.total_picks}건 x {len(result.forward_periods)} 기간"
        + (f" / preset={result.preset_name}" if result.preset_name else "")
    )

    # 라운드별 line chart
    rounds_data: list[dict[str, object]] = []
    for r in result.rounds:
        for p in result.forward_periods:
            vals = [
                v
                for tr in r.forward_returns.values()
                if (v := tr.get(p)) is not None
            ]
            if vals:
                rounds_data.append(
                    {
                        "as_of": r.as_of.isoformat(),
                        "period": p,
                        "avg_return_pct": round(sum(vals) / len(vals) * 100, 2),
                        "picks": len(r.selected),
                    }
                )
    if rounds_data:
        rounds_df = pd.DataFrame(rounds_data)
        chart = (
            alt.Chart(rounds_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("as_of:T", title="rebalance 일자"),
                y=alt.Y(
                    "avg_return_pct:Q", title="평균 forward return (%)"
                ),
                color=alt.Color("period:N", title="forward 기간"),
                tooltip=[
                    "as_of",
                    "period",
                    alt.Tooltip("avg_return_pct:Q", format=".2f"),
                    "picks",
                ],
            )
            .properties(height=320)
        )
        # 0% baseline
        rule = (
            alt.Chart(pd.DataFrame({"y": [0.0]}))
            .mark_rule(strokeDash=[3, 3], color="#888")
            .encode(y="y:Q")
        )
        st.altair_chart(chart + rule, use_container_width=True)
    else:
        st.info("라운드는 있지만 forward return 데이터 부족 (미래 시점 데이터 미수신).")

    # 통계 상세 테이블
    stats_rows = []
    for p in result.forward_periods:
        avg = result.stats.avg_return.get(p)
        med = result.stats.median_return.get(p)
        hit = result.stats.hit_rate.get(p)
        worst = result.stats.worst_return.get(p)
        stats_rows.append(
            {
                "기간": p,
                "평균 (%)": round((avg or 0) * 100, 2) if avg is not None else None,
                "중앙값 (%)": round((med or 0) * 100, 2) if med is not None else None,
                "적중률 (%)": round((hit or 0) * 100, 1) if hit is not None else None,
                "최저 (%)": round((worst or 0) * 100, 2) if worst is not None else None,
            }
        )
    st.subheader("전체 통계")
    st.dataframe(
        pd.DataFrame(stats_rows), use_container_width=True, hide_index=True
    )

    # 라운드 expander
    st.subheader(f"라운드별 종목 ({len(result.rounds)} 라운드)")
    for r in result.rounds:
        with st.expander(
            f"{r.as_of.isoformat()} — {len(r.selected)}종목"
        ):
            if not r.selected:
                st.caption("선정 종목 없음 (조건 미충족 또는 데이터 부족)")
                continue
            sel_df = pd.DataFrame(r.selected)
            for p in result.forward_periods:
                sel_df[f"forward_{p}_pct"] = [
                    _round_pct(
                        r.forward_returns.get(
                            str(row.get("code", row.get("ticker", "")))
                        ),
                        p,
                    )
                    for _, row in sel_df.iterrows()
                ]
            st.dataframe(sel_df, use_container_width=True, hide_index=True)

    # 한계
    st.caption(
        "백테스트 결과는 과거 데이터 기반 통계. **매수 권유 아님.** "
        "거래비용·세금·슬리피지·배당 미반영. survivorship: 상장폐지 종목 제외 "
        "→ 실제 시점 선택 가능했던 종목 누락 가능. 과거 성과가 미래를 보장하지 않음."
    )
