"""주간 유니버스 재채점 — weekly-discover(토 08:00) 전 v_latest_scores 신선화.

점수만 갱신(알림·Craft·백업 생략) — 853종목 알림 폭주 방지. 관심권 신규 진입만 보고.
"""

from __future__ import annotations

from datetime import date as date_cls

from stock_compass.commands._app import console
from stock_compass.commands._helpers import resolve_universe_targets, run_mixed
from stock_compass.commands.scoring.reporting import publish_via_sinks

# 주간 재채점 대상 유니버스 (KR 먼저 → US 나중: US 금요일 종가 신선도 확보).
_WEEKLY_RESCORE_UNIVERSES = "ALL_KR,SP500"
# 853종목 풀 API sentiment 1회분(in+out 합산 ~1.5M)을 수용하도록 이 실행에 한해
# 일일 한도 상향 — settings 객체만 변경(프로세스 스코프), plist env·.env 불변.
_WEEKLY_RESCORE_INPUT_LIMIT = 20_000_000


def run_universe_rescore(*, dry_run: bool) -> int:
    """유니버스(ALL_KR+SP500) 전체 재채점 → composite_scores 신선화. 0=전건 성공."""
    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from stock_compass.config import settings
    from stock_compass.scoring import ScoringEngine
    from stock_compass.utils.dates import today_kst

    targets = resolve_universe_targets(_WEEKLY_RESCORE_UNIVERSES)
    if not targets:
        console.print(
            "[yellow]유니버스 멤버 없음 — `universe refresh` 먼저.[/yellow]"
        )
        return 1

    if dry_run:
        kr = sum(1 for _, m in targets if m == "KR")
        console.print(
            f"[dim]dry-run: 유니버스 재채점 {len(targets)}종목 "
            f"(KR={kr}, US={len(targets) - kr}), 한도={_WEEKLY_RESCORE_INPUT_LIMIT:,}[/dim]"
        )
        return 0

    # #1 멤버십 선갱신 — 재채점이 최신 편입/퇴출을 반영하도록 (실패 격리).
    _refresh_universe_membership()
    targets = resolve_universe_targets(_WEEKLY_RESCORE_UNIVERSES)

    # 풀 API sentiment 보장 — 기본 500k 한도면 ~120종목 후 fallback(50)으로 끊김.
    settings.anthropic_daily_input_limit = max(
        settings.anthropic_daily_input_limit, _WEEKLY_RESCORE_INPUT_LIMIT
    )

    engine = ScoringEngine()
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        results = run_mixed(
            engine,
            [t for t, _ in targets],
            dict(targets),
            progress,
            persist=True,
        )

    on_date = today_kst()
    console.print(
        f"[green]✓[/green] 유니버스 재채점 {len(results)}/{len(targets)}종목 "
        f"(date={on_date.isoformat()})"
    )
    if results:
        from stock_compass.output.report_render import render_rescore_summary

        iso_year, iso_week, _ = on_date.isocalendar()
        publish_via_sinks(
            render_rescore_summary(results, on_date),
            on_date=on_date,
            kind="weekly-rescore",
            title=f"stock-compass · weekly-rescore · {on_date.isoformat()}",
            filename=f"{iso_year}-W{iso_week:02d} 재채점",
        )
        # #3 관심권(≥threshold) 신규 진입 종목 → 노트 + macOS 알림
        _report_universe_entrants(results, on_date)
    return 0 if len(results) == len(targets) else 1


def _refresh_universe_membership() -> None:
    """주간 재채점 전 유니버스 멤버십 갱신 (ALL_KR·SP500). 실패는 격리 (기존 정전 유지)."""
    from stock_compass.db import get_db_connection
    from stock_compass.screener.universes import UniverseFetchError, refresh

    with get_db_connection() as conn:
        for code in ("ALL_KR", "SP500"):
            try:
                r = refresh(conn, code)
                console.print(
                    f"[dim]universe {r.universe_code}: +{r.members} "
                    f"(as_of={r.as_of_date})[/dim]"
                )
            except (UniverseFetchError, ValueError) as e:
                console.print(f"[yellow]universe {code} 갱신 실패: {e}[/yellow]")


def _report_universe_entrants(
    results: list,  # type: ignore[type-arg]
    on_date: date_cls,
) -> None:
    """직전 대비 관심권(≥alert_threshold_buy) 신규 진입 종목 보고 + macOS 알림."""
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection, get_previous_total_scores
    from stock_compass.output.notify import macos_notify
    from stock_compass.output.report_render import render_universe_entrants

    thr = settings.alert_threshold_buy
    high = [s for s in results if s.total_score >= thr]
    if not high:
        return
    with get_db_connection() as conn:
        prev = get_previous_total_scores(
            conn, [(s.ticker, s.market) for s in high], before_date=on_date
        )
    entrants = [
        s
        for s in high
        if (p := prev.get((s.ticker, s.market))) is None or p[0] < thr
    ]
    if not entrants:
        return
    entrants.sort(key=lambda x: x.total_score, reverse=True)

    publish_via_sinks(
        render_universe_entrants(entrants, on_date, threshold=thr),
        on_date=on_date,
        kind="universe-entry",
        title=f"stock-compass · 관심권 신규진입 · {on_date.isoformat()}",
        filename=f"{on_date.isoformat()} 관심권 진입",
    )
    top = ", ".join(f"{s.ticker}({s.total_score:.0f})" for s in entrants[:5])
    macos_notify(
        title=f"📈 유니버스 관심권 신규진입 {len(entrants)}종목",
        body=f"≥{thr}점 진입: {top}" + (" 외" if len(entrants) > 5 else ""),
        subtitle="주간 재채점",
    )
