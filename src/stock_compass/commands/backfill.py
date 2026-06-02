"""backfill 명령 — 과거 데이터로 composite_scores 시드 (backtest 입력 확보).

Technical+Macro 만 시점별 실제 데이터, Valuation/Fundamentals/Sentiment 는
backfill_skip (neutral 50). 결과는 backtest 가 즉시 활용 가능.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer

from stock_compass.commands._app import app, console
from stock_compass.commands._helpers import parse_market, resolve_universe_targets
from stock_compass.markets.base import Market


@app.command()
def backfill(
    start: Annotated[
        str, typer.Option("--start", help="백필 시작일 YYYY-MM-DD")
    ],
    end: Annotated[
        str,
        typer.Option(
            "--end", help="종료일 YYYY-MM-DD (미지정 시 오늘)"
        ),
    ] = "",
    market: Annotated[
        str | None,
        typer.Option("--market", "-m", help="kr / us (자동 감지가 기본)"),
    ] = None,
    tickers: Annotated[
        str | None,
        typer.Option(
            "--tickers", "-t", help="콤마 구분 종목 코드 (예: AAPL,MSFT,005930)"
        ),
    ] = None,
    universe: Annotated[
        str | None,
        typer.Option(
            "--universe",
            "-u",
            help="유니버스 코드 콤마 구분 (예: ALL_KR,SP500). --tickers 대신 사용",
        ),
    ] = None,
) -> None:
    """과거 OHLCV + FRED 시계열로 시점별 점수 백필 — backtest 입력 확보."""
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table

    from stock_compass.config import settings
    from stock_compass.markets import detect_market
    from stock_compass.scoring.backfill import (
        BackfillResult,
        run_backfill,
    )
    from stock_compass.utils.dates import today_kst
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    if not tickers and not universe:
        console.print("[red]--tickers 또는 --universe 중 하나 필요[/red]")
        raise typer.Exit(code=2)

    try:
        start_d = date.fromisoformat(start)
        end_d = date.fromisoformat(end) if end else today_kst()
    except ValueError as e:
        console.print(f"[red]날짜 형식 오류: {e}[/red]")
        raise typer.Exit(code=2) from e

    forced = parse_market(market)
    if universe:
        targets = resolve_universe_targets(universe)
        if forced is not None:
            targets = [(t, m) for t, m in targets if m == forced]
    else:
        codes = [c.strip() for c in (tickers or "").split(",") if c.strip()]
        targets = [(c, forced or detect_market(c)) for c in codes]

    if not targets:
        console.print("[red]대상 종목 없음 — --tickers/--universe 확인[/red]")
        raise typer.Exit(code=2)

    console.print(
        f"[cyan]backfill[/cyan] {len(targets)}종목 · {start_d} ~ {end_d}"
        f" (Technical+Macro 실제 시점 / Valuation+Fundamentals+Sentiment baseline 50)"
    )

    results: list[BackfillResult] = []
    errors: list[tuple[str, Market, str]] = []

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]백필", total=len(targets))
        for code, m in targets:
            progress.update(task, description=f"[cyan]백필[/cyan] {code} [{m}]")
            try:
                r = run_backfill(code, m, start=start_d, end=end_d)
                results.append(r)
            except Exception as e:
                errors.append((code, m, f"{type(e).__name__}: {e}"))
            progress.advance(task)

    if results:
        table = Table(title=f"백필 결과 ({len(results)}종목)")
        table.add_column("ticker", style="cyan")
        table.add_column("market", justify="center")
        table.add_column("처리 일수", justify="right")
        table.add_column("이력부족 skip", justify="right", style="dim")
        table.add_column("범위 외 skip", justify="right", style="dim")
        for r in results:
            table.add_row(
                r.ticker,
                r.market,
                str(r.days_processed),
                str(r.days_skipped_insufficient_history),
                str(r.days_skipped_out_of_range),
            )
        console.print(table)

    if errors:
        err_tbl = Table(title=f"실패 ({len(errors)}건)")
        err_tbl.add_column("ticker", style="red")
        err_tbl.add_column("market", justify="center")
        err_tbl.add_column("error", overflow="fold")
        for code, m, msg in errors:
            err_tbl.add_row(code, m, msg)
        console.print(err_tbl)

    console.print(
        Panel(
            "백필 결과는 **Technical+Macro 시점별 실제 데이터** 기반. "
            "Valuation/Fundamentals/Sentiment 는 시점별 재구성 불가 → 50 baseline "
            "(score_at_trade·factor_scores.source='backfill_skip' 표시). "
            "backtest 사용 시 종합 점수 변동은 Technical+Macro 신호 위주임을 인지.",
            title="면책 / 한계",
            border_style="dim",
            padding=(0, 1),
        )
    )
