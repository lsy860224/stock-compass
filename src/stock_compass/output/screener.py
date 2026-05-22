"""스크리너 결과 출력 어댑터 — table / csv / json."""

from __future__ import annotations

import csv as csv_module
import io
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from stock_compass.screener.engine import ScreenerResult

# 면책 footer — 모든 스크리너 출력에 자동 첨부 (CLAUDE.md 16 / SCREENER_SPEC 11)
DISCLAIMER = (
    "결과는 매수 권유 아님. 본인 추가 조사 필수. "
    "거래비용·세금·survivorship bias 미반영."
)


def render_table(
    result: ScreenerResult, console: Console | None = None
) -> None:
    console = console or Console()
    if not result.rows:
        console.print(
            f"[yellow]결과 0건 (실행 {result.elapsed_ms}ms"
            + (f", preset={result.preset_name}" if result.preset_name else "")
            + ")[/yellow]"
        )
        console.print(f"[dim]면책: {DISCLAIMER}[/dim]")
        return

    title = f"스크리너 결과 ({result.row_count}건"
    if result.preset_name:
        title += f", preset={result.preset_name}"
    title += f", {result.elapsed_ms}ms)"

    table = Table(title=title, show_lines=False)
    for col in result.columns:
        table.add_column(col, overflow="fold")

    for row in result.rows:
        cells = [_format_cell(row.get(col)) for col in result.columns]
        table.add_row(*cells)

    console.print(table)
    console.print(f"[dim]면책: {DISCLAIMER}[/dim]")


def render_csv(result: ScreenerResult) -> str:
    buf = io.StringIO()
    writer = csv_module.DictWriter(
        buf, fieldnames=result.columns, extrasaction="ignore"
    )
    writer.writeheader()
    for row in result.rows:
        writer.writerow({k: _stringify(v) for k, v in row.items()})
    return buf.getvalue()


def render_json(result: ScreenerResult, *, indent: int = 2) -> str:
    return json.dumps(
        {
            "columns": result.columns,
            "row_count": result.row_count,
            "elapsed_ms": result.elapsed_ms,
            "preset_name": result.preset_name,
            "rows": result.rows,
            "disclaimer": DISCLAIMER,
        },
        ensure_ascii=False,
        indent=indent,
        default=str,
    )


def export_to_file(content: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _format_cell(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) >= 1_000_000:
            return f"{v:,.0f}"
        if abs(v) < 1:
            return f"{v:.4f}"
        return f"{v:,.2f}"
    return str(v)


def _stringify(v: object) -> object:
    """CSV/JSON에서 안전 직렬화 — None은 빈 문자열, dict는 JSON."""
    if v is None:
        return ""
    if isinstance(v, dict | list):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v
