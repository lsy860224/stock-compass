"""rich 기반 콘솔 렌더러."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from stock_compass.factors.base import FactorScore
from stock_compass.scoring.engine import DISCLAIMER, CompositeScore, Verdict

_VERDICT_STYLE: dict[Verdict, str] = {
    "관심권": "bold green",
    "중립": "bold yellow",
    "주의": "bold red",
}


def render_single_score(score: CompositeScore, console: Console | None = None) -> None:
    """단일 종목 점수 + 5팩터 분해 + 면책 출력."""
    console = console or Console()

    header = _header_panel(score)
    table = _factors_table(score.factors)

    console.print(header)
    console.print(table)
    console.print(Panel(DISCLAIMER, title="면책", border_style="dim", padding=(0, 1)))


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
