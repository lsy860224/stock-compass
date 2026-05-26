"""rich 기반 콘솔 렌더러."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from stock_compass.factors.base import FactorScore
from stock_compass.output.valuation_range import ValuationRange
from stock_compass.scoring.engine import DISCLAIMER, CompositeScore, Verdict

if TYPE_CHECKING:
    from stock_compass.db import HistoryRow

_VERDICT_STYLE: dict[Verdict, str] = {
    "관심권": "bold green",
    "중립": "bold yellow",
    "주의": "bold red",
}


def render_single_score(score: CompositeScore, console: Console | None = None) -> None:
    """단일 종목 점수 + 5팩터 분해 + valuation range + 면책 출력."""
    console = console or Console()

    header = _header_panel(score)
    table = _factors_table(score.factors)

    console.print(header)
    console.print(table)

    # Valuation Range (Phase A) — Fundamentals 가져와서 계산
    vrange = _try_valuation_range(score)
    if vrange is not None and not vrange.is_empty():
        console.print(render_valuation_range(vrange))

    console.print(Panel(DISCLAIMER, title="면책", border_style="dim", padding=(0, 1)))


def render_valuation_range(vrange: ValuationRange) -> Table:
    """ValuationRange → rich.Table. 방법(PER/PBR/DIVIDEND)별 3 시나리오."""
    from stock_compass.output.valuation_range import (
        DISCLAIMER as VR_DISCLAIMER,
    )

    cur = vrange.current_price
    cur_str = f" (현재 {cur:,.2f} {vrange.currency})" if cur else ""
    table = Table(
        title=f"Valuation Range — {vrange.ticker}{cur_str}",
        caption=VR_DISCLAIMER,
        caption_style="dim italic",
        show_lines=False,
    )
    table.add_column("시나리오", style="cyan")
    table.add_column("방법", style="magenta")
    table.add_column("배수/yield", justify="right")
    table.add_column("적정가", justify="right")
    table.add_column("vs 현재", justify="right")
    for p in vrange.points:
        mul_str = (
            f"{p.multiple * 100:.1f}%" if p.method == "DIVIDEND" else f"{p.multiple:.1f}x"
        )
        price_str = f"{p.fair_price:,.2f} {vrange.currency}"
        if p.vs_current_pct is None:
            vs_str = "—"
        else:
            sign = "+" if p.vs_current_pct >= 0 else ""
            color = "green" if p.vs_current_pct >= 0 else "red"
            vs_str = f"[{color}]{sign}{p.vs_current_pct:.1f}%[/{color}]"
        table.add_row(p.scenario, p.method, mul_str, price_str, vs_str)
    return table


def _try_valuation_range(score: CompositeScore) -> ValuationRange | None:
    """score → fundamentals 호출 → ValuationRange. 실패 시 None."""
    try:
        from stock_compass.markets import get_adapter
        from stock_compass.output.valuation_range import compute_valuation_range

        adapter = get_adapter(score.ticker, score.market)
        fund = adapter.get_fundamentals(score.ticker)
        return compute_valuation_range(fund, current_price=score.price_at_score)
    except Exception:  # UI render는 score 출력 막지 않음
        return None


def _header_panel(score: CompositeScore) -> Panel:
    verdict_text = Text(score.verdict, style=_VERDICT_STYLE[score.verdict])
    price_txt = (
        f"  ·  가격 {score.price_at_score:,.2f} {score.currency}"
        if score.price_at_score is not None
        else ""
    )
    body = Text.assemble(
        (score.ticker, "bold cyan"),
        (f" [{score.market}]", "dim"),
        ("\n종합 점수  ", "white"),
        (f"{score.total_score:.1f}", "bold white"),
        (" / 100   ", "dim"),
        verdict_text,
        (price_txt, "dim"),
    )
    return Panel(body, border_style="cyan", padding=(0, 1))


def _factors_table(factors: list[FactorScore]) -> Table:
    table = Table(title="팩터 분해", show_lines=False, header_style="bold")
    table.add_column("팩터", style="cyan", no_wrap=True)
    table.add_column("가중", justify="right")
    table.add_column("점수", justify="right")
    table.add_column("소스", style="dim")
    table.add_column("메모", overflow="fold")
    for f in factors:
        table.add_row(
            f.name,
            f"{f.weight:.0%}",
            _color_score(f.score),
            f.source,
            f.note,
        )
    return table


def _color_score(score: float) -> Text:
    if score >= 70:
        style = "green"
    elif score >= 50:
        style = "yellow"
    else:
        style = "red"
    return Text(f"{score:.1f}", style=style)


def render_score_ranking(scores: list[CompositeScore], console: Console | None = None) -> None:
    """워치리스트 batch 결과를 종합 점수 내림차순 테이블로 표시."""
    console = console or Console()
    if not scores:
        console.print(
            "[yellow]표시할 결과 없음 (워치리스트 비어 있거나 모든 종목 실패)[/yellow]"
        )
        return

    ordered = sorted(scores, key=lambda s: s.total_score, reverse=True)
    table = Table(title=f"워치리스트 점수 순위 ({len(ordered)}종목)", show_lines=False)
    table.add_column("순", justify="right", style="dim")
    table.add_column("종목", style="cyan")
    table.add_column("시장", justify="center", style="dim")
    table.add_column("종합", justify="right")
    table.add_column("판단", justify="center")
    table.add_column("가격", justify="right")
    table.add_column("V", justify="right", style="dim")
    table.add_column("F", justify="right", style="dim")
    table.add_column("T", justify="right", style="dim")
    table.add_column("M", justify="right", style="dim")
    table.add_column("S", justify="right", style="dim")

    for i, s in enumerate(ordered, start=1):
        price_str = (
            f"{s.price_at_score:,.2f} {s.currency}"
            if s.price_at_score is not None
            else "—"
        )
        f_map = {f.name: f.score for f in s.factors}
        table.add_row(
            str(i),
            f"{s.ticker}" + (f"\n[dim]{s.name}[/dim]" if s.name else ""),
            s.market,
            _color_score(s.total_score),
            Text(s.verdict, style=_VERDICT_STYLE[s.verdict]),
            price_str,
            _short(f_map.get("valuation")),
            _short(f_map.get("fundamentals")),
            _short(f_map.get("technical")),
            _short(f_map.get("macro")),
            _short(f_map.get("sentiment")),
        )

    console.print(table)
    console.print(
        Panel(
            f"{DISCLAIMER}  ·  V=Valuation F=Fundamentals T=Technical M=Macro S=Sentiment",
            title="면책",
            border_style="dim",
            padding=(0, 1),
        )
    )


def render_history(
    ticker: str,
    market: str,
    rows: list[HistoryRow],
    console: Console | None = None,
) -> None:
    """단일 종목 N일 점수 추이."""
    console = console or Console()
    if not rows:
        console.print(f"[yellow]{ticker} 점수 이력 없음 — batch 먼저 실행 필요.[/yellow]")
        return

    table = Table(
        title=f"{ticker} [{market}] 점수 추이 ({len(rows)}영업일)",
        show_lines=False,
    )
    table.add_column("일자", style="cyan")
    table.add_column("종합", justify="right")
    table.add_column("판단", justify="center")
    table.add_column("가격", justify="right", style="dim")
    table.add_column("V", justify="right")
    table.add_column("F", justify="right")
    table.add_column("T", justify="right")
    table.add_column("M", justify="right")
    table.add_column("S", justify="right")

    for r in rows:
        table.add_row(
            r.date,
            _color_score(r.total_score),
            Text(r.verdict, style=_VERDICT_STYLE[r.verdict]),
            f"{r.price_at_score:,.2f}" if r.price_at_score is not None else "—",
            _short(r.factor_scores.get("valuation")),
            _short(r.factor_scores.get("fundamentals")),
            _short(r.factor_scores.get("technical")),
            _short(r.factor_scores.get("macro")),
            _short(r.factor_scores.get("sentiment")),
        )

    console.print(table)
    console.print(Panel(DISCLAIMER, title="면책", border_style="dim", padding=(0, 1)))


def _short(score: float | None) -> Text:
    if score is None:
        return Text("—", style="dim")
    return _color_score(score)


def render_factor_breakdown(score: CompositeScore, console: Console | None = None) -> None:
    """단일 종목 — 종합 점수 + 5팩터 계층 트리 (raw_values 핵심 지표 포함)."""
    console = console or Console()
    header = Text.assemble(
        (score.ticker, "bold cyan"),
        (f" [{score.market}]", "dim"),
        (f"  종합 {score.total_score:.1f}  ", "white"),
        (score.verdict, _VERDICT_STYLE[score.verdict]),
    )
    tree = Tree(header)
    for f in score.factors:
        node = tree.add(
            Text.assemble(
                (f"{f.name:<12}", "cyan"),
                (f"  {f.weight:>4.0%}  ", "dim"),
                _color_score(f.score),
                (f"  {f.note}", "dim"),
            )
        )
        for k, v in f.raw_values.items():
            if k == "component_scores" or v is None:
                continue
            node.add(Text(f"{k} = {_fmt_raw(v)}", style="dim"))
    console.print(tree)
    console.print(Panel(DISCLAIMER, title="면책", border_style="dim", padding=(0, 1)))


def _fmt_raw(v: object) -> str:
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 1 else f"{v:,.2f}"
    return str(v)
