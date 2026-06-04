"""weekly-discover — 주간 종목 발굴 (3개 preset → 통합 Craft 노트)."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from stock_compass.commands._app import app, console
from stock_compass.commands.screener._common import screener_rows_to_targets


@app.command("weekly-discover")
def weekly_discover(
    publish_craft: Annotated[
        bool, typer.Option("--publish-craft", help="결과를 Craft에 통합 노트로 발행")
    ] = True,
    presets: Annotated[
        str,
        typer.Option(
            "--presets",
            help=(
                "실행할 preset 쉼표 구분 "
                "(기본: value_growth_kr,momentum_us,oversold_quality_global)"
            ),
        ),
    ] = "value_growth_kr,momentum_us,oversold_quality_global",
    refresh_universe: Annotated[
        bool,
        typer.Option(
            "--refresh-universe/--no-refresh-universe",
            help="실행 전 KOSPI_200 + KOSDAQ_150 + SP500 universe 갱신",
        ),
    ] = True,
    limit_per_preset: Annotated[
        int, typer.Option("--limit", help="preset당 결과 행 수 (기본 15)")
    ] = 15,
    track: Annotated[
        bool,
        typer.Option(
            "--track/--no-track",
            help="발굴 상위 종목을 'discover' 추적 그룹에 자동 등록 (다음 배치부터 채점)",
        ),
    ] = True,
) -> None:
    """주간 종목 발굴 — 3개 preset 실행 + 통합 Craft 노트.

    토요일 아침 launchd가 호출하는 일괄 발굴 흐름. 각 preset 결과는
    한 Craft 노트의 별도 섹션으로 묶임. --track 이면 상위 종목을 'discover'
    추적 그룹에 등록 → 일일 배치가 자동 채점·히스토리 축적.
    """
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection
    from stock_compass.screener import (
        PresetNotFoundError,
        ScreenerEngine,
        ScreenerError,
        list_presets,
        load_preset,
    )
    from stock_compass.screener.universes import (
        UniverseFetchError,
        refresh,
    )
    from stock_compass.utils.dates import today_kst
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    requested_names = [p.strip() for p in presets.split(",") if p.strip()]
    available = {p.name for p in list_presets()}
    unknown = [n for n in requested_names if n not in available]
    if unknown:
        console.print(f"[red]존재하지 않는 preset: {unknown}[/red]")
        raise typer.Exit(code=2)

    if refresh_universe:
        with get_db_connection() as conn:
            for code in ("KOSPI_200", "KOSDAQ_150", "SP500"):
                try:
                    r = refresh(conn, code)
                    console.print(
                        f"[dim]universe {r.universe_code}: +{r.members} 신규[/dim]"
                    )
                except UniverseFetchError as e:
                    console.print(f"[yellow]universe {code} 갱신 실패: {e}[/yellow]")

    engine = ScreenerEngine()
    sections: list[str] = [
        f"# 주간 발굴 · {today_kst().isoformat()} (토요일 KST)",
        "",
        "> 매주 자동 발굴된 후보 종목 — **매수 권유 아님.** 본인 추가 조사 필수.",
    ]
    total_found = 0
    tracked_total = 0
    for name in requested_names:
        try:
            sql = load_preset(name)
            result = engine.run_sql(
                sql,
                limit=limit_per_preset,
                preset_name=f"weekly:{name}",
            )
        except (PresetNotFoundError, ScreenerError) as e:
            console.print(f"[red]preset {name} 실패: {e}[/red]")
            sections.append(f"\n## ❌ {name}\n\n실행 실패: {e}")
            continue

        total_found += result.row_count
        if track and result.rows:
            tracked_total += _track_discover_rows(
                name, result.rows, settings.discover_track_top_n
            )
        sections.append(f"\n## 🔍 {name} — {result.row_count}건")

        # 모델 신뢰도 검증 — 지난 1년 backtest 적중률 + 평균 수익률 한 줄
        bt_line = _preset_backtest_line(sql, name)
        if bt_line:
            sections.append(bt_line)

        if not result.rows:
            sections.append("\n조건 충족 종목 없음.")
            continue
        cols = result.columns[:6]
        sections.append("\n| " + " | ".join(cols) + " |")
        sections.append("|" + "|".join(["---"] * len(cols)) + "|")
        for row in result.rows[:15]:
            cells = [_format_md_cell(row.get(c)) for c in cols]
            sections.append("| " + " | ".join(cells) + " |")
        sections.append(
            f"\n_(전체 {result.row_count}건, 표시 상위 {min(15, result.row_count)}건)_"
        )

    sections.append(
        "\n---\n\n> 면책: 결과는 매수 권유 아님. 거래비용·세금·survivorship bias 미반영."
    )
    body = "\n".join(sections)

    console.print(
        f"\n[cyan]주간 발굴 완료[/cyan] — {len(requested_names)}개 preset, "
        f"총 {total_found}건"
        + (f" · 'discover' 추적 {tracked_total}종목 등록" if track else "")
    )

    if publish_craft:
        # dual-sink: Obsidian 볼트 + Craft API (Craft 미설정이어도 Obsidian은 발행)
        from stock_compass.output.report import publish_report

        on_date = today_kst()
        iso_year, iso_week, _ = on_date.isocalendar()
        report_result = publish_report(
            body,
            on_date=on_date,
            kind="weekly-discover",
            title=f"주간 발굴 · {on_date.isoformat()}",
            filename=f"{iso_year}-W{iso_week:02d} 발굴",
        )
        if report_result.obsidian_path is not None:
            console.print(
                f"[green]✓ Obsidian:[/green] "
                f"[cyan]{report_result.obsidian_path.name}[/cyan]"
            )
        if report_result.craft_url:
            console.print(
                f"[green]✓ Craft 발행:[/green] [cyan]{report_result.craft_url}[/cyan]"
            )
        elif report_result.craft_skipped:
            console.print(
                f"[dim]Craft 발행 skip ({report_result.craft_skipped})[/dim]"
            )


def _preset_backtest_line(preset_sql: str, preset_name: str) -> str | None:
    """preset SQL에 대해 지난 1년 monthly backtest → 3m 적중률·평균 수익률 한 줄.

    Craft 노트의 preset 섹션에 "_과거 검증_ 적중률 X%, 평균 +Y%" 추가.
    백테스트는 v_at_date(:as_of) 필요 → v_latest_scores를 자동 치환 시도.
    데이터 부족/실행 실패 시 None 반환 (섹션에 안 추가).
    """
    import re
    from datetime import timedelta

    from stock_compass.screener.backtest import BacktestError, run_backtest
    from stock_compass.screener.engine import ScreenerError
    from stock_compass.utils.dates import today_kst
    from stock_compass.utils.logging import get_logger

    logger = get_logger(__name__)

    # v_latest_scores → v_at_date(:as_of) 자동 변환 (이미 :as_of 또는 v_at_date 있으면 그대로)
    if ":as_of" in preset_sql or re.search(
        r"v_at_date\(", preset_sql, re.IGNORECASE
    ):
        bt_sql = preset_sql
    else:
        bt_sql = re.sub(
            r"\bv_latest_scores\b",
            "v_at_date(:as_of)",
            preset_sql,
            flags=re.IGNORECASE,
        )
        if bt_sql == preset_sql:
            return None  # 변환 대상 없음

    end_d = today_kst() - timedelta(days=7)  # 직전 주까지 (당주 데이터 부족)
    start_d = end_d - timedelta(days=365)

    try:
        result = run_backtest(
            bt_sql,
            start=start_d,
            end=end_d,
            rebalance="monthly",
            forward_periods=("3m",),
            limit=10,
            preset_name=f"weekly-verify:{preset_name}",
        )
    except (BacktestError, ScreenerError) as e:
        logger.debug("weekly backtest %s 실패 — 노트에 생략: %s", preset_name, e)
        return None

    avg = result.stats.avg_return.get("3m")
    hit = result.stats.hit_rate.get("3m")
    if avg is None or hit is None:
        return None
    avg_sign = "+" if avg >= 0 else ""
    return (
        f"\n_과거 1년 검증 (monthly, 3m forward, {result.stats.rounds_count} 라운드):_ "
        f"**적중률 {hit * 100:.0f}%**, 평균 {avg_sign}{avg * 100:.1f}% "
        f"_(총 {result.stats.total_picks}건 선정)_"
    )


def _format_md_cell(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}" if abs(v) >= 1 else f"{v:.4f}"
    s = str(v)
    return s.replace("|", "/").replace("\n", " ")


def _track_discover_rows(
    preset_name: str, rows: list[dict[str, Any]], top_n: int
) -> int:
    """weekly-discover 상위 top_n 종목을 'discover' 추적 그룹에 등록. 신규 수 반환."""
    from stock_compass.db import get_db_connection, track_ticker

    targets = screener_rows_to_targets(rows[:top_n], default_market=None)
    if not targets:
        return 0
    added = 0
    with get_db_connection() as conn:
        for code, market in targets:
            if track_ticker(
                conn,
                code=code,
                market=market,
                group="discover",
                added_by="discover",
                notes=f"preset:{preset_name}",
            ):
                added += 1
    return added
