"""universe 서브그룹 — refresh / list."""

from __future__ import annotations

from typing import Annotated

import typer

from stock_compass.commands._app import console, universe_app


@universe_app.command("refresh")
def universe_refresh(
    code: Annotated[
        str | None,
        typer.Option(
            "--code",
            help="갱신할 universe (WATCHLIST/KOSPI_200/KOSDAQ_150/SP500/NASDAQ_100/DOW30)",
        ),
    ] = None,
) -> None:
    """유니버스 멤버 갱신 — pykrx(KR) + Wikipedia(US). 실패 시 7일 fallback 캐시."""
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection
    from stock_compass.screener.universes import (
        SUPPORTED_UNIVERSES,
        UNIVERSE_WATCHLIST,
        UniverseFetchError,
        refresh,
    )
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    target = (code or UNIVERSE_WATCHLIST).upper()
    if target not in SUPPORTED_UNIVERSES:
        console.print(
            f"[red]지원하지 않는 universe: {target}[/red]\n"
            f"[dim]지원: {', '.join(SUPPORTED_UNIVERSES)}[/dim]"
        )
        raise typer.Exit(code=2)

    try:
        with get_db_connection() as conn:
            r = refresh(conn, target)
    except UniverseFetchError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    console.print(
        f"[green]✓[/green] {r.universe_code}: {r.members}종목 신규 등록 "
        f"(as_of={r.as_of_date})"
    )


@universe_app.command("list")
def universe_list(
    code: Annotated[
        str | None,
        typer.Option("--code", help="특정 universe 멤버 (미지정 시 universe별 카운트)"),
    ] = None,
) -> None:
    """유니버스 목록 또는 멤버 조회."""
    from rich.table import Table

    from stock_compass.config import settings
    from stock_compass.db import get_db_connection
    from stock_compass.screener.universes import list_universe_members
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    with get_db_connection() as conn:
        rows = list_universe_members(conn, universe_code=code.upper() if code else None)

    if not rows:
        console.print("[yellow]등록된 universe 멤버 없음 — `universe refresh` 먼저.[/yellow]")
        return

    if code:
        table = Table(title=f"{code.upper()} 멤버 ({len(rows)}종목)")
        table.add_column("종목", style="cyan")
        table.add_column("이름")
        table.add_column("시장", justify="center")
        table.add_column("기준일", style="dim")
        for r in rows:
            table.add_row(
                str(r["code"]),
                str(r["name"]),
                str(r["market"]),
                str(r["as_of_date"]),
            )
    else:
        table = Table(title="등록된 universe 목록")
        table.add_column("Universe", style="cyan")
        table.add_column("멤버", justify="right")
        table.add_column("최신 기준일", style="dim")
        for r in rows:
            table.add_row(
                str(r["universe_code"]),
                str(r["member_count"]),
                str(r["latest_date"]),
            )
    console.print(table)
