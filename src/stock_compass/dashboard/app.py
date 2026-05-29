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
    adapt_preset_for_backtest,
    db_metadata,
    fetch_backtest_history,
    fetch_hindsight,
    fetch_history,
    fetch_overview,
    fetch_sector_averages,
    fetch_trades_recent,
    list_backtestable_presets,
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
        ("Overview", "History", "Trades", "Backtest", "Backtest History"),
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


def _render_backtest() -> None:
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


def _render_backtest_history() -> None:
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


if page == "Overview":
    _render_overview()
elif page == "History":
    _render_history()
elif page == "Trades":
    _render_trades()
elif page == "Backtest":
    _render_backtest()
elif page == "Backtest History":
    _render_backtest_history()

st.markdown("---")
st.caption(f"📋 {DISCLAIMER}")
