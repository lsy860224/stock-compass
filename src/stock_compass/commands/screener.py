"""screen / discover / weekly-discover / backtest — SQL 스크리너 + 발굴 + 백테스트."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from stock_compass.commands._app import app, console
from stock_compass.commands._helpers import parse_market, run_mixed
from stock_compass.markets.base import Market

if TYPE_CHECKING:
    from stock_compass.scoring import CompositeScore
    from stock_compass.screener import PresetInfo
    from stock_compass.screener.backtest import BacktestResult


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
    add_to_watchlist: Annotated[
        bool,
        typer.Option(
            "--add-to-watchlist",
            help="결과 종목을 WATCHLIST_<GROUP> universe에 등록 (--group 으로 그룹 지정)",
        ),
    ] = False,
    watchlist_group: Annotated[
        str,
        typer.Option(
            "--group",
            help="--add-to-watchlist 그룹 이름 (영숫자+언더바, 기본 'screening')",
        ),
    ] = "screening",
    prompt_deepdive: Annotated[
        bool,
        typer.Option(
            "--prompt-deepdive",
            help="결과 종목 sentiment 심층 분석 prompt MD 생성 (Claude.ai 복붙용)",
        ),
    ] = False,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive",
            help="인터랙티브 SQL REPL (Phase 7-8 미구현 — 친절 안내만)",
        ),
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

    if interactive:
        console.print(
            "[yellow]--interactive REPL은 Phase 7-8 (선택) 미구현입니다.[/yellow]\n"
            "[dim]대안:[/dim]\n"
            "  · [cyan]stock-compass screen --preset <name>[/cyan] — 프리셋 실행\n"
            "  · [cyan]stock-compass screen --file path.sql[/cyan] — 저장된 SQL\n"
            "  · [cyan]stock-compass screen --sql \"...\"[/cyan] — 인라인 SQL\n"
            "  · [cyan]stock-compass screen --list-fields[/cyan] — 칼럼 치트시트"
        )
        raise typer.Exit(code=2)
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

    bm_norm = parse_market(budget_market)
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

    if add_to_watchlist:
        _add_screener_to_watchlist(result.rows, group=watchlist_group)
    if prompt_deepdive:
        _generate_deepdive_prompt(result.rows, preset_name=preset_name)


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


@app.command("weekly-discover")
def weekly_discover(
    publish_craft: Annotated[
        bool, typer.Option("--publish-craft", help="결과를 Craft에 통합 노트로 발행")
    ] = True,
    presets: Annotated[
        str,
        typer.Option(
            "--presets",
            help=(
                "실행할 preset 쉼표 구분 "
                "(기본: value_growth_kr,momentum_us,oversold_quality_global)"
            ),
        ),
    ] = "value_growth_kr,momentum_us,oversold_quality_global",
    refresh_universe: Annotated[
        bool,
        typer.Option(
            "--refresh-universe/--no-refresh-universe",
            help="실행 전 KOSPI_200 + SP500 universe 갱신",
        ),
    ] = True,
    limit_per_preset: Annotated[
        int, typer.Option("--limit", help="preset당 결과 행 수 (기본 15)")
    ] = 15,
) -> None:
    """주간 종목 발굴 — 3개 preset 실행 + 통합 Craft 노트.

    토요일 아침 launchd가 호출하는 일괄 발굴 흐름. 각 preset 결과는
    한 Craft 노트의 별도 섹션으로 묶임.
    """
    from stock_compass.config import settings
    from stock_compass.db import get_db_connection
    from stock_compass.output.craft import (
        CraftAPIError,
        CraftAuthError,
        CraftPublisher,
    )
    from stock_compass.screener import (
        PresetNotFoundError,
        ScreenerEngine,
        ScreenerError,
        list_presets,
        load_preset,
    )
    from stock_compass.screener.universes import (
        UniverseFetchError,
        refresh,
    )
    from stock_compass.utils.dates import today_kst
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    requested_names = [p.strip() for p in presets.split(",") if p.strip()]
    available = {p.name for p in list_presets()}
    unknown = [n for n in requested_names if n not in available]
    if unknown:
        console.print(f"[red]존재하지 않는 preset: {unknown}[/red]")
        raise typer.Exit(code=2)

    if refresh_universe:
        with get_db_connection() as conn:
            for code in ("KOSPI_200", "SP500"):
                try:
                    r = refresh(conn, code)
                    console.print(
                        f"[dim]universe {r.universe_code}: +{r.members} 신규[/dim]"
                    )
                except UniverseFetchError as e:
                    console.print(f"[yellow]universe {code} 갱신 실패: {e}[/yellow]")

    engine = ScreenerEngine()
    sections: list[str] = [
        f"# 주간 발굴 · {today_kst().isoformat()} (토요일 KST)",
        "",
        "> 매주 자동 발굴된 후보 종목 — **매수 권유 아님.** 본인 추가 조사 필수.",
    ]
    total_found = 0
    for name in requested_names:
        try:
            sql = load_preset(name)
            result = engine.run_sql(
                sql,
                limit=limit_per_preset,
                preset_name=f"weekly:{name}",
            )
        except (PresetNotFoundError, ScreenerError) as e:
            console.print(f"[red]preset {name} 실패: {e}[/red]")
            sections.append(f"\n## ❌ {name}\n\n실행 실패: {e}")
            continue

        total_found += result.row_count
        sections.append(f"\n## 🔍 {name} — {result.row_count}건")

        # 모델 신뢰도 검증 — 지난 1년 backtest 적중률 + 평균 수익률 한 줄
        bt_line = _preset_backtest_line(sql, name)
        if bt_line:
            sections.append(bt_line)

        if not result.rows:
            sections.append("\n조건 충족 종목 없음.")
            continue
        cols = result.columns[:6]
        sections.append("\n| " + " | ".join(cols) + " |")
        sections.append("|" + "|".join(["---"] * len(cols)) + "|")
        for row in result.rows[:15]:
            cells = [_format_md_cell(row.get(c)) for c in cols]
            sections.append("| " + " | ".join(cells) + " |")
        sections.append(
            f"\n_(전체 {result.row_count}건, 표시 상위 {min(15, result.row_count)}건)_"
        )

    sections.append(
        "\n---\n\n> 면책: 결과는 매수 권유 아님. 거래비용·세금·survivorship bias 미반영."
    )
    body = "\n".join(sections)

    console.print(
        f"\n[cyan]주간 발굴 완료[/cyan] — {len(requested_names)}개 preset, "
        f"총 {total_found}건"
    )

    if publish_craft:
        if settings.craft_api_token is None:
            console.print("[yellow]CRAFT_API_TOKEN 미설정 — 발행 생략[/yellow]")
            return
        try:
            publisher = CraftPublisher()
            publish_result = publisher.publish_daily_note(
                body,
                today_kst(),
                note_kind="weekly-discover",
                title=f"주간 발굴 · {today_kst().isoformat()}",
            )
            action = "갱신" if publish_result.is_update else "발행"
            console.print(
                f"[green]✓ Craft {action}:[/green] [cyan]{publish_result.url}[/cyan]"
            )
        except CraftAuthError as e:
            console.print(f"[yellow]Craft 인증 실패: {e}[/yellow]")
        except CraftAPIError as e:
            console.print(f"[red]Craft API 오류: {e}[/red]")


def _preset_backtest_line(preset_sql: str, preset_name: str) -> str | None:
    """preset SQL에 대해 지난 1년 monthly backtest → 3m 적중률·평균 수익률 한 줄.

    Craft 노트의 preset 섹션에 "_과거 검증_ 적중률 X%, 평균 +Y%" 추가.
    백테스트는 v_at_date(:as_of) 필요 → v_latest_scores를 자동 치환 시도.
    데이터 부족/실행 실패 시 None 반환 (섹션에 안 추가).
    """
    import re
    from datetime import timedelta

    from stock_compass.screener.backtest import BacktestError, run_backtest
    from stock_compass.screener.engine import ScreenerError
    from stock_compass.utils.dates import today_kst
    from stock_compass.utils.logging import get_logger

    logger = get_logger(__name__)

    # v_latest_scores → v_at_date(:as_of) 자동 변환 (이미 :as_of 또는 v_at_date 있으면 그대로)
    if ":as_of" in preset_sql or re.search(
        r"v_at_date\(", preset_sql, re.IGNORECASE
    ):
        bt_sql = preset_sql
    else:
        bt_sql = re.sub(
            r"\bv_latest_scores\b",
            "v_at_date(:as_of)",
            preset_sql,
            flags=re.IGNORECASE,
        )
        if bt_sql == preset_sql:
            return None  # 변환 대상 없음

    end_d = today_kst() - timedelta(days=7)  # 직전 주까지 (당주 데이터 부족)
    start_d = end_d - timedelta(days=365)

    try:
        result = run_backtest(
            bt_sql,
            start=start_d,
            end=end_d,
            rebalance="monthly",
            forward_periods=("3m",),
            limit=10,
            preset_name=f"weekly-verify:{preset_name}",
        )
    except (BacktestError, ScreenerError) as e:
        logger.debug("weekly backtest %s 실패 — 노트에 생략: %s", preset_name, e)
        return None

    avg = result.stats.avg_return.get("3m")
    hit = result.stats.hit_rate.get("3m")
    if avg is None or hit is None:
        return None
    avg_sign = "+" if avg >= 0 else ""
    return (
        f"\n_과거 1년 검증 (monthly, 3m forward, {result.stats.rounds_count} 라운드):_ "
        f"**적중률 {hit * 100:.0f}%**, 평균 {avg_sign}{avg * 100:.1f}% "
        f"_(총 {result.stats.total_picks}건 선정)_"
    )


def _format_md_cell(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}" if abs(v) >= 1 else f"{v:.4f}"
    s = str(v)
    return s.replace("|", "/").replace("\n", " ")


def screener_rows_to_targets(
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


@app.command()
def backtest(
    preset: Annotated[
        str | None,
        typer.Option("--preset", help="screeners/presets/<name>.sql (`:as_of` placeholder 필요)"),
    ] = None,
    file: Annotated[
        Path | None, typer.Option("--file", "-f", help="저장된 .sql 파일")
    ] = None,
    sql: Annotated[
        str | None, typer.Option("--sql", help="인라인 SQL (`:as_of` placeholder 필요)")
    ] = None,
    start: Annotated[
        str, typer.Option("--start", help="백테스트 시작일 YYYY-MM-DD")
    ] = "",
    end: Annotated[
        str, typer.Option("--end", help="백테스트 종료일 YYYY-MM-DD (미지정 시 오늘)")
    ] = "",
    rebalance: Annotated[
        str, typer.Option("--rebalance", help="monthly / quarterly / weekly")
    ] = "monthly",
    forward: Annotated[
        str,
        typer.Option(
            "--forward",
            help="forward periods 쉼표 구분 (기본 1m,3m — 지원: 1m/3m/6m/12m)",
        ),
    ] = "1m,3m",
    limit: Annotated[
        int, typer.Option("--limit", help="라운드별 종목 수 상한 (기본 15)")
    ] = 15,
) -> None:
    """Phase 7-5 백테스트 — preset SQL x 시점별 forward return 통계.

    preset SQL은 `v_at_date(:as_of)` 형태를 사용해야 함. 매 라운드마다 `:as_of`가
    리밸런싱 일자로 치환됨. 결과는 라운드별 + 전체 통계 + 면책.
    """
    from stock_compass.config import settings
    from stock_compass.screener import (
        PresetNotFoundError,
        load_preset,
    )
    from stock_compass.screener.backtest import (
        BacktestError,
        run_backtest,
    )
    from stock_compass.utils.dates import today_kst
    from stock_compass.utils.logging import setup_logging

    setup_logging(settings.log_dir)

    # 진입점: preset > file > sql
    preset_name: str | None = None
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
    elif sql:
        sql_text = sql
    else:
        console.print("[red]--preset / --file / --sql 중 하나 필수[/red]")
        raise typer.Exit(code=2)

    if not start:
        console.print("[red]--start (YYYY-MM-DD) 필수[/red]")
        raise typer.Exit(code=2)
    try:
        start_d = date.fromisoformat(start)
        end_d = date.fromisoformat(end) if end else today_kst()
    except ValueError as e:
        console.print(f"[red]날짜 형식 오류: {e}[/red]")
        raise typer.Exit(code=2) from e

    periods = tuple(p.strip() for p in forward.split(",") if p.strip())

    try:
        result = run_backtest(
            sql_text,
            start=start_d,
            end=end_d,
            rebalance=rebalance,
            forward_periods=periods,
            limit=limit,
            preset_name=preset_name,
        )
    except BacktestError as e:
        console.print(f"[red]백테스트 거부: {e}[/red]")
        raise typer.Exit(code=2) from e

    _render_backtest(result)


def _render_backtest(result: BacktestResult) -> None:
    """라운드별 요약 + 전체 통계 + 면책."""
    from rich.panel import Panel
    from rich.table import Table

    console.print(
        f"[cyan]백테스트[/cyan] preset=[yellow]{result.preset_name or 'inline'}[/yellow]"
        f"  ·  기간 {result.start} ~ {result.end}"
        f"  ·  {result.rebalance} 리밸런싱  ·  forward {','.join(result.forward_periods)}"
    )

    if not result.rounds:
        console.print("[yellow]라운드 0개 — 기간 또는 데이터 부족[/yellow]")
        return

    # 라운드별 요약 테이블
    round_tbl = Table(
        title=f"라운드별 요약 ({len(result.rounds)}회)",
        show_lines=False,
    )
    round_tbl.add_column("as_of", style="cyan")
    round_tbl.add_column("종목", justify="right")
    for p in result.forward_periods:
        round_tbl.add_column(f"평균 {p}", justify="right")
    for r in result.rounds:
        row_avgs: list[str] = []
        for p in result.forward_periods:
            vals: list[float] = [
                v
                for tr in r.forward_returns.values()
                if (v := tr.get(p)) is not None
            ]
            if vals:
                avg_v = sum(vals) / len(vals)
                color = "green" if avg_v >= 0 else "red"
                sign = "+" if avg_v >= 0 else ""
                row_avgs.append(f"[{color}]{sign}{avg_v * 100:.1f}%[/{color}]")
            else:
                row_avgs.append("[dim]—[/dim]")
        round_tbl.add_row(r.as_of.isoformat(), str(len(r.selected)), *row_avgs)
    console.print(round_tbl)

    # 전체 통계
    stats_tbl = Table(title="전체 통계", show_lines=False)
    stats_tbl.add_column("Forward", style="cyan")
    stats_tbl.add_column("평균", justify="right")
    stats_tbl.add_column("중앙값", justify="right")
    stats_tbl.add_column("적중률 (양수)", justify="right")
    stats_tbl.add_column("최저", justify="right")
    for p in result.forward_periods:
        avg = result.stats.avg_return.get(p)
        med = result.stats.median_return.get(p)
        hit = result.stats.hit_rate.get(p)
        worst = result.stats.worst_return.get(p)
        stats_tbl.add_row(
            p,
            _fmt_pct(avg),
            _fmt_pct(med),
            f"{hit * 100:.1f}%" if hit is not None else "—",
            _fmt_pct(worst, force_color=True),
        )
    console.print(stats_tbl)
    console.print(
        f"[dim]총 선정 {result.stats.total_picks}건 x {len(result.forward_periods)} 기간[/dim]"
    )

    console.print(
        Panel(
            result.disclaimer,
            title="면책 / 한계",
            border_style="dim",
            padding=(0, 1),
        )
    )


def _fmt_pct(v: float | None, *, force_color: bool = False) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    text = f"{sign}{v * 100:.1f}%"
    if force_color:
        color = "green" if v >= 0 else "red"
        return f"[{color}]{text}[/{color}]"
    return text


def _add_screener_to_watchlist(
    rows: list[dict[str, Any]], *, group: str
) -> None:
    """`screen --add-to-watchlist` — 결과 종목을 WATCHLIST_<GROUP> universe에 등록."""
    from stock_compass.db import get_db_connection
    from stock_compass.screener.universes import (
        add_to_watchlist_group,
    )

    targets = screener_rows_to_targets(rows, default_market=None)
    if not targets:
        console.print(
            "[yellow]code/market 컬럼 없어 워치리스트 추가 생략.[/yellow]"
        )
        return

    pairs: list[tuple[str, Market, str | None, str | None]] = [
        (code, market, None, None) for code, market in targets
    ]
    try:
        with get_db_connection() as conn:
            r = add_to_watchlist_group(conn, group, pairs)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2) from e

    console.print(
        f"[green]✓[/green] [cyan]{r.universe_code}[/cyan] — "
        f"{r.members}종목 신규 등록 (대상 {len(pairs)}, 기존 멤버는 dedup)"
    )
    console.print(
        "[dim]자동 batch 추적은 .env의 WATCHLIST_KR/US에 직접 추가 필요.[/dim]"
    )


def _generate_deepdive_prompt(
    rows: list[dict[str, Any]], *, preset_name: str | None
) -> None:
    """`screen --prompt-deepdive` — 결과 종목 sentiment prompt MD 생성."""
    from stock_compass.output.prompt_generator import PromptGenerator

    targets = screener_rows_to_targets(rows, default_market=None)
    if not targets:
        console.print(
            "[yellow]code/market 컬럼 없어 deepdive prompt 생략.[/yellow]"
        )
        return

    tag = f"deepdive-{preset_name}" if preset_name else "deepdive"
    result = PromptGenerator().generate_sentiment_prompt(targets, tag=tag)
    console.print(
        f"[green]✓[/green] deepdive prompt: [cyan]{result.path}[/cyan]"
        f"  ({result.ticker_count}종목, 파일 {result.file_count}개)"
    )
    console.print(
        "[dim]Claude.ai에 복붙 → 응답 받아 "
        f"[cyan]stock-compass sentiment import --batch-id {result.batch_id}[/cyan][/dim]"
    )


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
            "Macro / Sentiment",
            [
                ("macro_score", "Macro 점수 0~100 (VIX·금리·KR USD/KRW)"),
                ("sentiment_score", "Sentiment 점수 0~100 (뉴스·공시 톤)"),
                ("sentiment_source", "api/manual_prompt/fallback/cache/placeholder"),
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
