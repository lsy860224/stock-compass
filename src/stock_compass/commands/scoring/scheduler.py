"""launchd 자동화 엔진 — KST 시각·요일 기반 task 분기 + 실행 오케스트레이션."""

from __future__ import annotations

import typer

from stock_compass.commands._app import console
from stock_compass.commands._helpers import resolve_default_targets, run_mixed
from stock_compass.commands.scoring.reporting import (
    batch_health_check,
    daily_charts,
    previous_scores_for,
    publish_alerts_report,
    publish_batch_reports,
    publish_via_sinks,
)
from stock_compass.commands.scoring.rescore import run_universe_rescore
from stock_compass.markets.base import Market
from stock_compass.output.craft_exporter import SectorRanks


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


def run_scheduled_task(task: str, *, dry_run: bool) -> int:
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
        return run_universe_rescore(dry_run=dry_run)

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
            batch_health_check(task, len(results), len(targets), dry_run=dry_run)
        if results:
            previous = previous_scores_for(results)
            render_score_ranking(
                results, console=console, previous_scores=previous
            )
            if not dry_run:
                publish_batch_reports(results, today_kst())

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
        publish_alerts_report(fired, today_kst())

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
            publish_via_sinks(
                content,
                on_date=on_date,
                kind="daily",
                title=f"stock-compass · daily · {on_date.isoformat()}",
                filename=on_date.isoformat(),
                charts=daily_charts(scores),
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
