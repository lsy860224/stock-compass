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
trade_app = typer.Typer(help="매매 일지 — 입력·조회·편향 분석.")
app.add_typer(trade_app, name="trade")
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

    resolved = _resolve_task(task, now_kst().hour)
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


@trade_app.command("add")
def trade_add(
    ticker: Annotated[str, typer.Argument(help="종목 코드")],
    side: Annotated[str, typer.Argument(help="buy / sell")],
    price: Annotated[float, typer.Option("--price", "-p", help="체결가 (현지 통화)")],
    qty: Annotated[float, typer.Option("--qty", "-q", help="수량 (분할 매매 OK)")],
    reason: Annotated[
        str | None, typer.Option("--reason", "-r", help="매매 사유 (편향 분석용)")
    ] = None,
    tag: Annotated[
        str | None,
        typer.Option(
            "--tag", help="planned / impulse / rebalance / 본인 정의 (분석용)"
        ),
    ] = None,
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us")
    ] = None,
    score: Annotated[
        float | None,
        typer.Option("--score", help="진입 시점 점수 명시 (미지정 시 자동 최신)"),
    ] = None,
) -> None:
    """매매 한 건 기록 (점수 자동 lookup 또는 수동 지정)."""
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, insert_trade
    from stock_compass.markets import detect_market
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    side_norm = side.lower()
    if side_norm not in ("buy", "sell"):
        console.print(f"[red]side는 buy/sell: {side!r}[/red]")
        raise typer.Exit(code=2)
    market_norm = _parse_market(market) or detect_market(ticker)

    try:
        with get_db_connection() as conn:
            trade = insert_trade(
                conn,
                ticker=ticker,
                market=market_norm,
                side=side_norm,  # type: ignore[arg-type]
                price=price,
                qty=qty,
                reason=reason,
                tag=tag,
                score_at_trade=score,
            )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    score_str = (
        f"{trade.score_at_trade:.1f}" if trade.score_at_trade is not None else "—"
    )
    console.print(
        f"[green]✓[/green] {trade.side.upper()} {trade.ticker} "
        f"{trade.qty:g} @ {trade.price:,.2f} (점수 {score_str})"
        + (f"\n  사유: {trade.reason}" if trade.reason else "")
        + (f"\n  태그: {trade.tag}" if trade.tag else "")
    )


@trade_app.command("list")
def trade_list(
    days: Annotated[int, typer.Option("--days", "-d", help="최근 N일")] = 30,
    ticker: Annotated[
        str | None, typer.Option("--ticker", "-t", help="특정 종목만")
    ] = None,
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us")
    ] = None,
) -> None:
    """매매 일지 조회 (최신순)."""
    from rich.table import Table

    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, get_trades
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    market_norm = _parse_market(market)

    with get_db_connection() as conn:
        trades = get_trades(conn, days=days, ticker=ticker, market=market_norm)

    if not trades:
        console.print(f"[yellow]최근 {days}일 매매 없음.[/yellow]")
        return

    table = Table(title=f"매매 일지 (최근 {days}일, {len(trades)}건)")
    table.add_column("일시", style="cyan", no_wrap=True)
    table.add_column("종목", style="cyan")
    table.add_column("매매", justify="center")
    table.add_column("가격", justify="right")
    table.add_column("수량", justify="right")
    table.add_column("점수", justify="right")
    table.add_column("태그", style="magenta")
    table.add_column("사유", overflow="fold")

    for t in trades:
        side_color = "green" if t.side == "buy" else "red"
        table.add_row(
            t.executed_at.split("T")[0],
            f"{t.ticker} [{t.market}]",
            f"[{side_color}]{t.side.upper()}[/{side_color}]",
            f"{t.price:,.2f}",
            f"{t.qty:g}",
            f"{t.score_at_trade:.1f}" if t.score_at_trade is not None else "—",
            t.tag or "—",
            t.reason or "—",
        )
    console.print(table)


@trade_app.command("analyze")
def trade_analyze(
    days: Annotated[int, typer.Option("--days", "-d", help="분석 기간 N일")] = 90,
) -> None:
    """본인 매매 편향 리포트 — 진입 시점 점수 분포 + 태그별 평균."""
    from rich.table import Table

    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, get_performance_summary
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    with get_db_connection() as conn:
        summary = get_performance_summary(conn, days=days)

    if summary["total"] == 0:
        console.print(
            f"[yellow]최근 {days}일 점수 동반 매매 없음 — "
            "`trade add` + batch 사전 실행 필요.[/yellow]"
        )
        return

    console.print(
        f"[cyan]편향 분석[/cyan] · 최근 {days}일 · 총 {summary['total']}건 "
        "(점수 동반)\n"
    )

    side_table = Table(title="매수 vs 매도 (진입 점수)")
    side_table.add_column("방향", style="cyan")
    side_table.add_column("건수", justify="right")
    side_table.add_column("평균 점수", justify="right")
    side_table.add_column("70+ 비율", justify="right")
    side_table.add_column("30- 비율", justify="right")
    for side in ("buy", "sell"):
        stat = summary["by_side"][side]
        avg = stat["avg_score"]
        avg_str = f"{avg:.1f}" if avg is not None else "—"
        side_table.add_row(
            side.upper(),
            str(stat["count"]),
            avg_str,
            f"{stat['high_zone_pct']:.1f}%",
            f"{stat['low_zone_pct']:.1f}%",
        )
    console.print(side_table)

    if summary["by_tag"]:
        tag_table = Table(title="태그별 평균 진입 점수")
        tag_table.add_column("태그", style="magenta")
        tag_table.add_column("건수", justify="right")
        tag_table.add_column("평균 점수", justify="right")
        for tag, stat in sorted(summary["by_tag"].items()):
            tag_table.add_row(tag, str(stat["count"]), f"{stat['avg_score']:.1f}")
        console.print(tag_table)

    console.print(
        "[dim]힌트: 매수 평균 점수 > 70 → FOMO 추격 경향, "
        "< 50 → 역추세 저점 매수 경향. 본인 전략과 의도된 방향인지 검토.[/dim]"
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


def _resolve_task(task: str, hour: int) -> str:
    """`auto` 입력을 KST 시각 기반 us/kr/daily/all로 변환."""
    t = task.lower()
    if t in ("us", "kr", "daily", "all"):
        return t
    if t != "auto":
        raise typer.BadParameter(f"--task는 auto/us/kr/daily/all 중 하나: {task!r}")
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

    if task in ("us", "kr", "all"):
        forced: Market | None = (
            "US" if task == "us" else "KR" if task == "kr" else None
        )
        targets = _resolve_targets(
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
            results = _run_mixed(
                engine,
                [t for t, _ in targets],
                market_for,
                progress,
                persist=not dry_run,
            )
        if len(results) < len(targets):
            exit_code = 1  # 일부 실패
        if results:
            render_score_ranking(results, console=console)

    # alert는 모든 task에서 실행 (daily 트리거가 자체 dedup)
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
        # 07:00 KST 또는 manual — 오늘자 Craft 노트 생성
        with get_db_connection() as conn:
            from stock_compass.db import get_scores_on_date

            scores = get_scores_on_date(conn, today_kst())
        if scores and not dry_run:
            path = CraftExporter().export(scores, today_kst())
            console.print(f"[green]✓[/green] Craft 노트: [cyan]{path}[/cyan]")
        elif not scores:
            console.print("[yellow]오늘 스냅샷 없음 — Craft 노트 생략[/yellow]")

    return exit_code


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
