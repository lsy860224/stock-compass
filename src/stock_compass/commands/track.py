"""track 서브그룹 — `.env` 외 종목을 DB(watchlists)로 영속 추적.

add/remove/list. 추적 등록 즉시 일일 배치(.env + 추적)가 자동 채점 → 점수
히스토리 축적. discover 발굴 종목은 'discover' 그룹으로 자동 등록된다.
"""

from __future__ import annotations

from typing import Annotated

import typer

from stock_compass.commands._app import console, track_app
from stock_compass.commands._helpers import parse_market


@track_app.command("add")
def track_add(
    tickers: Annotated[
        str, typer.Argument(help="콤마 구분 종목 코드 (예: AAPL,005930)")
    ],
    group: Annotated[
        str, typer.Option("--group", "-g", help="추적 그룹 (기본 manual)")
    ] = "manual",
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us (자동 감지가 기본)")
    ] = None,
    note: Annotated[
        str | None, typer.Option("--note", help="메모 (provenance·사유)")
    ] = None,
) -> None:
    """종목을 추적 그룹에 등록 — 다음 배치부터 자동 채점·히스토리 축적."""
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, track_ticker
    from stock_compass.markets import detect_market
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    forced = parse_market(market)
    codes = [c.strip() for c in tickers.split(",") if c.strip()]
    if not codes:
        console.print("[red]종목 코드가 비어 있습니다.[/red]")
        raise typer.Exit(code=2)

    added = 0
    with get_db_connection() as conn:
        for code in codes:
            m = forced or detect_market(code)
            if track_ticker(
                conn, code=code, market=m, group=group, added_by="manual", notes=note
            ):
                added += 1
    console.print(
        f"[green]✓[/green] '[cyan]{group}[/cyan]' 그룹에 {added}/{len(codes)}종목 "
        f"신규 추적 (다음 배치부터 자동 채점)"
    )


@track_app.command("remove")
def track_remove(
    tickers: Annotated[str, typer.Argument(help="콤마 구분 종목 코드")],
    group: Annotated[
        str | None,
        typer.Option("--group", "-g", help="그룹 (미지정 시 전 그룹에서 해제)"),
    ] = None,
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us (자동 감지가 기본)")
    ] = None,
) -> None:
    """추적 해제 — DB 추적 목록에서 제거 (과거 점수 이력은 보존)."""
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, get_ticker_id, remove_tracked
    from stock_compass.markets import detect_market
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    forced = parse_market(market)
    codes = [c.strip() for c in tickers.split(",") if c.strip()]
    if not codes:
        console.print("[red]종목 코드가 비어 있습니다.[/red]")
        raise typer.Exit(code=2)

    removed = 0
    with get_db_connection() as conn:
        for code in codes:
            m = forced or detect_market(code)
            tid = get_ticker_id(conn, code, m)
            if tid is None:
                continue
            removed += remove_tracked(conn, ticker_id=tid, group=group)
    scope = f"'{group}' 그룹" if group else "전 그룹"
    console.print(f"[green]✓[/green] {scope}에서 {removed}건 추적 해제")


@track_app.command("list")
def track_list(
    group: Annotated[
        str | None, typer.Option("--group", "-g", help="특정 그룹만 (미지정 시 전체)")
    ] = None,
) -> None:
    """추적 종목 목록 (그룹·provenance·메모)."""
    from rich.table import Table

    from stock_compass.config import settings
    from stock_compass.db import (
        count_tracked_by_group,
        get_db_connection,
        list_tracked,
    )
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    with get_db_connection() as conn:
        rows = list_tracked(conn, group=group)
        counts = count_tracked_by_group(conn)

    if not rows:
        console.print(
            "[yellow]추적 종목 없음 — `track add <코드>` 또는 weekly-discover 발굴 대기.[/yellow]"
        )
        return

    summary = " · ".join(f"{g}={n}" for g, n in counts)
    table = Table(title=f"추적 종목 {len(rows)} ({summary})")
    table.add_column("종목", style="cyan")
    table.add_column("이름")
    table.add_column("시장", justify="center")
    table.add_column("그룹", style="green")
    table.add_column("출처", style="dim")
    table.add_column("등록", style="dim")
    table.add_column("메모", overflow="fold")
    for r in rows:
        table.add_row(
            str(r["code"]),
            str(r["name"] or "—"),
            str(r["market"]),
            str(r["group_name"]),
            str(r["added_by"]),
            str(r["added_at"])[:10],
            str(r["notes"] or ""),
        )
    console.print(table)
