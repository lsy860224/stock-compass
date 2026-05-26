"""news — 단일 종목 최근 뉴스 목록 (어댑터 직접)."""

from __future__ import annotations

from typing import Annotated

import typer

from stock_compass.commands._app import app, console
from stock_compass.commands._helpers import parse_market


@app.command()
def news(
    ticker: Annotated[str, typer.Argument(help="종목 코드")],
    days: Annotated[int, typer.Option("--days", "-d", help="최근 N일")] = 7,
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us")
    ] = None,
) -> None:
    """단일 종목 최근 뉴스 목록 (sentiment 모드 무관, 어댑터에서 직접)."""
    from rich.table import Table

    from stock_compass.config import settings
    from stock_compass.markets import get_adapter
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    market_norm = parse_market(market)

    adapter = get_adapter(ticker, market_norm)
    items = adapter.get_news(ticker, days=days)

    if not items:
        console.print(
            f"[yellow]{ticker}: 최근 {days}일 뉴스 없음 (소스: {adapter.market}).[/yellow]"
        )
        return

    table = Table(title=f"{ticker} 최근 뉴스 ({len(items)}건)", show_lines=False)
    table.add_column("일자", style="cyan", no_wrap=True)
    table.add_column("출처", style="dim")
    table.add_column("제목", overflow="fold")
    for n in items:
        table.add_row(
            n.published_at.strftime("%Y-%m-%d"),
            n.source_name or "—",
            n.title,
        )
    console.print(table)
    console.print(
        "[dim]면책: AI 생성 요약 아님 — 어댑터(yfinance 등) 원본 메타데이터. "
        "Claude 요약은 batch 실행 후 sentiment 팩터 결과로 확인.[/dim]"
    )
