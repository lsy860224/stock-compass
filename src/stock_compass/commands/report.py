"""report — Craft 일일 노트 파일 생성 + 옵션으로 Craft Pro API 발행."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from stock_compass.commands._app import app, console
from stock_compass.commands._helpers import parse_market

if TYPE_CHECKING:
    from stock_compass.scoring import CompositeScore


@app.command()
def report(
    date: Annotated[
        str | None, typer.Option("--date", help="YYYY-MM-DD (기본: 오늘 KST)")
    ] = None,
    open_file: Annotated[
        bool, typer.Option("--open", help="생성 후 Finder에서 reveal")
    ] = False,
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us — 미지정 시 전체")
    ] = None,
    publish: Annotated[
        bool,
        typer.Option(
            "--publish", help="파일 생성 후 Craft Pro API로 즉시 발행 (CRAFT_API_TOKEN 필요)"
        ),
    ] = False,
    no_file: Annotated[
        bool,
        typer.Option(
            "--no-file", help="파일 생성 생략 (--publish와 함께 사용해 API만 발행)"
        ),
    ] = False,
) -> None:
    """Craft 일일 노트 — 파일 생성 + 옵션으로 Craft Pro API 자동 발행."""
    import subprocess
    from datetime import date as date_cls

    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, get_scores_on_date
    from stock_compass.output.craft import (
        CraftAPIError,
        CraftAuthError,
        CraftExporter,
        CraftPublisher,
    )
    from stock_compass.utils.dates import today_kst
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    market_norm = parse_market(market)
    if date is None:
        on_date = today_kst()
    else:
        try:
            on_date = date_cls.fromisoformat(date)
        except ValueError as e:
            console.print(f"[red]잘못된 날짜 형식: {date!r} (YYYY-MM-DD 필요)[/red]")
            raise typer.Exit(code=2) from e

    with get_db_connection() as conn:
        scores = get_scores_on_date(conn, on_date, market=market_norm)

    if not scores:
        console.print(
            f"[yellow]{on_date} 점수 없음 — 먼저 `stock-compass batch` 실행하세요.[/yellow]"
        )
        raise typer.Exit(code=1)

    exporter = CraftExporter()
    content = exporter.render_daily_note(scores, on_date)

    if not no_file:
        path = exporter.export_to_file(content, on_date)
        console.print(
            f"[green]✓[/green] 노트 파일: [cyan]{path}[/cyan]  ({len(scores)}종목)"
        )
        if open_file:
            try:
                subprocess.run(["open", "-R", str(path)], check=False)
            except FileNotFoundError:
                console.print("[yellow]`open` 명령 미지원 (macOS 외부 환경).[/yellow]")

    if publish:
        charts = _build_interest_charts(scores)
        try:
            publisher = CraftPublisher()
            result = publisher.publish_daily_note(content, on_date, charts=charts)
            action = "갱신" if result.is_update else "발행"
            chart_note = f" + 차트 {len(charts)}개" if charts else ""
            console.print(
                f"[green]✓ Craft {action}:[/green] [cyan]{result.url}[/cyan]"
                f" (note_id={result.note_id}{chart_note})"
            )
        except CraftAuthError as e:
            console.print(f"[yellow]Craft 인증 실패: {e}[/yellow]")
        except CraftAPIError as e:
            console.print(f"[red]Craft API 오류: {e}[/red]")


def _build_interest_charts(
    scores: list[CompositeScore],
) -> list[tuple[str, bytes]]:
    """관심권(>=70) 종목 한정 30일 추이 차트. 데이터 부족 종목은 자동 스킵."""
    from stock_compass.db import get_db_connection, get_score_history
    from stock_compass.output.chart import render_score_history_chart

    interest = [s for s in scores if s.verdict == "관심권"]
    if not interest:
        return []

    out: list[tuple[str, bytes]] = []
    with get_db_connection() as conn:
        for s in interest:
            history = get_score_history(conn, s.ticker, s.market, days=30)
            png = render_score_history_chart(
                history, ticker=s.ticker, name=s.name
            )
            if png is None:
                continue
            caption = (
                f"{s.name} ({s.ticker})" if s.name else s.ticker
            ) + f" — 현재 {s.total_score:.1f}점"
            out.append((caption, png))
    return out
