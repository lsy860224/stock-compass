"""score / batch / history / batch-and-alert — 점수화·이력·스케줄 통합."""

from __future__ import annotations

from typing import Annotated

import typer

from stock_compass.commands._app import app, console
from stock_compass.commands._helpers import (
    parse_market,
    resolve_targets,
    resolve_universe_targets,
    run_mixed,
)
from stock_compass.markets.base import Market
from stock_compass.output.craft_exporter import PreviousScores, SectorRanks


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
    else:
        targets = resolve_targets(
            tickers, forced_market, settings.watchlist_kr, settings.watchlist_us
        )

    if not targets:
        console.print(
            "[yellow]대상 종목이 없습니다. WATCHLIST_KR/US · --tickers · --universe 확인.[/yellow]"
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

    previous = _previous_scores_for(results)
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
        exit_code = _run_scheduled_task(resolved, dry_run=dry_run)
    except KeyboardInterrupt:
        console.print("[red]사용자 중단[/red]")
        raise typer.Exit(code=130) from None
    except Exception as e:
        console.print(f"[red]치명적 오류: {type(e).__name__}: {e}[/red]")
        sys.exit(2)

    sys.exit(exit_code)


def resolve_task(task: str, hour: int, *, weekday: int | None = None) -> str:
    """`auto` 입력을 KST 시각 + 요일 기반 task로 변환.

    Args:
        weekday: Python weekday (Mon=0, Sun=6). 미지정 시 현재 KST.

    Returns: us/kr/daily/all/weekly
    """
    t = task.lower()
    if t in ("us", "kr", "daily", "all", "weekly", "weekly-rescore"):
        return t
    if t != "auto":
        raise typer.BadParameter(
            f"--task는 auto/us/kr/daily/all/weekly/weekly-rescore 중 하나: {task!r}"
        )
    if weekday is None:
        from stock_compass.utils.dates import now_kst

        weekday = now_kst().weekday()
    # 토요일(5) 05:00 → 유니버스 주간 재채점 (weekly-discover 전 신선화). hour==5 가
    # 아래 us 분기(5<=hour<7)에 잡히기 전에 먼저 매칭.
    if weekday == 5 and hour == 5:
        return "weekly-rescore"
    # 토요일(5) 08:00 → 주간 발굴 (plist Weekday=6, Hour=8)
    if weekday == 5 and hour == 8:
        return "weekly"
    # 시각 분기 — plist (06:30 / 07:00 / 16:30) 매칭, ±1h 관용
    if hour == 7:
        return "daily"
    if 5 <= hour < 7 or hour == 8:
        return "us"
    if 15 <= hour <= 17:
        return "kr"
    return "all"


def _run_scheduled_task(task: str, *, dry_run: bool) -> int:
    """단일 task 실행. 0=성공, 1=일부 실패."""

    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from stock_compass.alerts import default_manager
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection
    from stock_compass.output.craft import CraftExporter
    from stock_compass.output.terminal import render_score_ranking
    from stock_compass.scoring import ScoringEngine
    from stock_compass.utils.dates import today_kst

    exit_code = 0

    # 주간 발굴 — 별도 흐름
    if task == "weekly":
        import subprocess
        import sys

        cmd = [sys.executable, "-m", "stock_compass", "weekly-discover"]
        if dry_run:
            console.print(f"[dim]dry-run: {' '.join(cmd)}[/dim]")
            return 0
        result = subprocess.run(cmd, check=False)
        return result.returncode

    # 유니버스 주간 재채점 — weekly-discover(08:00) 전 v_latest_scores 신선화.
    # 점수만 갱신(알림·Craft·백업 생략) — 853종목 알림 폭주 방지.
    if task == "weekly-rescore":
        return _run_universe_rescore(dry_run=dry_run)

    if task in ("us", "kr", "all"):
        forced: Market | None = (
            "US" if task == "us" else "KR" if task == "kr" else None
        )
        targets = resolve_targets(
            None, forced, settings.watchlist_kr, settings.watchlist_us
        )
        if not targets:
            console.print(
                f"[yellow]task={task} — 대상 종목 없음 (워치리스트 확인)[/yellow]"
            )
            return 1

        engine = ScoringEngine()
        market_for: dict[str, Market] = dict(targets)
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            results = run_mixed(
                engine,
                [t for t, _ in targets],
                market_for,
                progress,
                persist=not dry_run,
            )
        if len(results) < len(targets):
            exit_code = 1
        if results:
            previous = _previous_scores_for(results)
            render_score_ranking(
                results, console=console, previous_scores=previous
            )

    manager = default_manager()
    with get_db_connection() as conn:
        fired = manager.run(conn, on_date=today_kst(), dry_run=dry_run)
    delivered = sum(1 for f in fired if f.delivered)
    deduped = sum(1 for f in fired if f.deduplicated)
    console.print(
        f"[cyan]alert[/cyan] fired={delivered} deduped={deduped}"
        + (" [dim](dry-run)[/dim]" if dry_run else "")
    )

    if task in ("daily", "all"):
        with get_db_connection() as conn:
            from stock_compass.db import (
                get_previous_total_scores,
                get_scores_on_date,
                get_sector_score_rank,
                get_ticker_id,
                get_today_token_usage,
            )

            on_date = today_kst()
            scores = get_scores_on_date(conn, on_date)
            previous = get_previous_total_scores(
                conn,
                [(s.ticker, s.market) for s in scores],
                before_date=on_date,
            )
            sector_ranks: SectorRanks = {}
            for s in scores:
                if s.sector:
                    tid = get_ticker_id(conn, s.ticker, s.market)
                    if tid is not None:
                        r = get_sector_score_rank(
                            conn, s.market, s.sector, tid
                        )
                        if r is not None:
                            sector_ranks[(s.ticker, s.market)] = r
            token_usage = get_today_token_usage(conn, mode="api", on_date=on_date)
        if scores and not dry_run:
            path = CraftExporter().export(
                scores,
                on_date,
                previous_scores=previous,
                sector_ranks=sector_ranks,
                token_usage=token_usage,
            )
            console.print(f"[green]✓[/green] Craft 노트: [cyan]{path}[/cyan]")
        elif not scores:
            console.print("[yellow]오늘 스냅샷 없음 — Craft 노트 생략[/yellow]")

        # CLAUDE.md 6) Phase 5 — daily 잡 직후 DB 자동 백업
        if not dry_run:
            from stock_compass.utils.backup import backup_database

            backup_path = backup_database(on_date=today_kst())
            if backup_path is not None:
                console.print(
                    f"[dim]✓ DB 백업: {backup_path.name}[/dim]"
                )

    return exit_code


# 주간 재채점 대상 유니버스 (KR 먼저 → US 나중: US 금요일 종가 신선도 확보).
_WEEKLY_RESCORE_UNIVERSES = "ALL_KR,SP500"
# 853종목 풀 API sentiment 1회분(in+out 합산 ~1.5M)을 수용하도록 이 실행에 한해
# 일일 한도 상향 — settings 객체만 변경(프로세스 스코프), plist env·.env 불변.
_WEEKLY_RESCORE_INPUT_LIMIT = 20_000_000


def _run_universe_rescore(*, dry_run: bool) -> int:
    """유니버스(ALL_KR+SP500) 전체 재채점 → composite_scores 신선화. 0=전건 성공."""
    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from stock_compass.config import settings
    from stock_compass.scoring import ScoringEngine
    from stock_compass.utils.dates import today_kst

    targets = resolve_universe_targets(_WEEKLY_RESCORE_UNIVERSES)
    if not targets:
        console.print(
            "[yellow]유니버스 멤버 없음 — `universe refresh` 먼저.[/yellow]"
        )
        return 1

    if dry_run:
        kr = sum(1 for _, m in targets if m == "KR")
        console.print(
            f"[dim]dry-run: 유니버스 재채점 {len(targets)}종목 "
            f"(KR={kr}, US={len(targets) - kr}), 한도={_WEEKLY_RESCORE_INPUT_LIMIT:,}[/dim]"
        )
        return 0

    # 풀 API sentiment 보장 — 기본 500k 한도면 ~120종목 후 fallback(50)으로 끊김.
    settings.anthropic_daily_input_limit = max(
        settings.anthropic_daily_input_limit, _WEEKLY_RESCORE_INPUT_LIMIT
    )

    engine = ScoringEngine()
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        results = run_mixed(
            engine,
            [t for t, _ in targets],
            dict(targets),
            progress,
            persist=True,
        )

    console.print(
        f"[green]✓[/green] 유니버스 재채점 {len(results)}/{len(targets)}종목 "
        f"(date={today_kst().isoformat()})"
    )
    return 0 if len(results) == len(targets) else 1


def _previous_scores_for(
    results: list,  # type: ignore[type-arg]
) -> PreviousScores:
    """batch 결과 종목들의 직전 (어제 또는 이전) total_score + verdict 조회.

    DB 미등록·이력 없는 종목은 결과 dict에 미포함 → render에서 '—' 표시.
    """
    if not results:
        return {}
    from stock_compass.db import get_db_connection, get_previous_total_scores
    from stock_compass.utils.dates import today_kst

    code_markets = [(r.ticker, r.market) for r in results]
    on_date = today_kst()
    with get_db_connection() as conn:
        return get_previous_total_scores(conn, code_markets, before_date=on_date)
