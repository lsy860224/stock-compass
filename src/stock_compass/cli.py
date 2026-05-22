"""stock-compass CLI — typer 진입점.

명령:
    score   단일 종목 점수 (Phase 1)
    batch   워치리스트 일일 배치 (Phase 2)
    alert   알림 트리거 체크 (Phase 5)
    report  Craft 일일 노트 생성 (Phase 3)

면책: 본 도구의 출력은 투자 자문이 아닙니다. 본인 판단의 보조 자료입니다.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

app = typer.Typer(
    name="stock-compass",
    help="개인 매매 의사결정 보조 다요인 점수화 도구 (KR·US).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.callback()
def _root() -> None:
    """전역 옵션 자리 (현재는 없음)."""


@app.command()
def score(
    ticker: Annotated[str, typer.Argument(help="종목 코드 (예: AAPL, 005930)")],
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us (자동 감지가 기본)")
    ] = None,
) -> None:
    """단일 종목의 5팩터 점수 + 종합 점수 출력 (Phase 1)."""
    _ = (ticker, market)
    raise NotImplementedError("score 명령은 Phase 1에서 구현됩니다.")


@app.command()
def batch(
    market: Annotated[
        str | None, typer.Option("--market", "-m", help="kr / us — 미지정 시 전체")
    ] = None,
    tickers: Annotated[
        str | None, typer.Option("--tickers", "-t", help="콤마 구분 직접 지정")
    ] = None,
) -> None:
    """워치리스트 일일 배치 → SQLite 저장 (Phase 2)."""
    _ = (market, tickers)
    raise NotImplementedError("batch 명령은 Phase 2에서 구현됩니다.")


@app.command()
def alert(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="발화 없이 어떤 알림이 나갈지만 출력")
    ] = False,
) -> None:
    """3종 알림 트리거(임계치·급변·일일) 체크 후 발화 (Phase 5)."""
    _ = dry_run
    raise NotImplementedError("alert 명령은 Phase 5에서 구현됩니다.")


@app.command()
def report(
    date: Annotated[
        str | None, typer.Option("--date", help="YYYY-MM-DD (기본: 오늘)")
    ] = None,
    open_file: Annotated[
        bool, typer.Option("--open", help="생성 후 Finder에서 열기")
    ] = False,
) -> None:
    """Craft 일일 노트 Markdown 생성 (Phase 3)."""
    _ = (date, open_file)
    raise NotImplementedError("report 명령은 Phase 3에서 구현됩니다.")


if __name__ == "__main__":
    app()
