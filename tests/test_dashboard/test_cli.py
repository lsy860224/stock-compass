"""dashboard 명령 CLI — --help + streamlit 미설치 안내."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from stock_compass.commands._app import app


def test_dashboard_help_lists_options() -> None:
    runner = CliRunner()
    r = runner.invoke(app, ["dashboard", "--help"])
    assert r.exit_code == 0
    for opt in ("--port", "--headless"):
        assert opt in r.stdout


def test_friendly_error_when_streamlit_missing() -> None:
    runner = CliRunner()
    with patch(
        "stock_compass.commands.dashboard.shutil.which", return_value=None
    ):
        r = runner.invoke(app, ["dashboard"])
    assert r.exit_code == 2
    assert "streamlit 미설치" in r.stdout
    assert "uv sync --extra dashboard" in r.stdout
