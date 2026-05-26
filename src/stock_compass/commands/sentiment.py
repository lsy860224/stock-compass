"""sentiment 서브그룹 — prompt / import / status."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from stock_compass.commands._app import console, sentiment_app
from stock_compass.commands._helpers import parse_market
from stock_compass.markets.base import Market


@sentiment_app.command("prompt")
def sentiment_prompt(
    tickers: Annotated[
        str, typer.Option("--tickers", "-t", help="콤마 구분 종목 코드 (필수)")
    ],
    days: Annotated[int, typer.Option("--days", "-d", help="최근 N일 뉴스/공시")] = 7,
    tag: Annotated[str, typer.Option("--tag", help="batch_id에 포함될 태그")] = "sentiment",
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us — 자동 감지가 기본")
    ] = None,
) -> None:
    """수동 sentiment용 Markdown 파일 생성 — Claude.ai에 복붙."""
    from stock_compass.config import settings
    from stock_compass.markets import detect_market
    from stock_compass.output.prompt_generator import PromptGenerator
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    forced = parse_market(market)
    codes = [t.strip() for t in tickers.split(",") if t.strip()]
    if not codes:
        console.print("[red]--tickers 비어 있음[/red]")
        raise typer.Exit(code=2)

    pairs: list[tuple[str, Market]] = [
        (c, forced or detect_market(c)) for c in codes
    ]
    result = PromptGenerator().generate_sentiment_prompt(pairs, days=days, tag=tag)

    console.print(
        f"[green]✓[/green] Prompt 생성: [cyan]{result.path}[/cyan]"
        f"  (batch_id=[yellow]{result.batch_id}[/yellow], "
        f"{result.ticker_count}종목, 파일 {result.file_count}개)"
    )
    console.print(
        "\n[dim]다음 단계:[/dim]\n"
        f"  1. 위 파일 전체를 Claude.ai에 복붙\n"
        f"  2. 응답을 텍스트 파일로 저장\n"
        f"  3. [cyan]stock-compass sentiment import "
        f"--file <응답파일> --batch-id {result.batch_id}[/cyan]"
    )


@sentiment_app.command("import")
def sentiment_import(
    file: Annotated[Path, typer.Option("--file", "-f", help="Claude.ai 응답 파일 경로")],
    batch_id: Annotated[
        str | None,
        typer.Option(
            "--batch-id",
            help="기대 batch_id (지정 시 응답과 일치 검증; 미지정 시 응답 값 사용)",
        ),
    ] = None,
    no_archive: Annotated[
        bool, typer.Option("--no-archive", help="처리 후 archive/로 사본 복사 생략")
    ] = False,
) -> None:
    """Claude.ai JSON 응답 파일 → news_summaries (source='manual_prompt') 저장."""
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection
    from stock_compass.llm.prompt_importer import (
        PromptImportError,
        import_response,
    )
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    if not file.exists():
        console.print(f"[red]파일 없음: {file}[/red]")
        raise typer.Exit(code=2)

    try:
        with get_db_connection() as conn:
            result = import_response(
                conn, file, batch_id=batch_id, archive=not no_archive
            )
    except PromptImportError as e:
        console.print(f"[red]import 실패: {e}[/red]")
        raise typer.Exit(code=1) from e

    console.print(
        f"[green]✓[/green] 저장: {result.saved}건  ·  batch_id={result.batch_id}"
    )
    for w in result.warnings:
        console.print(f"  [yellow]⚠ {w}[/yellow]")
    for b in result.blocked:
        console.print(f"  [red]✕ {b}[/red]")
    if result.archived_to:
        console.print(f"  [dim]archive: {result.archived_to}[/dim]")


@sentiment_app.command("status")
def sentiment_status() -> None:
    """오늘의 Anthropic 토큰 사용량 + 대기 중 prompt batch 목록."""
    from rich.table import Table

    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, get_today_token_usage
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    with get_db_connection() as conn:
        usage = get_today_token_usage(conn, mode="api")

    in_used = int(usage["input_tokens"])
    in_limit = settings.anthropic_daily_input_limit
    pct = (in_used / in_limit * 100) if in_limit > 0 else 0.0

    table = Table(title="오늘 토큰 사용량 (API)", show_lines=False)
    table.add_column("항목", style="cyan")
    table.add_column("값", justify="right")
    table.add_row("Input tokens", f"{in_used:,} / {in_limit:,} ({pct:.1f}%)")
    table.add_row("Output tokens", f"{int(usage['output_tokens']):,}")
    table.add_row("호출 수", f"{int(usage['call_count']):,}")
    table.add_row("누적 비용", f"${float(usage['cost_usd']):.4f}")
    console.print(table)

    pending = sorted(
        p for p in settings.prompt_dir.glob("*.md") if p.parent == settings.prompt_dir
    )
    if pending:
        console.print(
            f"\n[cyan]대기 중 prompt 파일 ({len(pending)}개):[/cyan]"
        )
        for p in pending:
            console.print(f"  - {p.name}")
        console.print(
            "\n[dim]응답 받으면 `stock-compass sentiment import --file <응답>` 실행[/dim]"
        )
    else:
        console.print(f"\n[dim]대기 중 prompt 없음 ({settings.prompt_dir})[/dim]")
