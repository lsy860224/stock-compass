"""discover — 동적 발굴 파이프라인 (universe → screener → batch → 노트)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from stock_compass.commands._app import app, console
from stock_compass.commands._helpers import parse_market, run_mixed
from stock_compass.commands.screener._common import screener_rows_to_targets
from stock_compass.markets.base import Market

if TYPE_CHECKING:
    from stock_compass.scoring import CompositeScore


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
    market_norm = parse_market(market)

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

    targets = screener_rows_to_targets(result.rows, default_market=market_norm)
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
        scores = run_mixed(
            scoring_engine,
            [t for t, _ in targets],
            market_for,
            progress,
            persist=not no_persist,
        )

    render_score_ranking(scores, console=console)

    if publish_craft:
        _publish_discover_to_craft(scores, preset_name=preset)


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

    from stock_compass.output.craft import CraftExporter, CraftPublisher
    from stock_compass.utils.dates import today_kst

    publisher = CraftPublisher()
    on_date = today_kst()
    content = CraftExporter().render_daily_note(scores, on_date)
    try:
        result = publisher.publish_daily_note(
            content, on_date, note_kind=f"discover:{preset_name}"
        )
        console.print(f"[green]✓ Craft 발행:[/green] {result.url}")
    except Exception as e:
        console.print(f"[red]Craft 발행 실패: {e}[/red]")
