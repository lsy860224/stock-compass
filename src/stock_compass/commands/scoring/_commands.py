"""score / batch / history / batch-and-alert — 점수화·이력·스케줄 typer 명령."""

from __future__ import annotations

from typing import Annotated

import typer

from stock_compass.commands._app import app, console
from stock_compass.commands._helpers import (
    parse_market,
    resolve_default_targets,
    resolve_targets,
    resolve_universe_targets,
    run_mixed,
)
from stock_compass.commands.scoring.reporting import previous_scores_for
from stock_compass.commands.scoring.scheduler import resolve_task, run_scheduled_task
from stock_compass.markets.base import Market


@app.command()
def score(
    ticker: Annotated[str, typer.Argument(help="종목 코드 (예: AAPL, 005930)")],
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us (자동 감지가 기본)")
    ] = None,
) -> None:
    """단일 종목의 5팩터 점수 + 종합 점수 출력."""
    from stock_compass.config import settings
    from stock_compass.db import (
        get_db_connection,
        get_sector_score_rank,
        get_ticker_id,
    )
    from stock_compass.output.terminal import render_single_score
    from stock_compass.scoring import ScoringEngine
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    market_norm = parse_market(market)

    with console.status(f"[cyan]{ticker}[/cyan] 점수 계산 중…", spinner="dots"):
        try:
            result = ScoringEngine().analyze(ticker, market=market_norm)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1) from e

    sector_rank: tuple[int, int] | None = None
    if result.sector:
        with get_db_connection() as conn:
            tid = get_ticker_id(conn, result.ticker, result.market)
            if tid is not None:
                sector_rank = get_sector_score_rank(
                    conn, result.market, result.sector, tid
                )

    render_single_score(result, console=console, sector_rank=sector_rank)


@app.command()
def batch(
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us — 미지정 시 전체")
    ] = None,
    tickers: Annotated[
        str | None,
        typer.Option("--tickers", "-t", help="콤마 구분 직접 지정 (.env 워치리스트 무시)"),
    ] = None,
    universe: Annotated[
        str | None,
        typer.Option(
            "--universe",
            "-u",
            help="유니버스 코드 콤마 구분 채점 (예: ALL_KR,SP500). 워치리스트/--tickers 와 병합",
        ),
    ] = None,
    no_persist: Annotated[
        bool, typer.Option("--no-persist", help="DB 저장 생략 (드라이런)")
    ] = False,
) -> None:
    """워치리스트(또는 --universe) 배치 → SQLite 저장 + 점수 순위 출력."""
    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from stock_compass.config import settings
    from stock_compass.output.terminal import render_score_ranking
    from stock_compass.scoring import ScoringEngine
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    forced_market = parse_market(market)
    if universe:
        # --universe 지정 시 유니버스 멤버를 대상으로 (--tickers/워치리스트는 무시)
        targets = resolve_universe_targets(universe)
        if forced_market is not None:
            targets = [(t, m) for t, m in targets if m == forced_market]
    elif tickers:
        targets = resolve_targets(
            tickers, forced_market, settings.watchlist_kr, settings.watchlist_us
        )
    else:
        # 기본: .env 워치리스트 + DB 추적 종목 (discover 포함)
        targets = resolve_default_targets(forced_market)

    if not targets:
        console.print(
            "[yellow]대상 종목이 없습니다. "
            "WATCHLIST_KR/US · --tickers · --universe · track add 확인.[/yellow]"
        )
        raise typer.Exit(code=2)

    console.print(
        f"[cyan]{len(targets)}개 종목 배치 시작[/cyan] "
        f"(KR={sum(1 for t in targets if t[1] == 'KR')}, "
        f"US={sum(1 for t in targets if t[1] == 'US')})"
    )

    engine = ScoringEngine()
    market_for: dict[str, Market] = dict(targets)
    just_tickers = [t for t, _ in targets]

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        results = run_mixed(engine, just_tickers, market_for, progress, persist=not no_persist)

    previous = previous_scores_for(results)
    render_score_ranking(results, console=console, previous_scores=previous)


@app.command()
def history(
    ticker: Annotated[str, typer.Argument(help="종목 코드")],
    days: Annotated[int, typer.Option("--days", "-d", help="조회 일수")] = 30,
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us (자동 감지가 기본)")
    ] = None,
) -> None:
    """단일 종목 점수 추이 (DB 기록 기반)."""
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, get_score_history
    from stock_compass.markets import detect_market
    from stock_compass.output.terminal import render_history
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    market_norm = parse_market(market) or detect_market(ticker)
    with get_db_connection() as conn:
        rows = get_score_history(conn, ticker, market_norm, days=days)
    render_history(ticker, market_norm, rows, console=console)


@app.command("batch-and-alert")
def batch_and_alert(
    task: Annotated[
        str,
        typer.Option(
            "--task",
            help="auto/us/kr/daily/all — auto면 KST 시각 기반 자동 선택",
        ),
    ] = "auto",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="DB·발화 없이 계획만 표시")
    ] = False,
) -> None:
    """launchd가 호출하는 통합 명령 — KST 시각 기반 batch + alert 자동 분기.

    종료 코드: 0=성공, 1=일부 종목 실패, 2=치명적 오류.
    """
    import sys

    from stock_compass.config import settings
    from stock_compass.utils.dates import now_kst
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    resolved = resolve_task(task, now_kst().hour)
    console.print(
        f"[cyan]batch-and-alert[/cyan] task=[yellow]{resolved}[/yellow]"
        f" (요청={task}, KST={now_kst().strftime('%Y-%m-%d %H:%M')})"
    )

    try:
        exit_code = run_scheduled_task(resolved, dry_run=dry_run)
    except KeyboardInterrupt:
        console.print("[red]사용자 중단[/red]")
        raise typer.Exit(code=130) from None
    except Exception as e:
        console.print(f"[red]치명적 오류: {type(e).__name__}: {e}[/red]")
        sys.exit(2)

    sys.exit(exit_code)
