"""trade 서브그룹 — add / list / analyze."""

from __future__ import annotations

from typing import Annotated

import typer

from stock_compass.commands._app import console, trade_app
from stock_compass.commands._helpers import parse_market


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
    market_norm = parse_market(market) or detect_market(ticker)

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
    market_norm = parse_market(market)

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


@trade_app.command("hindsight")
def trade_hindsight(
    days: Annotated[
        int, typer.Option("--days", "-d", help="lookback 매매 기간 (기본 365)")
    ] = 365,
    forward: Annotated[
        str,
        typer.Option(
            "--forward",
            help="forward 검증 일수 쉼표 구분 (기본 30,90 — 매매 후 N일 가격)",
        ),
    ] = "30,90",
    ticker: Annotated[
        str | None,
        typer.Option("--ticker", "-t", help="특정 종목만 hindsight"),
    ] = None,
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us")
    ] = None,
) -> None:
    """매매 후 N일 가격 변화로 사후 검증 — 진입 점수 대비 실제 성과."""
    from rich.panel import Panel
    from rich.table import Table

    from stock_compass.config import settings
    from stock_compass.db import (
        get_db_connection,
        get_trade_hindsight,
        summarize_hindsight,
    )
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)
    market_norm = parse_market(market)

    try:
        forward_days = tuple(int(x.strip()) for x in forward.split(",") if x.strip())
    except ValueError as e:
        console.print(f"[red]--forward 형식 오류: {forward!r}[/red]")
        raise typer.Exit(code=2) from e
    if not forward_days:
        console.print("[red]--forward 비어 있음[/red]")
        raise typer.Exit(code=2)

    with get_db_connection() as conn:
        rows = get_trade_hindsight(
            conn,
            days=days,
            forward_days=forward_days,
            ticker=ticker,
            market=market_norm,
        )

    if not rows:
        console.print(
            f"[yellow]최근 {days}일 매매 없음 — `trade add` 사전 실행 필요.[/yellow]"
        )
        return

    summary = summarize_hindsight(rows, forward_days=forward_days)
    console.print(
        f"[cyan]Hindsight[/cyan] · 최근 {days}일 매매 {summary['total']}건 · "
        f"forward {','.join(summary['forward_keys'])} (composite_scores 기반)"
    )

    # 1) 개별 매매 (상위 20건만)
    if len(rows) <= 20:
        _render_hindsight_table(rows, forward_days)
    else:
        console.print(
            f"[dim]개별 매매 표시 생략 (총 {len(rows)}건 > 20). "
            "--ticker 로 좁히세요.[/dim]"
        )

    # 2) 방향별 통계
    side_tbl = Table(title="방향별 forward return (매수 + 좋음 / 매도 - 좋음)")
    side_tbl.add_column("side", style="cyan")
    side_tbl.add_column("기간", justify="center")
    side_tbl.add_column("건수", justify="right")
    side_tbl.add_column("평균", justify="right")
    side_tbl.add_column("중앙값", justify="right")
    side_tbl.add_column("적중률", justify="right")
    for side in ("buy", "sell"):
        for k in summary["forward_keys"]:
            stat = summary["by_side"][side][k]
            side_tbl.add_row(
                side.upper(),
                k,
                str(stat["count"]),
                _fmt_pct_value(stat["avg"]),
                _fmt_pct_value(stat["median"]),
                f"{stat['hit_rate'] * 100:.1f}%" if stat["hit_rate"] is not None else "—",
            )
    console.print(side_tbl)

    # 3) 진입 점수 zone별 (매수만)
    zone_tbl = Table(title="진입 점수 zone별 매수 forward return")
    zone_tbl.add_column("zone", style="cyan")
    zone_tbl.add_column("기간", justify="center")
    zone_tbl.add_column("건수", justify="right")
    zone_tbl.add_column("평균", justify="right")
    zone_tbl.add_column("적중률", justify="right")
    zone_labels = {
        "high_70_plus": "관심권(≥70)",
        "mid_50_70": "중립(50~69)",
        "low_below_50": "주의(<50)",
        "no_score": "점수 없음",
    }
    for zone, label in zone_labels.items():
        for k in summary["forward_keys"]:
            stat = summary["by_zone"][zone][k]
            if stat["count"] == 0:
                continue
            zone_tbl.add_row(
                label,
                k,
                str(stat["count"]),
                _fmt_pct_value(stat["avg"]),
                f"{stat['hit_rate'] * 100:.1f}%" if stat["hit_rate"] is not None else "—",
            )
    console.print(zone_tbl)

    console.print(
        Panel(
            "Hindsight는 forward 가격 데이터(composite_scores.price_at_score) "
            "있을 때만 계산. 거래비용·세금·배당 미반영. **매매 판단 자체의 "
            "사후 검증** — 자가 학습용 보조 자료, 매매 권유 아님.",
            title="면책 / 한계",
            border_style="dim",
            padding=(0, 1),
        )
    )


def _render_hindsight_table(
    rows: list,  # type: ignore[type-arg]
    forward_days: tuple[int, ...],
) -> None:
    from rich.table import Table

    table = Table(title=f"개별 매매 hindsight ({len(rows)}건)")
    table.add_column("일시", style="cyan", no_wrap=True)
    table.add_column("종목", style="cyan")
    table.add_column("side", justify="center")
    table.add_column("진입가", justify="right")
    table.add_column("점수", justify="right")
    for d in forward_days:
        table.add_column(f"+{d}d", justify="right")
    for r in rows:
        t = r.trade
        side_color = "green" if t.side == "buy" else "red"
        fwd_cells: list[str] = []
        for d in forward_days:
            v = r.forward_returns.get(f"{d}d")
            if v is None:
                fwd_cells.append("[dim]—[/dim]")
                continue
            # 매수면 + 좋음, 매도면 - 좋음
            good = (v > 0) if t.side == "buy" else (v < 0)
            color = "green" if good else "red"
            sign = "+" if v >= 0 else ""
            fwd_cells.append(f"[{color}]{sign}{v * 100:.1f}%[/{color}]")
        table.add_row(
            t.executed_at.split("T")[0],
            f"{t.ticker} [{t.market}]",
            f"[{side_color}]{t.side.upper()}[/{side_color}]",
            f"{t.price:,.2f}",
            f"{t.score_at_trade:.1f}" if t.score_at_trade is not None else "—",
            *fwd_cells,
        )
    console.print(table)


def _fmt_pct_value(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    color = "green" if v >= 0 else "red"
    return f"[{color}]{sign}{v * 100:.1f}%[/{color}]"
