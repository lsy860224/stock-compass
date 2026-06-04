"""trade hindsight — 매매 후 N일 가격 변화로 진입 판단 사후 검증."""

from __future__ import annotations

from typing import Annotated

import typer

from stock_compass.commands._app import console, trade_app
from stock_compass.commands._helpers import parse_market


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
