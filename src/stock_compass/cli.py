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
from typing import TYPE_CHECKING, Annotated, Any, get_args

import typer
from rich.console import Console

from stock_compass.markets.base import Market

if TYPE_CHECKING:
    from rich.progress import Progress

    from stock_compass.scoring import CompositeScore, ScoringEngine
    from stock_compass.screener import PresetInfo

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
universe_app = typer.Typer(help="유니버스 — refresh / list.")
app.add_typer(universe_app, name="universe")
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
        try:
            publisher = CraftPublisher()
            result = publisher.publish_daily_note(content, on_date)
            action = "갱신" if result.is_update else "발행"
            console.print(
                f"[green]✓ Craft {action}:[/green] [cyan]{result.url}[/cyan]"
                f" (note_id={result.note_id})"
            )
        except CraftAuthError as e:
            console.print(f"[yellow]Craft 인증 실패: {e}[/yellow]")
        except CraftAPIError as e:
            console.print(f"[red]Craft API 오류: {e}[/red]")


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


@app.command()
def screen(
    preset: Annotated[
        str | None, typer.Option("--preset", help="screeners/presets/<name>.sql")
    ] = None,
    file: Annotated[
        Path | None, typer.Option("--file", "-f", help="저장된 .sql 파일")
    ] = None,
    sql: Annotated[
        str | None, typer.Option("--sql", help="인라인 SQL")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", "-l", help="결과 행 수 (기본 50, 최대 5000)")
    ] = None,
    budget: Annotated[
        float | None,
        typer.Option(
            "--budget",
            help="1주 가격 ≤ 예산 (시장 통화 — KRW 또는 USD). preset에 price 컬럼 필요",
        ),
    ] = None,
    budget_market: Annotated[
        str | None,
        typer.Option(
            "--budget-market",
            help="budget을 적용할 시장 (kr/us). 미지정 시 모든 시장",
        ),
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", help="table / csv / json")
    ] = "table",
    out: Annotated[
        Path | None, typer.Option("--out", help="csv/json은 파일로 저장 (미지정 시 stdout)")
    ] = None,
    list_presets_flag: Annotated[
        bool, typer.Option("--list-presets", help="사용 가능한 프리셋 목록")
    ] = False,
    list_fields_flag: Annotated[
        bool, typer.Option("--list-fields", help="v_latest_scores 칼럼 치트시트")
    ] = False,
) -> None:
    """SQL 스크리너 — RO 모드 + LIMIT 강제 + 안전 검증 + 예산 필터."""
    from stock_compass.config import settings
    from stock_compass.output.screener import (
        export_to_file,
        render_csv,
        render_json,
        render_table,
    )
    from stock_compass.screener import (
        PresetNotFoundError,
        ScreenerEngine,
        ScreenerError,
        list_presets,
        load_preset,
    )
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    if list_presets_flag:
        _print_preset_catalog(list_presets())
        return
    if list_fields_flag:
        _print_field_cheatsheet()
        return

    # 진입점 우선순위: preset > file > sql
    if preset:
        try:
            sql_text = load_preset(preset)
            preset_name = preset
        except PresetNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=2) from e
    elif file:
        if not file.exists():
            console.print(f"[red]파일 없음: {file}[/red]")
            raise typer.Exit(code=2)
        sql_text = file.read_text(encoding="utf-8")
        preset_name = None
    elif sql:
        sql_text = sql
        preset_name = None
    else:
        console.print(
            "[red]--preset / --file / --sql 중 하나는 필수입니다.[/red]"
            "\n[dim]힌트: `stock-compass screen --list-presets`[/dim]"
        )
        raise typer.Exit(code=2)

    bm_norm = _parse_market(budget_market)
    try:
        engine = ScreenerEngine()
        result = engine.run_sql(
            sql_text,
            limit=limit,
            preset_name=preset_name,
            budget=budget,
            budget_market=bm_norm,
        )
    except ScreenerError as e:
        console.print(f"[red]스크리너 거부: {e}[/red]")
        raise typer.Exit(code=1) from e

    fmt = output_format.lower()
    if fmt == "table":
        render_table(result, console=console)
    elif fmt == "csv":
        body = render_csv(result)
        if out:
            export_to_file(body, out)
            console.print(f"[green]✓[/green] CSV 저장: {out}")
        else:
            console.print(body)
    elif fmt == "json":
        body = render_json(result)
        if out:
            export_to_file(body, out)
            console.print(f"[green]✓[/green] JSON 저장: {out}")
        else:
            console.print(body)
    else:
        console.print(f"[red]지원하지 않는 format: {fmt} (table/csv/json만)[/red]")
        raise typer.Exit(code=2)


@app.command()
def discover(
    preset: Annotated[str, typer.Option("--preset", help="screeners/presets/<name>.sql")],
    budget: Annotated[
        float | None,
        typer.Option("--budget", help="1주 가격 ≤ 예산 (시장 통화)"),
    ] = None,
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us (budget 시장 분리)")
    ] = None,
    refresh_universe: Annotated[
        str | None,
        typer.Option(
            "--refresh",
            help="discover 전 universe 갱신 (KOSPI_200/KOSDAQ_150/SP500/...)",
        ),
    ] = None,
    score_limit: Annotated[
        int,
        typer.Option(
            "--score-limit", help="screener 결과 중 상위 N개만 점수화 (기본 20)"
        ),
    ] = 20,
    no_persist: Annotated[
        bool, typer.Option("--no-persist", help="batch 결과 DB 저장 생략")
    ] = False,
    publish_craft: Annotated[
        bool,
        typer.Option("--publish-craft", help="결과를 Craft API로 즉시 발행 (Phase D)"),
    ] = False,
) -> None:
    """동적 발굴 파이프라인 — universe → screener → batch → 노트.

    워치리스트(.env) 대신 매일 새 후보를 발굴해 점수화. 예산 필터로 1주 가격
    제한 가능. `--publish-craft`로 Craft Pro API 자동 발행.
    """
    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from stock_compass.config import settings
    from stock_compass.db import get_db_connection
    from stock_compass.output.terminal import render_score_ranking
    from stock_compass.scoring import ScoringEngine
    from stock_compass.screener import (
        PresetNotFoundError,
        ScreenerEngine,
        ScreenerError,
        load_preset,
    )
    from stock_compass.screener.universes import (
        SUPPORTED_UNIVERSES,
        UniverseFetchError,
    )
    from stock_compass.screener.universes import refresh as universe_refresh_fn
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    market_norm = _parse_market(market)

    # 1) universe 갱신 (선택)
    if refresh_universe:
        target = refresh_universe.upper()
        if target not in SUPPORTED_UNIVERSES:
            console.print(
                f"[red]지원하지 않는 universe: {target}[/red]\n"
                f"[dim]지원: {', '.join(SUPPORTED_UNIVERSES)}[/dim]"
            )
            raise typer.Exit(code=2)
        try:
            with get_db_connection() as conn:
                r = universe_refresh_fn(conn, target)
            console.print(
                f"[cyan]universe[/cyan] {r.universe_code}: +{r.members} 신규"
            )
        except UniverseFetchError as e:
            console.print(f"[yellow]{e} (기존 멤버 사용)[/yellow]")

    # 2) screener 실행
    try:
        sql_text = load_preset(preset)
    except PresetNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    try:
        engine = ScreenerEngine()
        result = engine.run_sql(
            sql_text,
            limit=score_limit,
            preset_name=f"discover:{preset}",
            budget=budget,
            budget_market=market_norm,
        )
    except ScreenerError as e:
        console.print(f"[red]스크리너 거부: {e}[/red]")
        raise typer.Exit(code=1) from e

    if not result.rows:
        console.print(
            f"[yellow]preset={preset} 결과 0건 — 조건 완화 또는 batch 사전 실행 필요.[/yellow]"
        )
        return

    # 3) screener row → (ticker, market) 추출 → batch
    targets = _screener_rows_to_targets(result.rows, default_market=market_norm)
    if not targets:
        console.print(
            "[red]screener 결과에서 ticker/market 컬럼을 찾을 수 없음.[/red]\n"
            "[dim]preset SQL이 `code` (또는 `ticker`) + `market` 컬럼을 노출해야 함.[/dim]"
        )
        raise typer.Exit(code=1)

    console.print(
        f"[cyan]후보 {len(targets)}종목[/cyan] 점수화 시작 "
        f"(KR={sum(1 for _, m in targets if m == 'KR')}, "
        f"US={sum(1 for _, m in targets if m == 'US')})"
    )

    scoring_engine = ScoringEngine()
    market_for: dict[str, Market] = dict(targets)
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        scores = _run_mixed(
            scoring_engine,
            [t for t, _ in targets],
            market_for,
            progress,
            persist=not no_persist,
        )

    render_score_ranking(scores, console=console)

    # 4) Craft 발행 (선택)
    if publish_craft:
        _publish_discover_to_craft(scores, preset_name=preset)


def _screener_rows_to_targets(
    rows: list[dict[str, Any]],
    *,
    default_market: Market | None,
) -> list[tuple[str, Market]]:
    """screener row → (ticker, market) 튜플. code/ticker + market 컬럼 자동 탐지."""
    out: list[tuple[str, Market]] = []
    for row in rows:
        ticker = row.get("code") or row.get("ticker")
        if not ticker:
            continue
        row_market = row.get("market") or default_market
        if not row_market or row_market not in ("KR", "US"):
            continue
        out.append((str(ticker), row_market))
    return out


def _publish_discover_to_craft(
    scores: list[CompositeScore], *, preset_name: str
) -> None:
    """Phase D — Craft API 발행. 토큰 없으면 친절한 안내."""
    from stock_compass.config import settings

    if settings.craft_api_token is None:
        console.print(
            "[yellow]--publish-craft 무시: CRAFT_API_TOKEN 미설정 "
            "(.env.local에 추가 후 재시도)[/yellow]"
        )
        return

    try:
        from stock_compass.output.craft import CraftPublisher
    except ImportError:
        console.print(
            "[yellow]CraftPublisher 미구현 (Phase D 후속) — 파일 fallback 사용[/yellow]"
        )
        return

    publisher = CraftPublisher()
    from stock_compass.output.craft import CraftExporter
    from stock_compass.utils.dates import today_kst

    on_date = today_kst()
    content = CraftExporter().render_daily_note(scores, on_date)
    try:
        result = publisher.publish_daily_note(
            content, on_date, note_kind=f"discover:{preset_name}"
        )
        console.print(f"[green]✓ Craft 발행:[/green] {result.url}")
    except Exception as e:
        console.print(f"[red]Craft 발행 실패: {e}[/red]")


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


def _print_preset_catalog(presets: list[PresetInfo]) -> None:
    from rich.table import Table

    if not presets:
        console.print(
            "[yellow]프리셋 없음 — screeners/presets/*.sql 확인.[/yellow]"
        )
        return
    table = Table(title=f"사용 가능한 프리셋 ({len(presets)}개)")
    table.add_column("이름", style="cyan")
    table.add_column("설명", overflow="fold")
    for p in presets:
        table.add_row(p.name, p.description)
    console.print(table)
    console.print("[dim]사용: stock-compass screen --preset <name>[/dim]")


def _print_field_cheatsheet() -> None:
    from rich.table import Table

    sections: list[tuple[str, list[tuple[str, str]]]] = [
        (
            "Valuation",
            [
                ("per", "PER (배)"),
                ("pbr", "PBR (배)"),
                ("peg", "PEG (배)"),
                ("dividend_yield", "배당수익률 (0.03 = 3%)"),
                ("market_cap", "시가총액 (현지 통화)"),
                ("valuation_score", "Valuation 팩터 점수 0~100"),
            ],
        ),
        (
            "Fundamentals",
            [
                ("revenue_growth_yoy", "매출 성장률 YoY (0.10 = 10%)"),
                ("operating_margin", "영업이익률"),
                ("roe", "ROE (0.15 = 15%)"),
                ("fundamentals_score", "Fundamentals 점수 0~100"),
            ],
        ),
        (
            "Technical",
            [
                ("rsi_14", "RSI(14)"),
                ("ma200_distance", "200MA 이격률 (+0.05 = 5% 위)"),
                ("volume_zscore", "20일 거래량 z-score"),
                ("technical_score", "Technical 점수 0~100"),
            ],
        ),
        (
            "메타",
            [
                ("code", "종목 코드"),
                ("name", "종목명"),
                ("market", "KR / US"),
                ("sector", "섹터"),
                ("universes", "지수 멤버십 (CSV 문자열)"),
                ("composite_score", "5팩터 가중평균 0~100"),
                ("verdict", "관심권/중립/주의"),
                ("price", "최신 종가"),
                ("as_of_date", "데이터 기준일"),
            ],
        ),
    ]

    for title, items in sections:
        table = Table(title=f"[{title}]")
        table.add_column("필드", style="cyan")
        table.add_column("설명", overflow="fold")
        for name, desc in items:
            table.add_row(name, desc)
        console.print(table)


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
