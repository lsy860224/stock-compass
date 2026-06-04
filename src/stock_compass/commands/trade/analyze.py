"""trade analyze — 본인 매매 편향 리포트 (진입 점수 분포 + 태그별 평균)."""

from __future__ import annotations

from typing import Annotated

import typer

from stock_compass.commands._app import console, trade_app


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
