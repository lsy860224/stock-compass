"""alert — 3종 트리거 평가 + macOS 알림 발화 + DB 기록."""

from __future__ import annotations

from typing import Annotated

import typer

from stock_compass.commands._app import app, console


@app.command()
def alert(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="발화 없이 어떤 알림이 나갈지만 출력")
    ] = False,
    date: Annotated[
        str | None,
        typer.Option("--date", help="대상 일자 YYYY-MM-DD (기본: 오늘 KST)"),
    ] = None,
) -> None:
    """3종 트리거(임계치/급변/일일) 평가 + macOS 알림 발화 + DB 기록."""
    from datetime import date as date_cls

    from rich.table import Table

    from stock_compass.alerts import default_manager
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection
    from stock_compass.utils.dates import today_kst
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    if date is None:
        on_date = today_kst()
    else:
        try:
            on_date = date_cls.fromisoformat(date)
        except ValueError as e:
            console.print(f"[red]잘못된 날짜 형식: {date!r}[/red]")
            raise typer.Exit(code=2) from e

    manager = default_manager()
    with get_db_connection() as conn:
        fired = manager.run(conn, on_date=on_date, dry_run=dry_run)

    if not fired:
        console.print(
            f"[dim]{on_date} 발화할 알림 없음 (스냅샷 부재이거나 트리거 조건 미충족).[/dim]"
        )
        return

    table = Table(
        title=f"알림 평가 결과 ({on_date}{' · dry-run' if dry_run else ''})",
        show_lines=False,
    )
    table.add_column("종목", style="cyan")
    table.add_column("Trigger", style="magenta")
    table.add_column("이전→현재", justify="right")
    table.add_column("상태", justify="center")
    table.add_column("사유 / 메시지", overflow="fold")

    for f in fired:
        before = (
            f"{f.alert.score_before:.1f}" if f.alert.score_before is not None else "—"
        )
        delta = f"{before} → {f.alert.score_after:.1f}"
        if f.deduplicated:
            status = "[yellow]중복 차단[/yellow]"
            reason = f.reason
        elif f.delivered:
            status = "[green]발화 ✓[/green]"
            reason = f"{f.delivered_via} · {f.alert.body}"
        else:
            status = "[dim]dry-run[/dim]"
            reason = f.alert.body
        table.add_row(f.alert.ticker, f.alert.trigger_type, delta, status, reason)

    console.print(table)
    console.print(
        "[dim]면책: 알림은 점수 기반 정보 요약. 본인 판단의 보조 자료. "
        "BUY/SELL 명령 아님.[/dim]"
    )
