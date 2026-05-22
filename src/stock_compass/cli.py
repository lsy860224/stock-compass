"""stock-compass CLI — typer 진입점.

명령:
    score              단일 종목 점수 (Phase 1)
    batch              워치리스트 일일 배치 (Phase 2)
    history            종목 점수 추이 (Phase 2)
    report             Craft 일일 노트 생성 (Phase 3)
    sentiment prompt   수동 sentiment용 Markdown 생성 (Phase 4)
    sentiment import   Claude.ai 응답 파일 → DB (Phase 4)
    sentiment status   토큰 사용량 + 대기 batch (Phase 4)
    news               단일 종목 뉴스 목록 (Phase 4)
    alert              알림 트리거 체크 (Phase 5)

면책: 본 도구의 출력은 투자 자문이 아닙니다. 본인 판단의 보조 자료입니다.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, get_args

import typer
from rich.console import Console

from stock_compass.markets.base import Market

if TYPE_CHECKING:
    from rich.progress import Progress

    from stock_compass.scoring import CompositeScore, ScoringEngine

_MARKET_VALUES = set(get_args(Market))

app = typer.Typer(
    name="stock-compass",
    help="개인 매매 의사결정 보조 다요인 점수화 도구 (KR·US).",
    no_args_is_help=True,
    add_completion=False,
)
sentiment_app = typer.Typer(help="하이브리드 sentiment — API/Prompt/Import/Status.")
app.add_typer(sentiment_app, name="sentiment")
console = Console()


@app.callback()
def _root() -> None:
    """전역 옵션 자리 (현재는 없음)."""


@app.command()
def score(
    ticker: Annotated[str, typer.Argument(help="종목 코드 (예: AAPL, 005930)")],
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us (자동 감지가 기본)")
    ] = None,
) -> None:
    """단일 종목의 5팩터 점수 + 종합 점수 출력."""
    from stock_compass.config import settings
    from stock_compass.output.terminal import render_single_score
    from stock_compass.scoring import ScoringEngine
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    market_norm = _parse_market(market)

    with console.status(f"[cyan]{ticker}[/cyan] 점수 계산 중…", spinner="dots"):
        try:
            result = ScoringEngine().analyze(ticker, market=market_norm)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1) from e

    render_single_score(result, console=console)


@app.command()
def batch(
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us — 미지정 시 전체")
    ] = None,
    tickers: Annotated[
        str | None,
        typer.Option("--tickers", "-t", help="콤마 구분 직접 지정 (.env 워치리스트 무시)"),
    ] = None,
    no_persist: Annotated[
        bool, typer.Option("--no-persist", help="DB 저장 생략 (드라이런)")
    ] = False,
) -> None:
    """워치리스트 일일 배치 → SQLite 저장 + 점수 순위 출력."""
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

    forced_market = _parse_market(market)
    targets = _resolve_targets(tickers, forced_market, settings.watchlist_kr, settings.watchlist_us)

    if not targets:
        console.print("[yellow]대상 종목이 없습니다. WATCHLIST_KR/US 또는 --tickers 확인.[/yellow]")
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
        # market이 종목별로 섞일 수 있어 analyze_watchlist에 직접 마켓을 넘기지 않고 종목별 분기
        results = _run_mixed(engine, just_tickers, market_for, progress, persist=not no_persist)

    render_score_ranking(results, console=console)


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

    market_norm = _parse_market(market) or detect_market(ticker)
    with get_db_connection() as conn:
        rows = get_score_history(conn, ticker, market_norm, days=days)
    render_history(ticker, market_norm, rows, console=console)


@app.command()
def alert(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="발화 없이 어떤 알림이 나갈지만 출력")
    ] = False,
) -> None:
    """3종 알림 트리거(임계치·급변·일일) 체크 후 발화 (Phase 5)."""
    _ = dry_run
    raise NotImplementedError("alert 명령은 Phase 5에서 구현됩니다.")


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
) -> None:
    """Craft 일일 노트 Markdown 생성 (`data/craft_export/YYYY-MM-DD.md`)."""
    import subprocess
    from datetime import date as date_cls

    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, get_scores_on_date
    from stock_compass.output.craft import CraftExporter
    from stock_compass.utils.dates import today_kst
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    market_norm = _parse_market(market)
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
    path = exporter.export(scores, on_date)
    console.print(
        f"[green]✓[/green] 노트 생성: [cyan]{path}[/cyan]  ({len(scores)}종목)"
    )

    if open_file:
        try:
            subprocess.run(["open", "-R", str(path)], check=False)
        except FileNotFoundError:
            console.print("[yellow]`open` 명령 미지원 (macOS 외부 환경).[/yellow]")


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
    forced = _parse_market(market)
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
    market_norm = _parse_market(market)

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


def _parse_market(raw: str | None) -> Market | None:
    """--market 옵션 → 'KR'/'US' 또는 None. 잘못된 값은 즉시 종료."""
    if raw is None:
        return None
    m = raw.upper()
    if m not in _MARKET_VALUES:
        console.print(f"[red]지원하지 않는 시장: {raw!r} (kr / us 만 허용)[/red]")
        raise typer.Exit(code=2)
    return m  # type: ignore[return-value]


def _resolve_targets(
    tickers_csv: str | None,
    forced_market: Market | None,
    wl_kr: list[str],
    wl_us: list[str],
) -> list[tuple[str, Market]]:
    """(ticker, market) 튜플 리스트 생성. 명시 우선, 그다음 env 워치리스트."""
    from stock_compass.markets import detect_market

    if tickers_csv:
        tokens = [t.strip() for t in tickers_csv.split(",") if t.strip()]
        return [(t, forced_market or detect_market(t)) for t in tokens]

    out: list[tuple[str, Market]] = []
    if forced_market in (None, "KR"):
        out.extend((t, "KR") for t in wl_kr)
    if forced_market in (None, "US"):
        out.extend((t, "US") for t in wl_us)
    return out


def _run_mixed(
    engine: ScoringEngine,
    tickers: list[str],
    market_for: dict[str, Market],
    progress: Progress,
    *,
    persist: bool,
) -> list[CompositeScore]:
    """KR/US가 섞인 목록을 한 번에 처리. 시장별로 그룹 분리해 analyze_watchlist 호출."""
    from collections import defaultdict

    groups: dict[Market, list[str]] = defaultdict(list)
    for t in tickers:
        groups[market_for[t]].append(t)

    all_results: list[CompositeScore] = []
    for market, ts in groups.items():
        results = engine.analyze_watchlist(
            ts, market=market, persist=persist, progress=progress
        )
        all_results.extend(results)
    return all_results


if __name__ == "__main__":
    app()
