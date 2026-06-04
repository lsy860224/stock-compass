"""점수화 결과 후처리 — 보고 발행(dual-sink)·헬스체크·차트·직전 점수 조회.

scheduler·rescore·batch 명령이 공유하는 순수 후처리 헬퍼 모음.
"""

from __future__ import annotations

from datetime import date as date_cls

from stock_compass.commands._app import console
from stock_compass.markets.base import Market
from stock_compass.output.craft_exporter import PreviousScores


def publish_via_sinks(
    content: str,
    *,
    on_date: date_cls,
    kind: str,
    title: str,
    filename: str,
    charts: list[tuple[str, bytes]] | None = None,
) -> None:
    """publish_report(Obsidian + Craft) 호출 + 결과 콘솔 출력 — 자동화 공용."""
    from stock_compass.output.report import publish_report

    result = publish_report(
        content,
        on_date=on_date,
        kind=kind,
        title=title,
        filename=filename,
        charts=charts,
    )
    if result.obsidian_path is not None:
        console.print(
            f"[green]✓[/green] Obsidian: [cyan]{result.obsidian_path.name}[/cyan]"
        )
    if result.craft_url:
        console.print(f"[green]✓[/green] Craft: [cyan]{result.craft_url}[/cyan]")
    elif result.craft_skipped:
        console.print(f"[dim]Craft 발행 skip ({result.craft_skipped})[/dim]")
    if not result.any_delivered:
        console.print(f"[yellow]보고 sink 전부 미발행 ({kind})[/yellow]")


def publish_batch_reports(
    results: list,  # type: ignore[type-arg]
    on_date: date_cls,
) -> None:
    """장 마감 배치 결과를 시장별로 분리해 보고 발행 (batch-us / batch-kr)."""
    from collections import defaultdict

    from stock_compass.output.report_render import render_batch_note

    groups: dict[Market, list] = defaultdict(list)  # type: ignore[type-arg]
    for s in results:
        groups[s.market].append(s)
    for market, market_scores in groups.items():
        content = render_batch_note(market_scores, market, on_date)
        publish_via_sinks(
            content,
            on_date=on_date,
            kind=f"batch-{market.lower()}",
            title=f"stock-compass · batch {market} · {on_date.isoformat()}",
            filename=f"{on_date.isoformat()} {market}",
        )


def publish_alerts_report(
    fired: list,  # type: ignore[type-arg]
    on_date: date_cls,
) -> None:
    """발화된 알림 요약 보고 발행."""
    from stock_compass.output.report_render import render_alerts_note

    content = render_alerts_note(fired, on_date)
    publish_via_sinks(
        content,
        on_date=on_date,
        kind="alerts",
        title=f"stock-compass · alerts · {on_date.isoformat()}",
        filename=f"{on_date.isoformat()} 알림",
    )


def batch_health_check(
    task: str, succeeded: int, total: int, *, dry_run: bool
) -> None:
    """배치 실패율이 임계 초과면 macOS 알림 — 데이터 소스 장애 조기 감지."""
    from stock_compass.config import settings

    if total == 0:
        return
    failed = total - succeeded
    ratio = failed / total
    console.print(
        f"[yellow]배치 실패 {failed}/{total} ({ratio:.0%})[/yellow]"
    )
    if dry_run or ratio < settings.batch_failure_alert_ratio:
        return
    from stock_compass.output.notify import macos_notify

    macos_notify(
        title="⚠️ stock-compass 배치 경고",
        body=f"{task} 배치 {failed}/{total} 종목 실패 ({ratio:.0%}) — 데이터 소스 점검 필요",
        subtitle="헬스체크",
    )


def daily_charts(
    scores: list,  # type: ignore[type-arg]
) -> list[tuple[str, bytes]]:
    """워치리스트 종목별 30일 점수추이 차트 (caption, png). 데이터 부족·실패는 skip."""
    from stock_compass.db import get_db_connection, get_score_history
    from stock_compass.output.chart import render_score_history_chart

    charts: list[tuple[str, bytes]] = []
    with get_db_connection() as conn:
        for s in scores:
            try:
                hist = get_score_history(conn, s.ticker, s.market, days=30)
                png = render_score_history_chart(hist, ticker=s.ticker, name=s.name)
            except Exception:
                png = None
            if png is not None:
                charts.append((f"{s.ticker} {s.name or ''}".strip(), png))
    return charts


def previous_scores_for(
    results: list,  # type: ignore[type-arg]
) -> PreviousScores:
    """batch 결과 종목들의 직전 (어제 또는 이전) total_score + verdict 조회.

    DB 미등록·이력 없는 종목은 결과 dict에 미포함 → render에서 '—' 표시.
    """
    if not results:
        return {}
    from stock_compass.db import get_db_connection, get_previous_total_scores
    from stock_compass.utils.dates import today_kst

    code_markets = [(r.ticker, r.market) for r in results]
    on_date = today_kst()
    with get_db_connection() as conn:
        return get_previous_total_scores(conn, code_markets, before_date=on_date)
