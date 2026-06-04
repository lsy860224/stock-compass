"""screen — SQL 스크리너 (RO 모드 + LIMIT 강제 + 예산 필터)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from stock_compass.commands._app import app, console
from stock_compass.commands._helpers import parse_market
from stock_compass.commands.screener._cheatsheet import (
    print_field_cheatsheet,
    print_preset_catalog,
)
from stock_compass.commands.screener._common import screener_rows_to_targets


@app.command()
def screen(
    preset: Annotated[
        str | None, typer.Option("--preset", help="screeners/presets/<name>.sql")
    ] = None,
    file: Annotated[
        Path | None, typer.Option("--file", "-f", help="저장된 .sql 파일")
    ] = None,
    sql: Annotated[
        str | None, typer.Option("--sql", help="인라인 SQL")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", "-l", help="결과 행 수 (기본 50, 최대 5000)")
    ] = None,
    budget: Annotated[
        float | None,
        typer.Option(
            "--budget",
            help="1주 가격 ≤ 예산 (시장 통화 — KRW 또는 USD). preset에 price 컬럼 필요",
        ),
    ] = None,
    budget_market: Annotated[
        str | None,
        typer.Option(
            "--budget-market",
            help="budget을 적용할 시장 (kr/us). 미지정 시 모든 시장",
        ),
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", help="table / csv / json")
    ] = "table",
    out: Annotated[
        Path | None, typer.Option("--out", help="csv/json은 파일로 저장 (미지정 시 stdout)")
    ] = None,
    list_presets_flag: Annotated[
        bool, typer.Option("--list-presets", help="사용 가능한 프리셋 목록")
    ] = False,
    list_fields_flag: Annotated[
        bool, typer.Option("--list-fields", help="v_latest_scores 칼럼 치트시트")
    ] = False,
    add_to_watchlist: Annotated[
        bool,
        typer.Option(
            "--add-to-watchlist",
            help="결과 종목을 WATCHLIST_<GROUP> universe에 등록 (--group 으로 그룹 지정)",
        ),
    ] = False,
    watchlist_group: Annotated[
        str,
        typer.Option(
            "--group",
            help="--add-to-watchlist 그룹 이름 (영숫자+언더바, 기본 'screening')",
        ),
    ] = "screening",
    prompt_deepdive: Annotated[
        bool,
        typer.Option(
            "--prompt-deepdive",
            help="결과 종목 sentiment 심층 분석 prompt MD 생성 (Claude.ai 복붙용)",
        ),
    ] = False,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive",
            help="인터랙티브 SQL REPL (Phase 7-8 미구현 — 친절 안내만)",
        ),
    ] = False,
) -> None:
    """SQL 스크리너 — RO 모드 + LIMIT 강제 + 안전 검증 + 예산 필터."""
    from stock_compass.config import settings
    from stock_compass.output.screener import (
        export_to_file,
        render_csv,
        render_json,
        render_table,
    )
    from stock_compass.screener import (
        PresetNotFoundError,
        ScreenerEngine,
        ScreenerError,
        list_presets,
        load_preset,
    )
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    if interactive:
        console.print(
            "[yellow]--interactive REPL은 Phase 7-8 (선택) 미구현입니다.[/yellow]\n"
            "[dim]대안:[/dim]\n"
            "  · [cyan]stock-compass screen --preset <name>[/cyan] — 프리셋 실행\n"
            "  · [cyan]stock-compass screen --file path.sql[/cyan] — 저장된 SQL\n"
            "  · [cyan]stock-compass screen --sql \"...\"[/cyan] — 인라인 SQL\n"
            "  · [cyan]stock-compass screen --list-fields[/cyan] — 칼럼 치트시트"
        )
        raise typer.Exit(code=2)
    if list_presets_flag:
        print_preset_catalog(list_presets())
        return
    if list_fields_flag:
        print_field_cheatsheet()
        return

    # 진입점 우선순위: preset > file > sql
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
        preset_name = None
    elif sql:
        sql_text = sql
        preset_name = None
    else:
        console.print(
            "[red]--preset / --file / --sql 중 하나는 필수입니다.[/red]"
            "\n[dim]힌트: `stock-compass screen --list-presets`[/dim]"
        )
        raise typer.Exit(code=2)

    bm_norm = parse_market(budget_market)
    try:
        engine = ScreenerEngine()
        result = engine.run_sql(
            sql_text,
            limit=limit,
            preset_name=preset_name,
            budget=budget,
            budget_market=bm_norm,
        )
    except ScreenerError as e:
        console.print(f"[red]스크리너 거부: {e}[/red]")
        raise typer.Exit(code=1) from e

    fmt = output_format.lower()
    if fmt == "table":
        render_table(result, console=console)
    elif fmt == "csv":
        body = render_csv(result)
        if out:
            export_to_file(body, out)
            console.print(f"[green]✓[/green] CSV 저장: {out}")
        else:
            console.print(body)
    elif fmt == "json":
        body = render_json(result)
        if out:
            export_to_file(body, out)
            console.print(f"[green]✓[/green] JSON 저장: {out}")
        else:
            console.print(body)
    else:
        console.print(f"[red]지원하지 않는 format: {fmt} (table/csv/json만)[/red]")
        raise typer.Exit(code=2)

    if add_to_watchlist:
        _add_screener_to_watchlist(result.rows, group=watchlist_group)
    if prompt_deepdive:
        _generate_deepdive_prompt(result.rows, preset_name=preset_name)


def _add_screener_to_watchlist(
    rows: list[dict[str, Any]], *, group: str
) -> None:
    """`screen --add-to-watchlist` — 결과 종목을 추적 그룹(watchlists)에 등록.

    추적 등록 즉시 일일 배치(.env + 추적)가 자동 채점 → 별도 .env 수정 불필요.
    """
    from stock_compass.db import get_db_connection, track_ticker

    targets = screener_rows_to_targets(rows, default_market=None)
    if not targets:
        console.print("[yellow]code/market 컬럼 없어 추적 추가 생략.[/yellow]")
        return

    added = 0
    with get_db_connection() as conn:
        for code, market in targets:
            if track_ticker(
                conn,
                code=code,
                market=market,
                group=group,
                added_by="screen",
                notes="screen --add-to-watchlist",
            ):
                added += 1

    console.print(
        f"[green]✓[/green] '[cyan]{group}[/cyan]' 추적 그룹 — "
        f"{added}/{len(targets)}종목 신규 등록 (다음 배치부터 자동 채점)"
    )


def _generate_deepdive_prompt(
    rows: list[dict[str, Any]], *, preset_name: str | None
) -> None:
    """`screen --prompt-deepdive` — 결과 종목 sentiment prompt MD 생성."""
    from stock_compass.output.prompt_generator import PromptGenerator

    targets = screener_rows_to_targets(rows, default_market=None)
    if not targets:
        console.print(
            "[yellow]code/market 컬럼 없어 deepdive prompt 생략.[/yellow]"
        )
        return

    tag = f"deepdive-{preset_name}" if preset_name else "deepdive"
    result = PromptGenerator().generate_sentiment_prompt(targets, tag=tag)
    console.print(
        f"[green]✓[/green] deepdive prompt: [cyan]{result.path}[/cyan]"
        f"  ({result.ticker_count}종목, 파일 {result.file_count}개)"
    )
    console.print(
        "[dim]Claude.ai에 복붙 → 응답 받아 "
        f"[cyan]stock-compass sentiment import --batch-id {result.batch_id}[/cyan][/dim]"
    )
