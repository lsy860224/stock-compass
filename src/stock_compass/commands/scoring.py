"""score / batch / history / batch-and-alert — 점수화·이력·스케줄 통합."""

from __future__ import annotations

from datetime import date as date_cls
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
        # .env 워치리스트 + DB 추적 종목 (discover 포함) 자동 채점
        targets = resolve_default_targets(forced)
        if not targets:
            console.print(
                f"[yellow]task={task} — 대상 종목 없음 (워치리스트/추적 확인)[/yellow]"
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
            _batch_health_check(task, len(results), len(targets), dry_run=dry_run)
        if results:
            previous = _previous_scores_for(results)
            render_score_ranking(
                results, console=console, previous_scores=previous
            )
            if not dry_run:
                _publish_batch_reports(results, today_kst())

    manager = default_manager()
    with get_db_connection() as conn:
        fired = manager.run(conn, on_date=today_kst(), dry_run=dry_run)
    delivered = sum(1 for f in fired if f.delivered)
    deduped = sum(1 for f in fired if f.deduplicated)
    console.print(
        f"[cyan]alert[/cyan] fired={delivered} deduped={deduped}"
        + (" [dim](dry-run)[/dim]" if dry_run else "")
    )
    if delivered and not dry_run:
        _publish_alerts_report(fired, today_kst())

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
            exporter = CraftExporter()
            path = exporter.export(
                scores,
                on_date,
                previous_scores=previous,
                sector_ranks=sector_ranks,
                token_usage=token_usage,
            )
            console.print(f"[green]✓[/green] craft_export 파일: [cyan]{path}[/cyan]")
            # dual-sink: Obsidian 볼트 + Craft API (파일 export와 동일 본문)
            content = exporter.render_daily_note(
                scores,
                on_date,
                previous_scores=previous,
                sector_ranks=sector_ranks,
                token_usage=token_usage,
            )
            _publish_via_sinks(
                content,
                on_date=on_date,
                kind="daily",
                title=f"stock-compass · daily · {on_date.isoformat()}",
                filename=on_date.isoformat(),
                charts=_daily_charts(scores),
            )
        elif not scores:
            console.print("[yellow]오늘 스냅샷 없음 — 일일 노트 생략[/yellow]")

        # CLAUDE.md 6) Phase 5 — daily 잡 직후 DB 자동 백업 + 디스크 정리
        if not dry_run:
            from stock_compass.utils.backup import backup_database
            from stock_compass.utils.maintenance import prune_craft_export_backups

            backup_path = backup_database(on_date=today_kst())
            if backup_path is not None:
                console.print(f"[dim]✓ DB 백업: {backup_path.name}[/dim]")
            pruned = prune_craft_export_backups()
            if pruned:
                console.print(f"[dim]✓ craft_export .bak 정리: {pruned}개[/dim]")

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

    # #1 멤버십 선갱신 — 재채점이 최신 편입/퇴출을 반영하도록 (실패 격리).
    _refresh_universe_membership()
    targets = resolve_universe_targets(_WEEKLY_RESCORE_UNIVERSES)

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

    on_date = today_kst()
    console.print(
        f"[green]✓[/green] 유니버스 재채점 {len(results)}/{len(targets)}종목 "
        f"(date={on_date.isoformat()})"
    )
    if results:
        from stock_compass.output.report_render import render_rescore_summary

        iso_year, iso_week, _ = on_date.isocalendar()
        _publish_via_sinks(
            render_rescore_summary(results, on_date),
            on_date=on_date,
            kind="weekly-rescore",
            title=f"stock-compass · weekly-rescore · {on_date.isoformat()}",
            filename=f"{iso_year}-W{iso_week:02d} 재채점",
        )
        # #3 관심권(≥threshold) 신규 진입 종목 → 노트 + macOS 알림
        _report_universe_entrants(results, on_date)
    return 0 if len(results) == len(targets) else 1


def _refresh_universe_membership() -> None:
    """주간 재채점 전 유니버스 멤버십 갱신 (ALL_KR·SP500). 실패는 격리 (기존 정전 유지)."""
    from stock_compass.db import get_db_connection
    from stock_compass.screener.universes import UniverseFetchError, refresh

    with get_db_connection() as conn:
        for code in ("ALL_KR", "SP500"):
            try:
                r = refresh(conn, code)
                console.print(
                    f"[dim]universe {r.universe_code}: +{r.members} "
                    f"(as_of={r.as_of_date})[/dim]"
                )
            except (UniverseFetchError, ValueError) as e:
                console.print(f"[yellow]universe {code} 갱신 실패: {e}[/yellow]")


def _report_universe_entrants(
    results: list,  # type: ignore[type-arg]
    on_date: date_cls,
) -> None:
    """직전 대비 관심권(≥alert_threshold_buy) 신규 진입 종목 보고 + macOS 알림."""
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, get_previous_total_scores
    from stock_compass.output.notify import macos_notify
    from stock_compass.output.report_render import render_universe_entrants

    thr = settings.alert_threshold_buy
    high = [s for s in results if s.total_score >= thr]
    if not high:
        return
    with get_db_connection() as conn:
        prev = get_previous_total_scores(
            conn, [(s.ticker, s.market) for s in high], before_date=on_date
        )
    entrants = [
        s
        for s in high
        if (p := prev.get((s.ticker, s.market))) is None or p[0] < thr
    ]
    if not entrants:
        return
    entrants.sort(key=lambda x: x.total_score, reverse=True)

    _publish_via_sinks(
        render_universe_entrants(entrants, on_date, threshold=thr),
        on_date=on_date,
        kind="universe-entry",
        title=f"stock-compass · 관심권 신규진입 · {on_date.isoformat()}",
        filename=f"{on_date.isoformat()} 관심권 진입",
    )
    top = ", ".join(f"{s.ticker}({s.total_score:.0f})" for s in entrants[:5])
    macos_notify(
        title=f"📈 유니버스 관심권 신규진입 {len(entrants)}종목",
        body=f"≥{thr}점 진입: {top}" + (" 외" if len(entrants) > 5 else ""),
        subtitle="주간 재채점",
    )


def _publish_via_sinks(
    content: str,
    *,
    on_date: date_cls,
    kind: str,
    title: str,
    filename: str,
    charts: list[tuple[str, bytes]] | None = None,
) -> None:
    """publish_report(Obsidian + Craft) 호출 + 결과 콘솔 출력 — 자동화 공용."""
    from stock_compass.output.report import publish_report

    result = publish_report(
        content,
        on_date=on_date,
        kind=kind,
        title=title,
        filename=filename,
        charts=charts,
    )
    if result.obsidian_path is not None:
        console.print(
            f"[green]✓[/green] Obsidian: [cyan]{result.obsidian_path.name}[/cyan]"
        )
    if result.craft_url:
        console.print(f"[green]✓[/green] Craft: [cyan]{result.craft_url}[/cyan]")
    elif result.craft_skipped:
        console.print(f"[dim]Craft 발행 skip ({result.craft_skipped})[/dim]")
    if not result.any_delivered:
        console.print(f"[yellow]보고 sink 전부 미발행 ({kind})[/yellow]")


def _publish_batch_reports(
    results: list,  # type: ignore[type-arg]
    on_date: date_cls,
) -> None:
    """장 마감 배치 결과를 시장별로 분리해 보고 발행 (batch-us / batch-kr)."""
    from collections import defaultdict

    from stock_compass.output.report_render import render_batch_note

    groups: dict[Market, list] = defaultdict(list)  # type: ignore[type-arg]
    for s in results:
        groups[s.market].append(s)
    for market, market_scores in groups.items():
        content = render_batch_note(market_scores, market, on_date)
        _publish_via_sinks(
            content,
            on_date=on_date,
            kind=f"batch-{market.lower()}",
            title=f"stock-compass · batch {market} · {on_date.isoformat()}",
            filename=f"{on_date.isoformat()} {market}",
        )


def _publish_alerts_report(
    fired: list,  # type: ignore[type-arg]
    on_date: date_cls,
) -> None:
    """발화된 알림 요약 보고 발행."""
    from stock_compass.output.report_render import render_alerts_note

    content = render_alerts_note(fired, on_date)
    _publish_via_sinks(
        content,
        on_date=on_date,
        kind="alerts",
        title=f"stock-compass · alerts · {on_date.isoformat()}",
        filename=f"{on_date.isoformat()} 알림",
    )


def _batch_health_check(
    task: str, succeeded: int, total: int, *, dry_run: bool
) -> None:
    """배치 실패율이 임계 초과면 macOS 알림 — 데이터 소스 장애 조기 감지."""
    from stock_compass.config import settings

    if total == 0:
        return
    failed = total - succeeded
    ratio = failed / total
    console.print(
        f"[yellow]배치 실패 {failed}/{total} ({ratio:.0%})[/yellow]"
    )
    if dry_run or ratio < settings.batch_failure_alert_ratio:
        return
    from stock_compass.output.notify import macos_notify

    macos_notify(
        title="⚠️ stock-compass 배치 경고",
        body=f"{task} 배치 {failed}/{total} 종목 실패 ({ratio:.0%}) — 데이터 소스 점검 필요",
        subtitle="헬스체크",
    )


def _daily_charts(
    scores: list,  # type: ignore[type-arg]
) -> list[tuple[str, bytes]]:
    """워치리스트 종목별 30일 점수추이 차트 (caption, png). 데이터 부족·실패는 skip."""
    from stock_compass.db import get_db_connection, get_score_history
    from stock_compass.output.chart import render_score_history_chart

    charts: list[tuple[str, bytes]] = []
    with get_db_connection() as conn:
        for s in scores:
            try:
                hist = get_score_history(conn, s.ticker, s.market, days=30)
                png = render_score_history_chart(hist, ticker=s.ticker, name=s.name)
            except Exception:
                png = None
            if png is not None:
                charts.append((f"{s.ticker} {s.name or ''}".strip(), png))
    return charts


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
