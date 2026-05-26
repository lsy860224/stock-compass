"""Typer 앱 인스턴스 + 공용 console — 다른 commands 모듈이 import해서 데코레이터 부착."""

from __future__ import annotations

import typer
from rich.console import Console

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
