"""backtest — preset SQL x 시점별 forward return 통계 (Phase 7-5)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from stock_compass.commands._app import app, console

if TYPE_CHECKING:
    from stock_compass.screener.backtest import BacktestResult


@app.command()
def backtest(
    preset: Annotated[
        str | None,
        typer.Option("--preset", help="screeners/presets/<name>.sql (`:as_of` placeholder 필요)"),
    ] = None,
    file: Annotated[
        Path | None, typer.Option("--file", "-f", help="저장된 .sql 파일")
    ] = None,
    sql: Annotated[
        str | None, typer.Option("--sql", help="인라인 SQL (`:as_of` placeholder 필요)")
    ] = None,
    start: Annotated[
        str, typer.Option("--start", help="백테스트 시작일 YYYY-MM-DD")
    ] = "",
    end: Annotated[
        str, typer.Option("--end", help="백테스트 종료일 YYYY-MM-DD (미지정 시 오늘)")
    ] = "",
    rebalance: Annotated[
        str, typer.Option("--rebalance", help="monthly / quarterly / weekly")
    ] = "monthly",
    forward: Annotated[
        str,
        typer.Option(
            "--forward",
            help="forward periods 쉼표 구분 (기본 1m,3m — 지원: 1m/3m/6m/12m)",
        ),
    ] = "1m,3m",
    limit: Annotated[
        int, typer.Option("--limit", help="라운드별 종목 수 상한 (기본 15)")
    ] = 15,
    save: Annotated[
        bool,
        typer.Option(
            "--save/--no-save",
            help="DB(backtest_results)에 저장 (기본 ON — dashboard history)",
        ),
    ] = True,
    publish_craft: Annotated[
        bool,
        typer.Option(
            "--publish-craft",
            help="결과를 Craft 노트로 발행 (CRAFT_API_TOKEN 필요)",
        ),
    ] = False,
) -> None:
    """Phase 7-5 백테스트 — preset SQL x 시점별 forward return 통계.

    preset SQL은 `v_at_date(:as_of)` 형태를 사용해야 함. 매 라운드마다 `:as_of`가
    리밸런싱 일자로 치환됨. 결과는 라운드별 + 전체 통계 + 면책.
    """
    from stock_compass.config import settings
    from stock_compass.screener import (
        PresetNotFoundError,
        load_preset,
    )
    from stock_compass.screener.backtest import (
        BacktestError,
        run_backtest,
    )
    from stock_compass.utils.dates import today_kst
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    # 진입점: preset > file > sql
    preset_name: str | None = None
    if preset:
        try:
            sql_text = load_preset(preset)
            preset_name = preset
        except PresetNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=2) from e
    elif file:
        if not file.exists():
            console.print(f"[red]파일 없음: {file}[/red]")
            raise typer.Exit(code=2)
        sql_text = file.read_text(encoding="utf-8")
    elif sql:
        sql_text = sql
    else:
        console.print("[red]--preset / --file / --sql 중 하나 필수[/red]")
        raise typer.Exit(code=2)

    if not start:
        console.print("[red]--start (YYYY-MM-DD) 필수[/red]")
        raise typer.Exit(code=2)
    try:
        start_d = date.fromisoformat(start)
        end_d = date.fromisoformat(end) if end else today_kst()
    except ValueError as e:
        console.print(f"[red]날짜 형식 오류: {e}[/red]")
        raise typer.Exit(code=2) from e

    periods = tuple(p.strip() for p in forward.split(",") if p.strip())

    try:
        result = run_backtest(
            sql_text,
            start=start_d,
            end=end_d,
            rebalance=rebalance,
            forward_periods=periods,
            limit=limit,
            preset_name=preset_name,
        )
    except BacktestError as e:
        console.print(f"[red]백테스트 거부: {e}[/red]")
        raise typer.Exit(code=2) from e

    _render_backtest(result)

    if save:
        from stock_compass.db import get_db_connection, save_backtest_result

        with get_db_connection() as conn:
            row_id = save_backtest_result(conn, result, limit_per_round=limit)
        console.print(
            f"[dim]✓ backtest_results 저장 (id={row_id})[/dim]"
        )

    if publish_craft:
        _publish_backtest_to_craft(result)


def _publish_backtest_to_craft(result: BacktestResult) -> None:
    """Craft 노트로 발행 — Stock-compass 폴더 (settings.craft_daily_folder_id)."""
    from stock_compass.config import settings

    if settings.craft_api_token is None:
        console.print(
            "[yellow]--publish-craft 무시: CRAFT_API_TOKEN 미설정[/yellow]"
        )
        return

    from stock_compass.output.backtest_md import render_backtest_note
    from stock_compass.output.craft import (
        CraftAPIError,
        CraftAuthError,
        CraftPublisher,
    )
    from stock_compass.utils.dates import today_kst

    publisher = CraftPublisher()
    note_kind = f"backtest:{result.preset_name or 'inline'}:{result.start}~{result.end}"
    title = (
        f"백테스트 · {result.preset_name or 'inline'} · "
        f"{result.start} ~ {result.end}"
    )
    body = render_backtest_note(result)
    try:
        r = publisher.publish_daily_note(
            body,
            today_kst(),
            note_kind=note_kind,
            title=title,
        )
        action = "갱신" if r.is_update else "발행"
        console.print(
            f"[green]✓ Craft {action}:[/green] [cyan]{r.url}[/cyan]"
        )
    except CraftAuthError as e:
        console.print(f"[yellow]Craft 인증 실패: {e}[/yellow]")
    except CraftAPIError as e:
        console.print(f"[red]Craft API 오류: {e}[/red]")


def _render_backtest(result: BacktestResult) -> None:
    """라운드별 요약 + 전체 통계 + 면책."""
    from rich.panel import Panel
    from rich.table import Table

    console.print(
        f"[cyan]백테스트[/cyan] preset=[yellow]{result.preset_name or 'inline'}[/yellow]"
        f"  ·  기간 {result.start} ~ {result.end}"
        f"  ·  {result.rebalance} 리밸런싱  ·  forward {','.join(result.forward_periods)}"
    )

    if not result.rounds:
        console.print("[yellow]라운드 0개 — 기간 또는 데이터 부족[/yellow]")
        return

    # 라운드별 요약 테이블
    round_tbl = Table(
        title=f"라운드별 요약 ({len(result.rounds)}회)",
        show_lines=False,
    )
    round_tbl.add_column("as_of", style="cyan")
    round_tbl.add_column("종목", justify="right")
    for p in result.forward_periods:
        round_tbl.add_column(f"평균 {p}", justify="right")
    for r in result.rounds:
        row_avgs: list[str] = []
        for p in result.forward_periods:
            vals: list[float] = [
                v
                for tr in r.forward_returns.values()
                if (v := tr.get(p)) is not None
            ]
            if vals:
                avg_v = sum(vals) / len(vals)
                color = "green" if avg_v >= 0 else "red"
                sign = "+" if avg_v >= 0 else ""
                row_avgs.append(f"[{color}]{sign}{avg_v * 100:.1f}%[/{color}]")
            else:
                row_avgs.append("[dim]—[/dim]")
        round_tbl.add_row(r.as_of.isoformat(), str(len(r.selected)), *row_avgs)
    console.print(round_tbl)

    # 전체 통계
    stats_tbl = Table(title="전체 통계", show_lines=False)
    stats_tbl.add_column("Forward", style="cyan")
    stats_tbl.add_column("평균", justify="right")
    stats_tbl.add_column("중앙값", justify="right")
    stats_tbl.add_column("적중률 (양수)", justify="right")
    stats_tbl.add_column("최저", justify="right")
    for p in result.forward_periods:
        avg = result.stats.avg_return.get(p)
        med = result.stats.median_return.get(p)
        hit = result.stats.hit_rate.get(p)
        worst = result.stats.worst_return.get(p)
        stats_tbl.add_row(
            p,
            _fmt_pct(avg),
            _fmt_pct(med),
            f"{hit * 100:.1f}%" if hit is not None else "—",
            _fmt_pct(worst, force_color=True),
        )
    console.print(stats_tbl)
    console.print(
        f"[dim]총 선정 {result.stats.total_picks}건 x {len(result.forward_periods)} 기간[/dim]"
    )

    console.print(
        Panel(
            result.disclaimer,
            title="면책 / 한계",
            border_style="dim",
            padding=(0, 1),
        )
    )


def _fmt_pct(v: float | None, *, force_color: bool = False) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    text = f"{sign}{v * 100:.1f}%"
    if force_color:
        color = "green" if v >= 0 else "red"
        return f"[{color}]{text}[/{color}]"
    return text
