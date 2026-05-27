"""dashboard 명령 — streamlit run 의 subprocess 진입점.

streamlit 은 optional dep — 미설치 시 친절 안내 후 exit(2).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from stock_compass.commands._app import app, console


@app.command()
def dashboard(
    port: Annotated[
        int, typer.Option("--port", "-p", help="Streamlit 서버 포트")
    ] = 8501,
    headless: Annotated[
        bool,
        typer.Option(
            "--headless",
            help="브라우저 자동 오픈 안 함 (--server.headless=true)",
        ),
    ] = False,
) -> None:
    """로컬 Streamlit 대시보드 — Overview / History / Trades 페이지.

    설치: `uv sync --extra dashboard`
    """
    if shutil.which("streamlit") is None:
        console.print(
            "[red]streamlit 미설치.[/red]\n"
            "[dim]설치: [cyan]uv sync --extra dashboard[/cyan] "
            "또는 [cyan]uv pip install streamlit altair[/cyan][/dim]"
        )
        raise typer.Exit(code=2)

    app_path = (
        Path(__file__).resolve().parent.parent / "dashboard" / "app.py"
    )
    if not app_path.exists():
        console.print(f"[red]dashboard/app.py 미발견: {app_path}[/red]")
        raise typer.Exit(code=2)

    cmd = [
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(port),
    ]
    if headless:
        cmd.extend(["--server.headless", "true"])
    console.print(
        f"[cyan]streamlit dashboard[/cyan] http://localhost:{port}"
        " (Ctrl+C 종료)"
    )
    try:
        result = subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        console.print("[yellow]대시보드 종료[/yellow]")
        raise typer.Exit(code=0) from None
    sys.exit(result.returncode)
